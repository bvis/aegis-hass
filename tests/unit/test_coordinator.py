"""Tests for the data update coordinator."""

from __future__ import annotations

import asyncio
import contextlib
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
from custom_components.aegis_ajax.const import ConnectionStatus, DeviceState, SecurityState


def _make_space(space_id: str = "s1") -> Space:
    return Space(
        id=space_id,
        hub_id="hub-1",
        name="Home",
        security_state=SecurityState.DISARMED,
        connection_status=ConnectionStatus.ONLINE,
        malfunctions_count=0,
    )


def _make_device(device_id: str = "d1") -> Device:
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
        statuses={},
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
        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True
        coordinator.hub_network = {"hub-1": HubNetworkState(ethernet_connected=True)}
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

        assert coordinator.hub_network == {}
        coordinator._start_hts.assert_awaited_once()
        coordinator.async_set_updated_data.assert_called_once()


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
        coordinator.async_set_updated_data = MagicMock()
        return coordinator

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

    def test_handle_hts_disconnect_clears_stale_network_state(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.hub_network["hub-1"] = HubNetworkState(ethernet_connected=True)

        coordinator._handle_hts_disconnect()

        assert coordinator.hub_network == {}
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

    def test_handle_hts_task_done_clears_state(self) -> None:
        coordinator = self._make_coordinator_with_stream()
        coordinator.hub_network["hub-1"] = HubNetworkState(ethernet_connected=True)
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = False
        task.result.return_value = None

        coordinator._handle_hts_task_done(task)

        assert coordinator.hub_network == {}
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

        coordinator._devices_api.start_device_stream.assert_called_once_with(
            "s1",
            on_devices_snapshot=coordinator._handle_devices_snapshot,
            on_status_update=coordinator._handle_status_update,
        )
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

    def test_hts_disconnect_clears_device_readings(self) -> None:
        from custom_components.aegis_ajax.api.hts.hub_state import DeviceReadings

        coordinator = _make_coordinator()
        coordinator.device_readings["311B058D"] = DeviceReadings(
            current_ma=40, power_consumed_wh=2409
        )
        coordinator.async_set_updated_data = MagicMock()

        coordinator._handle_hts_disconnect(reconnect=False)

        assert coordinator.device_readings == {}
        # Both hub_network and device_readings clear in the same call,
        # so async_set_updated_data fires exactly once.
        coordinator.async_set_updated_data.assert_called_once()
