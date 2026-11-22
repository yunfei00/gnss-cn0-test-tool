import base64
import json

import pytest

from gnss_cn0_tool.adb import LOGGER, Adb, DeviceDisconnected, DeviceError


def test_adb_discovery_and_device_selection(monkeypatch):
    adb = Adb(serial="selected")
    monkeypatch.setattr(adb, "run", lambda *_args, **_kwargs:
                        b"List of devices attached\nother\tdevice\nselected\tunauthorized\n")
    assert [device.state for device in adb.devices()] == ["device", "unauthorized"]
    with pytest.raises(DeviceError, match="授权"):
        adb.ensure_connected()
    adb.serial = "missing"
    with pytest.raises(DeviceDisconnected):
        adb.ensure_connected()


def test_adb_capability_handshake(monkeypatch):
    adb = Adb(serial="fixture")

    def response(data):
        encoded = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=")
        monkeypatch.setattr(adb, "run", lambda *_args, **_kwargs: b"Result: Bundle[{reply=" + encoded + b"}]")

    response({"protocol_version": 1, "operations": ["start", "stop"]})
    assert adb.capabilities(LOGGER, {"start"})["protocol_version"] == 1
    with pytest.raises(DeviceError, match="缺少"):
        adb.capabilities(LOGGER, {"mark"})
    response({"state": "ERROR", "reason": "LOGGER_BUSY"})
    with pytest.raises(DeviceError, match="LOGGER_BUSY"):
        adb.call(LOGGER, "start")
