"""Tests for the per-detector "Always active" switch (#689)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax.api import _proto_path as _proto_path  # noqa: F401
from custom_components.aegis_ajax.api.devices import DeviceCommandError
from custom_components.aegis_ajax.switch import (
    AjaxAlwaysActiveSwitch,
    AjaxNightModeArmSwitch,
    async_setup_entry,
)
from tests.unit.test_night_mode_arm import _device


def _switch(device: object | None = None) -> tuple[AjaxAlwaysActiveSwitch, MagicMock]:
    coordinator = MagicMock()
    coordinator.rooms = {}
    coordinator.devices = {"30EA219B": device} if device else {}
    sw = AjaxAlwaysActiveSwitch(
        coordinator=coordinator, device_id="30EA219B", hub_id="hub-1", device_type="door_protect"
    )
    return sw, coordinator


@pytest.mark.parametrize(("method", "enable"), [("async_turn_on", True), ("async_turn_off", False)])
async def test_a_multitransmitter_input_is_written_over_the_hub_connection(
    method: str, enable: bool
) -> None:
    sw, coordinator = _switch(_device())
    sw._device_type = "wire_input_mt"
    coordinator.async_set_wire_input_always_active = AsyncMock()

    with patch("custom_components.aegis_ajax.switch.async_send_device_command") as send:
        await getattr(sw, method)()

    coordinator.async_set_wire_input_always_active.assert_awaited_once_with(
        "30EA219B", enable=enable
    )
    send.assert_not_called()


def test_disabled_by_default_with_its_own_id() -> None:
    sw, _ = _switch(_device())
    assert sw.unique_id == "aegis_ajax_30EA219B_always_active"
    assert sw.translation_key == "always_active"
    assert sw.entity_registry_enabled_default is False


def test_state_follows_its_own_status_not_night_mode() -> None:
    sw, coordinator = _switch(_device(statuses={"always_active": True}))
    assert sw.is_on is True
    coordinator.devices["30EA219B"] = _device(statuses={"armed_in_night_mode": True})
    assert sw.is_on is False


@pytest.mark.parametrize(("method", "enable"), [("async_turn_on", True), ("async_turn_off", False)])
async def test_toggle_sends_the_always_active_command(method: str, enable: bool) -> None:
    sw, _ = _switch(_device())
    with patch(
        "custom_components.aegis_ajax.switch.async_send_device_command", AsyncMock()
    ) as send:
        await getattr(sw, method)()
    cmd = send.await_args.args[1]
    assert cmd.action == "always_active"
    assert cmd.always_active is enable
    assert cmd.night_mode_arm is None


async def test_wrong_state_keeps_the_hubs_own_message() -> None:
    """Unlike night mode, nothing is known about why the hub would refuse, so no rewording."""
    from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

    sw, coordinator = _switch()
    coordinator.devices_api.send_command = AsyncMock(
        side_effect=DeviceCommandError("always_active: hub_wrong_state", reason="hub_wrong_state")
    )
    coordinator.async_request_refresh = AsyncMock()
    with pytest.raises(HomeAssistantError) as err:
        await sw.async_turn_on()
    assert not isinstance(err.value, ServiceValidationError)
    assert err.value.translation_key == "command_hub_wrong_state"


async def test_created_next_to_the_night_mode_switch() -> None:
    coordinator = MagicMock()
    coordinator.rooms = {}
    coordinator.spaces = {}
    door = _device("door_protect")
    coordinator.devices = {door.id: door, "SIREN001": _device("home_siren")}
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
    assert {e.unique_id for e in added if isinstance(e, AjaxNightModeArmSwitch)} == {
        "aegis_ajax_30EA219B_night_mode_arm",
        "aegis_ajax_30EA219B_always_active",
    }
