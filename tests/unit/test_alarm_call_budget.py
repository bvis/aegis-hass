"""Network budgets for push imports and manual alarm backfill."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.aegis_ajax.api.media import MediaApi
from tests.unit.test_alarm_image_regressions import _coordinator
from tests.unit.test_media import TestAlarmHistory as AlarmHistoryFixtures
from tests.unit.test_media import _AsyncResponseStream

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_push_opens_one_stream_without_searching_history() -> None:
    client = MagicMock()
    client._session.get_call_metadata.return_value = []
    stub = MagicMock()
    stub.findNotifications = AsyncMock()
    stub.streamNotificationMedia.return_value = _AsyncResponseStream(
        AlarmHistoryFixtures._successful_media()
    )
    with patch(
        "custom_components.aegis_ajax.api.media."
        "notification_log_endpoints_pb2_grpc.NotificationLogServiceStub",
        return_value=stub,
    ):
        media = await MediaApi(client).get_alarm_media("notification", "hub", "camera", 123)
    assert media is not None and media.timestamp == 123
    stub.findNotifications.assert_not_awaited()
    stub.streamNotificationMedia.assert_called_once()
    request = stub.streamNotificationMedia.call_args.args[0]
    assert request.notification_id == "notification"
    assert request.origin.hub_hex_id == "hub"


@pytest.mark.asyncio
async def test_backfill_cooldown_blocks_network_and_is_per_space(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator._last_alarm_backfill = {}
    coordinator._media_api.get_recent_alarm_media = AsyncMock(return_value=())
    with patch("custom_components.aegis_ajax.coordinator.time.monotonic", return_value=1000):
        await coordinator.async_import_alarm_images("space")
        with pytest.raises(HomeAssistantError) as error:
            await coordinator.async_import_alarm_images("space")
        assert error.value.translation_key == "alarm_backfill_rate_limited"
        assert error.value.translation_placeholders == {"seconds": "300"}
        assert coordinator._media_api.get_recent_alarm_media.await_count == 1
        await coordinator.async_import_alarm_images("other-space")
    with patch("custom_components.aegis_ajax.coordinator.time.monotonic", return_value=1300):
        await coordinator.async_import_alarm_images("space")
    assert coordinator._media_api.get_recent_alarm_media.await_count == 3


@pytest.mark.asyncio
async def test_stored_album_skips_media_stream_after_restart(tmp_path: Path) -> None:
    from custom_components.aegis_ajax.photo_storage import alarm_album_name

    coordinator = _coordinator(tmp_path)
    coordinator._last_alarm_backfill = {}
    album = tmp_path / "ajax_photos" / "Hall camera" / alarm_album_name(123)
    album.mkdir(parents=True)
    (album / "preview.jpg").write_bytes(b"preview")
    (album / "01.jpg").write_bytes(b"frame")
    history = AlarmHistoryFixtures._successful_history()
    history.success.notifications[0].content.hub_notification_content.source.id = "camera"
    client = MagicMock()
    client._session.get_call_metadata.return_value = []
    coordinator._media_api = MediaApi(client)
    stub = MagicMock()
    stub.findNotifications = AsyncMock(return_value=history)
    with patch(
        "custom_components.aegis_ajax.api.media."
        "notification_log_endpoints_pb2_grpc.NotificationLogServiceStub",
        return_value=stub,
    ):
        result = await coordinator.async_import_alarm_images("space")
    assert result == {"notifications": 0, "images": 0}
    stub.findNotifications.assert_awaited_once()
    stub.streamNotificationMedia.assert_not_called()


@pytest.mark.asyncio
async def test_push_failure_does_not_reopen_stream(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator._media_api.get_alarm_media = AsyncMock(return_value=None)
    coordinator._media_api.get_recent_alarm_media = AsyncMock()
    with patch("custom_components.aegis_ajax.coordinator.asyncio.sleep", new=AsyncMock()):
        await coordinator._async_import_pushed_alarm_images("notification", "camera", "hub", 123)
    coordinator._media_api.get_alarm_media.assert_awaited_once()
    coordinator._media_api.get_recent_alarm_media.assert_not_awaited()


@pytest.mark.asyncio
async def test_incomplete_album_does_not_skip_recovery(tmp_path: Path) -> None:
    from custom_components.aegis_ajax.photo_storage import alarm_album_name

    coordinator = _coordinator(tmp_path)
    album = tmp_path / "ajax_photos" / "Hall camera" / alarm_album_name(123)
    album.mkdir(parents=True)
    (album / "01.jpg").write_bytes(b"frame")
    assert not await coordinator._async_alarm_is_stored("camera", "notification", 123)
    (album / "preview.jpg").write_bytes(b"complete preview")
    assert await coordinator._async_alarm_is_stored("camera", "notification", 123)


@pytest.mark.asyncio
async def test_push_scheduler_deduplicates_and_validates_source(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator.spaces = {"space": MagicMock(hub_id="hub")}
    coordinator._alarm_import_tasks = {}
    coordinator._async_import_pushed_alarm_images = AsyncMock()
    coordinator.hass.async_create_task.side_effect = asyncio.create_task
    coordinator.schedule_alarm_image_import("space", "wrong-hub", "camera", "other", 123)
    coordinator.schedule_alarm_image_import("space", "unknown", "missing-camera", "hub", 123)
    coordinator.schedule_alarm_image_import("missing-space", "unknown", "camera", "hub", 123)
    coordinator.hass.async_create_task.assert_not_called()
    coordinator.schedule_alarm_image_import("space", "alarm", "camera", "hub", 123)
    coordinator.schedule_alarm_image_import("space", "alarm", "camera", "hub", 123)
    await coordinator._alarm_import_tasks["alarm"]
    # Finished/failed notifications must not reopen a stream on duplicate delivery.
    coordinator.schedule_alarm_image_import("space", "alarm", "camera", "hub", 123)
    coordinator._async_import_pushed_alarm_images.assert_awaited_once_with(
        "alarm", "camera", "hub", 123
    )
    assert not coordinator._alarm_import_tasks


@pytest.mark.parametrize("missing", [None, "timestamp", "source", "hub", "id", "content"])
def test_push_photo_context_requires_complete_matching_hub_notification(
    missing: str | None,
) -> None:
    from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.service.push_notification_dispatch import (  # noqa: E501
        event_pb2,
    )

    from custom_components.aegis_ajax.notification_event_parser import extract_alarm_photo_context

    dispatch = event_pb2.PushNotificationDispatchEvent()
    notification = dispatch.notification
    notification.id = "alarm"
    notification.server_timestamp.seconds = 123
    notification.server_timestamp.nanos = 500_000_000
    content = notification.content.hub_notification_content
    content.source.id = "camera"
    content.origin.hex_id = "hub"
    if missing == "timestamp":
        notification.ClearField("server_timestamp")
    elif missing == "source":
        content.ClearField("source")
    elif missing == "hub":
        content.ClearField("origin")
    elif missing == "id":
        notification.id = "another-alarm"
    elif missing == "content":
        notification.ClearField("content")
    result = extract_alarm_photo_context(dispatch.SerializeToString(), "alarm")
    assert result == (("camera", "hub", 123.5) if missing is None else None)


def test_malformed_push_has_no_photo_context() -> None:
    from custom_components.aegis_ajax.notification_event_parser import extract_alarm_photo_context

    assert extract_alarm_photo_context(b"\xff", "alarm") is None
