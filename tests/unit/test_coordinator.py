"""Tests for the data update coordinator."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax.api.hts.hub_state import HubNetworkState
from custom_components.aegis_ajax.api.models import Device, Space
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

    def test_rooms_initially_empty(self) -> None:
        coordinator = _make_coordinator()
        assert coordinator.rooms == {}


class TestRoomsRefresh:
    @pytest.mark.asyncio
    async def test_rooms_populated_from_spaces_api(self) -> None:
        from custom_components.aegis_ajax.api.models import Room

        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True
        coordinator._streams_started = True

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(return_value=[_make_space("s1")])
        coordinator._spaces_api.list_rooms = AsyncMock(
            return_value=[Room(id="r1", name="Kitchen", space_id="s1")]
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
        coordinator._spaces_api.list_rooms = AsyncMock(side_effect=RuntimeError("oops"))
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.get_devices_snapshot = AsyncMock(return_value=[])

        # Should not raise — failure is downgraded to debug log
        await coordinator._async_update_data()
        assert coordinator.rooms == {}


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
    async def test_update_data_raises_update_failed_on_error(self) -> None:
        from homeassistant.helpers.update_coordinator import UpdateFailed

        coordinator = _make_coordinator()
        coordinator._client.session.is_authenticated = True

        coordinator._spaces_api = MagicMock()
        coordinator._spaces_api.list_spaces = AsyncMock(side_effect=RuntimeError("API error"))

        with pytest.raises(UpdateFailed, match="Error fetching Ajax data"):
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
