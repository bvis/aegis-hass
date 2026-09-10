"""Tests for force_arm and force_arm_night custom services."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

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


class TestRefreshAlarmImagesService:
    @pytest.mark.asyncio
    async def test_returns_import_summary_for_every_space(self) -> None:
        from custom_components.aegis_ajax import _async_handle_refresh_alarm_images

        coordinator = MagicMock()
        coordinator._space_ids = ["space1", "space2"]
        coordinator.async_import_alarm_images = AsyncMock(
            side_effect=[
                {"notifications": 1, "images": 3},
                {"notifications": 2, "images": 4},
            ]
        )
        entry = MagicMock(runtime_data=coordinator)
        hass = MagicMock()
        hass.config_entries.async_entries = MagicMock(return_value=[entry])
        service_call = MagicMock(data={})

        result = await _async_handle_refresh_alarm_images(hass, service_call)

        # `skipped` counts spaces still inside their backfill cooldown, so a
        # cooling space cannot hide what the others returned (#495 follow-up).
        assert result == {"spaces": 2, "notifications": 3, "images": 7, "skipped": 0}
        assert coordinator.async_import_alarm_images.await_args_list == [
            call("space1"),
            call("space2"),
        ]


class TestClientSessionServices:
    @staticmethod
    def _session(
        session_id: int, *, is_current: bool = False, is_self_identity: bool = False
    ) -> MagicMock:
        return MagicMock(
            session_id=session_id,
            is_current=is_current,
            is_self_identity=is_self_identity,
        )

    @pytest.mark.asyncio
    async def test_list_sessions_surfaces_hts_errors_as_service_validation_errors(self) -> None:
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.aegis_ajax import _async_handle_list_client_sessions
        from custom_components.aegis_ajax.api.hts.client import HtsConnectionError

        coordinator = MagicMock()
        coordinator.async_list_client_sessions = AsyncMock(
            side_effect=HtsConnectionError("connection closed")
        )
        entry = MagicMock()
        entry.runtime_data = coordinator
        hass = MagicMock()
        hass.config_entries.async_entries = MagicMock(return_value=[entry])
        call = MagicMock()
        call.data = {}

        with pytest.raises(ServiceValidationError, match="Could not list Ajax account sessions"):
            await _async_handle_list_client_sessions(hass, call)

    @pytest.mark.asyncio
    async def test_current_session_cannot_be_terminated(self) -> None:
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        coordinator = object.__new__(AjaxCobrandedCoordinator)
        coordinator._hts_client = MagicMock(is_connected=True)
        coordinator._hts_client.get_client_sessions = AsyncMock(
            return_value=[self._session(1, is_current=True)]
        )

        with pytest.raises(ValueError, match="Aegis integration sessions"):
            await coordinator.async_terminate_client_session(1)
        coordinator._hts_client.kill_client_sessions.assert_not_called()

    @pytest.mark.asyncio
    async def test_self_identity_session_cannot_be_terminated(self) -> None:
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        coordinator = object.__new__(AjaxCobrandedCoordinator)
        coordinator._hts_client = MagicMock(is_connected=True)
        coordinator._hts_client.get_client_sessions = AsyncMock(
            return_value=[self._session(1, is_current=False, is_self_identity=True)]
        )

        with pytest.raises(ValueError, match="Aegis integration sessions"):
            await coordinator.async_terminate_client_session(1)
        coordinator._hts_client.kill_client_sessions.assert_not_called()

    @pytest.mark.asyncio
    async def test_terminate_other_sessions_excludes_current_session(self) -> None:
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        coordinator = object.__new__(AjaxCobrandedCoordinator)
        coordinator._hts_client = MagicMock(is_connected=True)
        coordinator._hts_client.get_client_sessions = AsyncMock(
            return_value=[
                self._session(1, is_current=True),
                self._session(2),
                self._session(3),
                self._session(4, is_current=False, is_self_identity=True),
            ]
        )
        coordinator._hts_client.kill_client_sessions = AsyncMock(side_effect=lambda ids: ids)

        assert await coordinator.async_terminate_other_client_sessions() == 2
        coordinator._hts_client.kill_client_sessions.assert_awaited_once_with([2, 3])

    @pytest.mark.asyncio
    async def test_uncertain_single_termination_succeeds_when_verified_absent(self) -> None:
        from custom_components.aegis_ajax.api.hts.client import HtsTerminationOutcomeUnknownError
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        coordinator = object.__new__(AjaxCobrandedCoordinator)
        coordinator._hts_client = MagicMock(is_connected=True)
        coordinator._hts_client.get_client_sessions = AsyncMock(
            return_value=[self._session(1), self._session(2)]
        )
        coordinator._hts_client.kill_client_sessions = AsyncMock(
            side_effect=HtsTerminationOutcomeUnknownError(2)
        )
        coordinator._async_verify_termination_after_uncertain_outcome = AsyncMock(return_value=True)

        await coordinator.async_terminate_client_session(2)

        coordinator._hts_client.kill_client_sessions.assert_awaited_once_with([2])
        coordinator._async_verify_termination_after_uncertain_outcome.assert_awaited_once_with(
            2, {1}
        )

    @pytest.mark.asyncio
    async def test_uncertain_bulk_termination_counts_verified_final_session(self) -> None:
        from custom_components.aegis_ajax.api.hts.client import HtsTerminationOutcomeUnknownError
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        coordinator = object.__new__(AjaxCobrandedCoordinator)
        coordinator._hts_client = MagicMock(is_connected=True)
        coordinator._hts_client.get_client_sessions = AsyncMock(
            return_value=[self._session(1, is_current=True), self._session(2), self._session(3)]
        )
        coordinator._hts_client.kill_client_sessions = AsyncMock(
            side_effect=HtsTerminationOutcomeUnknownError(3, [2])
        )
        coordinator._async_verify_termination_after_uncertain_outcome = AsyncMock(return_value=True)

        assert await coordinator.async_terminate_other_client_sessions() == 2
        coordinator._hts_client.kill_client_sessions.assert_awaited_once_with([2, 3])
        coordinator._async_verify_termination_after_uncertain_outcome.assert_awaited_once_with(
            3, {1}
        )

    @pytest.mark.asyncio
    async def test_uncertain_termination_remaining_session_reports_confirmed_failure(self) -> None:
        from custom_components.aegis_ajax.api.hts.client import (
            HtsConnectionError,
            HtsTerminationOutcomeUnknownError,
        )
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        coordinator = object.__new__(AjaxCobrandedCoordinator)
        coordinator._hts_client = MagicMock(is_connected=True)
        coordinator._hts_client.get_client_sessions = AsyncMock(return_value=[self._session(2)])
        coordinator._hts_client.kill_client_sessions = AsyncMock(
            side_effect=HtsTerminationOutcomeUnknownError(2)
        )
        coordinator._async_verify_termination_after_uncertain_outcome = AsyncMock(
            return_value=False
        )

        with pytest.raises(HtsConnectionError, match="remains active"):
            await coordinator.async_terminate_client_session(2)
        coordinator._hts_client.kill_client_sessions.assert_awaited_once_with([2])

    @pytest.mark.asyncio
    async def test_uncertain_outcome_verification_lists_once_after_reconnect(self) -> None:
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        coordinator = object.__new__(AjaxCobrandedCoordinator)
        coordinator._hts_client = MagicMock(is_connected=False)
        coordinator._hts_client.get_client_sessions = AsyncMock(return_value=[self._session(1)])

        async def reconnect() -> None:
            coordinator._hts_client.is_connected = True

        coordinator._maybe_restart_hts = AsyncMock(side_effect=reconnect)

        assert await coordinator._async_verify_termination_after_uncertain_outcome(2, {1}) is True
        coordinator._maybe_restart_hts.assert_awaited_once()
        coordinator._hts_client.get_client_sessions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uncertain_outcome_verification_failure_is_explicit(self) -> None:
        from custom_components.aegis_ajax.api.hts.client import HtsConnectionError
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        coordinator = object.__new__(AjaxCobrandedCoordinator)
        coordinator._hts_client = MagicMock(is_connected=True)
        coordinator._hts_client.get_client_sessions = AsyncMock(
            side_effect=HtsConnectionError("verification closed")
        )
        coordinator._maybe_restart_hts = AsyncMock()

        with pytest.raises(HtsConnectionError, match="outcome is unknown"):
            await coordinator._async_verify_termination_after_uncertain_outcome(2, set())
        coordinator._hts_client.get_client_sessions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uncertain_outcome_verification_attempts_one_reconnect(self) -> None:
        from custom_components.aegis_ajax.api.hts.client import HtsConnectionError
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        coordinator = object.__new__(AjaxCobrandedCoordinator)
        coordinator._hts_client = None
        coordinator._maybe_restart_hts = AsyncMock()

        monotonic = iter([0.0, 30.0])
        with (
            patch(
                "custom_components.aegis_ajax.coordinator.time.monotonic",
                side_effect=monotonic,
            ),
            pytest.raises(HtsConnectionError, match="did not reconnect"),
        ):
            await coordinator._async_verify_termination_after_uncertain_outcome(2, set())

        coordinator._maybe_restart_hts.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uncertain_outcome_empty_verification_is_unknown(self) -> None:
        from custom_components.aegis_ajax.api.hts.client import HtsConnectionError
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        coordinator = object.__new__(AjaxCobrandedCoordinator)
        coordinator._hts_client = MagicMock(is_connected=True)
        coordinator._hts_client.get_client_sessions = AsyncMock(return_value=[])
        coordinator._maybe_restart_hts = AsyncMock()

        with pytest.raises(HtsConnectionError, match="outcome is unknown"):
            await coordinator._async_verify_termination_after_uncertain_outcome(2, set())
        coordinator._hts_client.get_client_sessions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uncertain_outcome_undecodable_session_id_is_unknown(self) -> None:
        """A record whose 0x01 did not decode cannot prove the target is gone.

        `timestamp()` yields None unless the value is exactly eight bytes, so a
        response clipped inside the target's own creation timestamp comes back
        with the target's record present and unidentifiable — which the absence
        check would otherwise read as a successful termination.
        """
        from custom_components.aegis_ajax.api.hts.client import HtsConnectionError
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        coordinator = object.__new__(AjaxCobrandedCoordinator)
        coordinator._hts_client = MagicMock(is_connected=True)
        coordinator._hts_client.get_client_sessions = AsyncMock(
            return_value=[self._session(1), self._session(3), self._session(None)]
        )
        coordinator._maybe_restart_hts = AsyncMock()

        with pytest.raises(HtsConnectionError, match="could not be decoded"):
            await coordinator._async_verify_termination_after_uncertain_outcome(2, {1, 3})
        coordinator._hts_client.get_client_sessions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uncertain_outcome_truncated_verification_is_unknown(self) -> None:
        from custom_components.aegis_ajax.api.hts.client import HtsConnectionError
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        coordinator = object.__new__(AjaxCobrandedCoordinator)
        coordinator._hts_client = MagicMock(is_connected=True)
        coordinator._hts_client.get_client_sessions = AsyncMock(return_value=[self._session(1)])
        coordinator._maybe_restart_hts = AsyncMock()

        with pytest.raises(
            HtsConnectionError,
            match=r"omitted previously active session ID\(s\) \[3\]",
        ):
            await coordinator._async_verify_termination_after_uncertain_outcome(2, {1, 3})
        coordinator._hts_client.get_client_sessions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_termination_requires_confirmation(self) -> None:
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.aegis_ajax import _async_handle_terminate_client_session

        with pytest.raises(ServiceValidationError, match="confirm: true"):
            await _async_handle_terminate_client_session(MagicMock(), MagicMock(data={}))


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
            patch("custom_components.aegis_ajax.dr.async_get", return_value=MagicMock()),
            patch(
                "custom_components.aegis_ajax.AjaxGrpcClient",
                return_value=mock_client,
            ),
            patch(
                "custom_components.aegis_ajax.AjaxCobrandedCoordinator",
                return_value=mock_coordinator,
            ),
            patch("custom_components.aegis_ajax.dr.async_get"),
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
        from homeassistant.core import SupportsResponse

        # Verify all custom services were registered
        register_calls = {
            call_args[0][1]: call_args[1].get("supports_response")
            for call_args in hass.services.async_register.call_args_list
        }
        assert "force_arm" in register_calls
        assert "force_arm_night" in register_calls
        assert "disarm_night_mode" in register_calls
        assert "press_panic_button" in register_calls
        assert "set_photo_on_demand_mode" in register_calls
        assert "refresh_alarm_images" in register_calls
        assert register_calls["refresh_alarm_images"] == SupportsResponse.OPTIONAL
        assert "list_client_sessions" in register_calls
        assert register_calls["list_client_sessions"] == SupportsResponse.ONLY
        assert "terminate_client_session" in register_calls
        assert "terminate_other_client_sessions" in register_calls
        assert register_calls["terminate_other_client_sessions"] == SupportsResponse.OPTIONAL

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
        assert "refresh_alarm_images" in remove_calls
        assert "list_client_sessions" in remove_calls
        assert "terminate_client_session" in remove_calls
        assert "terminate_other_client_sessions" in remove_calls
