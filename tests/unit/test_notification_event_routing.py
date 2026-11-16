"""Where a parsed push event is delivered, and which pushes are too old to deliver."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.service.push_notification_dispatch import (  # noqa: E501
    event_pb2,
)

from custom_components.aegis_ajax import notification as module
from custom_components.aegis_ajax.notification import (
    STALE_PUSH_THRESHOLD_SECONDS,
    AjaxNotificationListener,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_NOW = 1_800_000_000.0
_RAW = b"raw-push-bytes"
_ENCODED = base64.b64encode(_RAW).decode()


@pytest.fixture(autouse=True)
def _ha_sessions() -> Iterator[None]:
    with (
        patch("homeassistant.helpers.aiohttp_client.async_create_clientsession"),
        patch("homeassistant.helpers.aiohttp_client.async_get_clientsession"),
    ):
        yield


def _listener(*space_ids: str) -> AjaxNotificationListener:
    coordinator = MagicMock()
    coordinator._space_ids = list(space_ids)
    coordinator.devices = {}
    coordinator.doorbell_twin_aliases = {}
    listener = AjaxNotificationListener(
        hass=MagicMock(),
        coordinator=coordinator,
        fcm_project_id="mws-mobile-client---2",
        fcm_app_id="1:991608156148:android:" + "a" * 40,
        fcm_api_key="AIza" + "x" * 35,
        fcm_sender_id="991608156148",
    )
    listener._dispatch_to_loop = MagicMock()  # type: ignore[method-assign]
    return listener


def _dispatched(listener: AjaxNotificationListener) -> list[tuple[Any, ...]]:
    return [call.args for call in listener._dispatch_to_loop.call_args_list]


def _push(*, age: float | None, nanos: int = 0, kind: str = "notification") -> str:
    """A push dispatched ``age`` seconds before ``_NOW`` (no timestamp when None)."""
    dispatch = event_pb2.PushNotificationDispatchEvent()
    getattr(dispatch, kind).SetInParent()
    if age is not None:
        dispatch.notification.server_timestamp.seconds = int(_NOW - age)
        dispatch.notification.server_timestamp.nanos = nanos
    return base64.b64encode(dispatch.SerializeToString()).decode()


@pytest.mark.parametrize(
    ("encoded", "stale"),
    [
        (_push(age=STALE_PUSH_THRESHOLD_SECONDS + 1), True),
        (_push(age=STALE_PUSH_THRESHOLD_SECONDS), False),
        (_push(age=0), False),
        # The fraction of a second counts towards the push being newer.
        (_push(age=STALE_PUSH_THRESHOLD_SECONDS, nanos=500_000_000), False),
        (_push(age=STALE_PUSH_THRESHOLD_SECONDS + 1, nanos=500_000_000), True),
        # Anything whose dispatch time cannot be read is delivered, never dropped.
        (_push(age=None), False),
        (_push(age=None, kind="session_invalidation"), False),
        ("not-valid-base64!!!", False),
        (base64.b64encode(b"\xff\xff\xff not a message").decode(), False),
    ],
)
def test_only_a_push_dispatched_too_long_ago_is_stale(encoded: str, stale: bool) -> None:
    with patch.object(module.time, "time", return_value=_NOW):
        assert _listener()._is_stale_push(encoded) is stale


def test_the_fraction_of_a_second_of_the_dispatch_time_is_not_ignored() -> None:
    # A quarter of a second past the limit by whole seconds, but the push was
    # dispatched half a second into its second: still fresh.
    encoded = _push(age=STALE_PUSH_THRESHOLD_SECONDS, nanos=500_000_000)
    with patch.object(module.time, "time", return_value=_NOW + 0.25):
        assert _listener()._is_stale_push(encoded) is False


def _route(
    listener: AjaxNotificationListener,
    event_type: str,
    *,
    space: str | None,
    notification_id: str | None = None,
    photo_context: tuple[str, str, float] | None = None,
) -> tuple[dict[str, Any], MagicMock, MagicMock, MagicMock]:
    data: dict[str, Any] = {"raw_tag": "not-a-group-tag"}
    with (
        patch.object(listener, "_extract_event_from_proto", return_value=(event_type, data)),
        patch.object(listener, "_extract_source_info", return_value={"device_id": "D1"}),
        patch.object(listener, "_find_space_for_event", return_value=space) as find,
        patch.object(listener, "_apply_security_state_from_event") as apply_state,
        patch.object(listener, "_dispatch_event_to_device") as to_device,
        patch.object(
            module.notification_event_parser,
            "extract_alarm_photo_context",
            return_value=photo_context,
        ) as photo,
        patch.object(module.notification_event_parser, "extract_space_id", return_value=None),
    ):
        listener._parse_and_fire_event(_ENCODED, notification_id=notification_id)
    find.assert_called_once_with(_RAW)
    return data, apply_state, to_device, photo


def test_an_event_goes_to_the_space_it_names() -> None:
    listener = _listener("space-1", "space-2")
    data, apply_state, to_device, photo = _route(listener, "tamper", space="space-2")
    assert data == {"raw_tag": "not-a-group-tag", "device_id": "D1"}
    assert _dispatched(listener) == [
        (listener._coordinator.fire_push_event, "space-2", "tamper", data)
    ]
    apply_state.assert_called_once_with("space-2", data)
    to_device.assert_called_once_with("tamper", data, _RAW, "space-2")
    photo.assert_not_called()


def test_an_event_naming_no_space_goes_to_the_only_space() -> None:
    listener = _listener("space-1")
    data, apply_state, to_device, _ = _route(listener, "tamper", space=None)
    assert _dispatched(listener) == [
        (listener._coordinator.fire_push_event, "space-1", "tamper", data)
    ]
    apply_state.assert_called_once_with("space-1", data)
    to_device.assert_called_once_with("tamper", data, _RAW, "space-1")


def test_an_event_naming_no_space_is_not_guessed_among_several() -> None:
    listener = _listener("space-1", "space-2")
    data, apply_state, to_device, _ = _route(listener, "tamper", space=None)
    assert _dispatched(listener) == []
    apply_state.assert_not_called()
    to_device.assert_called_once_with("tamper", data, _RAW, None)


@pytest.mark.parametrize(("named", "delivered"), [("space-2", "space-2"), (None, "space-1")])
def test_an_alarm_with_a_photo_schedules_its_import_for_that_space(
    named: str | None, delivered: str
) -> None:
    listener = _listener(*(("space-1", "space-2") if named else ("space-1",)))
    data, _, _, photo = _route(
        listener,
        "alarm",
        space=named,
        notification_id="notif-1",
        photo_context=("D1", "hub-1", 1_700_000_000.5),
    )
    photo.assert_called_once_with(_RAW, "notif-1")
    assert _dispatched(listener)[:2] == [
        (listener._coordinator.fire_push_event, delivered, "alarm", data),
        (
            listener._coordinator.schedule_alarm_image_import,
            delivered,
            "notif-1",
            "D1",
            "hub-1",
            1_700_000_000.5,
        ),
    ]


def test_only_an_alarm_with_a_notification_id_looks_for_a_photo() -> None:
    for event_type, notification_id in (("alarm", None), ("tamper", "notif-1")):
        listener = _listener("space-1")
        _, _, _, photo = _route(listener, event_type, space=None, notification_id=notification_id)
        photo.assert_not_called()
        assert listener._coordinator.schedule_alarm_image_import not in [
            call[0] for call in _dispatched(listener)
        ]


def test_a_security_state_event_asks_for_a_fresh_snapshot() -> None:
    event_type = sorted(module.SECURITY_STATE_EVENT_TYPES)[0]
    listener = _listener("space-1")
    _route(listener, event_type, space=None)
    assert (listener._coordinator.request_security_snapshot_refresh,) in _dispatched(listener)

    listener = _listener("space-1")
    _route(listener, "tamper", space=None)
    assert (listener._coordinator.request_security_snapshot_refresh,) not in _dispatched(listener)


_DOORBELL, _MOTION = module.DOORBELL_EVENT_TYPE, module.MOTION_EVENT_TYPE


def _to_device(
    event_type: str,
    device_id: str | None,
    *,
    devices: tuple[str, ...] = ("D1", "D2"),
    aliases: dict[str, str] | None = None,
    fallback: list[str] | None = None,
) -> tuple[AjaxNotificationListener, dict[str, Any], MagicMock]:
    listener = _listener("space-1")
    listener._coordinator.devices = {device: MagicMock() for device in devices}
    listener._coordinator.doorbell_twin_aliases = aliases or {}
    data = {"device_id": device_id} if device_id else {}
    with patch.object(
        listener, "_doorbell_fallback_candidates", return_value=fallback or []
    ) as candidates:
        listener._dispatch_event_to_device(event_type, data, _RAW, "space-1")
    return listener, data, candidates


def test_a_ring_fires_on_the_doorbell_it_names() -> None:
    listener, data, candidates = _to_device(_DOORBELL, "D2")
    assert _dispatched(listener) == [
        (listener._coordinator.fire_push_device_event, "D2", _DOORBELL, data)
    ]
    candidates.assert_not_called()


def test_a_ring_from_a_doorbell_twin_fires_on_the_known_one() -> None:
    listener, data, _ = _to_device(_DOORBELL, "TWIN", aliases={"TWIN": "D1"})
    assert _dispatched(listener) == [
        (listener._coordinator.fire_push_device_event, "D1", _DOORBELL, data)
    ]


def test_an_unattributed_ring_falls_back_to_the_only_doorbell_of_that_space() -> None:
    listener, data, candidates = _to_device(_DOORBELL, None, fallback=["D2"])
    candidates.assert_called_once_with(listener._coordinator.devices, "space-1")
    assert _dispatched(listener) == [
        (listener._coordinator.fire_push_device_event, "D2", _DOORBELL, data)
    ]

    listener, _, _ = _to_device(_DOORBELL, "UNKNOWN", fallback=["D1", "D2"])
    assert _dispatched(listener) == []


def test_motion_marks_the_device_it_names_and_is_never_guessed() -> None:
    listener, _, candidates = _to_device(_MOTION, "D1")
    assert _dispatched(listener) == [(listener._coordinator.apply_push_device_motion, "D1")]

    listener, _, candidates = _to_device(_MOTION, None, fallback=["D2"])
    assert _dispatched(listener) == []
    candidates.assert_not_called()


def test_other_events_are_not_mirrored_onto_a_device() -> None:
    listener, _, _ = _to_device("tamper", "D1")
    assert _dispatched(listener) == []
