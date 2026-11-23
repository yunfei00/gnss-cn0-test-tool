import csv
import hashlib
import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from gnss_cn0_tool.adb import DeviceError
from gnss_cn0_tool.analysis import analyze
from gnss_cn0_tool.reporting import replay


def make_export(raw: Path, *, boot=3, invalid_window=False, elapsed_scale=1):
    raw.mkdir()
    columns = (
        "timestamp_ms", "elapsed_realtime_nanos", "session_id", "constellation_name",
        "svid", "cn0_dbhz", "carrier_frequency_hz",
    )
    with (raw / "satellites.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        for ns, constellation, svid, value, frequency in [
            (9, "GPS", 3, 31, 1575420000),
            (10, "GPS", 3, 42, 1575420000),
            (11, "GPS", 3, 44, 1575420000),
            (11, "GPS", 3, 44, 1575420000),
            (11, "GPS", 3, 29, 1176450000),
            (12, "GPS", 3, 30, ""),
            (15, "GPS", 3, 62, 1575420000),
            (20, "GPS", 3, 39, 1575420000),
            (21, "GPS", 3, 41, 1575420000),
            (22, "GPS", 12, 43, 1575420000),
            (22, "GALILEO", 3, 60, 1575420000),
            (30, "GPS", 3, 41, 1575420000),
        ]:
            writer.writerow((800000 + ns, ns * elapsed_scale,
                             "SESSION", constellation, svid, value, frequency))
    events = []
    for index, (scene, ns, kind) in enumerate([
        ("A", 10, "MEASUREMENT_START"), ("A", 15, "MEASUREMENT_END"),
        ("B", 20, "MEASUREMENT_START"), ("B", 25, "MEASUREMENT_END"),
        ("C", 30, "MEASUREMENT_START"), ("C", 35, "MEASUREMENT_END"),
    ]):
        if invalid_window and index == 5:
            break
        events.append({
            "test_run_id": "RUN", "logger_session_id": "SESSION", "boot_count": boot,
            "protocol_version": 1, "ack": "persisted", "scene_instance_id": scene,
            "event_id": str(index), "timestamp_ms": 5000 if index > 1 else 0,
            "elapsed_realtime_nanos": ns * elapsed_scale, "event_type": kind,
        })
    (raw / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    manifest = {
        "schema_version": 1, "test_run_id": "RUN", "logger_session_id": "SESSION",
        "boot_count": 3, "files": [],
    }
    for path in sorted(raw.iterdir()):
        data = path.read_bytes()
        manifest["files"].append({
            "name": path.name, "size": len(data), "lines": data.count(b"\n"),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    (raw / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_half_open_windows_cn0_baseline_drift_and_excel(tmp_path):
    make_export(tmp_path / "raw")
    (tmp_path / "scenes.json").write_text(json.dumps({
        "A": {"scene_id": "baseline"}, "B": {"scene_id": "video"}, "C": {"scene_id": "baseline"},
    }), encoding="utf-8")
    result = replay(tmp_path)
    a_l1 = result["by_satellite"][0]
    assert (a_l1["mean_cn0_dbhz"], a_l1["samples"]) == (43, 2)
    observed = result["comparisons"]
    assert observed[0]["mean_a_minus_b_db"] == 3
    assert observed[0]["matched_svid"] == "3"
    assert (observed[0]["a_valid_samples"], observed[0]["b_valid_samples"]) == (2, 2)
    assert observed[0]["b_only_svid"] == "12"
    assert observed[1]["quality"] == "NO_L5_DATA"
    assert observed[1]["mean_a_minus_b_db"] is None
    assert observed[1]["a_valid_samples"] == observed[1]["b_valid_samples"] == 0
    assert observed[2]["comparison"] == "baseline_drift"
    assert observed[2]["mean_a_minus_b_db"] == 2
    assert result["quality"][0]["wall_clock"] == "UTC_JUMP"
    book = load_workbook(tmp_path / "reports" / "report.xlsx", read_only=True)
    try:
        header, first, *_ = book["comparisons"].values
        assert first[header.index("mean_a_minus_b_db")] == 3
    finally:
        book.close()


def test_incomplete_boundary_is_not_estimated(tmp_path):
    make_export(tmp_path / "raw", invalid_window=True)
    result = analyze(tmp_path / "raw")
    assert {"scene_instance_id": "C", "quality": "INVALID_WINDOW"} in result["windows"]
    assert all(row["scene_instance_id"] != "C" for row in result["trend"])


def test_one_second_rows_keep_empty_seconds_and_original_observations(tmp_path):
    raw = tmp_path / "raw"
    make_export(raw, elapsed_scale=1_000_000_000)
    original = (raw / "satellites.csv").read_bytes()
    replay(tmp_path)
    assert (raw / "satellites.csv").read_bytes() == original
    with (tmp_path / "reports/observations_1s.csv").open(encoding="utf-8-sig", newline="") as stream:
        seconds = list(csv.DictReader(stream))
    assert len(seconds) == 15
    assert seconds[0]["time_beijing"] == "1970-01-01 08:00:00.000+08:00"
    assert seconds[0]["frame_time_beijing"] == "1970-01-01 08:13:20.010+08:00"
    assert seconds[1]["recorded_frames"] == "1"
    assert seconds[1]["gps_l5_dbhz"] == "G03=29"
    assert seconds[3]["status"] == "NO_FRAME"
    assert not seconds[3]["frame_time_beijing"]
    with (tmp_path / "reports/satellite_observations.csv").open(
        encoding="utf-8-sig", newline="",
    ) as stream:
        observations = list(csv.DictReader(stream))
    assert len(observations) == 12
    assert not observations[0]["scene_instance_id"]
    assert any(row["constellation"] == "GALILEO" for row in observations)


@pytest.mark.parametrize("invalid_window", [False, True])
def test_screen_off_reference_does_not_depend_on_execution_order(tmp_path, invalid_window):
    raw = tmp_path / "raw"
    make_export(raw, invalid_window=invalid_window)
    scenes = {
        "A": {"scene_id": "screen_on"},
        "B": {"scene_id": "video"},
        "C": {"scene_id": "screen_off", "readiness_source": "display_observed"},
    }
    result = analyze(raw, scenes=scenes, require_screen_off=True)
    if invalid_window:
        assert result["quality"][0]["reference_status"] == "MISSING_SCREEN_OFF_REFERENCE"
        assert result["comparisons"] == []
        assert any(row.get("scene_id") == "video" for row in result["windows"])
    else:
        assert result["quality"][0]["reference_status"] == "SCREEN_OFF"
        l1 = [row for row in result["comparisons"] if row["band"] == "L1"]
        assert [row["a_scene_instance_id"] for row in l1] == ["C", "C"]
        assert [row["b_scene_instance_id"] for row in l1] == ["A", "B"]
        assert [row["mean_a_minus_b_db"] for row in l1] == [-2, 1]


def test_rejects_boot_mismatch_or_tampered_csv(tmp_path):
    raw = tmp_path / "raw"
    make_export(raw, boot=4)
    with pytest.raises(ValueError, match="开机纪元"):
        analyze(raw)
    with (raw / "satellites.csv").open("ab") as stream:
        stream.write(b"corrupted\n")
    with pytest.raises(DeviceError, match="完整性"):
        analyze(raw)
