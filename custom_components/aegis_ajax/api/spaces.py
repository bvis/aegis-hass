"""Spaces (hubs) API operations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from custom_components.aegis_ajax.api.models import (
    Group,
    MonitoringCompany,
    MonitoringCompanyStatus,
    Room,
    Space,
    SpaceSnapshot,
)
from custom_components.aegis_ajax.const import ChimeStatus, ConnectionStatus, SecurityState

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


class ParsedGroups(NamedTuple):
    """Result of `SpacesApi.parse_groups` over a `SpaceSecurity` proto."""

    groups: tuple[Group, ...]
    group_mode_enabled: bool
    night_mode_enabled: bool


class SpacesApi:
    """API operations for spaces (hubs)."""

    def __init__(self, client: AjaxGrpcClient) -> None:
        self._client = client
        # The logged-in member's push preferences per space, kept from the
        # permission lookup (#519). Ajax filters pushes per member, so a
        # dedicated Home Assistant account can have video pushes off while the
        # owner's phone gets them. Only filled when that lookup runs
        # (bypass switches on `auto`); no call is made for it.
        self.member_push_preferences: dict[str, dict[str, Any]] = {}
        # How that lookup ended per space, so an empty section says why.
        self.member_push_preferences_lookup: dict[str, str] = {}

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
    def extract_chime_status(proto_space: Any) -> ChimeStatus:  # noqa: ANN401
        """Pull the hub-wide Chime status off the full space snapshot (#239).

        Mirrors the official app's `tqs.a(space)`: walk `space.devices`, take
        the `StandaloneDevice` whose `device` oneof is `hub`, and read its
        `chime_status`. Only the heavy `get_space_snapshot` carries the full
        `Space` (with `devices`); the lighter `list_spaces` returns a
        `LiteSpace` that has no devices, so Chime state rides the same hourly
        snapshot path as rooms/groups. Defensive on every step — no hub
        device, an unrecognised enum value, or a snapshot without `devices`
        all yield `UNSPECIFIED` (no Chime switch), never an error that would
        blank the whole snapshot.
        """
        try:
            for device in proto_space.devices:
                which = device.WhichOneof("device") if hasattr(device, "WhichOneof") else None
                if which == "hub":
                    return ChimeStatus(int(device.hub.chime_status))
        except (TypeError, ValueError, AttributeError):
            pass
        return ChimeStatus.UNSPECIFIED

    @staticmethod
    def parse_groups(proto_security: Any, space_id: str) -> ParsedGroups:  # noqa: ANN401
        """Extract groups + group-mode + night-mode flags from a `SpaceSecurity` proto.

        Combines the group definitions in `security.groups[]` (id, name,
        sorting_key) with the per-group security states in
        `security.mode.group_mode.groups[]` keyed by `group_id`. When the
        space is in regular mode (no group_mode oneof), returns an empty
        tuple regardless of whether group definitions exist — the official
        Ajax UI does the same.

        `night_mode_enabled` is `mode.group_mode.night_mode_enabled`: whether
        night mode is currently active. It matters because in group mode the
        lite `DisplayedSpaceSecurityState` reports PARTIALLY_ARMED while night
        mode is on, and this flag is the only wire signal that distinguishes
        that from "some groups armed" (#284).
        """
        if not hasattr(proto_security, "groups") or not hasattr(proto_security, "mode"):
            return ParsedGroups((), False, False)
        mode = proto_security.mode
        group_mode_active = hasattr(mode, "WhichOneof") and mode.WhichOneof("mode") == "group_mode"
        if not group_mode_active:
            return ParsedGroups((), False, False)

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
        night_mode_enabled = bool(getattr(mode.group_mode, "night_mode_enabled", False))
        return ParsedGroups(tuple(groups), True, night_mode_enabled)

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

    async def find_space_monitoring_companies(
        self, space_id: str, country_code: str
    ) -> list[MonitoringCompany]:
        """Return the CRA companies attached to a space (#561).

        `SpaceMonitoringCompanyService.findMonitoringCompanies` answers with
        two blocks: the space's own companies, and every company available in
        `country_code` (the app's sign-up list). Only the first is used; the
        country just sizes the second (about 0.5 KB with "AQ", 30 KB with
        "ES", 750 KB with no country). Errors propagate to the caller.
        """
        from systems.ajax.api.mobile.v2.space.company.monitoring import (  # noqa: PLC0415
            find_monitoring_companies_request_pb2,
            space_monitoring_company_endpoints_pb2_grpc,
        )

        stub = space_monitoring_company_endpoints_pb2_grpc.SpaceMonitoringCompanyServiceStub(
            self._client._get_channel()
        )
        request = find_monitoring_companies_request_pb2.FindMonitoringCompaniesRequest(
            space_id=space_id, company_country_code=country_code
        )
        response = await stub.findMonitoringCompanies(
            request, metadata=self._client._session.get_call_metadata(), timeout=15
        )
        if response.WhichOneof("response") != "success":
            raise ValueError(f"findMonitoringCompanies failed for space {space_id}")
        block = response.success.space_monitoring_companies_block
        return [self.parse_monitoring_company(company) for company in block.companies]

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
        the stream — rooms and groups rarely change so we don't keep it
        open. CRA companies no longer ride here: from client version 3.57
        Ajax leaves `monitoring_companies` empty (#561), see
        `find_space_monitoring_companies`.
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
        groups: tuple[Group, ...] = ()
        group_mode_enabled: bool = False
        night_mode_enabled: bool = False
        chime_status = ChimeStatus.UNSPECIFIED
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
                if hasattr(snapshot, "security"):
                    groups, group_mode_enabled, night_mode_enabled = self.parse_groups(
                        snapshot.security, space_id
                    )
                chime_status = self.extract_chime_status(snapshot)
                break
        finally:
            cancel = getattr(stream, "cancel", None)
            if callable(cancel):
                cancel()

        return SpaceSnapshot(
            rooms=tuple(rooms),
            groups=groups,
            group_mode_enabled=group_mode_enabled,
            night_mode_enabled=night_mode_enabled,
            chime_status=chime_status,
        )

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
                    reason = msg.failure.WhichOneof("error") or "failure"
                    self.member_push_preferences_lookup[space_id] = f"members_failure:{reason}"
                    return None
            if not member_id:
                self.member_push_preferences_lookup[space_id] = "not_a_member"
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
                    # Diagnostics only: a failure here must never cost the
                    # permissions, since the bypass lookup fails open on None.
                    try:
                        self.member_push_preferences[space_id] = _push_preferences_summary(
                            member.display_member_notification_preferences
                        )
                        self.member_push_preferences_lookup[space_id] = "ok"
                    except Exception as exc:  # noqa: BLE001
                        self.member_push_preferences_lookup[space_id] = (
                            f"summary_error:{type(exc).__name__}"
                        )
                        _LOGGER.debug("Could not read push preferences", exc_info=True)
                    return {perm_names.get(p, str(p)) for p in member.space_permissions.permissions}
                if msg.HasField("failure"):
                    reason = msg.failure.WhichOneof("error") or "failure"
                    self.member_push_preferences_lookup[space_id] = f"member_failure:{reason}"
                    return None
        except Exception as exc:  # noqa: BLE001
            self.member_push_preferences_lookup[space_id] = f"error:{type(exc).__name__}"
            _LOGGER.debug(
                "Could not fetch member permissions for space %s", space_id, exc_info=True
            )
            return None
        self.member_push_preferences_lookup[space_id] = "no_snapshot"
        return None


def _push_preferences_summary(prefs: Any) -> dict[str, Any]:  # noqa: ANN401
    """Reduce a member's notification preferences to what gates video pushes.

    `None` means Ajax did not send that part, which is not the same as off.
    """
    from systems.ajax.api.mobile.v2.common.space.member import (  # noqa: PLC0415
        display_member_notification_preferences_pb2 as prefs_pb2,
    )

    def name(enum: Any, value: int, prefix: str) -> str:  # noqa: ANN401
        # Ajax adds values our proto doesn't have yet (14 = line crossing).
        known = enum.DESCRIPTOR.values_by_number.get(value)
        return known.name.removeprefix(prefix) if known else str(value)

    legacy = sorted(
        name(prefs_pb2.DisplayMemberPushPreference, p, "DISPLAY_SPACE_MEMBER_PUSH_PREFERENCE_")
        for p in prefs.member_push_preferences.push_preferences
    )
    v2 = prefs.member_push_preferences_v2
    alarm_video = None
    if v2.HasField("alarm") and v2.alarm.HasField("video"):
        alarm_video = name(
            prefs_pb2.DisplayMemberPushPreferencesV2.PushPreferenceState,
            v2.alarm.video.state,
            "PUSH_PREFERENCE_STATE_",
        )
    video = None
    if v2.HasField("video"):
        video = {
            kind: getattr(v2.video, kind).enabled for kind in ("motion", "human", "pet", "vehicle")
        }
    return {"legacy": legacy, "alarm_video": alarm_video, "video": video}
