"""Binary sensor entities for Ajax Security."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.entity import build_device_info

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.aegis_ajax.api.models import Device

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BinarySensorTypeInfo:
    device_class: BinarySensorDeviceClass
    translation_key: str | None = None


BINARY_SENSOR_TYPES: dict[str, BinarySensorTypeInfo] = {
    "door_opened": BinarySensorTypeInfo(BinarySensorDeviceClass.DOOR),
    "motion_detected": BinarySensorTypeInfo(BinarySensorDeviceClass.MOTION),
    "smoke_detected": BinarySensorTypeInfo(BinarySensorDeviceClass.SMOKE),
    "leak_detected": BinarySensorTypeInfo(BinarySensorDeviceClass.MOISTURE),
    "tamper": BinarySensorTypeInfo(BinarySensorDeviceClass.TAMPER, "tamper"),
    "co_detected": BinarySensorTypeInfo(BinarySensorDeviceClass.CO),
    "high_temperature": BinarySensorTypeInfo(BinarySensorDeviceClass.HEAT),
    "monitoring_active": BinarySensorTypeInfo(BinarySensorDeviceClass.CONNECTIVITY, "monitoring"),
    "gsm_connected": BinarySensorTypeInfo(BinarySensorDeviceClass.CONNECTIVITY, "gsm"),
    "lid_opened": BinarySensorTypeInfo(BinarySensorDeviceClass.TAMPER, "lid"),
    "external_contact_broken": BinarySensorTypeInfo(BinarySensorDeviceClass.PROBLEM, "ext_contact"),
    "external_contact_alert": BinarySensorTypeInfo(BinarySensorDeviceClass.SAFETY, "ext_alert"),
    "case_drilling": BinarySensorTypeInfo(BinarySensorDeviceClass.TAMPER, "drilling"),
    "anti_masking": BinarySensorTypeInfo(BinarySensorDeviceClass.TAMPER, "anti_mask"),
    "malfunction": BinarySensorTypeInfo(BinarySensorDeviceClass.PROBLEM, "malfunction"),
    "interference": BinarySensorTypeInfo(BinarySensorDeviceClass.PROBLEM, "interference"),
    "relay_stuck": BinarySensorTypeInfo(BinarySensorDeviceClass.PROBLEM, "relay_stuck"),
    "always_active": BinarySensorTypeInfo(BinarySensorDeviceClass.RUNNING, "always_active"),
    "glass_break": BinarySensorTypeInfo(BinarySensorDeviceClass.SAFETY, "glass_break"),
    "vibration": BinarySensorTypeInfo(BinarySensorDeviceClass.VIBRATION, "vibration"),
    "wire_input_alert": BinarySensorTypeInfo(BinarySensorDeviceClass.SAFETY, "wire_input_alert"),
}

# Devices whose single "alert" entity should OR-reduce several hub status
# oneofs into one state, because Ajax hub firmwares disagree on which oneof
# carries the open/closed transition for wired inputs.
_WIRE_INPUT_DEVICE_TYPES: frozenset[str] = frozenset({"wire_input", "wire_input_mt"})
_WIRE_INPUT_ALERT_SOURCES: tuple[str, ...] = (
    "wire_input_alert",
    "external_contact_broken",
    "external_contact_alert",
)

_DEVICE_TYPE_SENSORS: dict[str, list[str]] = {
    "door_protect": ["door_opened", "tamper", "external_contact_broken", "external_contact_alert"],
    "door_protect_plus": [
        "door_opened",
        "tamper",
        "vibration",
        "external_contact_broken",
        "external_contact_alert",
    ],
    "door_protect_fibra": [
        "door_opened",
        "tamper",
        "external_contact_broken",
        "external_contact_alert",
    ],
    "door_protect_s": [
        "door_opened",
        "tamper",
        "external_contact_broken",
        "external_contact_alert",
    ],
    "door_protect_s_plus": [
        "door_opened",
        "tamper",
        "vibration",
        "external_contact_broken",
        "external_contact_alert",
    ],
    "door_protect_plus_fibra": [
        "door_opened",
        "tamper",
        "vibration",
        "external_contact_broken",
        "external_contact_alert",
    ],
    "door_protect_g3": [
        "door_opened",
        "tamper",
        "external_contact_broken",
        "external_contact_alert",
    ],
    "door_protect_plus_g3_fibra": [
        "door_opened",
        "tamper",
        "vibration",
        "external_contact_broken",
        "external_contact_alert",
    ],
    "motion_protect": ["motion_detected", "tamper"],
    "motion_protect_plus": ["motion_detected", "tamper"],
    "motion_protect_fibra": ["motion_detected", "tamper"],
    "motion_protect_plus_fibra": ["motion_detected", "tamper"],
    "motion_protect_outdoor": ["motion_detected", "tamper"],
    "motion_protect_curtain": ["motion_detected", "tamper"],
    "motion_protect_curtain_base": ["motion_detected", "tamper"],
    "motion_protect_curtain_outdoor_base": ["motion_detected", "tamper"],
    "motion_protect_curtain_outdoor_mini": ["motion_detected", "tamper"],
    "motion_protect_curtain_outdoor_plus": ["motion_detected", "tamper"],
    "motion_protect_g3": ["motion_detected", "tamper"],
    "motion_protect_g3_fibra": ["motion_detected", "tamper"],
    "motion_protect_g3_fibra_new": ["motion_detected", "tamper"],
    "motion_protect_plus_g3": ["motion_detected", "tamper"],
    "motion_protect_s": ["motion_detected", "tamper"],
    "motion_protect_s_plus": ["motion_detected", "tamper"],
    "motion_cam": ["motion_detected", "tamper"],
    "motion_cam_outdoor": ["motion_detected", "tamper"],
    "motion_cam_fibra": ["motion_detected", "tamper"],
    "motion_cam_fibra_base": ["motion_detected", "tamper"],
    "motion_cam_g3": ["motion_detected", "tamper"],
    "motion_cam_hd": ["motion_detected", "tamper"],
    "motion_cam_phod": ["motion_detected", "tamper"],
    "motion_cam_phod_fibra": ["motion_detected", "tamper"],
    "motion_cam_outdoor_phod": ["motion_detected", "tamper"],
    "motion_cam_outdoor_two_four_phod": ["motion_detected", "tamper"],
    "motion_cam_s_phod": ["motion_detected", "tamper"],
    "motion_cam_s_phod_am": ["motion_detected", "tamper"],
    "motion_cam_superior_phod": ["motion_detected", "tamper"],
    "combi_protect": ["motion_detected", "glass_break", "tamper"],
    "combi_protect_s": ["motion_detected", "glass_break", "tamper"],
    "combi_protect_fibra": ["motion_detected", "glass_break", "tamper"],
    "glass_protect": ["glass_break", "tamper"],
    "glass_protect_s": ["glass_break", "tamper"],
    "glass_protect_fibra": ["glass_break", "tamper"],
    "fire_protect": ["smoke_detected", "high_temperature", "tamper"],
    "fire_protect_plus": ["smoke_detected", "co_detected", "high_temperature", "tamper"],
    # FireProtect 2 family. Ajax's hub catalog uses both `_2` (legacy) and
    # `_two*` (current) naming for the same generation of detectors. Map all
    # variants to the same expressive sensor set; sub-models that don't have
    # CO/smoke/heat physically just leave the corresponding entity at False.
    "fire_protect_2": ["smoke_detected", "co_detected", "high_temperature", "tamper"],
    "fire_protect_two": ["smoke_detected", "co_detected", "high_temperature", "tamper"],
    "fire_protect_two_base": ["smoke_detected", "co_detected", "high_temperature", "tamper"],
    "fire_protect_two_plus": ["smoke_detected", "co_detected", "high_temperature", "tamper"],
    "fire_protect_two_plus_sb": ["smoke_detected", "co_detected", "high_temperature", "tamper"],
    "fire_protect_two_sb": ["smoke_detected", "co_detected", "high_temperature", "tamper"],
    "fire_protect_two_hcrb": ["smoke_detected", "co_detected", "high_temperature", "tamper"],
    "fire_protect_two_hcsb": ["smoke_detected", "co_detected", "high_temperature", "tamper"],
    "fire_protect_two_hrb": ["high_temperature", "tamper"],
    "fire_protect_two_hsb": ["high_temperature", "tamper"],
    "fire_protect_two_crb": ["co_detected", "tamper"],
    "fire_protect_two_csb": ["co_detected", "tamper"],
    "fire_protect_two_h_ac": ["high_temperature", "tamper"],
    "fire_protect_two_c_ac": ["co_detected", "tamper"],
    "fire_protect_two_hc_ac": ["co_detected", "high_temperature", "tamper"],
    "fire_protect_two_hs_ac": ["smoke_detected", "high_temperature", "tamper"],
    "fire_protect_two_hsc_ac": ["smoke_detected", "co_detected", "high_temperature", "tamper"],
    # UL-listed (US-market) variants — same sensor set as the EU sibling.
    "fire_protect_two_c_rb_ul": ["co_detected", "tamper"],
    "fire_protect_two_h_rb_ul": ["high_temperature", "tamper"],
    "fire_protect_two_hs_ac_ul": ["smoke_detected", "high_temperature", "tamper"],
    "fire_protect_two_hs_rb_ul": ["smoke_detected", "high_temperature", "tamper"],
    "fire_protect_two_hs_sb_ul": ["smoke_detected", "high_temperature", "tamper"],
    "fire_protect_two_hsc_ac_ul": [
        "smoke_detected",
        "co_detected",
        "high_temperature",
        "tamper",
    ],
    "fire_protect_two_hsc_rb_ul": [
        "smoke_detected",
        "co_detected",
        "high_temperature",
        "tamper",
    ],
    "fire_protect_two_hsc_sb_ul": [
        "smoke_detected",
        "co_detected",
        "high_temperature",
        "tamper",
    ],
    "leaks_protect": ["leak_detected", "tamper"],
    "home_siren": ["tamper"],
    "home_siren_s": ["tamper"],
    "home_siren_fibra": ["tamper"],
    "home_siren_g3": ["tamper"],
    "street_siren": ["tamper"],
    "street_siren_plus": ["tamper"],
    "street_siren_fibra": ["tamper"],
    "street_siren_plus_fibra": ["tamper"],
    "street_siren_plus_g3": ["tamper"],
    "street_siren_s": ["tamper"],
    "street_siren_double_deck": ["tamper"],
    "street_siren_s_double_deck": ["tamper"],
    "street_siren_double_deck_fibra": ["tamper"],
    # ReX / ReX 2 — kept legacy "rex" / "rex_2" keys for backwards compatibility
    # with any deployed setup that may have observed them; current cloud
    # naming is `range_extender` / `range_extender_2`.
    "rex": [],
    "rex_2": [],
    "range_extender": [],
    "range_extender_2": [],
    "range_extender_2_fire": ["smoke_detected", "high_temperature", "tamper"],
    "transmitter": ["tamper"],
    "multi_transmitter": ["tamper"],
    "multi_transmitter_fibra": ["tamper"],
    "wire_input": ["tamper", "wire_input_alert"],
    "wire_input_mt": ["tamper", "wire_input_alert"],
    "wire_input_rs": ["tamper", "wire_input_alert"],
    "life_quality": [],
    "life_quality_plus": [],
    "water_stop": [],
    "water_stop_base": [],
    "keypad_combi": ["tamper"],
    "keypad_plus": ["tamper"],
    "keypad_plus_g3": ["tamper"],
    "keypad_s_plus": ["tamper"],
    "keypad_outdoor": ["tamper"],
    "keypad_outdoor_fibra": ["tamper"],
    "keypad_touchscreen": ["tamper"],
    "keypad_touchscreen_fibra": ["tamper"],
    "keypad_touchscreen_g3": ["tamper"],
    # Hub family. Modern firmwares use `_two`, `_two_plus`, etc.; legacy data
    # may still expose `hub_two_4g`. Keep them all mapped to the same set.
    "hub": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_plus": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_4g": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_lite": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_two": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_two_plus": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_two_4g": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_two_lte_rtk": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_three": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_fibra": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_hybrid_2": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_hybrid_4g": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_mega": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_void_4g": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_yavir": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_yavir_plus": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_fire": ["monitoring_active", "gsm_connected", "lid_opened"],
    "hub_superior": ["monitoring_active", "gsm_connected", "lid_opened"],
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AjaxCobrandedCoordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for device_id, device in coordinator.devices.items():
        sensor_keys = _DEVICE_TYPE_SENSORS.get(device.device_type, ["tamper"])
        for key in sensor_keys:
            if key in BINARY_SENSOR_TYPES:
                entities.append(
                    AjaxBinarySensor(coordinator=coordinator, device_id=device_id, status_key=key)
                )
        entities.append(AjaxConnectivitySensor(coordinator=coordinator, device_id=device_id))
        entities.append(AjaxProblemSensor(coordinator=coordinator, device_id=device_id))

    # Hub-level network sensors from HTS
    for space in coordinator.spaces.values():
        if space.hub_id:
            hub_device = coordinator.devices.get(space.hub_id)
            if hub_device:
                entities.append(AjaxHubEthernetSensor(coordinator, space.hub_id))
                entities.append(AjaxHubWifiSensor(coordinator, space.hub_id))
                entities.append(AjaxHubPowerSensor(coordinator, space.hub_id))
    async_add_entities(entities)


class AjaxBinarySensor(CoordinatorEntity[AjaxCobrandedCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AjaxCobrandedCoordinator, device_id: str, status_key: str
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._status_key = status_key
        self._type_info = BINARY_SENSOR_TYPES[status_key]
        self._attr_unique_id = f"aegis_ajax_{device_id}_{status_key}"
        self._attr_device_class = self._type_info.device_class
        self._attr_translation_key = self._type_info.translation_key
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
    def is_on(self) -> bool:
        device = self._device
        if device is None:
            return False
        if (
            self._status_key == "wire_input_alert"
            and device.device_type in _WIRE_INPUT_DEVICE_TYPES
        ):
            # Different hub firmwares signal the wired-input trigger through
            # different oneofs. Treat any of them as "alert active".
            return any(
                bool(device.statuses.get(source, False)) for source in _WIRE_INPUT_ALERT_SOURCES
            )
        return bool(device.statuses.get(self._status_key, False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        device = self._device
        if device is None:
            return {}
        if self._status_key == "motion_detected":
            detected_at = device.statuses.get("motion_detected_at")
            if detected_at is not None:
                return {"detected_at": detected_at}
            return {}
        if self._status_key == "wire_input_alert":
            alarm_type = device.statuses.get("wire_input_alarm_type")
            if alarm_type is not None:
                return {"alarm_type": alarm_type}
            return {}
        return {}


class AjaxConnectivitySensor(CoordinatorEntity[AjaxCobrandedCoordinator], BinarySensorEntity):
    """Binary sensor reporting per-device online/offline connectivity."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connectivity"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"aegis_ajax_{device_id}_connectivity"
        device = coordinator.devices.get(device_id)
        if device:
            self._attr_device_info = build_device_info(device, coordinator.rooms)

    @property
    def is_on(self) -> bool:
        device = self.coordinator.devices.get(self._device_id)
        return device is not None and device.is_online


class AjaxProblemSensor(CoordinatorEntity[AjaxCobrandedCoordinator], BinarySensorEntity):
    """Binary sensor reporting per-device malfunction/problem state."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "problem"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"aegis_ajax_{device_id}_problem"
        device = coordinator.devices.get(device_id)
        if device:
            self._attr_device_info = build_device_info(device, coordinator.rooms)

    @property
    def available(self) -> bool:
        return self.coordinator.devices.get(self._device_id) is not None

    @property
    def is_on(self) -> bool:
        device = self.coordinator.devices.get(self._device_id)
        return device is not None and device.malfunctions > 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        device = self.coordinator.devices.get(self._device_id)
        if device:
            return {"malfunctions_count": device.malfunctions}
        return {}


class _HubNetworkBinarySensor(CoordinatorEntity[AjaxCobrandedCoordinator], BinarySensorEntity):
    """Base for hub-level binary sensors sourced from HTS network data."""

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


class AjaxHubEthernetSensor(_HubNetworkBinarySensor):
    """Hub ethernet link status."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "ethernet"

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id)
        self._attr_unique_id = f"aegis_ajax_{hub_id}_ethernet"

    @property
    def is_on(self) -> bool:
        state = self.coordinator.hub_network.get(self._hub_id)
        return state.ethernet_connected if state else False


class AjaxHubWifiSensor(_HubNetworkBinarySensor):
    """Hub Wi-Fi link status."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "wifi"

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id)
        self._attr_unique_id = f"aegis_ajax_{hub_id}_wifi"

    @property
    def is_on(self) -> bool:
        state = self.coordinator.hub_network.get(self._hub_id)
        return state.wifi_connected if state else False


class AjaxHubPowerSensor(_HubNetworkBinarySensor):
    """Hub mains power status."""

    _attr_device_class = BinarySensorDeviceClass.PLUG
    _attr_translation_key = "mains_power"

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id)
        self._attr_unique_id = f"aegis_ajax_{hub_id}_mains_power"

    @property
    def is_on(self) -> bool:
        state = self.coordinator.hub_network.get(self._hub_id)
        return state.externally_powered if state else False
