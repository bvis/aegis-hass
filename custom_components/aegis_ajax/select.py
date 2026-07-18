"""Select entities for writable Ajax device settings (#310).

Currently exposes the siren **volume level** for any siren whose snapshot
carries a `common_siren_part`. Entities are created purely from the presence of
the parser's `SIREN_VOLUME_LEVEL_KEY` status, so every siren SKU is covered
without a per-device-type table. Writes go through the shared `UpdateHubDevice`
command path (`DeviceCommand.set_siren_settings`); a hub rejection (e.g. the
account lacks device-edit permission) is surfaced as a translated
`HomeAssistantError`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.api.models import DeviceCommand
from custom_components.aegis_ajax.const import (
    SIREN_VOLUME_LEVEL_KEY,
    SIREN_VOLUME_LEVEL_VALUES,
    SIREN_VOLUME_LEVELS,
)
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.entity import async_send_device_command, build_device_info

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.aegis_ajax.api.models import Device

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AjaxCobrandedCoordinator = entry.runtime_data
    entities: list[SelectEntity] = [
        AjaxSirenVolumeSelect(coordinator=coordinator, device_id=device_id)
        for device_id, device in coordinator.devices.items()
        if SIREN_VOLUME_LEVEL_KEY in device.statuses
    ]
    async_add_entities(entities)


class AjaxSirenVolumeSelect(CoordinatorEntity[AjaxCobrandedCoordinator], SelectEntity):
    """Writable siren volume level."""

    _attr_has_entity_name = True
    _attr_translation_key = "siren_volume_level"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(SIREN_VOLUME_LEVELS.values())

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"aegis_ajax_{device_id}_siren_volume_level"
        device = coordinator.devices.get(device_id)
        if device:
            self._attr_device_info = build_device_info(device, coordinator.rooms)

    @property
    def _device(self) -> Device | None:
        return self.coordinator.devices.get(self._device_id)

    @property
    def available(self) -> bool:
        device = self._device
        return device is not None and device.is_online

    @property
    def current_option(self) -> str | None:
        device = self._device
        if device is None:
            return None
        raw = device.statuses.get(SIREN_VOLUME_LEVEL_KEY)
        return SIREN_VOLUME_LEVELS.get(raw) if raw is not None else None

    async def async_select_option(self, option: str) -> None:
        level = SIREN_VOLUME_LEVEL_VALUES.get(option)
        if level is None:
            return
        device = self._device
        if device is None:
            return
        cmd = DeviceCommand.set_siren_settings(
            hub_id=device.hub_id,
            device_id=device.id,
            device_type=device.device_type,
            siren_volume_level=level,
        )
        await async_send_device_command(self.coordinator, cmd)
