"""Tests for per-device internal temperature via the per-device StreamHubDevice RPC.

Covers sirens (#220) and outdoor curtain PIRs (#229) — device families whose
temperature lives only in the rich `StreamHubDevice` snapshot, not the lighter
`StreamLightDevices` stream.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

# Wire up the proto search path before any `systems.*` import.
from custom_components.aegis_ajax.api import _proto_path as _proto_path  # noqa: E402, F401
from custom_components.aegis_ajax.api.devices import DevicesApi  # noqa: E402
from custom_components.aegis_ajax.api.devices_parser import (  # noqa: E402
    parse_hub_device_temperature,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from contextlib import AbstractContextManager

    from google.protobuf.message import Message


def _hub_device_with_siren_temperature(value: int, *, is_extreme: bool = False) -> Message:
    from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device import (
        hub_device_pb2,
        street_siren_pb2,
    )
    from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device.common import (
        device_temperature_pb2,
    )

    return hub_device_pb2.HubDevice(
        street_siren=street_siren_pb2.StreetSiren(
            device_temperature=device_temperature_pb2.DeviceTemperature(
                value=value, is_extreme=is_extreme
            )
        )
    )


class TestParseHubDeviceTemperature:
    def test_returns_value_for_street_siren(self) -> None:
        hub_device = _hub_device_with_siren_temperature(23)
        assert parse_hub_device_temperature(hub_device) == 23.0

    def test_returns_value_for_home_siren(self) -> None:
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device import (
            home_siren_pb2,
            hub_device_pb2,
        )
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device.common import (
            device_temperature_pb2,
        )

        hub_device = hub_device_pb2.HubDevice(
            home_siren=home_siren_pb2.HomeSiren(
                device_temperature=device_temperature_pb2.DeviceTemperature(value=19)
            )
        )
        assert parse_hub_device_temperature(hub_device) == 19.0

    def test_returns_value_for_motion_protect_curtain_outdoor_mini(self) -> None:
        # #229: outdoor curtain PIRs carry their temperature in the same
        # `device_temperature` field on the rich HubDevice oneof, just like
        # sirens. The parser is oneof-case-agnostic, so it must work unchanged.
        # NOTE: our HubDevice proto only models the `_mini` curtain variant
        # (field 21); the Base/Plus variants are not yet in the proto — see the
        # #229 probe log in `DevicesApi.get_hub_device_temperature`.
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device import (
            hub_device_pb2,
            motion_protect_curtain_outdoor_pb2,
        )
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device.common import (
            device_temperature_pb2,
        )

        hub_device = hub_device_pb2.HubDevice(
            motion_protect_curtain_outdoor_mini=motion_protect_curtain_outdoor_pb2.MotionProtectCurtainOutdoorMini(
                device_temperature=device_temperature_pb2.DeviceTemperature(value=17)
            )
        )
        assert parse_hub_device_temperature(hub_device) == 17.0

    def test_returns_none_when_device_temperature_absent(self) -> None:
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device import (
            hub_device_pb2,
            street_siren_pb2,
        )

        hub_device = hub_device_pb2.HubDevice(street_siren=street_siren_pb2.StreetSiren())
        assert parse_hub_device_temperature(hub_device) is None

    def test_returns_none_when_no_device_oneof_set(self) -> None:
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device import hub_device_pb2

        assert parse_hub_device_temperature(hub_device_pb2.HubDevice()) is None


def _patch_stream_hub_device(stub_class: MagicMock) -> AbstractContextManager[None]:
    mock_request_pb2 = MagicMock()
    mock_grpc_module = MagicMock(StreamHubDeviceServiceStub=stub_class)
    return patch.dict(
        "sys.modules",
        {
            "v3.mobilegwsvc.service.stream_hub_device.endpoint_pb2_grpc": mock_grpc_module,
            "v3.mobilegwsvc.service.stream_hub_device.request_pb2": mock_request_pb2,
            "v3.mobilegwsvc.service.stream_hub_device": MagicMock(
                endpoint_pb2_grpc=mock_grpc_module,
                request_pb2=mock_request_pb2,
            ),
        },
    )


def _stub_yielding(msg: MagicMock) -> MagicMock:
    async def _aiter(*args: object, **kwargs: object) -> AsyncGenerator[MagicMock, None]:
        yield msg

    stub_instance = MagicMock()
    stub_instance.execute.return_value = _aiter()
    return MagicMock(return_value=stub_instance)


class TestGetHubDeviceTemperature:
    @pytest.mark.asyncio
    async def test_returns_temperature_from_snapshot(self) -> None:
        client = MagicMock()
        client._get_channel.return_value = MagicMock()
        client._session.get_call_metadata.return_value = []
        api = DevicesApi(client)

        hub_device = _hub_device_with_siren_temperature(21)
        msg = MagicMock()
        msg.HasField.side_effect = lambda field: field == "success"
        msg.success.WhichOneof.return_value = "snapshot"
        msg.success.snapshot.hub_device = hub_device

        with _patch_stream_hub_device(_stub_yielding(msg)):
            result = await api.get_hub_device_temperature("hub-1", "dev-1")

        assert result == 21.0

    @pytest.mark.asyncio
    async def test_returns_none_on_failure_message(self) -> None:
        client = MagicMock()
        client._get_channel.return_value = MagicMock()
        client._session.get_call_metadata.return_value = []
        api = DevicesApi(client)

        msg = MagicMock()
        msg.HasField.side_effect = lambda field: field == "failure"

        with _patch_stream_hub_device(_stub_yielding(msg)):
            result = await api.get_hub_device_temperature("hub-1", "dev-1")

        assert result is None
