"""Live status updates from the device stream: what each one hands to the coordinator.

Built from the real stream messages, so a reading that is dropped, mapped to the
wrong label or attached to the wrong device cannot pass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from v3.mobilegwsvc.commonmodels.space.device.light import light_device_status_pb2
from v3.mobilegwsvc.service.stream_light_devices import response_pb2

from custom_components.aegis_ajax.api.devices import DevicesApi

if TYPE_CHECKING:
    from collections.abc import Callable

_Status = light_device_status_pb2.LightDeviceStatus
_UPDATE = 2


def _handle(fill: Callable[[Any], None], op: int = _UPDATE) -> tuple[str, str, dict[str, Any]]:
    """Run one status update through the handler; return what the coordinator receives."""
    update = response_pb2.StreamLightDevicesResponse.Success.Update()
    update.device_id.hub_light_device_id.device_id = "3000AC9E"
    fill(update.status_update.status)
    update.status_update.update_type = op
    received: list[tuple[str, str, dict[str, Any]]] = []
    DevicesApi(MagicMock())._handle_update(
        update,
        on_devices_snapshot=lambda *_args, **_kwargs: pytest.fail("not a snapshot"),
        on_status_update=lambda device_id, name, payload: received.append(
            (device_id, name, payload)
        ),
    )
    (only,) = received
    return only


def _life_quality(status: Any) -> None:  # noqa: ANN401
    status.life_quality.actual_temperature = 215
    status.life_quality.actual_humidity = 45
    status.life_quality.actual_co2 = 800


def _gsm(network: int, state: int) -> Callable[[Any], None]:
    def fill(status: Any) -> None:  # noqa: ANN401
        status.gsm_status.type = network
        status.gsm_status.status = state

    return fill


def _set(path: str, value: Any) -> Callable[[Any], None]:  # noqa: ANN401
    """Set ``status.<a>.<b>`` (or the bare ``status.<a>``) to ``value``."""

    def fill(status: Any) -> None:  # noqa: ANN401
        *parents, leaf = path.split(".")
        target = status
        for name in parents:
            target = getattr(target, name)
        setattr(target, leaf, value)

    return fill


@pytest.mark.parametrize(
    ("name", "fill", "expected"),
    [
        ("temperature", _set("temperature.value", 21), {"value": 21}),
        (
            "life_quality",
            _life_quality,
            {"values": {"temperature": 215, "humidity": 45, "co2": 800}},
        ),
        ("signal_strength", _set("signal_strength.device_signal_level", 4), {"value": "Strong"}),
        ("signal_strength", _set("signal_strength.device_signal_level", 2), {"value": "Weak"}),
        (
            "gsm_status",
            _gsm(3, 2),
            {"values": {"mobile_network_type": "4G", "gsm_connected": True}},
        ),
        (
            "gsm_status",
            _gsm(1, 1),
            {"values": {"mobile_network_type": "2G", "gsm_connected": False}},
        ),
        ("monitoring", _set("monitoring.cms_active", True), {"value": True}),
        ("sim_status", _set("sim_status.sim_card_status", 2), {"value": "Missing"}),
        ("sim_status", _set("sim_status.sim_card_status", 1), {"value": "OK"}),
        ("nfc", _set("nfc.enabled", True), {"value": True}),
        (
            "wifi_signal_level_status",
            _set("wifi_signal_level_status.wifi_signal_level", 3),
            {"value": 3},
        ),
        ("smart_lock", _set("smart_lock", 2), {"value": "locked"}),
        ("smart_lock", _set("smart_lock", 3), {"value": "unlatched"}),
        ("lock_control_status", _set("lock_control_status.state", 1), {"value": "locked"}),
        ("lock_control_status", _set("lock_control_status.state", 3), {"value": "unlatched"}),
    ],
)
def test_status_update_reaches_the_coordinator_with_its_reading(
    name: str, fill: Callable[[Any], None], expected: dict[str, Any]
) -> None:
    assert _handle(fill) == ("3000AC9E", name, {"op": _UPDATE, **expected})


@pytest.mark.parametrize("name", ["wire_input_status", "transmitter_status"])
def test_wired_input_alert_carries_its_alarm_type(name: str) -> None:
    def fill(status: Any) -> None:  # noqa: ANN401
        getattr(status, name).is_alert = True
        getattr(status, name).type = 2

    assert _handle(fill) == (
        "3000AC9E",
        name,
        {"op": _UPDATE, "is_alert": True, "alarm_type": "fire"},
    )


def test_readings_switched_off_are_reported_as_off() -> None:
    def monitoring_off(status: Any) -> None:  # noqa: ANN401
        status.monitoring.SetInParent()

    def nfc_off(status: Any) -> None:  # noqa: ANN401
        status.nfc.SetInParent()

    assert _handle(monitoring_off)[2] == {"op": _UPDATE, "value": False}
    assert _handle(nfc_off)[2] == {"op": _UPDATE, "value": False}


def test_the_update_type_is_passed_on() -> None:
    assert _handle(_set("temperature.value", 21), op=1)[2] == {"op": 1, "value": 21}
