"""Tests for devices API."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

import pytest

from custom_components.aegis_ajax.api.devices import (
    DevicesApi,
    _encode_string_field,
    _encode_varint_field,
)
from custom_components.aegis_ajax.api.models import Device, DeviceCommand
from custom_components.aegis_ajax.const import DeviceState


class TestParseDevice:
    def test_parse_hub_device(self) -> None:
        proto_device = MagicMock()
        proto_device.hub_device.common_device.profile.id = "dev-1"
        proto_device.hub_device.common_device.profile.name = "Front Door"
        proto_device.hub_device.common_device.profile.room_id = "room-1"
        proto_device.hub_device.common_device.profile.group_id = ""
        proto_device.hub_device.common_device.profile.malfunctions = 0
        proto_device.hub_device.common_device.profile.bypassed = False
        proto_device.hub_device.common_device.profile.device_marketing_id = "DoorProtect"
        proto_device.hub_device.common_device.profile.states = []
        proto_device.hub_device.common_device.profile.statuses = []
        proto_device.hub_device.common_device.hub_id = "hub-1"
        proto_device.hub_device.common_device.object_type.WhichOneof.return_value = "door_protect"
        proto_device.WhichOneof.return_value = "hub_device"

        device = DevicesApi.parse_device(proto_device)
        assert isinstance(device, Device)
        assert device.id == "dev-1"
        assert device.name == "Front Door"
        assert device.hub_id == "hub-1"
        assert device.device_type == "door_protect"
        assert device.room_id == "room-1"
        assert device.state == DeviceState.ONLINE

    def test_parse_offline_device(self) -> None:
        proto_device = MagicMock()
        proto_device.hub_device.common_device.profile.id = "dev-2"
        proto_device.hub_device.common_device.profile.name = "Motion"
        proto_device.hub_device.common_device.profile.room_id = ""
        proto_device.hub_device.common_device.profile.group_id = ""
        proto_device.hub_device.common_device.profile.malfunctions = 0
        proto_device.hub_device.common_device.profile.bypassed = False
        proto_device.hub_device.common_device.profile.device_marketing_id = "MotionProtect"
        proto_device.hub_device.common_device.profile.states = [9]  # OFFLINE
        proto_device.hub_device.common_device.profile.statuses = []
        proto_device.hub_device.common_device.hub_id = "hub-1"
        proto_device.hub_device.common_device.object_type.WhichOneof.return_value = "motion_protect"
        proto_device.WhichOneof.return_value = "hub_device"

        device = DevicesApi.parse_device(proto_device)
        assert device is not None
        assert device.state == DeviceState.OFFLINE

    def test_parse_non_hub_device_returns_none(self) -> None:
        proto_device = MagicMock()
        proto_device.WhichOneof.return_value = "video_edge"
        result = DevicesApi.parse_device(proto_device)
        assert result is None


class TestDeviceStateParser:
    def test_empty_states_returns_online(self) -> None:
        result = DevicesApi._parse_device_state([])
        assert result == DeviceState.ONLINE

    def test_none_states_returns_online(self) -> None:
        result = DevicesApi._parse_device_state(None)
        assert result == DeviceState.ONLINE

    def test_offline_state(self) -> None:
        result = DevicesApi._parse_device_state([9])
        assert result == DeviceState.OFFLINE

    def test_worst_state_wins(self) -> None:
        # Mix of battery_saving (60) and offline (100) -> offline
        result = DevicesApi._parse_device_state([7, 9])
        assert result == DeviceState.OFFLINE

    def test_unknown_state_code(self) -> None:
        result = DevicesApi._parse_device_state([99])
        assert result == DeviceState.UNKNOWN


class TestBatteryParser:
    def test_parse_battery_found(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "battery"
        status.battery.charge_level_percentage = 75
        status.battery.battery_state = 1  # OK

        result = DevicesApi._parse_battery([status])
        assert result is not None
        assert result.level == 75
        assert result.is_low is False

    def test_parse_battery_low(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "battery"
        status.battery.charge_level_percentage = 10
        status.battery.battery_state = 2  # LOW

        result = DevicesApi._parse_battery([status])
        assert result is not None
        assert result.is_low is True

    def test_parse_battery_not_found(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "door_opened"
        result = DevicesApi._parse_battery([status])
        assert result is None

    def test_parse_battery_empty(self) -> None:
        result = DevicesApi._parse_battery([])
        assert result is None


class TestStatusParser:
    def test_door_opened_status(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "door_opened"
        result = DevicesApi._parse_statuses([status])
        assert result.get("door_opened") is True

    def test_motion_detected_status(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "motion_detected"
        result = DevicesApi._parse_statuses([status])
        assert result.get("motion_detected") is True

    def test_smoke_detected(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "smoke_detected"
        result = DevicesApi._parse_statuses([status])
        assert result.get("smoke_detected") is True

    def test_co_detected(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "co_level_detected"
        result = DevicesApi._parse_statuses([status])
        assert result.get("co_detected") is True

    def test_temperature_status(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "temperature"
        status.temperature.value = 22.5
        result = DevicesApi._parse_statuses([status])
        assert result.get("temperature") == 22.5

    def test_leak_detected(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "leak_detected"
        result = DevicesApi._parse_statuses([status])
        assert result.get("leak_detected") is True

    def test_tamper_status(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "tamper"
        result = DevicesApi._parse_statuses([status])
        assert result.get("tamper") is True

    def test_high_temperature(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "high_temperature_detected"
        result = DevicesApi._parse_statuses([status])
        assert result.get("high_temperature") is True

    def test_signal_strength(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "signal_strength"
        status.signal_strength.device_signal_level = 3
        result = DevicesApi._parse_statuses([status])
        assert result.get("signal_strength") == "Normal"

    def test_life_quality_status(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "life_quality"
        lq = MagicMock()
        lq.actual_temperature = 21.0
        lq.actual_humidity = 55.0
        lq.actual_co2 = 400
        status.life_quality = lq
        result = DevicesApi._parse_statuses([status])
        assert result.get("temperature") == 21.0
        assert result.get("humidity") == 55.0
        assert result.get("co2") == 400

    def test_none_which_oneof_skipped(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = None
        result = DevicesApi._parse_statuses([status])
        assert result == {}

    def test_no_whichoneof_attr(self) -> None:
        # status without WhichOneof method
        class SimpleStatus:
            pass

        result = DevicesApi._parse_statuses([SimpleStatus()])
        assert result == {}

    def test_motion_detected_with_timestamp(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "motion_detected"
        status.motion_detected.detected_at.seconds = 1700000000
        result = DevicesApi._parse_statuses([status])
        assert result.get("motion_detected") is True
        assert result.get("motion_detected_at") == 1700000000

    def test_gsm_status_connected(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "gsm_status"
        status.gsm_status.type = 3  # 4G
        status.gsm_status.status = 2  # connected
        result = DevicesApi._parse_statuses([status])
        assert result.get("mobile_network_type") == "4G"
        assert result.get("gsm_connected") is True

    def test_gsm_status_not_connected(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "gsm_status"
        status.gsm_status.type = 1  # 2G
        status.gsm_status.status = 0  # not connected
        result = DevicesApi._parse_statuses([status])
        assert result.get("gsm_connected") is False

    def test_monitoring_active(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "monitoring"
        status.monitoring.cms_active = True
        result = DevicesApi._parse_statuses([status])
        assert result.get("monitoring_active") is True

    def test_monitoring_inactive(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "monitoring"
        status.monitoring.cms_active = False
        result = DevicesApi._parse_statuses([status])
        assert result.get("monitoring_active") is False

    def test_sim_status(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "sim_status"
        status.sim_status.sim_card_status = 1
        result = DevicesApi._parse_statuses([status])
        assert result.get("sim_status") == "OK"

    def test_always_active(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "always_active"
        result = DevicesApi._parse_statuses([status])
        assert result.get("always_active") is True

    def test_armed_in_night_mode(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "armed_in_night_mode"
        result = DevicesApi._parse_statuses([status])
        assert result.get("armed_in_night_mode") is True

    def test_delay_when_leaving(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "delay_when_leaving"
        result = DevicesApi._parse_statuses([status])
        assert result.get("delay_when_leaving") is True

    def test_lid_opened(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "lid_opened"
        result = DevicesApi._parse_statuses([status])
        assert result.get("lid_opened") is True

    def test_external_contact_broken(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "external_contact_broken"
        result = DevicesApi._parse_statuses([status])
        assert result.get("external_contact_broken") is True

    def test_external_contact_alert(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "external_contact_alert"
        result = DevicesApi._parse_statuses([status])
        assert result.get("external_contact_alert") is True

    def test_wire_input_status_alerting(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "wire_input_status"
        status.wire_input_status.is_alert = True
        status.wire_input_status.type = 1  # INTRUSION
        result = DevicesApi._parse_statuses([status])
        assert result.get("wire_input_alert") is True
        assert result.get("wire_input_alarm_type") == "intrusion"

    def test_wire_input_status_not_alerting(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "wire_input_status"
        status.wire_input_status.is_alert = False
        status.wire_input_status.type = 11  # GLASS_BREAK
        result = DevicesApi._parse_statuses([status])
        assert result.get("wire_input_alert") is False
        assert result.get("wire_input_alarm_type") == "glass_break"

    def test_wire_input_status_unspecified_type(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "wire_input_status"
        status.wire_input_status.is_alert = True
        status.wire_input_status.type = 0  # UNSPECIFIED
        result = DevicesApi._parse_statuses([status])
        assert result.get("wire_input_alert") is True
        assert result.get("wire_input_alarm_type") == "unspecified"

    def test_case_drilling_detected(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "case_drilling_detected"
        result = DevicesApi._parse_statuses([status])
        assert result.get("case_drilling") is True

    def test_anti_masking_alert(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "anti_masking_alert"
        result = DevicesApi._parse_statuses([status])
        assert result.get("anti_masking") is True

    def test_malfunction(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "malfunction"
        result = DevicesApi._parse_statuses([status])
        assert result.get("malfunction") is True

    def test_relay_stuck(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "relay_stuck"
        result = DevicesApi._parse_statuses([status])
        assert result.get("relay_stuck") is True

    def test_interference_detected(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "interference_detected"
        result = DevicesApi._parse_statuses([status])
        assert result.get("interference") is True

    def test_wifi_signal_level_status(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "wifi_signal_level_status"
        status.wifi_signal_level_status = 4
        result = DevicesApi._parse_statuses([status])
        assert result.get("wifi_signal_level") == 4

    def test_smart_bracket_unlocked(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "smart_bracket_unlocked"
        result = DevicesApi._parse_statuses([status])
        assert result.get("smart_bracket_unlocked") is True

    def test_nfc_enabled(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "nfc"
        status.nfc.enabled = True
        result = DevicesApi._parse_statuses([status])
        assert result.get("nfc_enabled") is True


class TestDevicesApiInit:
    def test_init(self) -> None:
        client = MagicMock()
        api = DevicesApi(client)
        assert api._client is client


class TestSendCommand:
    @pytest.mark.asyncio
    async def test_send_command_on_raises_not_implemented(self) -> None:
        client = MagicMock()
        api = DevicesApi(client)
        cmd = DeviceCommand.on(hub_id="h1", device_id="d1", device_type="relay", channels=[1])
        with pytest.raises(NotImplementedError):
            await api.send_command(cmd)

    @pytest.mark.asyncio
    async def test_send_command_off_raises_not_implemented(self) -> None:
        client = MagicMock()
        api = DevicesApi(client)
        cmd = DeviceCommand.off(hub_id="h1", device_id="d1", device_type="relay", channels=[1])
        with pytest.raises(NotImplementedError):
            await api.send_command(cmd)

    @pytest.mark.asyncio
    async def test_send_command_brightness_raises_not_implemented(self) -> None:
        client = MagicMock()
        api = DevicesApi(client)
        cmd = DeviceCommand.set_brightness(
            hub_id="h1",
            device_id="d1",
            device_type="light_switch_dimmer",
            brightness=50,
            channels=[1],
        )
        with pytest.raises(NotImplementedError):
            await api.send_command(cmd)


class TestGetDevicesSnapshot:
    @pytest.mark.asyncio
    async def test_get_devices_snapshot_success(self) -> None:
        mock_client = MagicMock()
        mock_channel = MagicMock()
        mock_client._get_channel.return_value = mock_channel
        mock_client._session.get_call_metadata.return_value = []

        api = DevicesApi(mock_client)

        # Build a mock device to be returned in snapshot
        mock_light_device = MagicMock()
        mock_light_device.WhichOneof.return_value = "hub_device"
        mock_light_device.hub_device.common_device.profile.id = "dev-1"
        mock_light_device.hub_device.common_device.profile.name = "Sensor"
        mock_light_device.hub_device.common_device.profile.room_id = ""
        mock_light_device.hub_device.common_device.profile.group_id = ""
        mock_light_device.hub_device.common_device.profile.malfunctions = 0
        mock_light_device.hub_device.common_device.profile.bypassed = False
        mock_light_device.hub_device.common_device.profile.states = []
        mock_light_device.hub_device.common_device.profile.statuses = []
        mock_light_device.hub_device.common_device.hub_id = "hub-1"
        mock_light_device.hub_device.common_device.object_type.WhichOneof.return_value = (
            "door_protect"
        )

        # Build the snapshot message
        mock_msg = MagicMock()
        mock_msg.HasField.side_effect = lambda field: field == "success"
        mock_msg.success.WhichOneof.return_value = "snapshot"
        mock_msg.success.snapshot.light_devices = [mock_light_device]

        # Async iterator for stream
        async def _aiter(*args: object, **kwargs: object) -> AsyncGenerator[MagicMock, None]:
            yield mock_msg

        mock_stub_instance = MagicMock()
        mock_stub_instance.execute.return_value = _aiter()
        mock_stub_class = MagicMock(return_value=mock_stub_instance)

        mock_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(StreamLightDevicesServiceStub=mock_stub_class)

        with patch.dict(
            "sys.modules",
            {
                "v3.mobilegwsvc.service.stream_light_devices.endpoint_pb2_grpc": mock_grpc_module,
                "v3.mobilegwsvc.service.stream_light_devices.request_pb2": mock_request_pb2,
                "v3.mobilegwsvc.service.stream_light_devices": MagicMock(
                    endpoint_pb2_grpc=mock_grpc_module,
                    request_pb2=mock_request_pb2,
                ),
            },
        ):
            devices = await api.get_devices_snapshot("space-1")

        assert len(devices) == 1
        assert devices[0].id == "dev-1"

    @pytest.mark.asyncio
    async def test_get_devices_snapshot_failure_message(self) -> None:
        mock_client = MagicMock()
        mock_channel = MagicMock()
        mock_client._get_channel.return_value = mock_channel
        mock_client._session.get_call_metadata.return_value = []

        api = DevicesApi(mock_client)

        # Build a failure message
        mock_msg = MagicMock()
        mock_msg.HasField.side_effect = lambda field: field == "failure"

        async def _aiter(*args: object, **kwargs: object) -> AsyncGenerator[MagicMock, None]:
            yield mock_msg

        mock_stub_instance = MagicMock()
        mock_stub_instance.execute.return_value = _aiter()
        mock_stub_class = MagicMock(return_value=mock_stub_instance)

        mock_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(StreamLightDevicesServiceStub=mock_stub_class)

        with patch.dict(
            "sys.modules",
            {
                "v3.mobilegwsvc.service.stream_light_devices.endpoint_pb2_grpc": mock_grpc_module,
                "v3.mobilegwsvc.service.stream_light_devices.request_pb2": mock_request_pb2,
                "v3.mobilegwsvc.service.stream_light_devices": MagicMock(
                    endpoint_pb2_grpc=mock_grpc_module,
                    request_pb2=mock_request_pb2,
                ),
            },
        ):
            devices = await api.get_devices_snapshot("space-1")

        assert devices == []

    @pytest.mark.asyncio
    async def test_get_devices_snapshot_update_message_ignored(self) -> None:
        """Success messages that are 'update' type (not snapshot) are not counted."""
        mock_client = MagicMock()
        mock_channel = MagicMock()
        mock_client._get_channel.return_value = mock_channel
        mock_client._session.get_call_metadata.return_value = []

        api = DevicesApi(mock_client)

        # First message is an 'update' (not snapshot), second is snapshot
        mock_msg_update = MagicMock()
        mock_msg_update.HasField.side_effect = lambda field: field == "success"
        mock_msg_update.success.WhichOneof.return_value = "update"

        mock_msg_snapshot = MagicMock()
        mock_msg_snapshot.HasField.side_effect = lambda field: field == "success"
        mock_msg_snapshot.success.WhichOneof.return_value = "snapshot"
        mock_msg_snapshot.success.snapshot.light_devices = []

        async def _aiter(*args: object, **kwargs: object) -> AsyncGenerator[MagicMock, None]:
            yield mock_msg_update
            yield mock_msg_snapshot

        mock_stub_instance = MagicMock()
        mock_stub_instance.execute.return_value = _aiter()
        mock_stub_class = MagicMock(return_value=mock_stub_instance)

        mock_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(StreamLightDevicesServiceStub=mock_stub_class)

        with patch.dict(
            "sys.modules",
            {
                "v3.mobilegwsvc.service.stream_light_devices.endpoint_pb2_grpc": mock_grpc_module,
                "v3.mobilegwsvc.service.stream_light_devices.request_pb2": mock_request_pb2,
                "v3.mobilegwsvc.service.stream_light_devices": MagicMock(
                    endpoint_pb2_grpc=mock_grpc_module,
                    request_pb2=mock_request_pb2,
                ),
            },
        ):
            devices = await api.get_devices_snapshot("space-1")

        assert devices == []


def _make_stream_patch_modules(aiter_fn: object) -> dict[str, object]:
    """Build the sys.modules patch dict for stream_light_devices."""
    mock_stub_instance = MagicMock()
    mock_stub_instance.execute.return_value = aiter_fn()
    mock_stub_class = MagicMock(return_value=mock_stub_instance)
    mock_request_pb2 = MagicMock()
    mock_grpc_module = MagicMock(StreamLightDevicesServiceStub=mock_stub_class)
    return {
        "v3.mobilegwsvc.service.stream_light_devices.endpoint_pb2_grpc": mock_grpc_module,
        "v3.mobilegwsvc.service.stream_light_devices.request_pb2": mock_request_pb2,
        "v3.mobilegwsvc.service.stream_light_devices": MagicMock(
            endpoint_pb2_grpc=mock_grpc_module,
            request_pb2=mock_request_pb2,
        ),
    }


class TestStartDeviceStream:
    """Tests for DevicesApi.start_device_stream."""

    def _make_api(self) -> DevicesApi:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []
        return DevicesApi(mock_client)

    def _make_light_device_mock(self, device_id: str = "dev-1") -> MagicMock:
        mock_light_device = MagicMock()
        mock_light_device.WhichOneof.return_value = "hub_device"
        mock_light_device.hub_device.common_device.profile.id = device_id
        mock_light_device.hub_device.common_device.profile.name = "Sensor"
        mock_light_device.hub_device.common_device.profile.room_id = ""
        mock_light_device.hub_device.common_device.profile.group_id = ""
        mock_light_device.hub_device.common_device.profile.malfunctions = 0
        mock_light_device.hub_device.common_device.profile.bypassed = False
        mock_light_device.hub_device.common_device.profile.states = []
        mock_light_device.hub_device.common_device.profile.statuses = []
        mock_light_device.hub_device.common_device.hub_id = "hub-1"
        mock_light_device.hub_device.common_device.object_type.WhichOneof.return_value = (
            "door_protect"
        )
        return mock_light_device

    @pytest.mark.asyncio
    async def test_snapshot_calls_on_devices_snapshot(self) -> None:
        """Initial snapshot triggers on_devices_snapshot callback."""
        api = self._make_api()
        mock_light_device = self._make_light_device_mock("dev-1")

        mock_msg = MagicMock()
        mock_msg.HasField.side_effect = lambda field: field == "success"
        mock_msg.success.WhichOneof.return_value = "snapshot"
        mock_msg.success.snapshot.light_devices = [mock_light_device]

        # Stream yields snapshot then stops; sleep raises CancelledError to exit the loop.
        async def _aiter() -> AsyncGenerator[MagicMock, None]:
            yield mock_msg

        snapshot_received: list[list[Device]] = []
        status_received: list[tuple[str, str, dict]] = []

        def on_snap(devices: list) -> None:
            snapshot_received.append(devices)

        def on_status(device_id: str, status_name: str, data: dict) -> None:
            status_received.append((device_id, status_name, data))

        with (
            patch.dict("sys.modules", _make_stream_patch_modules(_aiter)),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_sleep.side_effect = asyncio.CancelledError()
            task = await api.start_device_stream("space-1", on_snap, on_status)
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(task, timeout=2.0)

        assert len(snapshot_received) == 1
        assert len(snapshot_received[0]) == 1
        assert snapshot_received[0][0].id == "dev-1"
        assert status_received == []

    @pytest.mark.asyncio
    async def test_status_update_add_calls_on_status_update(self) -> None:
        """Status ADD update triggers on_status_update with correct args."""
        api = self._make_api()

        update_msg = MagicMock()
        update_msg.HasField.side_effect = lambda field: field == "success"
        update_msg.success.WhichOneof.return_value = "updates"

        single_update = MagicMock()
        single_update.WhichOneof.return_value = "status_update"
        single_update.device_id.hub_light_device_id.device_id = "dev-42"
        single_update.status_update.status.WhichOneof.return_value = "door_opened"
        single_update.status_update.update_type = 1  # ADD

        update_msg.success.updates.updates = [single_update]

        async def _aiter() -> AsyncGenerator[MagicMock, None]:
            yield update_msg

        status_received: list[tuple[str, str, dict]] = []

        def on_snap(devices: list) -> None:
            pass

        def on_status(device_id: str, status_name: str, data: dict) -> None:
            status_received.append((device_id, status_name, data))

        with (
            patch.dict("sys.modules", _make_stream_patch_modules(_aiter)),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_sleep.side_effect = asyncio.CancelledError()
            task = await api.start_device_stream("space-1", on_snap, on_status)
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(task, timeout=2.0)

        assert len(status_received) == 1
        device_id, status_name, data = status_received[0]
        assert device_id == "dev-42"
        assert status_name == "door_opened"
        assert data == {"op": 1}

    @pytest.mark.asyncio
    async def test_status_update_remove_calls_on_status_update(self) -> None:
        """Status REMOVE update triggers on_status_update with op=3."""
        api = self._make_api()

        update_msg = MagicMock()
        update_msg.HasField.side_effect = lambda field: field == "success"
        update_msg.success.WhichOneof.return_value = "updates"

        single_update = MagicMock()
        single_update.WhichOneof.return_value = "status_update"
        single_update.device_id.hub_light_device_id.device_id = "dev-99"
        single_update.status_update.status.WhichOneof.return_value = "motion_detected"
        single_update.status_update.update_type = 3  # REMOVE

        update_msg.success.updates.updates = [single_update]

        async def _aiter() -> AsyncGenerator[MagicMock, None]:
            yield update_msg

        status_received: list[tuple[str, str, dict]] = []

        def on_snap(devices: list) -> None:
            pass

        def on_status(device_id: str, status_name: str, data: dict) -> None:
            status_received.append((device_id, status_name, data))

        with (
            patch.dict("sys.modules", _make_stream_patch_modules(_aiter)),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_sleep.side_effect = asyncio.CancelledError()
            task = await api.start_device_stream("space-1", on_snap, on_status)
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(task, timeout=2.0)

        assert len(status_received) == 1
        _, _, data = status_received[0]
        assert data["op"] == 3

    @pytest.mark.asyncio
    async def test_status_update_temperature_preserves_numeric_value(self) -> None:
        """Temperature updates must forward the actual reading, not boolean True."""
        api = self._make_api()

        update_msg = MagicMock()
        update_msg.HasField.side_effect = lambda field: field == "success"
        update_msg.success.WhichOneof.return_value = "updates"

        single_update = MagicMock()
        single_update.WhichOneof.return_value = "status_update"
        single_update.device_id.hub_light_device_id.device_id = "dev-temp"
        single_update.status_update.status.WhichOneof.return_value = "temperature"
        single_update.status_update.status.temperature.value = 19
        single_update.status_update.update_type = 2  # UPDATE

        update_msg.success.updates.updates = [single_update]

        async def _aiter() -> AsyncGenerator[MagicMock, None]:
            yield update_msg

        status_received: list[tuple[str, str, dict]] = []

        def on_snap(devices: list) -> None:
            pass

        def on_status(device_id: str, status_name: str, data: dict) -> None:
            status_received.append((device_id, status_name, data))

        with (
            patch.dict("sys.modules", _make_stream_patch_modules(_aiter)),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_sleep.side_effect = asyncio.CancelledError()
            task = await api.start_device_stream("space-1", on_snap, on_status)
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(task, timeout=2.0)

        assert len(status_received) == 1
        _, status_name, data = status_received[0]
        assert status_name == "temperature"
        assert data == {"op": 2, "value": 19}

    @pytest.mark.asyncio
    async def test_snapshot_update_calls_on_devices_snapshot(self) -> None:
        """snapshot_update in Updates triggers on_devices_snapshot."""
        api = self._make_api()
        mock_light_device = self._make_light_device_mock("dev-77")

        update_msg = MagicMock()
        update_msg.HasField.side_effect = lambda field: field == "success"
        update_msg.success.WhichOneof.return_value = "updates"

        single_update = MagicMock()
        single_update.WhichOneof.return_value = "snapshot_update"
        single_update.snapshot_update.light_device = mock_light_device

        update_msg.success.updates.updates = [single_update]

        async def _aiter() -> AsyncGenerator[MagicMock, None]:
            yield update_msg

        snapshot_received: list[list] = []

        def on_snap(devices: list) -> None:
            snapshot_received.append(devices)

        def on_status(device_id: str, status_name: str, data: dict) -> None:
            pass

        with (
            patch.dict("sys.modules", _make_stream_patch_modules(_aiter)),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_sleep.side_effect = asyncio.CancelledError()
            task = await api.start_device_stream("space-1", on_snap, on_status)
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(task, timeout=2.0)

        assert len(snapshot_received) == 1
        assert snapshot_received[0][0].id == "dev-77"

    @pytest.mark.asyncio
    async def test_failure_message_reconnects(self) -> None:
        """A failure message breaks the inner loop and triggers a reconnect sleep."""
        api = self._make_api()

        failure_msg = MagicMock()
        failure_msg.HasField.side_effect = lambda field: field == "failure"

        call_count = 0

        async def _aiter() -> AsyncGenerator[MagicMock, None]:
            nonlocal call_count
            call_count += 1
            yield failure_msg

        def on_snap(devices: list) -> None:
            pass

        def on_status(device_id: str, status_name: str, data: dict) -> None:
            pass

        with (
            patch.dict("sys.modules", _make_stream_patch_modules(_aiter)),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            # Make the second sleep raise CancelledError to stop the loop
            mock_sleep.side_effect = [None, asyncio.CancelledError()]
            task = await api.start_device_stream("space-1", on_snap, on_status)
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(task, timeout=5.0)

        # At least one reconnect sleep occurred
        assert mock_sleep.call_count >= 1

    @pytest.mark.asyncio
    async def test_returns_asyncio_task(self) -> None:
        """start_device_stream returns a running asyncio.Task."""
        api = self._make_api()

        async def _aiter() -> AsyncGenerator[MagicMock, None]:
            # Yield nothing; infinite loop will sleep
            return
            yield  # make this an async generator

        def on_snap(devices: list) -> None:
            pass

        def on_status(device_id: str, status_name: str, data: dict) -> None:
            pass

        with (
            patch.dict("sys.modules", _make_stream_patch_modules(_aiter)),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_sleep.side_effect = asyncio.CancelledError()
            task = await api.start_device_stream("space-1", on_snap, on_status)
            assert isinstance(task, asyncio.Task)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


class TestProtoHelpers:
    """Tests for raw protobuf encoding helpers."""

    def test_encode_string_field(self) -> None:
        result = _encode_string_field(1, "hello")
        # tag = (1 << 3) | 2 = 0x0a, length = 5, then "hello"
        assert result == b"\x0a\x05hello"

    def test_encode_string_field_field2(self) -> None:
        result = _encode_string_field(2, "abc")
        # tag = (2 << 3) | 2 = 0x12, length = 3
        assert result == b"\x12\x03abc"

    def test_encode_varint_field(self) -> None:
        result = _encode_varint_field(3, 2)
        # tag = (3 << 3) | 0 = 0x18, value = 2
        assert result == b"\x18\x02"

    def test_encode_varint_field_value_zero(self) -> None:
        result = _encode_varint_field(3, 0)
        assert result == b"\x18\x00"


class TestCapturePhotoV2:
    """Tests for DevicesApi.capture_photo using v2 PhotoOnDemandService."""

    def _make_api(self) -> DevicesApi:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []
        return DevicesApi(mock_client)

    @pytest.mark.asyncio
    async def test_capture_photo_success_returns_device_id(self) -> None:
        """Success response (0x0a prefix) returns device_id."""
        api = self._make_api()

        mock_method = AsyncMock(return_value=b"\x0a\x00")
        api._client._get_channel.return_value.unary_unary.return_value = mock_method

        result = await api.capture_photo("hub-1", "dev-1", "motion_cam")

        assert result == "dev-1"

    @pytest.mark.asyncio
    async def test_capture_photo_failure_response_returns_none(self) -> None:
        """Non-success response (0x12 prefix = error field) returns None."""
        api = self._make_api()

        mock_method = AsyncMock(return_value=b"\x12\x05error")
        api._client._get_channel.return_value.unary_unary.return_value = mock_method

        result = await api.capture_photo("hub-1", "dev-1", "motion_cam")

        assert result is None

    @pytest.mark.asyncio
    async def test_capture_photo_empty_response_returns_none(self) -> None:
        """Empty response returns None."""
        api = self._make_api()

        mock_method = AsyncMock(return_value=b"")
        api._client._get_channel.return_value.unary_unary.return_value = mock_method

        result = await api.capture_photo("hub-1", "dev-1", "motion_cam_outdoor")

        assert result is None

    @pytest.mark.asyncio
    async def test_capture_photo_exception_returns_none(self) -> None:
        """gRPC exception returns None without raising."""
        api = self._make_api()

        mock_method = AsyncMock(side_effect=Exception("gRPC error"))
        api._client._get_channel.return_value.unary_unary.return_value = mock_method

        result = await api.capture_photo("hub-1", "dev-1", "motion_cam_phod")

        assert result is None

    @pytest.mark.asyncio
    async def test_capture_photo_uses_correct_grpc_path(self) -> None:
        """Correct v2 gRPC service path is used."""
        api = self._make_api()

        mock_method = AsyncMock(return_value=b"\x0a\x00")
        mock_channel = api._client._get_channel.return_value
        mock_channel.unary_unary.return_value = mock_method

        await api.capture_photo("hub-1", "dev-1", "motion_cam")

        called_path = mock_channel.unary_unary.call_args[0][0]
        assert "PhotoOnDemandService/capturePhoto" in called_path
        assert "v2" in called_path

    @pytest.mark.asyncio
    async def test_capture_photo_device_type_mapping(self) -> None:
        """Outdoor cameras map to v2 device type 2."""
        api = self._make_api()

        captured_request: list[bytes] = []

        async def _capture(request_bytes: bytes, **kwargs: object) -> bytes:
            captured_request.append(request_bytes)
            return b"\x0a\x00"

        mock_channel = api._client._get_channel.return_value
        mock_channel.unary_unary.return_value = _capture

        await api.capture_photo("hub-1", "dev-1", "motion_cam_outdoor")

        # The request bytes should contain varint 2 for outdoor type
        request = captured_request[0]
        # Find field 3 (varint): tag = (3<<3)|0 = 0x18
        idx = request.find(b"\x18")
        assert idx != -1
        assert request[idx + 1] == 2  # device type 2 for outdoor
