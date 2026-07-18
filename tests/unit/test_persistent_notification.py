"""Tests for the persistent-notification manager (2.2)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from custom_components.aegis_ajax.const import (
    ALL_EVENT_TYPES,
    DEFAULT_PERSISTENT_NOTIFICATION_EVENTS,
)
from custom_components.aegis_ajax.persistent_notification import AjaxPersistentNotifier

_PN = "custom_components.aegis_ajax.persistent_notification.persistent_notification"


def _make_notifier(**kwargs: object) -> AjaxPersistentNotifier:
    hass = MagicMock()
    defaults: dict[str, object] = {
        "enabled": True,
        "event_types": ["alarm", "panic"],
        "account_name": "",
    }
    defaults.update(kwargs)
    return AjaxPersistentNotifier(hass, "entry-1", **defaults)  # type: ignore[arg-type]


class TestNotifyFiltering:
    def test_disabled_does_not_create(self) -> None:
        notifier = _make_notifier(enabled=False)
        with patch(_PN) as pn:
            notifier.notify("alarm", {"device_name": "Kitchen"})
        pn.async_create.assert_not_called()

    def test_event_not_in_filter_does_not_create(self) -> None:
        notifier = _make_notifier(event_types=["alarm"])
        with patch(_PN) as pn:
            notifier.notify("motion", {"device_name": "Kitchen"})
        pn.async_create.assert_not_called()

    def test_event_in_filter_creates(self) -> None:
        notifier = _make_notifier(event_types=["alarm"])
        with patch(_PN) as pn:
            notifier.notify("alarm", {"device_name": "Kitchen"})
        pn.async_create.assert_called_once()

    def test_empty_filter_never_creates(self) -> None:
        notifier = _make_notifier(event_types=[])
        with patch(_PN) as pn:
            notifier.notify("alarm", {"device_name": "Kitchen"})
        pn.async_create.assert_not_called()


class TestEnabledProperty:
    def test_enabled_true_when_on_with_events(self) -> None:
        assert _make_notifier(enabled=True, event_types=["alarm"]).enabled is True

    def test_enabled_false_when_off(self) -> None:
        assert _make_notifier(enabled=False, event_types=["alarm"]).enabled is False

    def test_enabled_false_when_no_events(self) -> None:
        assert _make_notifier(enabled=True, event_types=[]).enabled is False


class TestFormatting:
    def test_title_and_message_contents(self) -> None:
        notifier = _make_notifier(event_types=["alarm"], account_name="me@example.com")
        with patch(_PN) as pn:
            notifier.notify(
                "alarm",
                {
                    "device_name": "Front Door",
                    "device_id": "A1B2C3D4",
                    "room_name": "Hallway",
                },
            )
        args, kwargs = pn.async_create.call_args
        # positional: (hass, message)
        message = args[1]
        assert "Alarm triggered" in message
        assert "Front Door" in message
        assert "Hallway" in message
        assert kwargs["title"] == "Ajax · Alarm triggered (me@example.com)"
        assert kwargs["notification_id"] == "aegis_ajax_entry-1_alarm_A1B2C3D4"

    def test_notification_id_falls_back_to_space_without_device(self) -> None:
        notifier = _make_notifier(event_types=["panic"])
        with patch(_PN) as pn:
            notifier.notify("panic", {})
        _, kwargs = pn.async_create.call_args
        assert kwargs["notification_id"] == "aegis_ajax_entry-1_panic_space"

    def test_notification_id_uses_group_when_no_device(self) -> None:
        notifier = _make_notifier(event_types=["panic"])
        with patch(_PN) as pn:
            notifier.notify("panic", {"group_id": "g7", "space_id": "sp1"})
        _, kwargs = pn.async_create.call_args
        assert kwargs["notification_id"] == "aegis_ajax_entry-1_panic_group_g7"

    def test_notification_id_uses_space_when_no_device_or_group(self) -> None:
        notifier = _make_notifier(event_types=["panic"])
        with patch(_PN) as pn:
            notifier.notify("panic", {"space_id": "sp1"})
        _, kwargs = pn.async_create.call_args
        assert kwargs["notification_id"] == "aegis_ajax_entry-1_panic_space_sp1"

    def test_device_id_takes_precedence_over_group_and_space(self) -> None:
        notifier = _make_notifier(event_types=["alarm"])
        with patch(_PN) as pn:
            notifier.notify("alarm", {"device_id": "D1", "group_id": "g7", "space_id": "sp1"})
        _, kwargs = pn.async_create.call_args
        assert kwargs["notification_id"] == "aegis_ajax_entry-1_alarm_D1"

    def test_unknown_event_type_uses_generic_headline(self) -> None:
        notifier = _make_notifier(event_types=["some_new_event"])
        with patch(_PN) as pn:
            notifier.notify("some_new_event", {"device_name": "X"})
        args, kwargs = pn.async_create.call_args
        assert "Security event" in args[1]
        assert kwargs["title"].startswith("Ajax · Security event")

    def test_title_without_account_name_has_no_parens(self) -> None:
        notifier = _make_notifier(event_types=["alarm"], account_name="")
        with patch(_PN) as pn:
            notifier.notify("alarm", {"device_name": "X"})
        _, kwargs = pn.async_create.call_args
        assert kwargs["title"] == "Ajax · Alarm triggered"


class TestDefaults:
    def test_default_events_are_incidents(self) -> None:
        assert set(DEFAULT_PERSISTENT_NOTIFICATION_EVENTS) == {
            "alarm",
            "panic",
            "tamper",
            "fire",
            "co_alarm",
            "flood",
            "glass_break",
        }

    def test_default_events_are_valid_event_types(self) -> None:
        for event in DEFAULT_PERSISTENT_NOTIFICATION_EVENTS:
            assert event in ALL_EVENT_TYPES


class TestParserToNotifierContract:
    """End-to-end contract test (2.2 review): a real-proto push payload must

    flow through the listener's actual parse path and land in the notifier
    with the exact data keys `_format()` consumes. The unit tests above feed
    hand-written dicts whose keys merely *happen* to match the parser — if
    `notification_event_parser` ever renamed `device_name`/`device_id`, they
    would all stay green while every notification silently degraded to
    headline+time. This test only patches `persistent_notification.async_create`
    (the HA side effect); everything else is production code on real protos.
    """

    @staticmethod
    def _build_real_push() -> str:
        """Base64 push with a real HubEventQualifier + HubNotificationSource.

        Mirrors real FCM wire shape closely enough for both production
        extractors: the qualifier is embedded as a length-delimited
        submessage (what `_find_embedded_messages` scans for) and the
        source is appended the same way (what `_extract_source_info`
        byte-scans for).
        """
        import base64

        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event import (  # noqa: PLC0415, E501
            transition_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.hub import (  # noqa: PLC0415, E501
            qualifier_pb2 as hub_qualifier_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.hub import (
            tag_pb2 as hub_tag_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.hub import (  # noqa: PLC0415, E501
            source_pb2,
            source_type_pb2,
        )

        qualifier = hub_qualifier_pb2.HubEventQualifier(
            tag=hub_tag_pb2.HubEventTag(intrusion_alarm=hub_tag_pb2.IntrusionAlarm()),
            transition=transition_pb2.EventTransition(
                impulse=transition_pb2.EventTransition.Impulse()
            ),
        )
        source = source_pb2.HubNotificationSource(
            type=source_type_pb2.HubNotificationSourceType.MOTION_CAM_PHOD,
            id="A1B2C3D4",
            name="VESTIBULO",
        )
        q = qualifier.SerializeToString()
        s = source.SerializeToString()
        payload = b"\x0a" + bytes([len(q)]) + q + b"\x12" + bytes([len(s)]) + s
        return base64.b64encode(payload).decode()

    def test_real_push_reaches_notifier_with_device_context(self) -> None:
        from custom_components.aegis_ajax.notification import AjaxNotificationListener

        coordinator = MagicMock()
        coordinator._space_ids = ["space-1"]
        coordinator.spaces = {}
        listener = AjaxNotificationListener(
            hass=MagicMock(),
            coordinator=coordinator,
            fcm_project_id="mws-mobile-client---2",
            fcm_app_id="1:991608156148:android:" + "a" * 40,
            fcm_api_key="AIza" + "x" * 35,
            fcm_sender_id="991608156148",
        )

        # Run the worker-thread dispatch synchronously; everything else is
        # the production `_parse_and_fire_event` path.
        with patch.object(
            AjaxNotificationListener, "_dispatch_to_loop", lambda self, fn, *a: fn(*a)
        ):
            listener._parse_and_fire_event(self._build_real_push())

        coordinator.fire_push_event.assert_called_once()
        space_id, event_type, event_data = coordinator.fire_push_event.call_args[0]
        assert space_id == "space-1"
        assert event_type == "alarm"

        # Hand the parser's own output to a REAL notifier, mirroring the
        # entity's space_id enrichment (event.py) — only the HA call mocked.
        notifier = _make_notifier(event_types=["alarm"])
        with patch(_PN) as pn:
            notifier.notify(event_type, {**event_data, "space_id": space_id})

        args, kwargs = pn.async_create.call_args
        message = args[1]
        assert kwargs["title"] == "Ajax · Alarm triggered"
        # The parser-produced device context must survive into the card…
        assert "VESTIBULO" in message
        # …and the parser-produced device_id must key the dedup id.
        assert kwargs["notification_id"] == "aegis_ajax_entry-1_alarm_A1B2C3D4"
