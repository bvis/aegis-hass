"""Photo storage with timestamp overlay for Ajax Security."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import time
from math import ceil, sqrt
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PHOTOS_BASE_DIR = "ajax_photos"
_CONTACT_SHEET_GAP = 4
_CONTACT_SHEET_MAX_CELL_SIZE = (640, 480)
_LATEST_PHOTO_LOCK = Lock()


def _sanitize_name(name: str) -> str:
    """Sanitize device name for use as directory name."""
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip()


def alarm_album_name(timestamp: float) -> str:
    """Use the same event-time directory for push and historical imports."""
    captured_at = dt_util.utc_from_timestamp(timestamp) if timestamp else dt_util.now()
    return dt_util.as_local(captured_at).strftime("%Y-%m-%d_%H-%M-%S-%f")


async def alarm_album_exists(hass: HomeAssistant, device_name: str, timestamp: float) -> bool:
    """Skip network retrieval only for completed albums, not partial downloads."""
    if not timestamp:
        return False

    def _exists() -> bool:
        media_dir = Path(hass.config.media_dirs.get("local", "/media"))
        album = (
            media_dir / PHOTOS_BASE_DIR / _sanitize_name(device_name) / alarm_album_name(timestamp)
        )
        return (album / "preview.jpg").is_file() and any(
            photo.name != "preview.jpg" for photo in album.glob("*.jpg")
        )

    return await asyncio.to_thread(_exists)


def _overlay_timestamp(image_bytes: bytes, captured_at: datetime | None = None) -> bytes:
    """Add the image capture time, or now when it is unknown, as an overlay."""
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        timestamp = dt_util.as_local(captured_at or dt_util.now()).strftime("%Y-%m-%d %H:%M:%S")
        font = ImageFont.load_default(size=11)

        text_bbox = draw.textbbox((0, 0), timestamp, font=font)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]
        padding = 2
        x = img.width - text_w - padding * 2 - 4
        y = img.height - text_h - padding * 2 - 4
        draw.rectangle(
            [(x, y), (x + text_w + padding * 2, y + text_h + padding * 2)],
            fill=(0, 0, 0, 160),
        )
        draw.text((x + padding, y + padding), timestamp, fill=(255, 255, 255, 255), font=font)
        img = Image.alpha_composite(img, overlay).convert("RGB")

        output = io.BytesIO()
        img.save(output, format="JPEG", quality=90)
        return output.getvalue()
    except Exception:
        _LOGGER.debug("Failed to overlay timestamp, returning original image")
        return image_bytes


def _publish_latest_photo(device_dir: Path, image: bytes, captured_at: datetime) -> bool:
    """Atomically publish a preview only if it is not older than the current one.

    Persist capture time in last.jpg's mtime, including for PhOD. Comparison
    survives restart and is serialized across executor threads. Legacy files
    without a capture mtime conservatively retain their original write time.
    """
    timestamp = captured_at.timestamp()
    last_path = device_dir / "last.jpg"
    with _LATEST_PHOTO_LOCK:
        if last_path.exists() and last_path.stat().st_mtime > timestamp:
            return False
        pending = device_dir / ".last.jpg.tmp"
        try:
            pending.write_bytes(image)
            os.utime(pending, (timestamp, timestamp))
            pending.replace(last_path)
        finally:
            pending.unlink(missing_ok=True)
    return True


async def save_photo(
    hass: HomeAssistant,
    image_bytes: bytes,
    device_id: str,
    device_name: str,
    *,
    captured_at: datetime | None = None,
    album_name: str | None = None,
    filename: str | None = None,
    update_last: bool = True,
) -> Path | None:
    """Save a photo with its capture time overlay to the media directory."""
    photo_time = captured_at or dt_util.now()

    def _do_save() -> Path | None:
        try:
            media_dir = Path(hass.config.media_dirs.get("local", "/media"))
            device_dir = media_dir / PHOTOS_BASE_DIR / _sanitize_name(device_name)
            destination_dir = device_dir / _sanitize_name(album_name) if album_name else device_dir
            destination_dir.mkdir(parents=True, exist_ok=True)

            stamped = _overlay_timestamp(image_bytes, captured_at)
            # An alarm notification can carry several images. Microseconds
            # prevent frames saved by one import from overwriting each other.
            photo_filename = filename or dt_util.now().strftime("%Y-%m-%d_%H-%M-%S-%f") + ".jpg"
            filepath = destination_dir / photo_filename
            filepath.write_bytes(stamped)
            _LOGGER.debug("Photo saved: %s (%d bytes)", filepath, len(stamped))

            # On-demand images remain the Camera's latest single image. Alarm
            # imports set the preview only after all sequence frames are saved.
            if update_last:
                _publish_latest_photo(device_dir, stamped, photo_time)

            return filepath
        except Exception:
            _LOGGER.exception("Failed to save photo")
            return None

    return await asyncio.to_thread(_do_save)


async def save_alarm_contact_sheet(
    hass: HomeAssistant,
    device_name: str,
    photo_paths: Sequence[Path],
    *,
    captured_at: datetime | None = None,
) -> Path | None:
    """Compose one alarm's frames into the Camera entity's `last.jpg`.

    Individual frame files are kept for Media Source. A normal Camera entity
    can only return one image, so the contact sheet exposes the full sequence
    in a native HA camera card.
    """
    photo_time = captured_at or dt_util.now()

    def _do_save() -> Path | None:
        try:
            from PIL import Image  # noqa: PLC0415

            frames: list[Image.Image] = []
            for photo_path in photo_paths:
                try:
                    with Image.open(photo_path) as photo:
                        frame = photo.convert("RGB")
                        frame.thumbnail(_CONTACT_SHEET_MAX_CELL_SIZE)
                        frames.append(frame)
                except Exception:
                    _LOGGER.debug("Could not include alarm frame in contact sheet", exc_info=True)
                    return None

            if not frames:
                return None

            media_dir = Path(hass.config.media_dirs.get("local", "/media"))
            device_dir = media_dir / PHOTOS_BASE_DIR / _sanitize_name(device_name)
            album_preview = photo_paths[0].parent / "preview.jpg"
            last_path = device_dir / "last.jpg"

            # A one-frame alarm has an album preview and remains a normal
            # camera image instead of being needlessly recompressed.
            if len(frames) == 1:
                shutil.copyfile(photo_paths[0], album_preview)
                published = _publish_latest_photo(
                    device_dir, album_preview.read_bytes(), photo_time
                )
                return last_path if published else album_preview

            columns = ceil(sqrt(len(frames)))
            rows = ceil(len(frames) / columns)
            cell_width = max(frame.width for frame in frames)
            cell_height = max(frame.height for frame in frames)
            width = columns * cell_width + (columns - 1) * _CONTACT_SHEET_GAP
            height = rows * cell_height + (rows - 1) * _CONTACT_SHEET_GAP
            contact_sheet = Image.new("RGB", (width, height), color=(24, 24, 24))

            for index, frame in enumerate(frames):
                row, column = divmod(index, columns)
                x = column * (cell_width + _CONTACT_SHEET_GAP)
                y = row * (cell_height + _CONTACT_SHEET_GAP)
                x += (cell_width - frame.width) // 2
                y += (cell_height - frame.height) // 2
                contact_sheet.paste(frame, (x, y))

            contact_sheet.save(album_preview, format="JPEG", quality=90)
            published = _publish_latest_photo(device_dir, album_preview.read_bytes(), photo_time)
            _LOGGER.debug("Saved %d-frame alarm contact sheet: %s", len(frames), last_path)
            return last_path if published else album_preview
        except Exception:
            _LOGGER.debug("Could not save alarm contact sheet", exc_info=True)
            return None

    return await asyncio.to_thread(_do_save)


async def load_last_photo(
    hass: HomeAssistant,
    device_name: str,
) -> bytes | None:
    """Load the last saved photo for a device."""

    def _do_load() -> bytes | None:
        try:
            media_dir = Path(hass.config.media_dirs.get("local", "/media"))
            last_path = media_dir / PHOTOS_BASE_DIR / _sanitize_name(device_name) / "last.jpg"
            if last_path.exists():
                return last_path.read_bytes()
        except Exception:
            _LOGGER.debug("Failed to load last photo")
        return None

    return await asyncio.to_thread(_do_load)


async def cleanup_old_photos(
    hass: HomeAssistant,
    retention_days: int = 30,
    max_photos_per_device: int = 100,
) -> list[str]:
    """Delete photos older than retention period or exceeding max count per device."""

    def _do_cleanup() -> list[str]:
        deleted: list[str] = []
        try:
            media_dir = Path(hass.config.media_dirs.get("local", "/media"))
            photos_dir = media_dir / PHOTOS_BASE_DIR
            if not photos_dir.exists():
                return deleted

            cutoff = time.time() - (retention_days * 86400)

            for device_dir in photos_dir.iterdir():
                if not device_dir.is_dir():
                    continue

                # A historical alarm is a directory containing all its frames
                # and a preview. Treat it as one retention unit so a gallery
                # never loses just one image from a sequence.
                candidates: list[tuple[float, Path, int]] = []
                for entry in device_dir.iterdir():
                    if entry.is_dir():
                        frame_count = sum(
                            1
                            for photo in entry.iterdir()
                            if photo.is_file()
                            and photo.suffix == ".jpg"
                            and photo.name != "preview.jpg"
                        )
                        if frame_count:
                            candidates.append((entry.stat().st_mtime, entry, frame_count))
                    elif entry.is_file() and entry.suffix == ".jpg" and entry.name != "last.jpg":
                        candidates.append((entry.stat().st_mtime, entry, 1))

                kept_photos = 0
                for modified_at, candidate, photo_count in sorted(
                    candidates, key=lambda item: item[0], reverse=True
                ):
                    should_delete = modified_at < cutoff or (
                        max_photos_per_device > 0
                        and kept_photos + photo_count > max_photos_per_device
                    )
                    if should_delete:
                        if candidate.is_dir():
                            shutil.rmtree(candidate)
                        else:
                            candidate.unlink()
                        deleted.append(str(candidate))
                    else:
                        kept_photos += photo_count

        except Exception:
            _LOGGER.exception("Error cleaning up old photos")
        return deleted

    return await asyncio.to_thread(_do_cleanup)
