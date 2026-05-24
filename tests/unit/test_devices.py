"""Tests for devices API."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

import pytest

from custom_components.aegis_ajax.api.devices import (
    DevicesApi,
    _decode_proto_wire_shape,
    _encode_string_field,
    _encode_varint_field,
    _redact_proto_bytes_to_hex,
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

    def test_parse_unsupported_oneof_returns_none(self) -> None:
        # `video_edge` (the recorder), `smart_lock` (the auxiliary
        # smart-lock variant) and an unset oneof all fall through to
        # None — only `hub_device` and `video_edge_channel` are mapped.
        proto_device = MagicMock()
        proto_device.WhichOneof.return_value = "video_edge"
        assert DevicesApi.parse_device(proto_device) is None

    def test_parse_unsupported_oneof_with_unknown_field_logs_probe(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When `device_kind is None` and the proto has unknown-field bytes
        (a NEW oneof case the cloud emits but our `.proto` doesn't know),
        a DEBUG probe surfaces the wire-shape AND a PII-redacted hex dump
        so the new field number can be identified from a single capture.

        Hypothesis behind #179 (SaetanSaDiablo's Outlet Type E): the cloud
        is pushing live readings via a NEW `LightDevice.device` oneof
        case at e.g. field 5+, which protobuf silently drops because we
        only have fields 1-4 in the compiled proto. The probe stays at
        DEBUG so a default-level install pays nothing.
        """
        import logging as _logging  # noqa: PLC0415

        # A real LightDevice with a synthetic field-5 bytes payload that
        # carries the user's device name in ASCII to verify the redaction
        # path. Built via raw wire-format encoding so the proto class
        # doesn't need to know about field 5.
        from v3.mobilegwsvc.commonmodels.space.device.light import (  # noqa: PLC0415
            light_device_pb2,
        )

        device_name = b"Living Room Outlet"
        # Tag for field=5, wire_type=2 (length-delimited) is (5<<3)|2 = 42 = 0x2A
        # Followed by varint length and the bytes.
        unknown_field = bytes([0x2A, len(device_name), *device_name])
        proto_device = light_device_pb2.LightDevice()
        proto_device.MergeFromString(unknown_field)
        # Confirm WhichOneof returns None even though raw bytes were merged.
        assert proto_device.WhichOneof("device") is None

        with caplog.at_level(_logging.DEBUG, logger="custom_components.aegis_ajax.api.devices"):
            result = DevicesApi.parse_device(proto_device)

        assert result is None
        # Wire-shape line names the new field number with no contents.
        assert any(
            "wire-shape" in record.message and "f5=bytes" in record.message
            for record in caplog.records
        )
        # Bytes line includes the redaction marker — the device name
        # MUST NOT appear in cleartext, only the length stays visible.
        bytes_line = next(record.message for record in caplog.records if "bytes:" in record.message)
        assert "Living Room Outlet" not in bytes_line
        assert f"<text:{len(device_name)}b>" in bytes_line

    def test_parse_unsupported_oneof_with_empty_proto_logs_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An empty `LightDevice` (oneof unset, no unknown fields either)
        skips the byte probe — there's nothing to surface and we don't
        want to clutter DEBUG with `<empty>` lines for genuine heartbeats.
        """
        import logging as _logging  # noqa: PLC0415

        from v3.mobilegwsvc.commonmodels.space.device.light import (  # noqa: PLC0415
            light_device_pb2,
        )

        proto_device = light_device_pb2.LightDevice()
        with caplog.at_level(_logging.DEBUG, logger="custom_components.aegis_ajax.api.devices"):
            result = DevicesApi.parse_device(proto_device)

        assert result is None
        # The "Skipping unsupported" line is always emitted; the byte
        # probe lines are not, because the serialized form is empty.
        assert any("Skipping unsupported device type" in r.message for r in caplog.records)
        assert not any("wire-shape" in r.message for r in caplog.records)
        assert not any("bytes:" in r.message for r in caplog.records)


class TestDecodeProtoWireShape:
    """Structural decoder used by the `parse_device` probe (#179)."""

    def test_single_length_delimited_field(self) -> None:
        # field=5, wire_type=2 (length-delimited), 4-byte payload.
        # Tag byte = (5<<3)|2 = 0x2A.
        assert _decode_proto_wire_shape(bytes([0x2A, 0x04, 0xDE, 0xAD, 0xBE, 0xEF])) == (
            "f5=bytes(4)"
        )

    def test_varint_field(self) -> None:
        # field=1, wire_type=0 (varint), value=42 (single byte).
        assert _decode_proto_wire_shape(bytes([0x08, 0x2A])) == "f1=varint"

    def test_i32_field(self) -> None:
        # field=2, wire_type=5 (i32), 4 bytes payload.
        assert _decode_proto_wire_shape(bytes([0x15, 0x00, 0x00, 0x00, 0x00])) == "f2=i32"

    def test_i64_field(self) -> None:
        # field=3, wire_type=1 (i64), 8 bytes payload.
        assert _decode_proto_wire_shape(bytes([0x19] + [0x00] * 8)) == "f3=i64"

    def test_multiple_fields(self) -> None:
        # field=1 varint=1, then field=5 length-delimited 2-byte payload.
        # Used to discriminate a known-oneof message (e.g. hub_device=1)
        # from a brand-new oneof case (field=5+) appearing alongside it.
        encoded = bytes([0x08, 0x01, 0x2A, 0x02, 0xAA, 0xBB])
        assert _decode_proto_wire_shape(encoded) == "f1=varint,f5=bytes(2)"

    def test_truncated_input_does_not_raise(self) -> None:
        # Truncation in the middle of a varint length must surface as a
        # marker rather than blowing up the DEBUG log line.
        # 0x2A = tag (field=5, wire=2); next varint length missing.
        assert "truncated" in _decode_proto_wire_shape(bytes([0x2A])).lower()

    def test_empty_input(self) -> None:
        assert _decode_proto_wire_shape(b"") == "<empty>"


class TestRedactProtoBytesToHex:
    """ASCII-aware hex renderer used by the `parse_device` probe (#179)."""

    def test_short_ascii_run_stays_hex(self) -> None:
        # 2-byte ASCII ("AB") is below the redaction threshold (3) and
        # comes through as hex — the 3-byte threshold mirrors the HTS
        # probe so incidental ASCII bytes (a single 0x41 inside a numeric
        # field) don't get masked.
        assert _redact_proto_bytes_to_hex(b"\x01AB\x00") == "014142" + "00"

    def test_long_ascii_run_is_masked_with_length(self) -> None:
        # A device-name field "Living Room Outlet" (18 chars printable
        # ASCII) is replaced by a length-preserving marker. The byte
        # offset of the surrounding bytes (here: an empty surround) stays
        # readable so the redaction doesn't break structural analysis.
        assert _redact_proto_bytes_to_hex(b"Living Room Outlet") == "<text:18b>"

    def test_mixed_binary_and_ascii(self) -> None:
        # 4-byte numeric prefix (one printable byte, three non-printable)
        # + protobuf string header (`\n` + length) + 5-byte ASCII tail.
        # The numeric prefix stays hex, the ASCII tail gets redacted.
        data = b"\xde\xad\xbe\xef\n\x05Hello"
        result = _redact_proto_bytes_to_hex(data)
        assert "<text:5b>" in result
        assert "deadbeef" in result
        assert "Hello" not in result

    def test_all_non_printable(self) -> None:
        assert _redact_proto_bytes_to_hex(bytes([0x00, 0x01, 0xFF])) == "0001ff"

    def test_empty(self) -> None:
        assert _redact_proto_bytes_to_hex(b"") == ""

    def test_parse_video_edge_channel_doorbell(self) -> None:
        # @Permudious in #119: MotionCam Video Doorbell arrives as a
        # `video_edge_channel` LightDevice oneof case, not `hub_device`,
        # so it was being skipped entirely. The fix parses the channel
        # and emits a `video_edge_doorbell` device_type so the existing
        # `_DEVICE_TYPE_SENSORS` map surfaces motion + tamper entities
        # and HA renders a device card.
        from v3.mobilegwsvc.commonmodels.space.device.light import (
            light_device_pb2,
            light_device_profile_pb2,
        )
        from v3.mobilegwsvc.commonmodels.video.videoedge.light import (
            light_video_edge_pb2,
        )

        about_type_doorbell = 5  # About.Type.DOORBELL
        light_device = light_device_pb2.LightDevice(
            video_edge_channel=light_video_edge_pb2.LightVideoEdgeChannel(
                profile=light_device_profile_pb2.LightDeviceProfile(
                    id="cam-front-door",
                    name="Front Door Doorbell",
                ),
                video_edge_channel_properties=(
                    light_video_edge_pb2.LightVideoEdgeChannel.VideoEdgeChannelProperties(
                        video_edge_type=about_type_doorbell,
                    )
                ),
            )
        )

        device = DevicesApi.parse_device(light_device)
        assert device is not None
        assert device.id == "cam-front-door"
        assert device.name == "Front Door Doorbell"
        assert device.device_type == "video_edge_doorbell"
        assert device.state == DeviceState.ONLINE

    def test_parse_video_edge_channel_indoor_camera(self) -> None:
        # INDOOR=6 is the indoor variant of the MotionCam Video family;
        # parser shouldn't pretend it's a doorbell.
        from v3.mobilegwsvc.commonmodels.space.device.light import (
            light_device_pb2,
            light_device_profile_pb2,
        )
        from v3.mobilegwsvc.commonmodels.video.videoedge.light import (
            light_video_edge_pb2,
        )

        light_device = light_device_pb2.LightDevice(
            video_edge_channel=light_video_edge_pb2.LightVideoEdgeChannel(
                profile=light_device_profile_pb2.LightDeviceProfile(
                    id="cam-living",
                    name="Living Room",
                ),
                video_edge_channel_properties=(
                    light_video_edge_pb2.LightVideoEdgeChannel.VideoEdgeChannelProperties(
                        video_edge_type=6,  # INDOOR
                    )
                ),
            )
        )

        device = DevicesApi.parse_device(light_device)
        assert device is not None
        assert device.device_type == "video_edge_indoor"

    def test_parse_video_edge_channel_unknown_type(self) -> None:
        # `About.Type` is open-ended; new firmwares can introduce
        # values we don't know yet. Emit a generic `video_edge_unknown`
        # so the device still surfaces as a HA card with at least the
        # device-agnostic sensors (battery, signal_strength). Beats
        # silently dropping the device.
        from v3.mobilegwsvc.commonmodels.space.device.light import (
            light_device_pb2,
            light_device_profile_pb2,
        )
        from v3.mobilegwsvc.commonmodels.video.videoedge.light import (
            light_video_edge_pb2,
        )

        light_device = light_device_pb2.LightDevice(
            video_edge_channel=light_video_edge_pb2.LightVideoEdgeChannel(
                profile=light_device_profile_pb2.LightDeviceProfile(
                    id="cam-mystery", name="Mystery"
                ),
                video_edge_channel_properties=(
                    light_video_edge_pb2.LightVideoEdgeChannel.VideoEdgeChannelProperties(
                        video_edge_type=999,
                    )
                ),
            )
        )

        device = DevicesApi.parse_device(light_device)
        assert device is not None
        assert device.device_type == "video_edge_unknown"

    def test_parse_video_edge_channel_no_properties_returns_none(self) -> None:
        # If the channel comes without `video_edge_channel_properties`,
        # we have no type signal at all. Falling back to None is fine —
        # it's the same behaviour as today and Ajax has never sent us
        # such a payload in observed snapshots.
        from v3.mobilegwsvc.commonmodels.space.device.light import (
            light_device_pb2,
            light_device_profile_pb2,
        )
        from v3.mobilegwsvc.commonmodels.video.videoedge.light import (
            light_video_edge_pb2,
        )

        light_device = light_device_pb2.LightDevice(
            video_edge_channel=light_video_edge_pb2.LightVideoEdgeChannel(
                profile=light_device_profile_pb2.LightDeviceProfile(id="x", name="x"),
            )
        )

        assert DevicesApi.parse_device(light_device) is None


class TestSpreadPropertiesParser:
    """Cover the `LightHubDevice.spread_properties` walk for switch state.

    Built around real proto messages — `MagicMock` would silently
    accept attribute reads and let the tests pass even if the parser
    grabbed the wrong field name (the same MagicMock-hides-bug pattern
    that masked the System Health regression in #106).
    """

    def test_relay_channel_on_populates_switch_ch1(self) -> None:
        from v3.mobilegwsvc.commonmodels.hub.device.light import light_hub_device_pb2
        from v3.mobilegwsvc.commonmodels.hub.device.light.properties import (
            relay_channel_pb2,
        )

        hub_dev = light_hub_device_pb2.LightHubDevice()
        spread = hub_dev.spread_properties.add()
        spread.channel.CopyFrom(
            relay_channel_pb2.RelayChannel(
                channel_id=1,
                is_channel_on=True,
                is_transitioning=False,
            )
        )

        result = DevicesApi._parse_spread_properties(hub_dev)
        assert result == {"switch_ch1": True}

    def test_relay_channel_off_populates_switch_ch1_false(self) -> None:
        from v3.mobilegwsvc.commonmodels.hub.device.light import light_hub_device_pb2
        from v3.mobilegwsvc.commonmodels.hub.device.light.properties import (
            relay_channel_pb2,
        )

        hub_dev = light_hub_device_pb2.LightHubDevice()
        spread = hub_dev.spread_properties.add()
        spread.channel.CopyFrom(relay_channel_pb2.RelayChannel(channel_id=1, is_channel_on=False))

        result = DevicesApi._parse_spread_properties(hub_dev)
        assert result == {"switch_ch1": False}

    def test_light_switch_two_gang_populates_both_channels(self) -> None:
        # `light_switch_two_gang` exposes 2 channels via 2 entries in
        # spread_properties — the only place per-channel state lives.
        # Regression for the symptom @EpicManeuver hit in #104.
        from v3.mobilegwsvc.commonmodels.hub.device.light import light_hub_device_pb2
        from v3.mobilegwsvc.commonmodels.hub.device.light.properties import (
            light_switch_channel_pb2,
        )

        hub_dev = light_hub_device_pb2.LightHubDevice()
        e1 = hub_dev.spread_properties.add()
        e1.light_switch_channel.CopyFrom(
            light_switch_channel_pb2.LightSwitchChannel(
                id=light_switch_channel_pb2.LightSwitchChannel.CHANNEL_ID_1,
                state=light_switch_channel_pb2.LightSwitchChannel.STATE_ON,
            )
        )
        e2 = hub_dev.spread_properties.add()
        e2.light_switch_channel.CopyFrom(
            light_switch_channel_pb2.LightSwitchChannel(
                id=light_switch_channel_pb2.LightSwitchChannel.CHANNEL_ID_2,
                state=light_switch_channel_pb2.LightSwitchChannel.STATE_OFF,
            )
        )

        result = DevicesApi._parse_spread_properties(hub_dev)
        assert result == {"switch_ch1": True, "switch_ch2": False}

    def test_light_switch_dimmer_brightness_extracted(self) -> None:
        from v3.mobilegwsvc.commonmodels.hub.device.light import light_hub_device_pb2
        from v3.mobilegwsvc.commonmodels.hub.device.light.properties import (
            light_switch_channel_pb2,
        )

        hub_dev = light_hub_device_pb2.LightHubDevice()
        spread = hub_dev.spread_properties.add()
        spread.light_switch_channel.CopyFrom(
            light_switch_channel_pb2.LightSwitchChannel(
                id=light_switch_channel_pb2.LightSwitchChannel.CHANNEL_ID_1,
                state=light_switch_channel_pb2.LightSwitchChannel.STATE_ON,
                brightness=light_switch_channel_pb2.LightSwitchChannel.Brightness(level=42),
            )
        )

        result = DevicesApi._parse_spread_properties(hub_dev)
        assert result == {"switch_ch1": True, "brightness_ch1": 42}

    def test_socket_base_channel_on(self) -> None:
        from v3.mobilegwsvc.commonmodels.hub.device.light import light_hub_device_pb2
        from v3.mobilegwsvc.commonmodels.hub.device.light.properties import (
            socket_base_channel_pb2,
        )

        hub_dev = light_hub_device_pb2.LightHubDevice()
        spread = hub_dev.spread_properties.add()
        spread.socket_base_channel.CopyFrom(
            socket_base_channel_pb2.SocketBaseChannel(
                id=socket_base_channel_pb2.SocketBaseChannel.CHANNEL_ID_1,
                state=socket_base_channel_pb2.SocketBaseChannel.STATE_ON,
            )
        )

        result = DevicesApi._parse_spread_properties(hub_dev)
        assert result == {"switch_ch1": True}

    def test_unrelated_spread_properties_ignored(self) -> None:
        # photo_on_demand, billing_company, fire_zones, etc. share the
        # same oneof but aren't channel-bearing — the parser should leave
        # them alone instead of throwing.
        from v3.mobilegwsvc.commonmodels.hub.device.light import light_hub_device_pb2

        hub_dev = light_hub_device_pb2.LightHubDevice()
        spread = hub_dev.spread_properties.add()
        spread.photo_on_demand.SetInParent()

        result = DevicesApi._parse_spread_properties(hub_dev)
        assert result == {}

    def test_no_spread_properties_returns_empty(self) -> None:
        from v3.mobilegwsvc.commonmodels.hub.device.light import light_hub_device_pb2

        hub_dev = light_hub_device_pb2.LightHubDevice()
        result = DevicesApi._parse_spread_properties(hub_dev)
        assert result == {}

    def test_water_stop_channel_state_on_populates_valve_ch1_open(self) -> None:
        # Read-only valve path (#117). `STATE_ON` means the channel is
        # energised — water flowing — so the entity should report `open`.
        # If real-hardware testing later proves the WaterStop firmware
        # uses the inverted convention, this is the line to flip.
        from v3.mobilegwsvc.commonmodels.hub.device.light import light_hub_device_pb2
        from v3.mobilegwsvc.commonmodels.hub.device.light.properties import (
            water_stop_channel_pb2,
        )

        hub_dev = light_hub_device_pb2.LightHubDevice()
        spread = hub_dev.spread_properties.add()
        spread.water_stop_channel.CopyFrom(
            water_stop_channel_pb2.WaterStopChannel(
                id=water_stop_channel_pb2.WaterStopChannel.CHANNEL_ID_1,
                state=water_stop_channel_pb2.WaterStopChannel.STATE_ON,
            )
        )

        result = DevicesApi._parse_spread_properties(hub_dev)
        assert result == {"valve_ch1": True}

    def test_water_stop_channel_state_off_populates_valve_ch1_closed(self) -> None:
        from v3.mobilegwsvc.commonmodels.hub.device.light import light_hub_device_pb2
        from v3.mobilegwsvc.commonmodels.hub.device.light.properties import (
            water_stop_channel_pb2,
        )

        hub_dev = light_hub_device_pb2.LightHubDevice()
        spread = hub_dev.spread_properties.add()
        spread.water_stop_channel.CopyFrom(
            water_stop_channel_pb2.WaterStopChannel(
                id=water_stop_channel_pb2.WaterStopChannel.CHANNEL_ID_1,
                state=water_stop_channel_pb2.WaterStopChannel.STATE_OFF,
            )
        )

        result = DevicesApi._parse_spread_properties(hub_dev)
        assert result == {"valve_ch1": False}

    def test_water_stop_unknown_state_omits_valve_key(self) -> None:
        # `STATE_UNKNOWN` / `STATE_UNSPECIFIED` mean the hub didn't report
        # state (sensor reset, comms hiccup) — better to leave the key
        # absent than to fabricate a closed/open reading. The valve entity
        # then renders as `unknown` rather than wrong.
        from v3.mobilegwsvc.commonmodels.hub.device.light import light_hub_device_pb2
        from v3.mobilegwsvc.commonmodels.hub.device.light.properties import (
            water_stop_channel_pb2,
        )

        hub_dev = light_hub_device_pb2.LightHubDevice()
        spread = hub_dev.spread_properties.add()
        spread.water_stop_channel.CopyFrom(
            water_stop_channel_pb2.WaterStopChannel(
                id=water_stop_channel_pb2.WaterStopChannel.CHANNEL_ID_1,
                state=water_stop_channel_pb2.WaterStopChannel.STATE_UNKNOWN,
            )
        )

        result = DevicesApi._parse_spread_properties(hub_dev)
        assert "valve_ch1" not in result

    def test_water_stop_is_transitioning_flag(self) -> None:
        from v3.mobilegwsvc.commonmodels.hub.device.light import light_hub_device_pb2
        from v3.mobilegwsvc.commonmodels.hub.device.light.properties import (
            water_stop_channel_pb2,
        )

        hub_dev = light_hub_device_pb2.LightHubDevice()
        spread = hub_dev.spread_properties.add()
        spread.water_stop_channel.CopyFrom(
            water_stop_channel_pb2.WaterStopChannel(
                id=water_stop_channel_pb2.WaterStopChannel.CHANNEL_ID_1,
                state=water_stop_channel_pb2.WaterStopChannel.STATE_OFF,
                is_transitioning=True,
            )
        )

        result = DevicesApi._parse_spread_properties(hub_dev)
        assert result["valve_ch1_transitioning"] is True

    def test_water_stop_malfunction_is_stuck_exposed(self) -> None:
        # Distinct from the existing `water_stop_valve_stuck` Simple-flag
        # binary sensor parsed off `LightDeviceStatus.statuses`: this one
        # comes from the channel-level `malfunctions` repeated enum, which
        # is what the WaterStop firmware actually sets when the motor
        # can't seat the valve. The entity surfaces it as an attribute.
        from v3.mobilegwsvc.commonmodels.hub.device.light import light_hub_device_pb2
        from v3.mobilegwsvc.commonmodels.hub.device.light.properties import (
            water_stop_channel_pb2,
        )

        hub_dev = light_hub_device_pb2.LightHubDevice()
        spread = hub_dev.spread_properties.add()
        spread.water_stop_channel.CopyFrom(
            water_stop_channel_pb2.WaterStopChannel(
                id=water_stop_channel_pb2.WaterStopChannel.CHANNEL_ID_1,
                state=water_stop_channel_pb2.WaterStopChannel.STATE_ON,
                malfunctions=[
                    water_stop_channel_pb2.WaterStopChannel.MALFUNCTION_BATTERY_LOW_ERROR,
                    water_stop_channel_pb2.WaterStopChannel.MALFUNCTION_IS_STUCK,
                ],
            )
        )

        result = DevicesApi._parse_spread_properties(hub_dev)
        assert result["valve_ch1_stuck"] is True


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


def _LDS() -> type:  # noqa: N802
    """Shorthand to import the LightDeviceStatus proto module on demand.

    Real-proto tests use this instead of `MagicMock` so that any wrong-
    shape access (`int(sub_message)` on a sub-message, accessing a leaf
    that doesn't exist, etc.) blows up the same way it would on the
    wire — that's the lesson from #119. The proto import is hoisted into
    a helper rather than module-top so tests fail fast with a clear name
    if the import path moves rather than at collection time.
    """
    from v3.mobilegwsvc.commonmodels.space.device.light import (  # noqa: PLC0415
        light_device_status_pb2,
    )

    return light_device_status_pb2.LightDeviceStatus


class TestBatteryParser:
    def test_parse_battery_found(self) -> None:
        lds = _LDS()
        status = lds(
            battery=lds.Battery(charge_level_percentage=75, battery_state=1)  # OK
        )

        result = DevicesApi._parse_battery([status])
        assert result is not None
        assert result.level == 75
        assert result.is_low is False

    def test_parse_battery_low(self) -> None:
        lds = _LDS()
        status = lds(
            battery=lds.Battery(charge_level_percentage=10, battery_state=2)  # ERROR
        )

        result = DevicesApi._parse_battery([status])
        assert result is not None
        assert result.is_low is True

    def test_parse_battery_not_found(self) -> None:
        lds = _LDS()
        status = lds(door_opened=lds.Simple())
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
        # `ValueStatus.value` is a proto int; Ajax sends whole-degree
        # Celsius. MagicMock previously let the test set `22.5`
        # (impossible on the wire) — real proto would have truncated.
        lds = _LDS()
        status = lds(temperature=lds.ValueStatus(value=22))
        result = DevicesApi._parse_statuses([status])
        assert result.get("temperature") == 22

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

    @pytest.mark.parametrize(
        "value,expected",
        [(0, "unknown"), (1, "unlocked"), (2, "locked"), (3, "unlatched")],
    )
    def test_smart_lock_status(self, value: int, expected: str) -> None:
        # `smart_lock` is the SmartLockStatus.LockStatus enum carried
        # directly on the LightDeviceStatus oneof (not a sub-message).
        # The proto enum is the integer value, so `int(status.smart_lock)`
        # is correct as-is. Real-proto regression guard for #119: confirm
        # we don't accidentally start treating it as a sub-message.
        lds = _LDS()
        status = lds(smart_lock=value)
        result = DevicesApi._parse_statuses([status])
        assert result.get("smart_lock_state") == expected

    def test_signal_strength(self) -> None:
        lds = _LDS()
        status = lds(signal_strength=lds.SignalStrength(device_signal_level=3))
        result = DevicesApi._parse_statuses([status])
        assert result.get("signal_strength") == "Normal"

    def test_life_quality_status(self) -> None:
        # `actual_temperature/humidity/co2` are proto3-optional ints. The
        # MagicMock version of this test let us set floats and pretend
        # they round-tripped — real proto rejects floats and truncates,
        # and only emits the values that were explicitly populated.
        lds = _LDS()
        status = lds(
            life_quality=lds.LifeQualityStatus(
                actual_temperature=21,
                actual_humidity=55,
                actual_co2=400,
            )
        )
        result = DevicesApi._parse_statuses([status])
        assert result.get("temperature") == 21
        assert result.get("humidity") == 55
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
        from google.protobuf import timestamp_pb2  # noqa: PLC0415

        lds = _LDS()
        status = lds(
            motion_detected=lds.MotionDetected(
                detected_at=timestamp_pb2.Timestamp(seconds=1700000000)
            )
        )
        result = DevicesApi._parse_statuses([status])
        assert result.get("motion_detected") is True
        assert result.get("motion_detected_at") == 1700000000

    def test_gsm_status_connected(self) -> None:
        lds = _LDS()
        status = lds(
            gsm_status=lds.GsmStatus(type=3, status=2)  # 4G, CONNECTED
        )
        result = DevicesApi._parse_statuses([status])
        assert result.get("mobile_network_type") == "4G"
        assert result.get("gsm_connected") is True

    def test_gsm_status_not_connected(self) -> None:
        lds = _LDS()
        status = lds(
            gsm_status=lds.GsmStatus(type=1, status=0)  # 2G, UNSPECIFIED
        )
        result = DevicesApi._parse_statuses([status])
        assert result.get("gsm_connected") is False

    def test_monitoring_active(self) -> None:
        lds = _LDS()
        status = lds(monitoring=lds.Monitoring(cms_active=True))
        result = DevicesApi._parse_statuses([status])
        assert result.get("monitoring_active") is True

    def test_monitoring_inactive(self) -> None:
        lds = _LDS()
        status = lds(monitoring=lds.Monitoring(cms_active=False))
        result = DevicesApi._parse_statuses([status])
        assert result.get("monitoring_active") is False

    def test_sim_status(self) -> None:
        lds = _LDS()
        status = lds(sim_status=lds.SimStatus(sim_card_status=1))  # OK
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
        lds = _LDS()
        status = lds(
            wire_input_status=lds.WireInputStatus(is_alert=True, type=1)  # INTRUSION
        )
        result = DevicesApi._parse_statuses([status])
        assert result.get("wire_input_alert") is True
        assert result.get("wire_input_alarm_type") == "intrusion"

    def test_wire_input_status_not_alerting(self) -> None:
        lds = _LDS()
        status = lds(
            wire_input_status=lds.WireInputStatus(is_alert=False, type=11)  # GLASS_BREAK
        )
        result = DevicesApi._parse_statuses([status])
        assert result.get("wire_input_alert") is False
        assert result.get("wire_input_alarm_type") == "glass_break"

    def test_wire_input_status_unspecified_type(self) -> None:
        lds = _LDS()
        status = lds(
            wire_input_status=lds.WireInputStatus(is_alert=True, type=0)  # UNSPECIFIED
        )
        result = DevicesApi._parse_statuses([status])
        assert result.get("wire_input_alert") is True
        assert result.get("wire_input_alarm_type") == "unspecified"

    def test_transmitter_status_alerting(self) -> None:
        # Issue #65 follow-up: the Transmitter Jeweller emits its own oneof
        # `transmitter_status` (proto field 75) with the same shape as
        # `wire_input_status`. It must populate the same wire_input_alert
        # key so the unified safety entity reflects the bridged sensor.
        lds = _LDS()
        status = lds(transmitter_status=lds.TransmitterStatus(is_alert=True, type=1))
        result = DevicesApi._parse_statuses([status])
        assert result.get("wire_input_alert") is True
        assert result.get("wire_input_alarm_type") == "intrusion"

    def test_transmitter_status_not_alerting(self) -> None:
        lds = _LDS()
        status = lds(transmitter_status=lds.TransmitterStatus(is_alert=False, type=0))
        result = DevicesApi._parse_statuses([status])
        assert result.get("wire_input_alert") is False

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

    def test_wifi_signal_level_status_real_proto(self) -> None:
        # Regression for the TypeError @Permudious hit on beta.5 (#119): the
        # MagicMock version above silently allowed `int(status.wifi_signal_
        # level_status)` to pass even though the real proto field is a
        # sub-message (`WifiSignalLevelStatus`) with a nested
        # `wifi_signal_level` enum — the actual int lives one level deeper.
        # Use a real proto instance so the parser exercises the same shape
        # the wire delivers.
        from v3.mobilegwsvc.commonmodels.space.device.light import (
            light_device_status_pb2,
        )

        status = light_device_status_pb2.LightDeviceStatus(
            wifi_signal_level_status=(
                light_device_status_pb2.LightDeviceStatus.WifiSignalLevelStatus(wifi_signal_level=4)
            )
        )

        result = DevicesApi._parse_statuses([status])
        assert result.get("wifi_signal_level") == 4

    def test_smart_bracket_unlocked(self) -> None:
        status = MagicMock()
        status.WhichOneof.return_value = "smart_bracket_unlocked"
        result = DevicesApi._parse_statuses([status])
        assert result.get("smart_bracket_unlocked") is True

    def test_nfc_enabled(self) -> None:
        lds = _LDS()
        status = lds(nfc=lds.Nfc(enabled=True))
        result = DevicesApi._parse_statuses([status])
        assert result.get("nfc_enabled") is True

    def test_nfc_disabled(self) -> None:
        # New coverage uncovered by the real-proto sweep: the parser only
        # honoured `hasattr(...)` which always succeeds on a proto message,
        # so the False branch was never exercised before.
        lds = _LDS()
        status = lds(nfc=lds.Nfc(enabled=False))
        result = DevicesApi._parse_statuses([status])
        assert result.get("nfc_enabled") is False


class TestDeduplicateVideoDoorbells:
    """Issue #173: Ajax cloud delivers some MotionCam Video Doorbell hardware
    as BOTH a `hub_device` (`motion_cam_video_doorbell` ObjectType) AND a
    `video_edge_channel` (`video_edge_doorbell` About.Type) in the same
    snapshot. The `video_edge_channel` branch is the richer one (wifi_signal,
    channel id, no malfunctions noise) and is the canonical surface; the
    `hub_device` ghost shows up as a duplicate HA device card carrying a
    spurious `malfunctions=1` that bubbles up to the space-level malfunction
    counter. Drop the hub_device twin when its `video_edge_*` sibling is in
    the same snapshot. Permudious's setup (#119) — only the hub_device
    branch present — is unchanged."""

    @staticmethod
    def _make(
        device_id: str,
        name: str,
        device_type: str,
        *,
        malfunctions: int = 0,
    ) -> Device:
        return Device(
            id=device_id,
            hub_id="hub-1",
            name=name,
            device_type=device_type,
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=malfunctions,
            bypassed=False,
            statuses={},
            battery=None,
        )

    def test_drops_hub_device_when_video_edge_twin_present(self) -> None:
        """Same-name pair → hub_device representation gets dropped."""
        twin_video_edge = self._make("9c756e2bca39-0", "Deurbel", "video_edge_doorbell")
        twin_hub_device = self._make(
            "310A8DF4", "Deurbel", "motion_cam_video_doorbell", malfunctions=1
        )
        deduped = DevicesApi._dedupe_video_doorbells([twin_video_edge, twin_hub_device])
        assert [d.id for d in deduped] == ["9c756e2bca39-0"]

    def test_keeps_hub_device_when_no_video_edge_present(self) -> None:
        """Permudious's case: only the hub_device representation arrives — keep it.
        Removing it would make the doorbell disappear from HA entirely (#119)."""
        only_hub_device = self._make("310A8DF4", "Deurbel", "motion_cam_video_doorbell")
        deduped = DevicesApi._dedupe_video_doorbells([only_hub_device])
        assert [d.id for d in deduped] == ["310A8DF4"]

    def test_keeps_video_edge_when_no_hub_device_present(self) -> None:
        only_video_edge = self._make("9c756e2bca39-0", "Deurbel", "video_edge_doorbell")
        deduped = DevicesApi._dedupe_video_doorbells([only_video_edge])
        assert [d.id for d in deduped] == ["9c756e2bca39-0"]

    def test_different_names_keep_both(self) -> None:
        """A standalone hub_device with a different name is a different
        physical product — never dedupe across names."""
        hallway = self._make("9c756e2bca39-0", "Hallway", "video_edge_doorbell")
        garage = self._make("310A8DF4", "Garage", "motion_cam_video_doorbell")
        deduped = DevicesApi._dedupe_video_doorbells([hallway, garage])
        assert {d.id for d in deduped} == {"9c756e2bca39-0", "310A8DF4"}

    def test_name_match_is_case_insensitive(self) -> None:
        """Ajax sometimes stores the same product with differing case across
        the two oneofs (`Deurbel` vs `deurbel`). Treat them as the same."""
        video = self._make("9c756e2bca39-0", "Deurbel", "video_edge_doorbell")
        hub = self._make("310A8DF4", "deurbel", "motion_cam_video_doorbell")
        deduped = DevicesApi._dedupe_video_doorbells([video, hub])
        assert [d.id for d in deduped] == ["9c756e2bca39-0"]

    def test_indoor_and_base_siblings_also_deduped(self) -> None:
        """`motion_cam_video_indoor` and `motion_cam_video_base` are the
        other two members of the streaming-camera family (#121); they also
        appear on the video_edge_channel side as `video_edge_indoor` etc."""
        video_indoor = self._make("9c-ind-0", "Salon Cam", "video_edge_indoor")
        hub_indoor = self._make("31AABB01", "Salon Cam", "motion_cam_video_indoor")
        deduped = DevicesApi._dedupe_video_doorbells([video_indoor, hub_indoor])
        assert [d.id for d in deduped] == ["9c-ind-0"]


class TestDevicesApiInit:
    def test_init(self) -> None:
        client = MagicMock()
        api = DevicesApi(client)
        assert api._client is client


class TestSendCommand:
    """Dispatcher behaviour for `DevicesApi.send_command`.

    These tests mock out the per-action coroutines on the instance so we
    only assert the action-string → method routing. The actual gRPC
    serialisation is covered by the dedicated tests below.
    """

    @pytest.mark.asyncio
    async def test_send_command_dispatches_on(self) -> None:
        api = DevicesApi(MagicMock())
        api._device_on = AsyncMock()
        api._device_off = AsyncMock()
        api._device_brightness = AsyncMock()
        cmd = DeviceCommand.on(hub_id="h1", device_id="d1", device_type="relay", channels=[1])

        await api.send_command(cmd)

        api._device_on.assert_awaited_once_with(cmd)
        api._device_off.assert_not_called()
        api._device_brightness.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_command_dispatches_off(self) -> None:
        api = DevicesApi(MagicMock())
        api._device_on = AsyncMock()
        api._device_off = AsyncMock()
        api._device_brightness = AsyncMock()
        cmd = DeviceCommand.off(hub_id="h1", device_id="d1", device_type="relay", channels=[1])

        await api.send_command(cmd)

        api._device_off.assert_awaited_once_with(cmd)

    @pytest.mark.asyncio
    async def test_send_command_dispatches_brightness(self) -> None:
        api = DevicesApi(MagicMock())
        api._device_on = AsyncMock()
        api._device_off = AsyncMock()
        api._device_brightness = AsyncMock()
        cmd = DeviceCommand.set_brightness(
            hub_id="h1",
            device_id="d1",
            device_type="light_switch_dimmer",
            brightness=50,
            channels=[1],
        )

        await api.send_command(cmd)

        api._device_brightness.assert_awaited_once_with(cmd)

    @pytest.mark.asyncio
    async def test_send_command_unknown_action_raises(self) -> None:
        from custom_components.aegis_ajax.api.devices import DeviceCommandError

        api = DevicesApi(MagicMock())
        cmd = DeviceCommand(
            action="bogus",
            hub_id="h1",
            device_id="d1",
            device_type="relay",
            channels=[1],
        )
        with pytest.raises(DeviceCommandError):
            await api.send_command(cmd)


class TestBuildObjectType:
    """`_build_object_type` must mark the right ObjectType.type oneof case."""

    @pytest.mark.parametrize(
        "device_type",
        [
            "relay",
            "relay_fibra_base",
            "wall_switch",
            "socket",
            "socket_b",
            "socket_g",
            "socket_outlet_type_e",
            "socket_outlet_type_f",
            "socket_type_g_plus",
            "light_switch",
            "light_switch_one_gang",
            "light_switch_two_gang",
            "light_switch_dimmer",
        ],
    )
    def test_known_device_type_sets_oneof(self, device_type: str) -> None:
        from custom_components.aegis_ajax.api.devices import _build_object_type

        obj = _build_object_type(device_type)

        assert obj.WhichOneof("type") == device_type

    def test_unknown_device_type_raises(self) -> None:
        from custom_components.aegis_ajax.api.devices import (
            DeviceCommandError,
            _build_object_type,
        )

        with pytest.raises(DeviceCommandError):
            _build_object_type("not_a_real_device")


class TestDeviceCommandRoundTrip:
    """Cover the gRPC stub interaction for the three command actions.

    We patch the lazy-imported `endpoint_pb2_grpc` module's stub class so
    each call returns a controllable response message. The test then
    asserts on the actual request that hit the stub, catching any drift
    between `DeviceCommand` and the proto wire format.
    """

    def _make_api(self) -> DevicesApi:
        client = MagicMock()
        client._get_channel.return_value = MagicMock()
        client._session.get_call_metadata.return_value = []
        return DevicesApi(client)

    @pytest.mark.asyncio
    async def test_device_on_sends_correct_request(self) -> None:
        from v3.mobilegwsvc.commonmodels.response import response_pb2 as common_response_pb2
        from v3.mobilegwsvc.service.device_command_device_on import (
            endpoint_pb2_grpc,
            response_pb2,
        )

        api = self._make_api()
        captured: list = []
        success_response = response_pb2.DeviceCommandDeviceOnResponse(
            success=common_response_pb2.Success()
        )

        class _StubFactory:
            def __init__(self, channel: object) -> None:
                async def _execute(req: object, **_: object) -> object:
                    captured.append(req)
                    return success_response

                self.execute = AsyncMock(side_effect=_execute)

        with patch.object(endpoint_pb2_grpc, "DeviceCommandDeviceOnServiceStub", _StubFactory):
            await api.send_command(
                DeviceCommand.on(
                    hub_id="hub-1",
                    device_id="dev-1",
                    device_type="relay",
                    channels=[1],
                )
            )

        assert len(captured) == 1
        request = captured[0]
        assert request.hub_id == "hub-1"
        assert request.device_id == "dev-1"
        assert request.device_type.WhichOneof("type") == "relay"
        assert list(request.channels) == [1]

    @pytest.mark.asyncio
    async def test_device_off_failure_raises(self) -> None:
        from v3.mobilegwsvc.commonmodels.response import response_pb2 as common_response_pb2
        from v3.mobilegwsvc.service.device_command_device_off import (
            endpoint_pb2_grpc,
            response_pb2,
        )

        from custom_components.aegis_ajax.api.devices import DeviceCommandError

        api = self._make_api()
        failure_response = response_pb2.DeviceCommandDeviceOffResponse(
            failure=response_pb2.DeviceCommandDeviceOffResponse.Failure(
                hub_offline=common_response_pb2.Error(),
            )
        )

        class _StubFactory:
            def __init__(self, channel: object) -> None:
                self.execute = AsyncMock(return_value=failure_response)

        with (
            patch.object(endpoint_pb2_grpc, "DeviceCommandDeviceOffServiceStub", _StubFactory),
            pytest.raises(DeviceCommandError, match="hub_offline"),
        ):
            await api.send_command(
                DeviceCommand.off(
                    hub_id="hub-1",
                    device_id="dev-1",
                    device_type="socket",
                    channels=[1],
                )
            )

    @pytest.mark.asyncio
    async def test_brightness_sends_absolute_percentage(self) -> None:
        from v3.mobilegwsvc.commonmodels.response import response_pb2 as common_response_pb2
        from v3.mobilegwsvc.service.device_command_brightness import (
            endpoint_pb2_grpc,
            request_pb2,
            response_pb2,
        )

        api = self._make_api()
        captured: list = []
        success_response = response_pb2.DeviceCommandBrightnessResponse(
            success=common_response_pb2.Success()
        )

        class _StubFactory:
            def __init__(self, channel: object) -> None:
                async def _execute(req: object, **_: object) -> object:
                    captured.append(req)
                    return success_response

                self.execute = AsyncMock(side_effect=_execute)

        with patch.object(endpoint_pb2_grpc, "DeviceCommandBrightnessServiceStub", _StubFactory):
            await api.send_command(
                DeviceCommand.set_brightness(
                    hub_id="hub-1",
                    device_id="dev-1",
                    device_type="light_switch_dimmer",
                    brightness=42,
                    channels=[1],
                )
            )

        assert len(captured) == 1
        request = captured[0]
        assert request.brightness_in_percentage == 42
        absolute = request_pb2.DeviceCommandBrightnessRequest.BRIGHTNESS_TYPE_ABSOLUTE
        assert request.brightness_type == absolute
        assert request.device_type.WhichOneof("type") == "light_switch_dimmer"

    @pytest.mark.asyncio
    async def test_brightness_without_value_raises(self) -> None:
        from custom_components.aegis_ajax.api.devices import DeviceCommandError

        api = self._make_api()
        cmd = DeviceCommand(
            action="brightness",
            hub_id="h1",
            device_id="d1",
            device_type="light_switch_dimmer",
            channels=[1],
            brightness=None,
        )
        with pytest.raises(DeviceCommandError, match="brightness"):
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
    async def test_status_update_transmitter_status_forwards_alert_and_type(self) -> None:
        """transmitter_status updates must forward is_alert + alarm_type, not boolean True."""
        api = self._make_api()

        update_msg = MagicMock()
        update_msg.HasField.side_effect = lambda field: field == "success"
        update_msg.success.WhichOneof.return_value = "updates"

        single_update = MagicMock()
        single_update.WhichOneof.return_value = "status_update"
        single_update.device_id.hub_light_device_id.device_id = "dev-tr"
        single_update.status_update.status.WhichOneof.return_value = "transmitter_status"
        single_update.status_update.status.transmitter_status.is_alert = True
        single_update.status_update.status.transmitter_status.type = 1  # INTRUSION
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
        assert status_name == "transmitter_status"
        assert data["op"] == 2
        assert data["is_alert"] is True
        assert data["alarm_type"] == "intrusion"

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

    @pytest.mark.asyncio
    async def test_snapshot_parse_exception_isolates_bad_device(self) -> None:
        """A device that explodes during parse must not kill the snapshot.

        Regression guard for the #119 family: before the per-device guard
        was added, a TypeError raised parsing one device's `_parse_statuses`
        bubbled out of `_run_stream`, triggered the outer except, and put
        the stream into exponential-backoff reconnect — the symptom
        @Permudious saw 21 times in a row. The surviving devices must
        still reach `on_devices_snapshot` and the stream must keep its
        backoff baseline (no `asyncio.sleep` between iterations of the
        same connection).
        """
        api = self._make_api()
        good_a = self._make_light_device_mock("dev-good-a")
        bad = self._make_light_device_mock("dev-bad")
        good_b = self._make_light_device_mock("dev-good-b")

        # Make `bad` raise during parsing. Using WhichOneof so the failure
        # mirrors a real proto-shape mismatch surfaced at attribute access.
        bad.WhichOneof.side_effect = TypeError(
            "int() argument must be a string, a bytes-like object or a real number"
        )

        mock_msg = MagicMock()
        mock_msg.HasField.side_effect = lambda field: field == "success"
        mock_msg.success.WhichOneof.return_value = "snapshot"
        mock_msg.success.snapshot.light_devices = [good_a, bad, good_b]

        async def _aiter() -> AsyncGenerator[MagicMock, None]:
            yield mock_msg

        snapshot_received: list[list[Device]] = []

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

        # Snapshot delivered exactly once, containing only the survivors —
        # the bad device is dropped, not the whole batch.
        assert len(snapshot_received) == 1
        ids = [d.id for d in snapshot_received[0]]
        assert ids == ["dev-good-a", "dev-good-b"]

    @pytest.mark.asyncio
    async def test_status_update_exception_isolates_bad_update(self) -> None:
        """A single bad status update must not drop the rest of the batch.

        Same #119 hardening, exercised on the `updates` path: one update
        whose payload-build raises (e.g. an enum field that's actually a
        sub-message) must be skipped without killing the inner loop or
        the surrounding stream task.
        """
        api = self._make_api()

        update_msg = MagicMock()
        update_msg.HasField.side_effect = lambda field: field == "success"
        update_msg.success.WhichOneof.return_value = "updates"

        good_a = MagicMock()
        good_a.WhichOneof.return_value = "status_update"
        good_a.device_id.hub_light_device_id.device_id = "dev-good-a"
        good_a.status_update.status.WhichOneof.return_value = "door_opened"
        good_a.status_update.update_type = 1

        bad = MagicMock()
        bad.WhichOneof.return_value = "status_update"
        bad.device_id.hub_light_device_id.device_id = "dev-bad"
        bad.status_update.status.WhichOneof.side_effect = TypeError("boom")
        bad.status_update.update_type = 2

        good_b = MagicMock()
        good_b.WhichOneof.return_value = "status_update"
        good_b.device_id.hub_light_device_id.device_id = "dev-good-b"
        good_b.status_update.status.WhichOneof.return_value = "motion_detected"
        good_b.status_update.update_type = 2

        update_msg.success.updates.updates = [good_a, bad, good_b]

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

        ids = [device_id for device_id, _, _ in status_received]
        assert ids == ["dev-good-a", "dev-good-b"]

    @pytest.mark.asyncio
    async def test_snapshot_update_parse_exception_is_isolated(self) -> None:
        """A bad snapshot_update is dropped without crashing the stream."""
        api = self._make_api()
        bad = self._make_light_device_mock("dev-bad")
        bad.WhichOneof.side_effect = TypeError("boom")

        update_msg = MagicMock()
        update_msg.HasField.side_effect = lambda field: field == "success"
        update_msg.success.WhichOneof.return_value = "updates"

        single_update = MagicMock()
        single_update.WhichOneof.return_value = "snapshot_update"
        single_update.snapshot_update.light_device = bad

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

        # The bad snapshot_update is dropped without firing the callback
        # and without raising out of _run_stream. Before the per-update
        # guard, the parse exception bubbled out of the inner async-for
        # loop and hit the outer `except Exception` reconnect-backoff —
        # 21 occurrences in @Permudious's log. After the guard, the
        # update is silently skipped and the inner loop continues.
        assert snapshot_received == []


def _build_synthetic_snapshot_bytes() -> bytes:
    """Build a realistic multi-device snapshot serialised to wire bytes.

    Crucially includes a `video_edge_channel` device with
    `wifi_signal_level_status` — the exact shape that crashed beta.5
    on @Permudious's MotionCam Video Doorbell (#119). If a future
    regression on `_parse_statuses` re-introduces an `int(sub_message)`
    or similar wrong-shape access, the replay test (which deserialises
    these bytes through the real `StreamLightDevicesResponse` proto)
    fails before the beta hits any user.
    """
    from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels import (  # noqa: PLC0415
        object_type_pb2,
    )
    from v3.mobilegwsvc.commonmodels.hub.device.light import (  # noqa: PLC0415
        light_common_hub_device_pb2,
        light_hub_device_pb2,
    )
    from v3.mobilegwsvc.commonmodels.space.device.light import (  # noqa: PLC0415
        light_device_pb2,
        light_device_profile_pb2,
        light_device_status_pb2,
    )
    from v3.mobilegwsvc.commonmodels.video.videoedge.light import (  # noqa: PLC0415
        light_video_edge_pb2,
    )
    from v3.mobilegwsvc.service.stream_light_devices import (  # noqa: PLC0415
        response_pb2,
    )

    lds = light_device_status_pb2.LightDeviceStatus

    # hub_device with a healthy mix of sub-message statuses — every
    # branch in `_parse_statuses` that touches a sub-message must
    # survive end-to-end serialisation here.
    hub_obj_type = object_type_pb2.ObjectType()
    hub_obj_type.motion_protect.SetInParent()
    hub_device = light_hub_device_pb2.LightHubDevice(
        common_device=light_common_hub_device_pb2.LightCommonHubDevice(
            profile=light_device_profile_pb2.LightDeviceProfile(
                id="dev-hub-1",
                name="Living Room Motion",
                statuses=[
                    lds(battery=lds.Battery(charge_level_percentage=88, battery_state=1)),
                    lds(signal_strength=lds.SignalStrength(device_signal_level=4)),
                    lds(temperature=lds.ValueStatus(value=22)),
                    lds(door_opened=lds.Simple()),
                ],
            ),
            object_type=hub_obj_type,
            hub_id="hub-1",
        )
    )

    # video_edge_channel — the #119 path. Wifi signal status is the
    # sub-message wrapping the enum, surfaced via the v3 video edge.
    video_doorbell = light_video_edge_pb2.LightVideoEdgeChannel(
        profile=light_device_profile_pb2.LightDeviceProfile(
            id="dev-doorbell-1",
            name="Front Door",
            statuses=[
                lds(
                    wifi_signal_level_status=lds.WifiSignalLevelStatus(
                        wifi_signal_level=4  # WIFI_SIGNAL_LEVEL_STRONG
                    )
                ),
                lds(signal_strength=lds.SignalStrength(device_signal_level=4)),
            ],
        ),
        video_edge_channel_properties=light_video_edge_pb2.LightVideoEdgeChannel.VideoEdgeChannelProperties(
            video_edge_type=5  # VIDEO_EDGE_DOORBELL
        ),
    )

    snapshot = response_pb2.StreamLightDevicesResponse(
        success=response_pb2.StreamLightDevicesResponse.Success(
            snapshot=response_pb2.StreamLightDevicesResponse.Success.Snapshot(
                light_devices=[
                    light_device_pb2.LightDevice(hub_device=hub_device),
                    light_device_pb2.LightDevice(video_edge_channel=video_doorbell),
                ]
            )
        )
    )
    return snapshot.SerializeToString()


class TestSnapshotReplay:
    """End-to-end replay of serialised `StreamLightDevicesResponse` bytes.

    Mitigation 3 of the #119 hardening: a synthetic snapshot is built
    from real protos, round-tripped through wire bytes, and fed into
    `start_device_stream`. Any latent wrong-shape parser bug (the same
    class as the original `int(sub_message)` bug) fires as a parse
    error in this test before a user beta surfaces it.

    `tests/fixtures/*.bin` files captured from real installs are also
    replayed automatically; new captures drop in without test changes.
    """

    def _make_api(self) -> DevicesApi:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []
        return DevicesApi(mock_client)

    async def _replay(self, payload: bytes) -> list[Device]:
        from v3.mobilegwsvc.service.stream_light_devices import (  # noqa: PLC0415
            response_pb2,
        )

        api = self._make_api()
        msg = response_pb2.StreamLightDevicesResponse()
        msg.ParseFromString(payload)

        async def _aiter() -> AsyncGenerator[object, None]:
            yield msg

        devices_seen: list[Device] = []

        def on_snap(devices: list[Device]) -> None:
            devices_seen.extend(devices)

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

        return devices_seen

    @pytest.mark.asyncio
    async def test_synthetic_snapshot_parses_all_devices(self) -> None:
        payload = _build_synthetic_snapshot_bytes()
        devices = await self._replay(payload)

        ids = sorted(d.id for d in devices)
        assert ids == ["dev-doorbell-1", "dev-hub-1"]

        hub = next(d for d in devices if d.id == "dev-hub-1")
        # Sub-message statuses must have round-tripped cleanly. If
        # _parse_statuses regressed on any of these branches, the
        # corresponding key would be missing or the parse would have
        # raised inside the per-device guard.
        assert hub.statuses.get("temperature") == 22
        assert hub.statuses.get("signal_strength") == "Strong"
        assert hub.statuses.get("door_opened") is True
        assert hub.battery is not None
        assert hub.battery.level == 88

        doorbell = next(d for d in devices if d.id == "dev-doorbell-1")
        # #119 regression guard: the wifi_signal_level lives one
        # message deeper than the oneof tag.
        assert doorbell.device_type == "video_edge_doorbell"
        assert doorbell.statuses.get("wifi_signal_level") == 4
        assert doorbell.statuses.get("signal_strength") == "Strong"

    @pytest.mark.asyncio
    async def test_fixture_files_round_trip(self) -> None:
        """Replay every captured `tests/fixtures/*.bin` snapshot.

        Skips cleanly when no fixtures are present so CI stays green
        before the first capture is committed. The point is that when
        @Permudious (or any future reporter) shares a binary capture,
        dropping it into `tests/fixtures/` adds regression coverage
        with zero glue code.
        """
        fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        bin_files = sorted(fixtures_dir.glob("*.bin"))
        if not bin_files:
            pytest.skip("No captured fixtures yet — drop *.bin into tests/fixtures/")

        for path in bin_files:
            payload = path.read_bytes()
            # We don't assert specific device counts or ids — every
            # install has a different fleet — only that the parser
            # doesn't blow up and produces at least one Device. An
            # empty list usually means the snapshot was a failure
            # message or a no-op, not the parser-coverage we want.
            devices = await self._replay(payload)
            assert isinstance(devices, list), f"Fixture {path.name} did not yield a list"
            assert devices, f"Fixture {path.name} produced no devices — wrong capture?"


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


class TestSetPhotoOnDemandMode:
    """Tests for DevicesApi.set_photo_on_demand_mode (DeviceCommandPhotoOnDemandModeService)."""

    def _make_api(self) -> DevicesApi:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []
        return DevicesApi(mock_client)

    @pytest.mark.asyncio
    async def test_requires_at_least_one_channel(self) -> None:
        """Calling without either argument raises DeviceCommandError."""
        from custom_components.aegis_ajax.api.devices import DeviceCommandError

        api = self._make_api()
        with pytest.raises(DeviceCommandError, match="user_enabled"):
            await api.set_photo_on_demand_mode("hub-1")

    @pytest.mark.asyncio
    async def test_user_only_sends_single_call(self) -> None:
        """Setting only user_enabled fires exactly one gRPC execute."""
        api = self._make_api()
        stub = MagicMock()
        stub.execute = AsyncMock(return_value=MagicMock(HasField=MagicMock(return_value=False)))

        with patch(
            "v3.mobilegwsvc.service.device_command_photo_on_demand_mode.endpoint_pb2_grpc.DeviceCommandPhotoOnDemandModeServiceStub",
            return_value=stub,
        ):
            await api.set_photo_on_demand_mode("hub-XYZ", user_enabled=True)

        assert stub.execute.await_count == 1
        sent = stub.execute.await_args_list[0].args[0]
        assert sent.hub_id == "hub-XYZ"
        # USER_ENABLE = 2; field name on the oneof tracks WhichOneof:
        assert sent.WhichOneof("additional_param") == "photo_on_demand_mode_user"
        assert sent.photo_on_demand_mode_user == 2

    @pytest.mark.asyncio
    async def test_scenario_disable_sends_correct_enum(self) -> None:
        api = self._make_api()
        stub = MagicMock()
        stub.execute = AsyncMock(return_value=MagicMock(HasField=MagicMock(return_value=False)))

        with patch(
            "v3.mobilegwsvc.service.device_command_photo_on_demand_mode.endpoint_pb2_grpc.DeviceCommandPhotoOnDemandModeServiceStub",
            return_value=stub,
        ):
            await api.set_photo_on_demand_mode("hub-1", scenario_enabled=False)

        sent = stub.execute.await_args_list[0].args[0]
        assert sent.WhichOneof("additional_param") == "photo_on_demand_mode_scenario"
        # SCENARIO_DISABLE = 1
        assert sent.photo_on_demand_mode_scenario == 1

    @pytest.mark.asyncio
    async def test_both_channels_send_two_calls(self) -> None:
        api = self._make_api()
        stub = MagicMock()
        stub.execute = AsyncMock(return_value=MagicMock(HasField=MagicMock(return_value=False)))

        with patch(
            "v3.mobilegwsvc.service.device_command_photo_on_demand_mode.endpoint_pb2_grpc.DeviceCommandPhotoOnDemandModeServiceStub",
            return_value=stub,
        ):
            await api.set_photo_on_demand_mode("hub-1", user_enabled=True, scenario_enabled=True)

        assert stub.execute.await_count == 2
        sent_oneofs = [
            call.args[0].WhichOneof("additional_param") for call in stub.execute.await_args_list
        ]
        assert sent_oneofs == ["photo_on_demand_mode_user", "photo_on_demand_mode_scenario"]

    @pytest.mark.asyncio
    async def test_failure_response_raises(self) -> None:
        """A gRPC response with .failure set raises DeviceCommandError with the error name."""
        from custom_components.aegis_ajax.api.devices import DeviceCommandError

        api = self._make_api()
        failure = MagicMock()
        failure.WhichOneof.return_value = "bad_request"
        response = MagicMock()
        response.HasField.return_value = True
        response.failure = failure
        stub = MagicMock()
        stub.execute = AsyncMock(return_value=response)

        with (
            patch(
                "v3.mobilegwsvc.service.device_command_photo_on_demand_mode.endpoint_pb2_grpc.DeviceCommandPhotoOnDemandModeServiceStub",
                return_value=stub,
            ),
            pytest.raises(DeviceCommandError, match="user.*bad_request"),
        ):
            await api.set_photo_on_demand_mode("hub-1", user_enabled=True)
