"""Tests for switch entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aegis_ajax.switch import SWITCH_DEVICE_TYPES, AjaxSwitch


class TestSwitchDeviceTypes:
    def test_relay_is_switch(self) -> None:
        assert "relay" in SWITCH_DEVICE_TYPES

    def test_wall_switch_is_switch(self) -> None:
        assert "wall_switch" in SWITCH_DEVICE_TYPES

    def test_socket_is_switch(self) -> None:
        assert "socket" in SWITCH_DEVICE_TYPES

    def test_light_switch_two_gang_has_two_channels(self) -> None:
        assert SWITCH_DEVICE_TYPES["light_switch_two_gang"] == 2

    @pytest.mark.parametrize(
        "device_type",
        [
            "socket_b",
            "socket_g",
            "socket_outlet_type_e",
            "socket_outlet_type_f",
            "socket_type_g_plus",
            "relay_fibra_base",
            "light_switch_one_gang",
            "light_switch_one_gang_na",
            "light_switch_2_way",
            "light_switch_crossover",
            "light_switch_three_way_na",
            "light_switch_two_channel_two_way",
            "light_switch_four_way_na",
        ],
    )
    def test_extra_switch_variants_known(self, device_type: str) -> None:
        assert device_type in SWITCH_DEVICE_TYPES


class TestAjaxSwitch:
    def test_unique_id(self) -> None:
        coordinator = MagicMock()
        sw = AjaxSwitch(
            coordinator=coordinator, device_id="d1", hub_id="h1", device_type="relay", channel=1
        )
        assert sw.unique_id == "aegis_ajax_d1_switch_1"

    def test_turn_on_callable(self) -> None:
        coordinator = MagicMock()
        coordinator.devices_api.send_command = AsyncMock()
        sw = AjaxSwitch(
            coordinator=coordinator, device_id="d1", hub_id="h1", device_type="relay", channel=1
        )
        assert hasattr(sw, "async_turn_on")

    def test_turn_off_callable(self) -> None:
        coordinator = MagicMock()
        coordinator.devices_api.send_command = AsyncMock()
        sw = AjaxSwitch(
            coordinator=coordinator, device_id="d1", hub_id="h1", device_type="relay", channel=1
        )
        assert hasattr(sw, "async_turn_off")

    def test_single_channel_name_is_none(self) -> None:
        """Single-channel switch is the primary entity and adopts device name."""
        coordinator = MagicMock()
        mock_device = MagicMock()
        mock_device.name = "Garage Relay"
        coordinator.devices = {"d1": mock_device}
        sw = AjaxSwitch(
            coordinator=coordinator, device_id="d1", hub_id="h1", device_type="relay", channel=1
        )
        assert sw._attr_name is None

    def test_multi_channel_uses_translation_key(self) -> None:
        coordinator = MagicMock()
        mock_device = MagicMock()
        mock_device.name = "Wall Switch"
        coordinator.devices = {"d1": mock_device}
        sw = AjaxSwitch(
            coordinator=coordinator,
            device_id="d1",
            hub_id="h1",
            device_type="light_switch_two_gang",
            channel=2,
        )
        assert sw._attr_translation_key == "channel_2"

    def test_device_info_with_device(self) -> None:
        coordinator = MagicMock()
        mock_device = MagicMock()
        mock_device.id = "d1"
        mock_device.name = "Relay"
        mock_device.device_type = "relay"
        mock_device.hub_id = "h1"
        coordinator.devices = {"d1": mock_device}
        sw = AjaxSwitch(
            coordinator=coordinator, device_id="d1", hub_id="h1", device_type="relay", channel=1
        )
        assert sw._attr_device_info is not None
        assert ("aegis_ajax", "d1") in sw._attr_device_info["identifiers"]

    def test_device_info_without_device(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sw = AjaxSwitch(
            coordinator=coordinator, device_id="d1", hub_id="h1", device_type="relay", channel=1
        )
        assert not hasattr(sw, "_attr_device_info") or sw._attr_device_info is None

    def test_available_when_online(self) -> None:
        coordinator = MagicMock()
        mock_device = MagicMock()
        mock_device.is_online = True
        coordinator.devices = {"d1": mock_device}
        sw = AjaxSwitch(
            coordinator=coordinator, device_id="d1", hub_id="h1", device_type="relay", channel=1
        )
        assert sw.available is True

    def test_unavailable_when_device_missing(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sw = AjaxSwitch(
            coordinator=coordinator, device_id="d1", hub_id="h1", device_type="relay", channel=1
        )
        assert sw.available is False

    def test_is_on_true(self) -> None:
        coordinator = MagicMock()
        mock_device = MagicMock()
        mock_device.statuses = {"switch_ch1": True}
        coordinator.devices = {"d1": mock_device}
        sw = AjaxSwitch(
            coordinator=coordinator, device_id="d1", hub_id="h1", device_type="relay", channel=1
        )
        assert sw.is_on is True

    def test_is_on_false(self) -> None:
        coordinator = MagicMock()
        mock_device = MagicMock()
        mock_device.statuses = {"switch_ch1": False}
        coordinator.devices = {"d1": mock_device}
        sw = AjaxSwitch(
            coordinator=coordinator, device_id="d1", hub_id="h1", device_type="relay", channel=1
        )
        assert sw.is_on is False

    def test_is_on_returns_none_when_no_device(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sw = AjaxSwitch(
            coordinator=coordinator, device_id="d1", hub_id="h1", device_type="relay", channel=1
        )
        assert sw.is_on is None

    @pytest.mark.asyncio
    async def test_turn_on_sends_command(self) -> None:
        coordinator = MagicMock()
        coordinator.devices_api.send_command = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        sw = AjaxSwitch(
            coordinator=coordinator, device_id="d1", hub_id="h1", device_type="relay", channel=1
        )
        await sw.async_turn_on()
        coordinator.devices_api.send_command.assert_called_once()
        cmd = coordinator.devices_api.send_command.call_args[0][0]
        assert cmd.action == "on"

    @pytest.mark.asyncio
    async def test_turn_off_sends_command(self) -> None:
        coordinator = MagicMock()
        coordinator.devices_api.send_command = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        sw = AjaxSwitch(
            coordinator=coordinator, device_id="d1", hub_id="h1", device_type="relay", channel=1
        )
        await sw.async_turn_off()
        coordinator.devices_api.send_command.assert_called_once()
        cmd = coordinator.devices_api.send_command.call_args[0][0]
        assert cmd.action == "off"


class TestAjaxBypassSwitch:
    """Per-device bypass (deactivation) switch (#bypass)."""

    def _make(
        self, device_type: str = "door_protect", bypassed: bool = False
    ) -> tuple[object, MagicMock]:
        from custom_components.aegis_ajax.switch import AjaxBypassSwitch

        coordinator = MagicMock()
        coordinator.rooms = {}
        device = MagicMock()
        device.device_type = device_type
        device.bypassed = bypassed
        device.is_online = True
        coordinator.devices = {"d1": device}
        sw = AjaxBypassSwitch(
            coordinator=coordinator, device_id="d1", hub_id="h1", device_type=device_type
        )
        return sw, coordinator

    def test_unique_id(self) -> None:
        sw, _ = self._make()
        assert sw.unique_id == "aegis_ajax_d1_bypass"

    def test_translation_key(self) -> None:
        sw, _ = self._make()
        assert sw._attr_translation_key == "bypass"

    def test_is_config_entity(self) -> None:
        from homeassistant.helpers.entity import EntityCategory

        sw, _ = self._make()
        assert sw._attr_entity_category == EntityCategory.CONFIG

    def test_is_on_reflects_bypassed_true(self) -> None:
        sw, _ = self._make(bypassed=True)
        assert sw.is_on is True

    def test_is_on_reflects_bypassed_false(self) -> None:
        sw, _ = self._make(bypassed=False)
        assert sw.is_on is False

    @pytest.mark.asyncio
    async def test_turn_on_sends_bypass_enable(self) -> None:
        sw, coordinator = self._make()
        coordinator.devices_api.send_command = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()

        await sw.async_turn_on()

        cmd = coordinator.devices_api.send_command.call_args[0][0]
        assert cmd.action == "bypass"
        assert cmd.bypass_enable is True
        coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_permission_denied_surfaces_homeassistant_error(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        from custom_components.aegis_ajax.api.devices import DeviceCommandError

        sw, coordinator = self._make()
        coordinator.devices_api.send_command = AsyncMock(
            side_effect=DeviceCommandError("bypass: permission_denied", reason="permission_denied")
        )
        coordinator.async_request_refresh = AsyncMock()

        with pytest.raises(HomeAssistantError) as exc:
            await sw.async_turn_on()

        assert exc.value.translation_key == "command_permission_denied"

    @pytest.mark.asyncio
    async def test_turn_off_sends_bypass_disable(self) -> None:
        sw, coordinator = self._make(bypassed=True)
        coordinator.devices_api.send_command = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()

        await sw.async_turn_off()

        cmd = coordinator.devices_api.send_command.call_args[0][0]
        assert cmd.action == "bypass"
        assert cmd.bypass_enable is False


class TestBypassSwitchSetup:
    """`async_setup_entry` gates bypass switches by the `bypass_switches` option."""

    async def _run(self, *, mode: str | None, perms: set | None) -> set[str]:
        from custom_components.aegis_ajax.switch import AjaxBypassSwitch, async_setup_entry

        coordinator = MagicMock()
        coordinator.rooms = {}
        hub = MagicMock(device_type="hub_two_plus", hub_id="hub-1")
        sensor = MagicMock(device_type="door_protect", hub_id="hub-1")
        coordinator.devices = {"hub-1": hub, "d1": sensor}
        space = MagicMock(id="s1", hub_id="hub-1")
        coordinator.spaces = {"s1": space}
        coordinator.spaces_api.get_member_space_permissions = AsyncMock(return_value=perms)

        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.data = {"user_hex_id": "ABCD1234"}
        entry.options = {} if mode is None else {"bypass_switches": mode}
        added: list = []

        def _add(entities: list, *a: object, **k: object) -> None:
            added.extend(entities)

        await async_setup_entry(MagicMock(), entry, _add)
        return {e._device_id for e in added if isinstance(e, AjaxBypassSwitch)}

    @pytest.mark.asyncio
    async def test_always_creates_for_non_hub(self) -> None:
        ids = await self._run(mode="always", perms=None)
        assert ids == {"d1"}

    @pytest.mark.asyncio
    async def test_never_creates_none(self) -> None:
        ids = await self._run(mode="never", perms={"DEVICE_EDIT"})
        assert ids == set()

    @pytest.mark.asyncio
    async def test_auto_creates_when_user_has_device_edit(self) -> None:
        ids = await self._run(mode="auto", perms={"ARM", "DEVICE_EDIT"})
        assert ids == {"d1"}

    @pytest.mark.asyncio
    async def test_auto_skips_when_user_lacks_device_edit(self) -> None:
        ids = await self._run(mode="auto", perms={"ARM", "DISARM"})
        assert ids == set()

    @pytest.mark.asyncio
    async def test_auto_fail_open_when_permissions_unknown(self) -> None:
        ids = await self._run(mode="auto", perms=None)
        assert ids == {"d1"}

    @pytest.mark.asyncio
    async def test_default_is_auto(self) -> None:
        # No option set → default (auto); user lacks DEVICE_EDIT → no switches.
        ids = await self._run(mode=None, perms={"ARM"})
        assert ids == set()
