"""Spaces (hubs) API operations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from custom_components.aegis_ajax.api.models import (
    Group,
    MonitoringCompany,
    MonitoringCompanyStatus,
    Room,
    Space,
    SpaceSnapshot,
)
from custom_components.aegis_ajax.const import ConnectionStatus, SecurityState

# GroupSecurity.State proto enum:
#   GROUP_SECURITY_STATE_NONE = 0
#   GROUP_SECURITY_STATE_ARMED = 1
#   GROUP_SECURITY_STATE_DISARMED = 2
_GROUP_STATE_MAP: dict[int, SecurityState] = {
    0: SecurityState.NONE,
    1: SecurityState.ARMED,
    2: SecurityState.DISARMED,
}

if TYPE_CHECKING:
    from custom_components.aegis_ajax.api.client import AjaxGrpcClient

_LOGGER = logging.getLogger(__name__)

_FIND_SPACES_METHOD = (
    "/systems.ajax.api.ecosystem.v3.mobilegwsvc.service"
    ".find_user_spaces_with_pagination.FindUserSpacesWithPaginationService/execute"
)


class SpacesApi:
    """API operations for spaces (hubs)."""

    def __init__(self, client: AjaxGrpcClient) -> None:
        self._client = client

    @staticmethod
    def parse_space(proto_space: Any) -> Space:  # noqa: ANN401
        return Space(
            id=proto_space.id,
            hub_id=proto_space.hub_id if proto_space.hub_id else "",
            name=proto_space.profile.name,
            security_state=SecurityState(proto_space.security_state),
            connection_status=ConnectionStatus(proto_space.hub_connection_status),
            malfunctions_count=proto_space.malfunctions_count,
        )

    @staticmethod
    def parse_groups(proto_security: Any, space_id: str) -> tuple[tuple[Group, ...], bool]:  # noqa: ANN401
        """Extract groups + group-mode flag from a `SpaceSecurity` proto.

        Combines the group definitions in `security.groups[]` (id, name,
        sorting_key) with the per-group security states in
        `security.mode.group_mode.groups[]` keyed by `group_id`. When the
        space is in regular mode (no group_mode oneof), returns an empty
        tuple regardless of whether group definitions exist — the official
        Ajax UI does the same.
        """
        if not hasattr(proto_security, "groups") or not hasattr(proto_security, "mode"):
            return (), False
        mode = proto_security.mode
        group_mode_active = hasattr(mode, "WhichOneof") and mode.WhichOneof("mode") == "group_mode"
        if not group_mode_active:
            return (), False

        # Per-group state map keyed by group_id.
        states: dict[str, SecurityState] = {}
        if hasattr(mode, "group_mode") and hasattr(mode.group_mode, "groups"):
            for group_security in mode.group_mode.groups:
                gid = getattr(group_security, "group_id", "")
                if not gid:
                    continue
                proto_state = getattr(group_security, "state", 0)
                states[gid] = _GROUP_STATE_MAP.get(int(proto_state), SecurityState.NONE)

        groups: list[Group] = []
        for proto_group in proto_security.groups:
            gid = getattr(proto_group, "id", "")
            if not gid:
                continue
            groups.append(
                Group(
                    id=gid,
                    space_id=space_id,
                    name=getattr(proto_group, "name", "") or "",
                    security_state=states.get(gid, SecurityState.NONE),
                    sorting_key=getattr(proto_group, "sorting_key", "") or "",
                )
            )
        groups.sort(key=lambda g: (g.sorting_key, g.name))
        return tuple(groups), True

    @staticmethod
    def parse_monitoring_company(proto_company: Any) -> MonitoringCompany:  # noqa: ANN401
        name = ""
        hex_id = ""
        if hasattr(proto_company, "company_info"):
            company_info = proto_company.company_info
            if hasattr(company_info, "name"):
                raw_name = company_info.name
                if isinstance(raw_name, str):
                    name = raw_name
                elif hasattr(raw_name, "value") and isinstance(raw_name.value, str):
                    name = raw_name.value
            if hasattr(company_info, "hex_id") and isinstance(company_info.hex_id, str):
                hex_id = company_info.hex_id
        try:
            status = MonitoringCompanyStatus(proto_company.status)
        except ValueError:
            status = MonitoringCompanyStatus.UNSPECIFIED
        return MonitoringCompany(name=name, status=status, hex_id=hex_id)

    async def get_monitoring_company(
        self, space_id: str, company_hex_id: str
    ) -> MonitoringCompany | None:
        """Resolve a CRA company's full record by `(space_id, company_hex_id)`.

        Wraps `SpaceMonitoringCompanyService.getMonitoringCompany`. Returns
        a `MonitoringCompany` on success, `None` on a `failure` response or
        any RPC error — callers should be ready to treat the lookup as
        best-effort. Used as a fallback by `get_space_snapshot` when the
        stream-level snapshot only carries `hex_id` without a populated
        `name` (the modern client-version response shape).
        """
        from systems.ajax.api.mobile.v2.space.company.monitoring import (  # noqa: PLC0415
            get_monitoring_company_request_pb2,
            space_monitoring_company_endpoints_pb2_grpc,
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = space_monitoring_company_endpoints_pb2_grpc.SpaceMonitoringCompanyServiceStub(
            channel
        )
        request = get_monitoring_company_request_pb2.GetMonitoringCompanyRequest(
            company_hex_id=company_hex_id,
            space_id=space_id,
        )
        try:
            response = await stub.getMonitoringCompany(request, metadata=metadata, timeout=15)
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "getMonitoringCompany RPC failed for space %s hex_id %s",
                space_id,
                company_hex_id,
                exc_info=True,
            )
            return None
        which = response.WhichOneof("response") if hasattr(response, "WhichOneof") else None
        if which != "success":
            _LOGGER.debug(
                "getMonitoringCompany returned non-success (%s) for space %s hex_id %s",
                which,
                space_id,
                company_hex_id,
            )
            return None
        return self.parse_monitoring_company(response.success.company)

    async def list_spaces(self) -> list[Space]:
        from v3.mobilegwsvc.service.find_user_spaces_with_pagination import (  # noqa: PLC0415
            endpoint_pb2_grpc,
            request_pb2,
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = endpoint_pb2_grpc.FindUserSpacesWithPaginationServiceStub(channel)

        request = request_pb2.FindUserSpacesWithPaginationRequest(limit=100)
        response = await stub.execute(request, metadata=metadata, timeout=15)

        if response.HasField("failure"):
            _LOGGER.error("Failed to list spaces")
            return []

        return [self.parse_space(s) for s in response.success.spaces]

    async def get_space_snapshot(self, space_id: str) -> SpaceSnapshot:
        """Return a subset of the full space snapshot.

        Reads the snapshot message from `SpaceService/stream` and closes
        the stream — rooms and monitoring-company metadata rarely change so
        we don't keep it open.
        """
        from systems.ajax.api.mobile.v2.common.space import (  # noqa: PLC0415
            space_locator_pb2,
        )
        from systems.ajax.api.mobile.v2.space import (  # noqa: PLC0415
            space_endpoints_pb2_grpc,
            stream_space_updates_request_pb2,
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = space_endpoints_pb2_grpc.SpaceServiceStub(channel)

        request = stream_space_updates_request_pb2.StreamSpaceUpdatesRequest(
            space_locator=space_locator_pb2.SpaceLocator(space_id=space_id),
        )
        stream = stub.stream(request, metadata=metadata, timeout=15)

        rooms: list[Room] = []
        monitoring_companies: list[MonitoringCompany] = []
        groups: tuple[Group, ...] = ()
        group_mode_enabled: bool = False
        try:
            async for msg in stream:
                if msg.HasField("failure"):
                    _LOGGER.debug("Failed to stream space %s for rooms snapshot", space_id)
                    break
                if not msg.HasField("success"):
                    continue
                if msg.success.WhichOneof("success") != "snapshot":
                    continue
                snapshot = msg.success.snapshot
                for proto_room in snapshot.rooms:
                    rooms.append(Room(id=proto_room.id, name=proto_room.name, space_id=space_id))
                for proto_company in snapshot.monitoring_companies:
                    monitoring_companies.append(self.parse_monitoring_company(proto_company))
                if hasattr(snapshot, "security"):
                    groups, group_mode_enabled = self.parse_groups(snapshot.security, space_id)
                break
        finally:
            cancel = getattr(stream, "cancel", None)
            if callable(cancel):
                cancel()

        # Best-effort name resolution for companies that arrived with an
        # empty name field but a usable hex_id — happens on modern client
        # versions where the legacy `monitoring_companies` slot ships the
        # tuple `(hex_id, status)` without an attached name. Falls back to
        # the original record on any RPC failure so the snapshot still
        # surfaces the company by its known status.
        monitoring_companies = [
            await self._resolve_company_name(space_id, company) for company in monitoring_companies
        ]

        return SpaceSnapshot(
            rooms=tuple(rooms),
            monitoring_companies=tuple(monitoring_companies),
            monitoring_companies_loaded=True,
            groups=groups,
            group_mode_enabled=group_mode_enabled,
        )

    async def _resolve_company_name(
        self, space_id: str, company: MonitoringCompany
    ) -> MonitoringCompany:
        """Fill in `company.name` via `getMonitoringCompany` when missing.

        No-op when `name` is already populated or `hex_id` is empty. Returns
        the original `company` if the resolver returns `None` (RPC failure
        or backend `failure` response) so callers always have a usable
        record.
        """
        if company.name or not company.hex_id:
            return company
        resolved = await self.get_monitoring_company(space_id, company.hex_id)
        if resolved is None or not resolved.name:
            return company
        # Trust the resolver's name; keep the snapshot's status (the stream
        # is the authoritative state source, the lookup may return cached
        # data on the company-tenancy side).
        from dataclasses import replace as dc_replace  # noqa: PLC0415

        return dc_replace(company, name=resolved.name)

    async def list_rooms(self, space_id: str) -> list[Room]:
        """Return the rooms defined in the given space."""
        snapshot = await self.get_space_snapshot(space_id)
        return list(snapshot.rooms)

    async def press_panic_button(
        self,
        space_id: str,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> None:
        """Trigger the Ajax panic button (SOS) on a space.

        Calls `SpaceService/pressPanicButton` — the same endpoint the official
        Ajax mobile app hits when the user taps the red SOS button on the
        space view.

        Effects on the Ajax side (controlled by hub configuration):
        - Always fires regardless of the space's armed/disarmed state.
        - Triggers a `panic_button_pressed` event (mapped to event_type
          `panic` in this integration's event entity).
        - Forwards a Panic / Hold-up alarm to the monitoring station (CRA),
          which on most contracts results in immediate police dispatch with
          NO verification window.
        - Optionally activates sirens depending on the hub's
          `panic_siren_on_panic_button` setting.

        Because of the irreversible CRA dispatch, callers MUST treat this as
        a deliberate action — never wire it to noisy automations.

        Args:
            space_id: Target space (hub) identifier.
            latitude / longitude: Optional GPS coordinates of the caller. The
                Ajax cloud forwards these to monitoring services where
                supported. Both must be provided together.

        Raises:
            RuntimeError: When the server reports a failure (permission
                denied, hub not allowed to perform command, etc.). The
                message includes the specific error case so the caller can
                surface it to the user.
        """
        from systems.ajax.api.mobile.v2.common.space import (  # noqa: PLC0415
            space_locator_pb2,
        )
        from systems.ajax.api.mobile.v2.space import (  # noqa: PLC0415
            press_panic_button_request_pb2,
            space_endpoints_pb2_grpc,
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = space_endpoints_pb2_grpc.SpaceServiceStub(channel)

        request = press_panic_button_request_pb2.PressPanicButtonRequest(
            space_locator=space_locator_pb2.SpaceLocator(space_id=space_id),
        )
        if latitude is not None and longitude is not None:
            request.location.latitude = float(latitude)
            request.location.longitude = float(longitude)

        response = await stub.pressPanicButton(request, metadata=metadata, timeout=15)

        if response.HasField("failure"):
            error = response.failure.WhichOneof("error") or "unknown"
            raise RuntimeError(f"Panic button request rejected by Ajax: {error}")

    async def get_member_space_permissions(
        self, space_id: str, user_hex_id: str
    ) -> set[str] | None:
        """Return the current user's space-permission names, or None.

        Read-only. Lists lite members to resolve the user's member id (matched
        by `hex_id`), then fetches the full SpaceMember and returns its
        `space_permissions.permissions` as enum-name strings (e.g.
        `{"ARM", "DISARM", "DEVICE_EDIT"}`). Returns None when the user can't be
        matched, the server denies the members call, or anything goes wrong —
        callers treat None as "unknown / can't determine". (#bypass auto)
        """
        from systems.ajax.api.mobile.v2.common.space.member import (  # noqa: PLC0415
            space_permission_pb2,
        )
        from v3.mobilegwsvc.service.stream_lite_space_members import (  # noqa: PLC0415
            endpoint_pb2_grpc as lite_grpc,
        )
        from v3.mobilegwsvc.service.stream_lite_space_members import (
            request_pb2 as lite_req,
        )
        from v3.mobilegwsvc.service.stream_space_member import (  # noqa: PLC0415
            endpoint_pb2_grpc as full_grpc,
        )
        from v3.mobilegwsvc.service.stream_space_member import (
            request_pb2 as full_req,
        )

        try:
            channel = self._client._get_channel()
            metadata = self._client._session.get_call_metadata()

            member_id: str | None = None
            lite_stub = lite_grpc.StreamLiteSpaceMembersServiceStub(channel)
            async for msg in lite_stub.execute(
                lite_req.StreamLiteSpaceMembersRequest(space_id=space_id),
                metadata=metadata,
                timeout=15,
            ):
                if msg.HasField("success") and msg.success.HasField("snapshot"):
                    for member in msg.success.snapshot.lite_space_members.lite_space_members:
                        if member.hex_id == user_hex_id:
                            member_id = member.id
                            break
                    break
                if msg.HasField("failure"):
                    return None
            if not member_id:
                return None

            perm_names = {
                v.number: v.name for v in space_permission_pb2.SpacePermission.DESCRIPTOR.values
            }
            full_stub = full_grpc.StreamSpaceMemberServiceStub(channel)
            async for msg in full_stub.execute(
                full_req.StreamSpaceMemberRequest(space_id=space_id, space_member_id=member_id),
                metadata=metadata,
                timeout=15,
            ):
                if msg.HasField("success") and msg.success.HasField("snapshot"):
                    member = msg.success.snapshot.space_member
                    return {perm_names.get(p, str(p)) for p in member.space_permissions.permissions}
                if msg.HasField("failure"):
                    return None
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "Could not fetch member permissions for space %s", space_id, exc_info=True
            )
            return None
        return None
