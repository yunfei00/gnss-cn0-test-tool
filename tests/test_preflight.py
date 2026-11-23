from gnss_cn0_tool.adb import LOGGER, SCENE, Adb
from gnss_cn0_tool.preflight import inspect_settings


def test_preflight_checks_current_user_and_selected_scenes(monkeypatch):
    adb = Adb(serial="test", test_apps=True)
    commands = {
        ("am", "get-current-user"): "10",
        ("cmd", "location", "is-location-enabled", "--user", "current"): "true",
        ("dumpsys", "package", adb.package(LOGGER)): """
          versionName=1.1.0
          versionCode=2 minSdk=26
          User 0:
            android.permission.ACCESS_FINE_LOCATION: granted=false
          User 10:
            android.permission.ACCESS_FINE_LOCATION: granted=true
            android.permission.ACCESS_BACKGROUND_LOCATION: granted=false
        """,
        ("dumpsys", "package", adb.package(SCENE)): """
          versionName=1.1.0
          versionCode=2 minSdk=29
          User 10:
            android.permission.CAMERA: granted=true
            android.permission.RECORD_AUDIO: granted=false
        """,
        ("getprop", "ro.product.manufacturer"): "HONOR",
        ("settings", "--user", "current", "get", "secure", "aod_switch"): "1",
    }

    def read(shell, *args, **kwargs):
        assert shell == "shell"
        return commands[args].encode()

    monkeypatch.setattr(adb, "run", read)
    checks, logger, scene = inspect_settings(adb, {"recorder"}, screen_off=True, manual_background=True)
    assert {check.name: check.status for check in checks} == {
        "系统定位": "通过", "精确定位": "通过", "后台定位": "提示", "录音机": "失败", "熄屏显示": "失败",
    }
    assert logger == scene == {"app_version": "1.1.0", "version_code": 2}
