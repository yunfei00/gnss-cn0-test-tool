import csv
import hashlib
import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from gnss_cn0_tool.adb import DeviceError
from gnss_cn0_tool.analysis import analyze
from gnss_cn0_tool.reporting import replay


def make_export(raw: Path, *, boot=3, invalid_window=False):
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
            writer.writerow((800000 + ns, ns, "SESSION", constellation, svid, value, frequency))
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
            "elapsed_realtime_nanos": ns, "event_type": kind,
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
    assert observed[0]["b_only_svid"] == "12"
    assert observed[1]["quality"] == "NO_L5_DATA"
    assert observed[1]["mean_a_minus_b_db"] is None
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


def test_rejects_boot_mismatch_or_tampered_csv(tmp_path):
    raw = tmp_path / "raw"
    make_export(raw, boot=4)
    with pytest.raises(ValueError, match="开机纪元"):
        analyze(raw)
    with (raw / "satellites.csv").open("ab") as stream:
        stream.write(b"corrupted\n")
    with pytest.raises(DeviceError, match="完整性"):
        analyze(raw)
