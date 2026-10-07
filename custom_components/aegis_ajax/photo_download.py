"""Bounded, non-redirecting download of Ajax camera images."""

from __future__ import annotations

import asyncio
import io
import logging
from typing import TYPE_CHECKING

import aiohttp
from PIL import Image

from custom_components.aegis_ajax.api.media import is_valid_photo_url

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PHOTO_PIXELS = 16_000_000


def _valid_image(data: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                return False
            if image.width * image.height > MAX_PHOTO_PIXELS:
                return False
            image.verify()
        # Verify catches container corruption; load also catches truncated pixels.
        with Image.open(io.BytesIO(data)) as image:
            image.load()
        return True
    except Exception:
        return False


async def async_download_photo(session: ClientSession, url: str) -> bytes | None:
    """Reject untrusted URLs, redirects, oversized bodies and invalid images."""
    if not is_valid_photo_url(url):
        _LOGGER.warning("Rejected untrusted Ajax photo URL")
        return None
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=15), allow_redirects=False
        ) as response:
            if response.status != 200:
                return None
            if response.content_length is not None and response.content_length > MAX_PHOTO_BYTES:
                return None
            data = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                if len(data) + len(chunk) > MAX_PHOTO_BYTES:
                    return None
                data.extend(chunk)
        image = bytes(data)
        if image:
            valid = await asyncio.to_thread(_valid_image, image)
            if valid:
                return image
    except (aiohttp.ClientError, TimeoutError, OSError):
        # Exception strings can contain signed URLs; do not log them.
        _LOGGER.warning("Could not download Ajax photo")
    return None
