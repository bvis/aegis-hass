"""FCM push notification listener for Ajax Security."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from homeassistant.helpers.storage import Store

from custom_components.aegis_ajax.const import (
    DOMAIN,
    HUB_EVENT_TAG_MAP,
    RAW_TAG_TO_SECURITY_STATE,
    SPACE_EVENT_TAG_MAP,
    VIDEO_EVENT_TAG_MAP,
)
from custom_components.aegis_ajax.repairs import (
    async_clear_fcm_credentials_invalid,
    async_clear_fcm_not_configured,
    async_register_fcm_credentials_invalid,
    async_register_fcm_not_configured,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}_fcm_credentials"
STORAGE_VERSION = 1

# Ajax dispatches two FCM messages per security transition (one user-facing
# Notification + one silent DispatchEvent), separated by ~20-30 ms server-side.
# Both share the same Ajax notification_id, so we suppress duplicate event-fire
# and refresh paths within this window. See #80.
NOTIFICATION_DEDUPE_WINDOW_SECONDS = 5.0


def _classify_fcm_failure(exc: BaseException) -> str:
    """Return a user-actionable WARNING message for an FCM registration / push-client error.

    `firebase-messaging` 0.4.5 raises plain `RuntimeError` with one of three
    fixed message strings, hiding any HTTP status and aiohttp cause behind
    internal `_logger` calls — so `__cause__` / `__context__` are always None
    and the only signal we get is the literal `str(exc)`.

    The three branches below were measured empirically (probe against real FCM
    endpoints with deliberate credential corruptions + a DNS block of the FCM
    hosts), not inferred from the source:

      * "Unable to establish subscription with Google Cloud Messaging."
        — dominant failure mode for any credential-set error (bad sender_id,
        api_key, project_id, or app_id with valid shape). Hansontech190's
        case lands here.

      * "Unable to register with fcm"
        — fires only when the app_id is malformed enough that the Firebase
        Installation API rejects it with HTTP 400. The shape `1:<sender>:
        <platform>:<hex>` is what Firebase parses.

      * "Unable to register and check in to gcm"
        — the four credentials are not used in the GCM checkin step, so this
        string only appears when the FCM hosts are unreachable (DNS, firewall,
        proxy). aiohttp errors are swallowed by the library's retry loop.
    """
    msg = str(exc) if exc else ""
    lower = msg.lower()

    if "subscription" in lower and "google cloud messaging" in lower:
        return (
            "FCM registration rejected by Google. The four credentials must all "
            "come from the same Firebase project — fcm_sender_id must match the "
            "numeric prefix inside fcm_app_id, and fcm_api_key must be paired "
            "with that same fcm_project_id. Re-enter all four together via the "
            "Repair card under Settings → Repairs."
        )
    if "unable to register with fcm" in lower:
        return (
            "Firebase rejected the app credentials. Most likely fcm_app_id has "
            'an invalid format — the expected shape is "1:<numeric sender>:'
            '<platform>:<hex suffix>". Re-check the value entered via the '
            "Repair card under Settings → Repairs."
        )
    if "unable to register and check in to gcm" in lower:
        return (
            "Couldn't reach Google FCM servers. Check the HA host can reach "
            "android.clients.google.com / firebaseinstallations.googleapis.com "
            "(firewall, DNS, or proxy issue). The Repair card stays raised until "
            "the next successful registration."
        )
    return f"FCM registration failed: {msg or exc.__class__.__name__}"


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
        entry_id: str = "",
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._fcm_project_id = fcm_project_id
        self._fcm_app_id = fcm_app_id
        self._fcm_api_key = fcm_api_key
        self._fcm_sender_id = fcm_sender_id
        self._entry_id = entry_id
        self._push_client: Any = None
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._credentials: dict[str, Any] | None = None
        self._photo_callbacks: dict[str, asyncio.Future[str | None]] = {}
        self._notification_id_callbacks: dict[str, asyncio.Future[str | None]] = {}
        self._last_notification_id: str | None = None
        # notification_id → time.monotonic() of first sighting; used to suppress
        # the second of the two FCM messages Ajax sends per event (#80).
        self._recent_notification_ids: dict[str, float] = {}
        # Counters surfaced by system_health.py for in-UI diagnostics.
        # Incremented inside `_on_notification` after the dedupe gate so
        # only "real" pushes are counted; the dedupe-suppressed twin
        # doesn't double-count.
        self._pushes_received: int = 0
        self._last_push_at: float | None = None

    @property
    def pushes_received(self) -> int:
        """Total non-deduped push notifications received since startup."""
        return self._pushes_received

    @property
    def last_push_at(self) -> float | None:
        """`time.monotonic()` of the most recent non-deduped push, or None."""
        return self._last_push_at

    @property
    def is_fcm_connected(self) -> bool:
        """True if the FCM push client is alive."""
        return self._push_client is not None

    async def async_start(self) -> None:
        """Register with FCM and start listening for push notifications."""
        # Repair is per-entry; clear at every start so that a fresh
        # credentials roundtrip can re-raise it from a clean slate.
        if self._entry_id:
            async_clear_fcm_credentials_invalid(self._hass, entry_id=self._entry_id)
            async_clear_fcm_not_configured(self._hass, entry_id=self._entry_id)
        if not self._fcm_api_key:
            _LOGGER.warning(
                "FCM credentials not configured — push notifications disabled, "
                "real-time events (doorbell ring, arm/disarm, alarm) will not reach HA. "
                "Configure them in Settings → Devices & Services → Aegis for Ajax → Configure, "
                "or open the Repair card surfaced under Settings → Repairs."
            )
            if self._entry_id:
                async_register_fcm_not_configured(self._hass, entry_id=self._entry_id)
            return

        try:
            from firebase_messaging import FcmPushClient  # noqa: PLC0415
            from firebase_messaging.fcmregister import (  # noqa: PLC0415
                FcmRegister,
                FcmRegisterConfig,
            )
        except ImportError:
            _LOGGER.warning(
                "firebase_messaging package not installed — push notifications disabled. "
                "This is unexpected; reinstall the integration via HACS."
            )
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
                _LOGGER.info("FCM registration successful")
            except Exception as exc:
                _LOGGER.warning(_classify_fcm_failure(exc), exc_info=True)
                if self._entry_id:
                    async_register_fcm_credentials_invalid(self._hass, entry_id=self._entry_id)
                return

        # Extract FCM token and register with Ajax servers
        fcm_data = self._credentials.get("fcm", {})
        registration = fcm_data.get("registration", {}) if isinstance(fcm_data, dict) else {}
        fcm_token = registration.get("token") if isinstance(registration, dict) else None
        if fcm_token:
            _LOGGER.debug("FCM token obtained, registering with Ajax servers")
            await self._register_push_token(str(fcm_token))
        else:
            _LOGGER.warning(
                "FCM registration returned no token — push delivery will not work. "
                "Most often caused by malformed FCM credentials (project_id / app_id / "
                "api_key / sender_id mismatch). Re-extract the four values per the "
                "integration README and re-enter them in Options."
            )

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
            _LOGGER.info("FCM push client started — push notifications active")
        except Exception as exc:
            _LOGGER.warning(_classify_fcm_failure(exc), exc_info=True)
            self._push_client = None
            if self._entry_id:
                async_register_fcm_credentials_invalid(self._hass, entry_id=self._entry_id)

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
                _LOGGER.warning(
                    "Ajax server rejected the push-token registration — push delivery "
                    "may be silent. Response did not carry a `success` field."
                )
        except Exception:
            _LOGGER.exception("Error registering push token with Ajax servers")

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
        notif_id: str | None = None
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

        # Dedupe Ajax's two-FCM-per-event dispatch (#80). Pushes without an
        # extractable notification_id fall through unchanged so a parser miss
        # never silences an unrelated event.
        if notif_id and self._is_duplicate_notification(notif_id):
            _LOGGER.debug(
                "Duplicate notification_id %s within %.0fs window; skipping fire/refresh",
                notif_id[:20],
                NOTIFICATION_DEDUPE_WINDOW_SECONDS,
            )
            return

        # Count only real (non-dedupe-suppressed) pushes. Surfaced in the
        # System Health card so users can confirm push delivery is alive.
        self._pushes_received += 1
        self._last_push_at = time.monotonic()

        # Parse event from ENCODED_DATA using compiled protos
        if encoded_data:
            self._parse_and_fire_event(encoded_data)

        # Always trigger refresh
        if self._hass.loop and self._hass.loop.is_running():
            self._hass.loop.call_soon_threadsafe(
                self._hass.async_create_task,
                self._coordinator.async_request_refresh(),
            )

    def _is_duplicate_notification(self, notif_id: str) -> bool:
        """Return True if *notif_id* was seen within the dedupe window.

        Records the sighting on first call so the second push within the
        window is suppressed. Stale entries are pruned on every call to
        keep the dict bounded.
        """
        now = time.monotonic()
        cutoff = now - NOTIFICATION_DEDUPE_WINDOW_SECONDS
        # Prune expired entries.
        self._recent_notification_ids = {
            k: v for k, v in self._recent_notification_ids.items() if v > cutoff
        }
        if notif_id in self._recent_notification_ids:
            return True
        self._recent_notification_ids[notif_id] = now
        return False

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
        """Parse event by finding a Hub or Space event qualifier in raw protobuf.

        Arm/disarm pushes embed a `SpaceEventQualifier` (`SpaceNotificationContent.
        qualifier`) — try those first (#68). The same payload often also carries
        unrelated `HubEventQualifier` candidates describing zone-level
        sub-incidents (`ext_contact_opened`, `roller_shutter_alarm`); they are
        the legitimate primary tag for hub-level pushes (alarm, tamper, …) so
        they remain the fallback.
        """
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.hub import (  # noqa: PLC0415, E501
            qualifier_pb2 as hub_qualifier_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.space import (  # noqa: PLC0415, E501
            qualifier_pb2 as space_qualifier_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.video import (  # noqa: PLC0415, E501
            qualifier_pb2 as video_qualifier_pb2,
        )

        candidates = self._find_embedded_messages(raw)

        # Pass 1 — SpaceEventQualifier (arm/disarm/night/panic at space level).
        for candidate in candidates:
            try:
                space_q = space_qualifier_pb2.SpaceEventQualifier()
                space_q.ParseFromString(candidate)
            except Exception:
                continue
            if not space_q.HasField("tag"):
                continue
            tag_field = space_q.tag.WhichOneof("event_tag_case")
            if tag_field and tag_field in SPACE_EVENT_TAG_MAP:
                event_type = SPACE_EVENT_TAG_MAP[tag_field]
                data: dict[str, Any] = {"raw_tag": tag_field}
                if space_q.HasField("transition"):
                    trans_field = space_q.transition.WhichOneof("transition")
                    if trans_field:
                        data["transition"] = trans_field
                return event_type, data

        # Pass 2 — HubEventQualifier (zone-level events: alarm, tamper, …).
        for candidate in candidates:
            try:
                qualifier = hub_qualifier_pb2.HubEventQualifier()
                qualifier.ParseFromString(candidate)
                if qualifier.HasField("tag"):
                    tag = qualifier.tag
                    tag_field = tag.WhichOneof("event_tag_case")
                    if tag_field and tag_field in HUB_EVENT_TAG_MAP:
                        event_type = HUB_EVENT_TAG_MAP[tag_field]
                        data = {"raw_tag": tag_field}
                        if qualifier.HasField("transition"):
                            trans_field = qualifier.transition.WhichOneof("transition")
                            if trans_field:
                                data["transition"] = trans_field
                        return event_type, data
            except Exception:
                continue

        # Pass 3 — VideoEventQualifier (#119: MotionCam Video Doorbell ring,
        # plus motion / human detected from video devices). The video tag
        # set is disjoint from HubEventTag so its own qualifier is needed.
        for candidate in candidates:
            try:
                video_q = video_qualifier_pb2.VideoEventQualifier()
                video_q.ParseFromString(candidate)
            except Exception:
                continue
            if not video_q.HasField("tag"):
                continue
            tag_field = video_q.tag.WhichOneof("event_tag_case")
            if tag_field and tag_field in VIDEO_EVENT_TAG_MAP:
                event_type = VIDEO_EVENT_TAG_MAP[tag_field]
                data = {"raw_tag": tag_field}
                if video_q.HasField("transition"):
                    trans_field = video_q.transition.WhichOneof("transition")
                    if trans_field:
                        data["transition"] = trans_field
                return event_type, data
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
