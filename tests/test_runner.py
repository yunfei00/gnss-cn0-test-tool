import threading

from gnss_cn0_tool.adb import LOGGER, SCENE, DeviceDisconnected, DeviceError
from gnss_cn0_tool.run import RunControls, Runner
from gnss_cn0_tool.store import Plan, RunStore, Scene


class FakeAdb:
    serial = "fixture"

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


def test_scene_not_ready_never_creates_window(tmp_path, monkeypatch):
    monkeypatch.setattr("gnss_cn0_tool.run.replay", lambda _: {})
    fake = FakeAdb(scene_error=True)
    runner = Runner(fake, RunStore(tmp_path), RunControls(), lambda *_: None)
    _, state = runner.run(Plan((Scene("video", stabilize_seconds=0, measure_seconds=0.01),)))
    assert state == "FAILED"
    assert "MEASUREMENT_START" not in fake.events
    assert fake.stopped


def test_disconnect_keeps_window_open_and_records_interruption(tmp_path, monkeypatch):
    monkeypatch.setattr("gnss_cn0_tool.run.replay", lambda _: {})
    fake = FakeAdb(disconnect=True)
    runner = Runner(fake, RunStore(tmp_path), RunControls(), lambda *_: None)
    _, state = runner.run(Plan((Scene("video", stabilize_seconds=0, measure_seconds=0.01),)))
    assert state == "INTERRUPTED"
    assert "MEASUREMENT_START" in fake.events
    assert "MEASUREMENT_END" not in fake.events
