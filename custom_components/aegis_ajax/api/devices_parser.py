"""Pure parsers for Ajax device protos.

Extracted from `devices.py` so the proto-to-`Device` translation logic lives
in one focused, side-effect-free module that can be tested without standing up
a `DevicesApi`/gRPC client. `DevicesApi` keeps thin `@staticmethod` delegators
(`DevicesApi.parse_device`, `DevicesApi._parse_statuses`, …) that forward here,
so every existing caller and test keeps working unchanged.

Everything here is a pure function of its proto argument — no network, no
client state. The live-update path (`DevicesApi._handle_update`) imports the
maps and redaction helpers from here too.
"""

from __future__ import annotations

import logging
from typing import Any

from custom_components.aegis_ajax.api.models import BatteryInfo, Device
from custom_components.aegis_ajax.const import DeviceState

# Logger name is pinned to the original `api.devices` module (not __name__) so
# debug output keeps its existing namespace — callers and caplog-based tests
# filter on `custom_components.aegis_ajax.api.devices`. Same name == same
# logger object, so log lines are byte-for-byte unchanged after the move.
_LOGGER = logging.getLogger("custom_components.aegis_ajax.api.devices")


# Protos under this byte threshold are considered protocol-level state
# messages (no room for user-set names, rooms, or other PII inside a
# ≤16-byte envelope) and are logged as raw hex in `parse_device`'s
# unsupported-LightDevice probe so short ASCII codes come through.
# Larger protos go through `_redact_proto_bytes_to_hex` unchanged.
_TINY_PROTO_THRESHOLD = 16


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


# SmartLockStatus.LockStatus → human-readable state used by the lock entity.
# Mirrors `proto/.../space/smartlock/smart_lock_pb2`.
_SMART_LOCK_STATE_MAP: dict[int, str] = {
    0: "unknown",
    1: "unlocked",
    2: "locked",
    3: "unlatched",
}

# Field-99 `LockControlStatus.state` → lock entity state (#206). Current
# firmware pushes the lock state here instead of the bare-enum `smart_lock`
# (field 66), with an INVERTED integer scheme relative to the LockStatus enum
# above. Mapped empirically (1=locked, 2=open) — cross-confirmed against the
# device's local HTS signal and the reporter's lock/unlock action timestamps.
_LOCK_CONTROL_STATE_MAP: dict[int, str] = {
    1: "locked",
    2: "unlocked",
    3: "unlatched",
}

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


def _parse_battery(statuses: Any) -> BatteryInfo | None:  # noqa: ANN401
    for status in statuses:
        which = status.WhichOneof("status") if hasattr(status, "WhichOneof") else None
        if which == "battery":
            return BatteryInfo(
                level=status.battery.charge_level_percentage,
                is_low=status.battery.battery_state not in (0, 1),  # 0=UNSPECIFIED, 1=OK
            )
    return None


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
            result["signal_strength"] = _SIGNAL_LEVEL_MAP.get(signal_int, f"Unknown ({signal_int})")
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
            result["wire_input_alert"] = bool(sub.is_alert) if hasattr(sub, "is_alert") else True
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
        elif which == "lock_control_status":
            # Field 99 (#206): current firmware reports the lock state here as
            # a sub-message instead of the field-66 `smart_lock` enum above.
            result["smart_lock_state"] = _LOCK_CONTROL_STATE_MAP.get(
                int(status.lock_control_status.state), "unknown"
            )
    return result


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


def parse_device(proto_light_device: Any) -> Device | None:  # noqa: ANN401
    device_kind = proto_light_device.WhichOneof("device")
    if device_kind == "hub_device":
        return _parse_hub_device(proto_light_device.hub_device)
    if device_kind == "video_edge_channel":
        return _parse_video_edge_channel(proto_light_device.video_edge_channel)
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
            # Tiny protos (≤ TINY_PROTO_THRESHOLD bytes total) are
            # protocol-level state messages, not user-data payloads —
            # there's no room for a device name / room / email inside
            # that envelope. Render them with raw hex so short ASCII
            # codes (3-char status strings like `OFF`/`ON`, hub-id
            # suffixes, device-type tags) come through directly.
            # Larger protos (real device records that DO carry
            # user-set names) keep the ≥3-byte ASCII redaction. From
            # #179: beta.6 capture showed 18 events with the shape
            # `f5={f2:<3 ASCII chars>}` lining up with Outlet load
            # transitions — but redaction hid exactly the 3 chars
            # needed to identify whether they encode state or just
            # a device-id reference.
            bytes_str = (
                raw.hex() if len(raw) <= _TINY_PROTO_THRESHOLD else _redact_proto_bytes_to_hex(raw)
            )
            _LOGGER.debug(
                "Unsupported LightDevice (%db) bytes: %s",
                len(raw),
                bytes_str,
            )
    return None


def parse_hub_device_temperature(hub_device: Any) -> float | None:  # noqa: ANN401
    """Pull the internal temperature out of a rich `HubDevice` proto (#220).

    The per-device `StreamHubDevice` RPC returns a `HubDevice` whose
    `device` oneof selects a per-type message (`street_siren`,
    `home_siren`, `motion_protect_curtain_outdoor`, …). Those messages
    carry a `device_temperature` (`DeviceTemperature { value, is_extreme }`)
    that the lighter `StreamLightDevices` stream omits (#220 sirens, #229
    outdoor curtain PIRs) — so this is the only path to their temperature.
    The lookup is oneof-case-agnostic, so any device whose sub-message has
    a `device_temperature` works. Returns the value as a float, or
    `None` when the oneof is unset, the sub-message has no
    `device_temperature`, or anything about the shape is unexpected.
    """
    which = hub_device.WhichOneof("device") if hasattr(hub_device, "WhichOneof") else None
    if which is None:
        return None
    sub = getattr(hub_device, which, None)
    if sub is None or not hasattr(sub, "HasField"):
        return None
    try:
        if not sub.HasField("device_temperature"):
            return None
        return float(sub.device_temperature.value)
    except (ValueError, TypeError):
        return None


def _dedupe_video_doorbells(devices: list[Device]) -> tuple[list[Device], dict[str, str]]:
    """Drop `motion_cam_video_*` hub_device twins that have a
    `video_edge_*` sibling with a matching name in the same snapshot.

    Name match is case-insensitive (Ajax sometimes capitalises the same
    product differently on the two oneofs). Names are the only stable
    discriminator across the two LightDevice cases — the IDs are
    unrelated (Jeweller short id vs MAC-style `aa:bb:cc:dd:ee:ff-0`)
    and we don't have a serial-number bridge between them. See #173.

    Returns the deduped device list plus an alias map `{dropped twin id:
    surviving video_edge id}`. Doorbell ring / motion pushes carry the
    Jeweller twin id, which is no longer in the device set after dedup, so
    the alias lets `notification` still resolve those pushes onto the
    video_edge device the user sees (the motion-attribution miss in #173).
    """
    video_edge_id_by_name = {
        d.name.casefold(): d.id for d in devices if d.device_type.startswith(_VIDEO_EDGE_PREFIX)
    }
    result: list[Device] = []
    aliases: dict[str, str] = {}
    for d in devices:
        if (
            d.device_type.startswith(_MOTION_CAM_VIDEO_HUB_PREFIX)
            and d.name.casefold() in video_edge_id_by_name
        ):
            sibling_id = video_edge_id_by_name[d.name.casefold()]
            aliases[d.id] = sibling_id
            _LOGGER.debug(
                "Dropping duplicate hub_device twin %s (%s) — same name "
                "as a video_edge sibling in this snapshot (#173); "
                "aliasing pushes to %s",
                d.id,
                d.device_type,
                sibling_id,
            )
            continue
        result.append(d)
    return result, aliases


def _parse_hub_device(hub_dev: Any) -> Device | None:  # noqa: ANN401
    common = hub_dev.common_device
    profile = common.profile

    device_type = common.object_type.WhichOneof("type") or "unknown"

    statuses = _parse_statuses(profile.statuses)
    statuses.update(_parse_spread_properties(hub_dev))

    return Device(
        id=profile.id,
        hub_id=common.hub_id,
        name=profile.name,
        device_type=device_type,
        room_id=profile.room_id if profile.room_id else None,
        group_id=profile.group_id if profile.group_id else None,
        state=_parse_device_state(profile.states),
        malfunctions=profile.malfunctions,
        bypassed=profile.bypassed,
        statuses=statuses,
        battery=_parse_battery(profile.statuses),
    )


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
    device_type = _VIDEO_EDGE_TYPE_MAP.get(type_value, "video_edge_unknown")

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
        state=_parse_device_state(profile.states),
        malfunctions=profile.malfunctions,
        bypassed=profile.bypassed,
        statuses=_parse_statuses(profile.statuses),
        battery=_parse_battery(profile.statuses),
    )
