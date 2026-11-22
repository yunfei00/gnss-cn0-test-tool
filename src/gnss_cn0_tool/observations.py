import csv
import json
import math
from bisect import bisect_right
from datetime import datetime, timedelta, timezone

from gnss_cn0_tool.analysis import band_for, read_windows

NS_PER_SECOND = 1_000_000_000
LOCAL_ZONE = timezone(timedelta(hours=8))
SECOND_COLUMNS = [
    "time_beijing", "scene_id", "scene_instance_id", "second_in_scene", "frame_time_beijing",
    "frame_elapsed_ns", "recorded_frames", "gps_l1_dbhz", "gps_l5_dbhz", "status",
]
OBSERVATION_COLUMNS = [
    "time_beijing", "timestamp_utc_ms", "elapsed_realtime_nanos", "scene_id", "scene_instance_id",
    "session_id", "constellation", "svid", "carrier_frequency_hz", "cn0_dbhz", "azimuth_deg",
    "elevation_deg", "used_in_fix",
]


def readable_time(timestamp_ms):
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=LOCAL_ZONE).isoformat(
        sep=" ", timespec="milliseconds",
    )


def observation_tables(raw, manifest, scenes, tolerance):
    event_path = raw / "events.jsonl"
    windows, _, _ = read_windows(event_path, manifest)
    starts = [start for start, _, _ in windows]
    with event_path.open(encoding="utf-8") as stream:
        anchors = {
            event["scene_instance_id"]: event["timestamp_ms"]
            for event in map(json.loads, stream)
            if event["event_type"] == "MEASUREMENT_START"
        }

    def frames():
        with (raw / "satellites.csv").open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(line for line in stream if not line.startswith("#")):
                elapsed = int(row["elapsed_realtime_nanos"])
                index = bisect_right(starts, elapsed) - 1
                if index >= 0 and elapsed >= windows[index][1]:
                    index = -1
                yield row, elapsed, index

    def observations():
        for row, elapsed, index in frames():
            instance = windows[index][2] if index >= 0 else ""
            yield {
                "time_beijing": readable_time(row["timestamp_ms"]),
                "timestamp_utc_ms": int(row["timestamp_ms"]), "elapsed_realtime_nanos": elapsed,
                "scene_id": scenes.get(instance, {}).get("scene_id", ""), "scene_instance_id": instance,
                "session_id": row["session_id"], "constellation": row["constellation_name"],
                "svid": row["svid"], "carrier_frequency_hz": row["carrier_frequency_hz"],
                "cn0_dbhz": row["cn0_dbhz"], "azimuth_deg": row.get("azimuth_deg", ""),
                "elevation_deg": row.get("elevation_deg", ""), "used_in_fix": row.get("used_in_fix", ""),
            }

    def seconds():
        bins = {}
        for row, elapsed, index in frames():
            if index < 0:
                continue
            second = (elapsed - windows[index][0]) // NS_PER_SECOND
            bucket = bins.setdefault((index, second), {
                "elapsed": elapsed, "timestamp": row["timestamp_ms"], "last_frame": None,
                "count": 0, "L1": {}, "L5": {},
            })
            if bucket["last_frame"] != elapsed:
                bucket["count"] += 1
                bucket["last_frame"] = elapsed
            if elapsed != bucket["elapsed"] or row["constellation_name"] != "GPS":
                continue
            band = band_for(row["carrier_frequency_hz"], tolerance)
            if band in {"L1", "L5"} and row["cn0_dbhz"]:
                bucket[band][int(row["svid"])] = row["cn0_dbhz"]

        for index, (start, end, instance) in enumerate(windows):
            for second in range(math.ceil((end - start) / NS_PER_SECOND)):
                bucket = bins.get((index, second))
                result = {
                    "time_beijing": readable_time(int(anchors[instance]) + second * 1000),
                    "scene_id": scenes.get(instance, {}).get("scene_id", ""),
                    "scene_instance_id": instance, "second_in_scene": second,
                    "recorded_frames": bucket["count"] if bucket else 0,
                    "status": "OBSERVED" if bucket else "NO_FRAME",
                }
                if bucket:
                    result.update({
                        "frame_time_beijing": readable_time(bucket["timestamp"]),
                        "frame_elapsed_ns": bucket["elapsed"],
                    })
                    for band in ("L1", "L5"):
                        result[f"gps_{band.lower()}_dbhz"] = " | ".join(
                            f"G{svid:02}={value}" for svid, value in sorted(bucket[band].items())
                        )
                yield result

    return [
        ("observations_1s", SECOND_COLUMNS, seconds()),
        ("satellite_observations", OBSERVATION_COLUMNS, observations()),
    ]
