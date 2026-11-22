import hashlib
import threading
import time
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from gnss_cn0_tool.adb import LOGGER, SCENE, Adb, DeviceDisconnected, DeviceError
from gnss_cn0_tool.reporting import replay
from gnss_cn0_tool.store import Plan, RunStore, Scene, write_json


class Cancelled(Exception):
    pass


class Skipped(Exception):
    pass


class SceneCleanupFailed(DeviceError):
    pass


class RunControls:
    def __init__(self):
        self.cancel = threading.Event()
        self.pause = threading.Event()
        self.manual = threading.Event()
        self.skip = threading.Event()


class Runner:
    def __init__(self, adb: Adb, store: RunStore, controls: RunControls, update: Callable):
        self.adb = adb
        self.store = store
        self.controls = controls
        self.update = update
        self.scenes = {}
        self.session = None

    def advance(self, state, scene_id=None, reason=None):
        self.store.transition(self.run_id, state, scene_id, reason)
        self.update(state, reason or "")

    def check_cancelled(self):
        if self.controls.cancel.is_set():
            raise Cancelled("操作员取消")

    def wait(self, seconds, check=None):
        deadline = time.monotonic() + seconds
        next_check = 0.0
        while True:
            self.check_cancelled()
            now = time.monotonic()
            if check is not None and now >= next_check:
                check()
                next_check = now + 2
            if now >= deadline:
                return
            self.controls.cancel.wait(min(0.1, deadline - now))

    def wait_state(self, package, arg, target, timeout=30, *, cancellable=True):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancellable:
                self.check_cancelled()
            reply = self.adb.call(package, "status", arg)
            state = reply.get("state")
            if state == target:
                return reply
            if state == "INTERRUPTED":
                raise DeviceDisconnected(reply.get("reason", "SESSION_LOST"))
            if state not in {"STARTING", "STOPPING", "STARTED", "READY"}:
                raise DeviceError(f"{package}: {state}，预期 {target}")
            time.sleep(0.25)
        raise DeviceError(f"{package}: 等待 {target} 超时")

    def logger_health(self):
        logger = self.adb.call(LOGGER, "status", self.run_id)
        if (logger.get("state"), logger.get("logger_session_id"), logger.get("boot_count")) != (
            "STARTED", self.session["logger_session_id"], self.session["boot_count"]
        ):
            raise DeviceDisconnected("采集会话或 Android 开机纪元已改变")

    def calibrate(self, label, plan):
        samples = []
        for _ in range(9):
            self.check_cancelled()
            utc = time.time_ns()
            t1 = time.monotonic_ns()
            reply = self.adb.call(LOGGER, "clock")
            t3 = time.monotonic_ns()
            if reply["boot_count"] != self.session["boot_count"]:
                raise DeviceDisconnected("Android 开机纪元已改变")
            samples.append({
                **reply, "t1_ns": t1, "t3_ns": t3, "pc_utc_ns": utc, "rtt_ns": t3 - t1,
                "offset_ns": reply["elapsed_realtime_nanos"] - (t1 + t3) // 2,
            })
        best = min(samples, key=lambda row: row["rtt_ns"])
        uncertainty = best["rtt_ns"] / 2_000_000
        self.calibrations[label] = {
            "samples": samples, "best": best, "uncertainty_ms": uncertainty,
            "quality": "OK" if uncertainty <= plan.max_uncertainty_ms else "TIME_SYNC_LOW_CONFIDENCE",
        }
        write_json(self.folder / "clock_calibration.json", self.calibrations)

    def mark(self, instance, kind):
        event = str(uuid4())
        arg = f"{self.run_id}:{instance}:{event}:{kind}"
        try:
            reply = self.adb.call(LOGGER, "mark", arg)
        except DeviceDisconnected:
            raise
        except DeviceError:
            self.logger_health()
            reply = self.adb.call(LOGGER, "mark", arg)
        if (reply.get("event_id"), reply.get("ack"), reply.get("logger_session_id"),
            reply.get("boot_count")) != (
            event, "persisted", self.session["logger_session_id"], self.session["boot_count"]
        ):
            raise DeviceError("Logger 事件回执不匹配")

    def confirm(self, scene, instance, *, exit_scene=False):
        self.controls.manual.clear()
        self.controls.skip.clear()
        prompt = "请在手机上退出当前场景并确认" if exit_scene else (
            scene.instructions or f"请在手机上进入场景 {scene.scene_id}，完成后确认"
        )
        self.advance("WAITING_MANUAL_EXIT" if exit_scene else "WAITING_MANUAL_CONFIRM",
                     instance, prompt)
        deadline = time.monotonic() + scene.manual_timeout_seconds
        while time.monotonic() < deadline:
            self.check_cancelled()
            if self.controls.skip.is_set():
                raise Skipped("操作员跳过场景")
            if self.controls.manual.wait(0.2):
                return
        raise Skipped("人工确认超时") if scene.on_failure == "skip" else DeviceError("人工确认超时")

    def start_auto(self, scene, instance):
        duration = str(int(scene.stabilize_seconds + scene.measure_seconds) + 90)
        output = self.adb.run(
            "shell", "am", "start", "-W", "-n", self.adb.component(SCENE, "SceneEntryActivity"),
            "--ei", "protocol_version", "1", "--es", "scene", scene.scene_id,
            "--es", "scene_instance_id", instance, "--ei", "duration", duration,
            "--ei", "particles", str(scene.particles), "--ei", "cpu_threads", str(scene.cpu_threads),
        )
        if b"Error:" in output:
            raise DeviceError("ADB 无法启动 Scene Activity")
        self.wait_state(SCENE, instance, "READY")

    def baseline(self):
        self.adb.run("shell", "input", "keyevent", "KEYCODE_HOME")
        resolved = self.adb.run(
            "shell", "cmd", "package", "resolve-activity", "--brief",
            "-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME",
        ).decode(errors="replace").strip().splitlines()
        launcher, separator, _ = resolved[-1].partition("/") if resolved else ("", "", "")
        if not launcher or not separator:
            raise DeviceError("无法识别系统启动器")
        for _ in range(10):
            if self.is_home(launcher):
                return launcher
            self.wait(0.2)
        raise DeviceError("基准场景下启动器未处于前台")

    def is_home(self, launcher):
        output = self.adb.run("shell", "dumpsys", "activity", "activities")
        return any(
            b"topResumedActivity" in line and (launcher + "/").encode() in line
            for line in output.splitlines()
        )

    def execute_scene(self, scene: Scene, instance: str):
        auto = scene.mode == "auto" and scene.scene_id != "baseline"
        launcher = None
        measurement_finished = False
        failure = None
        try:
            if auto:
                self.advance("WAIT_SCENE_READY", instance)
                self.start_auto(scene, instance)
                readiness = "app_ack"
            elif scene.mode == "manual":
                self.confirm(scene, instance)
                readiness = "manual_confirm"
            else:
                launcher = self.baseline()
                readiness = "launcher_observed"
            self.scenes[instance] = {"scene_id": scene.scene_id, "readiness_source": readiness}
            write_json(self.folder / "scenes.json", self.scenes)
            self.mark(instance, "MANUAL_CONFIRMED" if scene.mode == "manual" else "SCENE_READY")
            self.advance("STABILIZING", instance)
            self.mark(instance, "STABILIZATION_START")

            def health():
                self.logger_health()
                if auto and self.adb.call(SCENE, "status", instance).get("state") != "READY":
                    raise DeviceError("自动场景已退出 READY")
                if launcher and not self.is_home(launcher):
                    raise DeviceError("基准场景已离开系统启动器")

            self.wait(scene.stabilize_seconds, health)
            self.advance("MEASURING", instance)
            self.mark(instance, "MEASUREMENT_START")
            self.wait(scene.measure_seconds, health)
            health()
            self.mark(instance, "MEASUREMENT_END")
            measurement_finished = True
        except (DeviceError, Cancelled, Skipped) as error:
            failure = error
        finally:
            if auto:
                self.advance("STOPPING_SCENE", instance)
                try:
                    self.adb.call(SCENE, "stop", instance)
                    self.wait_state(SCENE, instance, "STOPPED", cancellable=False)
                    if measurement_finished:
                        self.mark(instance, "SCENE_STOPPED")
                except DeviceDisconnected:
                    raise
                except DeviceError as error:
                    if isinstance(failure, DeviceDisconnected):
                        raise failure from error
                    raise SceneCleanupFailed(f"场景停止未核实：{error}") from error
        if failure is not None:
            if type(failure) is DeviceError and scene.on_failure == "skip":
                raise Skipped(str(failure)) from failure
            raise failure
        if scene.mode == "manual":
            self.confirm(scene, instance, exit_scene=True)

    def run(self, plan: Plan) -> tuple[str, str]:
        self.run_id = self.store.create(plan)
        self.folder = self.store.path(self.run_id)
        self.calibrations = {}
        self.scenes = {}
        self.session = None
        logger_requested = False
        state = "FAILED"
        reason = None
        try:
            self.advance("PREFLIGHT")
            self.adb.ensure_connected()
            logger = self.adb.capabilities(LOGGER, {"start", "status", "stop", "mark", "export", "clock"})
            needed = {s.scene_id for s in plan.scenes if s.mode == "auto"} - {"baseline"}
            scene_app = self.adb.capabilities(SCENE, {"start", "status", "stop"}) if needed else {}
            if needed - set(scene_app.get("scene_ids", [])):
                raise DeviceError("计划中有当前场景 APP 不支持的自动场景")
            write_json(self.folder / "environment.json", {
                "logger": logger, "scene": scene_app,
                "device_hash": hashlib.sha256(self.adb.serial.encode()).hexdigest()[:12],
            })
            self.advance("LOGGER_START")
            output = self.adb.run("shell", "am", "start", "-W", "-n",
                                  self.adb.component(LOGGER, "MainActivity"))
            if b"Error:" in output:
                raise DeviceError("无法打开 Logger 主界面")
            logger_requested = True
            arg = self.run_id + (":nmea" if plan.collect_nmea else "")
            if not plan.collect_raw:
                arg += ":status_only"
            self.adb.call(LOGGER, "start", arg)
            self.session = self.wait_state(LOGGER, self.run_id, "STARTED")
            self.advance("LOGGER_READY")
            self.calibrate("start", plan)
            for scene in plan.scenes:
                for _ in range(scene.repeat):
                    if self.controls.pause.is_set():
                        self.advance("PAUSED")
                        while self.controls.pause.is_set():
                            self.wait(0.2)
                        self.logger_health()
                    self.check_cancelled()
                    instance = str(uuid4())
                    self.advance("PREPARING_SCENE", instance, scene.scene_id)
                    try:
                        self.execute_scene(scene, instance)
                    except Skipped as error:
                        self.advance("SKIPPED", instance, str(error))
            self.calibrate("end", plan)
            state = "COMPLETED"
        except Cancelled as error:
            state, reason = "CANCELLED", str(error)
        except DeviceDisconnected as error:
            state, reason = "INTERRUPTED", str(error)
        except (DeviceError, ValueError, OSError, KeyError) as error:
            reason = str(error)
        if logger_requested:
            try:
                self.advance("FINALIZING")
                recover(self.adb, self.folder, self.run_id, generate_report=False)
                self.advance("REPORTING")
                result = replay(self.folder)
                if state == "COMPLETED":
                    if any(row["quality"] == "TIME_SYNC_LOW_CONFIDENCE"
                           for row in self.calibrations.values()):
                        state, reason = "COMPLETED_WITH_WARNINGS", "TIME_SYNC_LOW_CONFIDENCE"
                    elif (not result["comparisons"] or any(
                        row["quality"] != "OBSERVED"
                        for row in result["comparisons"] + result.get("windows", [])
                    )):
                        state, reason = "COMPLETED_WITH_WARNINGS", "有效窗口或匹配频段数据不足"
            except (DeviceError, ValueError, OSError, KeyError) as error:
                if state == "COMPLETED":
                    state = "INTERRUPTED" if isinstance(error, DeviceDisconnected) else "FAILED"
                reason = "; ".join(filter(None, [reason, f"导出或报告：{error}"]))
        self.advance(state, reason=reason)
        return self.run_id, state


def recover(adb: Adb, folder: Path, run_id: str, *, generate_report=True):
    state = adb.call(LOGGER, "stop", run_id)
    if state.get("state") != "STOPPED":
        raise DeviceError(f"采集未确认停止：{state.get('state')}")
    adb.export(run_id, folder / "raw")
    if generate_report:
        return replay(folder)
    return None
