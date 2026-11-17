"""One-shot device reads and photo requests: what goes out and how the answer is read."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from v3.mobilegwsvc.service.device_command_photo_on_demand_mode import (
    response_pb2 as phod_response_pb2,
)
from v3.mobilegwsvc.service.stream_hub_device import request_pb2 as hub_device_request_pb2
from v3.mobilegwsvc.service.stream_hub_device import response_pb2 as hub_device_response_pb2
from v3.mobilegwsvc.service.stream_light_devices import request_pb2 as light_request_pb2
from v3.mobilegwsvc.service.stream_light_devices import response_pb2 as light_response_pb2

from custom_components.aegis_ajax.api.devices import DeviceCommandError, DevicesApi

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from google.protobuf.message import Message

_METADATA = [("token", "abc")]
_HUB_DEVICE_STUB = "v3.mobilegwsvc.service.stream_hub_device.endpoint_pb2_grpc"
_LIGHT_STUB = "v3.mobilegwsvc.service.stream_light_devices.endpoint_pb2_grpc"
_PHOD_STUB = "v3.mobilegwsvc.service.device_command_photo_on_demand_mode.endpoint_pb2_grpc"
_CAPTURE_PATH = (
    "/systems.ajax.mobile.v2.service.hub.company.media.PhotoOnDemandService/capturePhoto"
)


def _api() -> tuple[DevicesApi, MagicMock]:
    client = MagicMock()
    client._session.get_call_metadata.return_value = _METADATA
    return DevicesApi(client), client._get_channel.return_value


def _stream(*messages: Message) -> MagicMock:
    """A stub whose ``execute`` returns ``messages`` as a server stream."""

    async def answers() -> AsyncIterator[Message]:
        for message in messages:
            yield message

    stub_cls = MagicMock()
    stub_cls.return_value.execute = MagicMock(side_effect=lambda *_a, **_k: answers())
    return stub_cls


def _hub_device_snapshot() -> Message:
    message = hub_device_response_pb2.StreamHubDeviceResponse()
    # Not an empty device: the parser must be given this one, not just any.
    message.success.snapshot.hub_device.motion_protect.SetInParent()
    return message


def _hub_device_failure() -> Message:
    message = hub_device_response_pb2.StreamHubDeviceResponse()
    message.failure.SetInParent()
    return message


def _assert_stream_call(
    stub_cls: MagicMock, channel: MagicMock, request: Message, timeout: int
) -> None:
    stub_cls.assert_called_once_with(channel)
    execute = stub_cls.return_value.execute
    execute.assert_called_once()
    assert execute.call_args.args == (request,)
    assert execute.call_args.kwargs == {"metadata": _METADATA, "timeout": timeout}


async def test_devices_snapshot_asks_for_the_space() -> None:
    api, channel = _api()
    snapshot = light_response_pb2.StreamLightDevicesResponse()
    snapshot.success.snapshot.SetInParent()
    stub_cls = _stream(snapshot)
    with patch(f"{_LIGHT_STUB}.StreamLightDevicesServiceStub", stub_cls):
        assert await api.get_devices_snapshot("space-1") == []
    _assert_stream_call(
        stub_cls, channel, light_request_pb2.StreamLightDevicesRequest(space_id="space-1"), 30
    )


@pytest.mark.parametrize(
    ("method", "parser", "parsed", "nothing"),
    [
        ("get_hub_device_temperature", "parse_hub_device_temperature", 21.5, None),
        (
            "get_hub_device_siren_settings",
            "parse_hub_device_siren_settings",
            {"siren_alarm_duration": 180},
            {},
        ),
    ],
)
async def test_hub_device_reads_ask_for_that_device_and_return_the_parsed_snapshot(
    method: str,
    parser: str,
    parsed: Any,  # noqa: ANN401
    nothing: Any,  # noqa: ANN401
) -> None:
    request = hub_device_request_pb2.StreamHubDeviceRequest(hub_id="hub-1", hub_device_id="dev-1")
    api, channel = _api()
    stub_cls = _stream(_hub_device_snapshot())
    with (
        patch(f"{_HUB_DEVICE_STUB}.StreamHubDeviceServiceStub", stub_cls),
        patch(
            f"custom_components.aegis_ajax.api.devices.devices_parser.{parser}",
            return_value=parsed,
        ) as parse,
    ):
        assert await getattr(api, method)("hub-1", "dev-1") == parsed
    _assert_stream_call(stub_cls, channel, request, 10)
    parse.assert_called_once()
    (hub_device,) = parse.call_args.args
    assert hub_device == _hub_device_snapshot().success.snapshot.hub_device
    assert hub_device.WhichOneof("device") == "motion_protect"

    # A failure answer, or a stream that ends without a snapshot, reads as nothing.
    for messages in ((_hub_device_failure(), _hub_device_snapshot()), ()):
        api, _ = _api()
        with (
            patch(f"{_HUB_DEVICE_STUB}.StreamHubDeviceServiceStub", _stream(*messages)),
            patch(
                f"custom_components.aegis_ajax.api.devices.devices_parser.{parser}",
                return_value=parsed,
            ) as parse,
        ):
            assert await getattr(api, method)("hub-1", "dev-1") == nothing
        parse.assert_not_called()


def _capture(response: bytes | Exception) -> tuple[DevicesApi, MagicMock, AsyncMock]:
    api, channel = _api()
    method = AsyncMock(side_effect=[response])
    channel.unary_unary.return_value = method
    return api, channel, method


@pytest.mark.parametrize(
    ("device_type", "wire_type"),
    [("motion_cam", 1), ("motion_cam_outdoor", 2), ("motion_cam_fibra", 3), ("not_mapped", 1)],
)
async def test_capture_photo_request(device_type: str, wire_type: int) -> None:
    api, channel, method = _capture(b"\x0a\x00")
    assert await api.capture_photo("hub-1", "dev-1", device_type) == "dev-1"
    channel.unary_unary.assert_called_once()
    assert channel.unary_unary.call_args.args == (_CAPTURE_PATH,)
    serializers = channel.unary_unary.call_args.kwargs
    assert set(serializers) == {"request_serializer", "response_deserializer"}
    # Raw bytes both ways.
    assert all(convert(b"raw") == b"raw" for convert in serializers.values())
    method.assert_awaited_once_with(
        b"\x0a\x05hub-1" + b"\x12\x05dev-1" + b"\x18" + bytes([wire_type]),
        metadata=_METADATA,
        timeout=30,
    )


@pytest.mark.parametrize(
    ("response", "accepted"),
    [
        (b"\x0a\x02ok", True),
        (b"\x12\x11ALREADY_PERFORMED", True),
        (b"\x12\x05other", False),
        (b"", False),
        (RuntimeError("rpc failed"), False),
    ],
)
async def test_capture_photo_reads_the_answer(response: bytes | Exception, accepted: bool) -> None:
    api, _, _ = _capture(response)
    assert await api.capture_photo("hub-1", "dev-1", "motion_cam") == (
        "dev-1" if accepted else None
    )


def _phod(*responses: Message) -> tuple[DevicesApi, MagicMock, MagicMock]:
    api, channel = _api()
    ok = phod_response_pb2.DeviceCommandPhotoOnDemandModeResponse()
    ok.success.SetInParent()
    stub_cls = MagicMock()
    stub_cls.return_value.execute = AsyncMock(side_effect=list(responses) or [ok, ok])
    return api, channel, stub_cls


def _phod_failure(error: str | None) -> Message:
    response = phod_response_pb2.DeviceCommandPhotoOnDemandModeResponse()
    response.failure.SetInParent()
    if error:
        getattr(response.failure, error).SetInParent()
    return response


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"user_enabled": True}, [("photo_on_demand_mode_user", "USER_ENABLE")]),
        ({"user_enabled": False}, [("photo_on_demand_mode_user", "USER_DISABLE")]),
        ({"scenario_enabled": True}, [("photo_on_demand_mode_scenario", "SCENARIO_ENABLE")]),
        ({"scenario_enabled": False}, [("photo_on_demand_mode_scenario", "SCENARIO_DISABLE")]),
        (
            {"user_enabled": False, "scenario_enabled": True},
            [
                ("photo_on_demand_mode_user", "USER_DISABLE"),
                ("photo_on_demand_mode_scenario", "SCENARIO_ENABLE"),
            ],
        ),
    ],
)
async def test_photo_on_demand_mode_sends_one_request_per_switch(
    kwargs: dict[str, bool], expected: list[tuple[str, str]]
) -> None:
    api, channel, stub_cls = _phod()
    with patch(f"{_PHOD_STUB}.DeviceCommandPhotoOnDemandModeServiceStub", stub_cls):
        await api.set_photo_on_demand_mode("hub-1", **kwargs)
    stub_cls.assert_called_once_with(channel)
    calls = stub_cls.return_value.execute.await_args_list
    assert [call.kwargs for call in calls] == [{"metadata": _METADATA, "timeout": 15}] * len(
        expected
    )
    sent = []
    for call in calls:
        (request,) = call.args
        assert request.hub_id == "hub-1"
        ((field, value),) = [(f, v) for f, v in request.ListFields() if f.name != "hub_id"]
        sent.append((field.name, field.enum_type.values_by_number[value].name.split("MODE_", 1)[1]))
    assert sent == expected


@pytest.mark.parametrize(
    ("kwargs", "error", "message"),
    [
        (
            {"user_enabled": True},
            "permission_denied",
            "photo_on_demand_mode user: permission_denied",
        ),
        ({"scenario_enabled": True}, None, "photo_on_demand_mode scenario: unknown"),
    ],
)
async def test_photo_on_demand_mode_rejection_names_the_switch(
    kwargs: dict[str, bool], error: str | None, message: str
) -> None:
    api, _, stub_cls = _phod(_phod_failure(error))
    with (
        patch(f"{_PHOD_STUB}.DeviceCommandPhotoOnDemandModeServiceStub", stub_cls),
        pytest.raises(DeviceCommandError) as err,
    ):
        await api.set_photo_on_demand_mode("hub-1", **kwargs)
    assert str(err.value) == message


async def test_photo_on_demand_mode_without_a_switch_is_refused_before_any_call() -> None:
    api, _, stub_cls = _phod()
    with (
        patch(f"{_PHOD_STUB}.DeviceCommandPhotoOnDemandModeServiceStub", stub_cls),
        pytest.raises(DeviceCommandError),
    ):
        await api.set_photo_on_demand_mode("hub-1")
    stub_cls.return_value.execute.assert_not_awaited()
