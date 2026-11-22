import csv
import json
import math
import statistics
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

from gnss_cn0_tool.adb import EXPORT_FILES, verify_file

FREQUENCIES = {"L1": 1_575_420_000, "L5": 1_176_450_000}
COLUMNS = {
    "timestamp_ms", "elapsed_realtime_nanos", "session_id", "constellation_name",
    "svid", "cn0_dbhz", "carrier_frequency_hz",
}


def band_for(frequency: str, tolerance: int) -> str:
    if not frequency:
        return "UNKNOWN"
    measured = float(frequency)
    return next(
        (band for band, center in FREQUENCIES.items() if abs(measured - center) <= tolerance),
        "UNKNOWN",
    )


def read_windows(path: Path, manifest: dict):
    bounds = defaultdict(dict)
    seen = set()
    previous = -1
    utc_origin = None
    utc_warning = False
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            if (
                event["test_run_id"], event["logger_session_id"], event["boot_count"],
                event["protocol_version"], event["ack"],
            ) != (
                manifest["test_run_id"], manifest["logger_session_id"], manifest["boot_count"],
                1, "persisted",
            ):
                raise ValueError("事件会话、开机纪元或协议不一致")
            clock = int(event["elapsed_realtime_nanos"])
            utc = int(event["timestamp_ms"])
            if clock < previous or event["event_id"] in seen:
                raise ValueError("事件时间倒退或 event_id 重复")
            previous = clock
            seen.add(event["event_id"])
            offset = utc - clock // 1_000_000
            if utc_origin is None:
                utc_origin = offset
            utc_warning |= abs(offset - utc_origin) > 1000
            kind = event["event_type"]
            if kind in {"MEASUREMENT_START", "MEASUREMENT_END"}:
                scene = event["scene_instance_id"]
                if kind in bounds[scene]:
                    raise ValueError("同一窗口的测量边界重复")
                bounds[scene][kind] = clock
    windows = []
    invalid = []
    for scene, pair in bounds.items():
        start = pair.get("MEASUREMENT_START")
        end = pair.get("MEASUREMENT_END")
        if start is None or end is None or start >= end:
            invalid.append({"scene_instance_id": scene, "quality": "INVALID_WINDOW"})
            continue
        windows.append((start, end, scene))
    windows.sort()
    if any(left[1] > right[0] for left, right in zip(windows, windows[1:])):
        raise ValueError("测量窗口重叠")
    return windows, invalid, utc_warning


def analyze(folder: Path, *, tolerance=2_000_000, min_samples=1, scenes=None):
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    names = [entry["name"] for entry in manifest["files"]]
    if (manifest["schema_version"] != 1 or len(names) != len(set(names))
            or set(names) - EXPORT_FILES or not {"satellites.csv", "events.jsonl"} <= set(names)):
        raise ValueError("导出清单版本或文件列表无效")
    for entry in manifest["files"]:
        verify_file(folder / entry["name"], entry)
    windows, invalid, utc_warning = read_windows(folder / "events.jsonl", manifest)
    starts = [window[0] for window in windows]
    samples = defaultdict(dict)
    unknown = defaultdict(int)
    with (folder / "satellites.csv").open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader((line for line in stream if not line.startswith("#")), strict=True)
        if not COLUMNS <= set(reader.fieldnames or ()):
            raise ValueError("卫星 CSV 缺少必需字段")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("卫星 CSV 行列数不一致")
            if row["session_id"] != manifest["logger_session_id"]:
                raise ValueError("卫星 CSV 会话不一致")
            elapsed = int(row["elapsed_realtime_nanos"])
            idx = bisect_right(starts, elapsed) - 1
            if idx < 0 or elapsed >= windows[idx][1] or row["constellation_name"] != "GPS":
                continue
            scene = windows[idx][2]
            band = band_for(row["carrier_frequency_hz"], tolerance)
            if band == "UNKNOWN":
                unknown[scene] += 1
                continue
            svid = int(row["svid"])
            cn0 = float(row["cn0_dbhz"])
            if not (1 <= svid <= 32 and math.isfinite(cn0) and 0 < cn0 <= 63):
                continue
            frames = samples[scene, band, svid]
            if elapsed in frames and frames[elapsed] != cn0:
                raise ValueError("同一卫星帧存在冲突的 C/N0")
            frames[elapsed] = cn0
    return summarize(windows, invalid, samples, unknown, utc_warning, min_samples, scenes or {})


def summarize(windows, invalid, samples, unknown, utc_warning, minimum, scenes):
    by_satellite = []
    summaries = list(invalid)
    for start, end, scene in windows:
        for band in FREQUENCIES:
            group = []
            for (instance, signal, svid), frames in sorted(samples.items()):
                if (instance, signal) != (scene, band):
                    continue
                values = list(frames.values())
                group.append({
                    "scene_instance_id": scene, "band": band, "svid": svid,
                    "samples": len(values), "mean_cn0_dbhz": statistics.fmean(values),
                    "median_cn0_dbhz": statistics.median(values), "std_db": statistics.pstdev(values),
                    "min_cn0_dbhz": min(values), "max_cn0_dbhz": max(values),
                    "sample_span_fraction": (max(frames) - min(frames)) / (end - start),
                    "quality": "OBSERVED" if len(values) >= minimum else "INSUFFICIENT_SAMPLES",
                })
            by_satellite.extend(group)
            quality = "OBSERVED" if any(row["quality"] == "OBSERVED" for row in group) else (
                "INSUFFICIENT_SAMPLES" if group else f"NO_{band}_DATA"
            )
            summaries.append({
                "scene_instance_id": scene, "scene_id": scenes.get(scene, {}).get("scene_id"),
                "readiness_source": scenes.get(scene, {}).get("readiness_source"), "band": band,
                "start_ns": start, "end_ns": end, "satellites": len(group),
                "unknown_frequency_rows": unknown[scene], "quality": quality,
            })
    pairs = []
    per_band = []
    by_window = defaultdict(dict)
    for row in by_satellite:
        if row["quality"] == "OBSERVED":
            by_window[row["scene_instance_id"], row["band"]][row["svid"]] = row["mean_cn0_dbhz"]
    initial = windows[0][2] if windows else None
    baseline = initial
    for _, _, other in windows[1:]:
        kind = scenes.get(other, {}).get("scene_id")
        is_baseline = kind in {"baseline", "screen_on"}
        reference = initial if is_baseline else baseline
        for band in FREQUENCIES:
            a, b = by_window[reference, band], by_window[other, band]
            matched = sorted(a.keys() & b.keys())
            deltas = [a[svid] - b[svid] for svid in matched]
            for svid, delta in zip(matched, deltas):
                pairs.append({
                    "a_scene_instance_id": reference, "b_scene_instance_id": other,
                    "band": band, "svid": svid, "a_dbhz": a[svid], "b_dbhz": b[svid],
                    "a_minus_b_db": delta,
                })
            per_band.append({
                "a_scene_instance_id": reference, "b_scene_instance_id": other, "band": band,
                "comparison": "baseline_drift" if is_baseline else "scene",
                "matched_svid": ",".join(map(str, matched)),
                "a_only_svid": ",".join(map(str, sorted(a.keys() - b.keys()))),
                "b_only_svid": ",".join(map(str, sorted(b.keys() - a.keys()))),
                "mean_a_minus_b_db": statistics.fmean(deltas) if deltas else None,
                "quality": "OBSERVED" if deltas else (
                    "NO_L5_DATA" if band == "L5" and (not a or not b) else "NO_MATCHED_SATELLITES"
                ),
            })
        if is_baseline:
            baseline = other
    trend = [
        {"scene_instance_id": scene, "band": band, "svid": svid, "elapsed_ns": elapsed,
         "cn0_dbhz": cn0}
        for (scene, band, svid), frames in samples.items()
        for elapsed, cn0 in sorted(frames.items())
    ]
    return {
        "windows": summaries, "by_satellite": by_satellite, "matched_satellites": pairs,
        "comparisons": per_band, "trend": trend,
        "quality": [{"wall_clock": "UTC_JUMP" if utc_warning else "OK",
                     "window_status": "OK" if windows and not invalid else "INVALID_WINDOW"}],
    }
