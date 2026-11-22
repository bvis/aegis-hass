"""What each device command actually sends to the hub, and how it reads the answer.

Real request and response messages, with only the gRPC stub replaced: a command
aimed at the wrong hub or device, sent without the session metadata, or a
rejection read as success cannot pass.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax.api.devices import (
    SMART_LOCK_ACTION_LOCK,
    DeviceCommandError,
    DevicesApi,
    SmartLockError,
)
from custom_components.aegis_ajax.api.models import DeviceCommand

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from google.protobuf.message import Message

_SERVICES = "v3.mobilegwsvc.service"
_METADATA = [("token", "abc")]


def _command(action: str, device_type: str = "relay", **fields: Any) -> DeviceCommand:  # noqa: ANN401
    return DeviceCommand(
        action=action, hub_id="hub-1", device_id="dev-1", device_type=device_type, **fields
    )


@dataclass(frozen=True)
class _Spec:
    """One command: where its stub lives and how to send it."""

    service: str  # package under v3.mobilegwsvc.service
    name: str  # prefix shared by the stub, request and response classes
    send: Callable[[DevicesApi], Awaitable[None]]
    error_prefix: str | None  # None: the error is the bare reason (smart lock)


_SPECS: dict[str, _Spec] = {
    "on": _Spec(
        "device_command_device_on",
        "DeviceCommandDeviceOn",
        lambda api: api.send_command(_command("on", channels=[1, 2])),
        "on",
    ),
    "off": _Spec(
        "device_command_device_off",
        "DeviceCommandDeviceOff",
        lambda api: api.send_command(_command("off", channels=[1, 2])),
        "off",
    ),
    "brightness": _Spec(
        "device_command_brightness",
        "DeviceCommandBrightness",
        lambda api: api.send_command(_command("brightness", "light_switch_dimmer", brightness=40)),
        "brightness",
    ),
    "bypass": _Spec(
        "device_command_device_bypass",
        "DeviceCommandDeviceBypass",
        lambda api: api.send_command(_command("bypass", "door_protect", bypass_enable=True)),
        "bypass",
    ),
    "siren_settings": _Spec(
        "update_hub_device",
        "UpdateHubDevice",
        lambda api: api.send_command(
            _command("siren_settings", "door_protect", alarm_duration=180)
        ),
        "siren_settings",
    ),
    "night_mode_arm": _Spec(
        "update_hub_device",
        "UpdateHubDevice",
        lambda api: api.send_command(
            _command("night_mode_arm", "door_protect", night_mode_arm=True)
        ),
        "night_mode_arm",
    ),
    "always_active": _Spec(
        "update_hub_device",
        "UpdateHubDevice",
        lambda api: api.send_command(_command("always_active", "door_protect", always_active=True)),
        "always_active",
    ),
    "sound_test": _Spec(
        "device_command_stest_start",
        "DeviceCommandSoundTestStart",
        lambda api: api.start_sound_test("hub-1", "dev-1", "door_protect"),
        "sound_test",
    ),
    "chimes_mode": _Spec(
        "device_command_chimes_mode",
        "DeviceCommandChimesMode",
        lambda api: api.set_chimes_mode("hub-1", enable=True),
        "chimes_mode",
    ),
    "smart_lock": _Spec(
        "switch_smart_lock",
        "SwitchSmartLock",
        lambda api: api.switch_smart_lock("space-1", "lock-1", SMART_LOCK_ACTION_LOCK),
        None,
    ),
}


class _Harness:
    """A DevicesApi whose stub for one command answers with the given response."""

    def __init__(self, key: str, response: Message | None = None) -> None:
        self.spec = _SPECS[key]
        package = f"{_SERVICES}.{self.spec.service}"
        self.request_cls = getattr(
            importlib.import_module(f"{package}.request_pb2"), f"{self.spec.name}Request"
        )
        self.response_cls = getattr(
            importlib.import_module(f"{package}.response_pb2"), f"{self.spec.name}Response"
        )
        self._stub_path = f"{package}.endpoint_pb2_grpc.{self.spec.name}ServiceStub"
        client = MagicMock()
        self.channel = client._get_channel.return_value
        client._session.get_call_metadata.return_value = _METADATA
        self.api = DevicesApi(client)
        self.rpc = AsyncMock(return_value=response if response is not None else self.success())
        self.stub_cls = MagicMock()
        self.stub_cls.return_value.execute = self.rpc

    def success(self) -> Message:
        response = self.response_cls()
        response.success.SetInParent()
        return response

    def failure(self, error: str | None) -> Message:
        response = self.response_cls()
        response.failure.SetInParent()
        if error:
            getattr(response.failure, error).SetInParent()
        return response

    async def run(self, send: Callable[[DevicesApi], Awaitable[None]] | None = None) -> Any:  # noqa: ANN401
        with patch(self._stub_path, self.stub_cls):
            await (send or self.spec.send)(self.api)
        return self.rpc.await_args.args[0]


@pytest.mark.parametrize("key", list(_SPECS))
async def test_command_goes_out_once_with_the_session_metadata(key: str) -> None:
    harness = _Harness(key)
    request = await harness.run()
    harness.stub_cls.assert_called_once_with(harness.channel)
    harness.rpc.assert_awaited_once()
    assert harness.rpc.await_args.kwargs == {"metadata": _METADATA, "timeout": 15}
    assert type(request) is harness.request_cls


@pytest.mark.parametrize("key", [key for key in _SPECS if key not in ("chimes_mode", "smart_lock")])
async def test_command_names_its_hub_and_device(key: str) -> None:
    request = await _Harness(key).run()
    assert (request.hub_id, request.device_id) == ("hub-1", "dev-1")


@pytest.mark.parametrize("key", list(_SPECS))
async def test_a_rejected_command_raises_with_the_reason(key: str) -> None:
    harness = _Harness(key)
    harness.rpc.return_value = harness.failure("permission_denied")
    error_cls = SmartLockError if key == "smart_lock" else DeviceCommandError
    with pytest.raises(error_cls) as err:
        await harness.run()
    prefix = harness.spec.error_prefix
    assert str(err.value) == (f"{prefix}: permission_denied" if prefix else "permission_denied")
    if prefix:
        assert err.value.reason == "permission_denied"
    harness.rpc.assert_awaited_once()


@pytest.mark.parametrize("key", list(_SPECS))
async def test_a_failure_without_a_reason_is_still_an_error(key: str) -> None:
    harness = _Harness(key)
    harness.rpc.return_value = harness.failure(None)
    error_cls = SmartLockError if key == "smart_lock" else DeviceCommandError
    with pytest.raises(error_cls) as err:
        await harness.run()
    prefix = harness.spec.error_prefix
    assert str(err.value) == (f"{prefix}: unknown" if prefix else "unknown")


@pytest.mark.parametrize("key", ["on", "off"])
async def test_on_and_off_carry_the_device_type_and_channels(key: str) -> None:
    request = await _Harness(key).run()
    assert request.device_type.WhichOneof("type") == "relay"
    assert list(request.channels) == [1, 2]


async def test_brightness_request() -> None:
    harness = _Harness("brightness")
    request = await harness.run()
    assert request.device_type.WhichOneof("type") == "light_switch_dimmer"
    assert request.brightness_in_percentage == 40
    assert request.brightness_type == 2
    # Without a channel the dimmer's first one is used.
    assert list(request.channels) == [1]

    request = await _Harness("brightness").run(
        lambda api: api.send_command(
            _command("brightness", "light_switch_dimmer", brightness=5, channels=[2])
        )
    )
    assert list(request.channels) == [2]
    assert request.brightness_in_percentage == 5


async def test_brightness_without_a_value_is_refused_before_any_call() -> None:
    harness = _Harness("brightness")
    with pytest.raises(DeviceCommandError, match="requires a brightness value"):
        await harness.run(
            lambda api: api.send_command(_command("brightness", "light_switch_dimmer"))
        )
    harness.rpc.assert_not_awaited()


@pytest.mark.parametrize(("enable", "bypass"), [(True, "SENSOR"), (False, "DISABLE")])
async def test_bypass_request(enable: bool, bypass: str) -> None:
    harness = _Harness("bypass")
    request = await harness.run(
        lambda api: api.send_command(_command("bypass", "door_protect", bypass_enable=enable))
    )
    assert request.object_type.WhichOneof("type") == "door_protect"
    assert request.bypass_type == getattr(harness.request_cls, f"BYPASS_ENGINEERING_{bypass}")


@pytest.mark.parametrize(("enable", "bypass"), [(True, "SENSOR"), (False, "DISABLE")])
async def test_one_time_bypass_request(enable: bool, bypass: str) -> None:
    """The one-time family has its own values, the clear included (#694)."""
    harness = _Harness("bypass")
    request = await harness.run(
        lambda api: api.send_command(
            _command("bypass", "wire_input_mt", bypass_enable=enable, bypass_once=True)
        )
    )
    assert request.bypass_type == getattr(harness.request_cls, f"BYPASS_ONE_TIME_{bypass}")


@pytest.mark.parametrize(
    ("values", "sent"),
    [
        ({"alarm_duration": 180}, {"alarm_duration": 180}),
        ({"siren_volume_level": 2}, {"siren_volume_level": 2}),
        (
            {"alarm_duration": 60, "siren_volume_level": 3},
            {"alarm_duration": 60, "siren_volume_level": 3},
        ),
    ],
)
async def test_siren_settings_send_only_the_values_given(
    values: dict[str, int], sent: dict[str, int]
) -> None:
    request = await _Harness("siren_settings").run(
        lambda api: api.send_command(_command("siren_settings", "door_protect", **values))
    )
    assert request.object_type.WhichOneof("type") == "door_protect"
    (update,) = request.update
    assert [field.name for field, _ in update.ListFields()] == ["siren_settings"]
    assert {field.name: value for field, value in update.siren_settings.ListFields()} == sent


async def test_siren_settings_without_a_value_are_refused_before_any_call() -> None:
    harness = _Harness("siren_settings")
    with pytest.raises(DeviceCommandError, match="requires at least one value"):
        await harness.run(lambda api: api.send_command(_command("siren_settings", "door_protect")))
    harness.rpc.assert_not_awaited()


@pytest.mark.parametrize(("enable", "name"), [(True, "ENABLED"), (False, "DISABLED")])
async def test_night_mode_arm_request(enable: bool, name: str) -> None:
    request = await _Harness("night_mode_arm").run(
        lambda api: api.send_command(
            _command("night_mode_arm", "door_protect", night_mode_arm=enable)
        )
    )
    assert request.object_type.WhichOneof("type") == "door_protect"
    (update,) = request.update
    assert [field.name for field, _ in update.ListFields()] == ["common_arming_part_settings"]
    settings = update.common_arming_part_settings
    assert [field.name for field, _ in settings.ListFields()] == ["night_mode_arm"]
    part = type(settings).DESCRIPTOR.containing_type
    assert settings.night_mode_arm == part.enum_values_by_name[f"NIGHT_MODE_ARM_{name}"].number


async def test_night_mode_arm_without_a_value_is_refused_before_any_call() -> None:
    harness = _Harness("night_mode_arm")
    with pytest.raises(DeviceCommandError, match="requires a value"):
        await harness.run(lambda api: api.send_command(_command("night_mode_arm", "door_protect")))
    harness.rpc.assert_not_awaited()


@pytest.mark.parametrize(("enable", "value"), [(True, 2), (False, 1)])
async def test_always_active_request(enable: bool, value: int) -> None:
    request = await _Harness("always_active").run(
        lambda api: api.send_command(
            _command("always_active", "door_protect", always_active=enable)
        )
    )
    assert request.object_type.WhichOneof("type") == "door_protect"
    (update,) = request.update
    assert [field.name for field, _ in update.ListFields()] == ["common_arming_part_settings"]
    settings = update.common_arming_part_settings
    # Only this field goes out, so night mode, delays and arming mode stay as they are.
    assert [field.name for field, _ in settings.ListFields()] == ["always_active"]
    assert settings.always_active == value


async def test_always_active_without_a_value_is_refused_before_any_call() -> None:
    harness = _Harness("always_active")
    with pytest.raises(DeviceCommandError, match="requires a value"):
        await harness.run(lambda api: api.send_command(_command("always_active", "door_protect")))
    harness.rpc.assert_not_awaited()


async def test_sound_test_names_the_device_type() -> None:
    request = await _Harness("sound_test").run()
    assert request.object_type.WhichOneof("type") == "door_protect"


@pytest.mark.parametrize(("enable", "status"), [(True, "ENABLE"), (False, "DISABLE")])
async def test_chimes_mode_request(enable: bool, status: str) -> None:
    harness = _Harness("chimes_mode")
    request = await harness.run(lambda api: api.set_chimes_mode("hub-1", enable=enable))
    assert request.hub_id == "hub-1"
    assert request.chimes_status == getattr(harness.request_cls, f"CHIMES_STATUS_{status}")


async def test_smart_lock_request() -> None:
    request = await _Harness("smart_lock").run()
    assert (request.space_id, request.smart_lock_id) == ("space-1", "lock-1")
    assert request.action == SMART_LOCK_ACTION_LOCK


async def test_an_unknown_action_is_refused() -> None:
    with pytest.raises(DeviceCommandError, match="Unknown device command action: explode"):
        await DevicesApi(MagicMock()).send_command(_command("explode"))
