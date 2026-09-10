"""Regressions for complete alarm sequences and chronological camera previews."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification import (
    origin_id_pb2,
)
from systems.ajax.api.mobile.v2.notificationlog import stream_media_pb2

from custom_components.aegis_ajax.api.media import AlarmMedia, MediaApi
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.photo_storage import (
    _overlay_timestamp,
    load_last_photo,
    save_alarm_contact_sheet,
    save_photo,
)
from tests.unit.test_alarm_image_import import _camera
from tests.unit.test_camera import AjaxCamera
from tests.unit.test_photo_storage import _make_jpeg


def _coordinator(tmp_path: Path) -> AjaxCobrandedCoordinator:
    coordinator = object.__new__(AjaxCobrandedCoordinator)
    coordinator.hass = MagicMock()
    coordinator.hass.config.media_dirs = {"local": str(tmp_path)}
    coordinator.devices = {"camera": _camera()}
    coordinator.rooms = {}
    coordinator.hub_registry_id = MagicMock(return_value=None)
    coordinator._media_api = MagicMock()
    coordinator._alarm_import_lock = asyncio.Lock()
    coordinator._last_alarm_backfill = {}
    coordinator._seen_alarm_push_ids = set()
    coordinator._imported_alarm_notification_ids = set()
    coordinator.photo_revisions = {}
    coordinator.last_photo_urls = {}
    coordinator.async_update_listeners = MagicMock()
    return coordinator


class _MediaStream:
    def __init__(self, statuses: list[list[int]], *, times_out: bool = False) -> None:
        self._statuses = iter(statuses)
        self._times_out = times_out
        self.cancelled = False

    def __aiter__(self) -> _MediaStream:
        return self

    async def __anext__(self) -> stream_media_pb2.StreamNotificationMediaResponse:
        statuses = next(self._statuses, None)
        if statuses is None:
            if self._times_out:
                raise TimeoutError
            raise StopAsyncIteration
        response = stream_media_pb2.StreamNotificationMediaResponse()
        for index, status in enumerate(statuses):
            image = response.success.media.hub_notification_media.images.add()
            image.status = status
            if status == 2:
                image.url = f"https://example.ajax.systems/{index}.jpg"
        return response

    def cancel(self) -> bool:
        self.cancelled = True
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("statuses", "times_out", "expected_count"),
    [
        ([[2, 1, 1], [2, 2, 2]], False, 3),
        ([[2, 1, 1]], True, 0),
        ([[2, 1, 1]], False, 0),
        ([[2, 3, 2]], False, 2),
        ([[2]], False, 1),
        ([[2, 2]], False, 2),
    ],
)
async def test_stream_waits_for_all_frames(
    statuses: list[list[int]], times_out: bool, expected_count: int
) -> None:
    stream = _MediaStream(statuses, times_out=times_out)
    stub = MagicMock()
    stub.streamNotificationMedia.return_value = stream
    result = await MediaApi(MagicMock())._get_notification_media_urls(
        stub,
        notification_id="notification",
        origin=origin_id_pb2.NotificationOriginId(hub_hex_id="hub"),
        metadata=[],
    )
    assert len(result) == expected_count
    assert stream.cancelled


@pytest.mark.asyncio
async def test_partial_download_remains_retryable(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator._media_api.get_recent_alarm_media = AsyncMock(
        return_value=(AlarmMedia("camera", "alarm", ("one", "two", "three"), 100),)
    )
    coordinator._async_download_and_save_alarm_image = AsyncMock(
        side_effect=[Path("one"), None, None, Path("one"), Path("two"), Path("three")]
    )
    with (
        patch("custom_components.aegis_ajax.coordinator.time.monotonic", side_effect=[1000, 1300]),
        patch(
            "custom_components.aegis_ajax.coordinator.save_alarm_contact_sheet",
            new=AsyncMock(return_value=Path("last.jpg")),
        ) as compose,
    ):
        first = await coordinator.async_import_alarm_images("space")
        assert first["notifications"] == 0
        assert "alarm" not in coordinator._imported_alarm_notification_ids
        compose.assert_not_awaited()
        second = await coordinator.async_import_alarm_images("space")
        assert second == {"notifications": 1, "images": 3}
        compose.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_preview_remains_retryable(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator._media_api.get_recent_alarm_media = AsyncMock(
        return_value=(AlarmMedia("camera", "alarm", ("one",), 100),)
    )
    coordinator._async_download_and_save_alarm_image = AsyncMock(return_value=Path("one"))
    with patch(
        "custom_components.aegis_ajax.coordinator.save_alarm_contact_sheet",
        new=AsyncMock(return_value=None),
    ):
        result = await coordinator.async_import_alarm_images("space")
    assert result["notifications"] == 0
    assert "alarm" not in coordinator._imported_alarm_notification_ids
    assert not coordinator.photo_revisions


@pytest.mark.asyncio
async def test_camera_read_during_composition_does_not_cache_stale_preview(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator._media_api.get_recent_alarm_media = AsyncMock(
        return_value=(
            AlarmMedia("camera", "alarm", ("https://example.ajax.systems/one.jpg",), 200),
        )
    )
    old = await save_photo(
        coordinator.hass,
        _make_jpeg(),
        "camera",
        "Hall camera",
        captured_at=dt_util.utc_from_timestamp(100),
    )
    assert old is not None
    old_bytes = old.read_bytes()
    camera = AjaxCamera(coordinator, "camera", "hub", "motion_cam")
    camera.hass = coordinator.hass
    assert await camera.async_camera_image() == old_bytes

    response = AsyncMock(status=200)
    response.read = AsyncMock(return_value=_make_jpeg(color=(255, 0, 0)))
    response.__aenter__ = AsyncMock(return_value=response)
    session = MagicMock()
    session.get.return_value = response

    async def compose(*args: object, **kwargs: object) -> Path | None:
        assert await camera.async_camera_image() == old_bytes
        return await save_alarm_contact_sheet(*args, **kwargs)

    with (
        patch(
            "custom_components.aegis_ajax.coordinator.async_get_clientsession", return_value=session
        ),
        patch(
            "custom_components.aegis_ajax.coordinator.save_alarm_contact_sheet", side_effect=compose
        ),
    ):
        await coordinator.async_import_alarm_images("space")

    current = await load_last_photo(coordinator.hass, "Hall camera")
    assert current != old_bytes
    assert await camera.async_camera_image() == current


@pytest.mark.asyncio
async def test_backfill_preserves_newer_preview_after_restart(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    newer = await save_photo(
        coordinator.hass,
        _make_jpeg(),
        "camera",
        "Hall camera",
        captured_at=dt_util.utc_from_timestamp(200),
    )
    assert newer is not None
    coordinator = _coordinator(tmp_path)  # No in-memory chronology survives a restart.
    older = await save_photo(
        coordinator.hass,
        _make_jpeg(color=(255, 0, 0)),
        "camera",
        "Hall camera",
        captured_at=dt_util.utc_from_timestamp(100),
        album_name="old",
        update_last=False,
    )
    assert older is not None
    preview = await save_alarm_contact_sheet(
        coordinator.hass, "Hall camera", [older], captured_at=dt_util.utc_from_timestamp(100)
    )
    assert preview is not None
    assert (older.parent / "preview.jpg").is_file()
    assert await load_last_photo(coordinator.hass, "Hall camera") == newer.read_bytes()


@pytest.mark.asyncio
async def test_backfill_preserves_phod_capture(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    capture = await save_photo(coordinator.hass, _make_jpeg(), "camera", "Hall camera")
    assert capture is not None
    old = await save_photo(
        coordinator.hass,
        _make_jpeg(color=(255, 0, 0)),
        "camera",
        "Hall camera",
        album_name="old",
        update_last=False,
    )
    assert old is not None
    await save_alarm_contact_sheet(
        coordinator.hass, "Hall camera", [old], captured_at=dt_util.utc_from_timestamp(100)
    )
    assert await load_last_photo(coordinator.hass, "Hall camera") == capture.read_bytes()


def test_overlay_uses_ha_timezone() -> None:
    with (
        patch.object(dt_util, "DEFAULT_TIME_ZONE", ZoneInfo("Europe/Madrid")),
        patch("PIL.ImageDraw.ImageDraw.text") as draw_text,
    ):
        _overlay_timestamp(_make_jpeg(), dt_util.parse_datetime("2026-09-09T18:00:00+00:00"))
    assert draw_text.call_args.args[1] == "2026-09-09 20:00:00"


@pytest.mark.asyncio
async def test_album_uses_ha_timezone(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator._media_api.get_recent_alarm_media = AsyncMock(
        return_value=(AlarmMedia("camera", "alarm", ("one",), 1788976800),)
    )
    coordinator._async_download_and_save_alarm_image = AsyncMock(return_value=None)
    with patch.object(dt_util, "DEFAULT_TIME_ZONE", ZoneInfo("Europe/Madrid")):
        await coordinator.async_import_alarm_images("space")
    album_name = coordinator._async_download_and_save_alarm_image.await_args.kwargs["album_name"]
    assert album_name.startswith("2026-09-09_20-00-00")


@pytest.mark.asyncio
async def test_overlapping_imports_publish_an_alarm_once(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator._media_api.get_recent_alarm_media = AsyncMock(
        return_value=(AlarmMedia("camera", "alarm", ("one",), 100),)
    )
    coordinator._media_api.get_alarm_media = AsyncMock(
        return_value=AlarmMedia("camera", "alarm", ("one",), 100)
    )
    original_sleep = asyncio.sleep

    async def skip_delay(_seconds: float) -> None:
        await original_sleep(0)

    async def download(*_args: object, **_kwargs: object) -> Path:
        await asyncio.sleep(0)
        return Path("one")

    coordinator._async_download_and_save_alarm_image = AsyncMock(side_effect=download)
    with (
        patch("custom_components.aegis_ajax.coordinator.asyncio.sleep", side_effect=skip_delay),
        patch(
            "custom_components.aegis_ajax.coordinator.save_alarm_contact_sheet",
            new=AsyncMock(return_value=Path("last.jpg")),
        ),
    ):
        results = await asyncio.gather(
            coordinator.async_import_alarm_images("space"),
            coordinator._async_import_pushed_alarm_images("alarm", "camera", "hub", 100),
        )
    assert results[0]["notifications"] == 1
    assert coordinator._async_download_and_save_alarm_image.await_count == 1


@pytest.mark.asyncio
async def test_corrupt_frame_does_not_publish_partial_preview(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"invalid JPEG")
    valid = tmp_path / "valid.jpg"
    valid.write_bytes(_make_jpeg())
    result = await save_alarm_contact_sheet(coordinator.hass, "Hall camera", [broken, valid])
    assert result is None
    assert await load_last_photo(coordinator.hass, "Hall camera") is None


@pytest.mark.asyncio
async def test_backfill_after_fcm_keeps_latest_and_archives_older(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    newer = AlarmMedia("camera", "newer", ("newer",), 200)
    older = AlarmMedia("camera", "older", ("older",), 100)
    coordinator._media_api.get_recent_alarm_media = AsyncMock(return_value=(newer, older))
    coordinator._media_api.get_alarm_media = AsyncMock(return_value=newer)

    async def download(device: object, url: str, **kwargs: object) -> Path | None:
        return await save_photo(
            coordinator.hass,
            _make_jpeg(color=(255, 0, 0) if url == "newer" else (0, 255, 0)),
            "camera",
            "Hall camera",
            update_last=False,
            **kwargs,
        )

    coordinator._async_download_and_save_alarm_image = AsyncMock(side_effect=download)
    with patch("custom_components.aegis_ajax.coordinator.asyncio.sleep", new=AsyncMock()):
        await coordinator._async_import_pushed_alarm_images("newer", "camera", "hub", 200)
    expected = await load_last_photo(coordinator.hass, "Hall camera")
    result = await coordinator.async_import_alarm_images("space")
    assert result == {"notifications": 1, "images": 1}
    assert await load_last_photo(coordinator.hass, "Hall camera") == expected
    assert len(list(tmp_path.rglob("preview.jpg"))) == 2
