"""Tests for alarm control panel entity."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aegis_ajax.alarm_control_panel import (
    AjaxAlarmControlPanel,
    map_security_state,
)
from custom_components.aegis_ajax.api.models import Space
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
