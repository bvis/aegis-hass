"""Logbook descriptions for Aegis for Ajax security events."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, callback

from custom_components.aegis_ajax.const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

LOGBOOK_ENTRY_NAME = "name"
LOGBOOK_ENTRY_MESSAGE = "message"

_EVENT_DESCRIPTIONS: dict[str, str] = {
    "alarm": "Alarm triggered (via {device_name})",
    "arm": "Armed (via {device_name})",
    "arm_night": "Night mode armed (via {device_name})",
    "battery_low": "Battery low ({device_name})",
    "co_alarm": "CO alarm ({device_name})",
    "connection_lost": "Connection lost ({device_name})",
    "disarm": "Disarmed (via {device_name})",
    "disarm_night": "Night mode disarmed (via {device_name})",
    "doorbell_pressed": "Doorbell pressed ({device_name})",
    "door_open": "Door opened (via {device_name})",
    "fire": "Fire detected ({device_name})",
    "flood": "Flood detected ({device_name})",
    "glass_break": "Glass break ({device_name})",
    "malfunction": "Malfunction ({device_name})",
    "motion": "Motion detected ({device_name})",
    "panic": "Panic ({device_name})",
    "tamper": "Tamper ({device_name})",
}

# #387: Ajax reports arming-despite-a-fault as its own qualifier
# (`space_armed_with_malfunctions`, `space_night_mode_on_with_malfunctions`,
# the `space_group_*` equivalents…), and the app shows it as "activated with
# malfunction". `event_type` deliberately normalises all of them to plain
# `arm` / `arm_night` so existing automations keep matching — see
# `SPACE_EVENT_TAG_MAP` — which left the logbook reading as an ordinary arm
# and the detail visible only to anyone inspecting `raw_tag` by hand.
#
# Keyed on the tag suffix rather than an explicit list so a qualifier we
# haven't seen yet is covered on arrival; every tag Ajax uses for this shares
# it. Appended rather than folded into the templates above so the marker is
# independent of which arm description was chosen.
# Arm/disarm entries are named after who did it (#659): the activity view
# shows only the entry name as the cause of the panel change ("By Ana"),
# never the message.
_ACTOR_MESSAGES: dict[str, str] = {
    "arm": "armed",
    "arm_night": "armed night mode",
    "disarm": "disarmed",
    "disarm_night": "disarmed night mode",
}

_MALFUNCTION_TAG_SUFFIX = "_with_malfunctions"
_MALFUNCTION_SUFFIX = " — with malfunctions"


@callback
def async_describe_events(
    hass: HomeAssistant,  # noqa: ARG001
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    """Register logbook event descriptions for Aegis security events."""

    @callback
    def async_describe_aegis_event(event: Event) -> dict[str, str]:
        data: dict[str, Any] = dict(event.data)
        event_type: str = data.get("event_type", "unknown")
        actor: str | None = data.get("device_name") or None
        device_name: str = actor or "Unknown device"
        room_name: str | None = data.get("room_name")

        raw_tag = data.get("raw_tag")

        name = "Aegis"
        if actor and event_type in _ACTOR_MESSAGES:
            name = actor
            message = _ACTOR_MESSAGES[event_type]
        else:
            template = _EVENT_DESCRIPTIONS.get(event_type, "Security event: {device_name}")
            message = template.format(device_name=device_name)
        if room_name:
            message += f" ({room_name})"
        if isinstance(raw_tag, str) and raw_tag.endswith(_MALFUNCTION_TAG_SUFFIX):
            message += _MALFUNCTION_SUFFIX

        return {
            LOGBOOK_ENTRY_NAME: name,
            LOGBOOK_ENTRY_MESSAGE: message,
        }

    async_describe_event(DOMAIN, f"{DOMAIN}_event", async_describe_aegis_event)
