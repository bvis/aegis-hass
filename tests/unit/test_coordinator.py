"""Tests for the data update coordinator."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax.api.hts.hub_state import HubNetworkState
from custom_components.aegis_ajax.api.models import (
    Device,
    MonitoringCompany,
    MonitoringCompanyStatus,
    Room,
    Space,
    SpaceSnapshot,
)
from custom_components.aegis_ajax.const import (
    HUB_DEVICE_TEMP_REFRESH_INTERVAL,
    SIREN_ALARM_DURATION_KEY,
    SIREN_VOLUME_LEVEL_KEY,
    ChimeStatus,
    ConnectionStatus,
    DeviceState,
    SecurityState,
)
from custom_components.aegis_ajax.coordinator import _HTS_SPACE_CONTROL_GATING_KEYS


def _make_space(space_id: str = "s1") -> Space:
    return Space(
        id=space_id,
        hub_id="hub-1",
        name="Home",
        security_state=SecurityState.DISARMED,
        connection_status=ConnectionStatus.ONLINE,
        malfunctions_count=0,
    )


def _make_device(device_id: str = "d1", statuses: dict | None = None) -> Device:
    return Device(
        id=device_id,
        hub_id="hub-1",
        name="Sensor",
        device_type="door_protect",
        room_id=None,
        group_id=None,
        state=DeviceState.ONLINE,
        malfunctions=0,
        bypassed=False,
        statuses=statuses if statuses is not None else {},
        battery=None,
    )


def _make_coordinator(
    space_ids: list[str] | None = None,
) -> AjaxCobrandedCoordinator:  # noqa: F821
    """Create coordinator with DataUpdateCoordinator.__init__ patched."""
    from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

    hass = MagicMock()
    client = MagicMock()
    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        coordinator = AjaxCobrandedCoordinator(
            hass=hass, client=client, space_ids=space_ids or ["s1"], poll_interval=30
        )
    coordinator.hass = hass
    return coordinator


class TestCoordinatorInit:
    def test_attributes(self) -> None:
        coordinator = _make_coordinator()
        assert coordinator._space_ids == ["s1"]

    def test_poll_interval_is_clamped_to_minimum(self) -> None:
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        hass = MagicMock()
        client = MagicMock()
        with patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
            return_value=None,
        ) as mock_init:
            AjaxCobrandedCoordinator(hass=hass, client=client, space_ids=["s1"], poll_interval=5)

        assert mock_init.call_args.kwargs["update_interval"] == timedelta(seconds=60)

    def test_data_structure(self) -> None:
        coordinator = _make_coordinator()
        assert coordinator.spaces == {}
        assert coordinator.devices == {}

    def test_security_api_property(self) -> None:
        coordinator = _make_coordinator()
        assert coordinator.security_api is coordinator._security_api

    def test_devices_api_property(self) -> None:
        coordinator = _make_coordinator()
        assert coordinator.devices_api is coordinator._devices_api

    def test_hub_network_initially_empty(self) -> None:
        coordinator = _make_coordinator()
        assert coordinator.hub_network == {}

    def test_device_readings_initially_empty(self) -> None:
        coordinator = _make_coordinator()
        assert coordinator.device_readings == {}

    def test_rooms_initially_empty(self) -> None:
        coordinator = _make_coordinator()
        assert coordinator.rooms == {}

    def test_last_update_success_time_initially_none(self) -> None:
        # Regression for #74 follow-up — the System Health card calls
        # `coordinator.last_update_success_time` and HA renders the row as
        # "error: unknown" if the attribute raises. The real
        # `DataUpdateCoordinator` doesn't expose this attribute, so the
        # subclass has to provide it — verify the default before any poll.
        coordinator = _make_coordinator()
        assert coordinator.last_update_success_time is None


class TestRoomsRefresh:
    @pytest.mark.asyncio
    async def test_rooms_populated_from_spaces_api(self) -> None:
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._spaces_api.get_space_snapshot = AsyncMock(
            return_value=SpaceSnapshot(
                rooms=(Room(id="r1", name="Kitchen", space_id="s1"),),
            )
        )
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        await coordinator._async_update_data()

        assert coordinator.rooms == {"r1": Room(id="r1", name="Kitchen", space_id="s1")}

    @pytest.mark.asyncio
    async def test_rooms_failure_swallowed(self) -> None:
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._spaces_api.get_space_snapshot = AsyncMock(side_effect=RuntimeError("oops"))
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        # Should not raise — failure is downgraded to debug log
        await coordinator._async_update_data()
        assert coordinator.rooms == {}

    @pytest.mark.asyncio
    async def test_monitoring_companies_populated_from_space_snapshot(self) -> None:
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._spaces_api.get_space_snapshot = AsyncMock(
            return_value=SpaceSnapshot(
                monitoring_companies=(
                    MonitoringCompany(
                        name="Central One",
                        status=MonitoringCompanyStatus.APPROVED,
                    ),
                ),
                monitoring_companies_loaded=True,
            )
        )
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        await coordinator._async_update_data()

        assert coordinator.spaces["s1"].has_monitoring is True
        assert coordinator.spaces["s1"].approved_monitoring_companies == (
            MonitoringCompany(
                name="Central One",
                status=MonitoringCompanyStatus.APPROVED,
            ),
        )
        assert coordinator.spaces["s1"].monitoring_companies_loaded is True


class TestAsyncUpdateData:
    @pytest.mark.asyncio
    async def test_update_data_when_authenticated(self) -> None:
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        # Mark streams already started so fallback polling runs
        coordinator._streams_started = True

        space = _make_space("s1")
        device = _make_device("d1")

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[space])
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[device])

        result = await coordinator._async_update_data()
        assert "spaces" in result
        assert "devices" in result
        assert "s1" in result["spaces"]
        assert "d1" in result["devices"]

    @pytest.mark.asyncio
    async def test_update_data_sets_last_success_timestamp(self) -> None:
        # Regression for #74 follow-up — the System Health card reads
        # `last_update_success_time` to render the "last poll" age. Before
        # this fix the attribute didn't exist and the entire row blew up
        # with "error: unknown" instead of showing diagnostic data.
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True

        space = _make_space("s1")
        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[space])
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        assert coordinator.last_update_success_time is None
        await coordinator._async_update_data()
        first_ts = coordinator.last_update_success_time
        assert first_ts is not None

        await coordinator._async_update_data()
        assert coordinator.last_update_success_time >= first_ts

    @pytest.mark.asyncio
    async def test_update_data_converts_subcall_cancel_to_update_failed(self) -> None:
        """Regression for #148: when the gRPC stub raises `CancelledError`
        mid-flight (most common during a reload race), the coordinator
        used to let it propagate — and `CancelledError` is a
        `BaseException`, so HA's first-refresh path marked the entry as
        permanently failed instead of retrying. Convert to `UpdateFailed`
        so HA's standard retry-with-backoff applies."""
        from homeassistant.helpers.update_coordinator import UpdateFailed

        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(UpdateFailed, match="cancelled mid-flight"):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_update_data_propagates_external_cancellation(self) -> None:
        """Counter-test: when HA cancels OUR task (shutdown, options
        listener reload, etc.) the `CancelledError` must propagate so
        the coroutine actually exits. Eating it would leave dangling
        coroutines and prevent HA from completing teardown.

        Setup: run `_async_update_data` inside a separate task and
        cancel that task before the loop runs it. The first await
        inside the coroutine delivers `CancelledError` while
        `cancelling()` is non-zero, hitting the re-raise branch.
        """
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[])
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        task = asyncio.create_task(coordinator._async_update_data())
        task.cancel()  # pre-cancel so cancelling() > 0 when the coro first awaits
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_update_data_does_not_set_timestamp_on_failure(self) -> None:
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(Exception):  # noqa: B017 - wrapped as UpdateFailed
            await coordinator._async_update_data()
        assert coordinator.last_update_success_time is None

    @pytest.mark.asyncio
    async def test_update_data_logs_in_when_not_authenticated(self) -> None:
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = False
        coordinator._client.login = AsyncMock()

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[])
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        await coordinator._async_update_data()
        coordinator._client.login.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_data_filters_spaces_by_id(self) -> None:
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True

        space_s1 = _make_space("s1")
        space_s2 = _make_space("s2")

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[space_s1, space_s2])
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        result = await coordinator._async_update_data()
        assert "s1" in result["spaces"]
        assert "s2" not in result["spaces"]

    @pytest.mark.asyncio
    async def test_update_data_preserves_cached_monitoring_companies_between_snapshot_refreshes(
        self,
    ) -> None:
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True
        coordinator._rooms_last_fetch = asyncio.get_running_loop().time()
        coordinator.spaces["s1"] = replace(
            _make_space("s1"),
            monitoring_companies=(
                MonitoringCompany(
                    name="Central One",
                    status=MonitoringCompanyStatus.APPROVED,
                ),
            ),
            monitoring_companies_loaded=True,
        )

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        await coordinator._async_update_data()

        assert coordinator.spaces["s1"].approved_monitoring_companies == (
            MonitoringCompany(
                name="Central One",
                status=MonitoringCompanyStatus.APPROVED,
            ),
        )
        assert coordinator.spaces["s1"].monitoring_companies_loaded is True

    @pytest.mark.asyncio
    async def test_update_data_preserves_cached_groups_between_snapshot_refreshes(
        self,
    ) -> None:
        """list_spaces() doesn't return groups; the coordinator must keep the
        previously cached groups + group_mode_enabled, otherwise per-group
        alarm panels go unavailable on every poll between hourly snapshots.
        """
        from custom_components.aegis_ajax.api.models import Group

        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True
        coordinator._rooms_last_fetch = asyncio.get_running_loop().time()
        cached_groups = (
            Group(
                id="g1",
                space_id="s1",
                name="Villa",
                security_state=SecurityState.ARMED,
                sorting_key="01",
            ),
            Group(
                id="g2",
                space_id="s1",
                name="Apartment",
                security_state=SecurityState.DISARMED,
                sorting_key="02",
            ),
        )
        coordinator.spaces["s1"] = replace(
            _make_space("s1"),
            groups=cached_groups,
            group_mode_enabled=True,
        )

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        await coordinator._async_update_data()

        assert coordinator.spaces["s1"].groups == cached_groups
        assert coordinator.spaces["s1"].group_mode_enabled is True

    @pytest.mark.asyncio
    async def test_update_data_preserves_night_mode_enabled_between_snapshot_refreshes(
        self,
    ) -> None:
        """`list_spaces()` (LiteSpace) doesn't carry `night_mode_enabled`; the
        coordinator must keep the cached flag or the panel flips from
        `armed_night` to `armed_custom_bypass` on every plain poll (#284).
        """
        from custom_components.aegis_ajax.api.models import Group

        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True
        coordinator._rooms_last_fetch = asyncio.get_running_loop().time()
        coordinator.spaces["s1"] = replace(
            _make_space("s1"),
            groups=(
                Group(
                    id="g1",
                    space_id="s1",
                    name="Villa",
                    security_state=SecurityState.ARMED,
                    sorting_key="01",
                ),
            ),
            group_mode_enabled=True,
            night_mode_enabled=True,
        )

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        await coordinator._async_update_data()

        assert coordinator.spaces["s1"].night_mode_enabled is True

    @pytest.mark.asyncio
    async def test_snapshot_refresh_applies_night_mode_enabled(self) -> None:
        """The hourly/forced snapshot is the authoritative source for
        `night_mode_enabled` — it must overwrite the cached flag (#284)."""
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True
        coordinator._rooms_last_fetch = asyncio.get_running_loop().time()
        coordinator.spaces["s1"] = replace(_make_space("s1"), night_mode_enabled=False)
        coordinator._force_snapshot_refresh = True

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._spaces_api.get_space_snapshot = AsyncMock(
            return_value=SpaceSnapshot(group_mode_enabled=True, night_mode_enabled=True)
        )
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        await coordinator._async_update_data()

        assert coordinator.spaces["s1"].night_mode_enabled is True

    @pytest.mark.asyncio
    async def test_space_event_forces_group_state_refresh_within_the_hour(self) -> None:
        """A space arm/disarm HTS event (#266) sets `_force_snapshot_refresh`, so
        the next poll re-reads group security states from the heavier snapshot
        even inside the hourly window — without it, per-group panels lag up to an
        hour when FCM push is off. The flag is consumed so later polls stay light.
        """
        from custom_components.aegis_ajax.api.models import Group

        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True
        # Pretend the hourly snapshot just ran — the gate would normally skip it.
        coordinator._rooms_last_fetch = asyncio.get_running_loop().time()
        coordinator.spaces["s1"] = replace(
            _make_space("s1"),
            groups=(
                Group(
                    id="g1",
                    space_id="s1",
                    name="Garage",
                    security_state=SecurityState.DISARMED,
                    sorting_key="01",
                ),
            ),
            group_mode_enabled=True,
        )
        # A space event arrived → forces the next refresh to re-read groups.
        coordinator._force_snapshot_refresh = True

        fresh_groups = (
            Group(
                id="g1",
                space_id="s1",
                name="Garage",
                security_state=SecurityState.ARMED,
                sorting_key="01",
            ),
        )
        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._spaces_api.get_space_snapshot = AsyncMock(
            return_value=SpaceSnapshot(groups=fresh_groups, group_mode_enabled=True)
        )
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        await coordinator._async_update_data()

        # Snapshot was read despite being inside the hour, and group state moved.
        coordinator._spaces_api.get_space_snapshot.assert_awaited_once()
        assert coordinator.spaces["s1"].groups[0].security_state == SecurityState.ARMED
        # Flag consumed so it doesn't snapshot on every subsequent poll.
        assert coordinator._force_snapshot_refresh is False

    def test_security_event_uses_dedicated_short_cooldown_debouncer(self) -> None:
        """A non-chime space event (#270) must re-read `security_state` through a
        dedicated short-cooldown debouncer, NOT the shared 10 s request-refresh
        debouncer. The default 10 s cooldown coalesces a rapid arm→disarm→arm
        burst into one trailing re-read that lags the alarm panel by up to ~10 s
        when FCM push doesn't deliver; the dedicated debouncer keeps the panel
        responsive while still setting `_force_snapshot_refresh` for group state.
        """
        from homeassistant.helpers.update_coordinator import REQUEST_REFRESH_DEFAULT_COOLDOWN

        from custom_components.aegis_ajax.const import SECURITY_EVENT_REFRESH_COOLDOWN

        # Far shorter than HA's shared request-refresh cooldown — that gap is the bug.
        assert SECURITY_EVENT_REFRESH_COOLDOWN < REQUEST_REFRESH_DEFAULT_COOLDOWN

        coordinator = _make_coordinator()
        coordinator.spaces["s1"] = _make_space("s1")
        coordinator._security_refresh_debouncer = MagicMock()
        coordinator.hass = MagicMock()

        # Non-chime byte (0x02) → security nudge branch, not the chime decode.
        coordinator._on_hts_space_event("hub-1", "deadbeef", 0x02)

        # Routed through the dedicated debouncer, and groups are forced to refresh.
        coordinator._security_refresh_debouncer.async_call.assert_called_once()
        assert coordinator._force_snapshot_refresh is True
        # The dedicated debouncer wraps an immediate refresh, never the shared
        # `async_request_refresh` whose 10 s cooldown caused the lag.
        coordinator.hass.async_create_task.assert_called_once()

    def test_request_security_snapshot_refresh_public_nudge(self) -> None:
        """The snapshot nudge must be callable on its own (#284/#287): the FCM
        event path needs the same forced-snapshot + dedicated-debouncer combo
        the HTS 0x08 handler uses, without faking an HTS event. A scenario /
        keypad / fob action can flip several groups at once plus
        `night_mode_enabled` — state only `get_space_snapshot` carries, which
        a push event alone never refreshes (wip3out3r's group panel lagged
        ~9 minutes on a scenario-driven arm).
        """
        coordinator = _make_coordinator()
        coordinator._security_refresh_debouncer = MagicMock()
        coordinator.hass = MagicMock()

        coordinator.request_security_snapshot_refresh()

        coordinator._security_refresh_debouncer.async_call.assert_called_once()
        assert coordinator._force_snapshot_refresh is True
        coordinator.hass.async_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_data_raises_update_failed_on_error(self) -> None:
        from homeassistant.helpers.update_coordinator import UpdateFailed

        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(side_effect=RuntimeError("API error"))

        with pytest.raises(UpdateFailed, match="Error fetching Ajax data"):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_hub_offline_24h_triggers_repair_and_clears_when_back_online(
        self,
    ) -> None:
        """A space sustained OFFLINE for >24h must raise the Repair, online clears it."""
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._spaces_api = MagicMock()
        offline_space = replace(_make_space("s1"), connection_status=ConnectionStatus.OFFLINE)
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[offline_space])
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        # Simulate a hub that's been offline for >24h by pre-seeding the
        # tracking dict 25h in the past.
        loop = asyncio.get_running_loop()
        coordinator._first_offline_at["s1"] = loop.time() - 25 * 3600

        with patch("custom_components.aegis_ajax.coordinator.async_register_hub_offline") as reg:
            await coordinator._async_update_data()

        reg.assert_called_once()
        kwargs = reg.call_args.kwargs
        assert kwargs["space_id"] == "s1"
        assert kwargs["hours_offline"] >= 24

        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        with patch("custom_components.aegis_ajax.coordinator.async_clear_hub_offline") as clr:
            await coordinator._async_update_data()

        clr.assert_called_once()
        assert clr.call_args.kwargs["space_id"] == "s1"
        assert "s1" not in coordinator._first_offline_at

    @pytest.mark.asyncio
    async def test_hub_offline_below_threshold_does_not_raise(self) -> None:
        """An offline hub under the 24h window must not surface a Repair."""
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._spaces_api = MagicMock()
        offline_space = replace(_make_space("s1"), connection_status=ConnectionStatus.OFFLINE)
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[offline_space])
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        with patch("custom_components.aegis_ajax.coordinator.async_register_hub_offline") as reg:
            await coordinator._async_update_data()

        reg.assert_not_called()
        assert "s1" in coordinator._first_offline_at

    def test_hts_chronic_failure_raised_after_30_min_window(self) -> None:
        """Sustained HTS reconnect failures must surface a Repair after 30 min."""
        import time as _time

        coordinator = _make_coordinator()
        coordinator._hts_first_failure_at = _time.monotonic() - 31 * 60
        coordinator.async_set_updated_data = MagicMock()

        with patch(
            "custom_components.aegis_ajax.coordinator.async_register_hts_chronic_failure"
        ) as reg:
            coordinator._handle_hts_disconnect(reconnect=False)

        reg.assert_called_once()
        assert reg.call_args.kwargs["space_id"] == "s1"
        assert reg.call_args.kwargs["minutes_failing"] >= 30

    def test_hts_first_disconnect_seeds_timestamp_without_repair(self) -> None:
        """The first HTS disconnect after a healthy run records the time but stays quiet."""
        coordinator = _make_coordinator()
        coordinator.async_set_updated_data = MagicMock()
        assert coordinator._hts_first_failure_at is None

        with patch(
            "custom_components.aegis_ajax.coordinator.async_register_hts_chronic_failure"
        ) as reg:
            coordinator._handle_hts_disconnect(reconnect=False)

        reg.assert_not_called()
        assert coordinator._hts_first_failure_at is not None

    def test_clear_hts_chronic_failure_resets_state_and_clears_repair(self) -> None:
        coordinator = _make_coordinator()
        coordinator._hts_first_failure_at = 12345.0

        with patch(
            "custom_components.aegis_ajax.coordinator.async_clear_hts_chronic_failure"
        ) as clr:
            coordinator._clear_hts_chronic_failure()

        assert coordinator._hts_first_failure_at is None
        clr.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_data_raises_auth_failed_when_login_invalid(self) -> None:
        """Bad credentials must raise ConfigEntryAuthFailed so HA shows reauth banner."""
        from homeassistant.exceptions import ConfigEntryAuthFailed

        from custom_components.aegis_ajax.api.session import AuthenticationError

        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = False
        coordinator._client.login = AsyncMock(side_effect=AuthenticationError("invalid"))

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_update_data_raises_auth_failed_when_token_rejected_and_relogin_invalid(
        self,
    ) -> None:
        """Stale token + invalid creds on retry must surface as ConfigEntryAuthFailed."""
        import grpc
        from homeassistant.exceptions import ConfigEntryAuthFailed

        from custom_components.aegis_ajax.api.session import AuthenticationError

        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True

        unauth = grpc.aio.AioRpcError(  # type: ignore[call-arg]
            code=grpc.StatusCode.UNAUTHENTICATED,
            initial_metadata=grpc.aio.Metadata(),
            trailing_metadata=grpc.aio.Metadata(),
            details="token expired",
        )
        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(side_effect=unauth)
        coordinator._client.login = AsyncMock(side_effect=AuthenticationError("revoked"))

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_update_data_raises_auth_failed_when_relogin_needs_2fa(self) -> None:
        """A fresh login that needs 2FA must open the reauth flow, not retry forever.

        `TwoFactorRequiredError` is not an `AuthenticationError` subclass, so
        without an explicit branch it fell through to the generic
        `UpdateFailed` handler. HA then retried setup indefinitely, and every
        retry asked Ajax for a new 2FA code — invalidating the code the user
        was typing into the reconfigure form.
        """
        from homeassistant.exceptions import ConfigEntryAuthFailed

        from custom_components.aegis_ajax.api.session import TwoFactorRequiredError

        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = False
        coordinator._client.login = AsyncMock(side_effect=TwoFactorRequiredError("req-1"))

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()
        coordinator._client.login.assert_awaited_once()
        assert coordinator.update_interval == timedelta(minutes=30)

    @pytest.mark.asyncio
    async def test_update_data_raises_auth_failed_when_token_rejected_and_relogin_needs_2fa(
        self,
    ) -> None:
        """The stale-token recovery path must also route 2FA to the reauth flow.

        This is the path a user hits after revoking sessions in the Ajax app:
        the stored token is rejected, the forced re-login needs a 2FA code.
        """
        import grpc
        from homeassistant.exceptions import ConfigEntryAuthFailed

        from custom_components.aegis_ajax.api.session import TwoFactorRequiredError

        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True

        unauth = grpc.aio.AioRpcError(  # type: ignore[call-arg]
            code=grpc.StatusCode.UNAUTHENTICATED,
            initial_metadata=grpc.aio.Metadata(),
            trailing_metadata=grpc.aio.Metadata(),
            details="User is not authenticated",
        )
        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(side_effect=unauth)
        coordinator._client.login = AsyncMock(side_effect=TwoFactorRequiredError("req-1"))

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()
        coordinator._client.login.assert_awaited_once()
        assert coordinator.update_interval == timedelta(minutes=30)

    @pytest.mark.asyncio
    async def test_login_persists_session_via_callback(self) -> None:
        """A successful login pushes the new token through on_session_persist."""
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = False
        coordinator._client.session.session_token = "tok-new"
        coordinator._client.session.user_hex_id = "hex-1"
        coordinator._client.login = AsyncMock()
        callback = MagicMock()
        coordinator._on_session_persist = callback
        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[])
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        await coordinator._async_update_data()

        coordinator._client.login.assert_awaited_once()
        callback.assert_called_once_with("tok-new", "hex-1")

    @pytest.mark.asyncio
    async def test_unauthenticated_error_triggers_relogin_and_retry(self) -> None:
        """Stale token rejected by Ajax → force fresh login, persist, retry."""
        import grpc

        class _UnauthenticatedError(Exception):
            def code(self) -> grpc.StatusCode:
                return grpc.StatusCode.UNAUTHENTICATED

        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True

        # First list_spaces raises UNAUTHENTICATED, second call returns []
        unauth_error = _UnauthenticatedError("session rejected")

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(side_effect=[unauth_error, []])
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])
        coordinator._client.session.clear_session = MagicMock()
        coordinator._client.login = AsyncMock()
        coordinator._client.session.session_token = "tok-fresh"
        coordinator._client.session.user_hex_id = "hex-1"
        callback = MagicMock()
        coordinator._on_session_persist = callback

        await coordinator._async_update_data()

        coordinator._client.session.clear_session.assert_called_once()
        coordinator._client.login.assert_awaited_once()
        callback.assert_called_once_with("tok-fresh", "hex-1")
        # list_spaces called twice (initial fail + retry)
        assert coordinator._spaces_api.list_spaces.await_count == 2

    @pytest.mark.asyncio
    async def test_async_shutdown_calls_client_close(self) -> None:
        coordinator = _make_coordinator(space_ids=[])
        coordinator._client.close = AsyncMock()

        await coordinator.async_shutdown()
        coordinator._client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_restarts_hts_when_previous_task_finished(self) -> None:
        net_state = HubNetworkState(ethernet_connected=True)
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True
        coordinator.hub_network = {"hub-1": net_state}
        coordinator._hts_task = MagicMock()
        coordinator._hts_task.done.return_value = True
        coordinator._start_hts = AsyncMock()

        space = _make_space("s1")
        device = _make_device("d1")
        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[space])
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[device])
        coordinator.async_set_updated_data = MagicMock()

        await coordinator._async_update_data()

        # Cached state survives the disconnect cycle (#146); restart is
        # triggered to refresh it with new deltas.
        assert coordinator.hub_network == {"hub-1": net_state}
        coordinator._start_hts.assert_awaited_once()
        coordinator.async_set_updated_data.assert_called_once()


class TestFallbackDeviceSnapshot:
    """The polled fallback must apply snapshots like the stream does (#403).

    `_maybe_fallback_device_snapshot` used to replace `self.devices`
    wholesale, bypassing every carry-forward `_handle_devices_snapshot`
    accumulated (#220 temperature, #339 tamper, #310 siren settings, #403
    readings and battery) and logging nothing — the same trap #406 hit:
    guarding one writer does not guard the others. These tests pin that the
    fallback routes through the same handler, and that it keeps the one
    behavior the stream path does not have: dropping devices the snapshot
    no longer reports.
    """

    def _make_fallback_coordinator(self) -> AjaxCobrandedCoordinator:  # noqa: F821
        coordinator = _make_coordinator()
        coordinator.spaces = {"s1": _make_space("s1")}
        coordinator._stream_tasks = []  # no live stream → fallback runs
        coordinator.async_set_updated_data = MagicMock()
        coordinator._devices_api = MagicMock()
        return coordinator

    @pytest.mark.asyncio
    async def test_fallback_snapshot_carries_forward_battery_and_readings(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator = self._make_fallback_coordinator()
        coordinator.devices["d1"] = replace(
            _make_device("d1", statuses={"signal_strength": 3}), battery=88
        )
        # The pathological snapshot from the report: same device, no
        # battery, no measurements.
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[_make_device("d1")])

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            await coordinator._maybe_fallback_device_snapshot()

        assert coordinator.devices["d1"].battery == 88
        assert coordinator.devices["d1"].statuses["signal_strength"] == 3
        # The wholesale replacement was also invisible — the fallback must
        # leave the same trace the stream path leaves.
        assert "Device snapshot applied" in caplog.text

    @pytest.mark.asyncio
    async def test_fallback_snapshot_still_drops_absent_devices(self) -> None:
        # The stream handler only ever adds or replaces; the fallback is the
        # one resync-from-scratch path, so a device deleted from Ajax while
        # no stream was alive must not survive it.
        coordinator = self._make_fallback_coordinator()
        coordinator.devices["d1"] = _make_device("d1")
        coordinator.devices["gone"] = _make_device("gone")
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[_make_device("d1")])

        await coordinator._maybe_fallback_device_snapshot()

        assert "d1" in coordinator.devices
        assert "gone" not in coordinator.devices

    @pytest.mark.asyncio
    async def test_fallback_is_a_noop_while_streams_are_alive(self) -> None:
        coordinator = self._make_fallback_coordinator()
        alive = MagicMock()
        alive.done.return_value = False
        coordinator._stream_tasks = [alive]
        coordinator._devices_api.get_devices_snapshot = AsyncMock()

        await coordinator._maybe_fallback_device_snapshot()

        coordinator._devices_api.get_devices_snapshot.assert_not_awaited()


class TestStreamHandlers:
    """Tests for coordinator stream callback handlers."""

    def _make_coordinator_with_stream(self) -> AjaxCobrandedCoordinator:  # noqa: F821
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        hass = MagicMock()
        client = MagicMock()
        with patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
            return_value=None,
        ):
            coordinator = AjaxCobrandedCoordinator(
                hass=hass, client=client, space_ids=["s1"], poll_interval=300
            )
        coordinator.hass = hass
        coordinator.entry_id = "entry-1"
        coordinator.async_set_updated_data = MagicMock()
        return coordinator

    def test_hub_registry_id_resolves_through_the_scoped_lookup(self) -> None:
        # #444: children link to the hub by registry id (`via_device_id`), so
        # entities need the hub's registry entry id at construction time.
        coordinator = self._make_coordinator_with_stream()
        device_reg = MagicMock()
        device_reg.async_get_device_by_identifier.return_value = MagicMock(id="reg-hub")

        with patch(
            "custom_components.aegis_ajax.coordinator.dr.async_get", return_value=device_reg
        ):
            assert coordinator.hub_registry_id("HUB7") == "reg-hub"

        device_reg.async_get_device_by_identifier.assert_called_once_with(
            ("aegis_ajax", "HUB7"), "entry-1"
        )

    def test_hub_registry_id_is_none_when_hub_not_registered(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        device_reg = MagicMock()
        device_reg.async_get_device_by_identifier.return_value = None

        with patch(
            "custom_components.aegis_ajax.coordinator.dr.async_get", return_value=device_reg
        ):
            assert coordinator.hub_registry_id("HUB7") is None

    def test_handle_devices_snapshot_populates_devices(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        device = _make_device("d1")
        coordinator._handle_devices_snapshot([device])
        assert "d1" in coordinator.devices
        coordinator.async_set_updated_data.assert_called_once()

    def test_handle_devices_snapshot_overwrites_existing(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")
        updated = _make_device("d1")
        coordinator._handle_devices_snapshot([updated])
        assert coordinator.devices["d1"] is updated

    # --- #403: measurements must survive a snapshot that omits them -----------
    #
    # A snapshot rebuilds each device, so a reading the stream does not repeat was
    # lost until that device next sent one. @wip3out3r measured 1/13 batteries and
    # 1/11 signal strengths still empty four hours on, while the only two devices
    # with an existing carry-forward kept their values the whole time.

    def test_snapshot_carries_battery_forward_when_it_omits_one(self) -> None:
        """Battery is the worst case: at 100% it has nothing to retransmit."""
        from custom_components.aegis_ajax.api.models import BatteryInfo

        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = replace(
            _make_device("d1"), battery=BatteryInfo(level=100, is_low=False)
        )

        coordinator._handle_devices_snapshot([_make_device("d1")])

        assert coordinator.devices["d1"].battery is not None
        assert coordinator.devices["d1"].battery.level == 100

    def test_snapshot_carries_signal_strength_forward(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1", statuses={"signal_strength": 3})

        coordinator._handle_devices_snapshot([_make_device("d1")])

        assert coordinator.devices["d1"].statuses["signal_strength"] == 3

    def test_temperature_is_in_the_carry_forward_list(self) -> None:
        """Records that the temperature exclusion was deliberately overturned.

        The exclusion pinned a worry that carrying temperature would "invent"
        one for families with no per-device temperature source. It cannot: the
        carry only fills a key already present in the existing statuses, so a
        device that never reported a temperature never gains one — pinned by
        `test_snapshot_does_not_invent_temperature_the_device_never_reported`.
        Meanwhile seven of the nine temperatures that emptied in #403 were
        light-stream families the siren-gated carry never covered.
        """
        from custom_components.aegis_ajax.coordinator import (
            _SNAPSHOT_CARRY_FORWARD_STATUS_KEYS,
        )

        assert "temperature" in _SNAPSHOT_CARRY_FORWARD_STATUS_KEYS

    def test_snapshot_carries_temperature_forward_for_light_stream_family(self) -> None:
        """A door_protect is not a siren: only the generic carry can reach it."""
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1", statuses={"temperature": 18.0})

        coordinator._handle_devices_snapshot([_make_device("d1")])

        assert coordinator.devices["d1"].statuses["temperature"] == 18.0

    def test_snapshot_does_not_invent_temperature_the_device_never_reported(self) -> None:
        """The half of the old exclusion worth keeping: a carry is not an invention.

        A family with no per-device temperature source never has one in its
        existing statuses, so there is nothing to fill and no device ends up
        holding a temperature it never reported.
        """
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1", statuses={"signal_strength": 3})

        coordinator._handle_devices_snapshot([_make_device("d1")])

        assert "temperature" not in coordinator.devices["d1"].statuses

    def test_snapshot_does_not_carry_an_operational_alert_forward(self) -> None:
        """The reason the carry list is named rather than a blanket merge.

        `lid_opened` is an alert, not a measurement: its absence from a fresh
        snapshot is the signal that it has cleared. Carrying it would pin a
        tamper alert on forever — the failure a "merge instead of replace" would
        have introduced while fixing the battery.
        """
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device(
            "d1", statuses={"lid_opened": True, "signal_strength": 3}
        )

        coordinator._handle_devices_snapshot([_make_device("d1")])

        assert "lid_opened" not in coordinator.devices["d1"].statuses
        # The measurement alongside it still survives, so this is the allowlist
        # discriminating rather than the carry-forward simply not running.
        assert coordinator.devices["d1"].statuses["signal_strength"] == 3

    # --- #419: deactivation must survive a degraded snapshot the hub disagrees with

    def _deactivated_device(self, device_id: str = "d1") -> Device:
        return _make_device(
            device_id,
            statuses={"temporary_deactivation_whole": True, "deactivated": True},
        )

    def _hub_says(
        self,
        coordinator: AjaxCobrandedCoordinator,  # noqa: F821
        device_id: str,
        *,
        deactivated: bool,
        age: float = 0.0,
    ) -> None:
        import time

        coordinator._hts_bypass_state[device_id] = (deactivated, time.monotonic() - age)

    def test_snapshot_clears_deactivation_without_hub_corroboration(self) -> None:
        """Pins the pre-#419 behavior for families that never report 0xB7:
        absence in the snapshot stays the clearing signal."""
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = self._deactivated_device()

        coordinator._handle_devices_snapshot([_make_device("d1")])

        assert "deactivated" not in coordinator.devices["d1"].statuses
        assert "temporary_deactivation_whole" not in coordinator.devices["d1"].statuses

    def test_snapshot_carries_deactivation_while_the_hub_reports_it_engaged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The #403 measurement: a degraded snapshot's silence, corroborated
        against the hub's own bypass report, is a degraded row — not a
        reactivation."""
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = self._deactivated_device()
        self._hub_says(coordinator, "d1", deactivated=True)

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._handle_devices_snapshot([_make_device("d1")])

        assert coordinator.devices["d1"].statuses["deactivated"] is True
        assert coordinator.devices["d1"].statuses["temporary_deactivation_whole"] is True
        assert "d1" in coordinator._hts_carried_deactivation_ids
        assert "Deactivation state carried across snapshot" in caplog.text

    def test_snapshot_clears_deactivation_when_the_hub_reports_it_lifted(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = self._deactivated_device()
        self._hub_says(coordinator, "d1", deactivated=False)

        coordinator._handle_devices_snapshot([_make_device("d1")])

        assert "deactivated" not in coordinator.devices["d1"].statuses
        assert "d1" not in coordinator._hts_carried_deactivation_ids

    def test_a_stale_hub_report_does_not_corroborate(self) -> None:
        """Beyond the trust window the hub's word is too old to overrule a
        snapshot — HTS may have been down long enough for the world to move."""
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = self._deactivated_device()
        self._hub_says(coordinator, "d1", deactivated=True, age=16 * 60)

        coordinator._handle_devices_snapshot([_make_device("d1")])

        assert "deactivated" not in coordinator.devices["d1"].statuses

    def test_deactivation_in_the_snapshot_wins_over_the_carry(self) -> None:
        """A snapshot that itself reports deactivation is gRPC-fresh: applied
        as-is and the carried-marker dropped, so the withdraw path can never
        touch state the server vouched for."""
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = self._deactivated_device()
        coordinator._hts_carried_deactivation_ids.add("d1")
        self._hub_says(coordinator, "d1", deactivated=True)

        coordinator._handle_devices_snapshot([self._deactivated_device()])

        assert coordinator.devices["d1"].statuses["deactivated"] is True
        assert "d1" not in coordinator._hts_carried_deactivation_ids

    def test_carry_does_not_invent_deactivation(self) -> None:
        """0xB7=01 for a device the model shows as protecting stores the
        observation and nothing else — the carry preserves, never creates."""
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")
        self._hub_says(coordinator, "d1", deactivated=True)

        coordinator._handle_devices_snapshot([_make_device("d1")])

        assert "deactivated" not in coordinator.devices["d1"].statuses
        assert "d1" not in coordinator._hts_carried_deactivation_ids

    def test_bypassed_field_is_carried_with_the_corroboration(self) -> None:
        """`is_device_deactivated` reads `bypassed` as an independent source,
        so the carry must preserve it too."""
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = replace(_make_device("d1"), bypassed=True)
        self._hub_says(coordinator, "d1", deactivated=True)

        coordinator._handle_devices_snapshot([_make_device("d1")])

        assert coordinator.devices["d1"].bypassed is True

    def test_a_value_in_the_snapshot_wins_over_the_carried_one(self) -> None:
        """Carry-forward only fills gaps, so #312's live tracking still holds.

        Uses `signal_strength` rather than `temperature` on purpose: temperature
        is not in the carry list, so this would pass without exercising anything —
        which is how it was first written, and a mutation that let a carried value
        override a fresh one did not fail it. Also asserts the battery, since a
        stale battery overriding a fresh reading is the same defect on the field
        that has its own carry.
        """
        from custom_components.aegis_ajax.api.models import BatteryInfo

        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = replace(
            _make_device("d1", statuses={"signal_strength": 1}),
            battery=BatteryInfo(level=10, is_low=True),
        )

        coordinator._handle_devices_snapshot(
            [
                replace(
                    _make_device("d1", statuses={"signal_strength": 3}),
                    battery=BatteryInfo(level=95, is_low=False),
                )
            ]
        )

        assert coordinator.devices["d1"].statuses["signal_strength"] == 3
        assert coordinator.devices["d1"].battery is not None
        assert coordinator.devices["d1"].battery.level == 95

    def test_snapshot_logs_what_it_replaced_and_carried(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The observable this path did not have at any log level (#403).

        A full snapshot could replace every device and empty a fleet of readings
        without leaving a trace, so the cause of the *invocation* could not be
        investigated. Reported by @wip3out3r, who read the source after failing
        to find the line I had asked him for.
        """
        from custom_components.aegis_ajax.api.models import BatteryInfo

        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = replace(
            _make_device("d1", statuses={"signal_strength": 3}),
            battery=BatteryInfo(level=100, is_low=False),
        )

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._handle_devices_snapshot([_make_device("d1")])

        assert "Device snapshot applied: 1 device(s) replaced" in caplog.text
        assert "carried forward 1 reading(s) and 1 battery value(s)" in caplog.text

    @staticmethod
    def _make_doorbell(device_id: str, device_type: str, name: str = "Deurbel") -> Device:
        return Device(
            id=device_id,
            hub_id="hub-1",
            name=name,
            device_type=device_type,
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=1 if device_type.startswith("motion_cam_video") else 0,
            bypassed=False,
            statuses={},
            battery=None,
        )

    def test_snapshot_evicts_cached_ghost_when_sibling_arrives(self) -> None:
        """#173 — a motion_cam_video ghost warm-started from cache is dropped
        once the video_edge sibling arrives in a later snapshot, and removed
        from the device registry so its card disappears."""
        coordinator = self._make_coordinator_with_stream()
        # Ghost was warm-started from the cache; sibling not present yet.
        coordinator.devices["310A8DF4"] = self._make_doorbell(
            "310A8DF4", "motion_cam_video_doorbell"
        )

        reg_device = MagicMock()
        reg_device.id = "reg-ghost"
        device_reg = MagicMock()
        device_reg.async_get_device_by_identifier.return_value = reg_device

        sibling = self._make_doorbell("9c756e2bca39-0", "video_edge_doorbell")
        with patch(
            "custom_components.aegis_ajax.coordinator.dr.async_get", return_value=device_reg
        ):
            coordinator._handle_devices_snapshot([sibling])

        assert "310A8DF4" not in coordinator.devices
        assert "9c756e2bca39-0" in coordinator.devices
        device_reg.async_remove_device.assert_called_once_with("reg-ghost")
        # #444: the lookup is scoped to our config entry, never the deprecated
        # cross-entry `async_get_device(identifiers=...)`.
        device_reg.async_get_device_by_identifier.assert_called_once_with(
            ("aegis_ajax", "310A8DF4"), "entry-1"
        )
        device_reg.async_get_device.assert_not_called()

    def test_snapshot_keeps_ghost_when_no_sibling(self) -> None:
        """Unbalanced #119 case: only the hub_device twin exists — keep it."""
        coordinator = self._make_coordinator_with_stream()
        ghost = self._make_doorbell("310A8DF4", "motion_cam_video_doorbell")

        with patch("custom_components.aegis_ajax.coordinator.dr.async_get") as mock_get:
            coordinator._handle_devices_snapshot([ghost])

        assert "310A8DF4" in coordinator.devices
        mock_get.assert_not_called()

    def test_snapshot_skips_registry_when_ghost_not_registered(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["310A8DF4"] = self._make_doorbell(
            "310A8DF4", "motion_cam_video_doorbell"
        )
        device_reg = MagicMock()
        device_reg.async_get_device_by_identifier.return_value = None

        sibling = self._make_doorbell("9c756e2bca39-0", "video_edge_doorbell")
        with patch(
            "custom_components.aegis_ajax.coordinator.dr.async_get", return_value=device_reg
        ):
            coordinator._handle_devices_snapshot([sibling])

        assert "310A8DF4" not in coordinator.devices
        device_reg.async_remove_device.assert_not_called()

    def test_handle_device_removed_evicts_device_registry_and_cache(self) -> None:
        """#422 — the stream's explicit REMOVE (a device deleted in the Ajax
        app) deletes the device everywhere: coordinator state, HA device
        registry, warm-start cache."""
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")
        coordinator._hts_carried_deactivation_ids.add("d1")
        coordinator._devices_cache = MagicMock()

        reg_device = MagicMock()
        reg_device.id = "reg-d1"
        device_reg = MagicMock()
        device_reg.async_get_device_by_identifier.return_value = reg_device

        with patch(
            "custom_components.aegis_ajax.coordinator.dr.async_get", return_value=device_reg
        ):
            coordinator._handle_device_removed("d1")

        assert "d1" not in coordinator.devices
        assert "d1" not in coordinator._hts_carried_deactivation_ids
        device_reg.async_remove_device.assert_called_once_with("reg-d1")
        coordinator.async_set_updated_data.assert_called_once()
        coordinator._devices_cache.async_schedule_save.assert_called_once()

    def test_handle_device_removed_unknown_id_is_quiet(self) -> None:
        """A REMOVE for a device we never tracked (and with no registry
        entry) must not notify listeners or rewrite the cache."""
        coordinator = self._make_coordinator_with_stream()
        coordinator._devices_cache = MagicMock()
        device_reg = MagicMock()
        device_reg.async_get_device_by_identifier.return_value = None

        with patch(
            "custom_components.aegis_ajax.coordinator.dr.async_get", return_value=device_reg
        ):
            coordinator._handle_device_removed("never-seen")

        device_reg.async_remove_device.assert_not_called()
        coordinator.async_set_updated_data.assert_not_called()
        coordinator._devices_cache.async_schedule_save.assert_not_called()

    def test_complete_snapshot_drops_absent_devices_of_its_hub(self) -> None:
        """#422 — a complete per-space snapshot is authoritative for
        membership: devices of that hub missing from the list are dropped
        from tracking. The hub itself and other hubs' devices are untouched,
        and the registry entry is deliberately NOT auto-removed — absence is
        weaker evidence than the explicit REMOVE (#419, #383)."""
        coordinator = self._make_coordinator_with_stream()
        coordinator.spaces = {"s1": _make_space("s1")}  # hub-1
        present = _make_device("d1")
        stale = _make_device("d2")
        hub = replace(_make_device("hub-1"), device_type="hub_2")
        other_hub_device = replace(_make_device("d9"), hub_id="hub-2")
        for device in (present, stale, hub, other_hub_device):
            coordinator.devices[device.id] = device
        coordinator._hts_carried_deactivation_ids.add("d2")

        device_reg = MagicMock()
        with patch(
            "custom_components.aegis_ajax.coordinator.dr.async_get", return_value=device_reg
        ):
            # The hub is deliberately absent from the list too — it must
            # survive on its device_type guard, whatever the list says.
            coordinator._handle_devices_snapshot([present], complete_for_space="s1")

        assert set(coordinator.devices) == {"d1", "hub-1", "d9"}
        assert "d2" not in coordinator._hts_carried_deactivation_ids
        device_reg.async_remove_device.assert_not_called()

    def test_complete_snapshot_for_unknown_space_drops_nothing(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d2"] = _make_device("d2")

        coordinator._handle_devices_snapshot([_make_device("d1")], complete_for_space="ghost-space")

        assert set(coordinator.devices) == {"d1", "d2"}

    def test_single_device_snapshot_never_drops_others(self) -> None:
        """A `snapshot_update` refresh of one device (complete_for_space
        unset) must never resync membership."""
        coordinator = self._make_coordinator_with_stream()
        coordinator.spaces = {"s1": _make_space("s1")}
        coordinator.devices["d2"] = _make_device("d2")

        coordinator._handle_devices_snapshot([_make_device("d1")])

        assert set(coordinator.devices) == {"d1", "d2"}

    def test_handle_status_update_add_sets_status_true(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update("d1", "door_opened", {"op": 1})

        assert coordinator.devices["d1"].statuses.get("door_opened") is True
        coordinator.async_set_updated_data.assert_called_once()

    def test_handle_status_update_remove_deletes_status(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        device = Device(
            id="d1",
            hub_id="hub-1",
            name="Sensor",
            device_type="door_protect",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={"door_opened": True},
            battery=None,
        )
        coordinator.devices["d1"] = device

        coordinator._handle_status_update("d1", "door_opened", {"op": 3})

        assert "door_opened" not in coordinator.devices["d1"].statuses
        coordinator.async_set_updated_data.assert_called_once()

    def test_handle_status_update_co_level_maps_to_co_detected(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update("d1", "co_level_detected", {"op": 1})

        assert coordinator.devices["d1"].statuses.get("co_detected") is True

    def test_handle_status_update_high_temp_maps_correctly(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update("d1", "high_temperature_detected", {"op": 1})

        assert coordinator.devices["d1"].statuses.get("high_temperature") is True

    def test_handle_status_update_lid_opened_folds_into_tamper(self) -> None:
        # #339: granular case-tampering deltas must drive the shared `tamper`
        # key the per-device tamper sensor binds to.
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update("d1", "lid_opened", {"op": 1})

        statuses = coordinator.devices["d1"].statuses
        assert statuses.get("lid_opened") is True
        assert statuses.get("tamper") is True

    def test_handle_status_update_smart_bracket_folds_into_tamper(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update("d1", "smart_bracket_unlocked", {"op": 1})

        assert coordinator.devices["d1"].statuses.get("tamper") is True

    def test_handle_status_update_tamper_clears_on_remove(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update("d1", "lid_opened", {"op": 1})
        coordinator._handle_status_update("d1", "lid_opened", {"op": 3})

        statuses = coordinator.devices["d1"].statuses
        assert "lid_opened" not in statuses
        assert "tamper" not in statuses

    def test_handle_status_update_tamper_survives_remove_while_other_source_active(self) -> None:
        # Lid still open while the bracket delta clears → tamper must hold.
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update("d1", "lid_opened", {"op": 1})
        coordinator._handle_status_update("d1", "smart_bracket_unlocked", {"op": 1})
        coordinator._handle_status_update("d1", "smart_bracket_unlocked", {"op": 3})

        statuses = coordinator.devices["d1"].statuses
        assert statuses.get("tamper") is True
        assert "smart_bracket_unlocked" not in statuses

    def test_handle_status_update_door_opened_does_not_set_tamper(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update("d1", "door_opened", {"op": 1})

        assert "tamper" not in coordinator.devices["d1"].statuses

    def test_handle_status_update_deactivation_folds_into_deactivated(self) -> None:
        # #338: the realtime path must reach the same shared key as the
        # snapshot parser, or a device deactivated while HA is running keeps
        # reporting live protection until the next full snapshot.
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update("d1", "temporary_deactivation_whole", {"op": 1})

        statuses = coordinator.devices["d1"].statuses
        assert statuses.get("temporary_deactivation_whole") is True
        assert statuses.get("deactivated") is True

    def test_handle_status_update_deactivation_clears_on_remove(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update("d1", "one_time_deactivation_whole", {"op": 1})
        coordinator._handle_status_update("d1", "one_time_deactivation_whole", {"op": 3})

        statuses = coordinator.devices["d1"].statuses
        assert "one_time_deactivation_whole" not in statuses
        assert "deactivated" not in statuses

    def test_handle_status_update_deactivation_holds_while_other_kind_active(self) -> None:
        # Tamper protection re-enabled while the whole-device deactivation
        # stands → the switch must stay on.
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update("d1", "temporary_deactivation_whole", {"op": 1})
        coordinator._handle_status_update("d1", "temporary_deactivation_tamper", {"op": 1})
        coordinator._handle_status_update("d1", "temporary_deactivation_tamper", {"op": 3})

        statuses = coordinator.devices["d1"].statuses
        assert statuses.get("deactivated") is True

    def test_handle_status_update_deactivation_tamper_is_not_a_tamper_alarm(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update("d1", "temporary_deactivation_tamper", {"op": 1})

        assert "tamper" not in coordinator.devices["d1"].statuses

    def test_handle_status_update_temperature_preserves_numeric_value(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update("d1", "temperature", {"op": 2, "value": 19})

        assert coordinator.devices["d1"].statuses.get("temperature") == 19
        assert coordinator.devices["d1"].statuses.get("temperature") is not True

    def test_handle_status_update_life_quality_updates_temperature_humidity_and_co2(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update(
            "d1",
            "life_quality",
            {"op": 2, "values": {"temperature": 21, "humidity": 58, "co2": 742}},
        )

        assert coordinator.devices["d1"].statuses.get("temperature") == 21
        assert coordinator.devices["d1"].statuses.get("humidity") == 58
        assert coordinator.devices["d1"].statuses.get("co2") == 742

    def test_handle_status_update_case_drilling_maps_correctly(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update("d1", "case_drilling_detected", {"op": 1})

        assert coordinator.devices["d1"].statuses.get("case_drilling") is True

    def test_handle_status_update_anti_masking_maps_correctly(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update("d1", "anti_masking_alert", {"op": 1})

        assert coordinator.devices["d1"].statuses.get("anti_masking") is True

    def test_handle_status_update_interference_maps_correctly(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update("d1", "interference_detected", {"op": 1})

        assert coordinator.devices["d1"].statuses.get("interference") is True

    def test_handle_status_update_wire_input_alert_true(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update(
            "d1",
            "wire_input_status",
            {"op": 2, "is_alert": True, "alarm_type": "intrusion"},
        )

        assert coordinator.devices["d1"].statuses.get("wire_input_alert") is True
        assert coordinator.devices["d1"].statuses.get("wire_input_alarm_type") == "intrusion"

    def test_handle_status_update_wire_input_alert_false_clears(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        device = Device(
            id="d1",
            hub_id="hub-1",
            name="Sensor",
            device_type="wire_input_mt",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={"wire_input_alert": True, "wire_input_alarm_type": "intrusion"},
            battery=None,
        )
        coordinator.devices["d1"] = device

        coordinator._handle_status_update(
            "d1",
            "wire_input_status",
            {"op": 2, "is_alert": False, "alarm_type": "intrusion"},
        )

        assert coordinator.devices["d1"].statuses.get("wire_input_alert") is False
        # alarm_type stays — same wire input, just cleared its alarm
        assert coordinator.devices["d1"].statuses.get("wire_input_alarm_type") == "intrusion"

    def test_handle_status_update_wire_input_remove_drops_both_keys(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        device = Device(
            id="d1",
            hub_id="hub-1",
            name="Sensor",
            device_type="wire_input_mt",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={"wire_input_alert": True, "wire_input_alarm_type": "intrusion"},
            battery=None,
        )
        coordinator.devices["d1"] = device

        coordinator._handle_status_update("d1", "wire_input_status", {"op": 3})

        assert "wire_input_alert" not in coordinator.devices["d1"].statuses
        assert "wire_input_alarm_type" not in coordinator.devices["d1"].statuses

    def test_handle_status_update_transmitter_status_alert_writes_wire_input_alert(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.devices["d1"] = _make_device("d1")

        coordinator._handle_status_update(
            "d1",
            "transmitter_status",
            {"op": 2, "is_alert": True, "alarm_type": "intrusion"},
        )

        statuses = coordinator.devices["d1"].statuses
        assert statuses.get("wire_input_alert") is True
        assert statuses.get("wire_input_alarm_type") == "intrusion"

    def test_handle_status_update_transmitter_status_clear_writes_false(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        device = Device(
            id="d1",
            hub_id="hub-1",
            name="Transmitter",
            device_type="transmitter",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={"wire_input_alert": True, "wire_input_alarm_type": "intrusion"},
            battery=None,
        )
        coordinator.devices["d1"] = device

        coordinator._handle_status_update("d1", "transmitter_status", {"op": 2, "is_alert": False})

        assert coordinator.devices["d1"].statuses.get("wire_input_alert") is False

    def test_handle_status_update_transmitter_status_remove_drops_both_keys(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        device = Device(
            id="d1",
            hub_id="hub-1",
            name="Transmitter",
            device_type="transmitter",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={"wire_input_alert": True, "wire_input_alarm_type": "intrusion"},
            battery=None,
        )
        coordinator.devices["d1"] = device

        coordinator._handle_status_update("d1", "transmitter_status", {"op": 3})

        statuses = coordinator.devices["d1"].statuses
        assert "wire_input_alert" not in statuses
        assert "wire_input_alarm_type" not in statuses

    def test_handle_status_update_life_quality_remove_drops_all_sub_keys(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        device = Device(
            id="d1",
            hub_id="hub-1",
            name="Life Quality",
            device_type="life_quality",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={"temperature": 21, "humidity": 58, "co2": 742},
            battery=None,
        )
        coordinator.devices["d1"] = device

        coordinator._handle_status_update("d1", "life_quality", {"op": 3})

        statuses = coordinator.devices["d1"].statuses
        assert "temperature" not in statuses
        assert "humidity" not in statuses
        assert "co2" not in statuses

    def test_handle_status_update_gsm_status_remove_drops_all_sub_keys(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        device = Device(
            id="d1",
            hub_id="hub-1",
            name="Hub",
            device_type="hub_two_4g",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={"mobile_network_type": "4G", "gsm_connected": True},
            battery=None,
        )
        coordinator.devices["d1"] = device

        coordinator._handle_status_update("d1", "gsm_status", {"op": 3})

        statuses = coordinator.devices["d1"].statuses
        assert "mobile_network_type" not in statuses
        assert "gsm_connected" not in statuses

    def test_handle_status_update_motion_remove_drops_detected_at(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        device = Device(
            id="d1",
            hub_id="hub-1",
            name="Motion",
            device_type="motion_protect",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={"motion_detected": True, "motion_detected_at": 1700000000},
            battery=None,
        )
        coordinator.devices["d1"] = device

        coordinator._handle_status_update("d1", "motion_detected", {"op": 3})

        statuses = coordinator.devices["d1"].statuses
        assert "motion_detected" not in statuses
        assert "motion_detected_at" not in statuses

    def test_handle_status_update_unknown_device_is_ignored(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        # No devices in coordinator
        coordinator._handle_status_update("nonexistent", "door_opened", {"op": 1})
        coordinator.async_set_updated_data.assert_not_called()

    def test_handle_hts_disconnect_preserves_hub_network(self) -> None:
        """#146 — hub_network entries survive transient HTS dropouts.

        Only the live HTS client is torn down; `is_hts_alive` becomes
        False so the `mains_power` alert flips to unavailable, but all
        diagnostic hub-network sensors keep rendering their cached
        value until the next STATUS_BODY refreshes them on reconnect.
        """
        state = HubNetworkState(ethernet_connected=True)
        coordinator = self._make_coordinator_with_stream()
        coordinator.hub_network["hub-1"] = state

        coordinator._handle_hts_disconnect()

        assert coordinator.hub_network == {"hub-1": state}
        assert coordinator.is_hts_alive is False
        coordinator.async_set_updated_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_hts_does_not_block_on_connect(self) -> None:
        # Regression for #112 — `_start_hts()` used to `await connect()`
        # before returning, extending HA's first-refresh past the boot
        # threshold. The refactored version creates a background task
        # for connect+listen and returns immediately.
        coordinator = self._make_coordinator_with_stream()
        coordinator._client.session.session_token = "abcdef"
        coordinator._client.session.user_hex_id = "00112233"
        coordinator._client.session.device_id = "device-1"
        coordinator._client.session.app_label = "Ajax"

        slow_connect_started = asyncio.Event()
        slow_connect_release = asyncio.Event()

        async def _slow_connect(self: object) -> object:
            slow_connect_started.set()
            await slow_connect_release.wait()
            return MagicMock(hubs=[])

        from custom_components.aegis_ajax.api.hts.client import HtsClient

        with (
            patch.object(HtsClient, "_ssl_ctx", create=True, new=object()),
            patch.object(HtsClient, "connect", new=_slow_connect),
            patch.object(HtsClient, "listen", new=AsyncMock()),
        ):
            await asyncio.wait_for(coordinator._start_hts(), timeout=1.0)
            assert coordinator._hts_task is not None
            await asyncio.wait_for(slow_connect_started.wait(), timeout=1.0)
            slow_connect_release.set()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(coordinator._hts_task, timeout=2.0)

    @pytest.mark.asyncio
    async def test_start_hts_is_idempotent_when_task_already_running(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator._client.session.session_token = "abcdef"

        existing_task = MagicMock(spec=asyncio.Task)
        existing_task.done.return_value = False
        coordinator._hts_task = existing_task

        await coordinator._start_hts()

        assert coordinator._hts_task is existing_task

    @pytest.mark.asyncio
    async def test_start_hts_logs_warning_when_session_token_missing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Regression for #111 — affected users reported "HTS streams: 0/1"
        # with empty `notification.py` and `api/hts/client.py` logs even
        # under DEBUG. The silent skip when the session token is missing
        # is the most common cause; promote it to WARNING so the reason
        # is visible at default log level.
        coordinator = self._make_coordinator_with_stream()
        coordinator._client.session.session_token = ""

        with caplog.at_level("WARNING"):
            await coordinator._start_hts()

        assert "HTS startup skipped" in caplog.text
        assert "no Ajax session token" in caplog.text
        assert coordinator._hts_task is None

    @pytest.mark.asyncio
    async def test_run_hts_lifecycle_logs_warning_on_connect_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Regression for #111 — `connect()` failures used to be DEBUG only,
        # so HTS connection collapses were invisible in the user's log.
        # Now WARNING with the exception class name + traceback (under
        # DEBUG) for fast triage.
        coordinator = self._make_coordinator_with_stream()
        coordinator._hts_client = MagicMock()
        coordinator._hts_client.connect = AsyncMock(side_effect=ConnectionRefusedError("refused"))

        with caplog.at_level("WARNING"):
            await coordinator._run_hts_lifecycle()

        assert "HTS connection failed" in caplog.text
        assert "ConnectionRefusedError" in caplog.text
        assert coordinator._hts_client is None

    @pytest.mark.asyncio
    async def test_run_hts_lifecycle_seeds_last_known_hub_states(self) -> None:
        # #323 — the last-known hub state must be seeded into the client
        # before connect() so a reconnect doesn't reset fields (most
        # visibly externally_powered) to their dataclass defaults. The
        # unit tests for HtsClient call seed_hub_states directly, so this
        # asserts the coordinator actually wires it into the lifecycle.
        state = HubNetworkState(externally_powered=True)
        coordinator = self._make_coordinator_with_stream()
        coordinator.hub_network = {"hub-1": state}
        client = MagicMock()
        client.seed_hub_states = MagicMock()
        # connect() raising is fine — seeding happens first, and it lets us
        # avoid driving the full listen loop.
        client.connect = AsyncMock(side_effect=ConnectionRefusedError("stop after seed"))
        coordinator._hts_client = client

        await coordinator._run_hts_lifecycle()

        client.seed_hub_states.assert_called_once_with({"hub-1": state})

    @pytest.mark.asyncio
    async def test_run_hts_lifecycle_skips_seed_when_no_hub_network(self) -> None:
        # #323 — with no cached hub state there is nothing to preserve, so
        # seeding is skipped (a fresh client starts from defaults).
        coordinator = self._make_coordinator_with_stream()
        coordinator.hub_network = {}
        client = MagicMock()
        client.seed_hub_states = MagicMock()
        client.connect = AsyncMock(side_effect=ConnectionRefusedError("stop after seed"))
        coordinator._hts_client = client

        await coordinator._run_hts_lifecycle()

        client.seed_hub_states.assert_not_called()

    def test_handle_hts_task_done_drops_client_and_broadcasts(self) -> None:
        """Task-done routes through `_handle_hts_disconnect` (#146).

        Cached hub_network entries stay intact — only the live client
        is torn down and a broadcast fires so `is_hts_alive`-aware
        sensors (e.g. `mains_power`) re-evaluate their availability.
        """
        state = HubNetworkState(ethernet_connected=True)
        coordinator = self._make_coordinator_with_stream()
        coordinator.hub_network["hub-1"] = state
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = False
        task.result.return_value = None

        coordinator._handle_hts_task_done(task)

        assert coordinator.hub_network == {"hub-1": state}
        assert coordinator.is_hts_alive is False
        coordinator.async_set_updated_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_stream_tasks(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator._client.close = AsyncMock()

        # Create a real task that runs forever
        async def _forever() -> None:
            await asyncio.sleep(9999)

        task = asyncio.create_task(_forever())
        coordinator._stream_tasks.append(task)

        await coordinator.async_shutdown()

        assert task.cancelled()
        coordinator._client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_first_update_starts_streams(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator._client.session.is_authenticated = True

        space = _make_space("s1")
        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[space])
        coordinator.spaces = {"s1": space}

        mock_task = MagicMock(spec=asyncio.Task)
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])
        coordinator._devices_api.start_device_stream = AsyncMock(return_value=mock_task)

        result = await coordinator._async_update_data()

        coordinator._devices_api.start_device_stream.assert_called_once()
        call = coordinator._devices_api.start_device_stream.call_args
        assert call.args == ("s1",)
        # The snapshot handler is a per-space closure that threads the
        # `complete` flag into the membership resync (#422).
        assert callable(call.kwargs["on_devices_snapshot"])
        assert call.kwargs["on_status_update"] == coordinator._handle_status_update
        assert call.kwargs["on_device_removed"] == coordinator._handle_device_removed
        coordinator._handle_devices_snapshot = MagicMock()
        call.kwargs["on_devices_snapshot"]([], complete=True)
        coordinator._handle_devices_snapshot.assert_called_with([], complete_for_space="s1")
        call.kwargs["on_devices_snapshot"]([])
        coordinator._handle_devices_snapshot.assert_called_with([], complete_for_space=None)
        assert coordinator._streams_started is True
        assert mock_task in coordinator._stream_tasks
        assert "spaces" in result

    @pytest.mark.asyncio
    async def test_second_update_does_not_restart_streams(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True  # already started

        space = _make_space("s1")
        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[space])
        coordinator.spaces = {"s1": space}

        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        await coordinator._async_update_data()

        coordinator._devices_api.start_device_stream = MagicMock()
        coordinator._devices_api.start_device_stream.assert_not_called()


class TestApplyPushSecurityState:
    """Direct security_state updates from FCM arm/disarm pushes (#68)."""

    def _make_coordinator_with_space(
        self, security_state: SecurityState = SecurityState.DISARMED
    ) -> AjaxCobrandedCoordinator:  # noqa: F821
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        hass = MagicMock()
        client = MagicMock()
        with patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
            return_value=None,
        ):
            coordinator = AjaxCobrandedCoordinator(
                hass=hass, client=client, space_ids=["s1"], poll_interval=300
            )
        coordinator.hass = hass
        coordinator.async_set_updated_data = MagicMock()
        coordinator.spaces = {
            "s1": Space(
                id="s1",
                hub_id="hub-1",
                name="Home",
                security_state=security_state,
                connection_status=ConnectionStatus.ONLINE,
                malfunctions_count=0,
            )
        }
        return coordinator

    def test_arm_push_updates_security_state(self) -> None:
        coordinator = self._make_coordinator_with_space(SecurityState.DISARMED)

        coordinator.apply_push_security_state("s1", SecurityState.ARMED)

        assert coordinator.spaces["s1"].security_state == SecurityState.ARMED
        coordinator.async_set_updated_data.assert_called_once()

    def test_night_mode_push_updates_security_state(self) -> None:
        coordinator = self._make_coordinator_with_space(SecurityState.DISARMED)

        coordinator.apply_push_security_state("s1", SecurityState.NIGHT_MODE)

        assert coordinator.spaces["s1"].security_state == SecurityState.NIGHT_MODE

    def test_disarm_push_updates_security_state(self) -> None:
        coordinator = self._make_coordinator_with_space(SecurityState.ARMED)

        coordinator.apply_push_security_state("s1", SecurityState.DISARMED)

        assert coordinator.spaces["s1"].security_state == SecurityState.DISARMED

    def test_no_change_skips_update(self) -> None:
        coordinator = self._make_coordinator_with_space(SecurityState.ARMED)

        coordinator.apply_push_security_state("s1", SecurityState.ARMED)

        coordinator.async_set_updated_data.assert_not_called()

    def test_unknown_space_no_op(self) -> None:
        coordinator = self._make_coordinator_with_space(SecurityState.DISARMED)

        coordinator.apply_push_security_state("unknown", SecurityState.ARMED)

        # Original space untouched, no update fired
        assert coordinator.spaces["s1"].security_state == SecurityState.DISARMED
        coordinator.async_set_updated_data.assert_not_called()

    def test_active_optimistic_state_is_respected(self) -> None:
        # Local arm-from-HA registers an optimistic state. A contradictory
        # push arriving before its 10s expiry must not flip the panel back.
        import time

        coordinator = self._make_coordinator_with_space(SecurityState.ARMED)
        future = time.monotonic() + 60
        coordinator._optimistic_space_states["s1"] = (future, SecurityState.ARMED)

        coordinator.apply_push_security_state("s1", SecurityState.DISARMED)

        assert coordinator.spaces["s1"].security_state == SecurityState.ARMED
        coordinator.async_set_updated_data.assert_not_called()

    def test_expired_optimistic_state_does_not_block(self) -> None:
        import time

        coordinator = self._make_coordinator_with_space(SecurityState.DISARMED)
        past = time.monotonic() - 60
        coordinator._optimistic_space_states["s1"] = (past, SecurityState.ARMED)

        coordinator.apply_push_security_state("s1", SecurityState.ARMED)

        assert coordinator.spaces["s1"].security_state == SecurityState.ARMED
        coordinator.async_set_updated_data.assert_called_once()

    def test_night_mode_push_sets_night_mode_enabled(self) -> None:
        # The push is the only real-time night-mode signal in group mode —
        # the debounced lite re-read that follows reports PARTIALLY_ARMED,
        # so the flag must flip here for the panel to keep `armed_night` (#284).
        coordinator = self._make_coordinator_with_space(SecurityState.DISARMED)

        coordinator.apply_push_security_state("s1", SecurityState.NIGHT_MODE)

        assert coordinator.spaces["s1"].night_mode_enabled is True

    def test_disarm_push_clears_night_mode_enabled(self) -> None:
        from dataclasses import replace as dc_replace

        coordinator = self._make_coordinator_with_space(SecurityState.NIGHT_MODE)
        coordinator.spaces["s1"] = dc_replace(coordinator.spaces["s1"], night_mode_enabled=True)

        coordinator.apply_push_security_state("s1", SecurityState.DISARMED)

        assert coordinator.spaces["s1"].night_mode_enabled is False

    def test_full_arm_push_clears_night_mode_enabled(self) -> None:
        from dataclasses import replace as dc_replace

        coordinator = self._make_coordinator_with_space(SecurityState.NIGHT_MODE)
        coordinator.spaces["s1"] = dc_replace(coordinator.spaces["s1"], night_mode_enabled=True)

        coordinator.apply_push_security_state("s1", SecurityState.ARMED)

        assert coordinator.spaces["s1"].night_mode_enabled is False

    def test_partial_push_keeps_night_mode_enabled(self) -> None:
        # PARTIALLY_ARMED is ambiguous (night mode vs subset of groups armed),
        # so it must not clobber a known night-mode flag.
        from dataclasses import replace as dc_replace

        coordinator = self._make_coordinator_with_space(SecurityState.NIGHT_MODE)
        coordinator.spaces["s1"] = dc_replace(coordinator.spaces["s1"], night_mode_enabled=True)

        coordinator.apply_push_security_state("s1", SecurityState.PARTIALLY_ARMED)

        assert coordinator.spaces["s1"].night_mode_enabled is True


class TestApplyPushDeviceMotion:
    """Per-device motion flips from FCM motion pushes (#173).

    Video doorbells (and other video-edge devices) only report motion over
    FCM, never in the gRPC snapshot, so their `motion` binary_sensor never
    turned on. `apply_push_device_motion` flips it on immediately and
    schedules an auto-off so it self-clears like a PIR sensor.
    """

    def _make_coordinator_with_device(
        self, device_type: str = "video_edge_doorbell"
    ) -> AjaxCobrandedCoordinator:  # noqa: F821
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        hass = MagicMock()
        client = MagicMock()
        with patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
            return_value=None,
        ):
            coordinator = AjaxCobrandedCoordinator(
                hass=hass, client=client, space_ids=["s1"], poll_interval=300
            )
        coordinator.hass = hass
        coordinator.async_set_updated_data = MagicMock()
        coordinator.devices = {
            "doorbell-1": Device(
                id="doorbell-1",
                hub_id="hub-1",
                name="Deurbel",
                device_type=device_type,
                room_id=None,
                group_id=None,
                state=DeviceState.ONLINE,
                malfunctions=0,
                bypassed=False,
                statuses={"signal_strength": 3},
                battery=None,
            )
        }
        return coordinator

    def test_motion_push_sets_motion_detected_true(self) -> None:
        coordinator = self._make_coordinator_with_device()

        with patch("homeassistant.helpers.event.async_call_later"):
            coordinator.apply_push_device_motion("doorbell-1")

        assert coordinator.devices["doorbell-1"].statuses["motion_detected"] is True
        coordinator.async_set_updated_data.assert_called_once()

    def test_motion_push_records_detected_at(self) -> None:
        coordinator = self._make_coordinator_with_device()

        with patch("homeassistant.helpers.event.async_call_later"):
            coordinator.apply_push_device_motion("doorbell-1")

        assert "motion_detected_at" in coordinator.devices["doorbell-1"].statuses

    def test_motion_push_preserves_other_statuses(self) -> None:
        coordinator = self._make_coordinator_with_device()

        with patch("homeassistant.helpers.event.async_call_later"):
            coordinator.apply_push_device_motion("doorbell-1")

        assert coordinator.devices["doorbell-1"].statuses["signal_strength"] == 3

    def test_motion_push_unknown_device_no_op(self) -> None:
        coordinator = self._make_coordinator_with_device()

        with patch("homeassistant.helpers.event.async_call_later"):
            coordinator.apply_push_device_motion("nonexistent")

        coordinator.async_set_updated_data.assert_not_called()

    def test_auto_off_clears_motion_detected(self) -> None:
        coordinator = self._make_coordinator_with_device()

        with patch("homeassistant.helpers.event.async_call_later"):
            coordinator.apply_push_device_motion("doorbell-1")
        coordinator._clear_device_motion("doorbell-1")

        assert coordinator.devices["doorbell-1"].statuses["motion_detected"] is False

    def test_motion_push_schedules_auto_off(self) -> None:
        coordinator = self._make_coordinator_with_device()

        with patch("homeassistant.helpers.event.async_call_later") as call_later:
            coordinator.apply_push_device_motion("doorbell-1")

        call_later.assert_called_once()

    def test_auto_off_is_scheduled_as_loop_callback(self) -> None:
        """Regression (#173): the auto-off action must be a HA `@callback` so
        `async_call_later` runs it on the event loop. A plain lambda is
        classified as a sync job and run in the executor (SyncWorker) thread,
        where its `async_set_updated_data` raises the off-loop
        `async_write_ha_state` RuntimeError storm Bruno hit on beta.8."""
        from homeassistant.core import is_callback

        coordinator = self._make_coordinator_with_device()

        with patch("homeassistant.helpers.event.async_call_later") as call_later:
            coordinator.apply_push_device_motion("doorbell-1")

        action = call_later.call_args.args[2]
        assert is_callback(action), (
            "auto-off action is not a HA callback; async_call_later would run it "
            "in the executor thread and async_set_updated_data would be off-loop"
        )


class TestApplyPushGroupSecurityState:
    """Per-group arm/disarm push updates (#148): only the matching Group is
    refreshed instantly, the space-level state stays put until the next poll
    resolves whether all groups now agree."""

    def _make_coordinator_with_groups(self) -> AjaxCobrandedCoordinator:  # noqa: F821
        from custom_components.aegis_ajax.api.models import Group
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        hass = MagicMock()
        client = MagicMock()
        with patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
            return_value=None,
        ):
            coordinator = AjaxCobrandedCoordinator(
                hass=hass, client=client, space_ids=["s1"], poll_interval=300
            )
        coordinator.hass = hass
        coordinator.async_set_updated_data = MagicMock()
        coordinator.spaces = {
            "s1": Space(
                id="s1",
                hub_id="hub-1",
                name="Home",
                security_state=SecurityState.DISARMED,
                connection_status=ConnectionStatus.ONLINE,
                malfunctions_count=0,
                group_mode_enabled=True,
                groups=(
                    Group(
                        id="g1",
                        space_id="s1",
                        name="Downstairs",
                        security_state=SecurityState.DISARMED,
                        sorting_key="01",
                    ),
                    Group(
                        id="g2",
                        space_id="s1",
                        name="Upstairs",
                        security_state=SecurityState.DISARMED,
                        sorting_key="02",
                    ),
                ),
            )
        }
        return coordinator

    def test_arm_one_group_only_updates_that_group(self) -> None:
        coordinator = self._make_coordinator_with_groups()

        coordinator.apply_push_group_security_state("s1", "g1", SecurityState.ARMED)

        groups = {g.id: g for g in coordinator.spaces["s1"].groups}
        assert groups["g1"].security_state == SecurityState.ARMED
        assert groups["g2"].security_state == SecurityState.DISARMED
        # Space-level state intentionally not touched — only the next poll
        # decides whether the whole space is armed.
        assert coordinator.spaces["s1"].security_state == SecurityState.DISARMED
        coordinator.async_set_updated_data.assert_called_once()

    def test_no_change_skips_update(self) -> None:
        coordinator = self._make_coordinator_with_groups()

        coordinator.apply_push_group_security_state("s1", "g1", SecurityState.DISARMED)

        coordinator.async_set_updated_data.assert_not_called()

    def test_unknown_group_no_op(self) -> None:
        coordinator = self._make_coordinator_with_groups()

        coordinator.apply_push_group_security_state("s1", "unknown", SecurityState.ARMED)

        groups = {g.id: g for g in coordinator.spaces["s1"].groups}
        assert groups["g1"].security_state == SecurityState.DISARMED
        coordinator.async_set_updated_data.assert_not_called()

    def test_unknown_space_no_op(self) -> None:
        coordinator = self._make_coordinator_with_groups()

        coordinator.apply_push_group_security_state("missing", "g1", SecurityState.ARMED)

        coordinator.async_set_updated_data.assert_not_called()


class TestCachedSnapshotStart:
    """First-refresh path now skips `get_devices_snapshot` when a cache is
    available, returning cached devices immediately so platform setup
    drops out of HA's *"integration taking too long"* boot warning. The
    streams started in the same first refresh deliver a fresh snapshot
    within seconds via `_handle_devices_snapshot`, replacing the cache.
    Tracked in #114.
    """

    def _coordinator_with_cache(self, cached: dict[str, Device] | None) -> object:
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        hass = MagicMock()
        client = MagicMock()
        with patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
            return_value=None,
        ):
            coordinator = AjaxCobrandedCoordinator(
                hass=hass,
                client=client,
                space_ids=["s1"],
                poll_interval=30,
                entry_id="entry-1",
            )
        coordinator.hass = hass
        # Replace the real DevicesCache with an in-memory fake
        cache_mock = MagicMock()
        cache_mock.async_load = AsyncMock(return_value=cached)
        cache_mock.async_save = AsyncMock()
        cache_mock.async_schedule_save = MagicMock()
        coordinator._devices_cache = cache_mock
        coordinator._client.session.is_authenticated = True
        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._spaces_api.get_space_snapshot = AsyncMock(return_value=SpaceSnapshot())
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(
            return_value=[_make_device("fresh-d1")]
        )
        coordinator._hub_object_api = MagicMock()
        coordinator._hub_object_api.get_sim_info = AsyncMock(return_value=None)
        coordinator._hub_object_api.get_firmware_info = AsyncMock(return_value=None)
        coordinator._hub_object_api.get_device_firmware_updates = AsyncMock(return_value=[])
        coordinator._start_device_streams = AsyncMock()
        coordinator._start_hts = AsyncMock()
        return coordinator

    @pytest.mark.asyncio
    async def test_first_refresh_with_cache_skips_devices_snapshot(self) -> None:
        cached = {"cached-d1": _make_device("cached-d1")}
        coordinator = self._coordinator_with_cache(cached)

        result = await coordinator._async_update_data()

        # Cache wins: no synchronous gRPC snapshot call on the boot path
        coordinator._devices_api.get_devices_snapshot.assert_not_called()
        assert result["devices"] == cached
        # Subsequent polls won't re-trigger the heavy path
        assert coordinator._streams_started is True
        # Streams + HTS are still kicked off (they were already non-blocking
        # after #113; we just made sure we don't regress that)
        coordinator._start_device_streams.assert_awaited_once()
        coordinator._start_hts.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_first_refresh_without_cache_runs_heavy_path_and_persists(self) -> None:
        coordinator = self._coordinator_with_cache(cached=None)

        await coordinator._async_update_data()

        coordinator._devices_api.get_devices_snapshot.assert_awaited()
        assert "fresh-d1" in coordinator.devices
        # Fresh snapshot is persisted so the next restart can warm-start
        coordinator._devices_cache.async_save.assert_awaited_once_with(coordinator.devices)

    @pytest.mark.asyncio
    async def test_stream_snapshot_callback_persists_cache(self) -> None:
        # When the device stream delivers its initial snapshot via
        # `_handle_devices_snapshot`, that fresh data should overwrite the
        # warm-started cache so the next boot reflects reality. The save
        # is debounced via `async_schedule_save` to coalesce bursts.
        coordinator = self._coordinator_with_cache(cached={"d1": _make_device("d1")})
        coordinator.async_set_updated_data = MagicMock()

        fresh = replace(_make_device("d1"), name="Renamed")
        coordinator._handle_devices_snapshot([fresh])

        assert coordinator.devices["d1"] == fresh
        coordinator._devices_cache.async_schedule_save.assert_called_once_with(coordinator.devices)

    @pytest.mark.asyncio
    async def test_no_cache_when_entry_id_missing(self) -> None:
        # Tests construct the coordinator without an entry_id. We must keep
        # working in that mode (no cache, heavy path always) so the existing
        # ~1080-test suite doesn't need a giant rewrite.
        coordinator = _make_coordinator()
        assert coordinator._devices_cache is None

    @pytest.mark.asyncio
    async def test_first_refresh_emits_startup_summary(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Diagnostic INFO line for #111: at the end of the first refresh
        # we want a single summary that says "device streams N/N started,
        # HTS lifecycle scheduled" so users debugging "0/1" reports can
        # see at a glance which surfaces are coming up at startup.
        coordinator = self._coordinator_with_cache(cached={"d1": _make_device("d1")})
        coordinator._stream_tasks = [MagicMock(done=lambda: False)]
        coordinator._hts_task = MagicMock(done=lambda: False)

        with caplog.at_level("INFO"):
            await coordinator._async_update_data()

        assert "Aegis startup" in caplog.text
        assert "device streams 1/1" in caplog.text
        assert "HTS lifecycle scheduled" in caplog.text


# ---------------------------------------------------------------------------
# Per-device readings via HTS (#123)
# ---------------------------------------------------------------------------


class TestHubFirmwareRefresh:
    """Coordinator piggybacks firmware fetch on the SIM refresh cadence."""

    @pytest.mark.asyncio
    async def test_firmware_info_stored_when_returned(self) -> None:
        from custom_components.aegis_ajax.api.hub_object import (
            HUB_FW_STATE_DOWNLOADING,
            HubFirmwareUpdateInfo,
        )

        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True
        # Force the SIM/firmware refresh branch to fire — `monotonic()`
        # returns a small value on freshly-booted CI runners, so the
        # default `_sim_info_last_fetch = 0` doesn't always exceed the
        # 3600 s gate.
        coordinator._sim_info_last_fetch = -10_000.0
        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._spaces_api.get_space_snapshot = AsyncMock(return_value=SpaceSnapshot())
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])
        coordinator._hub_object_api = MagicMock()
        coordinator._hub_object_api.get_sim_info = AsyncMock(return_value=None)
        coordinator._hub_object_api.get_firmware_info = AsyncMock(
            return_value=HubFirmwareUpdateInfo(
                target_version="2.17.0", state=HUB_FW_STATE_DOWNLOADING
            )
        )
        coordinator._hub_object_api.get_device_firmware_updates = AsyncMock(return_value=[])

        await coordinator._async_update_data()

        # _make_space defaults hub_id to "hub-1"
        assert "hub-1" in coordinator.hub_firmware_updates
        assert coordinator.hub_firmware_updates["hub-1"].target_version == "2.17.0"
        assert coordinator.hub_firmware_updates["hub-1"].state == HUB_FW_STATE_DOWNLOADING

    @pytest.mark.asyncio
    async def test_firmware_entry_cleared_when_api_returns_none(self) -> None:
        """Hub reports no pending update → previous cached entry must be dropped."""
        from custom_components.aegis_ajax.api.hub_object import (
            HUB_FW_STATE_NOT_STARTED,
            HubFirmwareUpdateInfo,
        )

        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True
        coordinator._sim_info_last_fetch = -10_000.0
        # Pre-seed a stale entry
        coordinator.hub_firmware_updates["hub-1"] = HubFirmwareUpdateInfo(
            target_version="2.16.0", state=HUB_FW_STATE_NOT_STARTED
        )
        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._spaces_api.get_space_snapshot = AsyncMock(return_value=SpaceSnapshot())
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])
        coordinator._hub_object_api = MagicMock()
        coordinator._hub_object_api.get_sim_info = AsyncMock(return_value=None)
        coordinator._hub_object_api.get_firmware_info = AsyncMock(return_value=None)
        coordinator._hub_object_api.get_device_firmware_updates = AsyncMock(return_value=[])

        await coordinator._async_update_data()

        assert "hub-1" not in coordinator.hub_firmware_updates

    @pytest.mark.asyncio
    async def test_device_firmware_updates_stored_and_cleared(self) -> None:
        """Per-device firmware updates are rebuilt each cycle (2.1):

        a returned update is stored keyed by device_id; a previously
        cached entry that Ajax no longer reports is dropped.
        """
        from custom_components.aegis_ajax.api.hub_object import (
            DEVICE_FW_STATE_DOWNLOADING,
            DeviceFirmwareUpdateInfo,
        )

        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True
        coordinator._sim_info_last_fetch = -10_000.0
        # Stale entry from a prior cycle that Ajax no longer reports.
        coordinator.device_firmware_updates["OLD99"] = DeviceFirmwareUpdateInfo(
            device_id="OLD99", target_version="1.0.0", state=DEVICE_FW_STATE_DOWNLOADING
        )
        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._spaces_api.get_space_snapshot = AsyncMock(return_value=SpaceSnapshot())
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])
        coordinator._hub_object_api = MagicMock()
        coordinator._hub_object_api.get_sim_info = AsyncMock(return_value=None)
        coordinator._hub_object_api.get_firmware_info = AsyncMock(return_value=None)
        coordinator._hub_object_api.get_device_firmware_updates = AsyncMock(
            return_value=[
                DeviceFirmwareUpdateInfo(
                    device_id="AA11BB22",
                    target_version="6.62.3",
                    state=DEVICE_FW_STATE_DOWNLOADING,
                    progress=30,
                    is_critical=True,
                )
            ]
        )

        await coordinator._async_update_data()

        assert "OLD99" not in coordinator.device_firmware_updates
        assert "AA11BB22" in coordinator.device_firmware_updates
        stored = coordinator.device_firmware_updates["AA11BB22"]
        assert stored.target_version == "6.62.3"
        assert stored.progress == 30
        assert stored.is_critical is True

    @pytest.mark.asyncio
    async def test_device_firmware_updates_keyed_uppercase(self) -> None:
        """Regression: the map key is normalized to uppercase because the

        `streamHubObject` hex id casing is not guaranteed to match the
        devices-snapshot `Device.id` the entities key off (the entity
        lookup uppercases too).
        """
        from custom_components.aegis_ajax.api.hub_object import (
            DEVICE_FW_STATE_NOT_STARTED,
            DeviceFirmwareUpdateInfo,
        )

        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True
        coordinator._sim_info_last_fetch = -10_000.0
        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._spaces_api.get_space_snapshot = AsyncMock(return_value=SpaceSnapshot())
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])
        coordinator._hub_object_api = MagicMock()
        coordinator._hub_object_api.get_sim_info = AsyncMock(return_value=None)
        coordinator._hub_object_api.get_firmware_info = AsyncMock(return_value=None)
        coordinator._hub_object_api.get_device_firmware_updates = AsyncMock(
            return_value=[
                DeviceFirmwareUpdateInfo(
                    device_id="aa11bb22",
                    target_version="6.62.3",
                    state=DEVICE_FW_STATE_NOT_STARTED,
                )
            ]
        )

        await coordinator._async_update_data()

        assert "AA11BB22" in coordinator.device_firmware_updates
        assert "aa11bb22" not in coordinator.device_firmware_updates

    async def test_sim_and_firmware_refresh_dedupes_shared_hub(self) -> None:
        """Multiple spaces backed by one hub (group mode) trigger only a

        single per-hub `streamHubObject` fetch per cycle (2.1 review).
        """
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True
        coordinator._sim_info_last_fetch = -10_000.0
        # Two spaces sharing the default hub_id "hub-1".
        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(
            return_value=[_make_space("s1"), _make_space("s2")]
        )
        coordinator._spaces_api.get_space_snapshot = AsyncMock(return_value=SpaceSnapshot())
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])
        coordinator._hub_object_api = MagicMock()
        coordinator._hub_object_api.get_sim_info = AsyncMock(return_value=None)
        coordinator._hub_object_api.get_firmware_info = AsyncMock(return_value=None)
        coordinator._hub_object_api.get_device_firmware_updates = AsyncMock(return_value=[])

        await coordinator._async_update_data()

        coordinator._hub_object_api.get_firmware_info.assert_awaited_once_with("hub-1")
        coordinator._hub_object_api.get_device_firmware_updates.assert_awaited_once_with("hub-1")


class TestHtsTamperProbeLogging:
    """DEBUG trace for the HTS case-tamper candidate keys (#339).

    A hardware capture on a Hub Plus showed per-device kv keys `0x04` and
    `0x0f` flipping `00` → `01` in lockstep with physically detaching a
    device from its SmartBracket, and back on re-attach — on a hub where
    the gRPC status snapshot carried no tamper at all. Which key is the lid
    and which the bracket is unconfirmed, and we only have one hub's data,
    so this logs the values without acting on them: enough for any reporter
    on DEBUG to confirm the semantics on their own hardware before the
    signal is wired to the tamper sensor.
    """

    def _make_device(self, device_type: str = "motion_protect_curtain") -> Device:
        return Device(
            id="003AE89B",
            hub_id="hub-1",
            name="Curtain",
            device_type=device_type,
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )

    def test_both_candidate_keys_are_logged_with_their_values(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator = _make_coordinator()
        coordinator.devices["003AE89B"] = self._make_device()
        coordinator.async_set_updated_data = MagicMock()

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv("002B1A51", "003AE89B", {0x04: b"\x01", 0x0F: b"\x01"})

        assert "HTS tamper probe" in caplog.text
        assert "0x04" in caplog.text
        assert "0x0F" in caplog.text
        assert "motion_protect_curtain" in caplog.text

    def test_probe_runs_for_a_temperature_family_device(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The HTS temperature merge returns early for its gated families, so
        # the probe has to run before it — otherwise a Curtain Outdoor or a
        # siren would never report its candidate keys.
        coordinator = _make_coordinator()
        coordinator.devices["003AE89B"] = self._make_device(
            device_type="motion_protect_curtain_outdoor_plus"
        )
        coordinator.async_set_updated_data = MagicMock()

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv(
                "002B1A51", "003AE89B", {0x02: b"\x18", 0x04: b"\x00", 0x0F: b"\x00"}
            )

        assert "HTS tamper probe" in caplog.text

    def test_nothing_logged_when_neither_key_is_present(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator = _make_coordinator()
        coordinator.devices["003AE89B"] = self._make_device()

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv("002B1A51", "003AE89B", {0x42: b"\x00\x28"})

        assert "HTS tamper probe" not in caplog.text


class TestHtsBypassStateTracking:
    """HTS `0xB7` as the corroborator for the deactivation carry (#419).

    The key is hardware-validated (#338): `01` ⇔ deactivated, `00` ⇔
    protecting, 1:1 at rest across every reporting device. Tracking it is
    read-mostly: the single write-back allowed is withdrawing a carry this
    coordinator itself applied, the moment the hub reports the bypass lifted.
    """

    def test_kv_row_records_the_hub_bypass_state(self) -> None:
        coordinator = _make_coordinator()
        coordinator.devices["003AE89B"] = _make_device("003AE89B")
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("002B1A51", "003AE89B", {0xB7: b"\x01"})
        assert coordinator._hts_bypass_state["003AE89B"][0] is True

        coordinator._on_hts_device_kv("002B1A51", "003AE89B", {0xB7: b"\x00"})
        assert coordinator._hts_bypass_state["003AE89B"][0] is False

    def test_hub_lift_withdraws_a_carried_deactivation(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The carry can never outlive the hub's own word by more than one
        status refresh — `00` takes it back on the spot."""
        coordinator = _make_coordinator()
        coordinator.devices["003AE89B"] = replace(
            _make_device(
                "003AE89B",
                statuses={
                    "temporary_deactivation_whole": True,
                    "deactivated": True,
                    "signal_strength": 3,
                },
            ),
            bypassed=True,
        )
        coordinator._hts_carried_deactivation_ids.add("003AE89B")
        coordinator.async_set_updated_data = MagicMock()

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv("002B1A51", "003AE89B", {0xB7: b"\x00"})

        device = coordinator.devices["003AE89B"]
        assert "deactivated" not in device.statuses
        assert "temporary_deactivation_whole" not in device.statuses
        assert device.bypassed is False
        # A measurement alongside survives the strip.
        assert device.statuses["signal_strength"] == 3
        assert "003AE89B" not in coordinator._hts_carried_deactivation_ids
        coordinator.async_set_updated_data.assert_called_once()
        assert "Withdrew carried deactivation" in caplog.text

    def test_hub_lift_leaves_grpc_fresh_deactivation_alone(self) -> None:
        """Withdrawal is licensed only for state that exists because of the
        carry — deactivation the server itself reported is not ours to clear."""
        coordinator = _make_coordinator()
        coordinator.devices["003AE89B"] = _make_device(
            "003AE89B",
            statuses={"temporary_deactivation_whole": True, "deactivated": True},
        )
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("002B1A51", "003AE89B", {0xB7: b"\x00"})

        assert coordinator.devices["003AE89B"].statuses["deactivated"] is True

    def test_hub_report_engaged_never_writes(self) -> None:
        """`01` for a protecting device stores the observation and nothing
        else — routing a wrong byte onto the switch would misreport whether a
        sensor is protecting."""
        coordinator = _make_coordinator()
        original = _make_device("003AE89B")
        coordinator.devices["003AE89B"] = original
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("002B1A51", "003AE89B", {0xB7: b"\x01"})

        assert coordinator.devices["003AE89B"] is original
        assert coordinator.devices["003AE89B"].statuses == {}


class TestHtsButtonActivityProbe:
    """HTS `0x39`/`0x40` as a Button's last-activity timestamp (#348).

    Evidence base: one install's Ajax Button in control mode, where `0x39` on
    the device's STATUS_UPDATE row moved to the exact second of each press —
    `6a683b9f` (05:18:23Z) for a press logged 08:18:25 local and `6a683cbd`
    (05:23:09Z) for one logged 08:23:10, on a UTC+3 install. Both a short and a
    long click moved that one key, so it cannot carry the two separate actions
    the issue asks for, and whether it also moves on a supervision ping is
    unproven. Hence a transition-only probe: an untouched Button must stay
    silent, because that silence is what falsifies the ping reading.

    The values below are the real captured ones.
    """

    # 0x39 as captured on two consecutive presses of the same Button.
    FIRST_PRESS = b"\x6a\x68\x3b\x9f"  # 2026-07-28T05:18:23Z
    SECOND_PRESS = b"\x6a\x68\x3c\xbd"  # 2026-07-28T05:23:09Z

    def _make_device(self, device_type: str = "button") -> Device:
        return Device(
            id="313E5F32",
            hub_id="hub-1",
            name="Boto",
            device_type=device_type,
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )

    def test_a_press_after_a_known_value_logs_the_decoded_transition(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator = _make_coordinator()
        coordinator.devices["313E5F32"] = self._make_device()

        # First row establishes the baseline, second is the press.
        coordinator._on_hts_device_kv("0023F477", "313E5F32", {0x39: self.FIRST_PRESS})
        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv("0023F477", "313E5F32", {0x39: self.SECOND_PRESS})

        assert "HTS button probe" in caplog.text
        assert "key=0x39" in caplog.text
        assert "6a683b9f -> 6a683cbd" in caplog.text
        # The decoded epoch is what lets a reporter line the line up against
        # the wall-clock moment they pressed.
        assert "2026-07-28T05:23:09" in caplog.text

    def test_first_sighting_in_a_body_is_silent(self, caplog: pytest.LogCaptureFixture) -> None:
        # The boot snapshot re-reports whatever the last press left behind;
        # logging it would look like a press at every restart.
        coordinator = _make_coordinator()
        coordinator.devices["313E5F32"] = self._make_device()

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv(
                "0023F477", "313E5F32", {0x39: self.FIRST_PRESS}, from_body=True
            )

        assert "HTS button probe" not in caplog.text

    def test_first_sighting_of_a_delta_only_key_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A key that never appears in a body has no baseline to swallow (#348).

        @wip3out3r's first press produced no line at all: on his hardware the
        key is delta-only, so its first sighting was silently recorded as a
        baseline and the press was lost. Once a body row for the device has
        arrived *without* the key, a later delta carrying it is an event.
        """
        coordinator = _make_coordinator()
        coordinator.devices["313E5F32"] = self._make_device()

        # A body row for this device that carries neither candidate key — this
        # is what establishes that the key is delta-only.
        coordinator._on_hts_device_kv("0023F477", "313E5F32", {0x05: b"\x64"}, from_body=True)

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv(
                "0023F477", "313E5F32", {0x39: self.FIRST_PRESS}, from_body=False
            )

        assert "HTS button probe" in caplog.text
        assert "key=0x39" in caplog.text

    def test_a_delta_before_any_body_is_still_silent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Without a body row we cannot tell a delta-only key from one we simply
        # haven't baselined yet, so stay silent rather than guess a press.
        coordinator = _make_coordinator()
        coordinator.devices["313E5F32"] = self._make_device()

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv(
                "0023F477", "313E5F32", {0x39: self.FIRST_PRESS}, from_body=False
            )

        assert "HTS button probe" not in caplog.text

    def test_a_body_carried_key_still_baselines_silently_after_a_body(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The regression guard for the fix: a key present in the body must not
        # start logging its first sighting just because a body has been seen.
        coordinator = _make_coordinator()
        coordinator.devices["313E5F32"] = self._make_device()

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            # Two consecutive bodies, same value — the untouched-Button case.
            coordinator._on_hts_device_kv(
                "0023F477", "313E5F32", {0x39: self.FIRST_PRESS}, from_body=True
            )
            coordinator._on_hts_device_kv(
                "0023F477", "313E5F32", {0x39: self.FIRST_PRESS}, from_body=True
            )

        assert "HTS button probe" not in caplog.text

    def test_an_unchanged_value_is_silent(self, caplog: pytest.LogCaptureFixture) -> None:
        # This is the control case the whole probe exists for: the 60s
        # STATUS_BODY re-reports the same value, and an untouched Button must
        # produce no line at all — otherwise silence proves nothing.
        coordinator = _make_coordinator()
        coordinator.devices["313E5F32"] = self._make_device()

        coordinator._on_hts_device_kv("0023F477", "313E5F32", {0x39: self.FIRST_PRESS})
        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            for _ in range(5):
                coordinator._on_hts_device_kv("0023F477", "313E5F32", {0x39: self.FIRST_PRESS})

        assert "HTS button probe" not in caplog.text

    def test_the_two_keys_are_tracked_independently(self, caplog: pytest.LogCaptureFixture) -> None:
        # `0x40` held an old epoch on the install's other Button, so it means
        # something rarer than a press. It must not be conflated with `0x39`.
        coordinator = _make_coordinator()
        coordinator.devices["313E5F32"] = self._make_device()

        coordinator._on_hts_device_kv(
            "0023F477", "313E5F32", {0x39: self.FIRST_PRESS, 0x40: b"\x00\x00\x00\x00"}
        )
        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv(
                "0023F477", "313E5F32", {0x39: self.SECOND_PRESS, 0x40: b"\x00\x00\x00\x00"}
            )

        assert "key=0x39" in caplog.text
        assert "key=0x40" not in caplog.text

    def test_a_non_timestamp_value_is_not_dressed_up_as_a_date(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A key that carries a counter or bitfield on another firmware must be
        # reported as raw, not converted into a plausible-looking date.
        coordinator = _make_coordinator()
        coordinator.devices["313E5F32"] = self._make_device()

        coordinator._on_hts_device_kv("0023F477", "313E5F32", {0x40: b"\x00\x00\x00\x00"})
        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv("0023F477", "313E5F32", {0x40: b"\x00\x00\x00\x01"})

        assert "HTS button probe" in caplog.text
        assert "not an epoch: 1" in caplog.text

    def test_a_short_value_is_reported_by_length(self, caplog: pytest.LogCaptureFixture) -> None:
        coordinator = _make_coordinator()
        coordinator.devices["313E5F32"] = self._make_device()

        coordinator._on_hts_device_kv("0023F477", "313E5F32", {0x39: b"\x00"})
        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv("0023F477", "313E5F32", {0x39: b"\x01"})

        assert "not an epoch: 1b" in caplog.text

    def test_two_buttons_do_not_share_a_baseline(self, caplog: pytest.LogCaptureFixture) -> None:
        # The install has two Buttons with different `0x39` values; a shared
        # cache would report a press on each alternating row.
        coordinator = _make_coordinator()
        coordinator.devices["313E5F32"] = self._make_device()
        coordinator.devices["313E63EC"] = replace(self._make_device(), id="313E63EC")

        coordinator._on_hts_device_kv(
            "0023F477", "313E5F32", {0x39: self.FIRST_PRESS}, from_body=True
        )
        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv(
                "0023F477", "313E63EC", {0x39: self.SECOND_PRESS}, from_body=True
            )

        assert "HTS button probe" not in caplog.text

    def test_nothing_logged_for_a_device_without_the_keys(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator = _make_coordinator()
        coordinator.devices["313E5F32"] = self._make_device(device_type="relay")
        # A relay row carries electrical readings, which reach the update path.
        coordinator.async_set_updated_data = MagicMock()

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv("0023F477", "313E5F32", {0x42: b"\x00\x00\x00\x00"})

        assert "HTS button probe" not in caplog.text

    def test_probe_does_not_change_any_state(self) -> None:
        # Read-only: short vs long click is still unresolved, and emitting an
        # HA event off a key that may track supervision pings would fire
        # phantom presses into the user's automations.
        coordinator = _make_coordinator()
        coordinator.devices["313E5F32"] = self._make_device()
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("0023F477", "313E5F32", {0x39: self.FIRST_PRESS})
        coordinator._on_hts_device_kv("0023F477", "313E5F32", {0x39: self.SECOND_PRESS})

        assert coordinator.devices["313E5F32"].statuses == {}
        coordinator.async_set_updated_data.assert_not_called()


class TestHtsCaseTamperRouting:
    """HTS `0x04`/`0x0f` drive the shared `tamper` status (#339).

    On hubs that carry case tampering only on the status stream, the gRPC
    fold shipped in #340 has nothing to fold — the snapshot never mentions
    it. Evidence base: one Hub Plus where both keys flipped `00` → `01` on a
    physical detach and back on re-attach (two runs), and a second hub where
    nine intact devices across four families all read `00`.
    """

    def _make_device(self, statuses: dict[str, object] | None = None) -> Device:
        return Device(
            id="003AE89B",
            hub_id="hub-1",
            name="Curtain",
            device_type="motion_protect_curtain",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses=statuses if statuses is not None else {},
            battery=None,
        )

    @pytest.mark.parametrize("key", [0x04, 0x0F])
    def test_either_candidate_key_set_raises_tamper(self, key: int) -> None:
        coordinator = _make_coordinator()
        coordinator.devices["003AE89B"] = self._make_device()
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("002B1A51", "003AE89B", {key: b"\x01"})

        assert coordinator.devices["003AE89B"].statuses["tamper"] is True
        coordinator.async_set_updated_data.assert_called_once()

    def test_keys_back_to_zero_clear_tamper(self) -> None:
        coordinator = _make_coordinator()
        coordinator.devices["003AE89B"] = self._make_device()
        coordinator.async_set_updated_data = MagicMock()
        coordinator._on_hts_device_kv("002B1A51", "003AE89B", {0x04: b"\x01", 0x0F: b"\x01"})
        assert coordinator.devices["003AE89B"].statuses["tamper"] is True

        coordinator._on_hts_device_kv("002B1A51", "003AE89B", {0x04: b"\x00", 0x0F: b"\x00"})

        assert "tamper" not in coordinator.devices["003AE89B"].statuses

    def test_zero_does_not_clear_a_tamper_from_the_grpc_path(self) -> None:
        # The device stream and the status stream are independent sources; a
        # lid still reported open over gRPC must survive an HTS `00`.
        coordinator = _make_coordinator()
        coordinator.devices["003AE89B"] = self._make_device(
            statuses={"tamper": True, "lid_opened": True}
        )
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("002B1A51", "003AE89B", {0x04: b"\x00", 0x0F: b"\x00"})

        assert coordinator.devices["003AE89B"].statuses["tamper"] is True

    def test_repeated_tamper_report_does_not_churn_entities(self) -> None:
        # Every device row repeats on the 60 s status refresh; an unchanged
        # value must not fire a state update.
        coordinator = _make_coordinator()
        coordinator.devices["003AE89B"] = self._make_device()
        coordinator.async_set_updated_data = MagicMock()
        coordinator._on_hts_device_kv("002B1A51", "003AE89B", {0x04: b"\x01"})
        coordinator.async_set_updated_data.reset_mock()

        coordinator._on_hts_device_kv("002B1A51", "003AE89B", {0x04: b"\x01"})

        coordinator.async_set_updated_data.assert_not_called()

    def test_unexpected_value_is_ignored(self) -> None:
        # Only `00` and `01` were ever observed. Anything else means the key
        # carries something different on that firmware — acting on it would
        # risk a phantom tamper alert.
        coordinator = _make_coordinator()
        coordinator.devices["003AE89B"] = self._make_device()
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("002B1A51", "003AE89B", {0x04: b"\x07"})

        assert "tamper" not in coordinator.devices["003AE89B"].statuses
        coordinator.async_set_updated_data.assert_not_called()

    def test_grpc_snapshot_does_not_wipe_an_hts_tamper(self) -> None:
        # A fresh device snapshot rebuilds `statuses` from the stream, which
        # on these hubs never carries the tamper — without a carry-forward
        # the sensor would drop out until the next status refresh.
        coordinator = _make_coordinator()
        coordinator.devices["003AE89B"] = self._make_device()
        coordinator.async_set_updated_data = MagicMock()
        coordinator._on_hts_device_kv("002B1A51", "003AE89B", {0x04: b"\x01"})

        coordinator._handle_devices_snapshot([self._make_device()])

        assert coordinator.devices["003AE89B"].statuses["tamper"] is True


class TestHtsContactState:
    """HTS `0x33` drives `external_contact_open` on MT wire inputs (#413).

    The value mapping comes from Taknok's timestamped capture (2026-08-22),
    NC mode: circuit open (app "Alerte") -> `0x33=00`, circuit closed (app
    "OK") -> `0x33=01`. The first shipped build (1.17.1-beta.4) read those
    two values the other way around — both field reports on the issue saw a
    closed door as "open" in NC — so `00` is open and `01` is closed. The
    proto enum (DISRUPTED=1/NORMAL=2) does NOT describe this byte: firmware
    sends a bool that contradicts the enum's names, so `02` proves nothing
    and is left untouched with the other unknowns.
    """

    def _make_device(
        self, device_type: str = "wire_input_mt", statuses: dict[str, object] | None = None
    ) -> Device:
        return Device(
            id="082639CE",
            hub_id="hub-1",
            name="Wire input 8",
            device_type=device_type,
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses=statuses if statuses is not None else {},
            battery=None,
        )

    def test_00_reads_open(self) -> None:
        # Capture, NC mode, 13:58: circuit open, app shows Alerte -> 0x33=00.
        coordinator = _make_coordinator()
        coordinator.devices["082639CE"] = self._make_device()
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("0029B936", "082639CE", {0x33: b"\x00"})

        assert coordinator.devices["082639CE"].statuses["external_contact_open"] is True
        coordinator.async_set_updated_data.assert_called_once()

    def test_01_reads_closed(self) -> None:
        # Capture, NC mode, 13:59: circuit closed, app shows OK -> 0x33=01.
        coordinator = _make_coordinator()
        coordinator.devices["082639CE"] = self._make_device(
            statuses={"external_contact_open": True}
        )
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("0029B936", "082639CE", {0x33: b"\x01"})

        assert coordinator.devices["082639CE"].statuses["external_contact_open"] is False

    def test_other_families_are_not_routed(self) -> None:
        # 0x33 is `external_sensor_power_broken` on the MultiTransmitter
        # itself and other things elsewhere — the sub-key is a per-family
        # legacy proto field number. Routing beyond the validated family
        # would repeat #406.
        coordinator = _make_coordinator()
        coordinator.devices["082639CE"] = self._make_device(device_type="multi_transmitter")
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("0029B936", "082639CE", {0x33: b"\x01"})

        assert "external_contact_open" not in coordinator.devices["082639CE"].statuses

    @pytest.mark.parametrize("raw", [b"\x02", b"\x03", b"\x80"])
    def test_not_available_and_unknown_sentinel_leave_state_untouched(self, raw: bytes) -> None:
        # `80` is this protocol's unknown sentinel. `02`/`03` are the proto
        # enum's CONTACT_NORMAL/CONTACT_NOT_AVAILABLE — but the enum's value
        # 1 was empirically wrong for this byte, so an enum name is not
        # evidence; no capture has shown either value, and neither says
        # anything about the contact's position.
        coordinator = _make_coordinator()
        coordinator.devices["082639CE"] = self._make_device(
            statuses={"external_contact_open": True}
        )
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("0029B936", "082639CE", {0x33: raw})

        assert coordinator.devices["082639CE"].statuses["external_contact_open"] is True
        coordinator.async_set_updated_data.assert_not_called()

    def test_unchanged_value_does_not_churn_entities(self) -> None:
        # Every device row repeats on the 60 s status refresh; an unchanged
        # value must not fire a state update.
        coordinator = _make_coordinator()
        coordinator.devices["082639CE"] = self._make_device(
            statuses={"external_contact_open": True}
        )
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("0029B936", "082639CE", {0x33: b"\x00"})

        coordinator.async_set_updated_data.assert_not_called()

    def test_the_no_nc_mode_key_is_not_consumed(self) -> None:
        """`0x4A` (the input's NO/NC mode) must not influence the reading.

        The hub already applies the polarity: Taknok's four-state capture
        (2026-08-25) has `00` meaning "away from rest" in BOTH modes. XOR-ing
        the mode in would recover the RAW circuit state, which inverts
        against the door on NO wiring. Both mode values must therefore give
        the same answer for the same `0x33`.
        """
        readings = []
        for mode in (b"\x00", b"\x01"):  # NO, NC
            coordinator = _make_coordinator()
            coordinator.devices["082639CE"] = self._make_device()
            coordinator.async_set_updated_data = MagicMock()

            coordinator._on_hts_device_kv("0029B936", "082639CE", {0x33: b"\x00", 0x4A: mode})

            readings.append(coordinator.devices["082639CE"].statuses["external_contact_open"])
        assert readings == [True, True]

    def test_contact_state_is_in_the_snapshot_carry(self) -> None:
        """gRPC has NO source for this key — a snapshot omitting it is never
        a signal, and only HTS itself (≤60 s cadence) updates or withdraws it."""
        from custom_components.aegis_ajax.coordinator import (
            _SNAPSHOT_CARRY_FORWARD_STATUS_KEYS,
        )

        assert "external_contact_open" in _SNAPSHOT_CARRY_FORWARD_STATUS_KEYS


class TestHtsCaseTamperFamilyGate:
    """Only families with a physical-tamper capture are routed (#406).

    The keys are not a two-value tamper field everywhere. A reporter's
    MotionProtect reads `0x0f` `01` steadily on an intact, remounted device
    that the Ajax app shows as fine, which the unrestricted routing turned
    into a permanent phantom alarm; a SpaceControl reads `0x04` `80` the same
    way (#339), caught only by the `00`/`01` guard. So the routing acts on
    the two families where a capture tied a key to a physical tamper, and
    every other family keeps the probe and nothing else.
    """

    def _make_device(self, device_type: str, statuses: dict[str, object] | None = None) -> Device:
        return Device(
            id="00935562",
            hub_id="hub-1",
            name="Sensor",
            device_type=device_type,
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses=statuses if statuses is not None else {},
            battery=None,
        )

    def test_unvalidated_family_does_not_raise_tamper(self) -> None:
        # @D0NY3NK0's row verbatim (#406): a MotionProtect whose `0x0f` sits
        # at `01` while the app reports no lid or bracket problem, alongside
        # five devices on the same hub reading `00`/`00`.
        coordinator = _make_coordinator()
        coordinator.devices["00935562"] = self._make_device("motion_protect")
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("002B1A51", "00935562", {0x04: b"\x00", 0x0F: b"\x01"})

        assert "tamper" not in coordinator.devices["00935562"].statuses
        coordinator.async_set_updated_data.assert_not_called()

    def test_unvalidated_family_is_still_probed(self, caplog: pytest.LogCaptureFixture) -> None:
        # The gate must not cost us the diagnostic: this probe line is the
        # only reason we know `0x0f` means something else on this family.
        coordinator = _make_coordinator()
        coordinator.devices["00935562"] = self._make_device("motion_protect")
        coordinator.async_set_updated_data = MagicMock()

        with caplog.at_level(logging.DEBUG, logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv("002B1A51", "00935562", {0x04: b"\x00", 0x0F: b"\x01"})

        assert "HTS tamper probe" in caplog.text
        assert "type=motion_protect " in caplog.text

    def test_unvalidated_family_keeps_a_grpc_tamper(self) -> None:
        # The gate withdraws nothing: a tamper the device stream reported is
        # a different source and stays exactly as it was.
        coordinator = _make_coordinator()
        coordinator.devices["00935562"] = self._make_device(
            "motion_protect", statuses={"tamper": True, "lid_opened": True}
        )
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("002B1A51", "00935562", {0x04: b"\x00", 0x0F: b"\x00"})

        assert coordinator.devices["00935562"].statuses["tamper"] is True

    def test_gated_family_withdraws_a_tamper_left_by_an_earlier_version(self) -> None:
        # @D0NY3NK0's install after upgrading (#406). `self.devices` is restored
        # from a persisted cache, so the `hts_case_tamper` 1.15.x wrote survives
        # every restart — gating the write alone left the sensor on forever,
        # because the only path that clears it is the one now skipped.
        coordinator = _make_coordinator()
        coordinator.devices["00935562"] = self._make_device(
            "motion_protect", statuses={"tamper": True, "hts_case_tamper": True}
        )
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("002B1A51", "00935562", {0x04: b"\x00", 0x0F: b"\x01"})

        statuses = coordinator.devices["00935562"].statuses
        assert "tamper" not in statuses
        assert "hts_case_tamper" not in statuses
        coordinator.async_set_updated_data.assert_called_once()

    def test_gated_withdrawal_respects_a_live_grpc_source(self) -> None:
        # The withdrawal drops only the HTS-sourced marker. A lid the device
        # stream still reports open is a different source and must hold.
        coordinator = _make_coordinator()
        coordinator.devices["00935562"] = self._make_device(
            "motion_protect",
            statuses={"tamper": True, "hts_case_tamper": True, "lid_opened": True},
        )
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("002B1A51", "00935562", {0x0F: b"\x01"})

        statuses = coordinator.devices["00935562"].statuses
        assert statuses["tamper"] is True
        assert "hts_case_tamper" not in statuses

    def test_gated_family_withdrawal_does_not_churn_when_already_clean(self) -> None:
        # Every row repeats on the 60 s refresh; with nothing to withdraw the
        # gate must stay silent rather than fire a state update per cycle.
        coordinator = _make_coordinator()
        coordinator.devices["00935562"] = self._make_device("motion_protect")
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("002B1A51", "00935562", {0x0F: b"\x01"})

        coordinator.async_set_updated_data.assert_not_called()

    def test_snapshot_does_not_carry_a_stale_tamper_for_a_gated_family(self) -> None:
        # The snapshot carry-forward exists for routed families whose hub sends
        # no tamper field. For a gated family it would re-raise the very value
        # the withdrawal above just dropped, between snapshots.
        coordinator = _make_coordinator()
        coordinator.devices["00935562"] = self._make_device(
            "motion_protect", statuses={"tamper": True, "hts_case_tamper": True}
        )
        coordinator.async_set_updated_data = MagicMock()

        coordinator._handle_devices_snapshot([self._make_device("motion_protect")])

        statuses = coordinator.devices["00935562"].statuses
        assert "tamper" not in statuses
        assert "hts_case_tamper" not in statuses

    def test_snapshot_still_carries_a_tamper_for_a_routed_family(self) -> None:
        # The #339 behaviour the carry-forward was written for.
        coordinator = _make_coordinator()
        coordinator.devices["00935562"] = self._make_device(
            "motion_protect_curtain", statuses={"tamper": True, "hts_case_tamper": True}
        )
        coordinator.async_set_updated_data = MagicMock()

        coordinator._handle_devices_snapshot([self._make_device("motion_protect_curtain")])

        assert coordinator.devices["00935562"].statuses["tamper"] is True

    @pytest.mark.parametrize("device_type", ["motion_protect_curtain", "transmitter"])
    def test_validated_families_still_route(self, device_type: str) -> None:
        # Both were confirmed on hardware in #339 — a SmartBracket detach and
        # an enclosure opening — and must keep working.
        coordinator = _make_coordinator()
        coordinator.devices["00935562"] = self._make_device(device_type)
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("002B1A51", "00935562", {0x0F: b"\x01"})

        assert coordinator.devices["00935562"].statuses["tamper"] is True


class TestOnHtsDeviceKv:
    """Coordinator translates HTS per-device kv into DeviceReadings."""

    def _make_electrical_device(
        self, device_id: str = "311B058D", device_type: str = "wall_switch"
    ) -> Device:
        return Device(
            id=device_id,
            hub_id="hub-1",
            name="Relay",
            device_type=device_type,
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )

    def test_wall_switch_readings_stored_and_event_fired(self) -> None:
        from custom_components.aegis_ajax.api.hts.hub_state import DeviceReadings

        coordinator = _make_coordinator()
        coordinator.devices["311B058D"] = self._make_electrical_device()
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv(
            "002B1A51",
            "311B058D",
            {0x42: b"\x00\x00\x00\x28", 0x43: b"\x00\x00\x09\x69"},
        )

        assert coordinator.device_readings["311B058D"] == DeviceReadings(
            current_ma=40, power_consumed_wh=2409
        )
        coordinator.async_set_updated_data.assert_called_once()

    def test_unknown_device_id_is_ignored(self) -> None:
        coordinator = _make_coordinator()
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("002B1A51", "DEADBEEF", {0x42: b"\x00\x28"})

        assert coordinator.device_readings == {}
        coordinator.async_set_updated_data.assert_not_called()

    def test_non_electrical_device_type_is_ignored(self) -> None:
        coordinator = _make_coordinator()
        coordinator.devices["311B058D"] = self._make_electrical_device(device_type="door_protect")
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("002B1A51", "311B058D", {0x42: b"\x00\x28"})

        assert coordinator.device_readings == {}
        coordinator.async_set_updated_data.assert_not_called()

    def test_unchanged_readings_dont_trigger_refresh(self) -> None:
        from custom_components.aegis_ajax.api.hts.hub_state import DeviceReadings

        coordinator = _make_coordinator()
        coordinator.devices["311B058D"] = self._make_electrical_device()
        coordinator.device_readings["311B058D"] = DeviceReadings(
            current_ma=40, power_consumed_wh=2409
        )
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv(
            "002B1A51",
            "311B058D",
            {0x42: b"\x00\x00\x00\x28", 0x43: b"\x00\x00\x09\x69"},
        )

        # Same values — no entity refresh needed.
        coordinator.async_set_updated_data.assert_not_called()

    def test_partial_update_does_not_clear_cached_readings(self) -> None:
        """Relay-state push without electrical keys must NOT blank out the readings (#123)."""
        from custom_components.aegis_ajax.api.hts.hub_state import DeviceReadings

        coordinator = _make_coordinator()
        coordinator.devices["311B058D"] = self._make_electrical_device()
        coordinator.device_readings["311B058D"] = DeviceReadings(
            current_ma=40, power_consumed_wh=2409
        )
        coordinator.async_set_updated_data = MagicMock()

        # Push containing only the on/off state byte — no 0x42 / 0x43.
        coordinator._on_hts_device_kv("002B1A51", "311B058D", {0x05: b"\x01"})

        assert coordinator.device_readings["311B058D"] == DeviceReadings(
            current_ma=40, power_consumed_wh=2409
        )
        coordinator.async_set_updated_data.assert_not_called()

    def test_partial_update_with_only_current_keeps_cached_energy(self) -> None:
        """Energy-consumed updates arrive on a different cadence than current (#123)."""
        from custom_components.aegis_ajax.api.hts.hub_state import DeviceReadings

        coordinator = _make_coordinator()
        coordinator.devices["311B058D"] = self._make_electrical_device()
        coordinator.device_readings["311B058D"] = DeviceReadings(
            current_ma=10, power_consumed_wh=2409
        )
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_hts_device_kv("002B1A51", "311B058D", {0x42: b"\x00\x00\x00\x28"})

        assert coordinator.device_readings["311B058D"] == DeviceReadings(
            current_ma=40, power_consumed_wh=2409
        )
        coordinator.async_set_updated_data.assert_called_once()

    def test_hts_disconnect_preserves_cached_state(self) -> None:
        """#146 — both hub_network and device_readings survive transient dropouts.

        Diagnostic values (IP, SSID, signal level, per-device electrical
        readings) keep rendering through the dropout. The single
        broadcast is what lets `mains_power` flip to `unavailable` via
        its `is_hts_alive`-gated `available` property.
        """
        from custom_components.aegis_ajax.api.hts.hub_state import (
            DeviceReadings,
            HubNetworkState,
        )

        coordinator = _make_coordinator()
        net_state = HubNetworkState(ethernet_connected=True, wifi_ssid="my-ssid")
        readings = DeviceReadings(current_ma=40, power_consumed_wh=2409)
        coordinator.hub_network["hub-1"] = net_state
        coordinator.device_readings["311B058D"] = readings
        coordinator._hts_client = MagicMock()
        coordinator.async_set_updated_data = MagicMock()

        coordinator._handle_hts_disconnect(reconnect=False)

        assert coordinator.hub_network == {"hub-1": net_state}
        assert coordinator.device_readings == {"311B058D": readings}
        assert coordinator.is_hts_alive is False
        coordinator.async_set_updated_data.assert_called_once()

    def test_is_hts_alive_reflects_client_presence(self) -> None:
        coordinator = _make_coordinator()
        assert coordinator.is_hts_alive is False
        coordinator._hts_client = MagicMock()
        assert coordinator.is_hts_alive is True
        coordinator._hts_client = None
        assert coordinator.is_hts_alive is False


class TestManualHubRefresh:
    """Coordinator-level guard for the per-hub manual refresh button (#179)."""

    @pytest.mark.asyncio
    async def test_raises_when_hts_not_connected(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        coordinator = _make_coordinator()
        # Default `_hts_client` is None — HTS has never connected.
        with pytest.raises(HomeAssistantError) as exc:
            await coordinator.async_request_manual_refresh("hub-1")
        assert exc.value.translation_key == "manual_refresh_hts_unavailable"

    @pytest.mark.asyncio
    async def test_first_call_dispatches(self) -> None:
        coordinator = _make_coordinator()
        hts = MagicMock()
        hts.request_full_status = AsyncMock()
        coordinator._hts_client = hts

        await coordinator.async_request_manual_refresh("hub-1")

        hts.request_full_status.assert_awaited_once_with("hub-1")
        assert "hub-1" in coordinator._last_manual_refresh

    @pytest.mark.asyncio
    async def test_second_call_within_window_raises(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        coordinator = _make_coordinator()
        hts = MagicMock()
        hts.request_full_status = AsyncMock()
        coordinator._hts_client = hts

        await coordinator.async_request_manual_refresh("hub-1")
        with pytest.raises(HomeAssistantError) as exc:
            await coordinator.async_request_manual_refresh("hub-1")

        assert exc.value.translation_key == "manual_refresh_rate_limited"
        assert exc.value.translation_placeholders is not None
        assert "seconds" in exc.value.translation_placeholders
        hts.request_full_status.assert_awaited_once()  # second call did NOT dispatch

    @pytest.mark.asyncio
    async def test_second_call_after_window_dispatches(self) -> None:
        coordinator = _make_coordinator()
        hts = MagicMock()
        hts.request_full_status = AsyncMock()
        coordinator._hts_client = hts

        with patch("custom_components.aegis_ajax.coordinator.time") as time_mod:
            time_mod.monotonic.side_effect = [1000.0, 1061.0]
            await coordinator.async_request_manual_refresh("hub-1")
            await coordinator.async_request_manual_refresh("hub-1")

        assert hts.request_full_status.await_count == 2

    @pytest.mark.asyncio
    async def test_rate_limit_is_per_hub(self) -> None:
        coordinator = _make_coordinator()
        hts = MagicMock()
        hts.request_full_status = AsyncMock()
        coordinator._hts_client = hts

        await coordinator.async_request_manual_refresh("hub-1")
        # Different hub on the same tick — independent budget, must dispatch.
        await coordinator.async_request_manual_refresh("hub-2")

        assert hts.request_full_status.await_count == 2
        hts.request_full_status.assert_any_await("hub-1")
        hts.request_full_status.assert_any_await("hub-2")

    @pytest.mark.asyncio
    async def test_poll_prunes_rate_limit_entries_for_removed_hubs(self) -> None:
        """`_last_manual_refresh` keys must follow the account's hub set
        (#276) — a hub removed from the account otherwise keeps its
        rate-limit entry for the life of the session.
        """
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True
        coordinator._spaces_api = MagicMock()
        # _make_space carries hub_id="hub-1" — the only hub still on the account.
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._spaces_api.get_space_snapshot = AsyncMock(return_value=SpaceSnapshot())
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        coordinator._last_manual_refresh = {"hub-1": 100.0, "hub-gone": 50.0}

        await coordinator._async_update_data()

        assert coordinator._last_manual_refresh == {"hub-1": 100.0}


class TestSmartLockProbeOrchestration:
    """#206 Bug B: the one-shot probe correlates lock hub-devices to their
    space and delegates to the read-only `DevicesApi.probe_smart_locks`."""

    @pytest.mark.asyncio
    async def test_probe_once_correlates_lock_to_space(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator.spaces = {"s1": _make_space("s1")}  # hub_id="hub-1"
        lock = Device(
            id="31524B92",
            hub_id="hub-1",
            name="Yale",
            device_type="smart_lock_yale",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )
        coordinator.devices = {"31524B92": lock, "d1": _make_device("d1")}
        coordinator._devices_api.probe_smart_locks = AsyncMock()

        await coordinator._probe_smart_locks_once()

        coordinator._devices_api.probe_smart_locks.assert_awaited_once_with("s1", ["31524B92"])

    @pytest.mark.asyncio
    async def test_probe_skips_when_no_lock_and_runs_once(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator.spaces = {"s1": _make_space("s1")}
        coordinator.devices = {"d1": _make_device("d1")}  # no lock
        coordinator._devices_api.probe_smart_locks = AsyncMock()

        await coordinator._probe_smart_locks_once()
        await coordinator._probe_smart_locks_once()

        coordinator._devices_api.probe_smart_locks.assert_not_called()
        assert coordinator._smart_lock_probe_done is True


def _make_siren(
    device_id: str, *, statuses: dict | None = None, device_type: str = "street_siren"
) -> Device:
    return Device(
        id=device_id,
        hub_id="hub-1",
        name="Siren",
        device_type=device_type,
        room_id=None,
        group_id=None,
        state=DeviceState.ONLINE,
        malfunctions=0,
        bypassed=False,
        statuses=statuses if statuses is not None else {},
        battery=None,
    )


class TestHubDeviceTemperatureRefresh:
    @pytest.mark.asyncio
    async def test_merges_temperature_into_curtain_mini_statuses(self) -> None:
        # The Curtain Outdoor Mini is the only family still sourced over gRPC
        # (it carries device_temperature and has no confirmed HTS 0x02). The
        # light stream omits its temperature, so the sensor materialises here.
        coordinator = _make_coordinator(["s1"])
        coordinator.devices = {
            "c1d": _make_siren("c1d", device_type="motion_protect_curtain_outdoor_mini")
        }
        coordinator.async_set_updated_data = MagicMock()
        coordinator._devices_api.get_hub_device_temperature = AsyncMock(return_value=17.0)

        await coordinator._async_refresh_hub_device_temperatures()

        assert coordinator.devices["c1d"].statuses["temperature"] == 17.0
        coordinator._devices_api.get_hub_device_temperature.assert_awaited_once_with("hub-1", "c1d")
        coordinator.async_set_updated_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_grpc_fetch_for_hts_sourced_families(self) -> None:
        # #312/#269: sirens (and Curtain Plus/Base) are sourced from HTS 0x02,
        # which is authoritative and matches the Ajax app. They must NOT be
        # fetched over the gRPC StreamHubDevice path (that returns the board
        # temperature and wastes an RPC).
        coordinator = _make_coordinator(["s1"])
        coordinator.devices = {
            "s1d": _make_siren("s1d"),
            "cpd": _make_siren("cpd", device_type="motion_protect_curtain_outdoor_plus"),
        }
        coordinator.async_set_updated_data = MagicMock()
        coordinator._devices_api.get_hub_device_temperature = AsyncMock(return_value=99.0)

        await coordinator._async_refresh_hub_device_temperatures()

        coordinator._devices_api.get_hub_device_temperature.assert_not_called()
        assert "temperature" not in coordinator.devices["s1d"].statuses
        assert "temperature" not in coordinator.devices["cpd"].statuses
        coordinator.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_non_temperature_devices(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator.devices = {"d1": _make_device("d1")}  # door_protect
        coordinator.async_set_updated_data = MagicMock()
        coordinator._devices_api.get_hub_device_temperature = AsyncMock(return_value=21.0)

        await coordinator._async_refresh_hub_device_temperatures()

        coordinator._devices_api.get_hub_device_temperature.assert_not_called()
        assert "temperature" not in coordinator.devices["d1"].statuses
        coordinator.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_changed_grpc_temperature_updates(self) -> None:
        # #312: a gRPC-sourced reading must not freeze after the first fetch —
        # a different value updates the stored temperature and notifies.
        coordinator = _make_coordinator(["s1"])
        coordinator.devices = {
            "c1d": _make_siren(
                "c1d",
                device_type="motion_protect_curtain_outdoor_mini",
                statuses={"temperature": 18.0},
            )
        }
        coordinator.async_set_updated_data = MagicMock()
        coordinator._devices_api.get_hub_device_temperature = AsyncMock(return_value=21.0)

        await coordinator._async_refresh_hub_device_temperatures()

        assert coordinator.devices["c1d"].statuses["temperature"] == 21.0
        coordinator.async_set_updated_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_unchanged_grpc_temperature_is_noop(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator.devices = {
            "c1d": _make_siren(
                "c1d",
                device_type="motion_protect_curtain_outdoor_mini",
                statuses={"temperature": 21.0},
            )
        }
        coordinator.async_set_updated_data = MagicMock()
        coordinator._devices_api.get_hub_device_temperature = AsyncMock(return_value=21.0)

        await coordinator._async_refresh_hub_device_temperatures()

        assert coordinator.devices["c1d"].statuses["temperature"] == 21.0
        coordinator.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_result_leaves_statuses_untouched(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator.devices = {
            "c1d": _make_siren("c1d", device_type="motion_protect_curtain_outdoor_mini")
        }
        coordinator.async_set_updated_data = MagicMock()
        coordinator._devices_api.get_hub_device_temperature = AsyncMock(return_value=None)

        await coordinator._async_refresh_hub_device_temperatures()

        assert "temperature" not in coordinator.devices["c1d"].statuses
        coordinator.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_one_device_error_does_not_abort_others(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator.devices = {
            "bad": _make_siren("bad", device_type="motion_protect_curtain_outdoor_mini"),
            "good": _make_siren("good", device_type="motion_protect_curtain_outdoor_mini"),
        }
        coordinator.async_set_updated_data = MagicMock()

        async def _temp(hub_id: str, device_id: str) -> float:
            if device_id == "bad":
                raise RuntimeError("boom")
            return 20.0

        coordinator._devices_api.get_hub_device_temperature = AsyncMock(side_effect=_temp)

        await coordinator._async_refresh_hub_device_temperatures()

        assert coordinator.devices["good"].statuses["temperature"] == 20.0
        assert "temperature" not in coordinator.devices["bad"].statuses

    @pytest.mark.asyncio
    async def test_not_driven_by_poll_cycle(self) -> None:
        # Regression for #220: the temperature refresh must NOT depend on the
        # scheduled poll. On push-heavy hubs every HTS update calls
        # `async_set_updated_data`, which resets HA's poll timer, so the
        # scheduled poll never fires again after startup — a poll-driven
        # refresh is starved and the siren sensor never appears. The poll path
        # must therefore not touch siren temperatures at all.
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True
        coordinator.devices = {
            "c1d": _make_siren("c1d", device_type="motion_protect_curtain_outdoor_mini")
        }
        coordinator._stream_tasks = [MagicMock(done=MagicMock(return_value=False))]
        coordinator._hts_client = MagicMock()
        coordinator._hts_task = MagicMock(done=MagicMock(return_value=False))
        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._spaces_api.get_space_snapshot = AsyncMock(return_value=SpaceSnapshot())
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_hub_device_temperature = AsyncMock(return_value=22.0)

        await coordinator._async_update_data()

        coordinator._devices_api.get_hub_device_temperature.assert_not_called()

    def test_schedule_registers_independent_timer_and_initial_kick(self) -> None:
        coordinator = _make_coordinator(["s1"])
        # Replace with a MagicMock so the initial-kick call returns a non-coro.
        coordinator._async_refresh_per_device_snapshots = MagicMock()
        with patch(
            "custom_components.aegis_ajax.coordinator.async_track_time_interval",
            return_value=MagicMock(),
        ) as mock_track:
            coordinator._schedule_hub_device_temperature_refresh()

        mock_track.assert_called_once()
        assert mock_track.call_args.args[2] == timedelta(seconds=HUB_DEVICE_TEMP_REFRESH_INTERVAL)
        assert coordinator._unsub_hub_device_temp is mock_track.return_value
        # Timer's first fire is one full interval out, so an initial
        # non-blocking kick must run for the sensor to appear within seconds.
        coordinator.hass.async_create_task.assert_called_once()

    def test_schedule_is_idempotent(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator._unsub_hub_device_temp = MagicMock()
        with patch(
            "custom_components.aegis_ajax.coordinator.async_track_time_interval",
        ) as mock_track:
            coordinator._schedule_hub_device_temperature_refresh()

        mock_track.assert_not_called()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_timer(self) -> None:
        coordinator = _make_coordinator()
        unsub = MagicMock()
        coordinator._unsub_hub_device_temp = unsub
        coordinator._stream_tasks = []
        coordinator._hts_task = None
        coordinator._hts_client = None
        coordinator._notification_listener = None
        coordinator._client.close = AsyncMock()

        await coordinator.async_shutdown()

        unsub.assert_called_once()
        assert coordinator._unsub_hub_device_temp is None

    def test_snapshot_preserves_merged_siren_temperature(self) -> None:
        coordinator = _make_coordinator()
        coordinator._devices_cache = None
        coordinator.async_set_updated_data = MagicMock()
        # A siren already carrying a temperature merged from StreamHubDevice.
        coordinator.devices = {"s1d": _make_siren("s1d", statuses={"temperature": 22.0})}

        # A fresh stream snapshot for the same siren WITHOUT temperature
        # (the light stream never carries siren temperature).
        coordinator._handle_devices_snapshot([_make_siren("s1d")])

        assert coordinator.devices["s1d"].statuses["temperature"] == 22.0


class TestIntrusionAlarmOverlay:
    """#426: `triggered` derived from the intrusion push, held until DISARMED.

    The served `SecurityState` has no alarm value, so the overlay lives
    client-side, in memory only — a reload/restart must never resurrect a
    stale alarm.
    """

    def _make_space(self, state):  # noqa: ANN001, ANN202
        from custom_components.aegis_ajax.api.models import Space
        from custom_components.aegis_ajax.const import ConnectionStatus

        return Space(
            id="s1",
            hub_id="h1",
            name="Home",
            security_state=state,
            connection_status=ConnectionStatus.ONLINE,
            malfunctions_count=0,
        )

    def _armed_coordinator(self):  # noqa: ANN202
        from custom_components.aegis_ajax.const import SecurityState

        coordinator = _make_coordinator()
        coordinator.async_set_updated_data = MagicMock()
        coordinator.spaces = {"s1": self._make_space(SecurityState.ARMED)}
        return coordinator

    def test_note_intrusion_alarm_marks_known_space(self) -> None:
        coordinator = self._armed_coordinator()

        coordinator.note_intrusion_alarm("s1")

        assert "s1" in coordinator.alarmed_space_ids
        coordinator.async_set_updated_data.assert_called_once()

    def test_note_intrusion_alarm_ignores_unknown_space(self) -> None:
        coordinator = self._armed_coordinator()

        coordinator.note_intrusion_alarm("ghost")

        assert "ghost" not in coordinator.alarmed_space_ids
        coordinator.async_set_updated_data.assert_not_called()

    def test_push_disarm_clears_the_overlay(self) -> None:
        from custom_components.aegis_ajax.const import SecurityState

        coordinator = self._armed_coordinator()
        coordinator.note_intrusion_alarm("s1")

        coordinator.apply_push_security_state("s1", SecurityState.DISARMED)

        assert "s1" not in coordinator.alarmed_space_ids

    def test_push_mode_switch_keeps_the_overlay(self) -> None:
        """Only DISARMED acknowledges the alarm; an armed-mode change does not."""
        from custom_components.aegis_ajax.const import SecurityState

        coordinator = self._armed_coordinator()
        coordinator.note_intrusion_alarm("s1")

        coordinator.apply_push_security_state("s1", SecurityState.NIGHT_MODE)

        assert "s1" in coordinator.alarmed_space_ids

    def test_poll_observing_disarm_drops_the_overlay(self) -> None:
        from custom_components.aegis_ajax.const import SecurityState

        coordinator = self._armed_coordinator()
        coordinator.note_intrusion_alarm("s1")
        coordinator.spaces["s1"] = self._make_space(SecurityState.DISARMED)

        coordinator._drop_cleared_alarms()

        assert "s1" not in coordinator.alarmed_space_ids

    def test_drop_clears_a_space_no_longer_tracked(self) -> None:
        coordinator = self._armed_coordinator()
        coordinator.note_intrusion_alarm("s1")
        coordinator.spaces = {}

        coordinator._drop_cleared_alarms()

        assert "s1" not in coordinator.alarmed_space_ids

    def test_drop_keeps_an_armed_space_in_alarm(self) -> None:
        coordinator = self._armed_coordinator()
        coordinator.note_intrusion_alarm("s1")

        coordinator._drop_cleared_alarms()

        assert "s1" in coordinator.alarmed_space_ids


class TestSirenSettingsRefresh:
    """Timer-driven merge of siren settings from StreamHubDevice (#310)."""

    @pytest.mark.asyncio
    async def test_merges_settings_into_siren_statuses(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator.devices = {"s1d": _make_siren("s1d")}
        coordinator.async_set_updated_data = MagicMock()
        coordinator._devices_api.get_hub_device_siren_settings = AsyncMock(
            return_value={SIREN_ALARM_DURATION_KEY: 90, SIREN_VOLUME_LEVEL_KEY: 18}
        )

        await coordinator._async_refresh_siren_settings()

        assert coordinator.devices["s1d"].statuses[SIREN_ALARM_DURATION_KEY] == 90
        assert coordinator.devices["s1d"].statuses[SIREN_VOLUME_LEVEL_KEY] == 18
        coordinator._devices_api.get_hub_device_siren_settings.assert_awaited_once_with(
            "hub-1", "s1d"
        )
        coordinator.async_set_updated_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_non_siren_devices(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator.devices = {"d1": _make_device("d1")}  # door_protect
        coordinator.async_set_updated_data = MagicMock()
        coordinator._devices_api.get_hub_device_siren_settings = AsyncMock(return_value={})

        await coordinator._async_refresh_siren_settings()

        coordinator._devices_api.get_hub_device_siren_settings.assert_not_called()
        coordinator.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_unchanged_settings_is_noop(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator.devices = {"s1d": _make_siren("s1d", statuses={SIREN_ALARM_DURATION_KEY: 90})}
        coordinator.async_set_updated_data = MagicMock()
        coordinator._devices_api.get_hub_device_siren_settings = AsyncMock(
            return_value={SIREN_ALARM_DURATION_KEY: 90}
        )

        await coordinator._async_refresh_siren_settings()

        coordinator.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_result_leaves_statuses_untouched(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator.devices = {"s1d": _make_siren("s1d")}
        coordinator.async_set_updated_data = MagicMock()
        coordinator._devices_api.get_hub_device_siren_settings = AsyncMock(return_value={})

        await coordinator._async_refresh_siren_settings()

        assert SIREN_ALARM_DURATION_KEY not in coordinator.devices["s1d"].statuses
        coordinator.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_one_device_error_does_not_abort_others(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator.devices = {
            "bad": _make_siren("bad"),
            "good": _make_siren("good"),
        }
        coordinator.async_set_updated_data = MagicMock()

        async def _settings(hub_id: str, device_id: str) -> dict:
            if device_id == "bad":
                raise RuntimeError("boom")
            return {SIREN_ALARM_DURATION_KEY: 45}

        coordinator._devices_api.get_hub_device_siren_settings = AsyncMock(side_effect=_settings)

        await coordinator._async_refresh_siren_settings()

        assert coordinator.devices["good"].statuses[SIREN_ALARM_DURATION_KEY] == 45
        assert SIREN_ALARM_DURATION_KEY not in coordinator.devices["bad"].statuses

    def test_snapshot_preserves_merged_siren_settings(self) -> None:
        coordinator = _make_coordinator()
        coordinator._devices_cache = None
        coordinator.async_set_updated_data = MagicMock()
        coordinator.devices = {
            "s1d": _make_siren(
                "s1d",
                statuses={SIREN_ALARM_DURATION_KEY: 90, SIREN_VOLUME_LEVEL_KEY: 18},
            )
        }

        # Fresh light snapshot without the settings must not wipe them.
        coordinator._handle_devices_snapshot([_make_siren("s1d")])

        assert coordinator.devices["s1d"].statuses[SIREN_ALARM_DURATION_KEY] == 90
        assert coordinator.devices["s1d"].statuses[SIREN_VOLUME_LEVEL_KEY] == 18


class TestSirenSettingsConfirm:
    """Targeted post-write settings re-read — the entity must confirm the real
    hub value within seconds instead of showing the stale one until the 900 s
    snapshot timer fires."""

    def test_schedule_creates_confirm_task(self) -> None:
        coordinator = _make_coordinator(["s1"])

        coordinator.schedule_siren_settings_confirm("s1d")

        coordinator.hass.async_create_task.assert_called_once()
        assert "s1d" in coordinator._siren_confirm_pending

    def test_schedule_is_single_flight_per_device(self) -> None:
        coordinator = _make_coordinator(["s1"])

        coordinator.schedule_siren_settings_confirm("s1d")
        coordinator.schedule_siren_settings_confirm("s1d")

        coordinator.hass.async_create_task.assert_called_once()

    def test_schedule_distinct_devices_each_get_a_task(self) -> None:
        coordinator = _make_coordinator(["s1"])

        coordinator.schedule_siren_settings_confirm("s1d")
        coordinator.schedule_siren_settings_confirm("s2d")

        assert coordinator.hass.async_create_task.call_count == 2

    @pytest.mark.asyncio
    async def test_confirm_merges_and_pushes(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator.devices = {"s1d": _make_siren("s1d", statuses={SIREN_ALARM_DURATION_KEY: 120})}
        coordinator.async_set_updated_data = MagicMock()
        coordinator._devices_api.get_hub_device_siren_settings = AsyncMock(
            return_value={SIREN_ALARM_DURATION_KEY: 90}
        )
        coordinator._siren_confirm_pending.add("s1d")

        with patch("custom_components.aegis_ajax.coordinator.SIREN_SETTINGS_CONFIRM_DELAY", 0):
            await coordinator._async_confirm_siren_settings("s1d")

        assert coordinator.devices["s1d"].statuses[SIREN_ALARM_DURATION_KEY] == 90
        coordinator.async_set_updated_data.assert_called_once()
        assert "s1d" not in coordinator._siren_confirm_pending

    @pytest.mark.asyncio
    async def test_confirm_unchanged_value_skips_push(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator.devices = {"s1d": _make_siren("s1d", statuses={SIREN_ALARM_DURATION_KEY: 90})}
        coordinator.async_set_updated_data = MagicMock()
        coordinator._devices_api.get_hub_device_siren_settings = AsyncMock(
            return_value={SIREN_ALARM_DURATION_KEY: 90}
        )
        coordinator._siren_confirm_pending.add("s1d")

        with patch("custom_components.aegis_ajax.coordinator.SIREN_SETTINGS_CONFIRM_DELAY", 0):
            await coordinator._async_confirm_siren_settings("s1d")

        coordinator.async_set_updated_data.assert_not_called()
        assert "s1d" not in coordinator._siren_confirm_pending

    @pytest.mark.asyncio
    async def test_confirm_fetch_error_clears_pending(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator.devices = {"s1d": _make_siren("s1d")}
        coordinator.async_set_updated_data = MagicMock()
        coordinator._devices_api.get_hub_device_siren_settings = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        coordinator._siren_confirm_pending.add("s1d")

        with patch("custom_components.aegis_ajax.coordinator.SIREN_SETTINGS_CONFIRM_DELAY", 0):
            await coordinator._async_confirm_siren_settings("s1d")

        coordinator.async_set_updated_data.assert_not_called()
        # Pending must clear so the next write can schedule a fresh confirm.
        assert "s1d" not in coordinator._siren_confirm_pending

    @pytest.mark.asyncio
    async def test_confirm_vanished_device_is_noop(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator.devices = {}
        coordinator.async_set_updated_data = MagicMock()
        coordinator._devices_api.get_hub_device_siren_settings = AsyncMock()
        coordinator._siren_confirm_pending.add("gone")

        with patch("custom_components.aegis_ajax.coordinator.SIREN_SETTINGS_CONFIRM_DELAY", 0):
            await coordinator._async_confirm_siren_settings("gone")

        coordinator._devices_api.get_hub_device_siren_settings.assert_not_called()
        assert "gone" not in coordinator._siren_confirm_pending


class TestBypassConfirm:
    """#338 option A — a bypass write the hub accepts but never applies must
    not leave the switch showing the requested state. An independent read-back
    corrects the entity and logs a warning."""

    def _make(self, *, statuses: dict | None = None) -> AjaxCobrandedCoordinator:  # noqa: F821
        coordinator = _make_coordinator(["s1"])
        coordinator.async_set_updated_data = MagicMock()
        coordinator.spaces = {"s1": _make_space("s1")}
        coordinator.devices = {"d1": _make_device("d1", statuses=statuses)}
        return coordinator

    def test_schedule_creates_confirm_task(self) -> None:
        coordinator = self._make()

        coordinator.schedule_bypass_confirm("d1", expected=True)

        coordinator.hass.async_create_task.assert_called_once()
        assert "d1" in coordinator._bypass_confirm_pending

    def test_schedule_is_single_flight_per_device(self) -> None:
        coordinator = self._make()

        coordinator.schedule_bypass_confirm("d1", expected=True)
        coordinator.schedule_bypass_confirm("d1", expected=True)

        coordinator.hass.async_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirm_applies_the_fresh_snapshot(self) -> None:
        coordinator = self._make()
        fresh = _make_device(
            "d1", statuses={"temporary_deactivation_whole": True, "deactivated": True}
        )
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[fresh])
        coordinator._bypass_confirm_pending.add("d1")

        with patch("custom_components.aegis_ajax.coordinator.BYPASS_CONFIRM_DELAY", 0):
            await coordinator._async_confirm_bypass("d1", expected=True)

        assert coordinator.devices["d1"].statuses.get("deactivated") is True
        assert "d1" not in coordinator._bypass_confirm_pending

    @pytest.mark.asyncio
    async def test_confirm_warns_when_the_hub_ignored_the_write(self, caplog) -> None:  # noqa: ANN001
        # The accept-but-inert case: the command returned success, the read
        # back shows the device still active.
        coordinator = self._make()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[_make_device("d1")])
        coordinator._bypass_confirm_pending.add("d1")

        with (
            patch("custom_components.aegis_ajax.coordinator.BYPASS_CONFIRM_DELAY", 0),
            caplog.at_level(logging.WARNING, logger="custom_components.aegis_ajax.coordinator"),
        ):
            await coordinator._async_confirm_bypass("d1", expected=True)

        assert "d1" in caplog.text
        assert "338" in caplog.text
        assert "had no effect" in caplog.text
        # The warning must state the symptom and NOT attribute a cause: the
        # account-permission explanation was disproved on hardware (the same
        # account deactivates the same device from the Ajax app successfully),
        # and users paste this line into issues.
        assert "lacks" not in caplog.text
        assert "permission" not in caplog.text

    @pytest.mark.asyncio
    async def test_confirm_silent_when_the_hub_applied_the_write(self, caplog) -> None:  # noqa: ANN001
        coordinator = self._make(statuses={"deactivated": True})
        coordinator._devices_api.get_devices_snapshot = AsyncMock(
            return_value=[_make_device("d1", statuses={"deactivated": True})]
        )
        coordinator._bypass_confirm_pending.add("d1")

        with (
            patch("custom_components.aegis_ajax.coordinator.BYPASS_CONFIRM_DELAY", 0),
            caplog.at_level(logging.WARNING, logger="custom_components.aegis_ajax.coordinator"),
        ):
            await coordinator._async_confirm_bypass("d1", expected=True)

        assert caplog.text == ""

    @pytest.mark.asyncio
    async def test_confirm_read_error_clears_pending(self) -> None:
        coordinator = self._make()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(side_effect=RuntimeError("boom"))
        coordinator._bypass_confirm_pending.add("d1")

        with patch("custom_components.aegis_ajax.coordinator.BYPASS_CONFIRM_DELAY", 0):
            await coordinator._async_confirm_bypass("d1", expected=True)

        assert "d1" not in coordinator._bypass_confirm_pending

    @pytest.mark.asyncio
    async def test_confirm_vanished_device_is_noop(self) -> None:
        coordinator = self._make()
        coordinator.devices = {}
        coordinator._devices_api.get_devices_snapshot = AsyncMock()
        coordinator._bypass_confirm_pending.add("d1")

        with patch("custom_components.aegis_ajax.coordinator.BYPASS_CONFIRM_DELAY", 0):
            await coordinator._async_confirm_bypass("d1", expected=True)

        coordinator._devices_api.get_devices_snapshot.assert_not_called()
        assert "d1" not in coordinator._bypass_confirm_pending


class TestPollSafetyTimer:
    """Independent poll safety-net timer (#178) — backstop when push is starved.

    On any active hub every HTS update calls `async_set_updated_data`, which
    reschedules HA's built-in poll timer faster than `poll_interval`, so the
    scheduled `_async_update_data` never fires on its own and `security_state`
    plus the hourly snapshot refresh depend 100% on FCM push. A dedicated
    `async_track_time_interval` fires on wall-clock time regardless of HTS
    chatter and requests a refresh, restoring the polled safety net.
    """

    def test_schedule_registers_independent_timer(self) -> None:
        coordinator = _make_coordinator(["s1"])
        with patch(
            "custom_components.aegis_ajax.coordinator.async_track_time_interval",
            return_value=MagicMock(),
        ) as mock_track:
            coordinator._schedule_poll_safety_refresh()

        mock_track.assert_called_once()
        # poll_interval=30 (from _make_coordinator) is clamped to MIN (60) in __init__.
        assert mock_track.call_args.args[2] == timedelta(seconds=60)
        assert coordinator._unsub_poll_safety is mock_track.return_value

    def test_schedule_is_idempotent(self) -> None:
        coordinator = _make_coordinator(["s1"])
        coordinator._unsub_poll_safety = MagicMock()
        with patch(
            "custom_components.aegis_ajax.coordinator.async_track_time_interval",
        ) as mock_track:
            coordinator._schedule_poll_safety_refresh()

        mock_track.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_requests_coordinator_refresh(self) -> None:
        coordinator = _make_coordinator()
        coordinator.async_request_refresh = AsyncMock()

        await coordinator._async_poll_safety_refresh()

        coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_startup_init_schedules_timer(self) -> None:
        coordinator = _make_coordinator()
        coordinator._devices_cache = None
        coordinator.spaces = {}
        coordinator._probe_smart_locks_once = AsyncMock()
        coordinator._start_device_streams = AsyncMock()
        coordinator._start_hts = AsyncMock()
        coordinator._schedule_hub_device_temperature_refresh = MagicMock()
        coordinator._schedule_poll_safety_refresh = MagicMock()

        await coordinator._first_startup_init()

        coordinator._schedule_poll_safety_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_timer(self) -> None:
        coordinator = _make_coordinator()
        unsub = MagicMock()
        coordinator._unsub_poll_safety = unsub
        coordinator._unsub_hub_device_temp = None
        coordinator._stream_tasks = []
        coordinator._hts_task = None
        coordinator._hts_client = None
        coordinator._notification_listener = None
        coordinator._client.close = AsyncMock()

        await coordinator.async_shutdown()

        unsub.assert_called_once()
        assert coordinator._unsub_poll_safety is None


class TestSetChimeOptimistic:
    """Immediate optimistic Chime state after an HA-initiated toggle (#239)."""

    def _make_coordinator(
        self, chime_status: ChimeStatus = ChimeStatus.CAN_BE_ENABLED
    ) -> AjaxCobrandedCoordinator:  # noqa: F821
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        hass = MagicMock()
        client = MagicMock()
        with patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
            return_value=None,
        ):
            coordinator = AjaxCobrandedCoordinator(
                hass=hass, client=client, space_ids=["s1"], poll_interval=300
            )
        coordinator.hass = hass
        coordinator.async_set_updated_data = MagicMock()
        coordinator.spaces = {
            "s1": Space(
                id="s1",
                hub_id="hub-1",
                name="Home",
                security_state=SecurityState.DISARMED,
                connection_status=ConnectionStatus.ONLINE,
                malfunctions_count=0,
                chime_status=chime_status,
            )
        }
        return coordinator

    def test_enable_sets_enabled(self) -> None:
        coordinator = self._make_coordinator(ChimeStatus.CAN_BE_ENABLED)
        coordinator.set_chime_optimistic("s1", enable=True)
        assert coordinator.spaces["s1"].chime_status == ChimeStatus.ENABLED
        coordinator.async_set_updated_data.assert_called_once()

    def test_disable_sets_can_be_enabled(self) -> None:
        coordinator = self._make_coordinator(ChimeStatus.ENABLED)
        coordinator.set_chime_optimistic("s1", enable=False)
        assert coordinator.spaces["s1"].chime_status == ChimeStatus.CAN_BE_ENABLED
        coordinator.async_set_updated_data.assert_called_once()

    def test_no_change_skips_update(self) -> None:
        coordinator = self._make_coordinator(ChimeStatus.ENABLED)
        coordinator.set_chime_optimistic("s1", enable=True)
        coordinator.async_set_updated_data.assert_not_called()

    def test_unknown_space_no_op(self) -> None:
        coordinator = self._make_coordinator(ChimeStatus.ENABLED)
        coordinator.set_chime_optimistic("unknown", enable=False)
        assert coordinator.spaces["s1"].chime_status == ChimeStatus.ENABLED
        coordinator.async_set_updated_data.assert_not_called()


class TestHtsChimeEvent:
    """HTS Chime-event nudge → authoritative gRPC re-read (#239)."""

    def _make_coordinator(
        self, chime_status: ChimeStatus = ChimeStatus.CAN_BE_ENABLED
    ) -> AjaxCobrandedCoordinator:  # noqa: F821
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        hass = MagicMock()
        client = MagicMock()
        with patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
            return_value=None,
        ):
            coordinator = AjaxCobrandedCoordinator(
                hass=hass, client=client, space_ids=["s1"], poll_interval=300
            )
        coordinator.hass = hass
        coordinator.async_set_updated_data = MagicMock()
        coordinator.spaces = {
            "s1": Space(
                id="s1",
                hub_id="HUB1",
                name="Home",
                security_state=SecurityState.DISARMED,
                connection_status=ConnectionStatus.ONLINE,
                malfunctions_count=0,
                chime_status=chime_status,
            )
        }
        return coordinator

    def test_chime_on_byte_decodes_directly(self) -> None:
        # 0x38 = chime ON → ENABLED, written straight from the stream, no refresh.
        coordinator = self._make_coordinator(ChimeStatus.CAN_BE_ENABLED)
        coordinator.async_request_refresh = MagicMock()
        coordinator.hass.async_create_task = MagicMock()

        coordinator._on_hts_space_event("HUB1", "deadbeef", 0x38)

        assert coordinator.spaces["s1"].chime_status == ChimeStatus.ENABLED
        coordinator.async_request_refresh.assert_not_called()
        coordinator.async_set_updated_data.assert_called_once()

    def test_chime_off_byte_decodes_to_can_be_enabled(self) -> None:
        coordinator = self._make_coordinator(ChimeStatus.ENABLED)
        coordinator.async_request_refresh = MagicMock()
        coordinator.hass.async_create_task = MagicMock()

        coordinator._on_hts_space_event("HUB1", "deadbeef", 0x39)

        assert coordinator.spaces["s1"].chime_status == ChimeStatus.CAN_BE_ENABLED
        coordinator.async_request_refresh.assert_not_called()
        coordinator.async_set_updated_data.assert_called_once()

    def test_chime_decode_no_write_when_unchanged(self) -> None:
        coordinator = self._make_coordinator(ChimeStatus.ENABLED)
        coordinator.hass.async_create_task = MagicMock()

        coordinator._on_hts_space_event("HUB1", "deadbeef", 0x38)

        coordinator.async_set_updated_data.assert_not_called()

    def test_chime_unknown_hub_is_ignored(self) -> None:
        coordinator = self._make_coordinator()
        coordinator.async_request_refresh = MagicMock()
        coordinator.hass.async_create_task = MagicMock()

        coordinator._on_hts_space_event("OTHER", "deadbeef", 0x38)

        coordinator.async_request_refresh.assert_not_called()
        coordinator.async_set_updated_data.assert_not_called()


class TestHtsSecurityEvent:
    """HTS `type=0x08` non-chime space event → authoritative re-read (#258).

    Arm/disarm/night share the chime event frame. Their byte is NOT decoded as
    state (arm-initiated ≠ armed; a disarm during the exit delay emits no event;
    events drop on reconnect → a decoded state sticks wrong). Instead the event
    triggers a re-read of the real `security_state` through the dedicated
    short-cooldown debouncer (#270); the state is never set straight from the byte.
    """

    def _make_coordinator(
        self, security_state: SecurityState = SecurityState.DISARMED
    ) -> AjaxCobrandedCoordinator:  # noqa: F821
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        hass = MagicMock()
        with patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
            return_value=None,
        ):
            coordinator = AjaxCobrandedCoordinator(
                hass=hass, client=MagicMock(), space_ids=["s1"], poll_interval=300
            )
        coordinator.hass = hass
        coordinator.async_set_updated_data = MagicMock()
        coordinator.hass.async_create_task = MagicMock()
        coordinator.spaces = {
            "s1": Space(
                id="s1",
                hub_id="HUB1",
                name="Home",
                security_state=security_state,
                connection_status=ConnectionStatus.ONLINE,
                malfunctions_count=0,
                chime_status=ChimeStatus.CAN_BE_ENABLED,
            )
        }
        return coordinator

    def _arm_disarm_triggers_refresh(self, byte: int, initial: SecurityState) -> None:
        coordinator = self._make_coordinator(initial)
        coordinator._security_refresh_debouncer = MagicMock()
        coordinator._on_hts_space_event("HUB1", "deadbeef", byte)
        # Re-reads authoritative state via the dedicated debouncer (#270); never
        # decodes the byte into the panel.
        coordinator._security_refresh_debouncer.async_call.assert_called_once()
        assert coordinator.spaces["s1"].security_state == initial
        coordinator.async_set_updated_data.assert_not_called()

    def test_arm_byte_triggers_refresh_not_decode(self) -> None:
        self._arm_disarm_triggers_refresh(0x01, SecurityState.DISARMED)

    def test_disarm_byte_triggers_refresh_not_decode(self) -> None:
        self._arm_disarm_triggers_refresh(0x00, SecurityState.ARMED)

    def test_night_byte_triggers_refresh_not_decode(self) -> None:
        self._arm_disarm_triggers_refresh(0x02, SecurityState.DISARMED)

    def test_unmapped_byte_also_triggers_refresh(self) -> None:
        # exit-delay / partial / group bytes we haven't mapped still re-read.
        self._arm_disarm_triggers_refresh(0x05, SecurityState.DISARMED)

    def test_unknown_hub_is_ignored(self) -> None:
        coordinator = self._make_coordinator(SecurityState.DISARMED)
        coordinator._security_refresh_debouncer = MagicMock()
        coordinator._on_hts_space_event("OTHER", "deadbeef", 0x01)
        coordinator._security_refresh_debouncer.async_call.assert_not_called()
        coordinator.async_set_updated_data.assert_not_called()

    def test_arm_disarm_event_forces_group_snapshot_refresh(self) -> None:
        # #266: per-group security state lives only on the hourly snapshot, so
        # the arm/disarm event must force the next refresh to re-read it — else
        # group panels lag up to an hour without FCM.
        coordinator = self._make_coordinator(SecurityState.DISARMED)
        coordinator._security_refresh_debouncer = MagicMock()
        assert coordinator._force_snapshot_refresh is False
        coordinator._on_hts_space_event("HUB1", "deadbeef", 0x01)
        assert coordinator._force_snapshot_refresh is True

    def test_chime_event_does_not_force_group_snapshot_refresh(self) -> None:
        # Chime is decoded directly from the event and doesn't touch group state,
        # so it must not trigger the heavier snapshot read.
        coordinator = self._make_coordinator(SecurityState.DISARMED)
        coordinator._on_hts_space_event("HUB1", "deadbeef", 0x38)
        assert coordinator._force_snapshot_refresh is False


def _keyfob_kv(name: bytes = b"ALICE", index: bytes = b"\x02\xef", active: int = 0x01) -> dict:
    """Build a real-shape SpaceControl keyfob SETTINGS_BODY row (synthetic name)."""
    return {
        0x02: name,
        0x07: bytes.fromhex("00000000ffffffff"),
        0x08: bytes.fromhex("00000000ffffffff"),
        0x09: bytes.fromhex("00000000"),
        0x0A: index,
        0x0B: bytes([active]),
        0x0C: b"\x01",
        0x0D: b"\x01",
        0x0E: b"\x01",
        0x0F: bytes(8),
        0x10: b"\x00",
        0x11: bytes(4),
        0x13: bytes(16),
        0x14: bytes(16),
        0x16: b"\xff\xff",
    }


class TestOnHtsDeviceKvKeyfob:
    """Keyfobs are HTS-only — they reach _on_hts_device_kv with no gRPC device."""

    def test_new_keyfob_is_stored_and_announced(self) -> None:
        from custom_components.aegis_ajax.api.hts.keyfobs import Keyfob
        from custom_components.aegis_ajax.const import SIGNAL_NEW_DEVICE

        coordinator = _make_coordinator()
        coordinator.async_set_updated_data = MagicMock()

        with patch("custom_components.aegis_ajax.coordinator.async_dispatcher_send") as mock_send:
            coordinator._on_hts_device_kv("002B1A51", "2ACCB91C", _keyfob_kv())

        assert coordinator.keyfobs["2ACCB91C"] == Keyfob(
            id="2ACCB91C",
            hub_id="002B1A51",
            name="ALICE",
            index=751,
            active=True,
            flags_hex="01:01:01:01",
        )
        mock_send.assert_called_once()
        assert mock_send.call_args.args[1:] == (SIGNAL_NEW_DEVICE, "2ACCB91C")
        coordinator.async_set_updated_data.assert_called_once()

    def test_unchanged_keyfob_no_refresh(self) -> None:
        from custom_components.aegis_ajax.api.hts.keyfobs import parse_keyfob

        coordinator = _make_coordinator()
        coordinator.keyfobs["2ACCB91C"] = parse_keyfob("2ACCB91C", "002B1A51", _keyfob_kv())
        coordinator.async_set_updated_data = MagicMock()

        with patch("custom_components.aegis_ajax.coordinator.async_dispatcher_send") as mock_send:
            coordinator._on_hts_device_kv("002B1A51", "2ACCB91C", _keyfob_kv())

        coordinator.async_set_updated_data.assert_not_called()
        mock_send.assert_not_called()

    def test_changed_keyfob_refreshes_without_reannouncing(self) -> None:
        from custom_components.aegis_ajax.api.hts.keyfobs import parse_keyfob

        coordinator = _make_coordinator()
        coordinator.keyfobs["2ACCB91C"] = parse_keyfob("2ACCB91C", "002B1A51", _keyfob_kv())
        coordinator.async_set_updated_data = MagicMock()

        # Active flag flips (e.g. a deactivated keyfob): refresh, but not "new".
        with patch("custom_components.aegis_ajax.coordinator.async_dispatcher_send") as mock_send:
            coordinator._on_hts_device_kv("002B1A51", "2ACCB91C", _keyfob_kv(active=0x00))

        assert coordinator.keyfobs["2ACCB91C"].active is False
        coordinator.async_set_updated_data.assert_called_once()
        mock_send.assert_not_called()

    def test_non_keyfob_unknown_row_ignored(self) -> None:
        coordinator = _make_coordinator()
        coordinator.async_set_updated_data = MagicMock()

        # A 1-key company marker row at an unknown id — not a keyfob.
        with patch("custom_components.aegis_ajax.coordinator.async_dispatcher_send") as mock_send:
            coordinator._on_hts_device_kv("0000016A", "0000016A", {0x01: b"ACME"})

        assert coordinator.keyfobs == {}
        coordinator.async_set_updated_data.assert_not_called()
        mock_send.assert_not_called()


# The sub-key set @wip3out3r captured for the SpaceControl on his hub (#311),
# where the keyfob is a gRPC-modeled device rather than HTS-only: 47 keys,
# identical across two SETTINGS_BODY messages. Only the six family keys carry
# meaningful values here — the rest are present in his capture and are kept so
# the row's *shape* is the real one, since that shape is what the classifier
# and the probe both key off. Values are synthetic; he reported keys, not bytes.
_MODELED_SPACE_CONTROL_ROW_KEYS = (
    0x08, 0x09, 0x0A, 0x11, 0x12, 0x15, 0x16, 0x20, 0x21, 0x22, 0x23, 0x24,
    0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x2E, 0x31, 0x33, 0x34, 0x35, 0x50,
    0x70, 0x71, 0x73, 0xAC, 0xAD, 0xAE, 0xB0, 0xB5, 0xB6, 0xBB, 0xBC, 0xC0,
    0xC1, 0xC3, 0xC5, 0xCB, 0xEC, 0xED, 0xEE, 0xEF, 0xF0, 0xF4, 0xF8,
)  # fmt: skip


# The *other* row the same device emits (#311): the 22-key STATUS_BODY row,
# arriving every 60 s. Unlike the settings row above these are @wip3out3r's real
# bytes, which he pasted in full — device flags and counters, nothing user-typed.
#
# Two things make this fixture worth having rather than an invented status row.
# `0xC3` is on it, which is what defeated the settings probe's early-out; a
# hand-written status row omitted `0xC3` and so the suite reported silence the
# real hardware never had. And `0x0b` is on it reading `01` — the same sub-key
# the HTS-only keyfob path reads its experimental `Active` flag from.
_MODELED_SPACE_CONTROL_STATUS_ROW: dict[int, bytes] = {
    0x02: b"\x80", 0x03: b"\x03", 0x04: b"\x80", 0x05: b"\x00\x64",
    0x06: b"\x00\x00", 0x07: b"\x80\x00\x00\x00", 0x0B: b"\x01", 0x0C: b"\x01",
    0x0E: b"\x80", 0x0F: b"\x00", 0x10: b"\x00", 0x17: b"\x00\x00\x00\x00",
    0x2C: b"\x01", 0x99: b"\x19", 0x9F: b"\x00", 0xB7: b"\x00",
    0xC2: b"\x80", 0xC3: b"\x00", 0xC6: b"\x00", 0xF5: b"\x00",
    0xF7: b"\x00", 0xF9: b"\x80",
}  # fmt: skip


def _modeled_space_control_status_kv() -> dict[int, bytes]:
    """The 60 s STATUS_BODY row for a modeled SpaceControl, as captured (#311)."""
    return dict(_MODELED_SPACE_CONTROL_STATUS_ROW)


def _modeled_space_control_kv() -> dict[int, bytes]:
    """A modeled SpaceControl's SETTINGS_BODY row, in @wip3out3r's shape (#311)."""
    row = {key: b"\x00" for key in _MODELED_SPACE_CONTROL_ROW_KEYS}
    row[0x2E] = b"\x01"  # siren_triggers
    row[0x31] = b"\x01"  # panic_enabled
    row[0x33] = b"\x00\x02"  # associated_group_id
    row[0x34] = b"\x00\x07"  # associated_user_id
    row[0x35] = b"\x01"  # false_press_filter
    row[0xC3] = b"\x00"  # subtype (SpaceControl vs SpaceControl S)
    return row


class TestModeledSpaceControlIsNotAKeyfobRow:
    """A SpaceControl the gRPC snapshot models is not on the keyfob path (#311).

    Keyfobs are HTS-only on *some* hubs, not all: `ObjectType` carries both
    `space_control` and `space_control_s`, and on a hub that reports one there
    the device gets an ordinary HA device (with the bypass switch that already
    shows deactivation) and its SETTINGS_BODY row never reaches the keyfob
    classifier — which is why @wip3out3r's install has a SpaceControl and no
    keyfob `Active` entity. Pinned so nobody "fixes" that into a duplicate
    entity, and so the shape mismatch is not mistaken for the cause.
    """

    def _make_space_control(self, device_type: str = "space_control") -> Device:
        return Device(
            id="2ACCB91C",
            hub_id="hub-1",
            name="Keyfob",
            device_type=device_type,
            room_id=None,
            group_id="g1",
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )

    def test_modeled_space_control_row_never_enters_the_keyfob_path(self) -> None:
        # Asserted on the *call*, not only on the empty result: the strict shape
        # check would keep `keyfobs` empty anyway, so a result-only assertion
        # could not tell whether the branch had been taken.
        coordinator = _make_coordinator()
        coordinator.devices["2ACCB91C"] = self._make_space_control()
        coordinator.async_set_updated_data = MagicMock()
        coordinator._handle_keyfob_kv = MagicMock()  # type: ignore[method-assign]

        with patch("custom_components.aegis_ajax.coordinator.async_dispatcher_send") as mock_send:
            coordinator._on_hts_device_kv("002B1A51", "2ACCB91C", _modeled_space_control_kv())

        coordinator._handle_keyfob_kv.assert_not_called()
        assert coordinator.keyfobs == {}
        mock_send.assert_not_called()

    def test_the_row_carries_every_space_control_family_key(self) -> None:
        # Provenance for the classification: these six are `SpaceControl`'s own
        # field numbers in the hub's device model, and the captured row has all
        # of them. That is what identifies the row as a SpaceControl's rather
        # than as an unrecognised keyfob variant.
        row = _modeled_space_control_kv()
        assert {0x2E, 0x31, 0x33, 0x34, 0x35, 0xC3} <= set(row)

    def test_probe_logs_the_family_settings_with_the_deactivation_state(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Same rationale as the bypass probe: the bytes are only conclusive
        # paired with what the device's deactivation state actually is.
        coordinator = _make_coordinator()
        coordinator.devices["2ACCB91C"] = replace(
            self._make_space_control(),
            statuses={"temporary_deactivation_whole": True, "deactivated": True},
        )

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv("002B1A51", "2ACCB91C", _modeled_space_control_kv())

        assert "HTS SpaceControl probe" in caplog.text
        assert "associated_user_id" in caplog.text
        assert "panic_enabled" in caplog.text
        assert "deactivated=True" in caplog.text
        assert "temporary_deactivation_whole" in caplog.text

    def test_probe_runs_for_the_s_variant_too(self, caplog: pytest.LogCaptureFixture) -> None:
        coordinator = _make_coordinator()
        coordinator.devices["2ACCB91C"] = self._make_space_control(device_type="space_control_s")

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv("002B1A51", "2ACCB91C", _modeled_space_control_kv())

        assert "HTS SpaceControl probe" in caplog.text

    def test_probe_is_silent_for_other_device_families(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The same sub-key numbers mean unrelated things on other families, so
        # the probe stays gated by device type rather than by shape.
        coordinator = _make_coordinator()
        coordinator.devices["003AE89B"] = _make_device(device_id="003AE89B")

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv("002B1A51", "003AE89B", _modeled_space_control_kv())

        assert "HTS SpaceControl probe" not in caplog.text

    def test_probe_is_silent_when_the_row_carries_no_family_key(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A STATUS_BODY row for the same device carries status keys, not the
        # settings family — no log noise on every 60 s probe.
        coordinator = _make_coordinator()
        coordinator.devices["2ACCB91C"] = self._make_space_control()

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv("002B1A51", "2ACCB91C", {0x04: b"\x00", 0x06: b"\x01"})

        assert "HTS SpaceControl probe" not in caplog.text

    def test_the_real_status_row_carries_subtype_so_it_cannot_gate_the_probe(self) -> None:
        # Provenance for the gate. `0xC3` is on *both* rows, which is why gating
        # on the full settings dict could never go silent — and why the row above
        # this one, written by hand without `0xC3`, reported a silence the
        # hardware never had.
        status = _modeled_space_control_status_kv()
        settings = _modeled_space_control_kv()
        assert 0xC3 in status
        assert 0xC3 in settings
        assert _HTS_SPACE_CONTROL_GATING_KEYS.isdisjoint(status)
        assert 0xC3 not in _HTS_SPACE_CONTROL_GATING_KEYS

    def test_probe_is_silent_for_the_real_60s_status_row(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The regression @wip3out3r measured: a line a minute, indefinitely (#311).

        His hub emitted six probe lines in four minutes, the last four exactly
        60 s apart, five of the six carrying nothing but `subtype`. This is that
        row verbatim, so the assertion fails against the old gate.
        """
        coordinator = _make_coordinator()
        coordinator.devices["2ACCB91C"] = self._make_space_control()
        coordinator.async_set_updated_data = MagicMock()

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv(
                "002B1A51", "2ACCB91C", _modeled_space_control_status_kv()
            )

        assert "HTS SpaceControl probe" not in caplog.text


class TestModeledSpaceControlFlagTransitions:
    """#311: the activation-flag candidates on a modeled SpaceControl's status row.

    @wip3out3r found `0x0b` reading `01` on a gRPC-modeled SpaceControl — the
    same sub-key the HTS-only keyfob path reads its experimental `Active` flag
    from. If it is the same byte, this hub class can supply the deactivated
    sample #311 has always needed, from a row already parsed. These tests pin the
    change-only contract, not the interpretation: whether the two are the same
    byte is what a deactivated capture would settle, and the `0x40` precedent is
    why a matching sub-key number is not treated as evidence on its own.
    """

    def _make_space_control(self, device_type: str = "space_control") -> Device:
        return Device(
            id="2ACCB91C",
            hub_id="hub-1",
            name="Keyfob",
            device_type=device_type,
            room_id=None,
            group_id="g1",
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )

    def _feed(self, coordinator: AjaxCobrandedCoordinator, kv: dict[int, bytes]) -> None:  # noqa: F821
        coordinator._on_hts_device_kv("002B1A51", "2ACCB91C", kv)

    def test_first_sighting_is_silent(self, caplog: pytest.LogCaptureFixture) -> None:
        # The status row re-reports the same constant at every poll and after
        # every restart, so a first sighting must not be announced as a change.
        coordinator = _make_coordinator()
        coordinator.devices["2ACCB91C"] = self._make_space_control()
        coordinator.async_set_updated_data = MagicMock()

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            self._feed(coordinator, _modeled_space_control_status_kv())

        assert "HTS SpaceControl flag probe" not in caplog.text

    def test_an_unchanged_flag_stays_silent_across_polls(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The point of the change-only contract: an active keyfob reads 01
        # forever, so repeated polls must add nothing.
        coordinator = _make_coordinator()
        coordinator.devices["2ACCB91C"] = self._make_space_control()
        coordinator.async_set_updated_data = MagicMock()

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            for _ in range(5):
                self._feed(coordinator, _modeled_space_control_status_kv())

        assert "HTS SpaceControl flag probe" not in caplog.text

    def test_a_flag_transition_is_logged_with_the_deactivation_state(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The one event this probe exists for: `0x0b` moving off `01`."""
        coordinator = _make_coordinator()
        coordinator.devices["2ACCB91C"] = self._make_space_control()
        coordinator.async_set_updated_data = MagicMock()
        self._feed(coordinator, _modeled_space_control_status_kv())

        deactivated = _modeled_space_control_status_kv()
        deactivated[0x0B] = b"\x00"
        coordinator.devices["2ACCB91C"] = replace(
            self._make_space_control(),
            statuses={"temporary_deactivation_whole": True, "deactivated": True},
        )

        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            self._feed(coordinator, deactivated)

        assert "HTS SpaceControl flag probe" in caplog.text
        assert "key=0x0B 01 -> 00" in caplog.text
        # Without the deactivation state alongside it, a transition cannot be
        # told apart from an unrelated flag flip — same pairing as the bypass
        # and settings probes.
        assert "deactivated=True" in caplog.text

    def test_probe_is_silent_for_other_device_families(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # `0x0b..0x0e` carry unrelated things on other families, so this stays
        # gated by device type rather than by shape.
        coordinator = _make_coordinator()
        coordinator.devices["003AE89B"] = _make_device(device_id="003AE89B")
        coordinator.async_set_updated_data = MagicMock()

        row = _modeled_space_control_status_kv()
        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv("002B1A51", "003AE89B", row)
            moved = dict(row)
            moved[0x0B] = b"\x00"
            coordinator._on_hts_device_kv("002B1A51", "003AE89B", moved)

        assert "HTS SpaceControl flag probe" not in caplog.text


class TestOnHtsDeviceKvSpaceSecurity:
    """#284: keypad full-arm of a group reaches us only as a STATUS_UPDATE arm-flag
    flip on a hub-internal space-security object (00000001/00000002) — no type=0x08
    space event, and no FCM push on no-FCM installs. A *change* in the 0x06 arm flag
    must nudge the authoritative re-read so the central panel follows; an unchanged
    flag (re-reported on every 60s STATUS_BODY probe) must NOT, or it would force a
    snapshot every cycle and defeat the hourly cadence.
    """

    def test_arm_flag_change_nudges_authoritative_refresh(self) -> None:
        coordinator = _make_coordinator()
        coordinator._security_refresh_debouncer = MagicMock()
        # Boot/poll already saw the disarmed flag; seed it so the next is a change.
        coordinator._on_hts_device_kv("002CA005", "00000001", {0x06: b"\x00"})
        coordinator._force_snapshot_refresh = False
        # Keypad full-arm: 0x06 flips 0 -> 1 in a lone STATUS_UPDATE delta.
        coordinator._on_hts_device_kv("002CA005", "00000001", {0x06: b"\x01"})
        assert coordinator._force_snapshot_refresh is True
        coordinator._security_refresh_debouncer.async_call.assert_called_once()

    def test_disarm_flag_change_nudges_authoritative_refresh(self) -> None:
        coordinator = _make_coordinator()
        coordinator._security_refresh_debouncer = MagicMock()
        coordinator._on_hts_device_kv("002CA005", "00000002", {0x06: b"\x01"})
        coordinator._force_snapshot_refresh = False
        coordinator._on_hts_device_kv("002CA005", "00000002", {0x06: b"\x00"})
        assert coordinator._force_snapshot_refresh is True

    def test_first_sighting_does_not_nudge(self) -> None:
        # The initial snapshot is already authoritative — the first time we see the
        # flag (boot STATUS_BODY) must not force a redundant refresh.
        coordinator = _make_coordinator()
        coordinator._security_refresh_debouncer = MagicMock()
        coordinator._on_hts_device_kv("002CA005", "00000001", {0x06: b"\x01"})
        assert coordinator._force_snapshot_refresh is False
        coordinator._security_refresh_debouncer.async_call.assert_not_called()

    def test_unchanged_arm_flag_does_not_nudge(self) -> None:
        coordinator = _make_coordinator()
        coordinator._security_refresh_debouncer = MagicMock()
        coordinator._on_hts_device_kv("002CA005", "00000001", {0x06: b"\x01"})
        coordinator._force_snapshot_refresh = False
        # 60s STATUS_BODY probe re-reports the same flag.
        coordinator._on_hts_device_kv("002CA005", "00000001", {0x06: b"\x01"})
        assert coordinator._force_snapshot_refresh is False
        coordinator._security_refresh_debouncer.async_call.assert_not_called()

    def test_space_security_object_not_treated_as_keyfob(self) -> None:
        coordinator = _make_coordinator()
        coordinator._security_refresh_debouncer = MagicMock()
        coordinator.async_set_updated_data = MagicMock()
        with patch("custom_components.aegis_ajax.coordinator.async_dispatcher_send") as mock_send:
            coordinator._on_hts_device_kv("002CA005", "00000001", {0x06: b"\x00"})
            coordinator._on_hts_device_kv("002CA005", "00000001", {0x06: b"\x01"})
        assert coordinator.keyfobs == {}
        mock_send.assert_not_called()

    def test_real_device_arm_like_byte_does_not_nudge(self) -> None:
        # A real Jeweller device (random 8-hex id, not 6 leading zeros) reporting a
        # 0x06 value must never be mistaken for a space-security object.
        coordinator = _make_coordinator()
        coordinator._security_refresh_debouncer = MagicMock()
        coordinator._on_hts_device_kv("002CA005", "30E3131A", {0x06: b"\x00"})
        coordinator._on_hts_device_kv("002CA005", "30E3131A", {0x06: b"\x01"})
        assert coordinator._force_snapshot_refresh is False
        coordinator._security_refresh_debouncer.async_call.assert_not_called()


class TestHtsDeviceTemperature:
    """HTS 0x02 → temperature for gRPC-temp-less device families (#229)."""

    def _coordinator_with_device(
        self, device_type: str, statuses: dict | None = None
    ) -> AjaxCobrandedCoordinator:  # noqa: F821
        coordinator = _make_coordinator()
        coordinator.async_set_updated_data = MagicMock()
        coordinator.devices["d1"] = replace(
            _make_device("d1"), device_type=device_type, statuses=statuses or {}
        )
        return coordinator

    def test_curtain_plus_temperature_merged_from_0x02(self) -> None:
        coordinator = self._coordinator_with_device("motion_protect_curtain_outdoor_plus")
        coordinator._on_hts_device_kv("hub-1", "d1", {0x02: b"\x1b"})
        assert coordinator.devices["d1"].statuses["temperature"] == 27.0
        coordinator.async_set_updated_data.assert_called_once()

    def test_siren_temperature_merged_from_0x02(self) -> None:
        # #312/#269: sirens are sourced from HTS 0x02 (matches the Ajax app),
        # not the gRPC board temperature.
        coordinator = self._coordinator_with_device("street_siren")
        coordinator._on_hts_device_kv("hub-1", "d1", {0x02: b"\x19"})
        assert coordinator.devices["d1"].statuses["temperature"] == 25.0
        coordinator.async_set_updated_data.assert_called_once()

    def test_changed_0x02_updates_existing_temperature(self) -> None:
        # #312: the reading must not freeze after the first value. A different
        # 0x02 updates the stored temperature and notifies listeners.
        coordinator = self._coordinator_with_device("street_siren", statuses={"temperature": 28.0})
        coordinator._on_hts_device_kv("hub-1", "d1", {0x02: b"\x1a"})
        assert coordinator.devices["d1"].statuses["temperature"] == 26.0
        coordinator.async_set_updated_data.assert_called_once()

    def test_unchanged_0x02_is_noop(self) -> None:
        # An identical 0x02 (re-reported on every STATUS_BODY probe) must not
        # churn listeners.
        coordinator = self._coordinator_with_device("street_siren", statuses={"temperature": 25.0})
        coordinator._on_hts_device_kv("hub-1", "d1", {0x02: b"\x19"})
        assert coordinator.devices["d1"].statuses["temperature"] == 25.0
        coordinator.async_set_updated_data.assert_not_called()

    def test_non_gated_device_not_given_hts_temperature(self) -> None:
        # A door sensor's 0x02 is not turned into a temperature here (it gets
        # temperature over gRPC); the HTS path must not fabricate one.
        coordinator = self._coordinator_with_device("door_protect")
        coordinator._on_hts_device_kv("hub-1", "d1", {0x02: b"\x1b"})
        assert "temperature" not in coordinator.devices["d1"].statuses
        coordinator.async_set_updated_data.assert_not_called()

    def test_gated_device_without_usable_0x02_is_noop(self) -> None:
        coordinator = self._coordinator_with_device("motion_protect_curtain_outdoor_base")
        coordinator._on_hts_device_kv("hub-1", "d1", {0x05: b"\x01"})
        assert "temperature" not in coordinator.devices["d1"].statuses
        coordinator.async_set_updated_data.assert_not_called()


class TestButtonPressEvent:
    """#348: the Button control-mode press event.

    Values are the real ones from @raven2k24's capture. `0x39` is a big-endian
    Unix epoch of the press; short and long click move it identically, so there
    is one event type rather than two.
    """

    BOOT_VALUE = b"\x6a\x68\x82\x26"  # 2026-07-28T10:19:18Z — 20 h old at boot
    FIRST_PRESS = b"\x6a\x69\x9b\xf5"  # 2026-07-29T06:21:41Z
    SECOND_PRESS = b"\x6a\x69\x9c\x0c"  # 2026-07-29T06:22:04Z

    def _setup(self, device_type: str = "button") -> tuple[AjaxCobrandedCoordinator, MagicMock]:  # noqa: F821
        coordinator = _make_coordinator()
        coordinator.devices["313E5F32"] = Device(
            id="313E5F32",
            hub_id="hub-1",
            name="Boto",
            device_type=device_type,
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )
        entity = MagicMock()
        coordinator.register_button_event_entity("313E5F32", entity)
        return coordinator, entity

    def test_a_press_fires_the_event_with_the_decoded_timestamp(self) -> None:
        coordinator, entity = self._setup()

        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.BOOT_VALUE}, from_body=True)
        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.FIRST_PRESS})

        entity.handle_event.assert_called_once()
        event_type, data = entity.handle_event.call_args[0]
        assert event_type == "pressed"
        assert data["device_id"] == "313E5F32"
        assert data["pressed_at"].startswith("2026-07-29T06:21:41")

    def test_the_boot_snapshot_does_not_fire(self) -> None:
        """The value at boot was 20 hours old — announcing it would be a lie."""
        coordinator, entity = self._setup()

        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.BOOT_VALUE}, from_body=True)

        entity.handle_event.assert_not_called()

    def test_an_unchanged_value_does_not_fire(self) -> None:
        # The snapshot re-reports the same epoch every cycle; only a change is a
        # press. Without this the entity would fire once a minute forever.
        coordinator, entity = self._setup()

        for _ in range(5):
            coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.FIRST_PRESS}, from_body=True)

        entity.handle_event.assert_not_called()

    def test_consecutive_presses_each_fire(self) -> None:
        coordinator, entity = self._setup()

        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.BOOT_VALUE}, from_body=True)
        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.FIRST_PRESS})
        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.SECOND_PRESS})

        assert entity.handle_event.call_count == 2

    def test_a_delta_and_the_lagging_snapshot_fire_only_once(self) -> None:
        """The snapshot samples once a minute and repeats the delta's value.

        Both paths share one cache, so whichever arrives first fires and the
        other sees no change — otherwise every press would double-trigger.
        """
        coordinator, entity = self._setup()

        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.BOOT_VALUE}, from_body=True)
        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.FIRST_PRESS})
        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.FIRST_PRESS}, from_body=True)

        assert entity.handle_event.call_count == 1

    def test_a_press_seen_only_in_the_snapshot_still_fires(self) -> None:
        # A missed delta must not lose the press outright; the snapshot catches
        # it, just with a slightly older timestamp.
        coordinator, entity = self._setup()

        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.BOOT_VALUE}, from_body=True)
        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.FIRST_PRESS}, from_body=True)

        entity.handle_event.assert_called_once()

    def test_time_going_backwards_does_not_fire(self) -> None:
        # A press can only produce a later timestamp. A lower one means
        # something else is going on, so re-baseline instead of inventing an
        # event.
        coordinator, entity = self._setup()

        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.SECOND_PRESS}, from_body=True)
        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.FIRST_PRESS})

        entity.handle_event.assert_not_called()

    def test_a_non_epoch_value_does_not_fire(self) -> None:
        # On a StreetSiren the same sub-key is a 1-byte counter. Nothing that
        # isn't a plausible epoch may reach the event.
        coordinator, entity = self._setup()

        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: b"\x05"}, from_body=True)
        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: b"\x06"})

        entity.handle_event.assert_not_called()

    def test_another_device_family_does_not_fire(self) -> None:
        """The gate that matters most: 0x39 means other things elsewhere.

        On a DoorProtect Plus it is a roller-shutter-online flag that sits at a
        constant value — read globally, an automation would fire off a door
        sensor.
        """
        coordinator, entity = self._setup(device_type="door_protect_plus")

        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.BOOT_VALUE}, from_body=True)
        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.FIRST_PRESS})

        entity.handle_event.assert_not_called()

    def test_a_press_with_no_entity_registered_still_advances_the_baseline(self) -> None:
        # The entity may be disabled or not yet added. The next press must still
        # fire rather than being swallowed as a baseline.
        coordinator, _ = self._setup()
        coordinator._button_event_entities.clear()

        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.BOOT_VALUE}, from_body=True)
        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.FIRST_PRESS})

        entity = MagicMock()
        coordinator.register_button_event_entity("313E5F32", entity)
        coordinator._on_hts_device_kv("h", "313E5F32", {0x39: self.SECOND_PRESS})

        entity.handle_event.assert_called_once()

    def test_the_0x40_key_is_not_wired_to_the_event(self) -> None:
        # It is a hub-wide counter on sirens, not this device's activity.
        coordinator, entity = self._setup()

        coordinator._on_hts_device_kv("h", "313E5F32", {0x40: self.BOOT_VALUE}, from_body=True)
        coordinator._on_hts_device_kv("h", "313E5F32", {0x40: self.FIRST_PRESS})

        entity.handle_event.assert_not_called()


class TestSirenSettingsFetchFailureLogging:
    """A failed siren-settings read must name its own cause (#354).

    nimahel's DoubleDeck returns `unknown` while *writes* reach the hub, and
    the beta.7 probe added for it (`carried no settings`) never appeared in
    their log — because it sits on the empty-snapshot branch and the read
    never gets that far: the RPC itself raises. The handler logged the
    exception only via `exc_info`, so the one fact needed to tell a
    permission denial from a timeout lives in a traceback tail that a
    reporter's log viewer truncates before the exception line.
    """

    @pytest.mark.asyncio
    async def test_grpc_failure_names_the_status_code_on_the_message_line(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator = _make_coordinator()
        coordinator.devices = {"d1": _make_device("d1")}
        coordinator._devices_api = MagicMock()

        class _FakeCode:
            name = "PERMISSION_DENIED"
            value = (7, "permission denied")

        class _FakeAioRpcError(Exception):
            def code(self) -> _FakeCode:
                return _FakeCode()

            def details(self) -> str:
                return "device edit not allowed"

        coordinator._devices_api.get_hub_device_siren_settings = AsyncMock(
            side_effect=_FakeAioRpcError()
        )

        with caplog.at_level(logging.DEBUG, logger="custom_components.aegis_ajax.coordinator"):
            changed = await coordinator._async_fetch_and_merge_siren_settings("d1")

        assert changed is False
        message = next(
            (r.getMessage() for r in caplog.records if "siren settings" in r.getMessage()), ""
        )
        assert "PERMISSION_DENIED" in message, f"status code missing from log line: {message!r}"

    @pytest.mark.asyncio
    async def test_non_grpc_failure_names_the_exception_type(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A plain exception has no `code()`; the type still has to show."""
        coordinator = _make_coordinator()
        coordinator.devices = {"d1": _make_device("d1")}
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_hub_device_siren_settings = AsyncMock(
            side_effect=TimeoutError("timed out")
        )

        with caplog.at_level(logging.DEBUG, logger="custom_components.aegis_ajax.coordinator"):
            changed = await coordinator._async_fetch_and_merge_siren_settings("d1")

        assert changed is False
        message = next(
            (r.getMessage() for r in caplog.records if "siren settings" in r.getMessage()), ""
        )
        assert "TimeoutError" in message, f"exception type missing from log line: {message!r}"

    @pytest.mark.asyncio
    async def test_first_failure_per_device_warns_then_stays_quiet(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The user-visible symptom is an entity stuck on `unknown` forever,
        and DEBUG-only logging makes that undiagnosable without the reporter
        first being told to turn debug on. Warn once per device so the cause
        is visible at HA's default level, then fall back to DEBUG so a
        permanently unreadable siren can't spam the log every 900 s.
        """
        coordinator = _make_coordinator()
        coordinator.devices = {"d1": _make_device("d1")}
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_hub_device_siren_settings = AsyncMock(
            side_effect=TimeoutError("timed out")
        )

        with caplog.at_level(logging.DEBUG, logger="custom_components.aegis_ajax.coordinator"):
            await coordinator._async_fetch_and_merge_siren_settings("d1")
            await coordinator._async_fetch_and_merge_siren_settings("d1")
            await coordinator._async_fetch_and_merge_siren_settings("d1")

        siren_records = [r for r in caplog.records if "siren settings" in r.getMessage()]
        warnings = [r for r in siren_records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1, f"expected exactly one warning, got {len(warnings)}"
        assert len(siren_records) == 3, "every attempt should still be logged somewhere"


class TestSimInfoFailureIsDiagnosable:
    """#379 — a SIM read that fails must say so at the default log level.

    The IMEI sensor is only created for hubs present in `sim_info`, so a
    failed read means the entity is never offered. Home Assistant does not
    evict entities a platform stops offering, so one created on an earlier
    start sits at `unavailable` indefinitely — with nothing in the log to
    explain it, because the read used to swallow every exception into a
    DEBUG line that named neither the cause nor the gRPC status code.
    """

    @staticmethod
    def _coordinator_with_sim(sim_result: object) -> AjaxCobrandedCoordinator:  # noqa: F821
        coordinator = _make_coordinator()
        coordinator._hub_object_api = MagicMock()
        if isinstance(sim_result, Exception):
            coordinator._hub_object_api.get_sim_info = AsyncMock(side_effect=sim_result)
        else:
            coordinator._hub_object_api.get_sim_info = AsyncMock(return_value=sim_result)
        return coordinator

    @pytest.mark.asyncio
    async def test_first_failure_warns_and_names_the_cause(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator = self._coordinator_with_sim(RuntimeError("boom"))

        with caplog.at_level(logging.WARNING):
            await coordinator._fetch_sim_info("hub-1")

        assert "Failed to read SIM info for hub hub-1" in caplog.text
        assert "RuntimeError" in caplog.text
        # The consequence has to be on the line too: a reporter reading this
        # needs to connect it to the entity they can see is unavailable.
        assert "IMEI" in caplog.text
        assert "hub-1" not in coordinator.sim_info

    @pytest.mark.asyncio
    async def test_repeat_failure_drops_to_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        coordinator = self._coordinator_with_sim(RuntimeError("boom"))
        await coordinator._fetch_sim_info("hub-1")
        caplog.clear()  # drop the first failure's warning

        with caplog.at_level(logging.WARNING):
            await coordinator._fetch_sim_info("hub-1")

        assert "Failed to read SIM info" not in caplog.text

    @pytest.mark.asyncio
    async def test_hub_without_a_modem_is_not_an_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`None` means "no SIM section", which is normal on a wired hub."""
        coordinator = self._coordinator_with_sim(None)

        with caplog.at_level(logging.WARNING):
            await coordinator._fetch_sim_info("hub-1")

        assert caplog.text == ""
        assert "hub-1" not in coordinator.sim_info

    @pytest.mark.asyncio
    async def test_success_stores_info_and_rearms_the_warning(self) -> None:
        from custom_components.aegis_ajax.api.hub_object import SimCardInfo

        coordinator = self._coordinator_with_sim(RuntimeError("boom"))
        await coordinator._fetch_sim_info("hub-1")
        assert "hub-1" in coordinator._sim_info_failed

        coordinator._hub_object_api.get_sim_info = AsyncMock(
            return_value=SimCardInfo(active_sim=1, status=2, imei="123456789012345")
        )
        await coordinator._fetch_sim_info("hub-1")

        assert coordinator.sim_info["hub-1"].imei == "123456789012345"
        # A hub that recovers must be able to warn again if it breaks later.
        assert "hub-1" not in coordinator._sim_info_failed

    @pytest.mark.asyncio
    async def test_a_failing_hub_does_not_break_the_refresh_cycle(self) -> None:
        """The read now raises; the cycle must still complete (#379)."""
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True
        coordinator._sim_info_last_fetch = -10_000.0
        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._spaces_api.get_space_snapshot = AsyncMock(return_value=SpaceSnapshot())
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])
        coordinator._hub_object_api = MagicMock()
        coordinator._hub_object_api.get_sim_info = AsyncMock(side_effect=RuntimeError("boom"))
        coordinator._hub_object_api.get_firmware_info = AsyncMock(return_value=None)
        coordinator._hub_object_api.get_device_firmware_updates = AsyncMock(return_value=[])

        await coordinator._async_update_data()

        assert "hub-1" in coordinator._sim_info_failed


class TestDeactivationCarryIsVisibleInDiagnostics:
    """The #419 carry must leave a record that outlives a log buffer.

    The carry self-corrects within one status refresh, so its only trace was a
    DEBUG line. The reporter watching for it lost the decisive instant to log
    rotation twice, which is what kept the fix unconfirmed rather than any
    doubt about the code. A counter and the corroborator's freshness survive in
    the diagnostics dump, so a natural occurrence can be reported afterwards
    instead of having to be caught live.
    """

    def _coordinator(self) -> AjaxCobrandedCoordinator:  # noqa: F821
        return TestStreamHandlers()._make_coordinator_with_stream()

    def _deactivated(self, device_id: str = "d1") -> Device:
        return _make_device(
            device_id,
            statuses={"temporary_deactivation_whole": True, "deactivated": True},
        )

    def test_a_carry_is_counted_and_dated(self) -> None:
        coordinator = self._coordinator()
        coordinator.devices["d1"] = self._deactivated()
        coordinator._hts_bypass_state["d1"] = (True, time.monotonic())

        before = coordinator.deactivation_carry_state()
        assert before["device_carries"] == 0
        assert before["last_carry_at"] is None

        coordinator._handle_devices_snapshot([_make_device("d1")])

        after = coordinator.deactivation_carry_state()
        assert after["snapshots_with_a_carry"] == 1
        assert after["device_carries"] == 1
        assert after["last_carry_at"] is not None
        assert after["currently_carried_device_ids"] == ["d1"]

    def test_two_devices_carried_in_one_snapshot_count_as_one_snapshot(self) -> None:
        coordinator = self._coordinator()
        for device_id in ("d1", "d2"):
            coordinator.devices[device_id] = self._deactivated(device_id)
            coordinator._hts_bypass_state[device_id] = (True, time.monotonic())

        coordinator._handle_devices_snapshot([_make_device("d1"), _make_device("d2")])

        state = coordinator.deactivation_carry_state()
        assert state["snapshots_with_a_carry"] == 1
        assert state["device_carries"] == 2
        assert state["currently_carried_device_ids"] == ["d1", "d2"]

    def test_a_healthy_snapshot_counts_nothing(self) -> None:
        coordinator = self._coordinator()
        coordinator.devices["d1"] = self._deactivated()
        coordinator._hts_bypass_state["d1"] = (True, time.monotonic())

        coordinator._handle_devices_snapshot([self._deactivated("d1")])

        state = coordinator.deactivation_carry_state()
        assert state["snapshots_with_a_carry"] == 0
        assert state["device_carries"] == 0
        assert state["currently_carried_device_ids"] == []

    def test_the_corroborator_reports_its_own_age(self) -> None:
        """A stale report is why a carry did *not* happen, so the age is the
        first thing to look at when the counter stays at zero."""
        coordinator = self._coordinator()
        coordinator._hts_bypass_state["d1"] = (True, time.monotonic() - 120)
        coordinator._hts_bypass_state["d2"] = (False, time.monotonic())

        reports = coordinator.deactivation_carry_state()["hub_bypass_reports"]

        assert reports["d1"]["deactivated"] is True
        assert 119 <= reports["d1"]["age_seconds"] <= 125
        assert reports["d2"]["deactivated"] is False
        assert reports["d2"]["age_seconds"] < 5

    def test_a_withdrawn_carry_leaves_the_count_but_clears_the_membership(self) -> None:
        """The count records that it happened; membership records that it still
        applies. Conflating them would erase the evidence on self-correction."""
        coordinator = self._coordinator()
        coordinator.devices["d1"] = self._deactivated()
        coordinator._hts_bypass_state["d1"] = (True, time.monotonic())
        coordinator._handle_devices_snapshot([_make_device("d1")])

        coordinator._maybe_track_hts_bypass_state("d1", coordinator.devices["d1"], {0xB7: b"\x00"})

        state = coordinator.deactivation_carry_state()
        assert state["device_carries"] == 1
        assert state["currently_carried_device_ids"] == []
