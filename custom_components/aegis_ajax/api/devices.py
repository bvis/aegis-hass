"""Devices API: streaming, parsing, and commands."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from custom_components.aegis_ajax.api import devices_parser
from custom_components.aegis_ajax.api.devices_parser import (
    _ALARM_TYPE_NAMES,
    _GSM_TYPE_MAP,
    _SIGNAL_LEVEL_MAP,
    _SIM_STATUS_MAP,
    _SMART_LOCK_STATE_MAP,
    _TINY_PROTO_THRESHOLD,
    _decode_proto_wire_shape,
    _redact_proto_bytes_to_hex,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from custom_components.aegis_ajax.api.client import AjaxGrpcClient
    from custom_components.aegis_ajax.api.models import BatteryInfo, Device, DeviceCommand
    from custom_components.aegis_ajax.const import DeviceState

_LOGGER = logging.getLogger(__name__)


class SmartLockError(Exception):
    """Raised when a SwitchSmartLockService call fails."""


class DeviceCommandError(Exception):
    """Raised when a DeviceCommand* gRPC call fails or is not supported.

    `reason` carries the server's failure-oneof case (e.g. `permission_denied`,
    `hub_offline`) when the hub rejected an otherwise well-formed command, so
    callers can map it to a clear, user-facing message. It is `None` for
    client-side failures (unsupported device type, etc.).
    """

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason


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


# SwitchSmartLockRequest.Action enum — UNLOCK=1, LOCK=2, UNLATCH=3.
SMART_LOCK_ACTION_LOCK = 2
SMART_LOCK_ACTION_UNLOCK = 1
SMART_LOCK_ACTION_UNLATCH = 3


def _build_object_type(device_type: str) -> Any:  # noqa: ANN401
    """Construct an `ObjectType` v2 proto with the matching `type` oneof set.

    `DeviceCommandDevice{On,Off,Brightness}Request.device_type` is an
    `ObjectType` message whose `type` oneof is selected per device family
    (relay / wall_switch / socket_* / light_switch_* / etc.). Each inner
    case is an empty marker message — `SetInParent()` marks the oneof
    case as present without setting any fields. The accepted strings
    mirror the WhichOneof("type") return values that `parse_device`
    already produces from the snapshot, so a `Device.device_type` round-
    trips back into a valid request without further mapping.
    """
    from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels import (  # noqa: PLC0415
        object_type_pb2,
    )

    obj = object_type_pb2.ObjectType()
    if not hasattr(obj, device_type):
        raise DeviceCommandError(f"Unsupported device type for command: {device_type}")
    getattr(obj, device_type).SetInParent()
    return obj


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


class DevicesApi:
    """API operations for devices."""

    def __init__(self, client: AjaxGrpcClient) -> None:
        self._client = client

    # Parsing delegators. The proto-to-Device logic lives in the pure,
    # client-free `devices_parser` module; these thin forwarders preserve the
    # historical `DevicesApi.parse_device` / `DevicesApi._parse_*` call surface
    # relied on by the coordinator and the test suite.

    @staticmethod
    def parse_device(proto_light_device: Any) -> Device | None:  # noqa: ANN401
        return devices_parser.parse_device(proto_light_device)

    @staticmethod
    def _parse_device_state(states: Any) -> DeviceState:  # noqa: ANN401
        return devices_parser._parse_device_state(states)

    @staticmethod
    def _parse_battery(statuses: Any) -> BatteryInfo | None:  # noqa: ANN401
        return devices_parser._parse_battery(statuses)

    @staticmethod
    def _parse_statuses(statuses: Any) -> dict[str, Any]:  # noqa: ANN401
        return devices_parser._parse_statuses(statuses)

    @staticmethod
    def _parse_spread_properties(hub_dev: Any) -> dict[str, Any]:  # noqa: ANN401
        return devices_parser._parse_spread_properties(hub_dev)

    @staticmethod
    def _parse_hub_device(hub_dev: Any) -> Device | None:  # noqa: ANN401
        return devices_parser._parse_hub_device(hub_dev)

    @staticmethod
    def _parse_video_edge_channel(channel: Any) -> Device | None:  # noqa: ANN401
        return devices_parser._parse_video_edge_channel(channel)

    @staticmethod
    def _dedupe_video_doorbells(devices: list[Device]) -> list[Device]:
        return devices_parser._dedupe_video_doorbells(devices)

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

        return self._dedupe_video_doorbells(devices)

    def _handle_update(
        self,
        update: Any,  # noqa: ANN401
        on_devices_snapshot: Callable[[list[Device]], None],
        on_status_update: Callable[[str, str, dict[str, Any]], None],
    ) -> None:
        """Dispatch a single LightDeviceUpdate.

        Extracted out of the stream loop so the caller can wrap each
        update in try/except — one bad update must not kill the inner
        async-for loop (#119).
        """
        update_kind = update.WhichOneof("update")
        if update_kind == "status_update":
            try:
                device_id = update.device_id.hub_light_device_id.device_id
            except AttributeError:
                _LOGGER.debug("Could not extract device_id from update")
                return

            status = update.status_update.status
            status_name = status.WhichOneof("status")
            if status_name is None:
                # The proto carried a `status` oneof case our compiled
                # `.proto` doesn't know — the same pattern as the
                # unknown-LightDevice oneof case `parse_device` probes
                # (#179, beta.6). When DEBUG is on, surface the inner
                # `status` proto's wire-shape + bytes so the new field
                # number is identifiable from a capture. Tiny protos
                # bypass redaction for the same reason as `parse_device`:
                # a ≤ 16-byte envelope has no room for PII.
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    try:
                        raw = status.SerializeToString()
                    except Exception:  # noqa: BLE001
                        return
                    if raw:
                        bytes_str = (
                            raw.hex()
                            if len(raw) <= _TINY_PROTO_THRESHOLD
                            else _redact_proto_bytes_to_hex(raw)
                        )
                        _LOGGER.debug(
                            "Unsupported LightDeviceStatus on device %s (%db) "
                            "wire-shape: %s, bytes: %s",
                            device_id,
                            len(raw),
                            _decode_proto_wire_shape(raw),
                            bytes_str,
                        )
                return

            op = int(update.status_update.update_type)
            payload: dict[str, Any] = {"op": op}
            if status_name in ("wire_input_status", "transmitter_status"):
                sub = getattr(status, status_name)
                if hasattr(sub, "is_alert"):
                    payload["is_alert"] = bool(sub.is_alert)
                if hasattr(sub, "type"):
                    payload["alarm_type"] = _ALARM_TYPE_NAMES.get(int(sub.type), "unspecified")
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
                signal_int = int(status.signal_strength.device_signal_level)
                payload["value"] = _SIGNAL_LEVEL_MAP.get(signal_int, f"Unknown ({signal_int})")
            elif status_name == "gsm_status":
                gsm = status.gsm_status
                gsm_int = int(gsm.type) if hasattr(gsm, "type") else 0
                payload["values"] = {
                    "mobile_network_type": _GSM_TYPE_MAP.get(gsm_int, "Unknown"),
                    "gsm_connected": (int(gsm.status) == 2 if hasattr(gsm, "status") else False),
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
                payload["value"] = _SIM_STATUS_MAP.get(sim_int, f"Unknown ({sim_int})")
            elif status_name == "nfc":
                payload["value"] = (
                    bool(status.nfc.enabled) if hasattr(status.nfc, "enabled") else True
                )
            elif status_name == "wifi_signal_level_status":
                # Sub-message wrapping the enum, not a plain int (#119).
                sub = getattr(status, "wifi_signal_level_status", None)
                payload["value"] = (
                    int(getattr(sub, "wifi_signal_level", 0)) if sub is not None else 0
                )
            elif status_name == "smart_lock":
                payload["value"] = _SMART_LOCK_STATE_MAP.get(int(status.smart_lock), "unknown")

            on_status_update(device_id, status_name, payload)
        elif update_kind == "snapshot_update":
            device = self.parse_device(update.snapshot_update.light_device)
            if device is not None:
                on_devices_snapshot([device])

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
                                    # Per-device guard: one bad LightDevice
                                    # (latent parser bug exposed by a new
                                    # oneof case — see #119) must not kill
                                    # the snapshot or trigger reconnect.
                                    try:
                                        device = self.parse_device(light_device)
                                    except Exception:  # noqa: BLE001
                                        _LOGGER.warning(
                                            "Skipping device in snapshot for space %s "
                                            "due to parse error",
                                            space_id,
                                            exc_info=True,
                                        )
                                        continue
                                    if device is not None:
                                        devices.append(device)
                                on_devices_snapshot(self._dedupe_video_doorbells(devices))
                                # Reset backoff after successful snapshot
                                backoff = 5.0
                            elif which == "updates":
                                for update in msg.success.updates.updates:
                                    # Per-update guard: one malformed update
                                    # (bad sub-message shape, unknown enum,
                                    # etc.) must not kill the inner loop.
                                    # See #119 hardening notes.
                                    try:
                                        self._handle_update(
                                            update, on_devices_snapshot, on_status_update
                                        )
                                    except Exception:  # noqa: BLE001
                                        _LOGGER.warning(
                                            "Skipping device update for space %s "
                                            "due to parse error",
                                            space_id,
                                            exc_info=True,
                                        )
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
        """Dispatch a device command to the right v3 DeviceCommand* service.

        action="on" / "off" → DeviceCommandDeviceOn / Off (relays, sockets,
        wall/light switches), action="brightness" → DeviceCommandBrightness
        (LightSwitch Dimmer). Raises DeviceCommandError on unsupported
        action / device_type or on a gRPC failure response.
        """
        if command.action == "on":
            await self._device_on(command)
        elif command.action == "off":
            await self._device_off(command)
        elif command.action == "brightness":
            await self._device_brightness(command)
        elif command.action == "bypass":
            await self._device_bypass(command)
        else:
            raise DeviceCommandError(f"Unknown device command action: {command.action}")

    async def _device_bypass(self, command: DeviceCommand) -> None:
        """Deactivate (bypass) or reactivate a device via DeviceCommandDeviceBypass.

        `bypass_enable=True` → permanent (engineering) whole-device
        deactivation, matching the `bypassed` flag the snapshot reports;
        `False` → clear the bypass (`BYPASS_UNSPECIFIED`).
        """
        from v3.mobilegwsvc.service.device_command_device_bypass import (  # noqa: PLC0415
            endpoint_pb2_grpc,
            request_pb2,
        )

        bypass_type = (
            request_pb2.DeviceCommandDeviceBypassRequest.BYPASS_ENGINEERING_DISABLE
            if command.bypass_enable
            else request_pb2.DeviceCommandDeviceBypassRequest.BYPASS_UNSPECIFIED
        )
        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = endpoint_pb2_grpc.DeviceCommandDeviceBypassServiceStub(channel)
        request = request_pb2.DeviceCommandDeviceBypassRequest(
            hub_id=command.hub_id,
            device_id=command.device_id,
            object_type=_build_object_type(command.device_type),
            bypass_type=bypass_type,
        )
        response = await stub.execute(request, metadata=metadata, timeout=15)
        if response.HasField("failure"):
            error = response.failure.WhichOneof("error") or "unknown"
            raise DeviceCommandError(f"bypass: {error}", reason=error)
        _LOGGER.debug("Device %s bypass=%s OK", command.device_id, bool(command.bypass_enable))

    async def _device_on(self, command: DeviceCommand) -> None:
        from v3.mobilegwsvc.service.device_command_device_on import (  # noqa: PLC0415
            endpoint_pb2_grpc,
            request_pb2,
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = endpoint_pb2_grpc.DeviceCommandDeviceOnServiceStub(channel)
        request = request_pb2.DeviceCommandDeviceOnRequest(
            hub_id=command.hub_id,
            device_id=command.device_id,
            device_type=_build_object_type(command.device_type),
            channels=command.channels or [1],
        )
        response = await stub.execute(request, metadata=metadata, timeout=15)
        if response.HasField("failure"):
            error = response.failure.WhichOneof("error") or "unknown"
            raise DeviceCommandError(f"on: {error}", reason=error)
        _LOGGER.debug("Device %s on (channels=%s) OK", command.device_id, command.channels)

    async def _device_off(self, command: DeviceCommand) -> None:
        from v3.mobilegwsvc.service.device_command_device_off import (  # noqa: PLC0415
            endpoint_pb2_grpc,
            request_pb2,
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = endpoint_pb2_grpc.DeviceCommandDeviceOffServiceStub(channel)
        request = request_pb2.DeviceCommandDeviceOffRequest(
            hub_id=command.hub_id,
            device_id=command.device_id,
            device_type=_build_object_type(command.device_type),
            channels=command.channels or [1],
        )
        response = await stub.execute(request, metadata=metadata, timeout=15)
        if response.HasField("failure"):
            error = response.failure.WhichOneof("error") or "unknown"
            raise DeviceCommandError(f"off: {error}", reason=error)
        _LOGGER.debug("Device %s off (channels=%s) OK", command.device_id, command.channels)

    async def _device_brightness(self, command: DeviceCommand) -> None:
        from v3.mobilegwsvc.service.device_command_brightness import (  # noqa: PLC0415
            endpoint_pb2_grpc,
            request_pb2,
        )

        if command.brightness is None:
            raise DeviceCommandError("brightness command requires a brightness value")

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = endpoint_pb2_grpc.DeviceCommandBrightnessServiceStub(channel)
        # BRIGHTNESS_TYPE_ABSOLUTE=2 — the percentage we send is the new
        # absolute target, not a delta. HA's brightness slider is always
        # absolute, so this matches user intent.
        request = request_pb2.DeviceCommandBrightnessRequest(
            hub_id=command.hub_id,
            device_id=command.device_id,
            device_type=_build_object_type(command.device_type),
            brightness_in_percentage=command.brightness,
            channels=command.channels or [1],
            brightness_type=2,
        )
        response = await stub.execute(request, metadata=metadata, timeout=15)
        if response.HasField("failure"):
            error = response.failure.WhichOneof("error") or "unknown"
            raise DeviceCommandError(f"brightness: {error}", reason=error)
        _LOGGER.debug(
            "Device %s brightness=%s%% (channels=%s) OK",
            command.device_id,
            command.brightness,
            command.channels,
        )

    async def switch_smart_lock(self, space_id: str, smart_lock_id: str, action: int) -> None:
        """Lock / unlock / unlatch a SmartLock or LockBridge.

        action: 1=UNLOCK, 2=LOCK, 3=UNLATCH (constants exported as
        SMART_LOCK_ACTION_*). Raises SmartLockError on a failure response.
        """
        from v3.mobilegwsvc.service.switch_smart_lock import (  # noqa: PLC0415
            endpoint_pb2_grpc,
            request_pb2,
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = endpoint_pb2_grpc.SwitchSmartLockServiceStub(channel)
        request = request_pb2.SwitchSmartLockRequest(
            space_id=space_id,
            smart_lock_id=smart_lock_id,
            action=action,
        )
        response = await stub.execute(request, metadata=metadata, timeout=15)
        if response.HasField("failure"):
            error_type = response.failure.WhichOneof("error") or "unknown"
            raise SmartLockError(error_type)
        _LOGGER.debug("SmartLock %s in space %s: action=%s OK", smart_lock_id, space_id, action)

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

    async def set_photo_on_demand_mode(
        self,
        hub_id: str,
        *,
        user_enabled: bool | None = None,
        scenario_enabled: bool | None = None,
    ) -> None:
        """Toggle hub-wide Photo on Demand mode (user and/or scenario channels).

        The request proto's two switches live in a oneof, so a single RPC
        only flips one of them; we issue one call per provided argument.
        At least one of `user_enabled` / `scenario_enabled` is required.
        Idempotent: re-sending the current value succeeds without error.
        """
        if user_enabled is None and scenario_enabled is None:
            raise DeviceCommandError(
                "set_photo_on_demand_mode requires user_enabled and/or scenario_enabled"
            )

        from v3.mobilegwsvc.service.device_command_photo_on_demand_mode import (  # noqa: PLC0415
            endpoint_pb2_grpc,
            request_pb2,
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = endpoint_pb2_grpc.DeviceCommandPhotoOnDemandModeServiceStub(channel)
        request_cls = request_pb2.DeviceCommandPhotoOnDemandModeRequest
        user_modes = request_cls.PhotoOnDemandModeUser
        scenario_modes = request_cls.PhotoOnDemandModeScenario

        calls: list[tuple[str, request_pb2.DeviceCommandPhotoOnDemandModeRequest]] = []
        if user_enabled is not None:
            calls.append(
                (
                    "user",
                    request_cls(
                        hub_id=hub_id,
                        photo_on_demand_mode_user=(
                            user_modes.PHOTO_ON_DEMAND_MODE_USER_ENABLE
                            if user_enabled
                            else user_modes.PHOTO_ON_DEMAND_MODE_USER_DISABLE
                        ),
                    ),
                )
            )
        if scenario_enabled is not None:
            calls.append(
                (
                    "scenario",
                    request_cls(
                        hub_id=hub_id,
                        photo_on_demand_mode_scenario=(
                            scenario_modes.PHOTO_ON_DEMAND_MODE_SCENARIO_ENABLE
                            if scenario_enabled
                            else scenario_modes.PHOTO_ON_DEMAND_MODE_SCENARIO_DISABLE
                        ),
                    ),
                )
            )

        for label, request in calls:
            response = await stub.execute(request, metadata=metadata, timeout=15)
            if response.HasField("failure"):
                error = response.failure.WhichOneof("error") or "unknown"
                raise DeviceCommandError(f"photo_on_demand_mode {label}: {error}")
            _LOGGER.debug(
                "Hub %s photo_on_demand_mode.%s set OK",
                hub_id,
                label,
            )
