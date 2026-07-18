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
