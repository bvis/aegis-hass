"""Security API operations (arm/disarm)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from custom_components.aegis_ajax.api.client import AjaxGrpcClient

_LOGGER = logging.getLogger(__name__)

_SERVICE_PREFIX = "/systems.ajax.api.mobile.v2.space.security.SpaceSecurityService"
_ARM_METHOD = f"{_SERVICE_PREFIX}/arm"
_DISARM_METHOD = f"{_SERVICE_PREFIX}/disarm"
_ARM_NIGHT_METHOD = f"{_SERVICE_PREFIX}/armToNightMode"
_DISARM_NIGHT_METHOD = f"{_SERVICE_PREFIX}/disarmFromNightMode"
_ARM_GROUP_METHOD = f"{_SERVICE_PREFIX}/armGroup"
_DISARM_GROUP_METHOD = f"{_SERVICE_PREFIX}/disarmGroup"


class SecurityError(Exception):
    """Raised when a security command fails."""


class SecurityApi:
    """API operations for arming/disarming."""

    def __init__(self, client: AjaxGrpcClient) -> None:
        self._client = client

    async def arm(self, space_id: str, ignore_alarms: bool = False) -> None:
        from systems.ajax.api.mobile.v2.common.space import space_locator_pb2  # noqa: PLC0415
        from systems.ajax.api.mobile.v2.space.security import (  # noqa: PLC0415
            arm_request_pb2,
            space_security_endpoints_pb2_grpc,
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = space_security_endpoints_pb2_grpc.SpaceSecurityServiceStub(channel)

        request = arm_request_pb2.ArmSpaceRequest(
            space_locator=space_locator_pb2.SpaceLocator(space_id=space_id),
            ignore_alarms=ignore_alarms,
        )
        response = await stub.arm(request, metadata=metadata, timeout=15)
        if response.HasField("failure"):
            error_type = response.failure.WhichOneof("error")
            if error_type == "already_in_the_requested_security_state":
                _LOGGER.debug("Space %s already armed", space_id)
                return
            raise SecurityError(error_type)
        _LOGGER.debug("Armed space %s", space_id)

    async def disarm(self, space_id: str) -> None:

        import asyncio  # noqa: PLC0415

        from systems.ajax.api.mobile.v2.common.space import space_locator_pb2  # noqa: PLC0415
        from systems.ajax.api.mobile.v2.space.security import (  # noqa: PLC0415
            disarm_request_pb2,
            space_security_endpoints_pb2_grpc,
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = space_security_endpoints_pb2_grpc.SpaceSecurityServiceStub(channel)

        request = disarm_request_pb2.DisarmSpaceRequest(
            space_locator=space_locator_pb2.SpaceLocator(space_id=space_id),
        )

        # Retry on transient errors (hub busy during alarm processing)
        for attempt in range(3):
            response = await stub.disarm(request, metadata=metadata, timeout=15)
            if not response.HasField("failure"):
                _LOGGER.debug("Disarmed space %s", space_id)
                return
            error_type = response.failure.WhichOneof("error")
            if error_type == "already_in_the_requested_security_state":
                _LOGGER.debug("Space %s already disarmed", space_id)
                return
            if error_type in ("hub_busy", "another_transition_is_in_progress") and attempt < 2:
                _LOGGER.debug("Disarm: %s, retrying in 2s (attempt %d)", error_type, attempt + 1)
                await asyncio.sleep(2)
                continue
            raise SecurityError(error_type)

    async def arm_night_mode(self, space_id: str, ignore_alarms: bool = False) -> None:

        from systems.ajax.api.mobile.v2.common.space import space_locator_pb2  # noqa: PLC0415
        from systems.ajax.api.mobile.v2.space.security import (  # noqa: PLC0415
            arm_to_night_mode_request_pb2,
            space_security_endpoints_pb2_grpc,
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = space_security_endpoints_pb2_grpc.SpaceSecurityServiceStub(channel)

        request = arm_to_night_mode_request_pb2.ArmSpaceToNightModeRequest(
            space_locator=space_locator_pb2.SpaceLocator(space_id=space_id),
            ignore_alarms=ignore_alarms,
        )
        response = await stub.armToNightMode(request, metadata=metadata, timeout=15)
        if response.HasField("failure"):
            error_type = response.failure.WhichOneof("error")
            if error_type == "already_in_the_requested_security_state":
                _LOGGER.debug("Space %s already in night mode", space_id)
                return
            raise SecurityError(error_type)
        _LOGGER.debug("Armed space %s in night mode", space_id)

    async def disarm_from_night_mode(self, space_id: str) -> None:

        from systems.ajax.api.mobile.v2.common.space import space_locator_pb2  # noqa: PLC0415
        from systems.ajax.api.mobile.v2.space.security import (  # noqa: PLC0415
            disarm_from_night_mode_request_pb2,
            space_security_endpoints_pb2_grpc,
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = space_security_endpoints_pb2_grpc.SpaceSecurityServiceStub(channel)

        request = disarm_from_night_mode_request_pb2.DisarmSpaceFromNightModeRequest(
            space_locator=space_locator_pb2.SpaceLocator(space_id=space_id),
        )
        response = await stub.disarmFromNightMode(request, metadata=metadata, timeout=15)
        if response.HasField("failure"):
            raise SecurityError(response.failure.WhichOneof("error") or "disarm_rejected")
        _LOGGER.debug("Disarmed space %s from night mode", space_id)

    async def arm_group(self, space_id: str, group_id: str, ignore_alarms: bool = False) -> None:

        from systems.ajax.api.mobile.v2.common.space import space_locator_pb2  # noqa: PLC0415
        from systems.ajax.api.mobile.v2.space.security import (  # noqa: PLC0415
            space_security_endpoints_pb2_grpc,
        )
        from systems.ajax.api.mobile.v2.space.security.group import (  # noqa: PLC0415
            arm_group_request_pb2,
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = space_security_endpoints_pb2_grpc.SpaceSecurityServiceStub(channel)

        request = arm_group_request_pb2.ArmSpaceGroupRequest(
            space_locator=space_locator_pb2.SpaceLocator(space_id=space_id),
            group_id=group_id,
            ignore_alarms=ignore_alarms,
        )
        response = await stub.armGroup(request, metadata=metadata, timeout=15)
        if response.HasField("failure"):
            raise SecurityError(response.failure.WhichOneof("error") or "arm_group_rejected")
        _LOGGER.debug("Armed group %s in space %s", group_id, space_id)

    async def disarm_group(self, space_id: str, group_id: str) -> None:

        from systems.ajax.api.mobile.v2.common.space import space_locator_pb2  # noqa: PLC0415
        from systems.ajax.api.mobile.v2.space.security import (  # noqa: PLC0415
            space_security_endpoints_pb2_grpc,
        )
        from systems.ajax.api.mobile.v2.space.security.group import (  # noqa: PLC0415
            disarm_group_request_pb2,
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = space_security_endpoints_pb2_grpc.SpaceSecurityServiceStub(channel)

        request = disarm_group_request_pb2.DisarmSpaceGroupRequest(
            space_locator=space_locator_pb2.SpaceLocator(space_id=space_id),
            group_id=group_id,
        )
        response = await stub.disarmGroup(request, metadata=metadata, timeout=15)
        if response.HasField("failure"):
            raise SecurityError(response.failure.WhichOneof("error") or "disarm_group_rejected")
        _LOGGER.debug("Disarmed group %s in space %s", group_id, space_id)
