"""Surface selected Ajax security events as HA persistent notifications (2.2).

A thin, side-effect-light manager that the coordinator hands each dispatched
push event. When the feature is enabled (Options → "Show security events as
persistent notifications") and the event type is in the configured filter, it
creates a Home Assistant persistent notification so the incident stays visible
in the UI until the user dismisses it.

Kept as its own module — free of coordinator/network state — so the filter and
message formatting can be unit-tested against a bare ``hass`` mock. Only
``notify`` has a side effect (`persistent_notification.async_create`); the rest
is a pure function of the event and its data.

Security *events* (alarm, tamper, panic, fire, …) reach the integration over
FCM only, so this manager only ever fires for FCM-delivered events — which is
exactly the "alarm events" scope the feature targets. Arm/disarm state changes
that also arrive over HTS are available in the filter but off by default.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.util import dt as dt_util

from custom_components.aegis_ajax.const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Human-readable headline per HA event type. Anything not listed falls back to
# a generic "Security event" headline so a newly-mapped event type still
# produces a sensible notification without a code change here.
_EVENT_HEADLINES: dict[str, str] = {
    "alarm": "Alarm triggered",
    "panic": "Panic alarm",
    "tamper": "Tamper detected",
    "fire": "Fire detected",
    "co_alarm": "CO alarm",
    "flood": "Flood detected",
    "glass_break": "Glass break detected",
    "motion": "Motion detected",
    "door_open": "Door opened",
    "doorbell_pressed": "Doorbell pressed",
    "arm": "Armed",
    "disarm": "Disarmed",
    "arm_night": "Night mode armed",
    "disarm_night": "Night mode disarmed",
    "battery_low": "Battery low",
    "connection_lost": "Connection lost",
    "malfunction": "Malfunction",
}


def _headline(event_type: str) -> str:
    return _EVENT_HEADLINES.get(event_type, "Security event")


class AjaxPersistentNotifier:
    """Creates HA persistent notifications for configured Ajax events."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        *,
        enabled: bool = False,
        event_types: Iterable[str] = (),
        account_name: str = "",
    ) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._enabled = enabled
        self._event_types = frozenset(event_types)
        self._account_name = account_name

    @property
    def enabled(self) -> bool:
        """True when the feature is on and at least one event type is selected."""
        return self._enabled and bool(self._event_types)

    def notify(self, event_type: str, data: dict[str, Any]) -> None:
        """Create a persistent notification when ``event_type`` is configured.

        No-op when the feature is disabled or the event isn't in the filter.
        Safe to call for every dispatched event — the filtering lives here so
        callers don't need to know the configuration. Runs on the event loop
        (the dispatch path), so ``async_create`` is used directly.
        """
        if not self._enabled or event_type not in self._event_types:
            return
        title, message, notification_id = self._format(event_type, data)
        persistent_notification.async_create(
            self._hass, message, title=title, notification_id=notification_id
        )
        _LOGGER.debug("Created persistent notification %s for %s", notification_id, event_type)

    def _format(self, event_type: str, data: dict[str, Any]) -> tuple[str, str, str]:
        """Build ``(title, message, notification_id)`` for an event.

        The id keys on ``entry_id + event_type + device`` so a repeat of the
        same event on the same device refreshes the existing card (with a fresh
        timestamp) instead of stacking duplicates, while different devices or
        event types get their own card.
        """
        headline = _headline(event_type)
        device_name = str(data.get("device_name") or "").strip()
        room_name = str(data.get("room_name") or "").strip()
        group_name = str(data.get("group_name") or "").strip()
        device_id = str(data.get("device_id") or "").strip()

        title = f"Ajax · {headline}"
        if self._account_name:
            title = f"{title} ({self._account_name})"

        lines = [f"**{headline}**"]
        if device_name:
            lines.append(f"Device: {device_name}")
        if room_name:
            lines.append(f"Room: {room_name}")
        if group_name:
            lines.append(f"Group: {group_name}")
        lines.append(f"Time: {dt_util.now().strftime('%Y-%m-%d %H:%M:%S')}")
        message = "\n".join(lines)

        id_suffix = device_id or "space"
        notification_id = f"{DOMAIN}_{self._entry_id}_{event_type}_{id_suffix}"
        return title, message, notification_id
