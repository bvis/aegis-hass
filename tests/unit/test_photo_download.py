"""Security boundaries for downloads, exercised without external network access."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from custom_components.aegis_ajax.api.media import is_valid_photo_url
from custom_components.aegis_ajax.photo_download import _valid_image, async_download_photo

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


TRUSTED = "https://hubs-uploaded-resources.s3.amazonaws.com/photo.jpg"


@pytest.mark.parametrize(
    "url",
    [
        "http://app.prod.ajax.systems/photo.jpg",
        "https://attacker-hubs-uploaded-resources.s3.amazonaws.com/photo.jpg",
        "https://hubs-uploaded-resources.attacker.com/photo.jpg",
        "https://hubs-uploaded-resources.s3.attacker.amazonaws.com/photo.jpg",
        "https://app.prod.ajax.systems:444/photo.jpg",
        "https://user:password@app.prod.ajax.systems/photo.jpg",
        "https://app.prod.ajax.systems:invalid/photo.jpg",
        "https://[bad/photo.jpg",
        "https://app.prod.ajax.systems/\nphoto.jpg",
    ],
)
async def test_untrusted_urls_do_not_make_requests(url: str) -> None:
    session = MagicMock()
    assert not is_valid_photo_url(url)
    assert await async_download_photo(session, url) is None
    session.get.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        TRUSTED,
        "https://hubs-uploaded-resources.s3.eu-west-1.amazonaws.com/photo.jpg",
        "https://hubs-uploaded-resources.s3-eu-west-1.amazonaws.com/photo.jpg",
        "https://app.prod.ajax.systems/photo.jpg",
    ],
)
def test_supported_hosts(url: str) -> None:
    assert is_valid_photo_url(url)


class Response:
    def __init__(
        self, chunks: list[bytes], status: int = 200, content_length: int | None = None
    ) -> None:
        self.chunks = chunks
        self.status = status
        self.content_length = content_length
        self.content = self
        self.closed = False
        self.yielded = 0

    async def __aenter__(self) -> Response:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.closed = True

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk


def jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2)).save(buffer, format="JPEG")
    return buffer.getvalue()


async def test_valid_jpeg_and_redirect_policy() -> None:
    image = jpeg()
    response = Response([image[:20], image[20:]])
    session = MagicMock()
    session.get.return_value = response
    assert await async_download_photo(session, TRUSTED) == image
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert response.closed


async def test_redirect_not_read_or_followed() -> None:
    response = Response([b"redirect body"], status=302)
    session = MagicMock()
    session.get.return_value = response
    assert await async_download_photo(session, TRUSTED) is None
    assert response.yielded == 0
    session.get.assert_called_once()


@pytest.mark.parametrize("content_length", [None, 1, 1000])
async def test_byte_limit_with_missing_false_or_oversized_length(
    content_length: int | None,
) -> None:
    response = Response([b"a" * 6, b"b" * 6, b"not read"], content_length=content_length)
    session = MagicMock()
    session.get.return_value = response
    with patch("custom_components.aegis_ajax.photo_download.MAX_PHOTO_BYTES", 10):
        assert await async_download_photo(session, TRUSTED) is None
    assert response.yielded <= 2
    assert response.closed


async def test_invalid_image_not_returned() -> None:
    session = MagicMock()
    session.get.return_value = Response([b"not an image"])
    assert await async_download_photo(session, TRUSTED) is None


def test_decoded_pixel_limit() -> None:
    with patch("custom_components.aegis_ajax.photo_download.MAX_PHOTO_PIXELS", 1):
        assert not _valid_image(jpeg())


def test_truncated_jpeg_rejected() -> None:
    assert not _valid_image(jpeg()[:-20])
