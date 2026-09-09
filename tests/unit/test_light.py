"""Tests for light entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aegis_ajax.api.models import Device
from custom_components.aegis_ajax.const import DeviceState
from custom_components.aegis_ajax.light import AjaxLight, async_setup_entry


def _device(device_id: str, device_type: str) -> Device:
    return Device(
        id=device_id,
        hub_id="h1",
        name="Device",
        device_type=device_type,
        room_id=None,
        group_id=None,
        state=DeviceState.ONLINE,
        malfunctions=0,
        bypassed=False,
        statuses={},
        battery=None,
    )


class TestLightSetup:
    @pytest.mark.asyncio
    async def test_setup_adds_only_the_light_capability(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {
            "dimmer": _device("dimmer", "light_switch_dimmer"),
            "not-a-light": _device("not-a-light", "door_protect"),
        }
        coordinator.rooms = {}
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []

        await async_setup_entry(MagicMock(), entry, lambda new: added.extend(new))

        assert [entity._device_id for entity in added] == ["dimmer"]


class TestAjaxLight:
    def test_unique_id(self) -> None:
        coordinator = MagicMock()
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        assert light.unique_id == "aegis_ajax_d1_light_1"

    def test_has_brightness_support(self) -> None:
        from homeassistant.components.light import ColorMode  # type: ignore[attr-defined]

        coordinator = MagicMock()
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        assert ColorMode.BRIGHTNESS in light.supported_color_modes

    def test_name_is_none(self) -> None:
        """Light is the primary entity and adopts device name."""
        coordinator = MagicMock()
        mock_device = MagicMock()
        mock_device.name = "Living Room Dimmer"
        coordinator.devices = {"d1": mock_device}
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        assert light._attr_name is None

    def test_device_info_with_device(self) -> None:
        coordinator = MagicMock()
        mock_device = MagicMock()
        mock_device.id = "d1"
        mock_device.name = "Living Room Dimmer"
        mock_device.device_type = "light_switch_dimmer"
        mock_device.hub_id = "h1"
        coordinator.devices = {"d1": mock_device}
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        assert light._attr_device_info is not None
        assert ("aegis_ajax", "d1") in light._attr_device_info["identifiers"]

    def test_device_info_without_device(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        assert not hasattr(light, "_attr_device_info") or light._attr_device_info is None

    def test_available_when_device_online(self) -> None:
        coordinator = MagicMock()
        mock_device = MagicMock()
        mock_device.is_online = True
        coordinator.devices = {"d1": mock_device}
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        assert light.available is True

    def test_unavailable_when_device_missing(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        assert light.available is False

    def test_is_on_reads_switch_state(self) -> None:
        coordinator = MagicMock()
        mock_device = MagicMock()
        mock_device.statuses = {"switch_ch1": True, "brightness_ch1": 50}
        coordinator.devices = {"d1": mock_device}
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        assert light.is_on is True

    def test_is_off_when_switch_off_despite_brightness(self) -> None:
        """The #429 symptom: brightness holds a level while the load is off."""
        coordinator = MagicMock()
        mock_device = MagicMock()
        mock_device.statuses = {"switch_ch1": False, "brightness_ch1": 100}
        coordinator.devices = {"d1": mock_device}
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        assert light.is_on is False

    def test_is_off_when_switch_state_missing(self) -> None:
        coordinator = MagicMock()
        mock_device = MagicMock()
        mock_device.statuses = {"brightness_ch1": 100}
        coordinator.devices = {"d1": mock_device}
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        assert light.is_on is False

    def test_is_on_returns_none_when_no_device(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        assert light.is_on is None

    def test_brightness_value(self) -> None:
        coordinator = MagicMock()
        mock_device = MagicMock()
        mock_device.statuses = {"brightness_ch1": 100}
        coordinator.devices = {"d1": mock_device}
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        # 100% -> 255
        assert light.brightness == 255

    def test_brightness_returns_none_when_no_device(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        assert light.brightness is None

    def test_brightness_partial(self) -> None:
        coordinator = MagicMock()
        mock_device = MagicMock()
        mock_device.statuses = {"brightness_ch1": 50}
        coordinator.devices = {"d1": mock_device}
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        # 50% -> ~128
        assert light.brightness == round(50 * 255 / 100)

    @staticmethod
    def _command_coordinator(statuses: dict[str, object] | None) -> MagicMock:
        """Coordinator with a d1 device carrying `statuses` (None = no device)."""
        coordinator = MagicMock()
        coordinator.devices_api.send_command = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        if statuses is None:
            coordinator.devices = {}
        else:
            mock_device = MagicMock()
            mock_device.statuses = statuses
            coordinator.devices = {"d1": mock_device}
        return coordinator

    @pytest.mark.asyncio
    async def test_turn_on_sends_on_command(self) -> None:
        coordinator = self._command_coordinator({"switch_ch1": False, "brightness_ch1": 100})
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        await light.async_turn_on()
        coordinator.devices_api.send_command.assert_called_once()
        cmd = coordinator.devices_api.send_command.call_args[0][0]
        assert cmd.action == "on"
        assert cmd.channels == [1]

    @pytest.mark.asyncio
    async def test_turn_on_with_brightness_while_off_sends_brightness_then_on(self) -> None:
        """The #429 fix: a brightness write alone never powers the load."""
        coordinator = self._command_coordinator({"switch_ch1": False, "brightness_ch1": 1})
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        # 128/255 * 100 ≈ 50
        await light.async_turn_on(brightness=128)
        cmds = [c.args[0] for c in coordinator.devices_api.send_command.call_args_list]
        assert [c.action for c in cmds] == ["brightness", "on"]
        assert cmds[0].brightness == round(128 * 100 / 255)
        assert cmds[1].channels == [1]

    @pytest.mark.asyncio
    async def test_turn_on_with_brightness_while_on_sends_brightness_only(self) -> None:
        coordinator = self._command_coordinator({"switch_ch1": True, "brightness_ch1": 100})
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        await light.async_turn_on(brightness=128)
        coordinator.devices_api.send_command.assert_called_once()
        cmd = coordinator.devices_api.send_command.call_args[0][0]
        assert cmd.action == "brightness"
        assert cmd.brightness == round(128 * 100 / 255)

    @pytest.mark.asyncio
    async def test_turn_off_sends_off_command(self) -> None:
        coordinator = self._command_coordinator({"switch_ch1": True, "brightness_ch1": 100})
        light = AjaxLight(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_dimmer",
            channel=1,
        )
        await light.async_turn_off()
        coordinator.devices_api.send_command.assert_called_once()
        cmd = coordinator.devices_api.send_command.call_args[0][0]
        assert cmd.action == "off"
        assert cmd.channels == [1]
