import threading

import pytest

from gnss_cn0_tool.adb import LOGGER, SCENE, Adb, DeviceDisconnected, DeviceError
from gnss_cn0_tool.run import RunControls, Runner, resolve_apps
from gnss_cn0_tool.store import Plan, RunStore, Scene


def test_app_resolution_falls_back_to_complete_side_by_side_pair(monkeypatch):
    checked = []

    def capabilities(adb, package, operations):
        checked.append((adb.test_apps, package))
        if not adb.test_apps and package == SCENE:
            raise DeviceError("protocol unavailable")
        return {"protocol_version": 1, "scene_ids": ["video"]}

    monkeypatch.setattr(Adb, "capabilities", capabilities)
    adb = Adb(serial="fixture")
    selected, _, _ = resolve_apps(adb, {"video"}, automatic=True)
    assert selected.test_apps
    assert selected.serial == adb.serial
    assert checked == [(False, LOGGER), (False, SCENE), (True, LOGGER), (True, SCENE)]


def test_app_resolution_explains_mixed_installation(monkeypatch):
    def capabilities(adb, package, operations):
        if adb.test_apps == (package == LOGGER):
            return {"protocol_version": 1, "scene_ids": ["video"]}
        raise DeviceError("protocol unavailable")

    monkeypatch.setattr(Adb, "capabilities", capabilities)
    with pytest.raises(DeviceError) as error:
        resolve_apps(Adb(serial="fixture"), {"video"}, automatic=True)
    assert "正式版采集应用未就绪" in str(error.value)
    assert "并装联调版场景应用未就绪" in str(error.value)


class FakeAdb:
    serial = "fixture"
    test_apps = False

    def __init__(self, *, scene_error=False, disconnect=False):
        self.scene_error = scene_error
        self.disconnect = disconnect
        self.events = []
        self.started = 0
        self.stopped = False
        self.clock = 1

    def run(self, *args):
        return b""

    def component(self, package, activity):
        return f"{package}/.{activity}"

    def ensure_connected(self):
        pass

    def capabilities(self, package, operations):
        return {"protocol_version": 1, "scene_ids": ["video"]}

    def kernel_clock(self):
        self.clock += 1
        return {"elapsed_realtime_nanos": self.clock, "quantization_ns": 10}

    def call(self, package, method, arg=None):
        if package == SCENE:
            if method == "stop":
                self.stopped = True
                return {"state": "STOPPING"}
            if self.stopped:
                return {"state": "STOPPED"}
            if self.scene_error:
                raise DeviceError("DEVICE_SCENE_NOT_READY")
            return {"state": "READY"}
        assert package == LOGGER
        if method == "clock":
            self.clock += 1
            return {"boot_count": 1, "elapsed_realtime_nanos": self.clock}
        if method == "mark":
            _, _, event, kind = arg.split(":")
            self.events.append(kind)
            return {"event_id": event, "ack": "persisted", "logger_session_id": "sid", "boot_count": 1}
        if method == "start":
            self.started += 1
        if method == "stop":
            return {"state": "STOPPED"}
        if self.disconnect and "MEASUREMENT_START" in self.events:
            raise DeviceDisconnected("device offline")
        return {"state": "STARTED", "logger_session_id": "sid", "boot_count": 1}

    def export(self, *_):
        return {}


def test_manual_requires_confirmation_and_reuses_one_logger(tmp_path, monkeypatch):
    monkeypatch.setattr("gnss_cn0_tool.run.replay", lambda _: {"comparisons": [{"quality": "OBSERVED"}]})
    fake = FakeAdb()
    controls = RunControls()
    waiting = threading.Event()
    states = []

    def update(state, _):
        states.append(state)
        if state == "WAITING_MANUAL_CONFIRM":
            waiting.set()
        elif state == "WAITING_MANUAL_EXIT":
            controls.manual.set()

    store = RunStore(tmp_path)
    runner = Runner(fake, store, controls, update)
    result = []
    thread = threading.Thread(target=lambda: result.append(runner.run(Plan((
        Scene("video", stabilize_seconds=0, measure_seconds=0.01),
        Scene("navigation", "manual", 0, 0.01, manual_timeout_seconds=3),
    )))))
    thread.start()
    assert waiting.wait(3)
    assert fake.events.count("MEASUREMENT_START") == 1
    controls.manual.set()
    thread.join(4)
    assert not thread.is_alive()
    assert result[0][1] == "COMPLETED"
    assert fake.events.count("MEASUREMENT_START") == 2
    assert "MANUAL_CONFIRMED" in fake.events
    assert fake.started == 1
    assert store.history()[0][1] == "COMPLETED"


def test_preselected_manual_scene_does_not_switch_foreground_or_wait(tmp_path, monkeypatch):
    monkeypatch.setattr("gnss_cn0_tool.run.replay", lambda _: {"comparisons": [{"quality": "OBSERVED"}]})
    fake = FakeAdb()

    def reject_foreground_changes(*_):
        pytest.fail("manual sampling issued a foreground command")

    monkeypatch.setattr(fake, "run", reject_foreground_changes)
    states = []
    progress = []
    runner = Runner(fake, RunStore(tmp_path), RunControls(), lambda state, _: states.append(state),
                    manual_ready=True, progress=progress.append)
    _, state = runner.run(Plan((Scene("external", "manual", 0, 0.02),)))
    assert state == "COMPLETED"
    assert "WAITING_MANUAL_CONFIRM" not in states
    assert "WAITING_MANUAL_EXIT" not in states
    assert fake.events == ["MANUAL_CONFIRMED", "STABILIZATION_START", "MEASUREMENT_START", "MEASUREMENT_END"]
    assert any(item.remaining_seconds == 1 for item in progress)
    assert progress[-1].processed == progress[-1].total == 1


def test_prepared_manual_scene_starts_only_after_operator_click(tmp_path, monkeypatch):
    monkeypatch.setattr("gnss_cn0_tool.run.replay", lambda _: {
        "comparisons": [], "windows": [{"quality": "OBSERVED"}],
    })
    fake = FakeAdb()
    commands = []
    monkeypatch.setattr(fake, "run", lambda *args: commands.append(args) or b"")
    controls = RunControls()
    states = []

    def update(state, _):
        states.append(state)
        if state == "MANUAL_CAPTURE_PREPARED":
            assert not fake.events
            controls.manual.set()

    runner = Runner(fake, RunStore(tmp_path), controls, update, manual_ready=True, prepare_manual=True)
    _, state = runner.run(Plan((Scene("external", "manual", 0, 0.02),)))
    assert state == "COMPLETED"
    assert len(commands) == 1 and commands[0][-1].endswith(".MainActivity")
    assert "WAITING_MANUAL_EXIT" not in states
    assert "MEASUREMENT_END" in fake.events


def test_scene_not_ready_never_creates_window(tmp_path, monkeypatch):
    monkeypatch.setattr("gnss_cn0_tool.run.replay", lambda _: {})
    fake = FakeAdb(scene_error=True)
    runner = Runner(fake, RunStore(tmp_path), RunControls(), lambda *_: None)
    _, state = runner.run(Plan((Scene("video", stabilize_seconds=0, measure_seconds=0.01),)))
    assert state == "FAILED"
    assert "MEASUREMENT_START" not in fake.events
    assert fake.stopped


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_screen_off_observes_display_and_restores_prior_state(tmp_path, monkeypatch, mode):
    monkeypatch.setattr("gnss_cn0_tool.run.replay", lambda _: {
        "comparisons": [], "windows": [{"quality": "OBSERVED"}],
    })
    fake = FakeAdb()
    state = "OFF"
    locked = True
    keys = []

    def adb_run(*args):
        nonlocal state, locked
        if args == ("shell", "dumpsys", "display"):
            return (
                f"  Display 0:\n    mBaseDisplayInfo=DisplayInfo{{state {state}, committedState {state}}}\n"
                "    mOverrideDisplayInfo=DisplayInfo{state ON}\n"
                "  Display 3:\n    mBaseDisplayInfo=DisplayInfo{state DOZE}\n"
                f"Display Power Controller:\n  mDisplayId=0\n  mScreenState={state}\n"
                "Display Power Controller:\n  mDisplayId=3\n  mScreenState=DOZE\n"
            ).encode()
        if args == ("shell", "dumpsys", "window", "policy"):
            return f"  showing={str(locked).lower()}\n".encode()
        if args == ("shell", "wm", "dismiss-keyguard"):
            assert state == "ON"
            keys.append("dismiss-keyguard")
            locked = False
        if args[:3] == ("shell", "input", "keyevent"):
            keys.append(args[3])
            state = "OFF" if args[3] == "KEYCODE_SLEEP" else "ON"
            locked = True
        return b""

    monkeypatch.setattr(fake, "run", adb_run)

    def update(status, reason):
        nonlocal state, locked
        if status == "WAITING_DISPLAY_OFF":
            assert mode == "manual"
            assert fake.started == 1
            state = "OFF"
            locked = True

    runner = Runner(fake, RunStore(tmp_path), RunControls(), update, manual_ready=mode == "manual")
    _, result = runner.run(Plan((Scene("screen_off", mode, stabilize_seconds=0, measure_seconds=0.01),)))
    assert result == "COMPLETED"
    sleep = ["KEYCODE_SLEEP"] if mode == "auto" else []
    assert keys == ["KEYCODE_WAKEUP", "dismiss-keyguard", *sleep, "KEYCODE_WAKEUP", "dismiss-keyguard"]
    assert not locked
    assert "MEASUREMENT_END" in fake.events


def test_display_state_uses_only_primary_controller_if_base_state_is_absent(tmp_path, monkeypatch):
    fake = FakeAdb()
    dump = (b"Display Power Controller:\n  mDisplayId=0\n  mScreenState=OFF\n"
            b"Display Power Controller:\n  mDisplayId=4\n  mScreenState=ON\n")
    monkeypatch.setattr(fake, "run", lambda *_: dump)
    runner = Runner(fake, RunStore(tmp_path), RunControls(), lambda *_: None)
    assert runner.display_state() == "OFF"
    dump = dump.replace(b"mDisplayId=0", b"mDisplayId=2")
    with pytest.raises(DeviceError, match="显示屏 0"):
        runner.display_state()


def test_disconnect_keeps_window_open_and_records_interruption(tmp_path, monkeypatch):
    monkeypatch.setattr("gnss_cn0_tool.run.replay", lambda _: {})
    fake = FakeAdb(disconnect=True)
    runner = Runner(fake, RunStore(tmp_path), RunControls(), lambda *_: None)
    _, state = runner.run(Plan((Scene("video", stabilize_seconds=0, measure_seconds=0.01),)))
    assert state == "INTERRUPTED"
    assert "MEASUREMENT_START" in fake.events
    assert "MEASUREMENT_END" not in fake.events


def test_unavailable_kernel_clock_falls_back_and_preserves_quality_flag(tmp_path, monkeypatch):
    fake = FakeAdb()

    def unsupported_clock():
        raise DeviceError("unsupported uptime")

    monkeypatch.setattr(fake, "kernel_clock", unsupported_clock)
    monkeypatch.setattr("gnss_cn0_tool.run.replay", lambda _: {"comparisons": [{"quality": "OBSERVED"}]})
    runner = Runner(fake, RunStore(tmp_path), RunControls(), lambda *_: None)
    runner.run(Plan((Scene("video", stabilize_seconds=0, measure_seconds=0.01),)))
    audit = runner.calibrations["start"]
    assert audit["fallback_reason"] == "unsupported uptime"
    assert audit["best"]["source"] == "logger_provider"
    assert len(audit["samples"]) == 9
