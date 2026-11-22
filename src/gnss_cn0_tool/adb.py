import base64
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

LOGGER = "com.example.gnsslogger"
SCENE = "com.yunfei.autotestscene"
EXPORT_FILES = {"satellites.csv", "raw.csv", "nmea.csv", "standard.nmea", "events.jsonl"}
SAFE_TOKEN = re.compile(r"[A-Za-z0-9_.:-]+")


class DeviceError(RuntimeError):
    pass


class DeviceDisconnected(DeviceError):
    pass


@dataclass(frozen=True)
class Device:
    serial: str
    state: str

    @property
    def label(self) -> str:
        suffix = hashlib.sha256(self.serial.encode()).hexdigest()[:8]
        return f"{suffix} ({self.state})"


class Adb:
    def __init__(self, executable: str = "adb", serial: str | None = None, *, test_apps=False):
        self.executable = executable
        self.serial = serial
        self.test_apps = test_apps

    def package(self, namespace: str) -> str:
        return namespace + (".gnsstest" if self.test_apps else "")

    def component(self, namespace: str, activity: str) -> str:
        return f"{self.package(namespace)}/{namespace}.{activity}"

    def run(
        self, *args: str, timeout: float = 20, host: bool = False, output=None
    ) -> bytes:
        serial_args = []
        if not host:
            if not self.serial:
                raise DeviceError("请显式选择 ADB 设备")
            serial_args = ["-s", self.serial]
        try:
            result = subprocess.run(
                [self.executable, *serial_args, *args], timeout=timeout,
                stdout=output if output is not None else subprocess.PIPE,
                stderr=subprocess.PIPE, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError as error:
            raise DeviceError("找不到 adb，请安装 Android Platform Tools") from error
        except subprocess.TimeoutExpired as error:
            raise DeviceError("ADB 命令超时") from error
        diagnostic = (result.stderr + (result.stdout or b"")).lower()
        if result.returncode and any(
            marker in diagnostic for marker in (b"not found", b"offline", b"no devices", b"closed")
        ):
            raise DeviceDisconnected("ADB 设备断开或离线")
        if result.returncode:
            raise DeviceError(f"ADB 操作失败，退出码 {result.returncode}")
        return result.stdout or b""

    def devices(self) -> list[Device]:
        devices = []
        for line in self.run("devices", host=True).decode(errors="replace").splitlines():
            columns = line.split()
            if len(columns) >= 2 and columns[1] in {"device", "offline", "unauthorized"}:
                devices.append(Device(columns[0], columns[1]))
        return devices

    def ensure_connected(self) -> None:
        device = next((d for d in self.devices() if d.serial == self.serial), None)
        if device is None or device.state == "offline":
            raise DeviceDisconnected("选定手机已断开或离线")
        if device.state != "device":
            raise DeviceError("请在手机上确认 USB 调试授权")

    def call(self, package: str, method: str, arg: str | None = None) -> dict:
        options = ["--arg", arg] if arg else []
        if not all(SAFE_TOKEN.fullmatch(value) for value in [package, method, arg or "_"]):
            raise ValueError("非法协议参数")
        result = self.run(
            "shell", "content", "call", "--uri", f"content://{self.package(package)}.gnss_test",
            "--method", method, *options,
            timeout=120 if method == "export" else 20,
        )
        payload = re.search(rb"\breply=([A-Za-z0-9_-]+)", result)
        if not payload:
            raise DeviceError(f"{package} 的 {method} 端点未返回协议 v1 响应")
        encoded = payload.group(1)
        try:
            reply = json.loads(base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4)))
        except (ValueError, UnicodeError) as error:
            raise DeviceError("Android 协议响应格式无效") from error
        if not isinstance(reply, dict):
            raise DeviceError("Android 协议响应必须是 JSON 对象")
        if reply.get("state") == "ERROR":
            reason = reply.get("reason", "UNKNOWN_ERROR")
            raise DeviceError(f"{method}: {reason}")
        return reply

    def capabilities(self, package: str, operations: set[str]) -> dict:
        reply = self.call(package, "capabilities")
        if reply.get("protocol_version") != 1 or not operations <= set(reply.get("operations", [])):
            raise DeviceError(f"{package} 缺少需要的 v1 能力")
        return reply

    def kernel_clock(self) -> dict:
        output = self.run("shell", "cat /proc/uptime && date +%s%N", timeout=5)
        match = re.fullmatch(rb"(\d{1,12})\.(\d{1,9}) [^\r\n]+\r?\n(\d{19})\s*", output)
        if match is None:
            raise DeviceError("Android 内核时钟响应格式不受支持")
        seconds, fraction, utc = match.groups()
        quantum = 10 ** (9 - len(fraction))
        return {
            "elapsed_realtime_nanos": int(seconds) * 1_000_000_000 + int(fraction) * quantum,
            "timestamp_ms": int(utc) // 1_000_000,
            "quantization_ns": quantum,
            "source": "android_proc_uptime",
        }

    def export(self, run_id: str, folder: Path) -> dict:
        if str(UUID(run_id)) != run_id:
            raise ValueError("无效运行 ID")
        manifest = self.call(LOGGER, "export", run_id)
        if manifest.get("schema_version") != 1 or manifest.get("test_run_id") != run_id:
            raise DeviceError("导出清单不属于当前运行")
        files = manifest.get("files", [])
        names = [entry["name"] for entry in files]
        if len(names) != len(set(names)) or set(names) - EXPORT_FILES:
            raise DeviceError("导出清单包含非法文件名或重复项")
        if not {"satellites.csv", "events.jsonl"} <= set(names):
            raise DeviceError("导出缺少卫星 CSV 或事件")
        folder.mkdir(parents=True, exist_ok=True)
        for entry in files:
            name = entry["name"]
            path = folder / name
            if path.exists():
                verify_file(path, entry)
                continue
            partial = folder / (name + ".partial")
            with partial.open("wb") as stream:
                self.run(
                    "exec-out", "content", "read", "--uri",
                    f"content://{self.package(LOGGER)}.gnss_test/files/{run_id}/{name}",
                    output=stream, timeout=120,
                )
            verify_file(partial, entry)
            partial.rename(path)
        manifest_path = folder / "manifest.json"
        if manifest_path.exists():
            if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
                raise DeviceError("已保存原始清单与设备清单不同")
        else:
            with manifest_path.open("x", encoding="utf-8") as stream:
                json.dump(manifest, stream, indent=2)
        return manifest


def verify_file(path: Path, entry: dict) -> None:
    digest = hashlib.sha256()
    lines = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
            lines += block.count(b"\n")
    if (path.stat().st_size, digest.hexdigest(), lines) != (
        entry["size"], entry["sha256"], entry["lines"]
    ):
        raise DeviceError(f"完整性校验失败：{entry['name']}")
