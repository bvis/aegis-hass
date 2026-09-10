"""Media API: retrieve photo URLs from notification media streams."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlparse

from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification import (
    filter_pb2,
    folder_pb2,
    origin_id_pb2,
)
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.hub.media import (  # noqa: E501
    image_status_pb2,
)
from systems.ajax.api.mobile.v2.notificationlog import (
    find_notifications_pb2,
    notification_log_endpoints_pb2_grpc,
    stream_media_pb2,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Sequence

    from custom_components.aegis_ajax.api.client import AjaxGrpcClient

_LOGGER = logging.getLogger(__name__)

_STREAM_NOTIFICATION_MEDIA = (
    "/systems.ajax.api.mobile.v2.notification.NotificationLogService/streamNotificationMedia"
)

_ALARM_HISTORY_LIMIT = 50
_ALARM_MEDIA_LIMIT = 10


@dataclass(frozen=True)
class AlarmMedia:
    """Media attached to one historical hub alarm notification."""

    device_id: str
    notification_id: str
    image_urls: tuple[str, ...]
    timestamp: float = 0


class _MediaAsset(Protocol):
    """The URL fields shared by alarm images and video frames."""

    url: str


class _HubImage(_MediaAsset, Protocol):
    """Hub photos carry a per-frame upload status."""

    status: int


class _HubNotificationMedia(Protocol):
    """Relevant subset of the generated HubNotificationMedia protobuf."""

    images: Sequence[_HubImage]


class _VideoFramesMedia(Protocol):
    """Relevant subset of the generated VideoFramesMedia protobuf."""

    frames: Sequence[_MediaAsset]


class _NotificationMedia(Protocol):
    """Relevant subset of the generated NotificationMedia protobuf."""

    hub_notification_media: _HubNotificationMedia
    video_frames_media: _VideoFramesMedia

    def WhichOneof(self, _oneof_group: str, /) -> str | None: ...  # noqa: N802


class _StreamNotificationMediaSuccess(Protocol):
    """Relevant subset of a successful notification-media response."""

    media: _NotificationMedia


class _StreamNotificationMediaResponse(Protocol):
    """Relevant subset of the generated stream response protobuf."""

    success: _StreamNotificationMediaSuccess

    def WhichOneof(self, _oneof_group: str, /) -> str | None: ...  # noqa: N802


class _NotificationMediaStream(Protocol):
    """The cancellable async stream returned by grpc.aio."""

    def __aiter__(self) -> AsyncIterator[_StreamNotificationMediaResponse]: ...

    def cancel(self) -> bool: ...


class _NotificationLogServiceStub(Protocol):
    """Typed subset of the generated notification-log client stub."""

    def streamNotificationMedia(  # noqa: N802
        self,
        request: stream_media_pb2.StreamNotificationMediaRequest,
        *,
        metadata: list[tuple[str, str]],
        timeout: float,
    ) -> _NotificationMediaStream: ...


def is_valid_photo_url(url: str) -> bool:
    """Return whether a signed Ajax photo URL points at a known host."""
    hostname = urlparse(url).hostname or ""
    # Anchor the S3 branch to the real bucket host so a substring match can't
    # accept e.g. `hubs-uploaded-resources.attacker.com` (SSRF).
    is_s3 = "hubs-uploaded-resources" in hostname and hostname.endswith(".amazonaws.com")
    return hostname.endswith(".ajax.systems") or is_s3


def _photo_urls_from_media(media: _NotificationMedia) -> tuple[str, ...]:
    """Extract a complete sequence, waiting for every pending hub frame.

    Failed frames are terminal and cannot be downloaded. An empty result for
    an unfinished sequence keeps the stream active instead of silently
    treating the first ready frame as the whole alarm.
    """
    content = media.WhichOneof("content")
    if content == "hub_notification_media":
        images = media.hub_notification_media.images
        if any(
            image.status != image_status_pb2.IMAGE_STATUS_FAILED
            and (image.status != image_status_pb2.IMAGE_STATUS_READY or not image.url)
            for image in images
        ):
            return ()
        return tuple(
            image.url for image in images if image.status == image_status_pb2.IMAGE_STATUS_READY
        )
    if content == "video_frames_media":
        return tuple(frame.url for frame in media.video_frames_media.frames if frame.url)
    return ()


def _notification_timestamp(notification: object) -> float:
    """Return a notification's server timestamp for deterministic ordering."""
    timestamp = getattr(notification, "server_timestamp", None)
    if timestamp is None:
        return 0
    return float(timestamp.seconds) + float(timestamp.nanos) / 1_000_000_000


def _encode_string_field(field_number: int, value: str) -> bytes:
    """Encode a protobuf string field (wire type 2)."""
    encoded = value.encode("utf-8")
    tag = (field_number << 3) | 2
    length_bytes = _encode_varint(len(encoded))
    return bytes([tag]) + length_bytes + encoded


def _encode_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def _encode_embedded_message(field_number: int, data: bytes) -> bytes:
    """Encode an embedded message field (wire type 2)."""
    tag = (field_number << 3) | 2
    length_bytes = _encode_varint(len(data))
    return bytes([tag]) + length_bytes + data


class MediaApi:
    """API for retrieving media (photos) from Ajax notification system."""

    def __init__(self, client: AjaxGrpcClient) -> None:
        self._client = client

    async def get_recent_alarm_media(
        self,
        space_id: str,
        *,
        device_ids: Collection[str] | None = None,
        is_stored: Callable[[str, str, float], Awaitable[bool]] | None = None,
    ) -> tuple[AlarmMedia, ...]:
        """Return recent alarm images for one space.

        Notification history is a server-side log, separate from FCM. It is
        queried only by the explicit backfill service, never by FCM or polling.
        Completed local albums are skipped before spending the media budget.
        """
        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()
        stub = notification_log_endpoints_pb2_grpc.NotificationLogServiceStub(channel)
        request = find_notifications_pb2.FindNotificationsRequest(
            filter=filter_pb2.NotificationsFilter(
                origin=origin_id_pb2.NotificationOriginId(space_id=space_id),
                folder=folder_pb2.FOLDER_ALARM,
            ),
            limit=_ALARM_HISTORY_LIMIT,
        )

        try:
            response = await stub.findNotifications(request, metadata=metadata, timeout=15)
        except Exception:
            _LOGGER.debug("Could not retrieve Ajax alarm notification history", exc_info=True)
            return ()

        if response.WhichOneof("response") != "success":
            _LOGGER.debug("Ajax alarm notification history was not available")
            return ()

        # The API's response ordering is not contractual. Prefer the newest
        # history entries before applying the media request budget, otherwise a
        # busy installation can spend all ten requests on old alarms.
        notifications = sorted(
            response.success.notifications,
            key=_notification_timestamp,
            reverse=True,
        )
        target_device_ids = set(device_ids) if device_ids is not None else None
        alarms: list[AlarmMedia] = []
        media_attempts = 0
        for notification in notifications:
            content = notification.content.WhichOneof("content")
            if content == "hub_notification_content":
                hub_content = notification.content.hub_notification_content
                device_id = hub_content.source.id
                origin = origin_id_pb2.NotificationOriginId(hub_hex_id=hub_content.origin.hex_id)
            elif content == "video_notification_content":
                # Video/NVR events are scoped to the space rather than an Ajax
                # hub. This has not appeared on the development account yet,
                # but the notification protocol explicitly supports it.
                device_id = notification.content.video_notification_content.source.id
                origin = origin_id_pb2.NotificationOriginId(space_id=space_id)
            else:
                continue

            if not notification.id or not device_id or origin.WhichOneof("origin") is None:
                continue
            # Skip non-camera alarm sources before using the bounded media
            # budget. A Hub can generate many other alarm history entries.
            if target_device_ids is not None and device_id not in target_device_ids:
                continue
            if is_stored is not None and await is_stored(
                device_id, notification.id, _notification_timestamp(notification)
            ):
                continue
            if media_attempts >= _ALARM_MEDIA_LIMIT:
                break
            media_attempts += 1

            image_urls = await self._get_notification_media_urls(
                stub,
                notification_id=notification.id,
                origin=origin,
                metadata=metadata,
            )
            if image_urls:
                alarms.append(
                    AlarmMedia(
                        device_id=device_id,
                        notification_id=notification.id,
                        image_urls=image_urls,
                        timestamp=_notification_timestamp(notification),
                    )
                )

        return tuple(alarms)

    async def get_alarm_media(
        self, notification_id: str, hub_id: str, device_id: str, timestamp: float
    ) -> AlarmMedia | None:
        """Open exactly one media stream for a pushed alarm; never query history."""
        stub = notification_log_endpoints_pb2_grpc.NotificationLogServiceStub(
            self._client._get_channel()
        )
        urls = await self._get_notification_media_urls(
            stub,
            notification_id=notification_id,
            origin=origin_id_pb2.NotificationOriginId(hub_hex_id=hub_id),
            metadata=self._client._session.get_call_metadata(),
            timeout=60,
        )
        return AlarmMedia(device_id, notification_id, urls, timestamp) if urls else None

    async def _get_notification_media_urls(
        self,
        stub: _NotificationLogServiceStub,
        *,
        notification_id: str,
        origin: origin_id_pb2.NotificationOriginId,
        metadata: list[tuple[str, str]],
        timeout: float = 10,
    ) -> tuple[str, ...]:
        """Read the ready media payload for one historical notification."""
        request = stream_media_pb2.StreamNotificationMediaRequest(
            notification_id=notification_id,
            origin=origin,
        )
        stream = stub.streamNotificationMedia(request, metadata=metadata, timeout=timeout)
        try:
            async for response in stream:
                if response.WhichOneof("response") != "success":
                    return ()
                urls = _photo_urls_from_media(response.success.media)
                if urls:
                    # Never turn a rejected frame into a successful partial album.
                    return urls if all(is_valid_photo_url(url) for url in urls) else ()
        except Exception:
            # `not_found` and expired assets are normal for old notifications;
            # do not make one unavailable item abort the import.
            _LOGGER.debug("Could not retrieve Ajax alarm notification media", exc_info=True)
        finally:
            stream.cancel()
        return ()

    async def get_photo_url(
        self, notification_id: str, hub_hex_id: str, timeout: float = 15.0
    ) -> str | None:
        """Stream notification media and return the photo URL when ready.

        Opens a server-streaming gRPC call to NotificationLogService/streamNotificationMedia.
        Waits for IMAGE_STATUS_READY and extracts the photo URL.
        Returns None on timeout or if no URL is found.
        """
        # Build StreamNotificationMediaRequest:
        # field 1 (string): notification_id
        # field 2 (message): NotificationOriginId { field 1 (string): hub_hex_id }
        origin_msg = _encode_string_field(1, hub_hex_id)
        request_bytes = _encode_string_field(1, notification_id) + _encode_embedded_message(
            2, origin_msg
        )

        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()

        method = channel.unary_stream(
            _STREAM_NOTIFICATION_MEDIA,
            request_serializer=lambda x: x,
            response_deserializer=lambda x: x,
        )

        _LOGGER.debug(
            "Opening media stream: notification_id=%s hub=%s",
            notification_id[:20],
            hub_hex_id,
        )

        # Poll the media stream with retries — the first response may have
        # IMAGE_STATUS_IN_PROGRESS (no URL). Retry after a delay until READY.
        poll_interval = 5.0
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                stream = method(request_bytes, metadata=metadata, timeout=10)
                async for raw_response in stream:
                    urls = re.findall(rb'https://[^\x00-\x1f\x7f-\x9f"\'\\]+', raw_response)
                    for raw_url in urls:
                        url: str = raw_url.decode("utf-8", errors="ignore")
                        parsed = urlparse(url)
                        hostname = parsed.hostname or ""
                        is_ajax = hostname.endswith(".ajax.systems")
                        # Anchor the S3 branch to the real bucket host — a bare
                        # `in` substring would also accept e.g.
                        # `hubs-uploaded-resources.attacker.com` (SSRF).
                        is_s3 = "hubs-uploaded-resources" in hostname and hostname.endswith(
                            ".amazonaws.com"
                        )
                        if is_ajax or is_s3:
                            # Host + path only — the query string holds the S3 signature.
                            _LOGGER.debug(
                                "Photo URL from media stream: %s%s", hostname, parsed.path
                            )
                            return url
                    _LOGGER.debug(
                        "Media stream: %d bytes, no URL yet, retrying in %.0fs",
                        len(raw_response),
                        poll_interval,
                    )
                    break  # Got a frame but no URL — break and retry after delay
            except Exception:
                _LOGGER.debug("Media stream attempt failed, retrying in %.0fs", poll_interval)
            await asyncio.sleep(poll_interval)

        _LOGGER.debug("Timeout waiting for photo URL from media stream")
        return None
