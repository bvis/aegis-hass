"""Which photo URLs a push is allowed to hand to a waiting capture, and to which device."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from custom_components.aegis_ajax.notification import AjaxNotificationListener

if TYPE_CHECKING:
    from collections.abc import Iterator

_AJAX = "https://app.prod.ajax.systems/photo/A1B2C3D4/test.jpg"
_S3 = "https://hubs-uploaded-resources.s3.eu-west-1.amazonaws.com/A1B2C3D4/x.jpg?X-Amz-Signature=s"


@pytest.fixture(autouse=True)
def _ha_sessions() -> Iterator[None]:
    with (
        patch("homeassistant.helpers.aiohttp_client.async_create_clientsession"),
        patch("homeassistant.helpers.aiohttp_client.async_get_clientsession"),
    ):
        yield


def _push(*urls: str) -> dict[str, str]:
    """A push whose payload carries ``urls``, each ended by a control byte."""
    raw = b"\x08\x01" + b"".join(url.encode() + b"\x00" for url in urls)
    return {"ENCODED_DATA": base64.b64encode(raw).decode()}


def _resolved(push: dict[str, str], *waiting: str, done: tuple[str, ...] = ()) -> list[tuple]:
    """Deliver ``push`` with captures waiting on ``waiting``; return (device, url) handed over."""
    listener = AjaxNotificationListener(
        hass=MagicMock(),
        coordinator=MagicMock(),
        fcm_project_id="mws-mobile-client---2",
        fcm_app_id="1:991608156148:android:" + "a" * 40,
        fcm_api_key="AIza" + "x" * 35,
        fcm_sender_id="991608156148",
    )
    for device_id in waiting:
        future = MagicMock()
        future.done.return_value = device_id in done
        listener._photo_callbacks[device_id] = future
    with (
        patch.object(listener, "_resolve_future_on_loop") as resolve,
        patch.object(listener, "_parse_and_fire_event"),
        patch.object(listener, "_note_push_delivered"),
    ):
        listener._on_notification(push, "persistent-1")
    for call in resolve.call_args_list:
        assert call.args[0] is listener._photo_callbacks
    return [call.args[1:] for call in resolve.call_args_list]


@pytest.mark.parametrize("url", [_AJAX, _S3])
def test_a_photo_from_ajax_or_its_bucket_is_handed_over(url: str) -> None:
    assert _resolved(_push(url), "A1B2C3D4") == [("A1B2C3D4", url)]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/A1B2C3D4/photo.jpg",
        # Looks like the bucket, but is not on Amazon.
        "https://hubs-uploaded-resources.attacker.com/A1B2C3D4/x.jpg",
        # On Amazon, but not the bucket.
        "https://other-bucket.s3.eu-west-1.amazonaws.com/A1B2C3D4/x.jpg",
        # The Ajax domain as a prefix or without its dot.
        "https://ajax.systems.attacker.com/A1B2C3D4/x.jpg",
        "https://evilajax.systems/A1B2C3D4/x.jpg",
        "https://attacker.com/hubs-uploaded-resources.s3.amazonaws.com/A1B2C3D4.jpg",
    ],
)
def test_a_photo_from_any_other_host_is_ignored(url: str) -> None:
    assert _resolved(_push(url), "A1B2C3D4") == []


def test_the_first_allowed_url_wins_and_others_are_skipped() -> None:
    push = _push("https://example.com/A1B2C3D4/first.jpg", _AJAX, _S3)
    assert _resolved(push, "A1B2C3D4") == [("A1B2C3D4", _AJAX)]


def test_the_photo_goes_to_the_device_named_in_its_url() -> None:
    assert _resolved(_push(_AJAX), "FFFF0000", "a1b2c3d4") == [("a1b2c3d4", _AJAX)]


def test_without_a_matching_device_the_first_waiting_capture_gets_it() -> None:
    assert _resolved(_push(_AJAX), "FFFF0000", "EEEE1111") == [("FFFF0000", _AJAX)]


def test_a_capture_that_already_finished_is_not_handed_another_photo() -> None:
    assert _resolved(_push(_AJAX), "A1B2C3D4", "EEEE1111", done=("A1B2C3D4",)) == [
        ("EEEE1111", _AJAX)
    ]
    assert _resolved(_push(_AJAX), "A1B2C3D4", done=("A1B2C3D4",)) == []
