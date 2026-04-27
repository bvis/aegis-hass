"""Devices API: streaming, parsing, and commands."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from custom_components.aegis_ajax.api.models import BatteryInfo, Device, DeviceCommand
from custom_components.aegis_ajax.const import DeviceState

if TYPE_CHECKING:
    from collections.abc import Callable

    from custom_components.aegis_ajax.api.client import AjaxGrpcClient

_LOGGER = logging.getLogger(__name__)

_STREAM_LIGHT_DEVICES = (
    "/systems.ajax.api.ecosystem.v3.mobilegwsvc.service"
    ".stream_light_devices.StreamLightDevicesService/execute"
)
_DEVICE_ON = (
    "/systems.ajax.api.ecosystem.v3.mobilegwsvc.service"
    ".device_command_device_on.DeviceCommandDeviceOnService/execute"
)
_DEVICE_OFF = (
    "/systems.ajax.api.ecosystem.v3.mobilegwsvc.service"
    ".device_command_device_off.DeviceCommandDeviceOffService/execute"
)
_DEVICE_BRIGHTNESS = (
    "/systems.ajax.api.ecosystem.v3.mobilegwsvc.service"
    ".device_command_brightness.DeviceCommandBrightnessService/execute"
)


def _encode_string_field(field_number: int, value: str) -> bytes:
    """Encode a protobuf string field (wire type 2)."""
    tag = (field_number << 3) | 2
    encoded = value.encode("utf-8")
    return bytes([tag, len(encoded)]) + encoded


def _encode_varint_field(field_number: int, value: int) -> bytes:
    """Encode a protobuf varint field (wire type 0)."""
    tag = (field_number << 3) | 0
    varint = bytearray()
    while value > 0x7F:
        varint.append((value & 0x7F) | 0x80)
        value >>= 7
    varint.append(value & 0x7F)
    return bytes([tag]) + bytes(varint)


_GSM_TYPE_MAP: dict[int, str] = {0: "Unknown", 1: "2G", 2: "3G", 3: "4G"}

_SIGNAL_LEVEL_MAP: dict[int, str] = {
    0: "Unknown",
    1: "No signal",
    2: "Weak",
    3: "Normal",
    4: "Strong",
    5: "Disconnected",
}

_SIM_STATUS_MAP: dict[int, str] = {
    0: "Unknown",
    1: "OK",
    2: "Missing",
    3: "Malfunction",
    4: "Locked",
    5: "Unknown",
}

_ALARM_TYPE_NAMES: dict[int, str] = {
    0: "unspecified",
    1: "intrusion",
    2: "fire",
    3: "medical",
    4: "panic",
    5: "gas",
    6: "tamper",
    7: "malfunction",
    8: "leak",
    9: "service",
    10: "key_arm",
    11: "glass_break",
    12: "high_temperature",
    13: "low_temperature",
    14: "masking",
    15: "duress_code",
    16: "vibration",
    17: "blocking_element",
    18: "bolt_contact",
}


_STATE_MAP: dict[int, DeviceState] = {
    0: DeviceState.ONLINE,
    1: DeviceState.LOCKED,
    2: DeviceState.SUSPENDED,
    3: DeviceState.UNKNOWN,
    4: DeviceState.UNKNOWN,
    5: DeviceState.ADDING,
    6: DeviceState.ADDING,
    7: DeviceState.BATTERY_SAVING,
    8: DeviceState.NOT_MIGRATED,
    9: DeviceState.OFFLINE,
    10: DeviceState.UPDATING,
    11: DeviceState.WALK_TEST,
}


class DevicesApi:
    """API operations for devices."""

    def __init__(self, client: AjaxGrpcClient) -> None:
        self._client = client

    @staticmethod
    def _parse_device_state(states: Any) -> DeviceState:  # noqa: ANN401
        if not states:
            return DeviceState.ONLINE
        priority = {
            DeviceState.OFFLINE: 100,
            DeviceState.LOCKED: 90,
            DeviceState.SUSPENDED: 80,
            DeviceState.UPDATING: 70,
            DeviceState.BATTERY_SAVING: 60,
            DeviceState.WALK_TEST: 50,
            DeviceState.ADDING: 40,
            DeviceState.NOT_MIGRATED: 30,
            DeviceState.UNKNOWN: 20,
            DeviceState.ONLINE: 0,
        }
        worst = DeviceState.ONLINE
        for s in states:
            val = s if isinstance(s, int) else int(s)
            mapped = _STATE_MAP.get(val, DeviceState.UNKNOWN)
            if priority.get(mapped, 0) > priority.get(worst, 0):
                worst = mapped
        return worst

    @staticmethod
    def _parse_battery(statuses: Any) -> BatteryInfo | None:  # noqa: ANN401
        for status in statuses:
            which = status.WhichOneof("status") if hasattr(status, "WhichOneof") else None
            if which == "battery":
                return BatteryInfo(
                    level=status.battery.charge_level_percentage,
                    is_low=status.battery.battery_state not in (0, 1),  # 0=UNSPECIFIED, 1=OK
                )
        return None

    @staticmethod
    def _parse_statuses(statuses: Any) -> dict[str, Any]:  # noqa: ANN401
        result: dict[str, Any] = {}
        for status in statuses:
            which = status.WhichOneof("status") if hasattr(status, "WhichOneof") else None
            if which is None:
                continue
            if which == "door_opened":
                result["door_opened"] = True
            elif which == "motion_detected":
                result["motion_detected"] = True
                if hasattr(status.motion_detected, "detected_at"):
                    result["motion_detected_at"] = status.motion_detected.detected_at.seconds
            elif which == "smoke_detected":
                result["smoke_detected"] = True
            elif which == "co_level_detected":
                result["co_detected"] = True
            elif which == "high_temperature_detected":
                result["high_temperature"] = True
            elif which == "leak_detected":
                result["leak_detected"] = True
            elif which == "glass_break_detected":
                result["glass_break"] = True
            elif which == "vibration_detected":
                result["vibration"] = True
            elif which == "tamper":
                result["tamper"] = True
            elif which == "temperature":
                result["temperature"] = status.temperature.value
            elif which == "life_quality":
                lq = status.life_quality
                if hasattr(lq, "actual_temperature"):
                    result["temperature"] = lq.actual_temperature
                if hasattr(lq, "actual_humidity"):
                    result["humidity"] = lq.actual_humidity
                if hasattr(lq, "actual_co2"):
                    result["co2"] = lq.actual_co2
            elif which == "signal_strength":
                signal_int = int(status.signal_strength.device_signal_level)
                result["signal_strength"] = _SIGNAL_LEVEL_MAP.get(
                    signal_int, f"Unknown ({signal_int})"
                )
            elif which == "gsm_status":
                gsm = status.gsm_status
                gsm_int = int(gsm.type) if hasattr(gsm, "type") else 0
                result["mobile_network_type"] = _GSM_TYPE_MAP.get(gsm_int, "Unknown")
                result["gsm_connected"] = int(gsm.status) == 2 if hasattr(gsm, "status") else False
            elif which == "monitoring":
                result["monitoring_active"] = (
                    bool(status.monitoring.cms_active)
                    if hasattr(status.monitoring, "cms_active")
                    else False
                )
            elif which == "sim_status":
                sim_int = (
                    int(status.sim_status.sim_card_status)
                    if hasattr(status.sim_status, "sim_card_status")
                    else 0
                )
                result["sim_status"] = _SIM_STATUS_MAP.get(sim_int, f"Unknown ({sim_int})")
            elif which == "always_active":
                result["always_active"] = True
            elif which == "armed_in_night_mode":
                result["armed_in_night_mode"] = True
            elif which == "delay_when_leaving":
                result["delay_when_leaving"] = True
            elif which == "lid_opened":
                result["lid_opened"] = True
            elif which == "nfc":
                result["nfc_enabled"] = (
                    bool(status.nfc.enabled) if hasattr(status.nfc, "enabled") else True
                )
            elif which == "external_contact_broken":
                result["external_contact_broken"] = True
            elif which == "external_contact_alert":
                result["external_contact_alert"] = True
            elif which == "wire_input_status":
                ws = status.wire_input_status
                result["wire_input_alert"] = bool(ws.is_alert) if hasattr(ws, "is_alert") else True
                if hasattr(ws, "type"):
                    result["wire_input_alarm_type"] = _ALARM_TYPE_NAMES.get(
                        int(ws.type), "unspecified"
                    )
            elif which == "case_drilling_detected":
                result["case_drilling"] = True
            elif which == "anti_masking_alert":
                result["anti_masking"] = True
            elif which == "smart_bracket_unlocked":
                result["smart_bracket_unlocked"] = True
            elif which == "malfunction":
                result["malfunction"] = True
            elif which == "relay_stuck":
                result["relay_stuck"] = True
            elif which == "interference_detected":
                result["interference"] = True
            elif which == "wifi_signal_level_status":
                result["wifi_signal_level"] = (
                    int(status.wifi_signal_level_status)
                    if hasattr(status, "wifi_signal_level_status")
                    else 0
                )
        return result

    @staticmethod
    def parse_device(proto_light_device: Any) -> Device | None:  # noqa: ANN401
        device_kind = proto_light_device.WhichOneof("device")
        if device_kind != "hub_device":
            _LOGGER.debug("Skipping non-hub device type: %s", device_kind)
            return None

        hub_dev = proto_light_device.hub_device
        common = hub_dev.common_device
        profile = common.profile

        device_type = common.object_type.WhichOneof("type") or "unknown"

        return Device(
            id=profile.id,
            hub_id=common.hub_id,
            name=profile.name,
            device_type=device_type,
            room_id=profile.room_id if profile.room_id else None,
            group_id=profile.group_id if profile.group_id else None,
            state=DevicesApi._parse_device_state(profile.states),
            malfunctions=profile.malfunctions,
            bypassed=profile.bypassed,
            statuses=DevicesApi._parse_statuses(profile.statuses),
            battery=DevicesApi._parse_battery(profile.statuses),
        )

    async def get_devices_snapshot(self, space_id: str) -> list[Device]:
        """Get initial snapshot of all devices in a space."""
        from v3.mobilegwsvc.service.stream_light_devices import (  # noqa: PLC0415
            endpoint_pb2_grpc,
            request_pb2,
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = endpoint_pb2_grpc.StreamLightDevicesServiceStub(channel)

        request = request_pb2.StreamLightDevicesRequest(space_id=space_id)
        stream = stub.execute(request, metadata=metadata, timeout=30)

        devices: list[Device] = []
        async for msg in stream:
            if msg.HasField("success"):
                which = msg.success.WhichOneof("success")
                if which == "snapshot":
                    for light_device in msg.success.snapshot.light_devices:
                        device = self.parse_device(light_device)
                        if device is not None:
                            devices.append(device)
                    break  # Got snapshot, stop
            elif msg.HasField("failure"):
                _LOGGER.error("Device stream failed: %s", msg.failure)
                break

        return devices

    async def start_device_stream(
        self,
        space_id: str,
        on_devices_snapshot: Callable[[list[Device]], None],
        on_status_update: Callable[[str, str, dict[str, Any]], None],
    ) -> asyncio.Task[None]:
        """Start persistent gRPC stream for real-time device updates.

        Returns a background asyncio.Task that keeps the stream open indefinitely,
        reconnecting with exponential backoff on errors.

        on_devices_snapshot(devices) is called with the initial snapshot and on
        full snapshot_update events.

        on_status_update(device_id, status_name, data) is called for each status
        change, where data contains {"op": int} (1=ADD, 2=UPDATE, 3=REMOVE).
        """

        async def _run_stream() -> None:
            from v3.mobilegwsvc.service.stream_light_devices import (  # noqa: PLC0415
                endpoint_pb2_grpc,
                request_pb2,
            )

            backoff = 5.0
            while True:
                try:
                    channel = self._client._get_channel()
                    metadata = self._client._session.get_call_metadata()
                    stub = endpoint_pb2_grpc.StreamLightDevicesServiceStub(channel)
                    request = request_pb2.StreamLightDevicesRequest(space_id=space_id)
                    # timeout=None keeps the stream open indefinitely
                    stream = stub.execute(request, metadata=metadata, timeout=None)

                    async for msg in stream:
                        if msg.HasField("success"):
                            which = msg.success.WhichOneof("success")
                            if which == "snapshot":
                                devices: list[Device] = []
                                for light_device in msg.success.snapshot.light_devices:
                                    device = self.parse_device(light_device)
                                    if device is not None:
                                        devices.append(device)
                                on_devices_snapshot(devices)
                                # Reset backoff after successful snapshot
                                backoff = 5.0
                            elif which == "updates":
                                for update in msg.success.updates.updates:
                                    update_kind = update.WhichOneof("update")
                                    if update_kind == "status_update":
                                        try:
                                            device_id = (
                                                update.device_id.hub_light_device_id.device_id
                                            )
                                        except AttributeError:
                                            _LOGGER.debug("Could not extract device_id from update")
                                            continue

                                        status = update.status_update.status
                                        status_name = status.WhichOneof("status")
                                        if status_name is None:
                                            continue

                                        op = int(update.status_update.update_type)
                                        payload: dict[str, Any] = {"op": op}
                                        if status_name == "wire_input_status":
                                            ws = status.wire_input_status
                                            if hasattr(ws, "is_alert"):
                                                payload["is_alert"] = bool(ws.is_alert)
                                            if hasattr(ws, "type"):
                                                payload["alarm_type"] = _ALARM_TYPE_NAMES.get(
                                                    int(ws.type), "unspecified"
                                                )
                                        elif status_name == "temperature":
                                            payload["value"] = status.temperature.value
                                        elif status_name == "life_quality":
                                            lq = status.life_quality
                                            values: dict[str, Any] = {}
                                            if hasattr(lq, "actual_temperature"):
                                                values["temperature"] = lq.actual_temperature
                                            if hasattr(lq, "actual_humidity"):
                                                values["humidity"] = lq.actual_humidity
                                            if hasattr(lq, "actual_co2"):
                                                values["co2"] = lq.actual_co2
                                            if values:
                                                payload["values"] = values
                                        elif status_name == "signal_strength":
                                            signal_int = int(
                                                status.signal_strength.device_signal_level
                                            )
                                            payload["value"] = _SIGNAL_LEVEL_MAP.get(
                                                signal_int, f"Unknown ({signal_int})"
                                            )
                                        elif status_name == "gsm_status":
                                            gsm = status.gsm_status
                                            gsm_int = int(gsm.type) if hasattr(gsm, "type") else 0
                                            payload["values"] = {
                                                "mobile_network_type": _GSM_TYPE_MAP.get(
                                                    gsm_int, "Unknown"
                                                ),
                                                "gsm_connected": (
                                                    int(gsm.status) == 2
                                                    if hasattr(gsm, "status")
                                                    else False
                                                ),
                                            }
                                        elif status_name == "monitoring":
                                            payload["value"] = (
                                                bool(status.monitoring.cms_active)
                                                if hasattr(status.monitoring, "cms_active")
                                                else False
                                            )
                                        elif status_name == "sim_status":
                                            sim_int = (
                                                int(status.sim_status.sim_card_status)
                                                if hasattr(status.sim_status, "sim_card_status")
                                                else 0
                                            )
                                            payload["value"] = _SIM_STATUS_MAP.get(
                                                sim_int, f"Unknown ({sim_int})"
                                            )
                                        elif status_name == "nfc":
                                            payload["value"] = (
                                                bool(status.nfc.enabled)
                                                if hasattr(status.nfc, "enabled")
                                                else True
                                            )
                                        elif status_name == "wifi_signal_level_status":
                                            payload["value"] = int(status.wifi_signal_level_status)

                                        on_status_update(
                                            device_id,
                                            status_name,
                                            payload,
                                        )
                                    elif update_kind == "snapshot_update":
                                        device = self.parse_device(
                                            update.snapshot_update.light_device
                                        )
                                        if device is not None:
                                            on_devices_snapshot([device])
                        elif msg.HasField("failure"):
                            _LOGGER.error(
                                "Device stream failure for space %s: %s",
                                space_id,
                                msg.failure,
                            )
                            break

                except asyncio.CancelledError:
                    _LOGGER.debug("Device stream task cancelled for space %s", space_id)
                    return
                except Exception:
                    _LOGGER.exception(
                        "Device stream error for space %s, reconnecting in %.0fs",
                        space_id,
                        backoff,
                    )

                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

        task: asyncio.Task[None] = asyncio.create_task(_run_stream())
        return task

    async def send_command(self, command: DeviceCommand) -> None:
        raise NotImplementedError(
            f"Device commands not yet implemented (action={command.action}, "
            f"device={command.device_id})"
        )

    async def capture_photo(self, hub_id: str, device_id: str, device_type: str) -> str | None:
        """Capture a photo using v2 PhotoOnDemandService.

        Returns device_id as a signal that capture was triggered successfully,
        or None on failure. The actual photo URL is delivered via FCM push.
        """
        # Map device_type to v2 DeviceType enum
        device_type_map = {
            "motion_cam": 1,
            "motion_cam_phod": 1,
            "motion_cam_outdoor": 2,
            "motion_cam_outdoor_phod": 2,
            "motion_cam_fibra": 3,
            "motion_cam_fibra_base": 3,
        }
        v2_device_type = device_type_map.get(device_type, 1)

        # Build raw protobuf request bytes
        # Field 1: hub_id (string), Field 2: device_id (string), Field 3: device_type (varint)
        request_bytes = (
            _encode_string_field(1, hub_id)
            + _encode_string_field(2, device_id)
            + _encode_varint_field(3, v2_device_type)
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()

        method = channel.unary_unary(
            "/systems.ajax.mobile.v2.service.hub.company.media.PhotoOnDemandService/capturePhoto",
            request_serializer=lambda x: x,
            response_deserializer=lambda x: x,
        )

        try:
            raw_response = await method(request_bytes, metadata=metadata, timeout=30)
            # Check response: 0x0a = field 1 (success), 0x12 = field 2 (error)
            if raw_response and raw_response[0:1] == b"\x0a":
                _LOGGER.debug("Photo capture triggered for %s", device_id)
                return device_id
            elif raw_response and b"ALREADY_PERFORMED" in raw_response:
                # Hub already took the photo — treat as success
                _LOGGER.debug("Photo already captured for %s, proceeding", device_id)
                return device_id
            else:
                _LOGGER.debug(
                    "Photo capture failed for %s: response=%s",
                    device_id,
                    raw_response.hex() if raw_response else "empty",
                )
                return None
        except Exception:
            _LOGGER.exception("Error capturing photo for %s", device_id)
            return None
