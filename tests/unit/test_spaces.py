"""Tests for spaces API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.protobuf.wrappers_pb2 import StringValue

from custom_components.aegis_ajax.api.models import (
    MonitoringCompany,
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
        night_mode_enabled: bool = False,
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
                    night_mode_enabled=night_mode_enabled,
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
        groups, enabled, _ = SpacesApi.parse_groups(security, space_id="space-1")

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
        groups, _, _ = SpacesApi.parse_groups(security, space_id="space-1")
        assert [g.sorting_key for g in groups] == ["01", "02"]
        assert groups[0].name == "A-Second-Insertion"

    def test_state_unknown_when_security_missing_for_group(self) -> None:
        security = self._build_security(
            groups=[("g1", "Villa", "01")],
            states={},  # no per-group state in mode.group_mode.groups
        )
        groups, enabled, _ = SpacesApi.parse_groups(security, space_id="s")
        assert enabled is True
        assert groups[0].security_state == SecurityState.NONE

    def test_returns_empty_when_regular_mode(self) -> None:
        security = self._build_security(
            groups=[("g1", "Villa", "01")],  # definitions exist but mode is regular
            mode="regular_mode",
        )
        groups, enabled, _ = SpacesApi.parse_groups(security, space_id="s")
        assert groups == ()
        assert enabled is False

    def test_skips_groups_without_id(self) -> None:
        security = self._build_security(
            groups=[("", "Bad", "00"), ("g1", "Good", "01")],
            states={"g1": 1},
        )
        groups, _, _ = SpacesApi.parse_groups(security, space_id="s")
        assert [g.id for g in groups] == ["g1"]

    def test_night_mode_enabled_parsed_from_group_mode(self) -> None:
        # In group mode the lite `DisplayedSpaceSecurityState` reports
        # PARTIALLY_ARMED while night mode is active — the only wire signal
        # that distinguishes "night mode" from "some groups armed" is
        # `mode.group_mode.night_mode_enabled` on the full snapshot (#284).
        security = self._build_security(
            groups=[("g1", "Villa", "01")],
            states={"g1": 1},
            night_mode_enabled=True,
        )
        result = SpacesApi.parse_groups(security, space_id="s")
        assert result.night_mode_enabled is True

    def test_night_mode_disabled_by_default(self) -> None:
        security = self._build_security(groups=[("g1", "Villa", "01")], states={"g1": 1})
        result = SpacesApi.parse_groups(security, space_id="s")
        assert result.night_mode_enabled is False

    def test_night_mode_false_in_regular_mode(self) -> None:
        security = self._build_security(groups=[("g1", "Villa", "01")], mode="regular_mode")
        result = SpacesApi.parse_groups(security, space_id="s")
        assert result.night_mode_enabled is False


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
    async def test_returns_rooms(self) -> None:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []

        api = SpacesApi(mock_client)

        room = MagicMock()
        room.id = "r1"
        room.name = "Kitchen"

        snapshot_msg = MagicMock()
        snapshot_msg.HasField.side_effect = lambda f: f == "success"
        snapshot_msg.success.WhichOneof.return_value = "snapshot"
        snapshot_msg.success.snapshot.rooms = [room]

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


_MONITORING_GRPC = (
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


class TestFindSpaceMonitoringCompanies:
    """#561: from client version 3.57 the CRA companies come from here."""

    @staticmethod
    def _api_with(response: object) -> tuple[SpacesApi, MagicMock]:
        mock_client = MagicMock()
        mock_client._get_channel.return_value = MagicMock()
        mock_client._session.get_call_metadata.return_value = []
        stub = MagicMock()
        stub.findMonitoringCompanies = AsyncMock(return_value=response)
        return SpacesApi(mock_client), stub

    @staticmethod
    def _company(hex_id: str, name: str, status: int) -> object:
        from systems.ajax.api.mobile.v2.common.space.company import (  # noqa: PLC0415
            space_monitoring_company_pb2,
        )

        company = space_monitoring_company_pb2.SpaceMonitoringCompany(status=status)
        company.company_info.hex_id = hex_id
        company.company_info.name.value = name
        return company

    @pytest.mark.asyncio
    async def test_returns_only_the_space_block(self) -> None:
        from systems.ajax.api.mobile.v2.space.company.monitoring import (  # noqa: PLC0415
            find_monitoring_companies_request_pb2 as pb2,
        )

        response = pb2.FindMonitoringCompaniesResponse()
        response.success.space_monitoring_companies_block.companies.append(
            self._company("0000016A", "Central One", 2)
        )
        response.success.available_monitoring_companies_block.companies.append(
            self._company("0000FFFF", "Someone Else", 0)
        )
        api, stub = self._api_with(response)

        with patch.dict(
            "sys.modules",
            {_MONITORING_GRPC: MagicMock(SpaceMonitoringCompanyServiceStub=lambda _c: stub)},
        ):
            companies = await api.find_space_monitoring_companies("space-1", "ES")

        assert companies == [
            MonitoringCompany(
                name="Central One", status=MonitoringCompanyStatus.APPROVED, hex_id="0000016A"
            )
        ]
        request = stub.findMonitoringCompanies.await_args.args[0]
        assert request.space_id == "space-1"
        assert request.company_country_code == "ES"

    @pytest.mark.asyncio
    async def test_failure_response_raises(self) -> None:
        from systems.ajax.api.mobile.v2.space.company.monitoring import (  # noqa: PLC0415
            find_monitoring_companies_request_pb2 as pb2,
        )

        response = pb2.FindMonitoringCompaniesResponse()
        response.failure.bad_request.SetInParent()
        api, stub = self._api_with(response)

        with (
            patch.dict(
                "sys.modules",
                {_MONITORING_GRPC: MagicMock(SpaceMonitoringCompanyServiceStub=lambda _c: stub)},
            ),
            pytest.raises(ValueError, match="findMonitoringCompanies"),
        ):
            await api.find_space_monitoring_companies("space-1", "AQ")


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
    async def test_records_the_members_push_preferences(self) -> None:
        """Ajax filters pushes per space member (#519), so the same fetch keeps them."""
        from systems.ajax.api.mobile.v2.common.space.member import (
            display_member_notification_preferences_pb2 as prefs_pb2,
        )
        from v3.mobilegwsvc.service.stream_lite_space_members import (
            endpoint_pb2_grpc as lite_grpc,
        )
        from v3.mobilegwsvc.service.stream_space_member import (
            endpoint_pb2_grpc as full_grpc,
        )

        api = self._make_api()
        lite = self._lite_response([("mid-1", "AAAA1111")])
        full = self._full_response([])
        prefs = full.success.snapshot.space_member.display_member_notification_preferences
        prefs.member_push_preferences.push_preferences.append(
            prefs_pb2.DISPLAY_SPACE_MEMBER_PUSH_PREFERENCE_ALARM
        )
        v2 = prefs.member_push_preferences_v2
        v2.alarm.video.state = prefs_pb2.DisplayMemberPushPreferencesV2.PUSH_PREFERENCE_STATE_NORMAL
        v2.video.human.enabled = True

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
            await api.get_member_space_permissions("space-1", "AAAA1111")

        assert api.member_push_preferences_lookup == {"space-1": "ok"}
        assert api.member_push_preferences == {
            "space-1": {
                "legacy": ["ALARM"],
                "alarm_video": "NORMAL",
                "video": {"motion": False, "human": True, "pet": False, "vehicle": False},
            }
        }

    async def _fetch(self, api: SpacesApi, full: object) -> set[str] | None:
        from v3.mobilegwsvc.service.stream_lite_space_members import (
            endpoint_pb2_grpc as lite_grpc,
        )
        from v3.mobilegwsvc.service.stream_space_member import (
            endpoint_pb2_grpc as full_grpc,
        )

        lite = self._lite_response([("mid-1", "AAAA1111")])

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
            return await api.get_member_space_permissions("space-1", "AAAA1111")

    @pytest.mark.asyncio
    async def test_unknown_preference_value_is_kept_as_its_number(self) -> None:
        """Ajax adds values our proto lacks (14 = line crossing, seen live on 1.23.0-beta.3)."""
        from systems.ajax.api.mobile.v2.common.space.member import space_permission_pb2 as sp

        api = self._make_api()
        full = self._full_response([sp.SpacePermission.DEVICE_EDIT])
        prefs = full.success.snapshot.space_member.display_member_notification_preferences
        prefs.member_push_preferences.push_preferences.extend([1, 14])
        prefs.member_push_preferences_v2.alarm.video.state = 9

        perms = await self._fetch(api, full)

        assert perms == {"DEVICE_EDIT"}
        summary = api.member_push_preferences["space-1"]
        assert summary["legacy"] == ["14", "ALARM"]
        assert summary["alarm_video"] == "9"

    @pytest.mark.asyncio
    async def test_a_failing_summary_never_costs_the_permissions(self) -> None:
        """The bypass lookup fails open on None, so the summary must not reach it."""
        from systems.ajax.api.mobile.v2.common.space.member import space_permission_pb2 as sp

        api = self._make_api()
        full = self._full_response([sp.SpacePermission.ARM])

        with patch(
            "custom_components.aegis_ajax.api.spaces._push_preferences_summary",
            side_effect=ValueError("boom"),
        ):
            perms = await self._fetch(api, full)

        assert perms == {"ARM"}
        assert api.member_push_preferences == {}

    @pytest.mark.asyncio
    async def test_absent_v2_preferences_read_as_none(self) -> None:
        """An unset message is not 'everything off': keep that distinguishable."""
        from v3.mobilegwsvc.service.stream_lite_space_members import (
            endpoint_pb2_grpc as lite_grpc,
        )
        from v3.mobilegwsvc.service.stream_space_member import (
            endpoint_pb2_grpc as full_grpc,
        )

        api = self._make_api()
        lite = self._lite_response([("mid-1", "AAAA1111")])
        full = self._full_response([])
        full.success.snapshot.space_member.id = "mid-1"

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
            await api.get_member_space_permissions("space-1", "AAAA1111")

        assert api.member_push_preferences == {
            "space-1": {"legacy": [], "alarm_video": None, "video": None}
        }

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
        assert api.member_push_preferences_lookup == {"space-1": "not_a_member"}

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self) -> None:
        api = self._make_api()
        api._client._get_channel.side_effect = RuntimeError("boom")

        perms = await api.get_member_space_permissions("space-1", "AAAA1111")

        assert perms is None
        assert api.member_push_preferences_lookup == {"space-1": "error:RuntimeError"}

    @pytest.mark.asyncio
    async def test_a_refused_members_list_is_recorded(self) -> None:
        """A non-admin account may not list members (#519): say so, don't go blank."""
        from v3.mobilegwsvc.service.stream_lite_space_members import (
            endpoint_pb2_grpc as lite_grpc,
        )
        from v3.mobilegwsvc.service.stream_lite_space_members import response_pb2 as r

        api = self._make_api()
        refused = r.StreamLiteSpaceMembersResponse()
        refused.failure.permission_denied.SetInParent()

        class _LiteStub:
            def __init__(self, ch: object) -> None: ...
            def execute(self, *a: object, **k: object) -> object:
                return TestGetMemberSpacePermissions._aiter([refused])

        with patch.object(lite_grpc, "StreamLiteSpaceMembersServiceStub", _LiteStub):
            perms = await api.get_member_space_permissions("space-1", "AAAA1111")

        assert perms is None
        assert api.member_push_preferences_lookup == {
            "space-1": "members_failure:permission_denied"
        }
