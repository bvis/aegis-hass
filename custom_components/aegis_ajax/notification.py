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

from custom_components.aegis_ajax import notification_event_parser
from custom_components.aegis_ajax.const import (
    DOMAIN,
    DOORBELL_DEVICE_TYPES,
    DOORBELL_EVENT_TYPE,
    MOTION_EVENT_TYPE,
    RAW_TAG_TO_GROUP_SECURITY_STATE,
    RAW_TAG_TO_SECURITY_STATE,
)
from custom_components.aegis_ajax.repairs import (
    async_clear_fcm_credentials_invalid,
    async_clear_fcm_credentials_malformed,
    async_clear_fcm_not_configured,
    async_register_fcm_credentials_invalid,
    async_register_fcm_credentials_malformed,
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

# Issue #174: when the underlying TCP socket against `mtalk.google.com:5228`
# (FCM's MCS endpoint) gets reset, Google replays any push that wasn't acked
# before the disconnect — sometimes hours after Ajax originally sent it. The
# notif_id dedupe above is bounded to 5 s, so a replay arriving minutes later
# slips through and fires a stale `desarmada` (or other security event) on
# the user's phone. The Notification proto carries a `server_timestamp` set
# by Ajax cloud at dispatch time, so we drop anything older than this window.
# 120 s is comfortably longer than the worst Ajax→FCM→client latency we've
# measured (sub-second) but short enough that a replay from any prior session
# is rejected.
STALE_PUSH_THRESHOLD_SECONDS = 120.0

# A run of this many consecutive printable-ASCII bytes in a redacted hex dump
# is treated as text (likely a device name / label) and masked. Shorter runs
# stay as hex — too short to leak meaningful PII, and often coincidental.
_MIN_PRINTABLE_RUN = 3


def _redact_printable(data: bytes) -> str:
    """Hex-encode `data`, masking runs of >=3 printable-ASCII bytes as
    `<text:Nb>` (#173). Debug payload dumps can carry user device labels;
    this keeps the binary shape visible for diagnosis without leaking PII
    when a user pastes the log publicly. See [[feedback_pii_in_debug_logs]].
    """
    out: list[str] = []
    run: list[int] = []

    def flush() -> None:
        if not run:
            return
        if len(run) >= _MIN_PRINTABLE_RUN:
            out.append(f"<text:{len(run)}b>")
        else:
            out.append(bytes(run).hex())
        run.clear()

    for byte in data:
        if 0x20 <= byte <= 0x7E:
            run.append(byte)
        else:
            flush()
            out.append(f"{byte:02x}")
    flush()
    return "".join(out)


# FCM credentials validation + library-error classifier extracted to
# `notification_fcm_creds.py`. Re-exported here so callers (tests + the
# listener inside this module) keep working unchanged.
from custom_components.aegis_ajax.notification_fcm_creds import (  # noqa: E402, F401
    _FCM_API_KEY_RE,
    _FCM_APP_ID_RE,
    _classify_fcm_failure,
    _validate_fcm_shape,
)


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
        app_label: str = "",
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._fcm_project_id = fcm_project_id
        self._fcm_app_id = fcm_app_id
        self._fcm_api_key = fcm_api_key
        self._fcm_sender_id = fcm_sender_id
        self._entry_id = entry_id
        self._app_label = app_label
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
            async_clear_fcm_credentials_malformed(self._hass, entry_id=self._entry_id)
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

        # Pre-flight shape check on the four values. A malformed
        # `fcm_app_id` (truncated hash tail) surfaces server-side as
        # `API_KEY_ANDROID_APP_BLOCKED` / `androidPackage: <empty>`,
        # which accurately reports the symptom but hides the culprit —
        # the user concludes the API key is wrong and keeps re-pasting
        # it (#155, #182). Catching it offline lets the Repair card
        # name `fcm_app_id` directly.
        shape_problem = _validate_fcm_shape(
            fcm_project_id=self._fcm_project_id,
            fcm_app_id=self._fcm_app_id,
            fcm_api_key=self._fcm_api_key,
            fcm_sender_id=self._fcm_sender_id,
        )
        if shape_problem is not None:
            _LOGGER.warning(
                "FCM credentials malformed — push notifications disabled. %s. "
                "Re-extract per the README's 'Where the values live' section "
                "and re-enter all four values via the Repair card under "
                "Settings → Repairs.",
                shape_problem,
            )
            if self._entry_id:
                async_register_fcm_credentials_malformed(
                    self._hass, entry_id=self._entry_id, problem=shape_problem
                )
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
            # Inject `X-Android-Package` on Firebase Installations calls when
            # we know the user's co-branded Android package. The Ajax co-brand
            # api-key on Project B has Google package restriction enabled, so
            # the default `firebase_messaging` request (no package header)
            # gets refused with `API_KEY_ANDROID_APP_BLOCKED` /
            # `androidPackage: <empty>` (#155, #182). We attach the header as
            # a default on a session passed via `http_client_session` —
            # aiohttp merges per-request headers on top, so the library's own
            # `x-firebase-client` / `x-goog-api-key` keys are untouched and
            # every request we initiate (`fcm_install`, refresh, register)
            # carries the package id. Co-brands without a mapping fall back
            # to the pre-1.5.3-beta.10 behaviour (no header, no session).
            import aiohttp  # noqa: PLC0415

            from custom_components.aegis_ajax.const import (  # noqa: PLC0415
                APP_LABEL_TO_ANDROID_PACKAGE,
            )

            android_package = APP_LABEL_TO_ANDROID_PACKAGE.get(self._app_label)
            fcm_session: aiohttp.ClientSession | None = None
            if android_package:
                fcm_session = aiohttp.ClientSession(headers={"X-Android-Package": android_package})
                _LOGGER.debug(
                    "FCM registration will carry X-Android-Package: %s",
                    android_package,
                )
            try:
                if fcm_session is not None:
                    registerer = FcmRegister(config=fcm_config, http_client_session=fcm_session)
                else:
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
            finally:
                if fcm_session is not None:
                    await fcm_session.close()

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

        # Drop FCM replays of pushes Ajax dispatched in a prior session (#174).
        # Done before any side effect — photo-URL futures and notif_id dedupe
        # state must stay untouched by a stale replay. Fail-open: pushes whose
        # `server_timestamp` we can't recover fall through unchanged so a
        # parser miss never silences a real event.
        if encoded_data and self._is_stale_push(encoded_data):
            return

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

    def _is_stale_push(self, encoded_data: str) -> bool:
        """Return True when an FCM push carries a `Notification.server_timestamp`
        older than `STALE_PUSH_THRESHOLD_SECONDS`.

        Parses just the top-level `PushNotificationDispatchEvent` to recover
        the timestamp Ajax stamped at dispatch time. Any decode error, a
        non-`notification` oneof, or a missing `server_timestamp` returns
        False — the caller treats the push as fresh so a parser miss never
        silences a real event (#174 fail-open).
        """
        try:
            from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.service.push_notification_dispatch import (  # noqa: PLC0415, E501
                event_pb2,
            )
        except ImportError:
            return False
        try:
            raw = base64.b64decode(encoded_data)
        except Exception:
            return False
        try:
            dispatch = event_pb2.PushNotificationDispatchEvent()
            dispatch.ParseFromString(raw)
        except Exception:
            return False
        if dispatch.WhichOneof("push") != "notification":
            return False
        if not dispatch.notification.HasField("server_timestamp"):
            return False
        ts = dispatch.notification.server_timestamp
        # `Timestamp.seconds` + `.nanos` → POSIX seconds. Compare against
        # `time.time()` (wall clock) — `time.monotonic` would be wrong here
        # because the FCM timestamp is absolute.
        push_unix = ts.seconds + ts.nanos / 1_000_000_000
        age = time.time() - push_unix
        if age > STALE_PUSH_THRESHOLD_SECONDS:
            _LOGGER.warning(
                "Dropping stale FCM push: server_timestamp is %.0fs old "
                "(threshold %.0fs). Likely an FCM-server replay after a "
                "reconnect (#174); the integration will resync on the next "
                "snapshot refresh.",
                age,
                STALE_PUSH_THRESHOLD_SECONDS,
            )
            return True
        return False

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
        return notification_event_parser.extract_notification_id(encoded_data)

    def _parse_and_fire_event(self, encoded_data: str) -> None:
        """Parse event from base64-encoded push notification data."""
        try:
            raw = base64.b64decode(encoded_data)
            event_info = self._extract_event_from_proto(raw)
            if event_info:
                event_type, event_data = event_info
                # Enrich with source device info (name, room, type). For
                # group-level events the source is a SpaceNotificationSource
                # carrying the group_id; extract that too so the per-group
                # alarm panel can be updated (#148).
                source_info = self._extract_source_info(raw)
                if source_info:
                    event_data.update(source_info)
                if event_data.get("raw_tag") in RAW_TAG_TO_GROUP_SECURITY_STATE:
                    # Ajax actually encodes the group context in
                    # `additional_data.space_display_groups` as a
                    # `DisplayGroups.Group(group_hex_id, group_name)` —
                    # not in the `SpaceNotificationSource` we used to scan
                    # (#148 wire capture in beta.6). Try DisplayGroups
                    # first; fall back to the legacy SpaceNotificationSource
                    # path in case a future Ajax build also emits it there.
                    group_info = self._extract_space_group_info(
                        raw
                    ) or self._extract_space_source_info(raw)
                    if group_info:
                        event_data.update(group_info)
                    else:
                        # Diagnostic path: parser confirmed a `space_group_*`
                        # event but neither extractor located the group_id,
                        # so the per-group panel only updates on next poll.
                        # The hex dump survives in case Ajax ships yet another
                        # wire shape down the road. WARNING is intentional —
                        # degraded user-visible behaviour.
                        _LOGGER.warning(
                            "Group push %s parsed without group_id; "
                            "per-group panel will only update on next poll. "
                            "Raw payload (hex, capped 2048 bytes): %s",
                            event_data.get("raw_tag"),
                            raw[:2048].hex(),
                        )
                _LOGGER.debug(
                    "Push event parsed: event_type=%s raw_tag=%s group_id=%s",
                    event_type,
                    event_data.get("raw_tag"),
                    event_data.get("group_id"),
                )
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
                # Surface doorbell ring / motion on the source device's own
                # card, in addition to the hub-level event entity (#173).
                self._dispatch_event_to_device(event_type, event_data, raw)
        except Exception:
            _LOGGER.debug("Failed to parse event from push notification", exc_info=True)

    def _dispatch_event_to_device(
        self, event_type: str, event_data: dict[str, Any], raw: bytes
    ) -> None:
        """Mirror a doorbell ring / motion push onto the source device (#173).

        Resolves the source device from the `device_id` carried in the push
        (`_extract_source_info`). For doorbell rings, when no usable device id
        is present we fall back to the sole doorbell device in the install —
        the common single-doorbell case — so users still see the ring on the
        doorbell card. Motion has no such fallback: the `motion` event_type is
        shared with PIR detectors, so an unattributed motion push must not be
        guessed onto an arbitrary device.

        The instrumentation log below is intentional: it records exactly what
        device attribution the parser resolved (or failed to), so if a user's
        firmware turns out to ship a source shape we don't decode, the next
        debug capture shows it without another code round-trip.
        """
        if event_type not in (DOORBELL_EVENT_TYPE, MOTION_EVENT_TYPE):
            return

        devices = getattr(self._coordinator, "devices", {}) or {}
        device_id = event_data.get("device_id")
        resolved = device_id if device_id in devices else None

        if resolved is None and event_type == DOORBELL_EVENT_TYPE:
            doorbells = [
                dev_id
                for dev_id, dev in devices.items()
                if getattr(dev, "device_type", None) in DOORBELL_DEVICE_TYPES
            ]
            if len(doorbells) == 1:
                resolved = doorbells[0]

        # device_name is intentionally omitted — it's a user label (PII) and
        # debug logs get pasted publicly. The hardware id + type are enough to
        # confirm attribution.
        _LOGGER.debug(
            "Push device attribution: event_type=%s extracted_device_id=%s "
            "device_type=%s resolved=%s",
            event_type,
            device_id,
            event_data.get("device_type"),
            resolved,
        )

        if resolved is None:
            # Capped, PII-redacted hex of the payload so an unattributed
            # doorbell/motion push can still be diagnosed from a debug log.
            _LOGGER.debug(
                "Push %s not attributed to a device; source missing or unknown. "
                "Payload (hex, redacted, capped 512b): %s",
                event_type,
                _redact_printable(raw[:512]),
            )
            return

        if event_type == DOORBELL_EVENT_TYPE:
            self._coordinator.fire_push_device_event(resolved, event_type, event_data)
        elif event_type == MOTION_EVENT_TYPE and self._hass.loop and self._hass.loop.is_running():
            self._hass.loop.call_soon_threadsafe(
                self._coordinator.apply_push_device_motion, resolved
            )

    def _apply_security_state_from_event(self, space_id: str, event_data: dict[str, Any]) -> None:
        """If the push event implies a new security_state, push it now (#68 / #148).

        Routes space-wide tags to the space-level `apply_push_security_state`
        and `space_group_*` tags (when accompanied by a `group_id` extracted
        from the SpaceNotificationSource) to the per-group equivalent.

        The FCM callback runs on the firebase_messaging worker thread, so we
        dispatch the update to the HA event loop via call_soon_threadsafe.
        """
        raw_tag = event_data.get("raw_tag")
        if not isinstance(raw_tag, str):
            return
        if not (self._hass.loop and self._hass.loop.is_running()):
            return
        group_state = RAW_TAG_TO_GROUP_SECURITY_STATE.get(raw_tag)
        group_id = event_data.get("group_id")
        if group_state is not None and isinstance(group_id, str) and group_id:
            self._hass.loop.call_soon_threadsafe(
                self._coordinator.apply_push_group_security_state,
                space_id,
                group_id,
                group_state,
            )
            return
        new_state = RAW_TAG_TO_SECURITY_STATE.get(raw_tag)
        if new_state is None:
            return
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

    # Parsing delegators. The base64/protobuf event-decoding logic lives in the
    # pure, listener-free `notification_event_parser` module; these thin
    # forwarders preserve the historical `AjaxNotificationListener._extract_*` /
    # `.extract_notification_id` call surface relied on by `_parse_and_fire_event`
    # and the test suite. `_extract_event_with_compiled_protos` stays an instance
    # method because the tests call it as `listener._extract_event_with_compiled_protos`.

    def _extract_event_with_compiled_protos(self, raw: bytes) -> tuple[str, dict[str, Any]] | None:
        return notification_event_parser._extract_event_with_compiled_protos(raw)

    @staticmethod
    def _extract_source_info(raw: bytes) -> dict[str, Any]:
        return notification_event_parser._extract_source_info(raw)

    @staticmethod
    def _extract_space_source_info(raw: bytes) -> dict[str, Any]:
        return notification_event_parser._extract_space_source_info(raw)

    @staticmethod
    def _extract_space_group_info(raw: bytes) -> dict[str, Any]:
        return notification_event_parser._extract_space_group_info(raw)

    @staticmethod
    def _extract_event_raw(raw: bytes) -> tuple[str, dict[str, Any]] | None:
        return notification_event_parser._extract_event_raw(raw)

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
