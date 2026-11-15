"""Tests for logbook integration."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.aegis_ajax.const import ALL_EVENT_TYPES, DOMAIN
from custom_components.aegis_ajax.logbook import (
    _EVENT_DESCRIPTIONS,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
    async_describe_events,
)


def _make_event(event_type: str, **data: object) -> MagicMock:
    event = MagicMock()
    event.data = {"event_type": event_type, **data}
    return event


class TestAsyncDescribeEvents:
    def test_registers_handler(self) -> None:
        hass = MagicMock()
        async_describe_event = MagicMock()
        async_describe_events(hass, async_describe_event)
        async_describe_event.assert_called_once()
        call_args = async_describe_event.call_args[0]
        assert call_args[0] == DOMAIN
        assert call_args[1] == f"{DOMAIN}_event"
        assert callable(call_args[2])


class TestLogbookDescriptions:
    def _get_handler(self) -> object:
        async_describe_event = MagicMock()
        async_describe_events(MagicMock(), async_describe_event)
        return async_describe_event.call_args[0][2]

    def test_arm_event(self) -> None:
        handler = self._get_handler()
        # #659: the activity view shows only the entry NAME as the cause of
        # the panel change, so who armed has to be the name.
        result = handler(_make_event("arm", device_name="Keypad"))
        assert result[LOGBOOK_ENTRY_NAME] == "Keypad"
        assert result[LOGBOOK_ENTRY_MESSAGE] == "armed"

    def test_disarm_event(self) -> None:
        handler = self._get_handler()
        result = handler(_make_event("disarm", device_name="App User"))
        assert result[LOGBOOK_ENTRY_NAME] == "App User"
        assert result[LOGBOOK_ENTRY_MESSAGE] == "disarmed"

    def test_other_events_stay_under_the_integration_name(self) -> None:
        handler = self._get_handler()
        result = handler(_make_event("alarm", device_name="Front Door"))
        assert result[LOGBOOK_ENTRY_NAME] == "Aegis"

    def test_alarm_event(self) -> None:
        handler = self._get_handler()
        result = handler(_make_event("alarm", device_name="Front Door"))
        assert "Alarm" in result[LOGBOOK_ENTRY_MESSAGE]
        assert "Front Door" in result[LOGBOOK_ENTRY_MESSAGE]

    def test_door_open_event(self) -> None:
        handler = self._get_handler()
        result = handler(_make_event("door_open", device_name="Main Entrance"))
        assert "Door opened" in result[LOGBOOK_ENTRY_MESSAGE]

    def test_motion_event(self) -> None:
        handler = self._get_handler()
        result = handler(_make_event("motion", device_name="Hallway"))
        assert "Motion" in result[LOGBOOK_ENTRY_MESSAGE]

    def test_room_name_appended(self) -> None:
        handler = self._get_handler()
        result = handler(_make_event("alarm", device_name="Sensor", room_name="Kitchen"))
        assert "(Kitchen)" in result[LOGBOOK_ENTRY_MESSAGE]

    def test_no_room_name(self) -> None:
        handler = self._get_handler()
        result = handler(_make_event("alarm", device_name="Sensor"))
        assert "Sensor" in result[LOGBOOK_ENTRY_MESSAGE]
        # No room appended
        assert result[LOGBOOK_ENTRY_MESSAGE].count("(") == 1  # only "(via Sensor)"

    def test_unknown_event_type(self) -> None:
        handler = self._get_handler()
        result = handler(_make_event("some_future_event", device_name="Device"))
        assert "Security event" in result[LOGBOOK_ENTRY_MESSAGE]

    def test_missing_device_name(self) -> None:
        handler = self._get_handler()
        result = handler(_make_event("arm"))
        assert result[LOGBOOK_ENTRY_NAME] == "Aegis"
        assert "Unknown device" in result[LOGBOOK_ENTRY_MESSAGE]

    def test_panic_event(self) -> None:
        handler = self._get_handler()
        result = handler(_make_event("panic", device_name="Keypad"))
        assert "Panic" in result[LOGBOOK_ENTRY_MESSAGE]

    def test_fire_event(self) -> None:
        handler = self._get_handler()
        result = handler(_make_event("fire", device_name="Kitchen"))
        assert "Fire" in result[LOGBOOK_ENTRY_MESSAGE]

    def test_arm_with_malfunctions_says_so(self) -> None:
        # #387: the Ajax app reports "activated with malfunction", but the
        # logbook read as an ordinary arm. `event_type` is deliberately
        # normalised to "arm" so automations keep matching, and the detail
        # survives only on `raw_tag` — which the logbook was ignoring.
        handler = self._get_handler()
        result = handler(
            _make_event("arm", device_name="App User", raw_tag="space_armed_with_malfunctions")
        )
        assert result[LOGBOOK_ENTRY_NAME] == "App User"
        assert result[LOGBOOK_ENTRY_MESSAGE].startswith("armed")
        assert "malfunction" in result[LOGBOOK_ENTRY_MESSAGE].lower()

    def test_night_arm_with_malfunctions_says_so(self) -> None:
        # The same qualifier exists on night mode, so the marker has to key
        # on the tag rather than on the arm description.
        handler = self._get_handler()
        result = handler(
            _make_event(
                "arm_night",
                device_name="Keypad",
                raw_tag="space_night_mode_on_with_malfunctions",
            )
        )
        assert "armed night mode" in result[LOGBOOK_ENTRY_MESSAGE]
        assert "malfunction" in result[LOGBOOK_ENTRY_MESSAGE].lower()

    def test_clean_arm_does_not_mention_malfunctions(self) -> None:
        # The negative control: a normal arm must read exactly as before.
        handler = self._get_handler()
        result = handler(_make_event("arm", device_name="Keypad", raw_tag="space_armed"))
        assert "malfunction" not in result[LOGBOOK_ENTRY_MESSAGE].lower()

    def test_malfunction_event_type_is_not_double_marked(self) -> None:
        # `malfunction` is its own event type with its own description; the
        # suffix is about an *arm* that happened despite one, so a plain
        # malfunction event must not gain a second mention.
        handler = self._get_handler()
        result = handler(_make_event("malfunction", device_name="Door Protect"))
        assert result[LOGBOOK_ENTRY_MESSAGE].lower().count("malfunction") == 1

    def test_room_name_still_appended_with_malfunctions(self) -> None:
        handler = self._get_handler()
        result = handler(
            _make_event(
                "arm",
                device_name="Keypad",
                room_name="Hall",
                raw_tag="space_armed_with_malfunctions",
            )
        )
        assert "(Hall)" in result[LOGBOOK_ENTRY_MESSAGE]
        assert "malfunction" in result[LOGBOOK_ENTRY_MESSAGE].lower()

    def test_all_event_types_have_descriptions(self) -> None:
        for event_type in ALL_EVENT_TYPES:
            assert event_type in _EVENT_DESCRIPTIONS, f"Missing description for {event_type}"
