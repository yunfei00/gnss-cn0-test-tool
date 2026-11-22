import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from gnss_cn0_tool.adb import Adb, Device
from gnss_cn0_tool.gui import Window
from gnss_cn0_tool.run import RunProgress


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(Adb, "devices", lambda _: [])
    app = QApplication.instance() or QApplication([])
    window = Window()
    yield window
    window.device_timer.stop()
    window.close()
    app.processEvents()


def test_startup_scan_and_poll_keep_selection_when_device_is_added(window, monkeypatch):
    first = Device("first-device", "device")
    second = Device("second-device", "device")
    detected = [first]
    monkeypatch.setattr(Adb, "devices", lambda _: detected.copy())
    for _ in range(50):
        if window.devices.currentData() == first:
            break
        QTest.qWait(20)
    assert window.devices.currentData() == first

    detected[:] = [second, first]
    window.device_timer.start(50)
    for _ in range(50):
        if window.devices.count() == 3:
            break
        QTest.qWait(20)
    assert window.devices.itemData(1) == first
    assert window.devices.itemData(2) == second
    assert window.devices.currentData() == first


def test_multiple_devices_require_selection_and_disconnect_does_not_switch(window):
    first = Device("first-device", "unauthorized")
    second = Device("second-device", "device")
    window.apply_devices("adb", [first, second], "")
    assert window.devices.currentData() is None
    window.devices.setCurrentIndex(2)
    window.apply_devices("adb", [first], "")
    assert window.devices.currentData() is None
    window.apply_devices("adb", [Device(first.serial, "device")], "")
    assert window.devices.currentData() is None


def test_checked_rows_only_are_kept_in_plan_order(window):
    keep = {"video", "navigation_manual"}
    for row in range(window.plan.rowCount()):
        if window.plan.item(row, 1).text() not in keep:
            window.plan.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
    plan = window.current_plan()
    assert [(scene.scene_id, scene.mode) for scene in plan.scenes] == [
        ("video", "auto"), ("navigation_manual", "manual"),
    ]
    for row in range(window.plan.rowCount()):
        window.plan.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
    with pytest.raises(ValueError, match="至少勾选"):
        window.current_plan()


def test_common_scene_catalog_defaults_to_screen_on_and_video(window):
    assert [scene.scene_id for scene in window.current_plan("auto").scenes] == [
        "screen_on", "video",
    ]
    rows = {window.plan.item(row, 1).text(): row for row in range(window.plan.rowCount())}
    assert {"front_camera", "rear_camera", "motor", "music", "flashlight",
            "recorder", "white_screen", "screen_off"} <= rows.keys()
    assert window.plan.isRowHidden(rows["navigation_manual"])
    window.manual_mode.setChecked(True)
    assert window.plan.isRowHidden(0)
    assert not window.plan.isRowHidden(rows["navigation_manual"])
    assert window.current_plan("manual").scenes[0].scene_id == "navigation_manual"
    assert window.start.text() == "开始手动采样"


def test_progress_displays_actual_phase_remaining_time(window):
    window.show_progress(RunProgress(3, 1, "video", 1, 1, 12, 20))
    assert window.progress_bar.value() == 8
    assert window.progress_bar.maximum() == 20
    assert "第 2 项 · 视频" in window.progress_text.text()
    assert "剩余 12 秒" in window.progress_text.text()


def test_single_scene_report_shows_window_summary(window, tmp_path):
    report = tmp_path / "report-run" / "reports"
    report.mkdir(parents=True)
    (report / "windows.csv").write_text(
        "scene_id,band,satellites,quality\nnavigation_manual,L5,0,NO_L5_DATA\n",
        encoding="utf-8",
    )
    window.history.addItem("手动采样", report.parent)
    assert window.result.horizontalHeaderItem(0).text() == "测试场景"
    assert window.result.item(0, 0).text() == "第三方导航"
    assert window.result.item(0, 3).text() == "无有效 L5 数据"
