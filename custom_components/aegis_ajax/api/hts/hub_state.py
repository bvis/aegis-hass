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
# is `current_ma`. The Ajax mobile app reads them through a separate TLV
# container (`poz0` in Ajax PRO 2.47 smali) — see #123 for the audit
# trail.

DEVICE_KEY_VOLTAGE_V = 0x35
DEVICE_KEY_CURRENT_MA = 0x42
DEVICE_KEY_POWER_CONSUMED_WH = 0x43

# device_type strings (as produced by `DevicesApi`) that emit the
# electrical-reading sub-keys. Mirrors SWITCH_DEVICE_TYPES in switch.py
# minus the light-switch variants whose firmware doesn't measure current
# (those are dry-contact relays, not load-meters). When a new
# electrical-reading-capable device family appears, add it here and to
# SWITCH_DEVICE_TYPES.
ELECTRICAL_DEVICE_TYPES: frozenset[str] = frozenset(
    {
        "wall_switch",
        "relay",
        "relay_fibra_base",
        "socket",
        "socket_b",
        "socket_g",
        "socket_outlet_type_e",
        "socket_outlet_type_f",
        "socket_type_g_plus",
    }
)


@dataclasses.dataclass(frozen=True)
class DeviceReadings:
    """Immutable snapshot of a single device's electrical readings.

    All three fields are `None` when the device does not emit them,
    when the body simply hadn't been refreshed yet, or when the
    sub-key was missing on a body that did include the device.
    Consumers should treat any `None` as "no measurement available"
    and render the entity as `unknown` rather than zero.

    `voltage_v` is a signed short straight from the wire (sub-key
    0x35, named `voltage` in Ajax PRO 2.47's `poz0` TLV container);
    units are volts as the device reports them, no scaling. Older
    WallSwitch firmwares omit the sub-key entirely — power-derived
    callers must fall back to a nominal voltage in that case.
    """

    current_ma: int | None = None
    power_consumed_wh: int | None = None
    voltage_v: int | None = None


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
    (`STATUS_UPDATE`) that frequently carry just one of the two
    electrical sub-keys — or neither — alongside e.g. the relay
    state byte. Without the merge, every relay toggle would null out
    the cached current / energy readings and the sensor would render
    `unknown` until the next full snapshot (#123 regression).
    """
    if device_type not in ELECTRICAL_DEVICE_TYPES:
        return None
    base = existing if existing is not None else DeviceReadings()
    return DeviceReadings(
        current_ma=(
            _int_be_val(kv[DEVICE_KEY_CURRENT_MA])
            if DEVICE_KEY_CURRENT_MA in kv
            else base.current_ma
        ),
        power_consumed_wh=(
            _int_be_val(kv[DEVICE_KEY_POWER_CONSUMED_WH])
            if DEVICE_KEY_POWER_CONSUMED_WH in kv
            else base.power_consumed_wh
        ),
        voltage_v=(
            _int_be_val(kv[DEVICE_KEY_VOLTAGE_V]) if DEVICE_KEY_VOLTAGE_V in kv else base.voltage_v
        ),
    )


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
