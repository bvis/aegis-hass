"""Hub network state parser for the Ajax HTS binary protocol."""

from __future__ import annotations

import dataclasses

# ---------------------------------------------------------------------------
# TLV key constants
# ---------------------------------------------------------------------------

KEY_HUB_POWERED = 3
KEY_GSM_SIGNAL_LVL = 4
KEY_ETH_DHCP = 16
KEY_WIFI_LEVEL = 18
# The hub's *installed* firmware version (#388). `hub_device.proto` declares
# `HubDevice.Firmware.version = 0x37`, and that file is the map for this row:
# all 15 hub keys already decoded here match it, including the complete
# Ethernet (0x10/0x23–0x26/0x4A) and Wi-Fi (0x12/0x27–0x2E/0x4B) blocks.
# `0x37` appears exactly once in the file, so nothing else can claim it.
#
# ⚠️ The proto declares this field `string`, but the hub sends four binary
# bytes: a packed decimal integer, big-endian. Observed on a Hub 2 (4G)
# Jeweller running 2.40.121: `0003a9f9` = 240121. So the app's declared type
# describes its own transport, not this one — read it as an int32.
#
# Not every hub firmware puts every declared sub-key in its status body, so
# treat absence as "unknown", never as "no firmware".
KEY_HUB_FIRMWARE = 0x37
KEY_ETH_IP = 35
KEY_ETH_MASK = 36
KEY_ETH_GATE = 37
KEY_ETH_DNS = 38
KEY_WIFI_SSID = 39
KEY_WIFI_CHANNEL = 41
KEY_WIFI_IP = 42
KEY_WIFI_MASK = 43
KEY_WIFI_GATE = 44
KEY_WIFI_DNS = 45
KEY_WIFI_DHCP = 46
KEY_ACTIVE_CHANNELS = 72
# Hub siren behaviour settings (#438). Declared only in the legacy
# `hub_device.proto` (fields 101/102, both `bool`), which is not
# gRPC-reachable — the hub's HTS row is the sole live source. Confirmed
# present on real hardware 2026-08-29, but ONLY in SETTINGS_BODY (the
# 60 s STATUS_BODY refresh skips them), so the values load at
# startup/reload and then hold until the next SETTINGS_BODY. The wire
# width is unobserved: decode by integer magnitude, and keep the raw
# value so a hub that packs these differently is answerable from a
# diagnostics download.
KEY_SIREN_ON_ANY_TAMPER = 0x65
KEY_SIREN_ON_PANIC_BUTTON = 0x66
# Restoration after alarm (#634). Legacy `hub_device.proto` fields 187/188,
# both declared `repeated AlarmType`, but each arrives as ONE byte: a bitmask.
# `0xBB` (restore_required) is the hub setting, the alarm types that need a
# restore before arming again; it comes in SETTINGS_BODY. `0xBC`
# (alarm_happened) comes in every STATUS_BODY refresh and is non-zero while a
# restore is pending. Seen on a Hub 2 Plus (2.43.123): 0xBC went 00 -> 80
# when a detector lost connection and back to 00 when the restore was
# accepted, with 0xBB at 98 throughout. Only "non-zero = pending" is
# established; the meaning of each bit is not, so the raw values are kept
# for diagnostics.
KEY_RESTORE_REQUIRED = 0xBB
KEY_ALARM_HAPPENED = 0xBC
KEY_ETH_ENABLED = 74
KEY_WIFI_ENABLED = 75
KEY_GPRS_ENABLED = 76
KEY_GSM_NETWORK_STATUS = 122

# ---------------------------------------------------------------------------
# Signal / network maps
# ---------------------------------------------------------------------------

GSM_SIGNAL_MAP: dict[int, str] = {0: "unknown", 1: "weak", 2: "normal", 3: "strong"}
GSM_NETWORK_MAP: dict[int, str] = {
    0: "unknown",
    1: "gsm",
    2: "2g",
    3: "3g",
    4: "4g",
}
WIFI_SIGNAL_MAP: dict[int, str] = {0: "unknown", 1: "weak", 2: "normal", 3: "strong"}

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

_ACTIVE_CHANNELS_ETH_BIT = 0  # bit 0
_ACTIVE_CHANNELS_WIFI_BIT = 1  # bit 1
_ACTIVE_CHANNELS_GSM_BIT = 2  # bit 2


@dataclasses.dataclass(frozen=True)
class HubNetworkState:
    """Immutable snapshot of hub network connectivity."""

    # Active connection flags (derived from KEY_ACTIVE_CHANNELS bitmask).
    # None until the hub sends that key: some hubs leave it out of the
    # startup row and only send it on change (#594), and "disconnected"
    # would be a guess.
    ethernet_connected: bool | None = None
    wifi_connected: bool | None = None
    gsm_connected: bool | None = None

    # Ethernet
    ethernet_enabled: bool = False
    ethernet_ip: str = ""
    ethernet_mask: str = ""
    ethernet_gateway: str = ""
    ethernet_dns: str = ""
    ethernet_dhcp: bool = False

    # Wi-Fi
    wifi_enabled: bool = False
    wifi_ssid: str = ""
    wifi_signal_level: str = "unknown"
    wifi_ip: str = ""

    # GSM
    gsm_signal_level: str = "unknown"
    gsm_network_type: str = "unknown"

    # Power
    externally_powered: bool = False

    # Installed firmware version (#388). Empty when this hub's firmware
    # doesn't report the sub-key — callers must treat "" as unknown.
    firmware_version: str = ""
    # The packed integer the version was decoded from, kept for diagnostics.
    # The decode rule rests on a small number of samples, so a hub that packs
    # it differently should be answerable from a diagnostics download rather
    # than from another capture session.
    firmware_version_raw: int = 0

    # Hub siren behaviour settings (#438). None means this hub's firmware
    # hasn't reported the sub-key (yet) — callers must render "unknown",
    # never "off". The raw integers exist for diagnostics only.
    siren_on_panic_button: bool | None = None
    siren_on_any_tamper: bool | None = None
    siren_on_panic_button_raw: int | None = None
    siren_on_any_tamper_raw: int | None = None

    # Restoration after alarm (#634). None until the hub reports the key.
    # `restore_pending` drives the binary sensor; the raw bitmasks are for
    # diagnostics only.
    restore_pending: bool | None = None
    alarm_happened_raw: int | None = None
    restore_required_raw: int | None = None

    @property
    def primary_connection(self) -> str | None:
        """Return the highest-priority active connection type, None if unreported."""
        if (self.ethernet_connected, self.wifi_connected, self.gsm_connected) == (None, None, None):
            return None
        if self.ethernet_connected:
            return "ethernet"
        if self.wifi_connected:
            return "wifi"
        if self.gsm_connected:
            return "gsm"
        return "none"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _int_to_ip(val: int) -> str:
    """Convert a 32-bit big-endian integer to a dotted IPv4 string."""
    return f"{(val >> 24) & 0xFF}.{(val >> 16) & 0xFF}.{(val >> 8) & 0xFF}.{val & 0xFF}"


def _byte_val(val: bytes) -> int:
    """Return the integer value of the first byte in *val*."""
    return val[0] if val else 0


def _bool_val(val: bytes) -> bool:
    """Return True if the first byte is non-zero."""
    return bool(_byte_val(val))


# Ajax's "no value" marker for the numeric version fields — INT32_MIN. Seen
# on `latest_available_version` (0x69) of a hub with no update available,
# alongside `new_version_available = false`.
_VERSION_UNSET = 0x80000000


def _version_val(val: bytes) -> str:
    """Decode the hub's packed firmware version into a dotted string (#388).

    The four bytes are a big-endian integer whose *decimal* digits are the
    version components: `major * 100000 + minor * 1000 + patch`. A hub
    running 2.40.121 sends 240121; 2.41.116 would send 241116.

    Returns "" for anything that doesn't decode sensibly. The range check
    matters more than it looks: without it any 4-byte value decodes to a
    plausible-looking version (`deadbeef` becomes "37359.28.559"). A major
    of 1..99 bounds the packed value to 100000..9999999, which rejects the
    unset sentinel and arbitrary payloads alike. Callers treat "" as
    unknown, so a hub whose firmware packs this differently degrades to
    showing no version rather than to showing a wrong one.
    """
    if len(val) != 4:
        return ""
    packed = int.from_bytes(val, "big")
    if not (100_000 <= packed < 10_000_000):
        return ""
    major, rest = divmod(packed, 100_000)
    minor, patch = divmod(rest, 1_000)
    return f"{major}.{minor}.{patch}"


def _str_val(val: bytes) -> str:
    """Decode bytes as a null-terminated UTF-8 string."""
    null_pos = val.find(b"\x00")
    if null_pos >= 0:
        val = val[:null_pos]
    return val.decode("utf-8", errors="replace")


def _ip_val(val: bytes) -> str:
    """Parse a 4-byte big-endian value as a dotted IPv4 string."""
    if len(val) < 4:
        return ""
    ip_int = int.from_bytes(val[:4], "big")
    return _int_to_ip(ip_int)


def _int_be_val(val: bytes | None) -> int | None:
    """Parse a big-endian unsigned integer of arbitrary length (1-4 bytes).

    Returns None when the input is missing or empty. The Ajax hub sends
    integers with the smallest length that fits (1 byte while readings
    are zero, 2-4 bytes once the WallSwitch has measurable draw), so
    `len(val)` is not fixed across messages — `int.from_bytes` handles
    any length the same way.
    """
    if not val:
        return None
    return int.from_bytes(val, "big")


# ---------------------------------------------------------------------------
# Per-device TLV keys (WallSwitch / Socket family)
# ---------------------------------------------------------------------------
#
# Distinct from the hub-level keys above: same numeric sub-key carries
# different meaning depending on which device's row of the body it
# belongs to. For the hub, 0x42 is `wifi_ip`; for a WallSwitch row, 0x42
# is `current_ma`. Per-device readings come through a separate TLV
# container in the same body — see #123 for context.

DEVICE_KEY_VOLTAGE_V = 0x35
DEVICE_KEY_CURRENT_MA = 0x42
DEVICE_KEY_POWER_CONSUMED_WH = 0x43

# Outlet Type E / Type F sub-keys (#179). Calibrated 2026-05-25 from a
# four-load reboot capture (off / idle / 15 W / ~2 kW). Distinct layout
# from the WallSwitch family above — same numeric keys carry different
# meaning on this device family.
DEVICE_KEY_OUTLET_POWER_W = 0x3F
DEVICE_KEY_OUTLET_ENERGY_WH = 0x40
DEVICE_KEY_OUTLET_CURRENT_10MA = 0x41
DEVICE_KEY_OUTLET_VOLTAGE_V = 0x42


@dataclasses.dataclass(frozen=True)
class DeviceReadings:
    """Immutable snapshot of a single device's electrical readings.

    Every field is `None` when the device does not emit it, when the
    body simply hadn't been refreshed yet, or when the sub-key was
    missing on a body that did include the device. Consumers should
    treat any `None` as "no measurement available" and render the
    entity as `unknown` rather than zero.

    `voltage_v` is always in volts, but the raw sub-key is NOT always in
    volts: the WallSwitch/Socket families report whole volts while the
    Jeweller Relay reports millivolts, so the per-family key map does the
    conversion (#325). Older WallSwitch firmwares omit it entirely — the
    derived-power sensor falls back to a nominal voltage in that case.

    `power_w` is the device-reported instantaneous power in watts. Only
    Outlet Type E / Type F report it directly; the WallSwitch family
    keeps it `None` and lets the derived-power sensor compute `current
    × voltage`.
    """

    current_ma: float | None = None
    power_consumed_wh: float | None = None
    voltage_v: float | None = None
    power_w: float | None = None


# Per-device-family sub-key map. Same DeviceReadings shape feeds every
# family; the mapping selects which sub-keys land in which field and
# at what scale. Scale converts the raw big-endian integer into the
# field's stored unit (e.g. Outlet current is reported in 10 mA units,
# so scale=10 yields the same mA contract the WallSwitch path already
# uses downstream; the Relay's millivolts need 0.001 to reach volts).
_WALLSWITCH_KEY_MAP: dict[int, tuple[str, float]] = {
    DEVICE_KEY_VOLTAGE_V: ("voltage_v", 1),
    DEVICE_KEY_CURRENT_MA: ("current_ma", 1),
    DEVICE_KEY_POWER_CONSUMED_WH: ("power_consumed_wh", 1),
}
# The Jeweller Relay shares the WallSwitch sub-keys but not its units
# (#325). `proto_src/systems/ajax/protobuf/hub/device/relay.proto` declares
# `Int32Value voltage_milli_volts = 0x35`, whereas `wall_switch.proto` and
# `socket.proto` declare `voltage_volts` at the identical sub-key — the HTS
# per-device kv keys are the field numbers of those legacy protos, so the
# vendor's own field names settle the unit. Note the name is unconditional:
# the Relay accepts 7-24 V⎓ *or* 110-230 V~ and reports millivolts either way
# (a mains-fed unit sends 230000), so a fixed factor is safe for both.
# `relay_fibra_base` is deliberately left on the WallSwitch map — no
# `relay_fibra*.proto` exists in `proto_src`, so its unit is unobserved and
# assuming it matches would be a guess.
_RELAY_KEY_MAP: dict[int, tuple[str, float]] = {
    **_WALLSWITCH_KEY_MAP,
    DEVICE_KEY_VOLTAGE_V: ("voltage_v", 0.001),
}
_OUTLET_KEY_MAP: dict[int, tuple[str, float]] = {
    DEVICE_KEY_OUTLET_POWER_W: ("power_w", 1),
    DEVICE_KEY_OUTLET_ENERGY_WH: ("power_consumed_wh", 1),
    DEVICE_KEY_OUTLET_CURRENT_10MA: ("current_ma", 10),
    DEVICE_KEY_OUTLET_VOLTAGE_V: ("voltage_v", 1),
}

_DEVICE_READINGS_KEY_MAP: dict[str, dict[int, tuple[str, float]]] = {
    "wall_switch": _WALLSWITCH_KEY_MAP,
    "relay": _RELAY_KEY_MAP,
    "relay_fibra_base": _WALLSWITCH_KEY_MAP,
    "socket": _WALLSWITCH_KEY_MAP,
    "socket_b": _WALLSWITCH_KEY_MAP,
    "socket_g": _WALLSWITCH_KEY_MAP,
    "socket_type_g_plus": _WALLSWITCH_KEY_MAP,
    "socket_outlet_type_e": _OUTLET_KEY_MAP,
    "socket_outlet_type_f": _OUTLET_KEY_MAP,
}

# device_type strings (as produced by `DevicesApi`) that emit
# electrical-reading sub-keys. Mirrors SWITCH_DEVICE_TYPES in switch.py
# minus the light-switch variants whose firmware doesn't measure current
# (those are dry-contact relays, not load-meters). When a new
# electrical-reading-capable device family appears, add it here, to
# `_DEVICE_READINGS_KEY_MAP`, and to SWITCH_DEVICE_TYPES.
ELECTRICAL_DEVICE_TYPES: frozenset[str] = frozenset(_DEVICE_READINGS_KEY_MAP.keys())

# Device families whose firmware reports instantaneous power directly
# (DeviceReadings.power_w is populated by the parser). Used by the
# sensor platform to register a real Power entity for these devices
# instead of the derived-from-current placeholder the WallSwitch
# family uses.
DIRECT_POWER_DEVICE_TYPES: frozenset[str] = frozenset(
    {"socket_outlet_type_e", "socket_outlet_type_f"}
)

# Internal temperature (#229). A device's STATUS_BODY row carries its internal
# temperature in whole degrees Celsius at sub-key 0x02, as a signed int8 (a
# sub-zero outdoor reading is a negative byte). Verified by correlating
# bvis-home's STATUS_BODY 0x02 against the gRPC-sourced temperature of every
# temperature-reporting device (Door Protect / Keypad / MotionCam): the 0x02
# value matched the known reading on all of them. This is the ONLY temperature
# source for device families whose gRPC `HubDevice` message has no
# `device_temperature` field — see the Curtain Outdoor Plus/Base note in
# const.py. Indoor motion/door sensors and the Curtain Outdoor *Mini* already
# get temperature over gRPC, so they are intentionally NOT read here.
DEVICE_KEY_TEMPERATURE_C = 0x02

# Device types whose internal temperature is sourced from HTS 0x02 because they
# expose no gRPC temperature. When a future device family reports temperature
# only over HTS, add it here (and keep it in
# const.HUB_DEVICE_TEMPERATURE_DEVICE_TYPES so the merged value carries across
# gRPC snapshots).
HTS_TEMPERATURE_DEVICE_TYPES: frozenset[str] = frozenset(
    {
        "motion_protect_curtain_outdoor_plus",
        "motion_protect_curtain_outdoor_base",
        # MotionProtect Outdoor (#269). No temperature on the light stream; a
        # reporter's STATUS_BODY capture showed 0x02 with plausible ambient
        # values on every candidate row (26–29 °C in July), matching the
        # Curtain Outdoor Plus/Base behaviour on the same protocol.
        "motion_protect_outdoor",
        "motion_cam_outdoor_phod",
        # Sirens (#312, #269). They also expose a gRPC `device_temperature`, but
        # that is the internal *board* temperature (runs hotter than ambient and
        # only refreshes on the slow per-device snapshot). HTS 0x02 is the value
        # the Ajax app shows and updates live, confirmed for both the indoor
        # HomeSiren and the outdoor StreetSiren, so sirens are sourced from 0x02
        # and excluded from the gRPC fetch.
        "street_siren",
        "street_siren_plus_g3",
        "street_siren_double_deck",
        "street_siren_s_double_deck",
        "street_siren_double_deck_fibra",
        "street_siren_fibra",
        "home_siren",
        "home_siren_g3",
        "home_siren_s",
        "home_siren_fibra",
    }
)

# Plausible internal-temperature window (°C). A 0x02 value outside it means the
# byte isn't a temperature on this row, so we decline it rather than surface a
# bogus reading.
_HTS_TEMP_MIN_C = -40
_HTS_TEMP_MAX_C = 85


def parse_device_temperature_c(device_type: str, kv: dict[int, bytes]) -> float | None:
    """Decode a device's internal temperature (°C) from its HTS kv block (#229).

    Returns the temperature as a float for device types in
    `HTS_TEMPERATURE_DEVICE_TYPES` when sub-key 0x02 is present and within a
    plausible range; otherwise `None`. The byte is a signed int8 so a sub-zero
    outdoor reading decodes correctly.
    """
    if device_type not in HTS_TEMPERATURE_DEVICE_TYPES:
        return None
    raw = kv.get(DEVICE_KEY_TEMPERATURE_C)
    if not raw:
        return None
    value = int.from_bytes(raw[:1], "big", signed=True)
    if not (_HTS_TEMP_MIN_C <= value <= _HTS_TEMP_MAX_C):
        return None
    return float(value)


def parse_device_readings(
    device_type: str,
    kv: dict[int, bytes],
    existing: DeviceReadings | None = None,
) -> DeviceReadings | None:
    """Map a per-device TLV kv block to `DeviceReadings`.

    Returns `None` when the device type does not emit electrical
    readings (so callers can early-out and avoid creating empty
    snapshots). For an electrical-capable device the return is always
    a `DeviceReadings` instance.

    If *existing* is provided only fields whose sub-keys are present in
    *kv* are updated; all other fields retain their values from
    *existing*. This is the same merge semantics `parse_hub_params`
    uses, and matters because the hub pushes per-device deltas
    (`STATUS_UPDATE`) that frequently carry just one electrical sub-key
    — or none — alongside e.g. the relay state byte. Without the merge,
    every relay toggle would null out the cached readings and the
    sensor would render `unknown` until the next full snapshot
    (#123 regression).
    """
    key_map = _DEVICE_READINGS_KEY_MAP.get(device_type)
    if key_map is None:
        return None
    base = existing if existing is not None else DeviceReadings()
    fields: dict[str, float | None] = dataclasses.asdict(base)
    for sub_key, (field_name, scale) in key_map.items():
        raw_bytes = kv.get(sub_key)
        if raw_bytes is None:
            continue
        raw = _int_be_val(raw_bytes)
        if raw is None:
            continue
        # `round` keeps an integer scale integral (round(2409, 3) is 2409) and
        # trims the binary-float noise a fractional one leaves behind
        # (11671 * 0.001 is 11.671000000000001).
        fields[field_name] = round(raw * scale, 3)
    return DeviceReadings(**fields)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_hub_params(
    params: dict[int, bytes],
    existing: HubNetworkState | None = None,
) -> HubNetworkState:
    """Parse a TLV key-value dict into a HubNetworkState.

    If *existing* is provided only fields whose keys are present in *params*
    are updated; all other fields retain their values from *existing*.  This
    supports incremental (delta) updates sent by the hub.

    Args:
        params: Mapping of TLV key → raw bytes value.
        existing: Optional prior state to merge into.

    Returns:
        A new frozen HubNetworkState instance.
    """
    base = existing if existing is not None else HubNetworkState()

    updates: dict[str, object] = {}

    # Active channels bitmask ------------------------------------------------
    if KEY_ACTIVE_CHANNELS in params:
        mask = _byte_val(params[KEY_ACTIVE_CHANNELS])
        updates["ethernet_connected"] = bool(mask & (1 << _ACTIVE_CHANNELS_ETH_BIT))
        updates["wifi_connected"] = bool(mask & (1 << _ACTIVE_CHANNELS_WIFI_BIT))
        updates["gsm_connected"] = bool(mask & (1 << _ACTIVE_CHANNELS_GSM_BIT))

    # Power ------------------------------------------------------------------
    if KEY_HUB_POWERED in params:
        updates["externally_powered"] = _bool_val(params[KEY_HUB_POWERED])

    # Firmware ---------------------------------------------------------------
    if KEY_HUB_FIRMWARE in params:
        raw = params[KEY_HUB_FIRMWARE]
        updates["firmware_version"] = _version_val(raw)
        updates["firmware_version_raw"] = int.from_bytes(raw, "big") if len(raw) == 4 else 0

    # Siren settings (#438) ----------------------------------------------------
    if KEY_SIREN_ON_PANIC_BUTTON in params:
        siren_raw = _int_be_val(params[KEY_SIREN_ON_PANIC_BUTTON])
        if siren_raw is not None:
            updates["siren_on_panic_button"] = siren_raw != 0
            updates["siren_on_panic_button_raw"] = siren_raw
    if KEY_SIREN_ON_ANY_TAMPER in params:
        siren_raw = _int_be_val(params[KEY_SIREN_ON_ANY_TAMPER])
        if siren_raw is not None:
            updates["siren_on_any_tamper"] = siren_raw != 0
            updates["siren_on_any_tamper_raw"] = siren_raw

    # Restoration after alarm (#634) --------------------------------------------
    if KEY_ALARM_HAPPENED in params:
        alarm_raw = _int_be_val(params[KEY_ALARM_HAPPENED])
        if alarm_raw is not None:
            updates["restore_pending"] = alarm_raw != 0
            updates["alarm_happened_raw"] = alarm_raw
    if KEY_RESTORE_REQUIRED in params:
        required_raw = _int_be_val(params[KEY_RESTORE_REQUIRED])
        if required_raw is not None:
            updates["restore_required_raw"] = required_raw

    # Ethernet ---------------------------------------------------------------
    if KEY_ETH_ENABLED in params:
        updates["ethernet_enabled"] = _bool_val(params[KEY_ETH_ENABLED])
    if KEY_ETH_DHCP in params:
        updates["ethernet_dhcp"] = _bool_val(params[KEY_ETH_DHCP])
    if KEY_ETH_IP in params:
        updates["ethernet_ip"] = _ip_val(params[KEY_ETH_IP])
    if KEY_ETH_MASK in params:
        updates["ethernet_mask"] = _ip_val(params[KEY_ETH_MASK])
    if KEY_ETH_GATE in params:
        updates["ethernet_gateway"] = _ip_val(params[KEY_ETH_GATE])
    if KEY_ETH_DNS in params:
        updates["ethernet_dns"] = _ip_val(params[KEY_ETH_DNS])

    # Wi-Fi ------------------------------------------------------------------
    if KEY_WIFI_ENABLED in params:
        updates["wifi_enabled"] = _bool_val(params[KEY_WIFI_ENABLED])
    if KEY_WIFI_SSID in params:
        updates["wifi_ssid"] = _str_val(params[KEY_WIFI_SSID])
    if KEY_WIFI_LEVEL in params:
        updates["wifi_signal_level"] = WIFI_SIGNAL_MAP.get(
            _byte_val(params[KEY_WIFI_LEVEL]), "unknown"
        )
    if KEY_WIFI_IP in params:
        updates["wifi_ip"] = _ip_val(params[KEY_WIFI_IP])

    # GSM --------------------------------------------------------------------
    if KEY_GSM_SIGNAL_LVL in params:
        raw = params[KEY_GSM_SIGNAL_LVL]
        # May be 1 or 2 bytes; use last byte for the signal level
        sig = raw[-1] if raw else 0
        updates["gsm_signal_level"] = GSM_SIGNAL_MAP.get(sig, "unknown")
    if KEY_GSM_NETWORK_STATUS in params:
        updates["gsm_network_type"] = GSM_NETWORK_MAP.get(
            _byte_val(params[KEY_GSM_NETWORK_STATUS]), "unknown"
        )

    return dataclasses.replace(base, **updates)  # type: ignore[arg-type]
