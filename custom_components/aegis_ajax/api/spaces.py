"""Spaces (hubs) API operations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from custom_components.aegis_ajax.api.models import Room, Space
from custom_components.aegis_ajax.const import ConnectionStatus, SecurityState

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

    async def list_rooms(self, space_id: str) -> list[Room]:
        """Return the rooms defined in the given space.

        Reads the snapshot message from `SpaceService/stream` and closes
        the stream — rooms rarely change so we don't keep it open.
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
        try:
            async for msg in stream:
                if msg.HasField("failure"):
                    _LOGGER.debug("Failed to stream space %s for rooms snapshot", space_id)
                    break
                if not msg.HasField("success"):
                    continue
                if msg.success.WhichOneof("success") != "snapshot":
                    continue
                for proto_room in msg.success.snapshot.rooms:
                    rooms.append(Room(id=proto_room.id, name=proto_room.name, space_id=space_id))
                break
        finally:
            cancel = getattr(stream, "cancel", None)
            if callable(cancel):
                cancel()

        return rooms

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
