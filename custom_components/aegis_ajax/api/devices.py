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


def _decode_proto_wire_shape(data: bytes) -> str:
    """Render a one-line structural summary of protobuf wire-format bytes.

    Walks the top-level fields, emitting `field=<num>,wt=<wire>,len=<n>`
    (or `=varint`/`=i32`/`=i64` for fixed-size cases) for each one. No
    values are surfaced — only field numbers and shapes — so this output
    is safe to paste even when the original proto carries device names
    or other PII. Used to spot a new oneof case (e.g. `field=5`) on a
    `LightDevice` that our compiled `.proto` doesn't yet know about (#179
    Outlet Type E hypothesis).
    """
    parts: list[str] = []
    i = 0
    while i < len(data):
        try:
            tag, i = _decode_varint(data, i)
        except ValueError:
            parts.append("<truncated>")
            break
        field = tag >> 3
        wire = tag & 0x07
        if wire == 0:
            try:
                _, i = _decode_varint(data, i)
            except ValueError:
                parts.append(f"f{field}=varint<truncated>")
                break
            parts.append(f"f{field}=varint")
        elif wire == 1:
            i += 8
            parts.append(f"f{field}=i64")
        elif wire == 2:
            try:
                length, i = _decode_varint(data, i)
            except ValueError:
                parts.append(f"f{field}=bytes<truncated>")
                break
            i += length
            parts.append(f"f{field}=bytes({length})")
        elif wire == 5:
            i += 4
            parts.append(f"f{field}=i32")
        else:
            parts.append(f"f{field}=wt{wire}?")
            break
    return ",".join(parts) if parts else "<empty>"


def _decode_varint(data: bytes, i: int) -> tuple[int, int]:
    """Read a protobuf varint at offset `i`, returning `(value, next_offset)`.

    Raises `ValueError` on truncation or an unreasonably long encoding —
    the caller surfaces this as a `<truncated>` marker in the wire-shape
    summary so a partial / corrupt buffer doesn't blow up the log line.
    """
    value = 0
    shift = 0
    while i < len(data):
        b = data[i]
        i += 1
        value |= (b & 0x7F) << shift
        if not (b & 0x80):
            return value, i
        shift += 7
        if shift >= 64:
            msg = "varint too long"
            raise ValueError(msg)
    msg = "truncated varint"
    raise ValueError(msg)


def _redact_proto_bytes_to_hex(data: bytes) -> str:
    """Render protobuf bytes as hex with printable-ASCII runs masked.

    Scans `data` for contiguous runs of printable ASCII bytes (length ≥ 3)
    and replaces each run with `<text:Nb>`; everything else renders as
    its two-character hex value. The chosen 3-byte threshold mirrors the
    HTS DEBUG probe (`_redact_if_text` in api/hts/client.py) — short
    incidental ASCII bytes (e.g. a single 0x41 byte that happens to be
    `'A'`) still come through as hex, while real text fields (device
    names, ids, emails) are length-preserving redacted. Numeric values
    almost always contain at least one non-printable byte (`0x00` is the
    most common), so electrical readings stay fully visible — exactly
    what we need to map an unknown LightDevice oneof case (#179) from a
    public bug-report log.
    """
    out: list[str] = []
    i = 0
    n = len(data)
    while i < n:
        j = i
        while j < n and 0x20 <= data[j] <= 0x7E:
            j += 1
        run_len = j - i
        if run_len >= 3:
            out.append(f"<text:{run_len}b>")
            i = j
        else:
            out.append(f"{data[i]:02x}")
            i += 1
    return "".join(out)


class SmartLockError(Exception):
    """Raised when a SwitchSmartLockService call fails."""


class DeviceCommandError(Exception):
    """Raised when a DeviceCommand* gRPC call fails or is not supported."""


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

# SmartLockStatus.LockStatus → human-readable state used by the lock entity.
# Mirrors `proto/.../space/smartlock/smart_lock_pb2`.
_SMART_LOCK_STATE_MAP: dict[int, str] = {
    0: "unknown",
    1: "unlocked",
    2: "locked",
    3: "unlatched",
}

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
            elif which in ("wire_input_status", "transmitter_status"):
                # Both oneofs share the same shape (is_alert + CustomAlarmType)
                # but different devices use different ones: WireInput / WireInputMt
                # use `wire_input_status`, the wireless Transmitter Jeweller
                # uses `transmitter_status`. Map both to the same internal keys
                # so the unified safety entity reflects either source.
                sub = getattr(status, which)
                result["wire_input_alert"] = (
                    bool(sub.is_alert) if hasattr(sub, "is_alert") else True
                )
                if hasattr(sub, "type"):
                    result["wire_input_alarm_type"] = _ALARM_TYPE_NAMES.get(
                        int(sub.type), "unspecified"
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
                # `wifi_signal_level_status` is a sub-message wrapping the
                # actual enum, not a plain int (#119, surfaced when video-
                # edge channels added in beta.5 started hitting this path).
                # `int(sub_message)` blew up with TypeError and killed the
                # device-stream loop in a reconnect cycle.
                sub = getattr(status, "wifi_signal_level_status", None)
                result["wifi_signal_level"] = (
                    int(getattr(sub, "wifi_signal_level", 0)) if sub is not None else 0
                )
            elif which == "smart_lock":
                # The status oneof carries the LockStatus enum directly (not a
                # sub-message), so `status.smart_lock` is already the int value.
                result["smart_lock_state"] = _SMART_LOCK_STATE_MAP.get(
                    int(status.smart_lock), "unknown"
                )
        return result

    @staticmethod
    def _parse_spread_properties(hub_dev: Any) -> dict[str, Any]:  # noqa: ANN401
        """Walk `LightHubDevice.spread_properties` for switch / dimmer state.

        The on/off state of relays, sockets, and light switches lives in
        the repeated `spread_properties` field — *not* in the
        `LightDeviceStatus.statuses` oneof we already parse — and one
        entry surfaces per physical channel. Multi-gang devices like
        `light_switch_two_gang` therefore appear as 2 entries each
        carrying a `LightSwitchChannel` with id 1 / 2.

        Output keys mirror what `AjaxSwitch` / `AjaxLight` already read
        (`switch_ch{N}`, `brightness_ch{N}`) so a Device parsed from a
        snapshot now drives those entities directly. Without this, the
        switch entity always read False regardless of the actual hub
        state — the symptom @EpicManeuver reported in #104 for a
        bistable Relay Jeweller.
        """
        result: dict[str, Any] = {}
        spread = getattr(hub_dev, "spread_properties", None)
        if not spread:
            return result
        for entry in spread:
            which = entry.WhichOneof("properties") if hasattr(entry, "WhichOneof") else None
            if which == "channel":
                # RelayChannel — `channel_id` is a plain int (1..4),
                # `is_channel_on` is a real bool.
                ch = entry.channel
                channel_id = int(ch.channel_id) if hasattr(ch, "channel_id") else 1
                if channel_id <= 0:
                    continue
                result[f"switch_ch{channel_id}"] = bool(ch.is_channel_on)
            elif which == "light_switch_channel":
                # LightSwitchChannel — `id` and `state` are ChannelId and
                # State enums (1=OFF, 2=ON). Optional `brightness.level`
                # carries the dimmer percentage 0..100.
                lsc = entry.light_switch_channel
                channel_id = int(lsc.id) if hasattr(lsc, "id") else 1
                if channel_id <= 0:
                    continue
                state_int = int(lsc.state) if hasattr(lsc, "state") else 0
                result[f"switch_ch{channel_id}"] = state_int == 2  # STATE_ON
                if lsc.HasField("brightness"):
                    result[f"brightness_ch{channel_id}"] = int(lsc.brightness.level)
            elif which == "socket_base_channel":
                # SocketBaseChannel — same shape as light_switch_channel
                # for our purposes.
                sbc = entry.socket_base_channel
                channel_id = int(sbc.id) if hasattr(sbc, "id") else 1
                if channel_id <= 0:
                    continue
                state_int = int(sbc.state) if hasattr(sbc, "state") else 0
                result[f"switch_ch{channel_id}"] = state_int == 2  # STATE_ON
            elif which == "water_stop_channel":
                # WaterStopChannel — feeds the read-only `valve` entity (#117).
                # State enum is distinct from the relay/switch family:
                # 0=UNSPECIFIED, 1=UNKNOWN, 2=OFF, 3=ON. We only emit
                # `valve_chN` for ON/OFF; UNKNOWN / UNSPECIFIED leave the
                # key absent so the entity renders as `unknown` rather
                # than fabricating a closed/open reading on a comms hiccup.
                # `STATE_ON` = water flowing (valve open) follows the same
                # convention as the relay parser above; if real-hardware
                # testing reveals the WaterStop firmware uses the inverted
                # mapping, flip the comparison here.
                wsc = entry.water_stop_channel
                channel_id = int(wsc.id) if hasattr(wsc, "id") else 1
                if channel_id <= 0:
                    continue
                state_int = int(wsc.state) if hasattr(wsc, "state") else 0
                if state_int == 3:  # STATE_ON
                    result[f"valve_ch{channel_id}"] = True
                elif state_int == 2:  # STATE_OFF
                    result[f"valve_ch{channel_id}"] = False
                if getattr(wsc, "is_transitioning", False):
                    result[f"valve_ch{channel_id}_transitioning"] = True
                # MALFUNCTION_IS_STUCK = 2 in the channel-level enum
                # (distinct from the Simple-flag `water_stop_valve_stuck`
                # parsed off `LightDeviceStatus.statuses`).
                if 2 in [int(m) for m in getattr(wsc, "malfunctions", [])]:
                    result[f"valve_ch{channel_id}_stuck"] = True
        return result

    # Map `LightVideoEdgeChannel.video_edge_channel_properties.video_edge_type`
    # (`About.Type` enum) to a HA-side device_type string parallel to the
    # `_DEVICE_TYPE_SENSORS` keys. Only the consumer-visible variants are
    # named — every NVR sub-type collapses to `video_edge_nvr` because users
    # don't typically wire HA automations off recorder boxes, and the long
    # tail (TURRET_HL, BULLET_HL_VF, S_TURRET_HL_VF, etc.) collapses to its
    # base shape. Anything we don't recognise becomes `video_edge_unknown`
    # so the device still appears as a HA card with the device-agnostic
    # sensors instead of silently disappearing.
    _VIDEO_EDGE_TYPE_MAP: dict[int, str] = {
        2: "video_edge_turret",
        3: "video_edge_bullet",
        4: "video_edge_minidome",
        5: "video_edge_doorbell",  # #119: @Permudious's MotionCam Video Doorbell
        6: "video_edge_indoor",
        17: "video_edge_turret",  # TURRET_HL
        18: "video_edge_turret",  # TURRET_HL_VF
        19: "video_edge_turret",  # S_TURRET_HL_VF
        20: "video_edge_bullet",  # BULLET_HL
        21: "video_edge_bullet",  # BULLET_HL_VF
        22: "video_edge_bullet",  # S_BULLET_HL_VF
        23: "video_edge_minidome",  # MINIDOME_HL
        24: "video_edge_minidome",  # MINIDOME_HL_VF
        25: "video_edge_minidome",  # S_MINIDOME_HL_VF
    }

    @staticmethod
    def parse_device(proto_light_device: Any) -> Device | None:  # noqa: ANN401
        device_kind = proto_light_device.WhichOneof("device")
        if device_kind == "hub_device":
            return DevicesApi._parse_hub_device(proto_light_device.hub_device)
        if device_kind == "video_edge_channel":
            return DevicesApi._parse_video_edge_channel(proto_light_device.video_edge_channel)
        _LOGGER.debug("Skipping unsupported device type: %s", device_kind)
        # `device_kind is None` means the LightDevice proto's `device`
        # oneof didn't match any case we know (`hub_device`,
        # `video_edge`, `video_edge_channel`, `smart_lock`). The bytes
        # are preserved as protobuf unknown fields — most likely a NEW
        # oneof case the cloud started emitting for a device family
        # we haven't pulled into the local `.proto` yet (#179 Outlet
        # Type E hypothesis: 18 of these landed during a load-toggle
        # capture with zero matching HTS deltas). Surface enough
        # structure to identify the new field number AND the redacted
        # contents so the case can be reconstructed from a single
        # capture without another user round-trip.
        if device_kind is None and _LOGGER.isEnabledFor(logging.DEBUG):
            try:
                raw = proto_light_device.SerializeToString()
            except Exception:  # noqa: BLE001
                return None
            if raw:
                _LOGGER.debug(
                    "Unsupported LightDevice (%db) wire-shape: %s",
                    len(raw),
                    _decode_proto_wire_shape(raw),
                )
                _LOGGER.debug(
                    "Unsupported LightDevice (%db) bytes: %s",
                    len(raw),
                    _redact_proto_bytes_to_hex(raw),
                )
        return None

    # MotionCam Video Doorbell / Indoor / Base hardware can arrive in a
    # snapshot under TWO LightDevice oneofs simultaneously — a Jeweller-side
    # `hub_device` with `object_type=motion_cam_video_*` and a `video_edge_
    # channel` with `video_edge_type=DOORBELL|INDOOR|...`. The hub_device
    # twin carries a spurious `malfunctions=1` that bubbles up to the
    # space-level counter and shows the user a "problem" indicator on the
    # device card. The video_edge_channel side is the richer, canonical
    # representation. When both arrive in the same snapshot we drop the
    # hub_device twin; when only the hub_device arrives (Permudious's setup
    # in #119) we leave it alone so the doorbell still appears in HA. See
    # `_dedupe_video_doorbells` below.
    _MOTION_CAM_VIDEO_HUB_PREFIX = "motion_cam_video_"
    _VIDEO_EDGE_PREFIX = "video_edge_"

    @staticmethod
    def _dedupe_video_doorbells(devices: list[Device]) -> list[Device]:
        """Drop `motion_cam_video_*` hub_device twins that have a
        `video_edge_*` sibling with a matching name in the same snapshot.

        Name match is case-insensitive (Ajax sometimes capitalises the same
        product differently on the two oneofs). Names are the only stable
        discriminator across the two LightDevice cases — the IDs are
        unrelated (Jeweller short id vs MAC-style `aa:bb:cc:dd:ee:ff-0`)
        and we don't have a serial-number bridge between them. See #173.
        """
        video_edge_names = {
            d.name.casefold()
            for d in devices
            if d.device_type.startswith(DevicesApi._VIDEO_EDGE_PREFIX)
        }
        result: list[Device] = []
        for d in devices:
            if (
                d.device_type.startswith(DevicesApi._MOTION_CAM_VIDEO_HUB_PREFIX)
                and d.name.casefold() in video_edge_names
            ):
                _LOGGER.debug(
                    "Dropping duplicate hub_device twin %s (%s) — same name "
                    "as a video_edge sibling in this snapshot (#173)",
                    d.id,
                    d.device_type,
                )
                continue
            result.append(d)
        return result

    @staticmethod
    def _parse_hub_device(hub_dev: Any) -> Device | None:  # noqa: ANN401
        common = hub_dev.common_device
        profile = common.profile

        device_type = common.object_type.WhichOneof("type") or "unknown"

        statuses = DevicesApi._parse_statuses(profile.statuses)
        statuses.update(DevicesApi._parse_spread_properties(hub_dev))

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
            statuses=statuses,
            battery=DevicesApi._parse_battery(profile.statuses),
        )

    @staticmethod
    def _parse_video_edge_channel(channel: Any) -> Device | None:  # noqa: ANN401
        """Parse a `LightVideoEdgeChannel` into a Device (#119).

        Video Edge channels (MotionCam Video Doorbell, Indoor camera, NVR
        channels, third-party RTSP cameras bridged via VideoEdge) come
        through their own LightDevice oneof case — distinct from
        `hub_device` — and don't expose an `object_type` field. The type
        signal is `video_edge_channel_properties.video_edge_type`, an
        `About.Type` enum which we map to `_DEVICE_TYPE_SENSORS`-friendly
        strings like `video_edge_doorbell`.
        """
        if not channel.HasField("video_edge_channel_properties"):
            _LOGGER.debug("video_edge_channel without properties, skipping")
            return None
        if not channel.HasField("profile"):
            _LOGGER.debug("video_edge_channel without profile, skipping")
            return None

        profile = channel.profile
        type_value = int(channel.video_edge_channel_properties.video_edge_type)
        device_type = DevicesApi._VIDEO_EDGE_TYPE_MAP.get(type_value, "video_edge_unknown")

        # No hub_id on the channel side — Ajax models the video bridge
        # as a sibling of the hub, not a child. Falling back to the
        # channel's own id keeps the HA device registry happy; entity
        # routing in the integration is keyed off `device.id` anyway.
        return Device(
            id=profile.id,
            hub_id=profile.id,
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
        else:
            raise DeviceCommandError(f"Unknown device command action: {command.action}")

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
            raise DeviceCommandError(f"on: {error}")
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
            raise DeviceCommandError(f"off: {error}")
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
            raise DeviceCommandError(f"brightness: {error}")
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
