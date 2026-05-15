"""Tests for photo storage."""

from __future__ import annotations

import io
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from custom_components.aegis_ajax.photo_storage import (
    PHOTOS_BASE_DIR,
    _overlay_timestamp,
    _sanitize_name,
    cleanup_old_photos,
    load_last_photo,
    save_photo,
)


def _make_jpeg(width: int = 32, height: int = 32) -> bytes:
    """Build a small valid JPEG as a fixture for filesystem tests."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(64, 64, 64)).save(buf, format="JPEG")
    return buf.getvalue()


def _hass_with_media(tmp_path: Path) -> MagicMock:
    """`hass` stub whose `.config.media_dirs["local"]` points at tmp_path."""
    hass = MagicMock()
    hass.config.media_dirs = {"local": str(tmp_path)}
    return hass


class TestSanitizeName:
    def test_simple_name(self) -> None:
        # Accented characters are alphanumeric in Python (isalnum() returns True)
        assert _sanitize_name("HALLWAY") == "HALLWAY"

    def test_name_with_spaces(self) -> None:
        assert _sanitize_name("Front Door") == "Front Door"

    def test_name_with_special_chars(self) -> None:
        result = _sanitize_name("Device <1> / test")
        assert "<" not in result
        assert "/" not in result


class TestOverlayTimestamp:
    def test_overlay_returns_bytes(self) -> None:
        result = _overlay_timestamp(_make_jpeg(320, 240))
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_overlay_on_invalid_image_returns_original(self) -> None:
        original = b"not a real image"
        result = _overlay_timestamp(original)
        assert result == original


class TestSavePhoto:
    @pytest.mark.asyncio
    async def test_writes_timestamped_file_and_last_jpg(self, tmp_path: Path) -> None:
        hass = _hass_with_media(tmp_path)
        result = await save_photo(hass, _make_jpeg(), "dev-1", "Front Door")

        assert result is not None
        device_dir = tmp_path / PHOTOS_BASE_DIR / "Front Door"
        # Stamped file uses YYYY-MM-DD_HH-MM-SS.jpg pattern
        jpgs = sorted(p for p in device_dir.iterdir() if p.suffix == ".jpg")
        assert (device_dir / "last.jpg") in jpgs
        # Exactly two files: the timestamped one and last.jpg
        assert len(jpgs) == 2
        assert result.parent == device_dir
        # Both files are non-empty
        for p in jpgs:
            assert p.stat().st_size > 0

    @pytest.mark.asyncio
    async def test_sanitizes_device_name_into_directory(self, tmp_path: Path) -> None:
        hass = _hass_with_media(tmp_path)
        # "/" and "<" are not allowed in a path component → replaced with "_"
        result = await save_photo(hass, _make_jpeg(), "dev-1", "Hall/Way<1>")

        assert result is not None
        # Verify the dir name has no path-separator characters left
        sanitized_dir = result.parent.name
        assert "/" not in sanitized_dir
        assert "<" not in sanitized_dir

    @pytest.mark.asyncio
    async def test_returns_none_when_filesystem_write_fails(self, tmp_path: Path) -> None:
        hass = _hass_with_media(tmp_path)
        # Patch Path.write_bytes to blow up — the broad except in _do_save must
        # swallow it and return None instead of crashing the caller.
        with patch.object(Path, "write_bytes", side_effect=OSError("disk full")):
            result = await save_photo(hass, _make_jpeg(), "dev-1", "Front Door")
        assert result is None

    @pytest.mark.asyncio
    async def test_falls_back_to_media_default_when_local_missing(self, tmp_path: Path) -> None:
        # `media_dirs` without a "local" key should fall back to "/media" per
        # the .get(..., "/media") default in _do_save. Patch mkdir to raise so
        # we exercise the fallback path without touching the real /media tree.
        hass = MagicMock()
        hass.config.media_dirs = {}
        with patch.object(Path, "mkdir", side_effect=OSError("read-only")):
            result = await save_photo(hass, _make_jpeg(), "dev-1", "Front Door")
        assert result is None


class TestLoadLastPhoto:
    @pytest.mark.asyncio
    async def test_returns_bytes_when_last_jpg_exists(self, tmp_path: Path) -> None:
        hass = _hass_with_media(tmp_path)
        device_dir = tmp_path / PHOTOS_BASE_DIR / "Garage"
        device_dir.mkdir(parents=True)
        payload = _make_jpeg(16, 16)
        (device_dir / "last.jpg").write_bytes(payload)

        result = await load_last_photo(hass, "Garage")
        assert result == payload

    @pytest.mark.asyncio
    async def test_returns_none_when_directory_missing(self, tmp_path: Path) -> None:
        hass = _hass_with_media(tmp_path)
        result = await load_last_photo(hass, "Nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_read_raises(self, tmp_path: Path) -> None:
        hass = _hass_with_media(tmp_path)
        device_dir = tmp_path / PHOTOS_BASE_DIR / "Garage"
        device_dir.mkdir(parents=True)
        (device_dir / "last.jpg").write_bytes(b"x")
        with patch.object(Path, "read_bytes", side_effect=OSError("io error")):
            result = await load_last_photo(hass, "Garage")
        assert result is None


class TestCleanupOldPhotos:
    @pytest.mark.asyncio
    async def test_empty_list_when_photos_dir_does_not_exist(self, tmp_path: Path) -> None:
        hass = _hass_with_media(tmp_path)
        # tmp_path itself exists but the photos sub-tree never got created
        deleted = await cleanup_old_photos(hass, retention_days=1)
        assert deleted == []

    @pytest.mark.asyncio
    async def test_deletes_files_older_than_retention(self, tmp_path: Path) -> None:
        hass = _hass_with_media(tmp_path)
        device_dir = tmp_path / PHOTOS_BASE_DIR / "Cam"
        device_dir.mkdir(parents=True)
        old = device_dir / "2026-04-01_00-00-00.jpg"
        recent = device_dir / "2026-05-15_12-00-00.jpg"
        old.write_bytes(b"old")
        recent.write_bytes(b"recent")
        # Push `old`'s mtime back 10 days; leave `recent`'s mtime at "now".
        now = time.time()
        os.utime(old, (now - 10 * 86400, now - 10 * 86400))

        deleted = await cleanup_old_photos(hass, retention_days=1, max_photos_per_device=100)
        assert str(old) in deleted
        assert str(recent) not in deleted
        assert not old.exists()
        assert recent.exists()

    @pytest.mark.asyncio
    async def test_keeps_only_max_photos_per_device(self, tmp_path: Path) -> None:
        hass = _hass_with_media(tmp_path)
        device_dir = tmp_path / PHOTOS_BASE_DIR / "Cam"
        device_dir.mkdir(parents=True)
        now = time.time()
        files = []
        for i in range(5):
            p = device_dir / f"2026-05-15_12-00-{i:02d}.jpg"
            p.write_bytes(f"frame {i}".encode())
            os.utime(p, (now - i, now - i))  # newer first when i is smaller
            files.append(p)

        deleted = await cleanup_old_photos(hass, retention_days=365, max_photos_per_device=2)
        # The two newest (i=0, i=1) survive; the rest are deleted by count.
        assert files[0].exists()
        assert files[1].exists()
        for f in files[2:]:
            assert not f.exists()
            assert str(f) in deleted

    @pytest.mark.asyncio
    async def test_does_not_delete_last_jpg_or_non_jpg_files(self, tmp_path: Path) -> None:
        hass = _hass_with_media(tmp_path)
        device_dir = tmp_path / PHOTOS_BASE_DIR / "Cam"
        device_dir.mkdir(parents=True)
        last = device_dir / "last.jpg"
        sidecar = device_dir / "thumbnail.png"
        old_jpg = device_dir / "2026-04-01_00-00-00.jpg"
        for p in (last, sidecar, old_jpg):
            p.write_bytes(b"x")
        # Make all files appear ancient
        ancient = time.time() - 365 * 86400
        for p in (last, sidecar, old_jpg):
            os.utime(p, (ancient, ancient))

        deleted = await cleanup_old_photos(hass, retention_days=1, max_photos_per_device=100)
        assert str(old_jpg) in deleted
        assert not old_jpg.exists()
        # last.jpg is excluded by name; the .png is excluded by suffix.
        assert last.exists()
        assert sidecar.exists()

    @pytest.mark.asyncio
    async def test_skips_non_directory_entries(self, tmp_path: Path) -> None:
        hass = _hass_with_media(tmp_path)
        photos_dir = tmp_path / PHOTOS_BASE_DIR
        photos_dir.mkdir(parents=True)
        # A stray file at the device-dir level — _do_cleanup skips non-dirs.
        (photos_dir / "stray.txt").write_bytes(b"x")
        deleted = await cleanup_old_photos(hass, retention_days=1)
        assert deleted == []

    @pytest.mark.asyncio
    async def test_max_photos_zero_disables_count_pruning(self, tmp_path: Path) -> None:
        hass = _hass_with_media(tmp_path)
        device_dir = tmp_path / PHOTOS_BASE_DIR / "Cam"
        device_dir.mkdir(parents=True)
        now = time.time()
        for i in range(3):
            p = device_dir / f"2026-05-15_12-00-{i:02d}.jpg"
            p.write_bytes(b"x")
            os.utime(p, (now, now))

        deleted = await cleanup_old_photos(hass, retention_days=365, max_photos_per_device=0)
        # With max_photos_per_device=0 the count rule is disabled and nothing
        # is old enough to trip the age rule either.
        assert deleted == []

    @pytest.mark.asyncio
    async def test_returns_partial_results_on_broad_exception(self, tmp_path: Path) -> None:
        hass = _hass_with_media(tmp_path)
        photos_dir = tmp_path / PHOTOS_BASE_DIR
        photos_dir.mkdir(parents=True)
        # iterdir raises → broad except returns whatever was accumulated (here, [])
        with patch.object(Path, "iterdir", side_effect=OSError("permission denied")):
            deleted = await cleanup_old_photos(hass, retention_days=1)
        assert deleted == []
