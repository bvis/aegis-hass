"""Tests for spaces API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax.api.models import Space
from custom_components.aegis_ajax.api.spaces import SpacesApi
from custom_components.aegis_ajax.const import ConnectionStatus, SecurityState

_FIND_SPACES_BASE = "v3.mobilegwsvc.service.find_user_spaces_with_pagination"
_STREAM_SPACE_REQUEST = "systems.ajax.api.mobile.v2.space.stream_space_updates_request_pb2"
_SPACE_GRPC = "systems.ajax.api.mobile.v2.space.space_endpoints_pb2_grpc"
_SPACE_LOCATOR = "systems.ajax.api.mobile.v2.common.space.space_locator_pb2"


class TestParseSpace:
    def test_parse_space_from_proto(self) -> None:
        proto_space = MagicMock()
        proto_space.id = "space-abc"
        proto_space.hub_id = "hub-xyz"
        proto_space.profile.name = "My Home"
        proto_space.security_state = 2  # DISARMED
        proto_space.hub_connection_status = 1  # ONLINE
        proto_space.malfunctions_count = 0

        result = SpacesApi.parse_space(proto_space)
        assert isinstance(result, Space)
        assert result.id == "space-abc"
        assert result.hub_id == "hub-xyz"
        assert result.name == "My Home"
        assert result.security_state == SecurityState.DISARMED
        assert result.connection_status == ConnectionStatus.ONLINE

    def test_parse_space_armed(self) -> None:
        proto_space = MagicMock()
        proto_space.id = "s1"
        proto_space.hub_id = "h1"
        proto_space.profile.name = "Office"
        proto_space.security_state = 1  # ARMED
        proto_space.hub_connection_status = 1
        proto_space.malfunctions_count = 2

        result = SpacesApi.parse_space(proto_space)
        assert result.security_state == SecurityState.ARMED
        assert result.malfunctions_count == 2

    def test_parse_space_hub_id_optional(self) -> None:
        proto_space = MagicMock()
        proto_space.id = "s1"
        proto_space.hub_id = ""
        proto_space.profile.name = "Test"
        proto_space.security_state = 0
        proto_space.hub_connection_status = 0
        proto_space.malfunctions_count = 0

        result = SpacesApi.parse_space(proto_space)
        assert result.hub_id == ""


class TestListSpaces:
    @pytest.mark.asyncio
    async def test_list_spaces_success(self) -> None:
        mock_client = MagicMock()
        mock_channel = MagicMock()
        mock_client._get_channel.return_value = mock_channel
        mock_client._session.get_call_metadata.return_value = [("token", "abc")]

        api = SpacesApi(mock_client)

        # Build mock spaces
        mock_space = MagicMock()
        mock_space.id = "space-1"
        mock_space.hub_id = "hub-1"
        mock_space.profile.name = "Home"
        mock_space.security_state = 2
        mock_space.hub_connection_status = 1
        mock_space.malfunctions_count = 0

        mock_response = MagicMock()
        mock_response.HasField.return_value = False
        mock_response.success.spaces = [mock_space]

        mock_stub_instance = MagicMock()
        mock_stub_instance.execute = AsyncMock(return_value=mock_response)
        mock_stub_class = MagicMock(return_value=mock_stub_instance)

        mock_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(FindUserSpacesWithPaginationServiceStub=mock_stub_class)

        with patch.dict(
            "sys.modules",
            {
                f"{_FIND_SPACES_BASE}.endpoint_pb2_grpc": mock_grpc_module,
                f"{_FIND_SPACES_BASE}.request_pb2": mock_request_pb2,
                _FIND_SPACES_BASE: MagicMock(
                    endpoint_pb2_grpc=mock_grpc_module,
                    request_pb2=mock_request_pb2,
                ),
            },
        ):
            spaces = await api.list_spaces()

        assert len(spaces) == 1
        assert spaces[0].id == "space-1"

    @pytest.mark.asyncio
    async def test_list_spaces_failure_returns_empty(self) -> None:
        mock_client = MagicMock()
        mock_channel = MagicMock()
        mock_client._get_channel.return_value = mock_channel
        mock_client._session.get_call_metadata.return_value = []

        api = SpacesApi(mock_client)

        mock_response = MagicMock()
        mock_response.HasField.return_value = True  # has failure

        mock_stub_instance = MagicMock()
        mock_stub_instance.execute = AsyncMock(return_value=mock_response)
        mock_stub_class = MagicMock(return_value=mock_stub_instance)

        mock_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(FindUserSpacesWithPaginationServiceStub=mock_stub_class)

        with patch.dict(
            "sys.modules",
            {
                f"{_FIND_SPACES_BASE}.endpoint_pb2_grpc": mock_grpc_module,
                f"{_FIND_SPACES_BASE}.request_pb2": mock_request_pb2,
                _FIND_SPACES_BASE: MagicMock(
                    endpoint_pb2_grpc=mock_grpc_module,
                    request_pb2=mock_request_pb2,
                ),
            },
        ):
            spaces = await api.list_spaces()

        assert spaces == []


class _AsyncIter:
    """Minimal async iterator that mirrors the grpc stream API used by SpacesApi."""

    def __init__(self, src: list) -> None:
        self._src = list(src)

    def __aiter__(self) -> _AsyncIter:
        return self

    async def __anext__(self) -> object:
        if not self._src:
            raise StopAsyncIteration
        return self._src.pop(0)

    def cancel(self) -> None:
        self._src.clear()


def _async_iter(items: list) -> _AsyncIter:
    return _AsyncIter(items)


class TestListRooms:
    @pytest.mark.asyncio
    async def test_list_rooms_returns_snapshot_rooms(self) -> None:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []

        api = SpacesApi(mock_client)

        room1 = MagicMock()
        room1.id = "r1"
        room1.name = "Kitchen"
        room2 = MagicMock()
        room2.id = "r2"
        room2.name = "Bedroom"

        snapshot_msg = MagicMock()
        snapshot_msg.HasField.side_effect = lambda f: f == "success"
        snapshot_msg.success.WhichOneof.return_value = "snapshot"
        snapshot_msg.success.snapshot.rooms = [room1, room2]

        update_msg = MagicMock()
        update_msg.HasField.side_effect = lambda f: f == "success"
        update_msg.success.WhichOneof.return_value = "update"

        stream = _async_iter([snapshot_msg, update_msg])
        stub_instance = MagicMock()
        stub_instance.stream = MagicMock(return_value=stream)
        stub_class = MagicMock(return_value=stub_instance)

        request_pb2 = MagicMock()
        grpc_module = MagicMock(SpaceServiceStub=stub_class)
        locator_pb2 = MagicMock()
        locator_pb2.SpaceLocator = MagicMock(return_value="locator-marker")

        with patch.dict(
            "sys.modules",
            {
                _STREAM_SPACE_REQUEST: request_pb2,
                _SPACE_GRPC: grpc_module,
                _SPACE_LOCATOR: locator_pb2,
            },
        ):
            rooms = await api.list_rooms("space-1")

        assert len(rooms) == 2
        assert rooms[0].id == "r1"
        assert rooms[0].name == "Kitchen"
        assert rooms[0].space_id == "space-1"
        assert rooms[1].name == "Bedroom"
        # We close the stream after the first snapshot rather than draining it
        assert stream._src == []
        locator_pb2.SpaceLocator.assert_called_once_with(space_id="space-1")

    @pytest.mark.asyncio
    async def test_list_rooms_returns_empty_on_failure(self) -> None:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []

        api = SpacesApi(mock_client)

        failure_msg = MagicMock()
        failure_msg.HasField.side_effect = lambda f: f == "failure"

        stream = _async_iter([failure_msg])
        stub_instance = MagicMock()
        stub_instance.stream = MagicMock(return_value=stream)

        request_pb2 = MagicMock()
        grpc_module = MagicMock(SpaceServiceStub=MagicMock(return_value=stub_instance))
        locator_pb2 = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                _STREAM_SPACE_REQUEST: request_pb2,
                _SPACE_GRPC: grpc_module,
                _SPACE_LOCATOR: locator_pb2,
            },
        ):
            rooms = await api.list_rooms("space-1")

        assert rooms == []


_PANIC_REQUEST = "systems.ajax.api.mobile.v2.space.press_panic_button_request_pb2"
_PANIC_GRPC = "systems.ajax.api.mobile.v2.space.space_endpoints_pb2_grpc"
_LOCATOR = "systems.ajax.api.mobile.v2.common.space.space_locator_pb2"


def _patched_panic_modules(stub_class: MagicMock) -> dict[str, MagicMock]:
    """Build a sys.modules patch for the panic button proto imports."""
    request_pb2 = MagicMock()
    grpc_module = MagicMock(SpaceServiceStub=stub_class)
    locator_pb2 = MagicMock()
    locator_pb2.SpaceLocator = MagicMock(side_effect=lambda **kwargs: kwargs)
    return {
        _PANIC_REQUEST: request_pb2,
        _PANIC_GRPC: grpc_module,
        _LOCATOR: locator_pb2,
    }


class TestPressPanicButton:
    @pytest.mark.asyncio
    async def test_press_panic_button_success(self) -> None:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = [("token", "abc")]

        api = SpacesApi(mock_client)

        # The proto request object: keep an attribute bag we can inspect.
        request_obj = MagicMock()
        request_pb2 = MagicMock()
        request_pb2.PressPanicButtonRequest = MagicMock(return_value=request_obj)

        response = MagicMock()
        response.HasField.return_value = False  # success branch

        stub_instance = MagicMock()
        stub_instance.pressPanicButton = AsyncMock(return_value=response)
        stub_class = MagicMock(return_value=stub_instance)

        grpc_module = MagicMock(SpaceServiceStub=stub_class)
        locator_pb2 = MagicMock()
        locator_pb2.SpaceLocator = MagicMock(return_value="locator-marker")

        with patch.dict(
            "sys.modules",
            {
                _PANIC_REQUEST: request_pb2,
                _PANIC_GRPC: grpc_module,
                _LOCATOR: locator_pb2,
            },
        ):
            await api.press_panic_button("space-1")

        # SpaceLocator built with the right space_id
        locator_pb2.SpaceLocator.assert_called_once_with(space_id="space-1")
        # Request created with that locator and no location override
        request_pb2.PressPanicButtonRequest.assert_called_once_with(space_locator="locator-marker")
        # Stub method was awaited
        stub_instance.pressPanicButton.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_press_panic_button_with_coordinates(self) -> None:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []

        api = SpacesApi(mock_client)

        request_obj = MagicMock()
        request_pb2 = MagicMock()
        request_pb2.PressPanicButtonRequest = MagicMock(return_value=request_obj)

        response = MagicMock()
        response.HasField.return_value = False
        stub_instance = MagicMock()
        stub_instance.pressPanicButton = AsyncMock(return_value=response)
        stub_class = MagicMock(return_value=stub_instance)
        grpc_module = MagicMock(SpaceServiceStub=stub_class)
        locator_pb2 = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                _PANIC_REQUEST: request_pb2,
                _PANIC_GRPC: grpc_module,
                _LOCATOR: locator_pb2,
            },
        ):
            await api.press_panic_button("space-1", latitude=40.4168, longitude=-3.7038)

        # latitude / longitude assigned on the request's location field
        assert request_obj.location.latitude == 40.4168
        assert request_obj.location.longitude == -3.7038

    @pytest.mark.asyncio
    async def test_press_panic_button_failure_raises(self) -> None:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []

        api = SpacesApi(mock_client)

        response = MagicMock()
        response.HasField.return_value = True
        response.failure.WhichOneof.return_value = "permissions_denied"
        stub_instance = MagicMock()
        stub_instance.pressPanicButton = AsyncMock(return_value=response)

        request_pb2 = MagicMock()
        grpc_module = MagicMock(SpaceServiceStub=MagicMock(return_value=stub_instance))
        locator_pb2 = MagicMock()

        with (
            patch.dict(
                "sys.modules",
                {
                    _PANIC_REQUEST: request_pb2,
                    _PANIC_GRPC: grpc_module,
                    _LOCATOR: locator_pb2,
                },
            ),
            pytest.raises(RuntimeError, match="permissions_denied"),
        ):
            await api.press_panic_button("space-1")
