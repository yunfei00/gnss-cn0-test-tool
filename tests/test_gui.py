import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from gnss_cn0_tool.adb import Adb, Device
from gnss_cn0_tool.gui import Window
from gnss_cn0_tool.run import RunProgress
from gnss_cn0_tool.store import Plan, Scene


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


def test_checked_scenes_share_timings_and_keep_grid_order(window):
    keep = {"video", "motor"}
    for scene_id, checkbox in window.scene_checks.items():
        checkbox.setChecked(scene_id in keep)
    window.stabilize.setValue(2.5)
    window.duration.setValue(12)
    window.repeats.setValue(3)
    plan = window.current_plan()
    assert [(scene.scene_id, scene.mode) for scene in plan.scenes] == [
        ("motor", "auto"), ("video", "auto"),
    ]
    assert {(s.stabilize_seconds, s.measure_seconds, s.repeat) for s in plan.scenes} == {(2.5, 12, 3)}
    assert not window.automatic_mode.isEnabled()
    for checkbox in window.scene_checks.values():
        checkbox.setChecked(False)
    with pytest.raises(ValueError, match="至少勾选"):
        window.current_plan()


def test_common_scene_catalog_defaults_to_screen_on_video_and_screen_off(window):
    assert [scene.scene_id for scene in window.current_plan().scenes] == [
        "screen_on", "video", "screen_off",
    ]
    assert {"front_camera", "rear_camera", "motor", "music", "flashlight",
            "recorder", "white_screen", "screen_off"} <= window.scene_checks.keys()
    assert window.scene_grid_layout.rowCount() == 3


def test_single_scene_uses_manual_only_when_automatic_is_unchecked(window):
    for scene_id, checkbox in window.scene_checks.items():
        checkbox.setChecked(scene_id == "video")
    assert window.current_plan().scenes[0].mode == "auto"
    window.automatic_mode.setChecked(False)
    assert window.current_plan().scenes[0].mode == "manual"
    assert window.start.text() == "开始手动采样"
    window.scene_checks["navigation_manual"].setChecked(True)
    assert window.automatic_mode.isChecked()
    with pytest.raises(ValueError, match="外部场景"):
        window.current_plan()


def test_imported_plan_shares_first_item_parameters_with_notice(window):
    window.set_plan(Plan((Scene("video", stabilize_seconds=6), Scene("music", stabilize_seconds=10))))
    assert [s.scene_id for s in window.current_plan().scenes] == ["video", "music"]
    assert [s.stabilize_seconds for s in window.current_plan().scenes] == [6, 6]
    assert "公用参数已取第一项" in window.run_log.toPlainText()


def test_progress_displays_actual_phase_remaining_time(window):
    window.on_status("PREPARING_SCENE", "video")
    window.on_status("MEASURING", "")
    window.show_progress(RunProgress(3, 1, "video", 1, 1, 12, 20))
    assert window.progress_bar.value() == 8
    assert window.progress_bar.maximum() == 20
    assert "第 2 项 · 视频" in window.progress_text.text()
    assert "剩余 12 秒" in window.progress_text.text()
    window.show_progress(RunProgress(3, 1, "video", 1, 1, 10, 20))
    window.show_progress(RunProgress(3, 1, "video", 1, 1, 10, 20))
    log = window.run_log.toPlainText()
    assert "正在准备场景 · 视频" in log
    assert "正在采样" in log
    assert log.count("剩余 10 秒") == 1


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
