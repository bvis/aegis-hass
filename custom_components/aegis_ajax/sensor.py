"""Sensor entities for Ajax Security."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.api.hts.hub_state import ELECTRICAL_DEVICE_TYPES
from custom_components.aegis_ajax.api.models import MonitoringCompanyStatus
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.entity import build_device_info

# Fallback voltage used to derive instantaneous power when the device
# hasn't reported a real voltage reading yet (#123). Recent WallSwitch
# firmwares emit `voltage` on HTS sub-key 0x35 (signed short, volts);
# older firmwares omit the field, so we land on this nominal value the
# Ajax mobile app uses as its labelled "230 V" baseline.
NOMINAL_GRID_VOLTAGE_V = 230.0

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
        if space.hub_id and coordinator.devices.get(space.hub_id):
            entities.append(
                AjaxHubMonitoringCompanySensor(
                    coordinator=coordinator,
                    space_id=space.id,
                    hub_id=space.hub_id,
                )
            )

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

    # Per-device electrical sensors for WallSwitch / Socket family (#123)
    for device_id, device in coordinator.devices.items():
        if device.device_type in ELECTRICAL_DEVICE_TYPES:
            entities.append(AjaxDeviceCurrentSensor(coordinator, device_id))
            entities.append(AjaxDeviceVoltageSensor(coordinator, device_id))
            entities.append(AjaxDeviceEnergyConsumedSensor(coordinator, device_id))
            entities.append(AjaxDeviceDerivedPowerSensor(coordinator, device_id))

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


class AjaxHubMonitoringCompanySensor(CoordinatorEntity[AjaxCobrandedCoordinator], SensorEntity):
    """Diagnostic sensor exposing approved CRA company names for a hub."""

    _attr_has_entity_name = True
    _attr_translation_key = "monitoring_company"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AjaxCobrandedCoordinator, space_id: str, hub_id: str) -> None:
        super().__init__(coordinator)
        self._space_id = space_id
        self._hub_id = hub_id
        self._attr_unique_id = f"aegis_ajax_{hub_id}_monitoring_company"
        hub_device = coordinator.devices.get(hub_id)
        if hub_device:
            self._attr_device_info = build_device_info(hub_device, coordinator.rooms)

    @property
    def available(self) -> bool:
        space = self.coordinator.spaces.get(self._space_id)
        return space is not None and space.monitoring_companies_loaded

    @property
    def native_value(self) -> str | None:
        space = self.coordinator.spaces.get(self._space_id)
        if space is None:
            return None
        approved = [company.name for company in space.approved_monitoring_companies if company.name]
        if not approved:
            return None
        if len(approved) == 1:
            return approved[0]
        return "multiple"

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        space = self.coordinator.spaces.get(self._space_id)
        if space is None:
            return {}
        attrs: dict[str, list[str]] = {
            "approved_companies": [],
            "pending_approval_companies": [],
            "pending_removal_companies": [],
        }
        for company in space.monitoring_companies:
            if not company.name:
                continue
            if company.status == MonitoringCompanyStatus.APPROVED:
                attrs["approved_companies"].append(company.name)
            elif company.status == MonitoringCompanyStatus.PENDING_APPROVAL:
                attrs["pending_approval_companies"].append(company.name)
            elif company.status == MonitoringCompanyStatus.PENDING_DELETION:
                attrs["pending_removal_companies"].append(company.name)
        return attrs


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


# ---------------------------------------------------------------------------
# Per-device electrical sensors (WallSwitch / Socket family, #123)
# ---------------------------------------------------------------------------


class _AjaxDeviceReadingsBase(CoordinatorEntity[AjaxCobrandedCoordinator], SensorEntity):
    """Shared scaffold for the current / energy / power triplet.

    Each subclass picks its own translation_key, device_class, state_class
    and unit. All three pull from `coordinator.device_readings[device_id]`
    which is populated by the HTS path (see `_on_hts_device_kv`).
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        device = coordinator.devices.get(device_id)
        if device:
            self._attr_device_info = build_device_info(device, coordinator.rooms)

    @property
    def available(self) -> bool:
        device = self.coordinator.devices.get(self._device_id)
        if device is None or not device.is_online:
            return False
        # No reading has arrived yet — entity stays unavailable instead of
        # rendering 0.0, otherwise the Energy dashboard would integrate a
        # phantom zero baseline before the hub's first STATUS_BODY.
        return self._device_id in self.coordinator.device_readings


class AjaxDeviceCurrentSensor(_AjaxDeviceReadingsBase):
    """Live current draw of a WallSwitch / Socket-family device (A)."""

    _attr_translation_key = "current"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"aegis_ajax_{device_id}_current"

    @property
    def native_value(self) -> float | None:
        readings = self.coordinator.device_readings.get(self._device_id)
        if readings is None or readings.current_ma is None:
            return None
        return readings.current_ma / 1000.0


class AjaxDeviceVoltageSensor(_AjaxDeviceReadingsBase):
    """Live line voltage reported by a WallSwitch / Socket-family device (V).

    Comes straight from HTS sub-key 0x35 of the device's TLV block,
    no scaling. Older firmwares (pre-WallSwitch PRO 2.47 era) don't
    emit the sub-key — the entity then stays `unknown` until the
    device reports one.
    """

    _attr_translation_key = "voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"aegis_ajax_{device_id}_voltage"

    @property
    def native_value(self) -> float | None:
        readings = self.coordinator.device_readings.get(self._device_id)
        if readings is None or readings.voltage_v is None:
            return None
        return float(readings.voltage_v)


class AjaxDeviceEnergyConsumedSensor(_AjaxDeviceReadingsBase):
    """Cumulative electric energy consumed by the device (kWh).

    `total_increasing` ties the entity into HA's Energy dashboard. The
    Ajax PRO app exposes a "reset consumption meter" button on the same
    device card; if the user presses it, the meter restarts from zero
    and HA treats that as a meter reset rather than negative
    consumption, which is exactly the `total_increasing` contract.
    """

    _attr_translation_key = "energy_consumed"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"aegis_ajax_{device_id}_energy_consumed"

    @property
    def native_value(self) -> float | None:
        readings = self.coordinator.device_readings.get(self._device_id)
        if readings is None or readings.power_consumed_wh is None:
            return None
        return readings.power_consumed_wh / 1000.0


class AjaxDeviceDerivedPowerSensor(_AjaxDeviceReadingsBase):
    """Instantaneous power derived from current × voltage (W).

    Uses the device's reported voltage when present (HTS sub-key 0x35);
    falls back to `NOMINAL_GRID_VOLTAGE_V` only for firmwares that don't
    emit it. The Ajax mobile app renders the Power line on the device
    card the same way — `current × voltage` — so the HA value matches
    what the user sees in the official app.
    """

    _attr_translation_key = "power_derived"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_registry_enabled_default = False
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"aegis_ajax_{device_id}_power_derived"

    @property
    def native_value(self) -> float | None:
        readings = self.coordinator.device_readings.get(self._device_id)
        if readings is None or readings.current_ma is None:
            return None
        voltage = (
            float(readings.voltage_v)
            if readings.voltage_v is not None and readings.voltage_v > 0
            else NOMINAL_GRID_VOLTAGE_V
        )
        return (readings.current_ma / 1000.0) * voltage
