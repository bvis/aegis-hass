"""Tests for force_arm and force_arm_night custom services."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestForceArmService:
    @pytest.mark.asyncio
    async def test_force_arm_calls_security_api(self) -> None:
        """Verify arm is called with ignore_alarms=True for each space."""
        from custom_components.aegis_ajax import _async_handle_force_arm

        mock_security_api = MagicMock()
        mock_security_api.arm = AsyncMock()

        mock_coordinator = MagicMock()
        mock_coordinator._space_ids = ["space1", "space2"]
        mock_coordinator.security_api = mock_security_api
        mock_coordinator.async_request_refresh = AsyncMock()

        mock_entry = MagicMock()
        mock_entry.runtime_data = mock_coordinator

        hass = MagicMock()
        hass.config_entries.async_entries = MagicMock(return_value=[mock_entry])

        call = MagicMock()
        call.data = {}  # No entity_id → all spaces

        await _async_handle_force_arm(hass, call)

        assert mock_security_api.arm.call_count == 2
        mock_security_api.arm.assert_any_call("space1", ignore_alarms=True)
        mock_security_api.arm.assert_any_call("space2", ignore_alarms=True)
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_force_arm_night_calls_security_api(self) -> None:
        """Verify arm_night_mode is called with ignore_alarms=True for each space."""
        from custom_components.aegis_ajax import _async_handle_force_arm_night

        mock_security_api = MagicMock()
        mock_security_api.arm_night_mode = AsyncMock()

        mock_coordinator = MagicMock()
        mock_coordinator._space_ids = ["space1", "space2"]
        mock_coordinator.security_api = mock_security_api
        mock_coordinator.async_request_refresh = AsyncMock()

        mock_entry = MagicMock()
        mock_entry.runtime_data = mock_coordinator

        hass = MagicMock()
        hass.config_entries.async_entries = MagicMock(return_value=[mock_entry])

        call = MagicMock()
        call.data = {}  # No entity_id → all spaces

        await _async_handle_force_arm_night(hass, call)

        assert mock_security_api.arm_night_mode.call_count == 2
        mock_security_api.arm_night_mode.assert_any_call("space1", ignore_alarms=True)
        mock_security_api.arm_night_mode.assert_any_call("space2", ignore_alarms=True)
        mock_coordinator.async_request_refresh.assert_called_once()


class TestDisarmNightModeService:
    @pytest.mark.asyncio
    async def test_disarm_night_mode_calls_security_api(self) -> None:
        """Verify disarm_from_night_mode is called per space (#233)."""
        from custom_components.aegis_ajax import _async_handle_disarm_night_mode

        mock_security_api = MagicMock()
        mock_security_api.disarm_from_night_mode = AsyncMock()

        mock_coordinator = MagicMock()
        mock_coordinator._space_ids = ["space1", "space2"]
        mock_coordinator.security_api = mock_security_api
        mock_coordinator.async_request_refresh = AsyncMock()

        mock_entry = MagicMock()
        mock_entry.runtime_data = mock_coordinator

        hass = MagicMock()
        hass.config_entries.async_entries = MagicMock(return_value=[mock_entry])

        call = MagicMock()
        call.data = {}  # No entity_id → all spaces

        await _async_handle_disarm_night_mode(hass, call)

        assert mock_security_api.disarm_from_night_mode.call_count == 2
        mock_security_api.disarm_from_night_mode.assert_any_call("space1")
        mock_security_api.disarm_from_night_mode.assert_any_call("space2")
        mock_coordinator.async_request_refresh.assert_called_once()


class TestServiceRegistration:
    @pytest.mark.asyncio
    async def test_services_registered_on_setup(self) -> None:
        """Verify services are registered during async_setup_entry."""
        from custom_components.aegis_ajax import async_setup_entry

        hass = MagicMock()
        hass.data = {}
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        hass.services.async_register = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)

        entry = MagicMock()
        entry.entry_id = "entry-1"
        entry.data = {
            "email": "test@example.com",
            "password_hash": "abc123hash",
            "spaces": ["s1"],
        }
        entry.options = {"poll_interval": 30}

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.session = MagicMock()

        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_start_push_notifications = AsyncMock()

        with (
            patch(
                "custom_components.aegis_ajax.AjaxGrpcClient",
                return_value=mock_client,
            ),
            patch(
                "custom_components.aegis_ajax.AjaxCobrandedCoordinator",
                return_value=mock_coordinator,
            ),
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
        # Verify all custom services were registered
        register_calls = {
            call_args[0][1] for call_args in hass.services.async_register.call_args_list
        }
        assert "force_arm" in register_calls
        assert "force_arm_night" in register_calls
        assert "disarm_night_mode" in register_calls
        assert "press_panic_button" in register_calls
        assert "set_photo_on_demand_mode" in register_calls

    @pytest.mark.asyncio
    async def test_services_removed_on_unload(self) -> None:
        """Verify services are removed during async_unload_entry."""
        from custom_components.aegis_ajax import async_unload_entry

        mock_coordinator = MagicMock()
        mock_coordinator.async_shutdown = AsyncMock()

        hass = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        hass.services.async_remove = MagicMock()

        entry = MagicMock()
        entry.entry_id = "entry-1"
        entry.runtime_data = mock_coordinator

        result = await async_unload_entry(hass, entry)

        assert result is True
        remove_calls = {call_args[0][1] for call_args in hass.services.async_remove.call_args_list}
        # Every service registered in async_setup_entry must be removed on unload
        assert "force_arm" in remove_calls
        assert "force_arm_night" in remove_calls
        assert "disarm_night_mode" in remove_calls
        assert "press_panic_button" in remove_calls
        assert "set_photo_on_demand_mode" in remove_calls
