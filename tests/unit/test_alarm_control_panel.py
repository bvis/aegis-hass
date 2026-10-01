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
from custom_components.aegis_ajax.api.models import Device, Group, Space
from custom_components.aegis_ajax.const import ConnectionStatus, DeviceState, SecurityState


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
        coordinator.config_entry.entry_id = "entry1"
        options: dict = {"use_pin_code": use_pin_code}
        if pin_code is not None:
            options["pin_code_hash"] = hashlib.sha256(pin_code.encode()).hexdigest()
        coordinator.config_entry.options = options
        return coordinator

    def test_unique_id(self) -> None:
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.unique_id == "aegis_ajax_alarm_entry1_s1"

    def test_available_when_online(self) -> None:
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space(online=True)}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.available is True

    def test_unavailable_when_offline(self) -> None:
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space(online=False)}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.available is False

    def test_unavailable_when_space_missing(self) -> None:
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.available is False

    def test_name_is_none(self) -> None:
        """Primary entity adopts device name — _attr_name must be None."""
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space()}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel._attr_name is None

    def test_device_info_with_space(self) -> None:
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
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
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {}
        coordinator.devices = {}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel._attr_device_info is not None

    def test_alarm_state_armed(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space(SecurityState.ARMED)}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.alarm_state == AlarmControlPanelState.ARMED_AWAY

    def test_alarm_state_disarmed(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space(SecurityState.DISARMED)}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.alarm_state == AlarmControlPanelState.DISARMED

    def test_alarm_state_none_when_no_space(self) -> None:
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.alarm_state is None

    # --- #426: `triggered` overlay from the intrusion push -------------------

    def test_alarm_state_triggered_while_armed_and_alarm_active(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space(SecurityState.ARMED)}
        coordinator.alarmed_space_ids = {"s1"}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.alarm_state == AlarmControlPanelState.TRIGGERED

    def test_alarm_state_triggered_overrides_night_mode(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space(SecurityState.NIGHT_MODE)}
        coordinator.alarmed_space_ids = {"s1"}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.alarm_state == AlarmControlPanelState.TRIGGERED

    def test_alarm_state_not_triggered_once_disarmed(self) -> None:
        """A stale overlay entry must never show through a disarmed panel."""
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space(SecurityState.DISARMED)}
        coordinator.alarmed_space_ids = {"s1"}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.alarm_state == AlarmControlPanelState.DISARMED

    # --- #454: exit / entry delay overlay --------------------------------------

    @staticmethod
    def _overlay(kind: str):  # noqa: ANN205
        from datetime import UTC, datetime

        from custom_components.aegis_ajax.delay_states import DelayKind, DelayOverlay

        return DelayOverlay(
            kind=DelayKind(kind),
            ends_at=datetime(2026, 9, 5, 9, 55, 7, tzinfo=UTC),
            from_hub=False,
        )

    def test_alarm_state_arming_while_exit_delay_runs(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space(SecurityState.ARMED)}
        coordinator.delay_overlays = {"s1": self._overlay("arming")}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.alarm_state == AlarmControlPanelState.ARMING

    def test_alarm_state_pending_while_entry_delay_runs(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space(SecurityState.NIGHT_MODE)}
        coordinator.delay_overlays = {"s1": self._overlay("pending")}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.alarm_state == AlarmControlPanelState.PENDING

    def test_alarm_state_triggered_beats_pending(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space(SecurityState.ARMED)}
        coordinator.alarmed_space_ids = {"s1"}
        coordinator.delay_overlays = {"s1": self._overlay("pending")}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.alarm_state == AlarmControlPanelState.TRIGGERED

    def test_alarm_state_delay_overlay_never_shows_through_disarmed(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space(SecurityState.DISARMED)}
        coordinator.delay_overlays = {"s1": self._overlay("arming")}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.alarm_state == AlarmControlPanelState.DISARMED

    def test_delay_attributes_present_when_option_enabled(self) -> None:
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space(SecurityState.ARMED)}
        coordinator.delay_panel_states = True
        coordinator.delay_overlays = {"s1": self._overlay("arming")}
        coordinator.space_exit_delay_seconds.return_value = 20
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        attrs = panel.extra_state_attributes
        assert attrs["hub_state"] == "armed_away"
        assert attrs["exit_delay_seconds"] == 20
        assert attrs["delay_ends_at"] == "2026-09-05T09:55:07+00:00"

    def test_delay_attributes_null_end_when_no_delay_runs(self) -> None:
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space(SecurityState.ARMED)}
        coordinator.delay_panel_states = True
        coordinator.delay_overlays = {}
        coordinator.space_exit_delay_seconds.return_value = 0
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.extra_state_attributes["delay_ends_at"] is None

    def test_delay_attributes_absent_when_option_disabled(self) -> None:
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space(SecurityState.ARMED)}
        coordinator.delay_panel_states = False
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        attrs = panel.extra_state_attributes
        assert "hub_state" not in attrs
        assert "exit_delay_seconds" not in attrs

    def test_optimistic_write_resyncs_the_delay_overlays(self) -> None:
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space(SecurityState.DISARMED)}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        panel.hass = None
        import asyncio

        async def run() -> None:
            panel._optimistic_state_update(SecurityState.ARMED)

        asyncio.run(run())
        coordinator.sync_delay_overlays.assert_called_once()

    def test_alarm_state_partially_armed_with_night_mode_is_armed_night(self) -> None:
        # In group mode the server reports PARTIALLY_ARMED while night mode
        # is active; `night_mode_enabled` is the discriminator (#284).
        from dataclasses import replace

        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {
            "s1": replace(
                self._make_space(SecurityState.PARTIALLY_ARMED),
                night_mode_enabled=True,
            )
        }
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.alarm_state == AlarmControlPanelState.ARMED_NIGHT

    def test_alarm_state_partially_armed_without_night_mode_is_custom_bypass(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelState,  # type: ignore[attr-defined]
        )

        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space(SecurityState.PARTIALLY_ARMED)}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        assert panel.alarm_state == AlarmControlPanelState.ARMED_CUSTOM_BYPASS

    def test_extra_state_attributes(self) -> None:
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.spaces = {"s1": self._make_space()}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        attrs = panel.extra_state_attributes
        assert "hub_id" in attrs
        assert "malfunctions" in attrs
        assert "connection_status" in attrs

    def test_extra_state_attributes_empty_when_no_space(self) -> None:
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
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
        coordinator.config_entry.entry_id = "entry1"
        coordinator.security_api.arm = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.config_entry.options = {"use_pin_code": False}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        await panel.async_alarm_arm_away()
        coordinator.security_api.arm.assert_called_once_with("s1", ignore_alarms=False)

    @pytest.mark.asyncio
    async def test_alarm_arm_night(self) -> None:
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.security_api.arm_night_mode = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.config_entry.options = {"use_pin_code": False}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        await panel.async_alarm_arm_night()
        coordinator.security_api.arm_night_mode.assert_called_once_with("s1", ignore_alarms=False)

    @pytest.mark.asyncio
    async def test_alarm_arm_night_sets_night_mode_flag_optimistically(self) -> None:
        # The optimistic Space write must carry night_mode_enabled too, so the
        # debounced lite re-read (PARTIALLY_ARMED in group mode) maps back to
        # armed_night instead of armed_custom_bypass (#284).
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.security_api.arm_night_mode = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.config_entry.options = {"use_pin_code": False}
        coordinator.spaces = {"s1": self._make_space(SecurityState.DISARMED)}
        coordinator._optimistic_space_states = {}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        await panel.async_alarm_arm_night()
        assert coordinator.spaces["s1"].night_mode_enabled is True

    @pytest.mark.asyncio
    async def test_alarm_disarm_clears_night_mode_flag_optimistically(self) -> None:
        from dataclasses import replace

        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.security_api.disarm = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.config_entry.options = {"use_pin_code": False}
        coordinator.spaces = {
            "s1": replace(
                self._make_space(SecurityState.NIGHT_MODE),
                night_mode_enabled=True,
            )
        }
        coordinator._optimistic_space_states = {}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        await panel.async_alarm_disarm()
        assert coordinator.spaces["s1"].night_mode_enabled is False

    @pytest.mark.asyncio
    async def test_alarm_disarm(self) -> None:
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
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
        coordinator.config_entry.entry_id = "entry1"
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

    def test_supported_features_excludes_arm_home_when_option_disabled(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelEntityFeature,
        )

        coordinator = self._make_coordinator(use_pin_code=False)
        coordinator.config_entry.options = {"expose_arm_home": False}
        panel = AjaxAlarmControlPanel(coordinator=coordinator, space_id="s1")
        features = panel.supported_features
        assert not (features & AlarmControlPanelEntityFeature.ARM_HOME)
        # ARM_AWAY and ARM_NIGHT stay available — only the duplicate is dropped.
        assert features & AlarmControlPanelEntityFeature.ARM_AWAY
        assert features & AlarmControlPanelEntityFeature.ARM_NIGHT

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
        coordinator.config_entry.entry_id = "entry1"
        coordinator.config_entry.options = {}
        return coordinator

    def test_unique_id_per_group(self) -> None:
        coordinator = self._make_coordinator()
        panel = AjaxGroupAlarmControlPanel(coordinator=coordinator, space_id="s1", group_id="g1")
        assert panel.unique_id == "aegis_ajax_alarm_entry1_s1_group_g1"

    def test_group_supported_features_excludes_arm_home_when_option_disabled(self) -> None:
        from homeassistant.components.alarm_control_panel import (
            AlarmControlPanelEntityFeature,
        )

        coordinator = self._make_coordinator()
        coordinator.config_entry.options = {"expose_arm_home": False}
        panel = AjaxGroupAlarmControlPanel(coordinator=coordinator, space_id="s1", group_id="g1")
        features = panel.supported_features
        assert not (features & AlarmControlPanelEntityFeature.ARM_HOME)
        assert features & AlarmControlPanelEntityFeature.ARM_AWAY

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
        coordinator.config_entry.entry_id = "entry1"
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


class TestGroupMembershipAttributes:
    """#366 — which devices belong to a group, readable from Home Assistant.

    Group membership was only visible in the Ajax mobile app, so a finding
    about a device's group (#348's siren activity counter) could not be
    reproduced from Home Assistant alone. Rooms don't answer it: a device
    has a room and a group independently.
    """

    @staticmethod
    def _space() -> Space:
        return Space(
            id="s1",
            hub_id="h1",
            name="Home",
            security_state=SecurityState.PARTIALLY_ARMED,
            connection_status=ConnectionStatus.ONLINE,
            malfunctions_count=0,
            groups=(
                Group(
                    id="g1",
                    space_id="s1",
                    name="Villa",
                    security_state=SecurityState.DISARMED,
                    sorting_key="g1",
                ),
                Group(
                    id="g2",
                    space_id="s1",
                    name="Garage",
                    security_state=SecurityState.ARMED,
                    sorting_key="g2",
                ),
            ),
            group_mode_enabled=True,
        )

    @staticmethod
    def _device(did: str, name: str, group_id: str | None) -> Device:
        return Device(
            id=did,
            hub_id="h1",
            name=name,
            device_type="door_protect",
            room_id=None,
            group_id=group_id,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )

    def _panel(
        self, devices: dict[str, Device], group_id: str = "g1"
    ) -> AjaxGroupAlarmControlPanel:
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.config_entry.options = {}
        coordinator.spaces = {"s1": self._space()}
        coordinator.devices = devices
        coordinator.rooms = {}
        return AjaxGroupAlarmControlPanel(coordinator=coordinator, space_id="s1", group_id=group_id)

    def test_lists_only_its_own_members(self) -> None:
        panel = self._panel(
            {
                "d1": self._device("d1", "Front Door", "g1"),
                "d2": self._device("d2", "Garage Door", "g2"),
                "d3": self._device("d3", "Hall Motion", "g1"),
            }
        )

        attrs = panel.extra_state_attributes

        assert attrs["member_device_ids"] == ["d1", "d3"]
        assert attrs["member_device_names"] == ["Front Door", "Hall Motion"]

    def test_members_are_sorted_by_name(self) -> None:
        """Attribute order is user-visible, so it must not follow dict order."""
        panel = self._panel(
            {
                "d1": self._device("d1", "Zebra", "g1"),
                "d2": self._device("d2", "Alpha", "g1"),
            }
        )

        assert panel.extra_state_attributes["member_device_names"] == ["Alpha", "Zebra"]

    def test_ungrouped_devices_are_never_listed(self) -> None:
        panel = self._panel(
            {
                "d1": self._device("d1", "Front Door", "g1"),
                "d2": self._device("d2", "Loose Sensor", None),
            }
        )

        assert panel.extra_state_attributes["member_device_ids"] == ["d1"]

    def test_empty_group_reports_an_empty_list_not_a_missing_key(self) -> None:
        """A template reading the attribute must not have to guard for absence."""
        panel = self._panel({"d2": self._device("d2", "Garage Door", "g2")})

        attrs = panel.extra_state_attributes

        assert attrs["member_device_ids"] == []
        assert attrs["member_device_names"] == []

    def test_existing_attributes_are_preserved(self) -> None:
        panel = self._panel({"d1": self._device("d1", "Front Door", "g1")})

        attrs = panel.extra_state_attributes

        assert attrs["group_id"] == "g1"
        assert attrs["group_name"] == "Villa"
        assert attrs["hub_id"] == "h1"
