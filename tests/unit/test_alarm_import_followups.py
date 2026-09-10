"""Follow-ups left open when the alarm-image import merged (#495, #496).

Three properties, none of which changes what the feature does or what it asks
of Ajax:

1. The two in-process id memories are bounded. They are a fast path in front of
   the on-disk album check, so forgetting an id costs a directory stat and
   never a request.
2. One space sitting in its backfill cooldown no longer hides the results of
   the spaces that did run, and no longer makes the outcome depend on the order
   the spaces happen to be resolved in.
3. The media stream, which can take up to a minute, is no longer awaited while
   the import lock is held. Serialisation of the writes is preserved by
   re-checking under the lock.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.aegis_ajax.api.media import AlarmMedia
from custom_components.aegis_ajax.coordinator import _ALARM_ID_MEMORY
from tests.unit.test_alarm_image_regressions import _coordinator

if TYPE_CHECKING:
    from pathlib import Path


class TestTheIdMemoriesAreBounded:
    def test_remembering_more_alarms_than_the_cap_does_not_grow_forever(
        self, tmp_path: Path
    ) -> None:
        coordinator = _coordinator(tmp_path)

        for index in range(_ALARM_ID_MEMORY + 50):
            coordinator._remember_alarm_id(
                coordinator._imported_alarm_notification_ids, f"alarm-{index}"
            )

        assert len(coordinator._imported_alarm_notification_ids) == _ALARM_ID_MEMORY
        # The most recent are what a duplicate delivery would ask about.
        assert f"alarm-{_ALARM_ID_MEMORY + 49}" in coordinator._imported_alarm_notification_ids
        assert "alarm-0" not in coordinator._imported_alarm_notification_ids

    def test_the_push_dedupe_memory_is_bounded_too(self, tmp_path: Path) -> None:
        coordinator = _coordinator(tmp_path)

        for index in range(_ALARM_ID_MEMORY + 10):
            coordinator._remember_alarm_id(coordinator._seen_alarm_push_ids, f"push-{index}")

        assert len(coordinator._seen_alarm_push_ids) == _ALARM_ID_MEMORY

    def test_re_remembering_an_id_keeps_it_and_does_not_duplicate_it(self, tmp_path: Path) -> None:
        coordinator = _coordinator(tmp_path)
        store = coordinator._imported_alarm_notification_ids

        coordinator._remember_alarm_id(store, "alarm")
        for index in range(_ALARM_ID_MEMORY - 1):
            coordinator._remember_alarm_id(store, f"filler-{index}")
        coordinator._remember_alarm_id(store, "alarm")
        coordinator._remember_alarm_id(store, "one-more")

        assert len(store) == _ALARM_ID_MEMORY
        # Touched again, so it must outlive the filler that was added after it.
        assert "alarm" in store
        assert "filler-0" not in store

    @pytest.mark.asyncio
    async def test_a_forgotten_id_is_still_caught_on_disk_without_a_request(
        self, tmp_path: Path
    ) -> None:
        """Why eviction is safe: the authoritative answer is the album directory."""
        from custom_components.aegis_ajax.photo_storage import alarm_album_name

        coordinator = _coordinator(tmp_path)
        album = tmp_path / "ajax_photos" / "Hall camera" / alarm_album_name(123)
        album.mkdir(parents=True)
        (album / "preview.jpg").write_bytes(b"preview")
        (album / "01.jpg").write_bytes(b"frame")

        assert coordinator._imported_alarm_notification_ids == {}
        assert await coordinator._async_alarm_is_stored("camera", "forgotten", 123)


class TestOneCoolingSpaceDoesNotHideTheOthers:
    @staticmethod
    def _hass_with(coordinator: MagicMock) -> MagicMock:
        entry = MagicMock(runtime_data=coordinator)
        hass = MagicMock()
        hass.config_entries.async_entries = MagicMock(return_value=[entry])
        return hass

    @pytest.mark.asyncio
    async def test_results_of_the_spaces_that_ran_survive_a_cooling_sibling(self) -> None:
        from custom_components.aegis_ajax import _async_handle_refresh_alarm_images

        coordinator = MagicMock()
        coordinator._space_ids = ["cooling", "ready"]
        coordinator.async_import_alarm_images = AsyncMock(
            side_effect=[
                HomeAssistantError(translation_key="alarm_backfill_rate_limited"),
                {"notifications": 2, "images": 5},
            ]
        )

        result = await _async_handle_refresh_alarm_images(
            self._hass_with(coordinator), MagicMock(data={})
        )

        assert result == {
            "spaces": 2,
            "notifications": 2,
            "images": 5,
            "skipped": 1,
        }

    @pytest.mark.asyncio
    async def test_the_order_the_spaces_come_back_in_does_not_change_the_outcome(self) -> None:
        from custom_components.aegis_ajax import _async_handle_refresh_alarm_images

        coordinator = MagicMock()
        coordinator._space_ids = ["ready", "cooling"]
        coordinator.async_import_alarm_images = AsyncMock(
            side_effect=[
                {"notifications": 2, "images": 5},
                HomeAssistantError(translation_key="alarm_backfill_rate_limited"),
            ]
        )

        result = await _async_handle_refresh_alarm_images(
            self._hass_with(coordinator), MagicMock(data={})
        )

        assert result == {"spaces": 2, "notifications": 2, "images": 5, "skipped": 1}

    @pytest.mark.asyncio
    async def test_every_space_cooling_still_raises_so_the_user_sees_why(self) -> None:
        """A single-space install must keep getting the cooldown message."""
        from custom_components.aegis_ajax import _async_handle_refresh_alarm_images

        coordinator = MagicMock()
        coordinator._space_ids = ["only"]
        coordinator.async_import_alarm_images = AsyncMock(
            side_effect=HomeAssistantError(translation_key="alarm_backfill_rate_limited")
        )

        with pytest.raises(HomeAssistantError) as error:
            await _async_handle_refresh_alarm_images(
                self._hass_with(coordinator), MagicMock(data={})
            )

        assert error.value.translation_key == "alarm_backfill_rate_limited"

    @pytest.mark.asyncio
    async def test_an_unrelated_failure_is_not_swallowed_as_a_cooldown(self) -> None:
        from custom_components.aegis_ajax import _async_handle_refresh_alarm_images

        coordinator = MagicMock()
        coordinator._space_ids = ["a", "b"]
        coordinator.async_import_alarm_images = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            await _async_handle_refresh_alarm_images(
                self._hass_with(coordinator), MagicMock(data={})
            )


class TestTheMediaStreamIsNotAwaitedUnderTheLock:
    @pytest.mark.asyncio
    async def test_a_backfill_is_not_blocked_while_the_pushed_stream_is_open(
        self, tmp_path: Path
    ) -> None:
        coordinator = _coordinator(tmp_path)
        coordinator._last_alarm_backfill = {}
        stream_open = asyncio.Event()
        release_stream = asyncio.Event()

        async def slow_stream(*_args: object, **_kwargs: object) -> AlarmMedia | None:
            stream_open.set()
            await release_stream.wait()
            return None

        coordinator._media_api.get_alarm_media = AsyncMock(side_effect=slow_stream)
        coordinator._media_api.get_recent_alarm_media = AsyncMock(return_value=())

        with patch("custom_components.aegis_ajax.coordinator.asyncio.sleep", new=AsyncMock()):
            pushed = asyncio.create_task(
                coordinator._async_import_pushed_alarm_images("alarm", "camera", "hub", 123)
            )
            await asyncio.wait_for(stream_open.wait(), timeout=1)
            # The stream is open. A manual backfill must not wait on it.
            await asyncio.wait_for(coordinator.async_import_alarm_images("space"), timeout=1)
            release_stream.set()
            await asyncio.wait_for(pushed, timeout=1)

        coordinator._media_api.get_recent_alarm_media.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_album_stored_while_the_stream_was_open_is_not_written_twice(
        self, tmp_path: Path
    ) -> None:
        """The re-check under the lock is what replaces holding it throughout."""
        coordinator = _coordinator(tmp_path)
        coordinator._media_api.get_alarm_media = AsyncMock(
            return_value=AlarmMedia("camera", "alarm", ("a.jpg",), 123)
        )
        coordinator._async_save_alarm_media = AsyncMock(
            return_value={"notifications": 1, "images": 1}
        )
        stored: list[bool] = [False, True]
        coordinator._async_alarm_is_stored = AsyncMock(side_effect=stored)

        with patch("custom_components.aegis_ajax.coordinator.asyncio.sleep", new=AsyncMock()):
            await coordinator._async_import_pushed_alarm_images("alarm", "camera", "hub", 123)

        # Free before the stream, taken by the time the lock was acquired.
        assert coordinator._async_alarm_is_stored.await_count == 2
        coordinator._async_save_alarm_media.assert_not_awaited()
