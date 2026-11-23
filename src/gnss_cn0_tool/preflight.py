import re
from dataclasses import dataclass

from gnss_cn0_tool.adb import LOGGER, SCENE, Adb, DeviceDisconnected, DeviceError


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str

    def __str__(self):
        return f"{self.name} · {self.status} · {self.detail}"


def read_setting(adb, *args):
    try:
        return adb.run("shell", *args, timeout=8).decode(errors="replace").strip()
    except DeviceDisconnected:
        raise
    except DeviceError:
        return ""


def package_info(adb, package, user):
    dump = read_setting(adb, "dumpsys", "package", adb.package(package))
    version_name = re.search(r"(?m)^\s*versionName=(\S+)", dump)
    version_code = re.search(r"(?m)^\s*versionCode=(\d+)", dump)
    users = re.split(r"(?m)^\s*User (\d+):", dump)
    current = next((data for number, data in zip(users[1::2], users[2::2], strict=True) if number == user), "")
    permissions = dict(re.findall(r"(?m)^\s*android\.permission\.([A-Z_]+): granted=(true|false)\b", current))
    return permissions, {
        "app_version": version_name[1] if version_name else None,
        "version_code": int(version_code[1]) if version_code else None,
    }


def permission_check(name, value, action, *, required=True):
    if value == "true":
        return Check(name, "通过", "已授予")
    if value == "false":
        return Check(name, "失败" if required else "提示", action)
    return Check(name, "待确认", action)


def inspect_settings(adb: Adb, needed, *, screen_off=False, manual_background=False):
    user = read_setting(adb, "am", "get-current-user")
    logger_permissions, logger_version = package_info(adb, LOGGER, user)
    scene_permissions, scene_version = package_info(adb, SCENE, user)
    location = read_setting(adb, "cmd", "location", "is-location-enabled", "--user", "current")
    if location == "true":
        location_check = Check("系统定位", "通过", "已开启")
    else:
        location_check = Check("系统定位", "失败" if location == "false" else "待确认", "请在手机设置确认定位已开启")
    checks = [
        location_check,
        permission_check("精确定位", logger_permissions.get("ACCESS_FINE_LOCATION"),
                         "请在 GNSS Logger 权限页启用精确位置"),
    ]
    if manual_background:
        background = permission_check("后台定位", logger_permissions.get("ACCESS_BACKGROUND_LOCATION"),
                                      "可先点「准备采集」，或检查 Logger 的后台定位授权", required=False)
        checks.append(background)
    for ids, permission, name, action in (
        ({"front_camera", "rear_camera"}, "CAMERA", "摄像头",
         "请在场景 App 权限页授予摄像头权限"),
        ({"recorder"}, "RECORD_AUDIO", "录音机",
         "请在场景 App 权限页授予麦克风权限"),
    ):
        if needed & ids:
            checks.append(permission_check(name, scene_permissions.get(permission), action))
    if screen_off:
        manufacturer = read_setting(adb, "getprop", "ro.product.manufacturer").casefold()
        switch = "aod_switch" if manufacturer in {"honor", "huawei"} else "doze_always_on"
        value = read_setting(adb, "settings", "--user", "current", "get", "secure", switch)
        if value == "1":
            checks.append(Check("熄屏显示", "失败", "请关闭熄屏显示后再测全灭屏基准"))
        elif value == "0":
            checks.append(Check("熄屏显示", "通过", "已读到关闭；采样前仍会验证主屏全灭"))
        else:
            checks.append(Check("熄屏显示", "待确认", "请检查手机设置；真正灭屏会在测试时核实"))
    return checks, logger_version, scene_version
