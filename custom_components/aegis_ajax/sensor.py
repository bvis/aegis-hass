"""Sensor entities for Ajax Security."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
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

from custom_components.aegis_ajax.api.models import MonitoringCompanyStatus
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.device_handlers import capabilities_for
from custom_components.aegis_ajax.entity import build_device_info

# Fallback voltage used to derive instantaneous power when the device
# hasn't reported a real voltage reading yet (#123). Recent WallSwitch
# firmwares report a measured voltage to the hub; older firmwares omit
# it, so we land on this nominal value — the same baseline the official
# app uses when no measurement is available.
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
    # NVR/video-edge box channel counters (#425) — the only per-row
    # measurements the recorder's light row carries. Creation is gated on
    # the keys being present, so only the box grows these.
    "channels_online": SensorTypeInfo(
        None,
        SensorStateClass.MEASUREMENT,
        None,
        "status",
        None,
        translation_key="channels_online",
    ),
    "channels_total": SensorTypeInfo(
        None,
        None,
        None,
        "status",
        EntityCategory.DIAGNOSTIC,
        translation_key="channels_total",
    ),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AjaxCobrandedCoordinator = entry.runtime_data
    entities: list[SensorEntity] = []

    def _should_create_status_sensor(device: Device, key: str) -> bool:
        if key == "temperature" and capabilities_for(device).has_hts_temperature:
            return True
        return key in device.statuses

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
            "channels_online",
            "channels_total",
        )
        for key in _status_sensor_keys:
            if _should_create_status_sensor(device, key):
                entities.append(
                    AjaxSensor(coordinator=coordinator, device_id=device_id, sensor_key=key)
                )

    # Add SIM sensors for hub devices that have SIM info
    for space in coordinator.spaces.values():
        if space.hub_id and coordinator.devices.get(space.hub_id):
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
    # and Outlet Type E / F (#179, calibrated in 1.5.3-beta.11).
    _remove_orphan_outlet_power_derived(hass, coordinator)
    for device_id, device in coordinator.devices.items():
        capabilities = capabilities_for(device)
        if capabilities.has_electrical_readings:
            entities.append(AjaxDeviceCurrentSensor(coordinator, device_id))
            entities.append(AjaxDeviceVoltageSensor(coordinator, device_id))
            entities.append(AjaxDeviceEnergyConsumedSensor(coordinator, device_id))
            if capabilities.has_direct_power:
                entities.append(AjaxDevicePowerSensor(coordinator, device_id))
            else:
                entities.append(AjaxDeviceDerivedPowerSensor(coordinator, device_id))

    async_add_entities(entities)


def _remove_orphan_outlet_power_derived(
    hass: HomeAssistant, coordinator: AjaxCobrandedCoordinator
) -> None:
    """Drop the `_power_derived` entity for Outlet Type E / F devices (#179).

    Between `1.4.0` (when the WallSwitch family's derived-power entity
    first shipped) and `1.5.3-beta.1` (when the Outlet was excluded
    from the electrical-readings key map while we figured out its sub-key
    map), users on Outlets got a `_power_derived` entity registered
    with WallSwitch-shaped (and incorrect) parsing behind it. From
    `1.5.3-beta.11` the Outlet emits a real `_power` sensor instead;
    the legacy `_power_derived` lingers in the entity registry as
    `unavailable` until the user deletes it by hand. This helper
    sweeps the registry once per setup and removes it cleanly.
    Touches only devices whose `has_direct_power` capability is set;
    WallSwitch family's own `_power_derived` is untouched.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    registry = er.async_get(hass)
    removed = 0
    for device_id, device in coordinator.devices.items():
        if not capabilities_for(device).has_direct_power:
            continue
        unique_id = f"aegis_ajax_{device_id}_power_derived"
        entity_id = registry.async_get_entity_id("sensor", "aegis_ajax", unique_id)
        if entity_id is None:
            continue
        registry.async_remove(entity_id)
        removed += 1
    if removed:
        _LOGGER.info(
            "Removed %d orphan `_power_derived` sensor(s) for Outlet devices — "
            "superseded by direct `_power` entity in 1.5.3-beta.11",
            removed,
        )


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
            self._attr_device_info = build_device_info(
                device, coordinator.rooms, via_device_id=coordinator.hub_registry_id(device.hub_id)
            )

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
        # Show the actual names (sorted for stable rendering across polls) so
        # the card is readable at a glance. HA truncates state strings at
        # 255 chars; fall back to a count if the joined names exceed that.
        joined = ", ".join(sorted(approved))
        if len(joined) > 255:
            return f"{len(approved)} companies"
        return joined

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


@dataclass(frozen=True)
class _HubNetSpec:
    """Describes one hub-network diagnostic sensor (translation_key, source
    attribute on `HubNetworkState`, plus a couple of small flags).

    `unique_id_suffix` always equals `translation_key` so the entity-registry
    ids match what users have stored from earlier integration versions —
    do not rename either field without a migration.
    """

    translation_key: str
    state_attr: str
    enabled_default: bool = False
    # Treat empty string from the proto as `None` so HA renders the entity
    # as `unknown` (matches the pre-collapse per-class behaviour for IPs,
    # SSID, gateway, DNS). Numeric-ish attrs ("unknown" signal level,
    # network type strings) skip this and return the raw value.
    empty_is_none: bool = False


_HUB_NET_SPECS: tuple[_HubNetSpec, ...] = (
    _HubNetSpec("connection_type", "primary_connection", enabled_default=True),
    _HubNetSpec("wifi_ssid", "wifi_ssid", empty_is_none=True),
    _HubNetSpec("wifi_signal_level", "wifi_signal_level"),
    _HubNetSpec("wifi_ip", "wifi_ip", empty_is_none=True),
    _HubNetSpec("ethernet_ip", "ethernet_ip", empty_is_none=True),
    _HubNetSpec("ethernet_gateway", "ethernet_gateway", empty_is_none=True),
    _HubNetSpec("ethernet_dns", "ethernet_dns", empty_is_none=True),
    _HubNetSpec("cellular_signal", "gsm_signal_level"),
    _HubNetSpec("cellular_network", "gsm_network_type"),
)
_HUB_NET_SPECS_BY_KEY: dict[str, _HubNetSpec] = {s.translation_key: s for s in _HUB_NET_SPECS}


class AjaxHubNetworkSensor(_HubNetworkSensor):
    """Generic descriptor-driven hub-network diagnostic sensor."""

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str, spec_key: str) -> None:
        super().__init__(coordinator, hub_id)
        spec = _HUB_NET_SPECS_BY_KEY[spec_key]
        self._spec = spec
        self._attr_translation_key = spec.translation_key
        self._attr_unique_id = f"aegis_ajax_{hub_id}_{spec.translation_key}"
        self._attr_entity_registry_enabled_default = spec.enabled_default

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.hub_network.get(self._hub_id)
        if state is None:
            return None
        value = getattr(state, self._spec.state_attr, None)
        if self._spec.empty_is_none and not value:
            return None
        return value


# Backwards-compatible aliases — the descriptor collapsed nine near-identical
# subclasses but tests and `async_setup_entry` reference these names directly,
# and downstream automations rely on the unique_ids these constructors set.
# Each subclass freezes one `_HubNetSpec` into the `(coordinator, hub_id)`
# signature so existing call sites keep working unchanged.
class AjaxHubConnectionTypeSensor(AjaxHubNetworkSensor):
    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id, "connection_type")


class AjaxHubWifiSsidSensor(AjaxHubNetworkSensor):
    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id, "wifi_ssid")


class AjaxHubWifiSignalSensor(AjaxHubNetworkSensor):
    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id, "wifi_signal_level")


class AjaxHubWifiIpSensor(AjaxHubNetworkSensor):
    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id, "wifi_ip")


class AjaxHubEthernetIpSensor(AjaxHubNetworkSensor):
    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id, "ethernet_ip")


class AjaxHubEthernetGatewaySensor(AjaxHubNetworkSensor):
    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id, "ethernet_gateway")


class AjaxHubEthernetDnsSensor(AjaxHubNetworkSensor):
    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id, "ethernet_dns")


class AjaxHubCellularSignalSensor(AjaxHubNetworkSensor):
    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id, "cellular_signal")


class AjaxHubCellularNetworkSensor(AjaxHubNetworkSensor):
    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id, "cellular_network")


# ---------------------------------------------------------------------------
# Per-device electrical sensors (WallSwitch / Socket family, #123)
# ---------------------------------------------------------------------------


class _AjaxDeviceReadingsBase(CoordinatorEntity[AjaxCobrandedCoordinator], RestoreSensor):
    """Shared scaffold for the current / voltage / energy / power quartet.

    Each subclass picks its own translation_key, device_class, state_class
    and unit, and provides `_live_native_value` reading from
    `coordinator.device_readings[device_id]` (populated by the HTS path
    in `_on_hts_device_kv`).

    Restoration on boot (#123): some Ajax hubs only emit electrical
    readings via `STATUS_UPDATE` deltas on change, not in the initial
    `STATUS_BODY` snapshot. For loads that run at a constant rate for
    hours (e.g. a relay driving fixed-speed ventilation), no delta
    arrives until the load actually shifts, so the sensor would render
    `unknown` after every restart even though the last known value is
    still the truth. `RestoreSensor` lets us seed the entity from its
    last persisted state on HA boot; the value is refreshed in place
    as soon as the next live delta lands.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        # Per-entity fallback used when the coordinator has no live reading
        # yet but HA restored a state from the previous run. Populated by
        # `async_added_to_hass`; stays `None` on a fresh install.
        self._restored_native_value: float | None = None
        device = coordinator.devices.get(device_id)
        if device:
            self._attr_device_info = build_device_info(
                device, coordinator.rooms, via_device_id=coordinator.hub_registry_id(device.hub_id)
            )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is None or last.native_value is None:
            return
        try:
            self._restored_native_value = float(last.native_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            # State recorded as a non-numeric string (e.g. "unknown")
            # or a non-castable type (date/Decimal) — skip the restore.
            self._restored_native_value = None

    @property
    def _live_native_value(self) -> float | None:
        """Subclass-specific lookup against `coordinator.device_readings`."""
        raise NotImplementedError

    @property
    def native_value(self) -> float | None:
        live = self._live_native_value
        if live is not None:
            return live
        return self._restored_native_value

    @property
    def available(self) -> bool:
        device = self.coordinator.devices.get(self._device_id)
        if device is None or not device.is_online:
            return False
        # Entity is available if we have a live reading OR a restored
        # value from the previous run. Otherwise we'd render `unknown`
        # for hours on installs whose hub only emits readings on
        # change (#123 follow-up).
        return self._live_native_value is not None or self._restored_native_value is not None


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
    def _live_native_value(self) -> float | None:
        readings = self.coordinator.device_readings.get(self._device_id)
        if readings is None or readings.current_ma is None:
            return None
        return readings.current_ma / 1000.0


class AjaxDeviceVoltageSensor(_AjaxDeviceReadingsBase):
    """Live line voltage reported by a WallSwitch / Socket-family device (V).

    The parser has already normalised the reading to volts — the raw
    sub-key is whole volts on the WallSwitch/Socket families but
    millivolts on the Jeweller Relay (#325). Older firmwares don't
    report it at all — the entity then stays `unknown` until the device
    sends a reading.
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
    def _live_native_value(self) -> float | None:
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
    def _live_native_value(self) -> float | None:
        readings = self.coordinator.device_readings.get(self._device_id)
        if readings is None or readings.power_consumed_wh is None:
            return None
        return readings.power_consumed_wh / 1000.0


class AjaxDeviceDerivedPowerSensor(_AjaxDeviceReadingsBase):
    """Instantaneous power derived from current × voltage (W).

    Uses the device's reported voltage when present; falls back to
    `NOMINAL_GRID_VOLTAGE_V` only for firmwares that don't report
    one. Same `current × voltage` product the official app renders
    on the device card, so the HA value matches what the user sees.
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
    def _live_native_value(self) -> float | None:
        readings = self.coordinator.device_readings.get(self._device_id)
        if readings is None or readings.current_ma is None:
            return None
        voltage = (
            float(readings.voltage_v)
            if readings.voltage_v is not None and readings.voltage_v > 0
            else NOMINAL_GRID_VOLTAGE_V
        )
        return (readings.current_ma / 1000.0) * voltage


class AjaxDevicePowerSensor(_AjaxDeviceReadingsBase):
    """Instantaneous power reported directly by the device (W).

    Used by device families that include a power reading in the
    `STATUS_BODY` row (Outlet Type E / Type F, #179) — distinct from
    the WallSwitch derived sensor which multiplies current by voltage.
    Enabled by default because the reading is real, not estimated.
    """

    _attr_translation_key = "power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"aegis_ajax_{device_id}_power"

    @property
    def _live_native_value(self) -> float | None:
        readings = self.coordinator.device_readings.get(self._device_id)
        if readings is None or readings.power_w is None:
            return None
        return float(readings.power_w)
