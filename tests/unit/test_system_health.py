"""Tests for the System Health card."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

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
    async def test_no_entries_marks_no_accounts(self) -> None:
        """With no configured entries, reachability falls back to a sentinel."""
        hass = self._make_hass([])
        info = await _system_health_info(hass)
        assert info["can_reach_server"] == "no accounts configured"
        assert info["configured_accounts"] == 0
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
        # Reachability derived from poll freshness: any account polled
        # within the staleness window means we're reachable.
        assert info["can_reach_server"] == "reachable"

    @pytest.mark.asyncio
    async def test_can_reach_server_unreachable_when_polls_stale(self) -> None:
        """A stale `last_update_success_time` (>10 min) flips the reach line."""
        coord = MagicMock()
        coord.spaces = {"s1": MagicMock()}
        coord.is_hts_connected = False
        coord.last_update_success_time = datetime.now(UTC) - timedelta(minutes=30)
        listener = MagicMock()
        listener.is_fcm_connected = False
        listener.pushes_received = 0
        listener.last_push_at = None
        coord.notification_listener = listener

        hass = self._make_hass([MagicMock(runtime_data=coord)])
        info = await _system_health_info(hass)
        assert info["can_reach_server"] == "unreachable"

    @pytest.mark.asyncio
    async def test_reachable_when_poll_stale_but_hts_connected(self) -> None:
        """A stale poll alone must not read as unreachable: HTS/FCM updates reset
        HA's poll timer, so the polled refresh can be starved while data still
        flows (#236). A live HTS connection means the cloud is reachable."""
        coord = MagicMock()
        coord.spaces = {"s1": MagicMock()}
        coord.is_hts_connected = True
        coord.last_update_success_time = datetime.now(UTC) - timedelta(minutes=30)
        listener = MagicMock()
        listener.is_fcm_connected = True
        listener.pushes_received = 5
        listener.last_push_at = None
        coord.notification_listener = listener

        hass = self._make_hass([MagicMock(runtime_data=coord)])
        info = await _system_health_info(hass)
        assert info["can_reach_server"] == "reachable"

    @pytest.mark.asyncio
    async def test_reachable_when_poll_stale_but_recent_push(self) -> None:
        """A recent FCM push is also proof of reachability even if the poll is stale."""
        coord = MagicMock()
        coord.spaces = {"s1": MagicMock()}
        coord.is_hts_connected = False
        coord.last_update_success_time = datetime.now(UTC) - timedelta(minutes=30)
        listener = MagicMock()
        listener.is_fcm_connected = True
        listener.pushes_received = 3
        listener.last_push_at = time.monotonic() - 30  # 30s ago
        coord.notification_listener = listener

        hass = self._make_hass([MagicMock(runtime_data=coord)])
        info = await _system_health_info(hass)
        assert info["can_reach_server"] == "reachable"

    @pytest.mark.asyncio
    async def test_can_reach_server_never_polled(self) -> None:
        """A fresh-install state with `last_update_success_time=None` says so explicitly."""
        coord = MagicMock()
        coord.spaces = {}
        coord.is_hts_connected = False
        coord.last_update_success_time = None
        listener = MagicMock()
        listener.is_fcm_connected = False
        listener.pushes_received = 0
        listener.last_push_at = None
        coord.notification_listener = listener

        hass = self._make_hass([MagicMock(runtime_data=coord)])
        info = await _system_health_info(hass)
        assert info["can_reach_server"] == "never polled"

    @pytest.mark.asyncio
    async def test_skips_entries_without_runtime_data(self) -> None:
        """A still-loading entry (no runtime_data) must not crash the card."""
        entry = MagicMock(runtime_data=None)
        hass = self._make_hass([entry])
        info = await _system_health_info(hass)

        assert info["configured_accounts"] == 1
        assert info["spaces"] == 0
        assert info["hts_connected"] == "0/1"
        assert info["fcm_connected"] == "0/1"
        assert info["can_reach_server"] == "never polled"
