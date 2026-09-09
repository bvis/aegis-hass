"""Tests for media API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification import (
    folder_pb2,
    media_pb2,
)
from systems.ajax.api.mobile.v2.notificationlog import (
    find_notifications_pb2,
    stream_media_pb2,
)

from custom_components.aegis_ajax.api.media import (
    MediaApi,
    _encode_embedded_message,
    _encode_string_field,
    _encode_varint,
    _photo_urls_from_media,
    is_valid_photo_url,
)


class _AsyncResponseStream:
    """Small cancellable async stream for grpc-media unit tests."""

    def __init__(self, response: stream_media_pb2.StreamNotificationMediaResponse) -> None:
        self._response = response
        self._sent = False
        self.cancelled = False

    def __aiter__(self) -> _AsyncResponseStream:
        return self

    async def __anext__(self) -> stream_media_pb2.StreamNotificationMediaResponse:
        if self._sent:
            raise StopAsyncIteration
        self._sent = True
        return self._response

    def cancel(self) -> bool:
        self.cancelled = True
        return True


class TestProtobufEncoding:
    def test_encode_varint_small(self) -> None:
        assert _encode_varint(1) == b"\x01"

    def test_encode_varint_medium(self) -> None:
        assert _encode_varint(300) == b"\xac\x02"

    def test_encode_varint_zero(self) -> None:
        assert _encode_varint(0) == b"\x00"

    def test_encode_string_field(self) -> None:
        result = _encode_string_field(1, "test")
        assert result[0] == 0x0A  # field 1, wire type 2
        assert result[1] == 4  # length
        assert result[2:] == b"test"

    def test_encode_embedded_message(self) -> None:
        inner = _encode_string_field(1, "hub123")
        result = _encode_embedded_message(2, inner)
        assert result[0] == 0x12  # field 2, wire type 2
        assert inner in result

    def test_stream_notification_media_request_encoding(self) -> None:
        """Verify the full request encoding for streamNotificationMedia."""
        notification_id = "ABC123"
        hub_hex_id = "E5F6A7B8"
        origin_msg = _encode_string_field(1, hub_hex_id)
        request = _encode_string_field(1, notification_id) + _encode_embedded_message(2, origin_msg)
        # Should contain both strings
        assert b"ABC123" in request
        assert b"E5F6A7B8" in request


class TestNotificationMedia:
    def test_extracts_hub_alarm_images(self) -> None:
        media = media_pb2.NotificationMedia()
        media.hub_notification_media.images.add().url = "https://example.ajax.systems/one.jpg"
        media.hub_notification_media.images.add().url = "https://example.ajax.systems/two.jpg"

        assert _photo_urls_from_media(media) == (
            "https://example.ajax.systems/one.jpg",
            "https://example.ajax.systems/two.jpg",
        )

    def test_extracts_video_alert_frames(self) -> None:
        media = media_pb2.NotificationMedia()
        media.video_frames_media.frames.add().url = "https://example.ajax.systems/frame.jpg"

        assert _photo_urls_from_media(media) == ("https://example.ajax.systems/frame.jpg",)

    def test_photo_url_validation_rejects_lookalike_host(self) -> None:
        assert is_valid_photo_url("https://hubs-uploaded-resources.s3.amazonaws.com/photo.jpg")
        assert not is_valid_photo_url("https://hubs-uploaded-resources.attacker.com/photo.jpg")


class TestAlarmHistory:
    @staticmethod
    def _successful_history() -> find_notifications_pb2.FindNotificationsResponse:
        response = find_notifications_pb2.FindNotificationsResponse()
        notification = response.success.notifications.add()
        notification.id = "notification"
        notification.server_timestamp.seconds = 123
        notification.content.hub_notification_content.source.id = "motion-cam"
        notification.content.hub_notification_content.origin.hex_id = "hub-id"
        return response

    @staticmethod
    def _successful_media() -> stream_media_pb2.StreamNotificationMediaResponse:
        response = stream_media_pb2.StreamNotificationMediaResponse()
        response.success.media.hub_notification_media.images.add().url = (
            "https://hubs-uploaded-resources.s3.amazonaws.com/image.jpg"
        )
        return response

    @pytest.mark.asyncio
    async def test_reads_alarm_folder_and_associates_hub_source(self) -> None:
        client = MagicMock()
        client._session.get_call_metadata.return_value = []
        stream = _AsyncResponseStream(self._successful_media())
        stub = MagicMock()
        stub.findNotifications = AsyncMock(return_value=self._successful_history())
        stub.streamNotificationMedia.return_value = stream

        with patch(
            "custom_components.aegis_ajax.api.media."
            "notification_log_endpoints_pb2_grpc.NotificationLogServiceStub",
            return_value=stub,
        ):
            result = await MediaApi(client).get_recent_alarm_media("space-id")

        assert len(result) == 1
        assert result[0].device_id == "motion-cam"
        assert result[0].notification_id == "notification"
        assert result[0].timestamp == 123
        assert result[0].image_urls == (
            "https://hubs-uploaded-resources.s3.amazonaws.com/image.jpg",
        )
        history_request = stub.findNotifications.await_args.args[0]
        assert history_request.filter.folder == folder_pb2.FOLDER_ALARM
        assert history_request.filter.origin.space_id == "space-id"
        media_request = stub.streamNotificationMedia.call_args.args[0]
        assert media_request.notification_id == "notification"
        assert media_request.origin.hub_hex_id == "hub-id"
        assert stream.cancelled

    @pytest.mark.asyncio
    async def test_prioritizes_newest_camera_alerts_before_media_budget(self) -> None:
        """Other alarm sources must not crowd a camera out of the first ten streams."""
        client = MagicMock()
        client._session.get_call_metadata.return_value = []
        history = find_notifications_pb2.FindNotificationsResponse()

        oldest_camera = history.success.notifications.add()
        oldest_camera.id = "oldest-camera"
        oldest_camera.server_timestamp.seconds = 1
        oldest_camera.content.hub_notification_content.source.id = "motion-cam"
        oldest_camera.content.hub_notification_content.origin.hex_id = "hub-id"

        newest_camera = history.success.notifications.add()
        newest_camera.id = "newest-camera"
        newest_camera.server_timestamp.seconds = 100
        newest_camera.content.hub_notification_content.source.id = "motion-cam"
        newest_camera.content.hub_notification_content.origin.hex_id = "hub-id"

        # More than the entire media budget of unrelated, newer alarms.
        for index in range(11):
            other = history.success.notifications.add()
            other.id = f"other-{index}"
            other.server_timestamp.seconds = 200 + index
            other.content.hub_notification_content.source.id = f"other-device-{index}"
            other.content.hub_notification_content.origin.hex_id = "hub-id"

        stub = MagicMock()
        stub.findNotifications = AsyncMock(return_value=history)
        stub.streamNotificationMedia.side_effect = lambda *_args, **_kwargs: _AsyncResponseStream(
            self._successful_media()
        )

        with patch(
            "custom_components.aegis_ajax.api.media."
            "notification_log_endpoints_pb2_grpc.NotificationLogServiceStub",
            return_value=stub,
        ):
            result = await MediaApi(client).get_recent_alarm_media(
                "space-id",
                device_ids={"motion-cam"},
            )

        assert [alarm.notification_id for alarm in result] == ["newest-camera", "oldest-camera"]
        assert [
            service_call.args[0].notification_id
            for service_call in stub.streamNotificationMedia.call_args_list
        ] == [
            "newest-camera",
            "oldest-camera",
        ]
