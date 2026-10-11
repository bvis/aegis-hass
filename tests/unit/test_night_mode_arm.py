"""Tests for the per-detector "Arm in night mode" switch (#579)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Wire up the proto search path before any `systems.*` import.
from custom_components.aegis_ajax import device_handlers
from custom_components.aegis_ajax.api import _proto_path as _proto_path  # noqa: F401
from custom_components.aegis_ajax.api.devices import DeviceCommandError, DevicesApi
from custom_components.aegis_ajax.api.models import Device, DeviceCommand
from custom_components.aegis_ajax.const import DeviceState
from custom_components.aegis_ajax.device_handlers import capabilities_for
from custom_components.aegis_ajax.switch import AjaxNightModeArmSwitch, async_setup_entry

STUB = "v3.mobilegwsvc.service.update_hub_device.endpoint_pb2_grpc.UpdateHubDeviceServiceStub"


def _device(
    device_type: str = "door_protect",
    *,
    statuses: dict | None = None,
    state: DeviceState = DeviceState.ONLINE,
) -> Device:
    return Device(
        id="30EA219B",
        hub_id="hub-1",
        name="Door",
        device_type=device_type,
        room_id=None,
        group_id=None,
        state=state,
        malfunctions=0,
        bypassed=False,
        statuses=statuses or {},
        battery=None,
    )


_GATED = tuple(
    sorted(
        t
        for t in device_handlers._DEVICE_HANDLERS
        if capabilities_for(_device(t)).has_night_mode_arm
    )
)


class TestGate:
    @pytest.mark.parametrize(
        "device_type",
        ["door_protect", "motion_protect", "motion_cam_phod", "combi_protect", "glass_protect"],
    )
    def test_detectors_get_it(self, device_type: str) -> None:
        assert capabilities_for(_device(device_type)).has_night_mode_arm

    @pytest.mark.parametrize(
        "device_type", ["fire_protect", "leak_protect", "home_siren", "keypad_combi", "hub"]
    )
    def test_other_families_dont(self, device_type: str) -> None:
        assert not capabilities_for(_device(device_type)).has_night_mode_arm

    @pytest.mark.parametrize("device_type", _GATED)
    def test_every_gated_type_is_a_real_object_type(self, device_type: str) -> None:
        """The write turns device_type back into an ObjectType; an unknown one can only fail."""
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels import object_type_pb2

        assert hasattr(object_type_pb2.ObjectType(), device_type)


class TestWrite:
    async def _send(self, *, enable: bool, response: MagicMock) -> MagicMock:
        client = MagicMock()
        api = DevicesApi(client)
        stub = MagicMock()
        stub.return_value.execute = AsyncMock(return_value=response)
        with patch(STUB, stub):
            await api.send_command(
                DeviceCommand.set_night_mode_arm(
                    hub_id="hub-1", device_id="30EA219B", device_type="door_protect", enable=enable
                )
            )
        return stub.return_value.execute.call_args.args[0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("enable", "value"), [(True, 2), (False, 1)])
    async def test_sends_only_the_night_mode_setting(self, enable: bool, value: int) -> None:
        ok = MagicMock()
        ok.HasField.return_value = False
        request = await self._send(enable=enable, response=ok)

        assert request.hub_id == "hub-1"
        assert request.device_id == "30EA219B"
        assert request.object_type.WhichOneof("type") == "door_protect"
        (update,) = request.update
        settings = update.common_arming_part_settings
        assert settings.night_mode_arm == value
        # Only the one optional field is present, so delays and arming mode are untouched.
        assert [f.name for f, _ in settings.ListFields()] == ["night_mode_arm"]

    @pytest.mark.asyncio
    async def test_failure_carries_the_reason(self) -> None:
        failed = MagicMock()
        failed.HasField.return_value = True
        failed.failure.WhichOneof.return_value = "hub_wrong_state"
        with pytest.raises(DeviceCommandError) as err:
            await self._send(enable=False, response=failed)
        assert err.value.reason == "hub_wrong_state"


class TestSwitch:
    def _switch(self, device: Device | None) -> tuple[AjaxNightModeArmSwitch, MagicMock]:
        coordinator = MagicMock()
        coordinator.rooms = {}
        coordinator.devices = {"30EA219B": device} if device else {}
        sw = AjaxNightModeArmSwitch(
            coordinator=coordinator,
            device_id="30EA219B",
            hub_id="hub-1",
            device_type="door_protect",
        )
        return sw, coordinator

    def test_disabled_by_default_config_entity(self) -> None:
        from homeassistant.helpers.entity import EntityCategory

        sw, _ = self._switch(_device())
        assert sw.unique_id == "aegis_ajax_30EA219B_night_mode_arm"
        assert sw.entity_registry_enabled_default is False
        assert sw.entity_category == EntityCategory.CONFIG

    def test_state_follows_the_stream_status(self) -> None:
        sw, coordinator = self._switch(_device(statuses={"armed_in_night_mode": True}))
        assert sw.is_on is True
        coordinator.devices["30EA219B"] = _device()
        assert sw.is_on is False

    def test_unavailable_when_offline_or_missing(self) -> None:
        sw, _ = self._switch(_device(state=DeviceState.OFFLINE))
        assert sw.available is False
        sw, _ = self._switch(None)
        assert sw.available is False
        assert sw.is_on is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "enable"), [("async_turn_on", True), ("async_turn_off", False)]
    )
    async def test_toggle_sends_the_command(self, method: str, enable: bool) -> None:
        sw, coordinator = self._switch(_device())
        with patch(
            "custom_components.aegis_ajax.switch.async_send_device_command", AsyncMock()
        ) as send:
            await getattr(sw, method)()
        cmd = send.await_args.args[1]
        assert cmd.action == "night_mode_arm"
        assert cmd.night_mode_arm is enable


class TestSetup:
    @pytest.mark.asyncio
    async def test_created_for_detectors_only(self) -> None:
        coordinator = MagicMock()
        coordinator.rooms = {}
        coordinator.spaces = {}
        door = _device("door_protect")
        siren = _device("home_siren")
        coordinator.devices = {door.id: door, "SIREN001": siren}
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {"bypass_switches": "never"}
        added: list = []
        with (
            patch("custom_components.aegis_ajax.switch.er.async_get", return_value=MagicMock()),
            patch(
                "custom_components.aegis_ajax.switch.er.async_entries_for_config_entry",
                return_value=[],
            ),
        ):
            await async_setup_entry(MagicMock(), entry, lambda e, *a, **k: added.extend(e))
        ids = {e.unique_id for e in added if isinstance(e, AjaxNightModeArmSwitch)}
        assert ids == {"aegis_ajax_30EA219B_night_mode_arm"}


class TestArmedRefusal:
    """An armed detector's refusal reads as a plain user error, not a traceback (#579)."""

    def _switch(self, error: Exception) -> AjaxNightModeArmSwitch:
        coordinator = MagicMock()
        coordinator.rooms = {}
        coordinator.devices = {}
        coordinator.devices_api.send_command = AsyncMock(side_effect=error)
        coordinator.async_request_refresh = AsyncMock()
        return AjaxNightModeArmSwitch(
            coordinator=coordinator, device_id="X", hub_id="h", device_type="door_protect"
        )

    @pytest.mark.asyncio
    async def test_wrong_state_says_disarm_first(self) -> None:
        from homeassistant.exceptions import ServiceValidationError

        sw = self._switch(
            DeviceCommandError("night_mode_arm: hub_wrong_state", reason="hub_wrong_state")
        )
        with pytest.raises(ServiceValidationError) as err:
            await sw.async_turn_off()
        assert err.value.translation_key == "night_mode_arm_while_armed"

    @pytest.mark.asyncio
    async def test_other_refusals_keep_their_message(self) -> None:
        from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

        sw = self._switch(
            DeviceCommandError("night_mode_arm: permission_denied", reason="permission_denied")
        )
        with pytest.raises(HomeAssistantError) as err:
            await sw.async_turn_on()
        assert not isinstance(err.value, ServiceValidationError)
        assert err.value.translation_key == "command_permission_denied"
