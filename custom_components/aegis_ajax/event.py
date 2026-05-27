"""Event platform for Ajax Security."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.const import (
    ALL_EVENT_TYPES,
    DOMAIN,
    DOORBELL_DEVICE_TYPES,
    DOORBELL_EVENT_TYPE,
    MANUFACTURER,
)
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.entity import build_device_info

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AjaxCobrandedCoordinator = entry.runtime_data
    entities = [
        AjaxSecurityEvent(coordinator=coordinator, space_id=space_id)
        for space_id in coordinator.spaces
    ]
    doorbell_entities = [
        AjaxDoorbellEvent(coordinator=coordinator, device_id=device_id)
        for device_id, device in coordinator.devices.items()
        if device.device_type in DOORBELL_DEVICE_TYPES
    ]
    async_add_entities([*entities, *doorbell_entities])
    for entity in entities:
        coordinator.register_event_entity(entity._space_id, entity)
    for doorbell in doorbell_entities:
        coordinator.register_device_event_entity(doorbell._device_id, doorbell)


class AjaxSecurityEvent(CoordinatorEntity[AjaxCobrandedCoordinator], EventEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "security_event"

    def __init__(self, coordinator: AjaxCobrandedCoordinator, space_id: str) -> None:
        super().__init__(coordinator)
        self._space_id = space_id
        space = coordinator.spaces.get(space_id)
        hub_id = space.hub_id if space else space_id
        self._attr_unique_id = f"aegis_ajax_{hub_id}_event"
        self._attr_event_types = ALL_EVENT_TYPES
        hub_device = coordinator.devices.get(hub_id)
        if hub_device:
            self._attr_device_info = build_device_info(hub_device, coordinator.rooms)
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, hub_id)},
                name=space.name if space else "Ajax Hub",
                manufacturer=MANUFACTURER,
                model="Hub",
            )

    @property
    def event_types(self) -> list[str]:
        return self._attr_event_types

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from coordinator when removed."""
        self.coordinator._event_entities.pop(self._space_id, None)
        await super().async_will_remove_from_hass()

    def handle_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Called by coordinator when a push event arrives."""
        if event_type not in ALL_EVENT_TYPES:
            _LOGGER.debug("Ignoring unknown event type: %s", event_type)
            return
        self._trigger_event(event_type, data)
        self.async_write_ha_state()
        # Fire bus event for logbook descriptions
        self.hass.bus.async_fire(
            f"{DOMAIN}_event",
            {"event_type": event_type, **data},
        )


class AjaxDoorbellEvent(CoordinatorEntity[AjaxCobrandedCoordinator], EventEntity):
    """Per-device doorbell event entity living on the doorbell device card (#173).

    The hub-level `AjaxSecurityEvent` already fires `doorbell_pressed`, but it
    lives on the hub card so users watching the doorbell device saw no
    activity. This mirrors the ring onto the doorbell's own card with the
    idiomatic HA `doorbell` device class.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "doorbell"
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = [DOORBELL_EVENT_TYPE]

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"aegis_ajax_{device_id}_doorbell_event"
        device = coordinator.devices.get(device_id)
        if device is not None:
            self._attr_device_info = build_device_info(device, coordinator.rooms)
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, device_id)},
                manufacturer=MANUFACTURER,
            )

    @property
    def event_types(self) -> list[str]:
        return self._attr_event_types

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from coordinator when removed."""
        self.coordinator._device_event_entities.pop(self._device_id, None)
        await super().async_will_remove_from_hass()

    def handle_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Called by coordinator when a doorbell push for this device arrives."""
        if event_type != DOORBELL_EVENT_TYPE:
            return
        self._trigger_event(event_type, data)
        self.async_write_ha_state()
        self.hass.bus.async_fire(
            f"{DOMAIN}_event",
            {"event_type": event_type, **data},
        )
