"""Valve entities for Ajax WaterStop devices (#117, open/close #308).

State comes from `WaterStopChannel.state`. Open/close ride the generic
device on/off command path the integration already uses for relays and
sockets — there's no dedicated WaterStop command service. The valve is a
single-channel device, so commands target channel 1: opening the valve
sends device-on, closing sends device-off (the same polarity as the
`valve_ch1` state read, where a truthy channel means the valve is open).

`reports_position` stays False — the WaterStop is binary, not positional.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.valve import (
    ValveDeviceClass,
    ValveEntity,
    ValveEntityFeature,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.api.models import DeviceCommand
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.entity import async_send_device_command, build_device_info

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.aegis_ajax.api.models import Device

_LOGGER = logging.getLogger(__name__)

# Ajax catalog ships two WaterStop buckets — `water_stop` (Jeweller, the
# default wireless variant) and `water_stop_base` (Fibra, wired). Same
# `WaterStopChannel` payload, same parser path, same entity surface.
VALVE_DEVICE_TYPES: frozenset[str] = frozenset({"water_stop", "water_stop_base"})

# The WaterStop exposes a single valve channel; on/off commands target it.
_VALVE_CHANNEL = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AjaxCobrandedCoordinator = entry.runtime_data
    entities: list[AjaxValve] = []
    for device_id, device in coordinator.devices.items():
        if device.device_type in VALVE_DEVICE_TYPES:
            entities.append(AjaxValve(coordinator=coordinator, device_id=device_id))
    async_add_entities(entities)


class AjaxValve(CoordinatorEntity[AjaxCobrandedCoordinator], ValveEntity):
    """Read-only valve entity backed by `WaterStopChannel.state`."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = ValveDeviceClass.WATER
    _attr_supported_features = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE
    _attr_reports_position = False

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"aegis_ajax_{device_id}_valve"
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
    def is_closed(self) -> bool | None:
        device = self._device
        if device is None:
            return None
        # Parser leaves `valve_ch1` absent on STATE_UNKNOWN / UNSPECIFIED
        # — propagate as `unknown` instead of guessing.
        state = device.statuses.get("valve_ch1")
        if state is None:
            return None
        return not bool(state)

    @property
    def is_closing(self) -> bool:
        device = self._device
        if device is None:
            return False
        if not device.statuses.get("valve_ch1_transitioning"):
            return False
        # In transit + currently open → on its way to closed.
        return bool(device.statuses.get("valve_ch1"))

    @property
    def is_opening(self) -> bool:
        device = self._device
        if device is None:
            return False
        if not device.statuses.get("valve_ch1_transitioning"):
            return False
        return not bool(device.statuses.get("valve_ch1"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        device = self._device
        if device is None:
            return {}
        return {"stuck": bool(device.statuses.get("valve_ch1_stuck"))}

    async def async_open_valve(self, **kwargs: object) -> None:
        await self._send("on")

    async def async_close_valve(self, **kwargs: object) -> None:
        await self._send("off")

    async def _send(self, action: str) -> None:
        device = self._device
        if device is None:
            return
        factory = DeviceCommand.on if action == "on" else DeviceCommand.off
        cmd = factory(
            hub_id=device.hub_id,
            device_id=self._device_id,
            device_type=device.device_type,
            channels=[_VALVE_CHANNEL],
        )
        await async_send_device_command(self.coordinator, cmd)
