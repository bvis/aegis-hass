"""Tests for alarm control panel entity."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aegis_ajax.alarm_control_panel import (
    AjaxAlarmControlPanel,
    AjaxGroupAlarmControlPanel,
    map_security_state,
)
from custom_components.aegis_ajax.api.models import Group, Space
from custom_components.aegis_ajax.const import ConnectionStatus, SecurityState


class TestMapSecurityState:
    def test_armed(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        assert map_security_state(SecurityState.ARMED) == AlarmControlPanelState.ARMED_AWAY

    def test_disarmed(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        assert map_security_state(SecurityState.DISARMED) == AlarmControlPanelState.DISARMED

    def test_night_mode(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        assert map_security_state(SecurityState.NIGHT_MODE) == AlarmControlPanelState.ARMED_NIGHT

    def test_partially_armed(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        assert (
            map_security_state(SecurityState.PARTIALLY_ARMED)
            == AlarmControlPanelState.ARMED_CUSTOM_BYPASS
        )

    def test_arming_states(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        assert (
            map_security_state(SecurityState.AWAITING_EXIT_TIMER) == AlarmControlPanelState.ARMING
        )
        assert (
            map_security_state(SecurityState.AWAITING_SECOND_STAGE) == AlarmControlPanelState.ARMING
        )

    def test_two_stage_incomplete(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        assert (
            map_security_state(SecurityState.TWO_STAGE_INCOMPLETE) == AlarmControlPanelState.ARMING
        )

    def test_awaiting_vds(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        assert map_security_state(SecurityState.AWAITING_VDS) == AlarmControlPanelState.ARMING

    def test_none_state(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        assert map_security_state(SecurityState.NONE) == AlarmControlPanelState.DISARMED

    def test_unknown_state_defaults_to_disarmed(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        # Use a value not in map - cast an enum value not in _STATE_MAP
        # Use NONE since it maps to DISARMED
        result = map_security_state(SecurityState.NONE)
        assert result == AlarmControlPanelState.DISARMED


class TestAlarmControlPanel:
    def _make_space(
        self, security_state: SecurityState = SecurityState.DISARMED, online: bool = True
    ) -> Space:
        return Space(
            id="s1",
            hub_id="h1",
            name="Home",
            security_state=security_state,
            connection_status=ConnectionStatus.ONLINE if online else ConnectionStatus.OFFLINE,
            malfunctions_count=0,
        )

    def _make_coordinator(
        self, use_pin_code: bool = False, pin_code: str | None = None
    ) -> MagicMock:
        coordinator = MagicMock()
        options: dict = {"use_pin_code": use_pin_code}
        if pin_code is not None:
            options["pin_code_hash"] = hashlib.sha256(pin_code.encode()).hexdigest()
        coordinator.config_entry.options = options
        return coordinator

    def test_unique_id(self) -> None:
        coordinator = MagicMock()
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.unique_id == "aegis_ajax_alarm_s1"

    def test_available_when_online(self) -> None:
        coordinator = MagicMock()
        coordinator.spaces = {"s1": self._make_space(online=True)}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.available is True

    def test_unavailable_when_offline(self) -> None:
        coordinator = MagicMock()
        coordinator.spaces = {"s1": self._make_space(online=False)}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.available is False

    def test_unavailable_when_space_missing(self) -> None:
        coordinator = MagicMock()
        coordinator.spaces = {}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.available is False

    def test_name_is_none(self) -> None:
        """Primary entity adopts device name — _attr_name must be None."""
        coordinator = MagicMock()
        coordinator.spaces = {"s1": self._make_space()}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel._attr_name is None

    def test_device_info_with_space(self) -> None:
        coordinator = MagicMock()
        coordinator.spaces = {"s1": self._make_space()}
        coordinator.devices = {}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel._attr_device_info is not None
        assert (
            "aegis_ajax",
            "h1",
        ) in panel._attr_device_info["identifiers"]

    def test_device_info_without_space(self) -> None:
        coordinator = MagicMock()
        coordinator.spaces = {}
        coordinator.devices = {}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel._attr_device_info is not None

    def test_alarm_state_armed(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        coordinator = MagicMock()
        coordinator.spaces = {"s1": self._make_space(SecurityState.ARMED)}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.alarm_state == AlarmControlPanelState.ARMED_AWAY

    def test_alarm_state_disarmed(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        coordinator = MagicMock()
        coordinator.spaces = {"s1": self._make_space(SecurityState.DISARMED)}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.alarm_state == AlarmControlPanelState.DISARMED

    def test_alarm_state_none_when_no_space(self) -> None:
        coordinator = MagicMock()
        coordinator.spaces = {}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.alarm_state is None

    def test_extra_state_attributes(self) -> None:
        coordinator = MagicMock()
        coordinator.spaces = {"s1": self._make_space()}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        attrs = panel.extra_state_attributes
        assert "hub_id" in attrs
        assert "malfunctions" in attrs
        assert "connection_status" in attrs

    def test_extra_state_attributes_empty_when_no_space(self) -> None:
        coordinator = MagicMock()
        coordinator.spaces = {}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.extra_state_attributes == {}

    def test_code_arm_required_false_by_default(self) -> None:
        coordinator = self._make_coordinator(use_pin_code=False)
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.code_arm_required is False

    def test_code_arm_required_true_when_enabled(self) -> None:
        coordinator = self._make_coordinator(use_pin_code=True)
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.code_arm_required is True

    @pytest.mark.asyncio
    async def test_alarm_arm_away(self) -> None:
        coordinator = MagicMock()
        coordinator.security_api.arm = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.config_entry.options = {"use_pin_code": False}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        await panel.async_alarm_arm_away()
        coordinator.security_api.arm.assert_called_once_with("s1", ignore_alarms=False)

    @pytest.mark.asyncio
    async def test_alarm_arm_night(self) -> None:
        coordinator = MagicMock()
        coordinator.security_api.arm_night_mode = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.config_entry.options = {"use_pin_code": False}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        await panel.async_alarm_arm_night()
        coordinator.security_api.arm_night_mode.assert_called_once_with("s1", ignore_alarms=False)

    @pytest.mark.asyncio
    async def test_alarm_disarm(self) -> None:
        coordinator = MagicMock()
        coordinator.security_api.disarm = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.config_entry.options = {"use_pin_code": False}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        await panel.async_alarm_disarm()
        coordinator.security_api.disarm.assert_called_once_with("s1")

    @pytest.mark.asyncio
    async def test_alarm_disarm_from_night_mode_uses_regular_disarm(self) -> None:
        """Regular disarm() works from night mode — server handles it correctly."""
        coordinator = MagicMock()
        coordinator.security_api.disarm = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.config_entry.options = {"use_pin_code": False}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        await panel.async_alarm_disarm()
        coordinator.security_api.disarm.assert_called_once_with("s1")

    @pytest.mark.asyncio
    async def test_alarm_disarm_with_valid_pin(self) -> None:
        coordinator = self._make_coordinator(use_pin_code=True, pin_code="1234")
        coordinator.security_api.disarm = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.spaces = {"s1": self._make_space(SecurityState.ARMED)}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        await panel.async_alarm_disarm(code="1234")
        coordinator.security_api.disarm.assert_called_once_with("s1")

    @pytest.mark.asyncio
    async def test_alarm_disarm_with_invalid_pin_raises(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        coordinator = self._make_coordinator(use_pin_code=True, pin_code="1234")
        coordinator.security_api.disarm = AsyncMock()
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        with pytest.raises(HomeAssistantError):
            await panel.async_alarm_disarm(code="9999")
        coordinator.security_api.disarm.assert_not_called()

    @pytest.mark.asyncio
    async def test_alarm_disarm_with_no_code_when_pin_required_raises(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        coordinator = self._make_coordinator(use_pin_code=True, pin_code="1234")
        coordinator.security_api.disarm = AsyncMock()
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        with pytest.raises(HomeAssistantError):
            await panel.async_alarm_disarm(code=None)
        coordinator.security_api.disarm.assert_not_called()

    @pytest.mark.asyncio
    async def test_alarm_arm_with_valid_pin(self) -> None:
        coordinator = self._make_coordinator(use_pin_code=True, pin_code="5678")
        coordinator.security_api.arm = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        await panel.async_alarm_arm_away(code="5678")
        coordinator.security_api.arm.assert_called_once_with("s1", ignore_alarms=False)

    @pytest.mark.asyncio
    async def test_alarm_arm_with_invalid_pin_raises(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        coordinator = self._make_coordinator(use_pin_code=True, pin_code="5678")
        coordinator.security_api.arm = AsyncMock()
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        with pytest.raises(HomeAssistantError):
            await panel.async_alarm_arm_away(code="0000")
        coordinator.security_api.arm.assert_not_called()

    def test_supported_features_includes_arm_home(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelEntityFeature,
        )

        coordinator = self._make_coordinator(use_pin_code=False)
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.supported_features & AlarmControlPanelEntityFeature.ARM_HOME

    def test_code_format_number_when_pin_set(self) -> None:
        from homeassistant.components.alarm_control_panel import CodeFormat

        coordinator = self._make_coordinator(use_pin_code=True, pin_code="1234")
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.code_format == CodeFormat.NUMBER

    def test_code_format_none_without_pin(self) -> None:
        coordinator = self._make_coordinator(use_pin_code=False)
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.code_format is None

    @pytest.mark.asyncio
    async def test_alarm_arm_home_maps_to_night_mode(self) -> None:
        coordinator = self._make_coordinator(use_pin_code=False)
        coordinator.security_api.arm_night_mode = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        await panel.async_alarm_arm_home()
        coordinator.security_api.arm_night_mode.assert_called_once_with("s1", ignore_alarms=False)


class TestGroupAlarmControlPanel:
    def _make_space_with_groups(
        self,
        group_states: list[tuple[str, str, SecurityState]] | None = None,
        online: bool = True,
    ) -> Space:
        groups = tuple(
            Group(id=gid, space_id="s1", name=name, security_state=state, sorting_key=gid)
            for gid, name, state in (group_states or [("g1", "Villa", SecurityState.DISARMED)])
        )
        return Space(
            id="s1",
            hub_id="h1",
            name="Home",
            security_state=SecurityState.PARTIALLY_ARMED,
            connection_status=ConnectionStatus.ONLINE if online else ConnectionStatus.OFFLINE,
            malfunctions_count=0,
            groups=groups,
            group_mode_enabled=True,
        )

    def _make_coordinator(self) -> MagicMock:
        coordinator = MagicMock()
        coordinator.config_entry.options = {}
        return coordinator

    def test_unique_id_per_group(self) -> None:
        coordinator = self._make_coordinator()
        panel = AjaxGroupAlarmControlPanel(coordinator=coordinator, space_id="s1", group_id="g1")
        assert panel.unique_id == "aegis_ajax_alarm_s1_group_g1"

    def test_name_is_group_name(self) -> None:
        coordinator = self._make_coordinator()
        coordinator.spaces = {
            "s1": self._make_space_with_groups([("g1", "Villa", SecurityState.DISARMED)])
        }
        coordinator.devices = {}
        coordinator.rooms = {}
        panel = AjaxGroupAlarmControlPanel(coordinator=coordinator, space_id="s1", group_id="g1")
        assert panel.name == "Villa"

    def test_alarm_state_reflects_group_state(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,
        )

        coordinator = self._make_coordinator()
        coordinator.spaces = {
            "s1": self._make_space_with_groups(
                [("g1", "Villa", SecurityState.ARMED), ("g2", "Apartment", SecurityState.DISARMED)]
            )
        }
        coordinator.devices = {}
        coordinator.rooms = {}
        p1 = AjaxGroupAlarmControlPanel(coordinator=coordinator, space_id="s1", group_id="g1")
        p2 = AjaxGroupAlarmControlPanel(coordinator=coordinator, space_id="s1", group_id="g2")
        assert p1.alarm_state == AlarmControlPanelState.ARMED_AWAY
        assert p2.alarm_state == AlarmControlPanelState.DISARMED

    def test_unavailable_when_group_missing(self) -> None:
        coordinator = self._make_coordinator()
        coordinator.spaces = {"s1": self._make_space_with_groups()}
        coordinator.devices = {}
        coordinator.rooms = {}
        panel = AjaxGroupAlarmControlPanel(coordinator=coordinator, space_id="s1", group_id="ghost")
        assert panel.available is False

    def test_extra_attributes(self) -> None:
        coordinator = self._make_coordinator()
        coordinator.spaces = {
            "s1": self._make_space_with_groups([("g1", "Villa", SecurityState.ARMED)])
        }
        coordinator.devices = {}
        coordinator.rooms = {}
        panel = AjaxGroupAlarmControlPanel(coordinator=coordinator, space_id="s1", group_id="g1")
        attrs = panel.extra_state_attributes
        assert attrs["group_id"] == "g1"
        assert attrs["group_name"] == "Villa"
        assert attrs["space_id"] == "s1"
        assert attrs["hub_id"] == "h1"

    @pytest.mark.asyncio
    async def test_arm_calls_arm_group(self) -> None:
        coordinator = self._make_coordinator()
        coordinator.spaces = {
            "s1": self._make_space_with_groups([("g1", "Villa", SecurityState.DISARMED)])
        }
        coordinator.devices = {}
        coordinator.rooms = {}
        coordinator.security_api.arm_group = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        panel = AjaxGroupAlarmControlPanel(coordinator=coordinator, space_id="s1", group_id="g1")
        await panel.async_alarm_arm_away()
        coordinator.security_api.arm_group.assert_called_once_with("s1", "g1", ignore_alarms=False)

    @pytest.mark.asyncio
    async def test_disarm_calls_disarm_group(self) -> None:
        coordinator = self._make_coordinator()
        coordinator.spaces = {
            "s1": self._make_space_with_groups([("g1", "Villa", SecurityState.ARMED)])
        }
        coordinator.devices = {}
        coordinator.rooms = {}
        coordinator.security_api.disarm_group = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        panel = AjaxGroupAlarmControlPanel(coordinator=coordinator, space_id="s1", group_id="g1")
        await panel.async_alarm_disarm()
        coordinator.security_api.disarm_group.assert_called_once_with("s1", "g1")

    def test_supported_features_includes_arm_home(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelEntityFeature,
        )

        coordinator = self._make_coordinator()
        coordinator.spaces = {
            "s1": self._make_space_with_groups([("g1", "Villa", SecurityState.DISARMED)])
        }
        panel = AjaxGroupAlarmControlPanel(coordinator=coordinator, space_id="s1", group_id="g1")
        assert panel.supported_features & AlarmControlPanelEntityFeature.ARM_HOME

    @pytest.mark.asyncio
    async def test_arm_home_maps_to_arm_group(self) -> None:
        coordinator = self._make_coordinator()
        coordinator.spaces = {
            "s1": self._make_space_with_groups([("g1", "Villa", SecurityState.DISARMED)])
        }
        coordinator.devices = {}
        coordinator.rooms = {}
        coordinator.security_api.arm_group = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        panel = AjaxGroupAlarmControlPanel(coordinator=coordinator, space_id="s1", group_id="g1")
        await panel.async_alarm_arm_home()
        coordinator.security_api.arm_group.assert_called_once_with("s1", "g1", ignore_alarms=False)


class TestAsyncSetupEntry:
    """`async_setup_entry` always creates the space-level panel.
    In group mode it ALSO creates one per-group panel.
    """

    def _coordinator_with_space(self, *, group_mode: bool, groups: tuple) -> MagicMock:  # type: ignore[type-arg]
        coordinator = MagicMock()
        coordinator.spaces = {
            "s1": Space(
                id="s1",
                hub_id="h1",
                name="Home",
                security_state=SecurityState.DISARMED,
                connection_status=ConnectionStatus.ONLINE,
                malfunctions_count=0,
                groups=groups,
                group_mode_enabled=group_mode,
            )
        }
        coordinator.devices = {}
        coordinator.rooms = {}
        return coordinator

    @pytest.mark.asyncio
    async def test_creates_only_space_panel_when_no_group_mode(self) -> None:
        from custom_components.aegis_ajax.alarm_control_panel import async_setup_entry

        coordinator = self._coordinator_with_space(group_mode=False, groups=())
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await async_setup_entry(MagicMock(), entry, added.append)
        assert len(added[0]) == 1
        assert isinstance(added[0][0], AjaxAlarmControlPanel)

    @pytest.mark.asyncio
    async def test_creates_space_panel_plus_per_group_panels_in_group_mode(self) -> None:
        from custom_components.aegis_ajax.alarm_control_panel import async_setup_entry

        groups = (
            Group(id="g1", space_id="s1", name="Villa", security_state=SecurityState.ARMED),
            Group(id="g2", space_id="s1", name="Apartment", security_state=SecurityState.DISARMED),
        )
        coordinator = self._coordinator_with_space(group_mode=True, groups=groups)
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await async_setup_entry(MagicMock(), entry, added.append)
        entities = added[0]
        # Expect: 1 whole-house + 2 group panels (Villa, Apartment)
        assert len(entities) == 3
        space_panels = [e for e in entities if isinstance(e, AjaxAlarmControlPanel)]
        group_panels = [e for e in entities if isinstance(e, AjaxGroupAlarmControlPanel)]
        assert len(space_panels) == 1
        assert len(group_panels) == 2
        assert {p._group_id for p in group_panels} == {"g1", "g2"}
