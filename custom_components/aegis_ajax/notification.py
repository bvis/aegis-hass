"""FCM push notification listener for Ajax Security."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from homeassistant.helpers.storage import Store

from custom_components.aegis_ajax.const import (
    DOMAIN,
    HUB_EVENT_TAG_MAP,
    RAW_TAG_TO_SECURITY_STATE,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}_fcm_credentials"
STORAGE_VERSION = 1


class AjaxNotificationListener:
    """Manages FCM push notification registration and listening."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: AjaxCobrandedCoordinator,
        *,
        fcm_project_id: str,
        fcm_app_id: str,
        fcm_api_key: str,
        fcm_sender_id: str,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._fcm_project_id = fcm_project_id
        self._fcm_app_id = fcm_app_id
        self._fcm_api_key = fcm_api_key
        self._fcm_sender_id = fcm_sender_id
        self._push_client: Any = None
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._credentials: dict[str, Any] | None = None
        self._photo_callbacks: dict[str, asyncio.Future[str | None]] = {}
        self._notification_id_callbacks: dict[str, asyncio.Future[str | None]] = {}
        self._last_notification_id: str | None = None

    async def async_start(self) -> None:
        """Register with FCM and start listening for push notifications."""
        if not self._fcm_api_key:
            _LOGGER.debug("FCM credentials not configured, push notifications disabled")
            return

        try:
            from firebase_messaging import FcmPushClient  # noqa: PLC0415
            from firebase_messaging.fcmregister import (  # noqa: PLC0415
                FcmRegister,
                FcmRegisterConfig,
            )
        except ImportError:
            _LOGGER.debug("firebase_messaging not installed, push notifications disabled")
            return

        # Load or create FCM credentials
        stored = await self._store.async_load()
        self._credentials = dict(stored) if stored else None

        fcm_config = FcmRegisterConfig(
            project_id=self._fcm_project_id,
            app_id=self._fcm_app_id,
            api_key=self._fcm_api_key,
            messaging_sender_id=self._fcm_sender_id,
        )

        if not self._credentials:
            _LOGGER.debug("Registering with FCM...")
            try:
                registerer = FcmRegister(config=fcm_config)
                # register() may be sync or async depending on library version
                if asyncio.iscoroutinefunction(registerer.register):
                    raw_result: Any = await registerer.register()  # noqa: ANN401
                else:
                    raw_result = await self._hass.async_add_executor_job(registerer.register)
                self._credentials = dict(raw_result)
                await self._store.async_save(self._credentials)
                _LOGGER.debug("FCM registration successful")
            except Exception:
                _LOGGER.exception("FCM registration failed")
                return

        # Extract FCM token and register with Ajax servers
        fcm_data = self._credentials.get("fcm", {})
        registration = fcm_data.get("registration", {}) if isinstance(fcm_data, dict) else {}
        fcm_token = registration.get("token") if isinstance(registration, dict) else None
        if fcm_token:
            _LOGGER.debug("FCM token obtained, registering with Ajax servers")
            await self._register_push_token(str(fcm_token))
        else:
            _LOGGER.debug("No FCM token found in credentials")

        # Start push client
        try:
            self._push_client = FcmPushClient(
                callback=self._on_notification,
                fcm_config=fcm_config,
                credentials=self._credentials,
            )
            if asyncio.iscoroutinefunction(self._push_client.start):
                await self._push_client.start()
            else:
                await self._hass.async_add_executor_job(self._push_client.start)
            _LOGGER.debug("FCM push client started for Ajax")
        except Exception:
            _LOGGER.exception("Failed to start FCM push client")

    async def _register_push_token(self, fcm_token: str) -> None:
        """Register the FCM token with Ajax servers via gRPC."""
        try:
            from v3.mobilegwsvc.commonmodels.type import user_role_pb2  # noqa: PLC0415
            from v3.mobilegwsvc.service.upsert_push_token import (  # noqa: PLC0415
                endpoint_pb2_grpc,
                request_pb2,
            )

            client = self._coordinator._client
            channel = client._get_channel()
            metadata = client._session.get_call_metadata()

            stub = endpoint_pb2_grpc.UpsertPushTokenServiceStub(channel)
            request = request_pb2.UpsertPushTokenRequest(
                user_hex_id=client.session.user_hex_id or "",
                user_role=user_role_pb2.USER_ROLE_USER,
                push_token=fcm_token,
                push_token_type=5,  # PUSH_TOKEN_TYPE_AOS_FCM
            )

            response = await stub.execute(request, metadata=metadata, timeout=15)
            if response.HasField("success"):
                _LOGGER.debug("Push token registered with Ajax servers")
            else:
                _LOGGER.debug("Failed to register push token with Ajax servers")
        except Exception:
            _LOGGER.exception("Error registering push token")

    def _on_notification(
        self,
        notification: dict[str, Any],
        persistent_id: str,
        obj: object = None,  # noqa: ARG002
    ) -> None:
        """Handle incoming FCM push notification."""
        _LOGGER.debug("Push notification received: persistent_id=%s", persistent_id)

        # Try to extract photo URL from push data
        # The key might be "ENCODED_DATA" (top-level) or nested inside "data"
        encoded_data = notification.get("ENCODED_DATA")
        if not encoded_data:
            data_field = notification.get("data")
            if isinstance(data_field, dict):
                encoded_data = data_field.get("ENCODED_DATA")
            elif isinstance(data_field, str):
                encoded_data = data_field
        if encoded_data:
            try:
                raw = base64.b64decode(encoded_data)
                # Search for HTTPS URLs in the decoded protobuf
                urls = re.findall(rb'https://[^\x00-\x1f\x7f-\x9f"\'\\]+', raw)
                for raw_url in urls:
                    photo_url = raw_url.decode("utf-8", errors="ignore")
                    parsed = urlparse(photo_url)
                    is_ajax = parsed.hostname and parsed.hostname.endswith(".ajax.systems")
                    is_s3 = parsed.hostname and "hubs-uploaded-resources" in parsed.hostname
                    if not is_ajax and not is_s3:
                        _LOGGER.debug("Rejected photo URL from unexpected domain")
                        continue
                    _LOGGER.debug("Extracted photo URL from push: %s", photo_url[:60])
                    # Resolve the photo future for the matching device
                    resolved = False
                    for device_id, future in list(self._photo_callbacks.items()):
                        if not future.done() and device_id.upper() in photo_url.upper():
                            future.set_result(photo_url)
                            self._photo_callbacks.pop(device_id, None)
                            resolved = True
                            break
                    if not resolved:
                        # Fallback: resolve first pending (single-device case)
                        for device_id, future in list(self._photo_callbacks.items()):
                            if not future.done():
                                future.set_result(photo_url)
                                self._photo_callbacks.pop(device_id, None)
                                break
                    break
            except Exception:
                _LOGGER.debug("Failed to parse ENCODED_DATA from push")

        # Extract notification_id for photo URL retrieval
        if encoded_data:
            notif_id = self.extract_notification_id(encoded_data)
            if notif_id:
                self._last_notification_id = notif_id
                _LOGGER.debug("Extracted notification_id: %s", notif_id[:20])
                # Resolve the future for the matching device_id
                # notification_id contains the device_id (e.g., ...A1B2C3D4...)
                for device_id, future in list(self._notification_id_callbacks.items()):
                    if not future.done() and device_id.upper() in notif_id.upper():
                        future.set_result(notif_id)
                        self._notification_id_callbacks.pop(device_id, None)
                        _LOGGER.debug("Resolved notification_id for device %s", device_id)
                        break

        # Parse event from ENCODED_DATA using compiled protos
        if encoded_data:
            self._parse_and_fire_event(encoded_data)

        # Always trigger refresh
        if self._hass.loop and self._hass.loop.is_running():
            self._hass.loop.call_soon_threadsafe(
                self._hass.async_create_task,
                self._coordinator.async_request_refresh(),
            )

    async def wait_for_photo_url(self, device_id: str, timeout: float = 15.0) -> str | None:
        """Wait for a photo URL to arrive via push notification."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str | None] = loop.create_future()
        self._photo_callbacks[device_id] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            _LOGGER.debug("Timeout waiting for photo URL from push")
            return None
        finally:
            self._photo_callbacks.pop(device_id, None)

    async def wait_for_notification_id(self, device_id: str, timeout: float = 15.0) -> str | None:
        """Wait for a notification_id to arrive via push notification after photo capture."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str | None] = loop.create_future()
        self._notification_id_callbacks[device_id] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            _LOGGER.debug("Timeout waiting for notification_id from push")
            return None
        finally:
            self._notification_id_callbacks.pop(device_id, None)

    @staticmethod
    def extract_notification_id(encoded_data: str) -> str | None:
        """Extract notification_id from base64-encoded push notification data."""
        try:
            raw = base64.b64decode(encoded_data)
            # PushNotificationDispatchEvent field 1 (Notification) is at tag 0x0a
            # Inside Notification, field 1 (id) is also tag 0x0a
            # We look for a 64-char hex string which is the notification ID format
            matches = re.findall(rb"[0-9A-Fa-f]{64}", raw)
            if matches:
                result: str = matches[0].decode("ascii")
                return result
        except Exception:
            _LOGGER.debug("Failed to extract notification_id from push")
        return None

    def _parse_and_fire_event(self, encoded_data: str) -> None:
        """Parse event from base64-encoded push notification data."""
        try:
            raw = base64.b64decode(encoded_data)
            event_info = self._extract_event_from_proto(raw)
            if event_info:
                event_type, event_data = event_info
                # Enrich with source device info (name, room, type)
                source_info = self._extract_source_info(raw)
                if source_info:
                    event_data.update(source_info)
                # Try to route to the correct space by matching hub_id from raw bytes
                target_space = self._find_space_for_event(raw)
                if target_space:
                    self._coordinator.fire_push_event(target_space, event_type, event_data)
                    self._apply_security_state_from_event(target_space, event_data)
                else:
                    # Fallback: single-space installations or unknown hub
                    for space_id in self._coordinator._space_ids:
                        self._coordinator.fire_push_event(space_id, event_type, event_data)
                        self._apply_security_state_from_event(space_id, event_data)
        except Exception:
            _LOGGER.debug("Failed to parse event from push notification", exc_info=True)

    def _apply_security_state_from_event(self, space_id: str, event_data: dict[str, Any]) -> None:
        """If the push event implies a new space security_state, push it now (#68).

        The FCM callback runs on the firebase_messaging worker thread, so we
        dispatch the update to the HA event loop via call_soon_threadsafe.
        """
        raw_tag = event_data.get("raw_tag")
        if not isinstance(raw_tag, str):
            return
        new_state = RAW_TAG_TO_SECURITY_STATE.get(raw_tag)
        if new_state is None:
            return
        if self._hass.loop and self._hass.loop.is_running():
            self._hass.loop.call_soon_threadsafe(
                self._coordinator.apply_push_security_state,
                space_id,
                new_state,
            )

    def _find_space_for_event(self, raw: bytes) -> str | None:
        """Try to match the event to a space by finding a known hub_id in raw bytes."""
        for space in self._coordinator.spaces.values():
            if space.hub_id:
                hub_bytes = bytes.fromhex(space.hub_id)
                if hub_bytes in raw:
                    return space.id
        return None

    def _extract_event_from_proto(self, raw: bytes) -> tuple[str, dict[str, Any]] | None:
        """Extract event type and data from raw protobuf bytes.

        Attempts to decode using compiled protos. Falls back to raw parsing
        if proto imports fail.
        """
        try:
            return self._extract_event_with_compiled_protos(raw)
        except Exception:
            _LOGGER.debug("Compiled proto parsing failed, trying raw extraction")
            return self._extract_event_raw(raw)

    def _extract_event_with_compiled_protos(self, raw: bytes) -> tuple[str, dict[str, Any]] | None:
        """Parse event by finding HubEventQualifier embedded in raw protobuf."""
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.hub import (  # noqa: PLC0415, E501
            qualifier_pb2,
        )

        for candidate in self._find_embedded_messages(raw):
            try:
                qualifier = qualifier_pb2.HubEventQualifier()
                qualifier.ParseFromString(candidate)
                if qualifier.HasField("tag"):
                    tag = qualifier.tag
                    tag_field = tag.WhichOneof("event_tag_case")
                    if tag_field and tag_field in HUB_EVENT_TAG_MAP:
                        event_type = HUB_EVENT_TAG_MAP[tag_field]
                        data: dict[str, Any] = {"raw_tag": tag_field}
                        if qualifier.HasField("transition"):
                            trans_field = qualifier.transition.WhichOneof("transition")
                            if trans_field:
                                data["transition"] = trans_field
                        return event_type, data
            except Exception:
                continue
        return None

    @staticmethod
    def _find_embedded_messages(raw: bytes) -> list[bytes]:
        """Extract candidate embedded protobuf messages from raw bytes.

        Scans for length-delimited fields (wire type 2) and extracts their content.
        Returns candidates from deepest nesting first (most likely to be the qualifier).
        """
        candidates: list[bytes] = []
        i = 0
        while i < len(raw) - 2:
            wire_type = raw[i] & 0x07
            if wire_type == 2:  # length-delimited
                # Read varint length
                j = i + 1
                length = 0
                shift = 0
                while j < len(raw):
                    byte = raw[j]
                    length |= (byte & 0x7F) << shift
                    shift += 7
                    j += 1
                    if not (byte & 0x80):
                        break
                if j + length <= len(raw) and 4 < length < 500:
                    candidate = raw[j : j + length]
                    candidates.append(candidate)
                    # Also recurse into the candidate
                    inner = AjaxNotificationListener._find_embedded_messages(candidate)
                    candidates.extend(inner)
                i = j + length if j + length <= len(raw) else i + 1
            else:
                i += 1
        return candidates

    @staticmethod
    def _extract_source_info(raw: bytes) -> dict[str, Any]:
        """Extract device source information from raw protobuf bytes.

        Scans for HubNotificationSource by looking for the field pattern
        (type varint + id string + name string) and attempting proto parsing
        at each potential start position.
        """
        try:
            from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.hub import (  # noqa: PLC0415, E501
                source_pb2,
                source_type_pb2,
            )
        except ImportError:
            _LOGGER.debug("Source proto not available")
            return {}

        # Build reverse map from enum value to name
        source_type_enum = source_type_pb2.HubNotificationSourceType.DESCRIPTOR
        type_name_map = {v.number: v.name for v in source_type_enum.values}

        # Scan for field 1 varint (0x08 XX) which is the source type field.
        # Try parsing HubNotificationSource from each potential start.
        for i in range(len(raw) - 5):
            if raw[i] != 0x08:
                continue
            # Try multiple slice lengths to find a valid parse
            for end in range(i + 10, min(i + 80, len(raw) + 1)):
                try:
                    source = source_pb2.HubNotificationSource()
                    source.ParseFromString(raw[i:end])
                    if source.name and source.id and source.type > 0:
                        result: dict[str, Any] = {
                            "device_name": source.name,
                            "device_id": source.id,
                            "device_type": type_name_map.get(source.type, str(source.type)),
                        }
                        if source.HasField("_room_name") and source.room_name:
                            result["room_name"] = source.room_name
                        return result
                except Exception:
                    continue
        return {}

    @staticmethod
    def _extract_event_raw(raw: bytes) -> tuple[str, dict[str, Any]] | None:
        """Fallback: extract event tag from raw protobuf bytes by scanning for known patterns."""
        # This is a best-effort fallback when compiled protos aren't available
        return None

    async def async_stop(self) -> None:
        """Stop the FCM push client."""
        if self._push_client:
            try:
                stop_result = self._push_client.stop()
                if hasattr(stop_result, "__await__"):
                    await stop_result
                _LOGGER.debug("FCM push client stopped")
            except Exception:
                _LOGGER.exception("Error stopping FCM push client")
            self._push_client = None
