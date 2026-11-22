import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from gnss_cn0_tool.adb import Adb, Device
from gnss_cn0_tool.gui import Window


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
    assert all(window.plan.item(row, 0).checkState() == Qt.CheckState.Checked
               for row in range(window.plan.rowCount()))
    for row in range(window.plan.rowCount()):
        if row not in {1, 5}:
            window.plan.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
    plan = window.current_plan()
    assert [(scene.scene_id, scene.mode) for scene in plan.scenes] == [
        ("video", "auto"), ("navigation_manual", "manual"),
    ]
    window.plan.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
    window.plan.item(5, 0).setCheckState(Qt.CheckState.Unchecked)
    with pytest.raises(ValueError, match="至少勾选"):
        window.current_plan()
