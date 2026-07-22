"""Number entities for writable Ajax device settings (#310).

Currently exposes the siren **alarm duration** (seconds). An entity is created
for every siren device type the rich `StreamHubDevice` proto models a
`common_siren_part` for (`SIREN_DEVICE_TYPES`) — the entity is created at setup
regardless of whether its current value has been fetched yet, so it appears on
first boot without waiting for the background settings refresh (it reads
`unknown` until the first snapshot merges the value). Writes go through the
shared `UpdateHubDevice` command path (`DeviceCommand.set_siren_settings`),
which the account can only perform with device-edit permission — a hub
rejection is surfaced as a translated `HomeAssistantError`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.api.models import DeviceCommand
from custom_components.aegis_ajax.const import (
    SIREN_ALARM_DURATION_KEY,
    SIREN_ALARM_DURATION_MAX,
    SIREN_ALARM_DURATION_MIN,
    SIREN_ALARM_DURATION_STEP,
    SIREN_DEVICE_TYPES,
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
    entities: list[NumberEntity] = [
        AjaxSirenAlarmDurationNumber(coordinator=coordinator, device_id=device_id)
        for device_id, device in coordinator.devices.items()
        if device.device_type in SIREN_DEVICE_TYPES
    ]
    async_add_entities(entities)


class AjaxSirenAlarmDurationNumber(CoordinatorEntity[AjaxCobrandedCoordinator], NumberEntity):
    """Writable siren alarm-duration (seconds)."""

    _attr_has_entity_name = True
    _attr_translation_key = "siren_alarm_duration"
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_min_value = SIREN_ALARM_DURATION_MIN
    _attr_native_max_value = SIREN_ALARM_DURATION_MAX
    _attr_native_step = SIREN_ALARM_DURATION_STEP
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"aegis_ajax_{device_id}_siren_alarm_duration"
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
    def native_value(self) -> float | None:
        device = self._device
        if device is None:
            return None
        raw = device.statuses.get(SIREN_ALARM_DURATION_KEY)
        return float(raw) if raw is not None else None

    async def async_set_native_value(self, value: float) -> None:
        device = self._device
        if device is None:
            return
        cmd = DeviceCommand.set_siren_settings(
            hub_id=device.hub_id,
            device_id=device.id,
            device_type=device.device_type,
            alarm_duration=int(value),
        )
        await async_send_device_command(self.coordinator, cmd)
        # Accepted write: confirm the real hub value within seconds instead of
        # showing the stale one until the 900 s snapshot timer fires.
        self.coordinator.schedule_siren_settings_confirm(self._device_id)
