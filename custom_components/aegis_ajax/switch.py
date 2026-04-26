"""Switch entities for Ajax Security."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.api.models import DeviceCommand
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.entity import build_device_info

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.aegis_ajax.api.models import Device

_LOGGER = logging.getLogger(__name__)

SWITCH_DEVICE_TYPES: dict[str, int] = {
    "relay": 1,
    "relay_fibra_base": 1,
    "wall_switch": 1,
    "socket": 1,
    "socket_b": 1,
    "socket_g": 1,
    "socket_outlet_type_e": 1,
    "socket_outlet_type_f": 1,
    "socket_type_g_plus": 1,
    "light_switch": 1,
    "light_switch_one_gang": 1,
    "light_switch_one_gang_na": 1,
    "light_switch_2_way": 1,
    "light_switch_crossover": 1,
    "light_switch_three_way_na": 1,
    "light_switch_two_gang": 2,
    "light_switch_two_channel_two_way": 2,
    "light_switch_four_way_na": 4,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AjaxCobrandedCoordinator = entry.runtime_data
    entities: list[AjaxSwitch] = []
    for device_id, device in coordinator.devices.items():
        num_channels = SWITCH_DEVICE_TYPES.get(device.device_type, 0)
        for ch in range(1, num_channels + 1):
            entities.append(
                AjaxSwitch(
                    coordinator=coordinator,
                    device_id=device_id,
                    hub_id=device.hub_id,
                    device_type=device.device_type,
                    channel=ch,
                )
            )
    async_add_entities(entities)


class AjaxSwitch(CoordinatorEntity[AjaxCobrandedCoordinator], SwitchEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AjaxCobrandedCoordinator,
        device_id: str,
        hub_id: str,
        device_type: str,
        channel: int,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._hub_id = hub_id
        self._device_type = device_type
        self._channel = channel
        self._attr_unique_id = f"aegis_ajax_{device_id}_switch_{channel}"
        total_channels = SWITCH_DEVICE_TYPES.get(device_type, 1)
        if total_channels > 1:
            self._attr_translation_key = f"channel_{channel}"
        else:
            self._attr_name = None
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
    def is_on(self) -> bool | None:
        device = self._device
        if device is None:
            return None
        return bool(device.statuses.get(f"switch_ch{self._channel}", False))

    async def async_turn_on(self, **kwargs: object) -> None:
        cmd = DeviceCommand.on(
            hub_id=self._hub_id,
            device_id=self._device_id,
            device_type=self._device_type,
            channels=[self._channel],
        )
        await self.coordinator.devices_api.send_command(cmd)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: object) -> None:
        cmd = DeviceCommand.off(
            hub_id=self._hub_id,
            device_id=self._device_id,
            device_type=self._device_type,
            channels=[self._channel],
        )
        await self.coordinator.devices_api.send_command(cmd)
        await self.coordinator.async_request_refresh()
