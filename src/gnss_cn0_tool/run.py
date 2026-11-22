import hashlib
import math
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from gnss_cn0_tool.adb import LOGGER, SCENE, Adb, DeviceDisconnected, DeviceError
from gnss_cn0_tool.reporting import replay
from gnss_cn0_tool.store import Plan, RunStore, Scene, write_json

DISPLAY_SCENES = {"screen_on": "ON", "screen_off": "OFF"}
HOST_SCENES = {"baseline", *DISPLAY_SCENES}
AOD_MESSAGE = "手机进入息屏显示（AOD）。请在系统设置关闭息屏显示后，重新测量完全灭屏基准。"


class Cancelled(Exception):
    pass


class Skipped(Exception):
    pass


class SceneCleanupFailed(DeviceError):
    pass


def resolve_apps(adb: Adb, needed: set[str], *, automatic=False):
    candidates = [adb]
    if automatic:
        candidates.append(Adb(adb.executable, adb.serial, test_apps=not adb.test_apps))
    errors = []
    for candidate in candidates:
        variant = "并装联调版" if candidate.test_apps else "正式版"
        try:
            logger = candidate.capabilities(LOGGER, {"start", "status", "stop", "mark", "export", "clock"})
        except DeviceDisconnected:
            raise
        except DeviceError as error:
            errors.append(f"{variant}采集应用未就绪（{error}）")
            continue
        try:
            scene_app = candidate.capabilities(SCENE, {"start", "status", "stop"}) if needed else {}
        except DeviceDisconnected:
            raise
        except DeviceError as error:
            errors.append(f"{variant}场景应用未就绪（{error}）")
            continue
        missing = needed - set(scene_app.get("scene_ids", []))
        if missing:
            errors.append(f"{variant}场景应用不支持 " + "、".join(sorted(missing)))
            continue
        return candidate, logger, scene_app
    explanation = "；".join(errors)
    raise DeviceError(f"应用检查失败。请安装支持协议 v1 的对应 Android 应用。{explanation}")


class RunControls:
    def __init__(self):
        self.cancel = threading.Event()
        self.pause = threading.Event()
        self.manual = threading.Event()
        self.skip = threading.Event()


@dataclass(frozen=True)
class RunProgress:
    total: int
    processed: int = 0
    scene: str = ""
    repetition: int = 0
    repeats: int = 0
    remaining_seconds: int | None = None
    phase_seconds: int = 0


class Runner:
    def __init__(self, adb: Adb, store: RunStore, controls: RunControls, update: Callable,
                 *, automatic_apps=False, manual_ready=False, prepare_manual=False, progress=None):
        self.adb = adb
        self.store = store
        self.controls = controls
        self.update = update
        self.automatic_apps = automatic_apps
        self.manual_ready = manual_ready
        self.prepare_manual = prepare_manual
        self.progress_callback = progress
        self.scenes = {}
        self.session = None

    def advance(self, state, scene_id=None, reason=None):
        self.store.transition(self.run_id, state, scene_id, reason)
        self.update(state, reason or "")
        self.report_progress(remaining_seconds=None)

    def report_progress(self, **changes):
        if self.progress_callback:
            self.progress = replace(self.progress, **changes)
            self.progress_callback(self.progress)

    def check_cancelled(self):
        if self.controls.cancel.is_set():
            raise Cancelled("操作员取消")

    def wait(self, seconds, check=None, *, countdown=False):
        deadline = time.monotonic() + seconds
        next_check = 0.0
        previous_remaining = None
        while True:
            self.check_cancelled()
            now = time.monotonic()
            if countdown:
                remaining = max(0, math.ceil(deadline - now))
                if remaining != previous_remaining:
                    self.report_progress(remaining_seconds=remaining, phase_seconds=math.ceil(seconds))
                    previous_remaining = remaining
            if check is not None and now >= next_check:
                check()
                next_check = now + 2
            if now >= deadline:
                return
            self.controls.cancel.wait(max(0, min(0.1, deadline - time.monotonic())))

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
        boot_count = self.session["boot_count"]

        def probe(read_clock):
            self.check_cancelled()
            utc = time.time_ns()
            t1 = time.monotonic_ns()
            reply = read_clock()
            t3 = time.monotonic_ns()
            if reply.get("boot_count", boot_count) != boot_count:
                raise DeviceDisconnected("Android 开机纪元已改变")
            return {
                **reply, "t1_ns": t1, "t3_ns": t3, "pc_utc_ns": utc, "rtt_ns": t3 - t1,
                "boot_count": boot_count, "source": reply.get("source", "logger_provider"),
                "offset_ns": reply["elapsed_realtime_nanos"] - (t1 + t3) // 2,
                "uncertainty_ms": (t3 - t1) / 2_000_000 + reply.get("quantization_ns", 0) / 1_000_000,
            }

        def logger_clock():
            return self.adb.call(LOGGER, "clock")

        anchors = []
        fallback = None
        try:
            anchors.append(probe(logger_clock))
            samples = [probe(self.adb.kernel_clock) for _ in range(9)]
            anchors.append(probe(logger_clock))
            elapsed = [sample["elapsed_realtime_nanos"] for sample in samples]
            lower, upper = (anchor["elapsed_realtime_nanos"] for anchor in anchors)
            if (elapsed != sorted(elapsed) or elapsed[0] + samples[0]["quantization_ns"] < lower
                    or elapsed[-1] > upper):
                raise DeviceError("内核时钟与 Logger 单调时钟读数不一致")
        except DeviceDisconnected:
            raise
        except DeviceError as error:
            fallback = str(error)
            samples = [probe(logger_clock) for _ in range(9)]
        best = min(samples, key=lambda row: row["uncertainty_ms"])
        uncertainty = best["uncertainty_ms"]
        self.calibrations[label] = {
            "samples": samples, "best": best, "uncertainty_ms": uncertainty,
            "logger_anchors": anchors, "fallback_reason": fallback,
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

    def confirm(self, scene, instance, *, exit_scene=False, prepared=False):
        self.controls.manual.clear()
        self.controls.skip.clear()
        prompt = "请在手机上退出当前场景并确认" if exit_scene else (
            scene.instructions or f"请在手机上进入场景 {scene.scene_id}，完成后确认"
        )
        state = "WAITING_MANUAL_EXIT" if exit_scene else "WAITING_MANUAL_CONFIRM"
        if prepared:
            state = "MANUAL_CAPTURE_PREPARED"
            prompt = "采集已准备好。请在手机上切到目标场景，然后点击「开始手动采样」。"
        self.advance(state, instance, prompt)
        deadline = time.monotonic() + scene.manual_timeout_seconds
        while time.monotonic() < deadline:
            self.check_cancelled()
            if self.controls.skip.is_set():
                raise Skipped("操作员跳过场景")
            if self.controls.manual.wait(0.2):
                return
        reason = "准备采集后未在时限内开始手动采样" if prepared else "人工确认超时"
        raise Skipped(reason) if scene.on_failure == "skip" else DeviceError(reason)

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

    def display_state(self):
        output = self.adb.run("shell", "dumpsys", "display")
        states = set(re.findall(rb"\bmScreenState=([A-Z_]+)\b", output))
        if len(states) != 1:
            raise DeviceError("无法确认手机主屏状态")
        return states.pop().decode()

    def set_display_state(self, target, *, cancellable=True):
        current = self.display_state()
        if current == target:
            return
        button = "KEYCODE_WAKEUP" if target == "ON" else "KEYCODE_SLEEP"
        self.adb.run("shell", "input", "keyevent", button)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if cancellable:
                self.check_cancelled()
            current = self.display_state()
            if current == target:
                return
            time.sleep(0.25)
        if target == "OFF" and current.startswith("DOZE"):
            raise DeviceError(AOD_MESSAGE)
        raise DeviceError(f"手机主屏未切换到 {'亮屏' if target == 'ON' else '灭屏'}")

    def keyguard_showing(self):
        output = self.adb.run("shell", "dumpsys", "window", "policy")
        values = set(re.findall(rb"(?m)^\s*showing=(true|false)\s*$", output))
        if len(values) != 1:
            raise DeviceError("无法核实手机锁屏状态，请确认手机已解锁")
        return values == {b"true"}

    def wake_and_dismiss_keyguard(self, *, cancellable=True):
        self.set_display_state("ON", cancellable=cancellable)
        if not self.keyguard_showing():
            return
        self.adb.run("shell", "wm", "dismiss-keyguard")
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if cancellable:
                self.check_cancelled()
            if not self.keyguard_showing():
                return
            time.sleep(0.25)
        raise DeviceError("手机锁屏仍在显示，请在手机上解锁后重试")

    def execute_scene(self, scene: Scene, instance: str):
        auto = scene.mode == "auto" and scene.scene_id not in HOST_SCENES
        launcher = None
        display_target = DISPLAY_SCENES.get(scene.scene_id) if scene.mode == "auto" else None
        restore_display = None
        measurement_finished = False
        failure = None
        try:
            if auto:
                self.advance("WAIT_SCENE_READY", instance)
                self.start_auto(scene, instance)
                readiness = "app_ack"
            elif scene.mode == "manual":
                if self.prepare_manual:
                    self.confirm(scene, instance, prepared=True)
                elif not self.manual_ready:
                    self.confirm(scene, instance)
                readiness = "manual_preselected" if self.manual_ready else "manual_confirm"
            elif display_target:
                previous = self.display_state()
                if display_target == "OFF":
                    restore_display = "ON"
                elif previous not in {"ON", "OFF"}:
                    raise DeviceError("手机主屏当前处于过渡或息屏显示状态，无法安全切换")
                elif previous != display_target:
                    restore_display = previous
                self.set_display_state(display_target)
                readiness = "display_observed"
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
                if display_target:
                    current_display = self.display_state()
                    if display_target == "OFF" and current_display.startswith("DOZE"):
                        raise DeviceError(AOD_MESSAGE)
                    if current_display != display_target:
                        raise DeviceError(f"手机主屏状态在测试期间变为 {current_display}")

            self.wait(scene.stabilize_seconds, health, countdown=True)
            self.advance("MEASURING", instance)
            self.mark(instance, "MEASUREMENT_START")
            self.wait(scene.measure_seconds, health, countdown=True)
            health()
            self.mark(instance, "MEASUREMENT_END")
            measurement_finished = True
        except (DeviceError, Cancelled, Skipped) as error:
            failure = error
        finally:
            if restore_display:
                self.advance("STOPPING_SCENE", instance)
                try:
                    if display_target == "OFF":
                        self.wake_and_dismiss_keyguard(cancellable=False)
                    else:
                        self.set_display_state(restore_display, cancellable=False)
                except DeviceDisconnected:
                    raise
                except DeviceError as error:
                    raise SceneCleanupFailed(f"无法恢复手机主屏状态：{error}") from error
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
        if scene.mode == "manual" and not self.manual_ready:
            self.confirm(scene, instance, exit_scene=True)

    def run(self, plan: Plan) -> tuple[str, str]:
        if self.prepare_manual and not self.manual_ready:
            raise ValueError("准备采集只支持单独的手动采样")
        if self.manual_ready and (len(plan.scenes) != 1 or plan.scenes[0].mode != "manual"):
            raise ValueError("手动采样一次只执行一个手动场景")
        self.run_id = self.store.create(plan)
        self.progress = RunProgress(total=sum(scene.repeat for scene in plan.scenes))
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
            if any(s.mode == "auto" and s.scene_id == "screen_off" for s in plan.scenes):
                self.advance("PREPARING_DISPLAY")
                self.wake_and_dismiss_keyguard()
            needed = {s.scene_id for s in plan.scenes if s.mode == "auto"} - HOST_SCENES
            self.adb, logger, scene_app = resolve_apps(self.adb, needed, automatic=self.automatic_apps)
            self.check_cancelled()
            write_json(self.folder / "environment.json", {
                "logger": logger, "scene": scene_app,
                "app_variant": "side_by_side" if self.adb.test_apps else "standard",
                "device_hash": hashlib.sha256(self.adb.serial.encode()).hexdigest()[:12],
            })
            variant = "并装联调版" if self.adb.test_apps else "正式版"
            self.advance("LOGGER_START", reason=f"已通过{variant}应用检查")
            if not self.manual_ready or self.prepare_manual:
                output = self.adb.run("shell", "am", "start", "-W", "-n",
                                      self.adb.component(LOGGER, "MainActivity"))
                if b"Error:" in output:
                    raise DeviceError("无法打开 Logger 主界面")
            logger_requested = True
            arg = self.run_id + (":nmea" if plan.collect_nmea else "")
            if not plan.collect_raw:
                arg += ":status_only"
            try:
                self.adb.call(LOGGER, "start", arg)
                self.session = self.wait_state(LOGGER, self.run_id, "STARTED")
            except DeviceError as error:
                if "LOGGER_BUSY" in str(error):
                    logger_requested = False
                    raise DeviceError("采集应用已有任务在运行，请结束该任务后重试。") from error
                if self.manual_ready and "FOREGROUND_START_DENIED" in str(error):
                    logger_requested = False
                    raise DeviceError("手机拒绝后台启动采集。请先点「准备采集」，再手动切到目标场景。") from error
                raise
            self.advance("LOGGER_READY")
            self.calibrate("start", plan)
            processed = 0
            for scene in plan.scenes:
                for repetition in range(1, scene.repeat + 1):
                    if self.controls.pause.is_set():
                        self.advance("PAUSED")
                        while self.controls.pause.is_set():
                            self.wait(0.2)
                        self.logger_health()
                    self.check_cancelled()
                    instance = str(uuid4())
                    self.report_progress(scene=scene.scene_id, repetition=repetition, repeats=scene.repeat)
                    self.advance("PREPARING_SCENE", instance, scene.scene_id)
                    try:
                        self.execute_scene(scene, instance)
                    except Skipped as error:
                        self.advance("SKIPPED", instance, str(error))
                    processed += 1
                    self.report_progress(processed=processed, scene="", repetition=0, repeats=0)
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
                    if any(row.get("reference_status") == "MISSING_SCREEN_OFF_REFERENCE"
                           for row in result.get("quality", [])):
                        state, reason = "COMPLETED_WITH_WARNINGS", "缺少有效灭屏基准"
                    elif any(row["quality"] == "TIME_SYNC_LOW_CONFIDENCE"
                           for row in self.calibrations.values()):
                        state, reason = "COMPLETED_WITH_WARNINGS", "TIME_SYNC_LOW_CONFIDENCE"
                    elif (not (result["comparisons"] or result.get("windows")) or any(
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
