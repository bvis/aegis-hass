"""Tests for importing historical Ajax alarm images."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.aegis_ajax.api.media import AlarmMedia
from custom_components.aegis_ajax.api.models import Device
from custom_components.aegis_ajax.const import DeviceState
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator


def _camera(device_id: str = "camera") -> Device:
    return Device(
        id=device_id,
        hub_id="hub",
        name="Hall camera",
        device_type="motion_cam",
        room_id=None,
        group_id=None,
        state=DeviceState.ONLINE,
        malfunctions=0,
        bypassed=False,
        statuses={},
        battery=None,
    )


class TestAlarmImageImport:
    @pytest.mark.asyncio
    async def test_pushed_alarm_uses_direct_media_stream(self) -> None:
        coordinator = object.__new__(AjaxCobrandedCoordinator)
        coordinator._alarm_import_lock = asyncio.Lock()
        coordinator._async_alarm_is_stored = AsyncMock(return_value=False)
        alarm = AlarmMedia("camera", "notification", ("one", "two", "three"), 123)
        coordinator._media_api = MagicMock()
        coordinator._media_api.get_alarm_media = AsyncMock(return_value=alarm)
        coordinator._async_save_alarm_media = AsyncMock(
            return_value={"notifications": 1, "images": 3}
        )

        with patch(
            "custom_components.aegis_ajax.coordinator.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            await coordinator._async_import_pushed_alarm_images(
                "notification", "camera", "hub", 123
            )

        sleep.assert_awaited_once_with(8)
        coordinator._media_api.get_alarm_media.assert_awaited_once_with(
            "notification", "hub", "camera", 123
        )
        coordinator._async_save_alarm_media.assert_awaited_once_with((alarm,))

    @pytest.mark.asyncio
    async def test_import_orders_photos_so_last_image_is_the_newest(self) -> None:
        coordinator = object.__new__(AjaxCobrandedCoordinator)
        coordinator._media_api = MagicMock()
        coordinator._media_api.get_recent_alarm_media = AsyncMock(
            return_value=(
                AlarmMedia("camera", "newer", ("newer.jpg",), timestamp=20),
                AlarmMedia("camera", "older", ("older-a.jpg", "older-b.jpg"), timestamp=10),
            )
        )
        coordinator._imported_alarm_notification_ids = set()
        coordinator._alarm_import_lock = asyncio.Lock()
        coordinator._last_alarm_backfill = {}
        coordinator.photo_revisions = {}
        coordinator.last_photo_urls = {}
        coordinator.devices = {"camera": _camera()}
        coordinator._async_download_and_save_alarm_image = AsyncMock(
            side_effect=[Path("older-a.jpg"), Path("older-b.jpg"), Path("newer.jpg")]
        )
        coordinator.async_update_listeners = MagicMock()
        coordinator.hass = MagicMock()

        with patch(
            "custom_components.aegis_ajax.coordinator.save_alarm_contact_sheet",
            new=AsyncMock(return_value=Path("last.jpg")),
        ) as contact_sheet:
            result = await coordinator.async_import_alarm_images("space")

        assert result == {"notifications": 2, "images": 3}
        assert coordinator._async_download_and_save_alarm_image.await_args_list == [
            call(
                coordinator.devices["camera"],
                "older-a.jpg",
                captured_at=dt_util.utc_from_timestamp(10),
                album_name=ANY,
                filename="01.jpg",
            ),
            call(
                coordinator.devices["camera"],
                "older-b.jpg",
                captured_at=dt_util.utc_from_timestamp(10),
                album_name=ANY,
                filename="02.jpg",
            ),
            call(
                coordinator.devices["camera"],
                "newer.jpg",
                captured_at=dt_util.utc_from_timestamp(20),
                album_name=ANY,
                filename="01.jpg",
            ),
        ]
        assert contact_sheet.await_args_list == [
            call(
                coordinator.hass,
                "Hall camera",
                [Path("older-a.jpg"), Path("older-b.jpg")],
                captured_at=dt_util.utc_from_timestamp(10),
            ),
            call(
                coordinator.hass,
                "Hall camera",
                [Path("newer.jpg")],
                captured_at=dt_util.utc_from_timestamp(20),
            ),
        ]
        assert coordinator.photo_revisions == {"camera": 2}
        coordinator.async_update_listeners.assert_called_once()

    @pytest.mark.asyncio
    async def test_download_saves_trusted_image_without_publishing_revision(self) -> None:
        coordinator = object.__new__(AjaxCobrandedCoordinator)
        coordinator.hass = MagicMock()
        coordinator.photo_revisions = {}
        device = _camera()

        response = AsyncMock(status=200)
        response.read = AsyncMock(return_value=b"image")
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=None)
        session = MagicMock()
        session.get.return_value = response

        with (
            patch(
                "custom_components.aegis_ajax.coordinator.async_get_clientsession",
                return_value=session,
            ),
            patch(
                "custom_components.aegis_ajax.coordinator.save_photo",
                new=AsyncMock(return_value=MagicMock()),
            ) as save_photo,
        ):
            saved_path = await coordinator._async_download_and_save_alarm_image(
                device, "https://hubs-uploaded-resources.s3.amazonaws.com/image.jpg"
            )

        assert saved_path is not None
        save_photo.assert_awaited_once_with(
            coordinator.hass,
            b"image",
            "camera",
            "Hall camera",
            captured_at=None,
            album_name=None,
            filename=None,
            update_last=True,
        )
        assert coordinator.photo_revisions == {}
