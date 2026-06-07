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

    # Active connection flags (derived from KEY_ACTIVE_CHANNELS bitmask)
    ethernet_connected: bool = False
    wifi_connected: bool = False
    gsm_connected: bool = False

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

    @property
    def primary_connection(self) -> str:
        """Return the highest-priority active connection type."""
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

    `voltage_v` is the device-reported line voltage in volts (no
    scaling). Older WallSwitch firmwares omit it entirely — the
    derived-power sensor falls back to a nominal voltage in that case.

    `power_w` is the device-reported instantaneous power in watts. Only
    Outlet Type E / Type F report it directly; the WallSwitch family
    keeps it `None` and lets the derived-power sensor compute `current
    × voltage`.
    """

    current_ma: int | None = None
    power_consumed_wh: int | None = None
    voltage_v: int | None = None
    power_w: int | None = None


# Per-device-family sub-key map. Same DeviceReadings shape feeds both
# families; the mapping selects which sub-keys land in which field and
# at what scale. Scale converts the raw big-endian integer into the
# field's stored unit (e.g. Outlet current is reported in 10 mA units,
# so scale=10 yields the same mA contract the WallSwitch path already
# uses downstream).
_WALLSWITCH_KEY_MAP: dict[int, tuple[str, int]] = {
    DEVICE_KEY_VOLTAGE_V: ("voltage_v", 1),
    DEVICE_KEY_CURRENT_MA: ("current_ma", 1),
    DEVICE_KEY_POWER_CONSUMED_WH: ("power_consumed_wh", 1),
}
_OUTLET_KEY_MAP: dict[int, tuple[str, int]] = {
    DEVICE_KEY_OUTLET_POWER_W: ("power_w", 1),
    DEVICE_KEY_OUTLET_ENERGY_WH: ("power_consumed_wh", 1),
    DEVICE_KEY_OUTLET_CURRENT_10MA: ("current_ma", 10),
    DEVICE_KEY_OUTLET_VOLTAGE_V: ("voltage_v", 1),
}

_DEVICE_READINGS_KEY_MAP: dict[str, dict[int, tuple[str, int]]] = {
    "wall_switch": _WALLSWITCH_KEY_MAP,
    "relay": _WALLSWITCH_KEY_MAP,
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
    fields: dict[str, int | None] = dataclasses.asdict(base)
    for sub_key, (field_name, scale) in key_map.items():
        raw_bytes = kv.get(sub_key)
        if raw_bytes is None:
            continue
        raw = _int_be_val(raw_bytes)
        if raw is None:
            continue
        fields[field_name] = raw * scale
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
