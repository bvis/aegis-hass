"""Device automation triggers for Ajax Security.

Exposes each Ajax security event (alarm, arm, disarm, motion, doorbell
pressed, …) as a named device trigger on the **hub** device, so users can
pick them in the Home Assistant automation editor instead of hand-writing an
event trigger against the `aegis_ajax_event` bus event.

All events fire on a single hub-level `event` entity, so the triggers live on
the hub device and filter purely by `event_type`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.helpers import device_registry as dr

from custom_components.aegis_ajax.const import ALL_EVENT_TYPES, DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import CALLBACK_TYPE, HomeAssistant
    from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo

# Every Ajax security event is a candidate trigger on the hub device.
TRIGGER_TYPES: set[str] = set(ALL_EVENT_TYPES)

EVENT_AEGIS = f"{DOMAIN}_event"

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
    }
)


def _aegis_id_for_device(hass: HomeAssistant, device_id: str) -> str | None:
    """Resolve a HA device registry id to its Ajax id (hub_id / device_id)."""
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        return None
    return next((ident[1] for ident in device.identifiers if ident[0] == DOMAIN), None)


def _is_hub(hass: HomeAssistant, aegis_id: str) -> bool:
    """True when `aegis_id` is a hub id of any loaded aegis config entry."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        if coordinator is None:
            continue
        for space in coordinator.spaces.values():
            if space.hub_id == aegis_id:
                return True
    return False


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    """List device triggers for an Ajax device. Hub devices expose one
    trigger per security event type; other devices expose none (their events
    surface on the hub-level event entity)."""
    aegis_id = _aegis_id_for_device(hass, device_id)
    if aegis_id is None or not _is_hub(hass, aegis_id):
        return []
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in sorted(TRIGGER_TYPES)
    ]


def _event_trigger_config(config: dict[str, Any]) -> dict[str, Any]:
    """Translate a device trigger config into an event-bus trigger config that
    fires when `aegis_ajax_event` carries the matching `event_type`.

    The bus payload's discriminator field is literally `event_type` (see
    `event.py`), not the trigger's own `type` key — match on that.
    """
    # Literal keys: the event-trigger config schema keys are stable
    # (`platform` / `event_type` / `event_data`) and not all are re-exported
    # by the trigger module for typed import.
    return {
        "platform": "event",
        "event_type": EVENT_AEGIS,
        "event_data": {"event_type": config[CONF_TYPE]},
    }


async def async_attach_trigger(
    hass: HomeAssistant,
    config: dict[str, Any],
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a device trigger by delegating to the event-bus trigger."""
    event_config = event_trigger.TRIGGER_SCHEMA(_event_trigger_config(config))
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
