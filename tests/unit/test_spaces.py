"""Tests for spaces API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.protobuf.wrappers_pb2 import StringValue

from custom_components.aegis_ajax.api.models import (
    MonitoringCompanyStatus,
    Space,
    SpaceSnapshot,
)
from custom_components.aegis_ajax.api.spaces import SpacesApi
from custom_components.aegis_ajax.const import ChimeStatus, ConnectionStatus, SecurityState

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
        assert result.monitoring_companies_loaded is False


class TestExtractChimeStatus:
    """Real-proto coverage of the `tqs.a(space)` hub-Chime walk (#239).

    Reads off the full `Space` snapshot (the only response carrying `devices`).
    """

    @staticmethod
    def _build_space(chime_value: int | None) -> object:
        from systems.ajax.api.mobile.v2.common.space import space_pb2
        from systems.ajax.api.mobile.v2.common.space.device import (
            standalone_device_pb2,
        )

        space = space_pb2.Space(id="s1")
        if chime_value is not None:
            hub_dev = standalone_device_pb2.StandaloneDevice()
            hub_dev.hub.chime_status = chime_value
            space.devices.append(hub_dev)
        return space

    @pytest.mark.parametrize(
        ("chime_value", "expected"),
        [
            (1, ChimeStatus.ENABLED),
            (2, ChimeStatus.CAN_BE_ENABLED),
            (3, ChimeStatus.MALFUNCTION),
            (4, ChimeStatus.DISABLED),
            (0, ChimeStatus.UNSPECIFIED),
        ],
    )
    def test_reads_chime_status_off_hub_device(
        self, chime_value: int, expected: ChimeStatus
    ) -> None:
        space = self._build_space(chime_value)
        assert SpacesApi.extract_chime_status(space) == expected

    def test_no_hub_device_yields_unspecified(self) -> None:
        space = self._build_space(None)
        assert SpacesApi.extract_chime_status(space) == ChimeStatus.UNSPECIFIED

    def test_no_devices_attribute_yields_unspecified(self) -> None:
        # `spec=[]` makes attribute access raise AttributeError, emulating a
        # response object that doesn't carry `devices` (e.g. a LiteSpace).
        proto_space = MagicMock(spec=[])
        assert SpacesApi.extract_chime_status(proto_space) == ChimeStatus.UNSPECIFIED

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

    def test_parse_monitoring_company(self) -> None:
        proto_company = MagicMock()
        proto_company.company_info.name = "Secure Co"
        proto_company.status = 2

        result = SpacesApi.parse_monitoring_company(proto_company)

        assert result.name == "Secure Co"
        assert result.status == MonitoringCompanyStatus.APPROVED

    def test_parse_monitoring_company_unwraps_string_value_name(self) -> None:
        proto_company = MagicMock()
        proto_company.company_info.name = StringValue(value="Secure Co")
        proto_company.status = 2

        result = SpacesApi.parse_monitoring_company(proto_company)

        assert isinstance(result.name, str)
        assert result.name == "Secure Co"
        assert result.status == MonitoringCompanyStatus.APPROVED


class TestParseGroups:
    """Parse group definitions + per-group security state from a SpaceSecurity proto."""

    def _build_security(  # noqa: ANN202
        self,
        groups: list[tuple[str, str, str]] | None = None,
        states: dict[str, int] | None = None,
        mode: str = "group_mode",
    ):
        # Imports inside the method so they don't pollute sys.modules with
        # real proto packages at collection time — that breaks unrelated tests
        # that patch.dict child modules of the same package.
        from systems.ajax.api.mobile.v2.common.space.security import (  # noqa: PLC0415
            space_security_mode_pb2,
            space_security_pb2,
        )
        from systems.ajax.api.mobile.v2.common.space.security.group import (  # noqa: PLC0415
            group_mode_space_security_pb2,
            group_pb2,
            group_security_pb2,
        )
        from systems.ajax.api.mobile.v2.common.space.security.regular import (  # noqa: PLC0415
            regular_mode_space_security_pb2,
        )

        proto_groups = [
            group_pb2.Group(id=gid, name=name, sorting_key=sort_key)
            for gid, name, sort_key in (groups or [])
        ]
        if mode == "group_mode":
            group_securities = [
                group_security_pb2.GroupSecurity(group_id=gid, state=state)
                for gid, state in (states or {}).items()
            ]
            mode_msg = space_security_mode_pb2.SpaceSecurityMode(
                group_mode=group_mode_space_security_pb2.GroupModeSpaceSecurity(
                    groups=group_securities,
                )
            )
        else:
            mode_msg = space_security_mode_pb2.SpaceSecurityMode(
                regular_mode=regular_mode_space_security_pb2.RegularModeSpaceSecurity()
            )
        return space_security_pb2.SpaceSecurity(groups=proto_groups, mode=mode_msg)

    def test_two_groups_with_distinct_states(self) -> None:
        security = self._build_security(
            groups=[("g1", "Villa", "01"), ("g2", "Apartment", "02")],
            states={"g1": 1, "g2": 2},  # ARMED, DISARMED
        )
        groups, enabled = SpacesApi.parse_groups(security, space_id="space-1")

        assert enabled is True
        assert [g.id for g in groups] == ["g1", "g2"]
        assert [g.name for g in groups] == ["Villa", "Apartment"]
        assert [g.security_state for g in groups] == [SecurityState.ARMED, SecurityState.DISARMED]
        assert all(g.space_id == "space-1" for g in groups)

    def test_groups_sorted_by_sorting_key(self) -> None:
        security = self._build_security(
            groups=[
                ("g1", "Z-First-Insertion", "02"),
                ("g2", "A-Second-Insertion", "01"),
            ],
            states={"g1": 2, "g2": 2},
        )
        groups, _ = SpacesApi.parse_groups(security, space_id="space-1")
        assert [g.sorting_key for g in groups] == ["01", "02"]
        assert groups[0].name == "A-Second-Insertion"

    def test_state_unknown_when_security_missing_for_group(self) -> None:
        security = self._build_security(
            groups=[("g1", "Villa", "01")],
            states={},  # no per-group state in mode.group_mode.groups
        )
        groups, enabled = SpacesApi.parse_groups(security, space_id="s")
        assert enabled is True
        assert groups[0].security_state == SecurityState.NONE

    def test_returns_empty_when_regular_mode(self) -> None:
        security = self._build_security(
            groups=[("g1", "Villa", "01")],  # definitions exist but mode is regular
            mode="regular_mode",
        )
        groups, enabled = SpacesApi.parse_groups(security, space_id="s")
        assert groups == ()
        assert enabled is False

    def test_skips_groups_without_id(self) -> None:
        security = self._build_security(
            groups=[("", "Bad", "00"), ("g1", "Good", "01")],
            states={"g1": 1},
        )
        groups, _ = SpacesApi.parse_groups(security, space_id="s")
        assert [g.id for g in groups] == ["g1"]


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
        snapshot_msg.success.snapshot.monitoring_companies = []

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

        with (
            patch.dict(
                "sys.modules",
                {
                    _STREAM_SPACE_REQUEST: request_pb2,
                    _SPACE_GRPC: grpc_module,
                    _SPACE_LOCATOR: locator_pb2,
                },
            ),
            # The production code does `from systems.ajax...space import
            # space_locator_pb2` which resolves via the parent's attribute
            # if previously loaded. Patch that attribute too so the test is
            # robust against earlier tests that triggered the real import.
            patch(
                "systems.ajax.api.mobile.v2.common.space.space_locator_pb2",
                locator_pb2,
                create=True,
            ),
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


class TestGetSpaceSnapshot:
    @pytest.mark.asyncio
    async def test_returns_rooms_and_monitoring_companies(self) -> None:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []

        api = SpacesApi(mock_client)

        room = MagicMock()
        room.id = "r1"
        room.name = "Kitchen"

        approved = MagicMock()
        approved.company_info.name = "Central One"
        approved.status = 2

        pending = MagicMock()
        pending.company_info.name = "Central Two"
        pending.status = 1

        snapshot_msg = MagicMock()
        snapshot_msg.HasField.side_effect = lambda f: f == "success"
        snapshot_msg.success.WhichOneof.return_value = "snapshot"
        snapshot_msg.success.snapshot.rooms = [room]
        snapshot_msg.success.snapshot.monitoring_companies = [approved, pending]

        stream = _async_iter([snapshot_msg])
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
            snapshot = await api.get_space_snapshot("space-1")

        assert isinstance(snapshot, SpaceSnapshot)
        assert len(snapshot.rooms) == 1
        assert snapshot.rooms[0].id == "r1"
        assert snapshot.rooms[0].name == "Kitchen"
        assert snapshot.monitoring_companies[0].name == "Central One"
        assert snapshot.monitoring_companies[0].status == MonitoringCompanyStatus.APPROVED
        assert snapshot.monitoring_companies[1].name == "Central Two"
        assert snapshot.monitoring_companies[1].status == MonitoringCompanyStatus.PENDING_APPROVAL
        assert snapshot.monitoring_companies_loaded is True

    @pytest.mark.asyncio
    async def test_reads_hub_chime_status_from_snapshot(self) -> None:
        """The snapshot's full Space carries the hub Chime status (#239)."""
        from systems.ajax.api.mobile.v2.common.space.device import (
            standalone_device_pb2,
        )

        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []
        api = SpacesApi(mock_client)

        hub_dev = standalone_device_pb2.StandaloneDevice()
        hub_dev.hub.chime_status = 1  # ENABLED

        snapshot_msg = MagicMock()
        snapshot_msg.HasField.side_effect = lambda f: f == "success"
        snapshot_msg.success.WhichOneof.return_value = "snapshot"
        snapshot_msg.success.snapshot.rooms = []
        snapshot_msg.success.snapshot.monitoring_companies = []
        snapshot_msg.success.snapshot.devices = [hub_dev]

        stream = _async_iter([snapshot_msg])
        stub_instance = MagicMock()
        stub_instance.stream = MagicMock(return_value=stream)
        grpc_module = MagicMock(SpaceServiceStub=MagicMock(return_value=stub_instance))

        with patch.dict(
            "sys.modules",
            {
                _STREAM_SPACE_REQUEST: MagicMock(),
                _SPACE_GRPC: grpc_module,
                _SPACE_LOCATOR: MagicMock(),
            },
        ):
            snapshot = await api.get_space_snapshot("space-1")

        assert snapshot.chime_status == ChimeStatus.ENABLED


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

        with (
            patch.dict(
                "sys.modules",
                {
                    _PANIC_REQUEST: request_pb2,
                    _PANIC_GRPC: grpc_module,
                    _LOCATOR: locator_pb2,
                },
            ),
            patch(
                "systems.ajax.api.mobile.v2.common.space.space_locator_pb2",
                locator_pb2,
                create=True,
            ),
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


_GET_MONITORING_REQ = (
    "systems.ajax.api.mobile.v2.space.company.monitoring.get_monitoring_company_request_pb2"
)
_GET_MONITORING_GRPC = (
    "systems.ajax.api.mobile.v2.space.company.monitoring"
    ".space_monitoring_company_endpoints_pb2_grpc"
)


class TestParseMonitoringCompanyHexId:
    def test_extracts_hex_id_when_present(self) -> None:
        proto_company = MagicMock()
        proto_company.company_info.name = "Central One"
        proto_company.company_info.hex_id = "AABBDC47"
        proto_company.status = 2

        result = SpacesApi.parse_monitoring_company(proto_company)

        assert result.hex_id == "AABBDC47"
        assert result.name == "Central One"

    def test_hex_id_defaults_to_empty_when_field_absent(self) -> None:
        proto_company = MagicMock(spec=["company_info", "status"])
        proto_company.company_info = MagicMock(spec=["name"])
        proto_company.company_info.name = "Central"
        proto_company.status = 2

        result = SpacesApi.parse_monitoring_company(proto_company)

        assert result.hex_id == ""


class TestGetMonitoringCompany:
    @pytest.mark.asyncio
    async def test_returns_parsed_company_on_success(self) -> None:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []

        api = SpacesApi(mock_client)

        proto_company = MagicMock()
        proto_company.company_info.name = "EXPANSIVA"
        proto_company.company_info.hex_id = "0000016A"
        proto_company.status = 2

        success_response = MagicMock()
        success_response.WhichOneof.return_value = "success"
        success_response.success.company = proto_company

        stub_instance = MagicMock()
        stub_instance.getMonitoringCompany = AsyncMock(return_value=success_response)

        request_pb2 = MagicMock()
        grpc_module = MagicMock(
            SpaceMonitoringCompanyServiceStub=MagicMock(return_value=stub_instance)
        )

        with patch.dict(
            "sys.modules",
            {
                _GET_MONITORING_REQ: request_pb2,
                _GET_MONITORING_GRPC: grpc_module,
            },
        ):
            result = await api.get_monitoring_company("space-1", "0000016A")

        assert result is not None
        assert result.name == "EXPANSIVA"
        assert result.hex_id == "0000016A"
        assert result.status == MonitoringCompanyStatus.APPROVED

        # Verify the request that was sent (request was built from the patched module)
        stub_instance.getMonitoringCompany.assert_awaited_once()
        assert request_pb2.GetMonitoringCompanyRequest.call_args.kwargs == {
            "company_hex_id": "0000016A",
            "space_id": "space-1",
        }

    @pytest.mark.asyncio
    async def test_returns_none_on_failure_response(self) -> None:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []

        api = SpacesApi(mock_client)

        failure_response = MagicMock()
        failure_response.WhichOneof.return_value = "failure"

        stub_instance = MagicMock()
        stub_instance.getMonitoringCompany = AsyncMock(return_value=failure_response)

        request_pb2 = MagicMock()
        grpc_module = MagicMock(
            SpaceMonitoringCompanyServiceStub=MagicMock(return_value=stub_instance)
        )

        with patch.dict(
            "sys.modules",
            {
                _GET_MONITORING_REQ: request_pb2,
                _GET_MONITORING_GRPC: grpc_module,
            },
        ):
            result = await api.get_monitoring_company("space-1", "00000000")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_rpc_exception(self) -> None:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []

        api = SpacesApi(mock_client)

        stub_instance = MagicMock()
        stub_instance.getMonitoringCompany = AsyncMock(side_effect=RuntimeError("boom"))

        request_pb2 = MagicMock()
        grpc_module = MagicMock(
            SpaceMonitoringCompanyServiceStub=MagicMock(return_value=stub_instance)
        )

        with patch.dict(
            "sys.modules",
            {
                _GET_MONITORING_REQ: request_pb2,
                _GET_MONITORING_GRPC: grpc_module,
            },
        ):
            result = await api.get_monitoring_company("space-1", "any")

        assert result is None


class TestGetSpaceSnapshotResolvesMissingNames:
    """get_space_snapshot fills empty names via getMonitoringCompany."""

    @pytest.mark.asyncio
    async def test_resolves_missing_name_when_hex_id_present(self) -> None:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []

        api = SpacesApi(mock_client)

        # Company arrived with empty name but a hex_id — the modern shape.
        unresolved = MagicMock()
        unresolved.company_info.name = ""
        unresolved.company_info.hex_id = "0000016A"
        unresolved.status = 2

        snapshot_msg = MagicMock()
        snapshot_msg.HasField.side_effect = lambda f: f == "success"
        snapshot_msg.success.WhichOneof.return_value = "snapshot"
        snapshot_msg.success.snapshot.rooms = []
        snapshot_msg.success.snapshot.monitoring_companies = [unresolved]

        stream = _async_iter([snapshot_msg])
        space_stub = MagicMock()
        space_stub.stream = MagicMock(return_value=stream)

        # The resolver's response — fully populated company.
        resolved_proto = MagicMock()
        resolved_proto.company_info.name = "EXPANSIVA"
        resolved_proto.company_info.hex_id = "0000016A"
        resolved_proto.status = 2

        resolve_response = MagicMock()
        resolve_response.WhichOneof.return_value = "success"
        resolve_response.success.company = resolved_proto

        monitoring_stub = MagicMock()
        monitoring_stub.getMonitoringCompany = AsyncMock(return_value=resolve_response)

        stream_request_pb2 = MagicMock()
        space_grpc = MagicMock(SpaceServiceStub=MagicMock(return_value=space_stub))
        locator_pb2 = MagicMock()

        get_monitoring_req_pb2 = MagicMock()
        get_monitoring_grpc = MagicMock(
            SpaceMonitoringCompanyServiceStub=MagicMock(return_value=monitoring_stub)
        )

        with patch.dict(
            "sys.modules",
            {
                _STREAM_SPACE_REQUEST: stream_request_pb2,
                _SPACE_GRPC: space_grpc,
                _SPACE_LOCATOR: locator_pb2,
                _GET_MONITORING_REQ: get_monitoring_req_pb2,
                _GET_MONITORING_GRPC: get_monitoring_grpc,
            },
        ):
            snapshot = await api.get_space_snapshot("space-1")

        # The resolver fired exactly once and the resulting snapshot has the name.
        monitoring_stub.getMonitoringCompany.assert_awaited_once()
        assert snapshot.monitoring_companies[0].name == "EXPANSIVA"
        assert snapshot.monitoring_companies[0].hex_id == "0000016A"
        # Status from the snapshot stream wins (authoritative state source).
        assert snapshot.monitoring_companies[0].status == MonitoringCompanyStatus.APPROVED

    @pytest.mark.asyncio
    async def test_skips_resolver_when_name_already_present(self) -> None:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []

        api = SpacesApi(mock_client)

        populated = MagicMock()
        populated.company_info.name = "Already Named"
        populated.company_info.hex_id = "AABBCCDD"
        populated.status = 2

        snapshot_msg = MagicMock()
        snapshot_msg.HasField.side_effect = lambda f: f == "success"
        snapshot_msg.success.WhichOneof.return_value = "snapshot"
        snapshot_msg.success.snapshot.rooms = []
        snapshot_msg.success.snapshot.monitoring_companies = [populated]

        stream = _async_iter([snapshot_msg])
        space_stub = MagicMock()
        space_stub.stream = MagicMock(return_value=stream)

        monitoring_stub = MagicMock()
        monitoring_stub.getMonitoringCompany = AsyncMock()

        stream_request_pb2 = MagicMock()
        space_grpc = MagicMock(SpaceServiceStub=MagicMock(return_value=space_stub))
        locator_pb2 = MagicMock()
        get_monitoring_req_pb2 = MagicMock()
        get_monitoring_grpc = MagicMock(
            SpaceMonitoringCompanyServiceStub=MagicMock(return_value=monitoring_stub)
        )

        with patch.dict(
            "sys.modules",
            {
                _STREAM_SPACE_REQUEST: stream_request_pb2,
                _SPACE_GRPC: space_grpc,
                _SPACE_LOCATOR: locator_pb2,
                _GET_MONITORING_REQ: get_monitoring_req_pb2,
                _GET_MONITORING_GRPC: get_monitoring_grpc,
            },
        ):
            snapshot = await api.get_space_snapshot("space-1")

        monitoring_stub.getMonitoringCompany.assert_not_called()
        assert snapshot.monitoring_companies[0].name == "Already Named"

    @pytest.mark.asyncio
    async def test_falls_back_to_original_record_when_resolver_returns_none(self) -> None:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []

        api = SpacesApi(mock_client)

        unresolved = MagicMock()
        unresolved.company_info.name = ""
        unresolved.company_info.hex_id = "DEADBEEF"
        unresolved.status = 1

        snapshot_msg = MagicMock()
        snapshot_msg.HasField.side_effect = lambda f: f == "success"
        snapshot_msg.success.WhichOneof.return_value = "snapshot"
        snapshot_msg.success.snapshot.rooms = []
        snapshot_msg.success.snapshot.monitoring_companies = [unresolved]

        stream = _async_iter([snapshot_msg])
        space_stub = MagicMock()
        space_stub.stream = MagicMock(return_value=stream)

        failure_response = MagicMock()
        failure_response.WhichOneof.return_value = "failure"

        monitoring_stub = MagicMock()
        monitoring_stub.getMonitoringCompany = AsyncMock(return_value=failure_response)

        stream_request_pb2 = MagicMock()
        space_grpc = MagicMock(SpaceServiceStub=MagicMock(return_value=space_stub))
        locator_pb2 = MagicMock()
        get_monitoring_req_pb2 = MagicMock()
        get_monitoring_grpc = MagicMock(
            SpaceMonitoringCompanyServiceStub=MagicMock(return_value=monitoring_stub)
        )

        with patch.dict(
            "sys.modules",
            {
                _STREAM_SPACE_REQUEST: stream_request_pb2,
                _SPACE_GRPC: space_grpc,
                _SPACE_LOCATOR: locator_pb2,
                _GET_MONITORING_REQ: get_monitoring_req_pb2,
                _GET_MONITORING_GRPC: get_monitoring_grpc,
            },
        ):
            snapshot = await api.get_space_snapshot("space-1")

        # Name stays empty (resolver failed), hex_id + status preserved.
        assert snapshot.monitoring_companies[0].name == ""
        assert snapshot.monitoring_companies[0].hex_id == "DEADBEEF"
        assert snapshot.monitoring_companies[0].status == MonitoringCompanyStatus.PENDING_APPROVAL


class TestGetMemberSpacePermissions:
    """Read-only fetch of the current user's space permissions (#bypass auto)."""

    def _make_api(self) -> SpacesApi:
        client = MagicMock()
        client._get_channel.return_value = MagicMock()
        client._session.get_call_metadata.return_value = []
        return SpacesApi(client)

    @staticmethod
    async def _aiter(items: list) -> object:
        for it in items:
            yield it

    def _lite_response(self, members: list) -> object:
        from v3.mobilegwsvc.service.stream_lite_space_members import response_pb2 as r

        resp = r.StreamLiteSpaceMembersResponse()
        for mid, hexid in members:
            m = resp.success.snapshot.lite_space_members.lite_space_members.add()
            m.id = mid
            m.hex_id = hexid
        return resp

    def _full_response(self, permission_numbers: list) -> object:
        from v3.mobilegwsvc.service.stream_space_member import response_pb2 as r

        resp = r.StreamSpaceMemberResponse()
        mem = resp.success.snapshot.space_member
        for p in permission_numbers:
            mem.space_permissions.permissions.append(p)
        return resp

    @pytest.mark.asyncio
    async def test_returns_permission_names_for_matched_user(self) -> None:
        from systems.ajax.api.mobile.v2.common.space.member import space_permission_pb2 as sp
        from v3.mobilegwsvc.service.stream_lite_space_members import (
            endpoint_pb2_grpc as lite_grpc,
        )
        from v3.mobilegwsvc.service.stream_space_member import (
            endpoint_pb2_grpc as full_grpc,
        )

        api = self._make_api()
        lite = self._lite_response([("mid-1", "AAAA1111"), ("mid-2", "BBBB2222")])
        full = self._full_response([sp.SpacePermission.ARM, sp.SpacePermission.DEVICE_EDIT])

        class _LiteStub:
            def __init__(self, ch: object) -> None: ...
            def execute(self, *a: object, **k: object) -> object:
                return TestGetMemberSpacePermissions._aiter([lite])

        class _FullStub:
            def __init__(self, ch: object) -> None: ...
            def execute(self, *a: object, **k: object) -> object:
                return TestGetMemberSpacePermissions._aiter([full])

        with (
            patch.object(lite_grpc, "StreamLiteSpaceMembersServiceStub", _LiteStub),
            patch.object(full_grpc, "StreamSpaceMemberServiceStub", _FullStub),
        ):
            perms = await api.get_member_space_permissions("space-1", "BBBB2222")

        assert perms == {"ARM", "DEVICE_EDIT"}

    @pytest.mark.asyncio
    async def test_returns_none_when_user_not_a_member(self) -> None:
        from v3.mobilegwsvc.service.stream_lite_space_members import (
            endpoint_pb2_grpc as lite_grpc,
        )

        api = self._make_api()
        lite = self._lite_response([("mid-1", "AAAA1111")])

        class _LiteStub:
            def __init__(self, ch: object) -> None: ...
            def execute(self, *a: object, **k: object) -> object:
                return TestGetMemberSpacePermissions._aiter([lite])

        with patch.object(lite_grpc, "StreamLiteSpaceMembersServiceStub", _LiteStub):
            perms = await api.get_member_space_permissions("space-1", "NOPE9999")

        assert perms is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self) -> None:
        api = self._make_api()
        api._client._get_channel.side_effect = RuntimeError("boom")

        perms = await api.get_member_space_permissions("space-1", "AAAA1111")

        assert perms is None
