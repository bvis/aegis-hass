"""Tests for the System Health card."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from custom_components.aegis_ajax.system_health import (
    _format_age,
    _system_health_info,
    async_register,
)


class TestFormatAge:
    def test_none_renders_as_never(self) -> None:
        assert _format_age(None) == "never"

    def test_seconds(self) -> None:
        assert _format_age(12) == "12s ago"

    def test_minutes(self) -> None:
        assert _format_age(125) == "2m ago"

    def test_hours(self) -> None:
        assert _format_age(7200) == "2h ago"

    def test_days(self) -> None:
        assert _format_age(2 * 86400) == "2d ago"


class TestAsyncRegister:
    def test_calls_register_async_register_info(self) -> None:
        hass = MagicMock()
        register = MagicMock()
        async_register(hass, register)
        register.async_register_info.assert_called_once()


class TestSystemHealthInfo:
    @staticmethod
    def _make_hass(entries: list[MagicMock]) -> MagicMock:
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = entries
        return hass

    @pytest.mark.asyncio
    async def test_no_entries_only_returns_reach_check(self) -> None:
        """With no configured entries, only the gRPC reach probe + count are returned."""
        hass = self._make_hass([])
        with patch(
            "custom_components.aegis_ajax.system_health.system_health.async_check_can_reach_url",
            new=MagicMock(return_value="reach-coro"),
        ):
            info = await _system_health_info(hass)
        assert info["can_reach_server"] == "reach-coro"
        assert info["configured_accounts"] == 0
        # No per-entry metrics should be set when there are no entries
        assert "spaces" not in info

    @pytest.mark.asyncio
    async def test_aggregates_across_entries(self) -> None:
        """Two configured accounts: spaces / HTS / FCM / pushes are summed."""
        coord_a = MagicMock()
        coord_a.spaces = {"s1": MagicMock(), "s2": MagicMock()}
        coord_a.is_hts_connected = True
        coord_a.last_update_success_time = datetime.now(UTC) - timedelta(seconds=30)
        listener_a = MagicMock()
        listener_a.is_fcm_connected = True
        listener_a.pushes_received = 17
        listener_a.last_push_at = None  # never
        coord_a.notification_listener = listener_a

        coord_b = MagicMock()
        coord_b.spaces = {"s3": MagicMock()}
        coord_b.is_hts_connected = False
        coord_b.last_update_success_time = None  # never polled successfully
        listener_b = MagicMock()
        listener_b.is_fcm_connected = False
        listener_b.pushes_received = 3
        listener_b.last_push_at = None
        coord_b.notification_listener = listener_b

        entry_a = MagicMock(runtime_data=coord_a)
        entry_b = MagicMock(runtime_data=coord_b)
        hass = self._make_hass([entry_a, entry_b])

        with patch(
            "custom_components.aegis_ajax.system_health.system_health.async_check_can_reach_url",
            new=MagicMock(return_value="reach-coro"),
        ):
            info = await _system_health_info(hass)

        assert info["configured_accounts"] == 2
        assert info["spaces"] == 3
        assert info["hts_connected"] == "1/2"
        assert info["fcm_connected"] == "1/2"
        assert info["pushes_received"] == 20
        # last_push: both listeners report None
        assert info["last_push"] == "never"
        # last_poll: only coord_a has a real timestamp, ~30s old
        assert info["last_poll"].endswith("s ago")

    @pytest.mark.asyncio
    async def test_skips_entries_without_runtime_data(self) -> None:
        """A still-loading entry (no runtime_data) must not crash the card."""
        entry = MagicMock(runtime_data=None)
        hass = self._make_hass([entry])

        with patch(
            "custom_components.aegis_ajax.system_health.system_health.async_check_can_reach_url",
            new=MagicMock(return_value="reach-coro"),
        ):
            info = await _system_health_info(hass)

        assert info["configured_accounts"] == 1
        assert info["spaces"] == 0
        assert info["hts_connected"] == "0/1"
        assert info["fcm_connected"] == "0/1"
