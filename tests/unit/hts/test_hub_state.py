"""Tests for hub network state parser (TLV key-value → HubNetworkState)."""

import dataclasses

import pytest

from custom_components.aegis_ajax.api.hts.hub_state import (
    KEY_ACTIVE_CHANNELS,
    KEY_ETH_DHCP,
    KEY_ETH_DNS,
    KEY_ETH_GATE,
    KEY_ETH_IP,
    KEY_ETH_MASK,
    KEY_GSM_NETWORK_STATUS,
    KEY_GSM_SIGNAL_LVL,
    KEY_HUB_POWERED,
    KEY_WIFI_SSID,
    HubNetworkState,
    _bool_val,
    _byte_val,
    _int_to_ip,
    _ip_val,
    _str_val,
    parse_hub_params,
)

# ---------------------------------------------------------------------------
# HubNetworkState defaults
# ---------------------------------------------------------------------------


class TestHubNetworkStateDefaults:
    def test_connections_default_false(self) -> None:
        s = HubNetworkState()
        assert s.ethernet_connected is False
        assert s.wifi_connected is False
        assert s.gsm_connected is False

    def test_signal_levels_default_unknown(self) -> None:
        s = HubNetworkState()
        assert s.gsm_signal_level == "unknown"
        assert s.gsm_network_type == "unknown"
        assert s.wifi_signal_level == "unknown"

    def test_externally_powered_default_false(self) -> None:
        assert HubNetworkState().externally_powered is False

    def test_primary_connection_none_by_default(self) -> None:
        assert HubNetworkState().primary_connection == "none"

    def test_is_frozen(self) -> None:
        s = HubNetworkState()
        with pytest.raises(dataclasses.FrozenInstanceError):
            s.ethernet_connected = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# primary_connection priority
# ---------------------------------------------------------------------------


class TestPrimaryConnection:
    def test_ethernet_wins_over_all(self) -> None:
        s = HubNetworkState(ethernet_connected=True, wifi_connected=True, gsm_connected=True)
        assert s.primary_connection == "ethernet"

    def test_wifi_wins_over_gsm(self) -> None:
        s = HubNetworkState(wifi_connected=True, gsm_connected=True)
        assert s.primary_connection == "wifi"

    def test_gsm_only(self) -> None:
        s = HubNetworkState(gsm_connected=True)
        assert s.primary_connection == "gsm"

    def test_none_when_all_false(self) -> None:
        assert HubNetworkState().primary_connection == "none"


# ---------------------------------------------------------------------------
# active_channels bitmask (bit0=eth, bit1=wifi, bit2=gsm)
# ---------------------------------------------------------------------------


class TestActiveChannelsBitmask:
    def test_bit0_sets_ethernet(self) -> None:
        state = parse_hub_params({KEY_ACTIVE_CHANNELS: bytes([0b001])})
        assert state.ethernet_connected is True
        assert state.wifi_connected is False
        assert state.gsm_connected is False

    def test_bit1_sets_wifi(self) -> None:
        state = parse_hub_params({KEY_ACTIVE_CHANNELS: bytes([0b010])})
        assert state.wifi_connected is True
        assert state.ethernet_connected is False
        assert state.gsm_connected is False

    def test_bit2_sets_gsm(self) -> None:
        state = parse_hub_params({KEY_ACTIVE_CHANNELS: bytes([0b100])})
        assert state.gsm_connected is True
        assert state.ethernet_connected is False
        assert state.wifi_connected is False

    def test_all_bits_set(self) -> None:
        state = parse_hub_params({KEY_ACTIVE_CHANNELS: bytes([0b111])})
        assert state.ethernet_connected is True
        assert state.wifi_connected is True
        assert state.gsm_connected is True

    def test_zero_clears_all(self) -> None:
        state = parse_hub_params({KEY_ACTIVE_CHANNELS: bytes([0x00])})
        assert state.ethernet_connected is False
        assert state.wifi_connected is False
        assert state.gsm_connected is False


# ---------------------------------------------------------------------------
# Ethernet IP parsing
# ---------------------------------------------------------------------------


class TestEthernetIP:
    def test_parse_ip_192_168_1_1(self) -> None:
        ip_bytes = bytes([192, 168, 1, 1])
        state = parse_hub_params({KEY_ETH_IP: ip_bytes})
        assert state.ethernet_ip == "192.168.1.1"

    def test_parse_ip_10_0_0_1(self) -> None:
        ip_bytes = bytes([10, 0, 0, 1])
        state = parse_hub_params({KEY_ETH_IP: ip_bytes})
        assert state.ethernet_ip == "10.0.0.1"

    def test_parse_mask(self) -> None:
        mask_bytes = bytes([255, 255, 255, 0])
        state = parse_hub_params({KEY_ETH_MASK: mask_bytes})
        assert state.ethernet_mask == "255.255.255.0"

    def test_parse_gateway(self) -> None:
        gw_bytes = bytes([10, 0, 0, 254])
        state = parse_hub_params({KEY_ETH_GATE: gw_bytes})
        assert state.ethernet_gateway == "10.0.0.254"

    def test_parse_dns(self) -> None:
        dns_bytes = bytes([8, 8, 8, 8])
        state = parse_hub_params({KEY_ETH_DNS: dns_bytes})
        assert state.ethernet_dns == "8.8.8.8"


# ---------------------------------------------------------------------------
# hub_powered
# ---------------------------------------------------------------------------


class TestHubPowered:
    def test_powered_on(self) -> None:
        state = parse_hub_params({KEY_HUB_POWERED: bytes([1])})
        assert state.externally_powered is True

    def test_powered_off(self) -> None:
        state = parse_hub_params({KEY_HUB_POWERED: bytes([0])})
        assert state.externally_powered is False

    def test_nonzero_is_true(self) -> None:
        state = parse_hub_params({KEY_HUB_POWERED: bytes([0xFF])})
        assert state.externally_powered is True


# ---------------------------------------------------------------------------
# GSM signal
# ---------------------------------------------------------------------------


class TestGsmSignal:
    @pytest.mark.parametrize(
        "code, expected",
        [(0, "unknown"), (1, "weak"), (2, "normal"), (3, "strong")],
    )
    def test_gsm_signal_map(self, code: int, expected: str) -> None:
        state = parse_hub_params({KEY_GSM_SIGNAL_LVL: bytes([code])})
        assert state.gsm_signal_level == expected

    def test_unknown_code_returns_unknown(self) -> None:
        state = parse_hub_params({KEY_GSM_SIGNAL_LVL: bytes([99])})
        assert state.gsm_signal_level == "unknown"


# ---------------------------------------------------------------------------
# GSM network type
# ---------------------------------------------------------------------------


class TestGsmNetwork:
    @pytest.mark.parametrize(
        "code, expected",
        [(0, "unknown"), (1, "gsm"), (2, "2g"), (3, "3g"), (4, "4g")],
    )
    def test_gsm_network_map(self, code: int, expected: str) -> None:
        state = parse_hub_params({KEY_GSM_NETWORK_STATUS: bytes([code])})
        assert state.gsm_network_type == expected

    def test_unknown_code_returns_unknown(self) -> None:
        state = parse_hub_params({KEY_GSM_NETWORK_STATUS: bytes([99])})
        assert state.gsm_network_type == "unknown"


# ---------------------------------------------------------------------------
# Wi-Fi SSID
# ---------------------------------------------------------------------------


class TestWifiSsid:
    def test_plain_ssid(self) -> None:
        state = parse_hub_params({KEY_WIFI_SSID: b"MyNetwork"})
        assert state.wifi_ssid == "MyNetwork"

    def test_null_terminated_ssid(self) -> None:
        state = parse_hub_params({KEY_WIFI_SSID: b"MyNetwork\x00garbage"})
        assert state.wifi_ssid == "MyNetwork"

    def test_empty_ssid(self) -> None:
        state = parse_hub_params({KEY_WIFI_SSID: b""})
        assert state.wifi_ssid == ""


# ---------------------------------------------------------------------------
# Ethernet DHCP
# ---------------------------------------------------------------------------


class TestEthernetDhcp:
    def test_dhcp_enabled(self) -> None:
        state = parse_hub_params({KEY_ETH_DHCP: bytes([1])})
        assert state.ethernet_dhcp is True

    def test_dhcp_disabled(self) -> None:
        state = parse_hub_params({KEY_ETH_DHCP: bytes([0])})
        assert state.ethernet_dhcp is False


# ---------------------------------------------------------------------------
# Merge with existing state (incremental updates)
# ---------------------------------------------------------------------------


class TestMergeWithExisting:
    def test_unmentioned_fields_preserved(self) -> None:
        existing = HubNetworkState(
            ethernet_connected=True,
            ethernet_ip="10.0.0.1",
            gsm_signal_level="strong",
        )
        # Only update hub_powered; all other fields must stay
        updated = parse_hub_params({KEY_HUB_POWERED: bytes([1])}, existing=existing)
        assert updated.ethernet_connected is True
        assert updated.ethernet_ip == "10.0.0.1"
        assert updated.gsm_signal_level == "strong"
        assert updated.externally_powered is True

    def test_updated_field_overwrites(self) -> None:
        existing = HubNetworkState(ethernet_ip="192.168.0.1")
        updated = parse_hub_params({KEY_ETH_IP: bytes([10, 0, 0, 2])}, existing=existing)
        assert updated.ethernet_ip == "10.0.0.2"

    def test_empty_params_returns_clone_of_existing(self) -> None:
        existing = HubNetworkState(wifi_connected=True, wifi_ssid="Home")
        updated = parse_hub_params({}, existing=existing)
        assert updated == existing

    def test_none_existing_uses_defaults(self) -> None:
        state = parse_hub_params({KEY_HUB_POWERED: bytes([1])}, existing=None)
        assert state.externally_powered is True
        assert state.ethernet_connected is False  # default

    def test_multiple_keys_merged(self) -> None:
        existing = HubNetworkState(gsm_connected=True)
        params = {
            KEY_ACTIVE_CHANNELS: bytes([0b011]),  # eth + wifi, clear gsm
            KEY_WIFI_SSID: b"Office",
            KEY_HUB_POWERED: bytes([1]),
        }
        updated = parse_hub_params(params, existing=existing)
        assert updated.ethernet_connected is True
        assert updated.wifi_connected is True
        assert updated.gsm_connected is False
        assert updated.wifi_ssid == "Office"
        assert updated.externally_powered is True


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_int_to_ip(self) -> None:
        assert _int_to_ip(0xC0A80101) == "192.168.1.1"
        assert _int_to_ip(0x00000000) == "0.0.0.0"
        assert _int_to_ip(0xFFFFFFFF) == "255.255.255.255"

    def test_byte_val(self) -> None:
        assert _byte_val(b"\x05") == 5
        assert _byte_val(b"\x00") == 0
        assert _byte_val(b"\xff\x01") == 255

    def test_bool_val(self) -> None:
        assert _bool_val(b"\x01") is True
        assert _bool_val(b"\x00") is False
        assert _bool_val(b"\xff") is True

    def test_str_val_plain(self) -> None:
        assert _str_val(b"hello") == "hello"

    def test_str_val_null_terminated(self) -> None:
        assert _str_val(b"hello\x00world") == "hello"

    def test_ip_val_four_bytes(self) -> None:
        assert _ip_val(bytes([192, 168, 0, 1])) == "192.168.0.1"

    def test_ip_val_too_short_returns_empty(self) -> None:
        assert _ip_val(bytes([192, 168])) == ""


# ---------------------------------------------------------------------------
# Per-device readings (WallSwitch / Socket family, #123)
# ---------------------------------------------------------------------------

from custom_components.aegis_ajax.api.hts.hub_state import (  # noqa: E402
    DEVICE_KEY_CURRENT_MA,
    DEVICE_KEY_OUTLET_CURRENT_10MA,
    DEVICE_KEY_OUTLET_ENERGY_WH,
    DEVICE_KEY_OUTLET_POWER_W,
    DEVICE_KEY_OUTLET_VOLTAGE_V,
    DEVICE_KEY_POWER_CONSUMED_WH,
    DEVICE_KEY_TEMPERATURE_C,
    DEVICE_KEY_VOLTAGE_V,
    DIRECT_POWER_DEVICE_TYPES,
    ELECTRICAL_DEVICE_TYPES,
    HTS_TEMPERATURE_DEVICE_TYPES,
    DeviceReadings,
    _int_be_val,
    parse_device_readings,
    parse_device_temperature_c,
)


class TestIntBeVal:
    def test_returns_none_for_none_or_empty(self) -> None:
        assert _int_be_val(None) is None
        assert _int_be_val(b"") is None

    def test_single_byte(self) -> None:
        assert _int_be_val(b"\x28") == 40

    def test_two_bytes_be(self) -> None:
        assert _int_be_val(b"\x01\x00") == 256

    def test_four_bytes_be(self) -> None:
        assert _int_be_val(b"\x00\x00\x09\x69") == 0x969  # 2409


class TestDeviceReadingsDefaults:
    def test_defaults_none(self) -> None:
        r = DeviceReadings()
        assert r.current_ma is None
        assert r.power_consumed_wh is None

    def test_is_frozen(self) -> None:
        r = DeviceReadings(current_ma=40, power_consumed_wh=2411)
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.current_ma = 99  # type: ignore[misc]


class TestParseDeviceReadings:
    def test_non_electrical_type_returns_none(self) -> None:
        # MotionProtect-family detectors don't emit current_ma / power_wh.
        assert (
            parse_device_readings(
                "motion_protect",
                {DEVICE_KEY_CURRENT_MA: b"\x28", DEVICE_KEY_POWER_CONSUMED_WH: b"\x09"},
            )
            is None
        )

    def test_wall_switch_full(self) -> None:
        r = parse_device_readings(
            "wall_switch",
            {
                DEVICE_KEY_CURRENT_MA: b"\x00\x00\x00\x28",  # 40 mA
                DEVICE_KEY_POWER_CONSUMED_WH: b"\x00\x00\x09\x69",  # 2409 Wh
                DEVICE_KEY_VOLTAGE_V: b"\x00\xe6",  # 230 V
            },
        )
        assert r == DeviceReadings(current_ma=40, power_consumed_wh=2409, voltage_v=230)

    def test_voltage_parsed_when_other_keys_absent(self) -> None:
        # Voltage updates can land on their own — current and energy
        # must stay absent rather than zeroed.
        r = parse_device_readings(
            "wall_switch",
            {DEVICE_KEY_VOLTAGE_V: b"\x00\xe7"},  # 231 V
        )
        assert r == DeviceReadings(current_ma=None, power_consumed_wh=None, voltage_v=231)

    def test_partial_update_with_only_voltage_keeps_current_and_energy(self) -> None:
        prior = DeviceReadings(current_ma=40, power_consumed_wh=2409, voltage_v=228)
        r = parse_device_readings(
            "wall_switch",
            {DEVICE_KEY_VOLTAGE_V: b"\x00\xe7"},  # 231 V
            existing=prior,
        )
        assert r == DeviceReadings(current_ma=40, power_consumed_wh=2409, voltage_v=231)

    def test_socket_partial_only_current(self) -> None:
        # Power-consumed sub-key may be absent on a freshly-installed device.
        r = parse_device_readings(
            "socket",
            {DEVICE_KEY_CURRENT_MA: b"\x05"},
        )
        assert r == DeviceReadings(current_ma=5, power_consumed_wh=None)

    def test_electrical_type_with_empty_kv(self) -> None:
        # The device's row was in the body but it carried no readings —
        # returns a DeviceReadings with both fields None, not None.
        r = parse_device_readings("relay_fibra_base", {})
        assert r == DeviceReadings(current_ma=None, power_consumed_wh=None)

    def test_partial_update_without_keys_preserves_existing(self) -> None:
        """STATUS_UPDATE deltas often omit 0x42/0x43; cached readings must survive (#123)."""
        prior = DeviceReadings(current_ma=40, power_consumed_wh=2409)
        # kv carries an unrelated sub-key (the relay state byte) — neither
        # electrical sub-key is present.
        r = parse_device_readings("wall_switch", {0x05: b"\x01"}, existing=prior)
        assert r == prior

    def test_partial_update_with_only_current_keeps_energy(self) -> None:
        prior = DeviceReadings(current_ma=10, power_consumed_wh=2409)
        r = parse_device_readings(
            "wall_switch",
            {DEVICE_KEY_CURRENT_MA: b"\x00\x00\x00\x28"},
            existing=prior,
        )
        assert r == DeviceReadings(current_ma=40, power_consumed_wh=2409)

    def test_partial_update_with_only_energy_keeps_current(self) -> None:
        prior = DeviceReadings(current_ma=40, power_consumed_wh=1000)
        r = parse_device_readings(
            "socket",
            {DEVICE_KEY_POWER_CONSUMED_WH: b"\x00\x00\x09\x69"},
            existing=prior,
        )
        assert r == DeviceReadings(current_ma=40, power_consumed_wh=2409)

    def test_no_existing_passed_falls_back_to_overwrite(self) -> None:
        # Boot-time snapshot path: no prior cache, fresh DeviceReadings built
        # straight from the kv block.
        r = parse_device_readings(
            "wall_switch",
            {DEVICE_KEY_CURRENT_MA: b"\x28"},
        )
        assert r == DeviceReadings(current_ma=40, power_consumed_wh=None)

    def test_known_electrical_types(self) -> None:
        # Sanity-check: every type the switch platform treats as a
        # power-controllable relay is in ELECTRICAL_DEVICE_TYPES.
        for dt in (
            "wall_switch",
            "relay",
            "relay_fibra_base",
            "socket",
            "socket_b",
            "socket_g",
            "socket_type_g_plus",
            "socket_outlet_type_e",
            "socket_outlet_type_f",
        ):
            assert dt in ELECTRICAL_DEVICE_TYPES, dt

    def test_direct_power_device_types_subset(self) -> None:
        # Only Outlet Type E / F report instantaneous power directly;
        # the WallSwitch family derives it from current × voltage.
        assert frozenset({"socket_outlet_type_e", "socket_outlet_type_f"}) == (
            DIRECT_POWER_DEVICE_TYPES
        )
        assert DIRECT_POWER_DEVICE_TYPES <= ELECTRICAL_DEVICE_TYPES

    def test_socket_outlet_type_e_full(self) -> None:
        # Calibrated 2026-05-25 from SaetanSaDiablo's four-load capture
        # (#179). 2 080 W / 230 V load row: power=0x0820=2080 W,
        # current=0x037d=893 raw → ×10 mA = 8930 mA, energy=0x18c2e
        # Wh cumulative, voltage=0xe7=231 V.
        r = parse_device_readings(
            "socket_outlet_type_e",
            {
                DEVICE_KEY_OUTLET_POWER_W: b"\x08\x20",
                DEVICE_KEY_OUTLET_ENERGY_WH: b"\x00\x01\x8c\x2e",
                DEVICE_KEY_OUTLET_CURRENT_10MA: b"\x03\x7d",
                DEVICE_KEY_OUTLET_VOLTAGE_V: b"\x00\xe7",
            },
        )
        assert r == DeviceReadings(
            current_ma=8930,
            power_consumed_wh=101422,
            voltage_v=231,
            power_w=2080,
        )

    def test_socket_outlet_idle_keeps_energy_counter(self) -> None:
        # Off / idle row: power and current zero, voltage and energy
        # still reported. Voltage 0xea = 234 V; energy 0x18c0f Wh.
        r = parse_device_readings(
            "socket_outlet_type_e",
            {
                DEVICE_KEY_OUTLET_POWER_W: b"\x00\x00",
                DEVICE_KEY_OUTLET_ENERGY_WH: b"\x00\x01\x8c\x0f",
                DEVICE_KEY_OUTLET_CURRENT_10MA: b"\x00\x00",
                DEVICE_KEY_OUTLET_VOLTAGE_V: b"\x00\xea",
            },
        )
        assert r == DeviceReadings(
            current_ma=0,
            power_consumed_wh=101391,
            voltage_v=234,
            power_w=0,
        )

    def test_socket_outlet_partial_keeps_existing(self) -> None:
        # STATUS_UPDATE deltas on Outlet only carry the readings that
        # actually changed. A power-only delta must not blank the
        # cached energy / voltage / current values.
        prior = DeviceReadings(
            current_ma=8930, power_consumed_wh=101422, voltage_v=231, power_w=2080
        )
        r = parse_device_readings(
            "socket_outlet_type_e",
            {DEVICE_KEY_OUTLET_POWER_W: b"\x00\x0f"},  # 15 W
            existing=prior,
        )
        assert r == DeviceReadings(
            current_ma=8930, power_consumed_wh=101422, voltage_v=231, power_w=15
        )

    def test_socket_outlet_current_scaled_by_ten(self) -> None:
        # 60 mA at 15 W / 230 V: raw 0x06 × 10 mA scale = 60 mA.
        r = parse_device_readings(
            "socket_outlet_type_f",
            {DEVICE_KEY_OUTLET_CURRENT_10MA: b"\x00\x06"},
        )
        assert r is not None
        assert r.current_ma == 60

    def test_wall_switch_does_not_populate_power_w(self) -> None:
        # WallSwitch family doesn't report instantaneous power — the
        # derived sensor handles that. `power_w` stays None even when
        # the body carries every WallSwitch sub-key.
        r = parse_device_readings(
            "wall_switch",
            {
                DEVICE_KEY_CURRENT_MA: b"\x00\x28",
                DEVICE_KEY_POWER_CONSUMED_WH: b"\x00\x00\x09\x69",
                DEVICE_KEY_VOLTAGE_V: b"\x00\xe6",
            },
        )
        assert r is not None
        assert r.power_w is None


class TestParseDeviceTemperatureC:
    """HTS sub-key 0x02 → internal temperature for gRPC-temp-less devices (#229)."""

    def test_curtain_plus_decodes_whole_celsius(self) -> None:
        # 0x1b = 27 °C, matching the value the Ajax app shows for the Plus.
        assert (
            parse_device_temperature_c(
                "motion_protect_curtain_outdoor_plus", {DEVICE_KEY_TEMPERATURE_C: b"\x1b"}
            )
            == 27.0
        )

    def test_curtain_base_decodes(self) -> None:
        assert (
            parse_device_temperature_c(
                "motion_protect_curtain_outdoor_base", {DEVICE_KEY_TEMPERATURE_C: b"\x17"}
            )
            == 23.0
        )

    def test_subzero_decodes_as_signed_int8(self) -> None:
        # Outdoor detector below freezing: 0xFB = -5 °C, not 251.
        assert (
            parse_device_temperature_c(
                "motion_protect_curtain_outdoor_plus", {DEVICE_KEY_TEMPERATURE_C: b"\xfb"}
            )
            == -5.0
        )

    def test_non_gated_type_returns_none(self) -> None:
        # Mini gets temperature over gRPC, so it's intentionally not read here;
        # neither are indoor sensors that already carry it in the light stream.
        assert (
            parse_device_temperature_c(
                "motion_protect_curtain_outdoor_mini", {DEVICE_KEY_TEMPERATURE_C: b"\x1b"}
            )
            is None
        )
        assert (
            parse_device_temperature_c("motion_protect", {DEVICE_KEY_TEMPERATURE_C: b"\x1b"})
            is None
        )

    def test_missing_subkey_returns_none(self) -> None:
        assert parse_device_temperature_c("motion_protect_curtain_outdoor_plus", {}) is None

    def test_out_of_range_value_rejected(self) -> None:
        # 0x7f = 127 °C is outside the plausible window → declined, not surfaced.
        assert (
            parse_device_temperature_c(
                "motion_protect_curtain_outdoor_plus", {DEVICE_KEY_TEMPERATURE_C: b"\x7f"}
            )
            is None
        )

    def test_gated_types_present(self) -> None:
        assert "motion_protect_curtain_outdoor_plus" in HTS_TEMPERATURE_DEVICE_TYPES
        assert "motion_protect_curtain_outdoor_base" in HTS_TEMPERATURE_DEVICE_TYPES
        # Mini stays gRPC-only — it carries device_temperature in its HubDevice
        # message and has no confirmed HTS 0x02 sample.
        assert "motion_protect_curtain_outdoor_mini" not in HTS_TEMPERATURE_DEVICE_TYPES

    def test_sirens_decode_from_0x02(self) -> None:
        # #312/#269: sirens carry their internal temperature on HTS 0x02 (the
        # value the Ajax app shows), confirmed for both the indoor HomeSiren and
        # the outdoor StreetSiren. They are sourced from 0x02, not the gRPC
        # board temperature, so the reading matches the app and updates live.
        assert (
            parse_device_temperature_c("street_siren", {DEVICE_KEY_TEMPERATURE_C: b"\x19"}) == 25.0
        )
        assert parse_device_temperature_c("home_siren", {DEVICE_KEY_TEMPERATURE_C: b"\x17"}) == 23.0

    def test_siren_types_in_gated_set(self) -> None:
        for siren_type in (
            "street_siren",
            "street_siren_plus_g3",
            "home_siren",
            "home_siren_g3",
            "home_siren_s",
            "home_siren_fibra",
        ):
            assert siren_type in HTS_TEMPERATURE_DEVICE_TYPES
