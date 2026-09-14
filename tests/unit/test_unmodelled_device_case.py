"""Tests for naming the `HubDevice.device` oneof cases we do not model (#408).

An unmodelled case is dropped into protobuf's unknown-field set, so
`WhichOneof("device")` returns `None` and nothing is logged at any level:
"this family is not in our definitions" and "this device reports nothing"
are indistinguishable. The retained wire field number tells them apart.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

# Wire up the proto search path before any `systems.*` import.
from custom_components.aegis_ajax.api import _proto_path as _proto_path  # noqa: E402, F401
from custom_components.aegis_ajax.api.devices import DevicesApi  # noqa: E402
from custom_components.aegis_ajax.api.devices_parser import (  # noqa: E402
    unmodelled_device_case_numbers,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from google.protobuf.message import Message


def _hub_device_with_case(field_number: int) -> Message:
    """Parse a `HubDevice` off the wire carrying only `field_number` as a
    length-delimited (message) field, exactly as the hub would send a family
    we do not model."""
    from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device import hub_device_pb2

    tag = (field_number << 3) | 2  # wire type 2 = length-delimited
    raw = bytearray()
    while tag > 0x7F:
        raw.append((tag & 0x7F) | 0x80)
        tag >>= 7
    raw.append(tag)
    raw.append(0x00)  # zero-length body

    device = hub_device_pb2.HubDevice()
    device.ParseFromString(bytes(raw))
    return device


class TestUnmodelledDeviceCaseNumbers:
    # 81 and 113 are `life_quality` and `roller_shutter_ws`: real families that
    # hub_device.proto deliberately leaves out, because the app ships no
    # definition for the parts they embed. Being unmodelled by decision rather
    # than by accident is what makes them stable fixtures here.
    def test_reports_the_wire_number_of_an_unmodelled_case(self) -> None:
        device = _hub_device_with_case(81)

        assert device.WhichOneof("device") is None, "precondition: case must be unmodelled"
        assert unmodelled_device_case_numbers(device) == [81]

    def test_reports_a_case_number_above_one_byte_of_varint(self) -> None:
        """Numbers past 15 push the tag into a multi-byte varint, and every
        case worth reporting now sits well above that."""
        device = _hub_device_with_case(113)

        assert device.WhichOneof("device") is None
        assert unmodelled_device_case_numbers(device) == [113]

    def test_returns_empty_for_a_modelled_case(self) -> None:
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device import (
            hub_device_pb2,
            street_siren_pb2,
        )

        device = hub_device_pb2.HubDevice(street_siren=street_siren_pb2.StreetSiren())

        assert device.WhichOneof("device") == "street_siren"
        assert unmodelled_device_case_numbers(device) == []

    def test_returns_empty_for_an_empty_message(self) -> None:
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device import hub_device_pb2

        assert unmodelled_device_case_numbers(hub_device_pb2.HubDevice()) == []

    # The helper feeds a `_LOGGER.debug()` argument list, which Python
    # evaluates eagerly — so it must swallow every shape surprise rather than
    # take the calling path down with it.
    def test_never_raises_on_a_non_message(self) -> None:
        assert unmodelled_device_case_numbers(None) == []
        assert unmodelled_device_case_numbers(object()) == []
        assert unmodelled_device_case_numbers("not a proto") == []


class TestProbeReportsTheCaseNumber:
    """The number has to reach the log, not just the helper — the whole point
    of #408 is that the existing `case=None` line says nothing actionable."""

    @pytest.mark.asyncio
    async def test_temperature_probe_logs_the_unmodelled_case_number(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = MagicMock()
        client._get_channel.return_value = MagicMock()
        client._session.get_call_metadata.return_value = []
        api = DevicesApi(client)

        msg = MagicMock()
        msg.HasField.side_effect = lambda field: field == "success"
        msg.success.WhichOneof.return_value = "snapshot"
        msg.success.snapshot.hub_device = _hub_device_with_case(81)  # life_quality

        async def _aiter(*args: object, **kwargs: object) -> AsyncGenerator[MagicMock]:
            yield msg

        stub_instance = MagicMock()
        stub_instance.execute.return_value = _aiter()
        mock_request_pb2 = MagicMock()
        stub_class = MagicMock(return_value=stub_instance)
        mock_grpc_module = MagicMock(StreamHubDeviceServiceStub=stub_class)

        with (
            patch.dict(
                "sys.modules",
                {
                    "v3.mobilegwsvc.service.stream_hub_device.endpoint_pb2_grpc": mock_grpc_module,
                    "v3.mobilegwsvc.service.stream_hub_device.request_pb2": mock_request_pb2,
                    "v3.mobilegwsvc.service.stream_hub_device": MagicMock(
                        endpoint_pb2_grpc=mock_grpc_module,
                        request_pb2=mock_request_pb2,
                    ),
                },
            ),
            caplog.at_level(logging.DEBUG, logger="custom_components.aegis_ajax.api.devices"),
        ):
            result = await api.get_hub_device_temperature("hub-1", "dev-1")

        assert result is None
        assert "unmodelled_case_numbers=[81]" in caplog.text
