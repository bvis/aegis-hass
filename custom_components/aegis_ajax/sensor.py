"""Sensor entities for Ajax Security."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.entity import build_device_info

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.aegis_ajax.api.hub_object import SimCardInfo
    from custom_components.aegis_ajax.api.models import Device

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SensorTypeInfo:
    device_class: SensorDeviceClass | None
    state_class: SensorStateClass | None
    unit: str | None
    value_source: str
    entity_category: EntityCategory | None
    translation_key: str | None = None
    entity_registry_enabled_default: bool = True


SENSOR_TYPES: dict[str, SensorTypeInfo] = {
    "battery_level": SensorTypeInfo(
        SensorDeviceClass.BATTERY,
        SensorStateClass.MEASUREMENT,
        PERCENTAGE,
        "battery",
        EntityCategory.DIAGNOSTIC,
    ),
    "temperature": SensorTypeInfo(
        SensorDeviceClass.TEMPERATURE,
        SensorStateClass.MEASUREMENT,
        UnitOfTemperature.CELSIUS,
        "status",
        None,
    ),
    "humidity": SensorTypeInfo(
        SensorDeviceClass.HUMIDITY,
        SensorStateClass.MEASUREMENT,
        PERCENTAGE,
        "status",
        None,
    ),
    "co2": SensorTypeInfo(
        SensorDeviceClass.CO2,
        SensorStateClass.MEASUREMENT,
        "ppm",
        "status",
        None,
    ),
    "signal_strength": SensorTypeInfo(
        None,
        None,
        None,
        "status",
        EntityCategory.DIAGNOSTIC,
        translation_key="signal_strength",
        entity_registry_enabled_default=False,
    ),
    "mobile_network_type": SensorTypeInfo(
        None,
        None,
        None,
        "status",
        EntityCategory.DIAGNOSTIC,
        translation_key="mobile_network_type",
        entity_registry_enabled_default=False,
    ),
    "wifi_signal_level": SensorTypeInfo(
        None,
        SensorStateClass.MEASUREMENT,
        None,
        "status",
        EntityCategory.DIAGNOSTIC,
        translation_key="wifi_signal_level",
        entity_registry_enabled_default=False,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AjaxCobrandedCoordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for device_id, device in coordinator.devices.items():
        if device.battery is not None:
            entities.append(
                AjaxSensor(coordinator=coordinator, device_id=device_id, sensor_key="battery_level")
            )
        _status_sensor_keys = (
            "temperature",
            "humidity",
            "co2",
            "signal_strength",
            "mobile_network_type",
            "wifi_signal_level",
        )
        for key in _status_sensor_keys:
            if key in device.statuses:
                entities.append(
                    AjaxSensor(coordinator=coordinator, device_id=device_id, sensor_key=key)
                )

    # Add SIM sensors for hub devices that have SIM info
    for space in coordinator.spaces.values():
        if space.hub_id in coordinator.sim_info:
            entities.append(AjaxSimImeiSensor(coordinator=coordinator, hub_id=space.hub_id))

    # Hub-level network sensors from HTS
    for space in coordinator.spaces.values():
        if space.hub_id and coordinator.devices.get(space.hub_id):
            entities.append(AjaxHubConnectionTypeSensor(coordinator, space.hub_id))
            entities.append(AjaxHubWifiSsidSensor(coordinator, space.hub_id))
            entities.append(AjaxHubWifiSignalSensor(coordinator, space.hub_id))
            entities.append(AjaxHubWifiIpSensor(coordinator, space.hub_id))
            entities.append(AjaxHubEthernetIpSensor(coordinator, space.hub_id))
            entities.append(AjaxHubEthernetGatewaySensor(coordinator, space.hub_id))
            entities.append(AjaxHubEthernetDnsSensor(coordinator, space.hub_id))
            entities.append(AjaxHubCellularSignalSensor(coordinator, space.hub_id))
            entities.append(AjaxHubCellularNetworkSensor(coordinator, space.hub_id))

    async_add_entities(entities)


class AjaxSensor(CoordinatorEntity[AjaxCobrandedCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AjaxCobrandedCoordinator, device_id: str, sensor_key: str
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._sensor_key = sensor_key
        self._type_info = SENSOR_TYPES[sensor_key]
        self._attr_unique_id = f"aegis_ajax_{device_id}_{sensor_key}"
        self._attr_device_class = self._type_info.device_class
        self._attr_state_class = self._type_info.state_class
        self._attr_native_unit_of_measurement = self._type_info.unit
        self._attr_entity_category = self._type_info.entity_category
        self._attr_translation_key = self._type_info.translation_key
        self._attr_entity_registry_enabled_default = self._type_info.entity_registry_enabled_default
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
    def native_value(self) -> float | int | str | None:
        device = self._device
        if device is None:
            return None
        if self._type_info.value_source == "battery" and device.battery:
            return int(device.battery.level)
        raw = device.statuses.get(self._sensor_key)
        if raw is None:
            return None
        if isinstance(raw, str):
            return raw
        return float(raw) if isinstance(raw, float) else int(raw)


class AjaxSimBaseSensor(CoordinatorEntity[AjaxCobrandedCoordinator], SensorEntity):
    """Base class for SIM card sensors attached to a hub device."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator)
        self._hub_id = hub_id
        # Find hub device to populate device_info
        hub_device = coordinator.devices.get(hub_id)
        if hub_device:
            self._attr_device_info = build_device_info(hub_device, coordinator.rooms)

    @property
    def _sim_info(self) -> SimCardInfo | None:
        return self.coordinator.sim_info.get(self._hub_id)

    @property
    def available(self) -> bool:
        return self._sim_info is not None


class AjaxSimImeiSensor(AjaxSimBaseSensor):
    """Sensor exposing the hub IMEI number."""

    _attr_translation_key = "sim_imei"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id)
        self._attr_unique_id = f"aegis_ajax_{hub_id}_sim_imei"

    @property
    def native_value(self) -> str | None:
        sim = self._sim_info
        return sim.imei if sim else None


# ---------------------------------------------------------------------------
# Hub network sensors (from HTS)
# ---------------------------------------------------------------------------


class _HubNetworkSensor(CoordinatorEntity[AjaxCobrandedCoordinator], SensorEntity):
    """Base for hub-level sensors from HTS network data."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator)
        self._hub_id = hub_id
        hub_device = coordinator.devices.get(hub_id)
        if hub_device:
            self._attr_device_info = build_device_info(hub_device, coordinator.rooms)

    @property
    def available(self) -> bool:
        return self._hub_id in self.coordinator.hub_network


class AjaxHubConnectionTypeSensor(_HubNetworkSensor):
    """Primary connection type: ethernet, wifi, gsm, or none."""

    _attr_translation_key = "connection_type"
    _attr_entity_registry_enabled_default = True  # useful summary, keep enabled

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id)
        self._attr_unique_id = f"aegis_ajax_{hub_id}_connection_type"

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.hub_network.get(self._hub_id)
        return state.primary_connection if state else None


class AjaxHubWifiSsidSensor(_HubNetworkSensor):
    """Hub Wi-Fi SSID."""

    _attr_translation_key = "wifi_ssid"

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id)
        self._attr_unique_id = f"aegis_ajax_{hub_id}_wifi_ssid"

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.hub_network.get(self._hub_id)
        return state.wifi_ssid if state and state.wifi_ssid else None


class AjaxHubWifiSignalSensor(_HubNetworkSensor):
    """Hub Wi-Fi signal level."""

    _attr_translation_key = "wifi_signal_level"

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id)
        self._attr_unique_id = f"aegis_ajax_{hub_id}_wifi_signal_level"

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.hub_network.get(self._hub_id)
        return state.wifi_signal_level if state else None


class AjaxHubWifiIpSensor(_HubNetworkSensor):
    """Hub Wi-Fi IP address."""

    _attr_translation_key = "wifi_ip"

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id)
        self._attr_unique_id = f"aegis_ajax_{hub_id}_wifi_ip"

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.hub_network.get(self._hub_id)
        return state.wifi_ip if state and state.wifi_ip else None


class AjaxHubEthernetIpSensor(_HubNetworkSensor):
    """Hub ethernet IP address."""

    _attr_translation_key = "ethernet_ip"

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id)
        self._attr_unique_id = f"aegis_ajax_{hub_id}_ethernet_ip"

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.hub_network.get(self._hub_id)
        return state.ethernet_ip if state and state.ethernet_ip else None


class AjaxHubEthernetGatewaySensor(_HubNetworkSensor):
    """Hub ethernet gateway address."""

    _attr_translation_key = "ethernet_gateway"

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id)
        self._attr_unique_id = f"aegis_ajax_{hub_id}_ethernet_gateway"

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.hub_network.get(self._hub_id)
        return state.ethernet_gateway if state and state.ethernet_gateway else None


class AjaxHubEthernetDnsSensor(_HubNetworkSensor):
    """Hub ethernet DNS server."""

    _attr_translation_key = "ethernet_dns"

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id)
        self._attr_unique_id = f"aegis_ajax_{hub_id}_ethernet_dns"

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.hub_network.get(self._hub_id)
        return state.ethernet_dns if state and state.ethernet_dns else None


class AjaxHubCellularSignalSensor(_HubNetworkSensor):
    """Hub cellular signal level."""

    _attr_translation_key = "cellular_signal"

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id)
        self._attr_unique_id = f"aegis_ajax_{hub_id}_cellular_signal"

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.hub_network.get(self._hub_id)
        return state.gsm_signal_level if state else None


class AjaxHubCellularNetworkSensor(_HubNetworkSensor):
    """Hub cellular network type (2g/3g/4g)."""

    _attr_translation_key = "cellular_network"

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id)
        self._attr_unique_id = f"aegis_ajax_{hub_id}_cellular_network"

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.hub_network.get(self._hub_id)
        return state.gsm_network_type if state else None
