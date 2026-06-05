"""Binary sensor entities for Ajax Security."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.const import DOMAIN, MANUFACTURER, SIGNAL_NEW_DEVICE
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
    # DoorProtect Plus accelerometer fires `tilt` for sustained off-axis
    # movement (sensor pulled / pried). Modeled as TAMPER because the firmware
    # treats it as anti-removal, distinct from VIBRATION (door knocks).
    "tilt": BinarySensorTypeInfo(BinarySensorDeviceClass.TAMPER, "tilt"),
    # FireProtect 2 steam discriminator — true while the smoke chamber is
    # reading steam rather than smoke (e.g. shower/cooking false-positive).
    "steam": BinarySensorTypeInfo(BinarySensorDeviceClass.PROBLEM, "steam"),
    "wire_input_alert": BinarySensorTypeInfo(BinarySensorDeviceClass.SAFETY, "wire_input_alert"),
}

# Devices whose single "alert" entity should OR-reduce several hub status
# oneofs into one state, because Ajax hub firmwares disagree on which oneof
# carries the open/closed transition for wired inputs.
_WIRE_INPUT_DEVICE_TYPES: frozenset[str] = frozenset({"wire_input", "wire_input_mt", "transmitter"})
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
        "tilt",
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
        "tilt",
        "external_contact_broken",
        "external_contact_alert",
    ],
    "door_protect_plus_fibra": [
        "door_opened",
        "tamper",
        "vibration",
        "tilt",
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
        "tilt",
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
    # MotionCam Video family — streaming cameras (Video Doorbell, Indoor,
    # Base). Same on-device sensors as PhOD MotionCams; the video stream
    # itself is a separate concern (#119, see RingButtonPressed in
    # `VIDEO_EVENT_TAG_MAP` for the doorbell ring path).
    "motion_cam_video_base": ["motion_detected", "tamper"],
    "motion_cam_video_doorbell": ["motion_detected", "tamper"],
    "motion_cam_video_indoor": ["motion_detected", "tamper"],
    # VideoEdge channel devices — third-party / bridged cameras that
    # arrive on the `video_edge_channel` oneof of LightDevice rather
    # than `hub_device` (#119, @Permudious's MotionCam Video Doorbell
    # actually surfaces here, not under `motion_cam_video_*` as we
    # first guessed in beta.3). They don't carry the Jeweller-side
    # signal_strength / battery, so the device-agnostic sensors in
    # `sensor.py` skip themselves naturally; motion / tamper still
    # surface when the camera exposes them via `LightDeviceStatus`.
    "video_edge_bullet": ["motion_detected", "tamper"],
    "video_edge_doorbell": ["motion_detected", "tamper"],
    "video_edge_indoor": ["motion_detected", "tamper"],
    "video_edge_minidome": ["motion_detected", "tamper"],
    "video_edge_turret": ["motion_detected", "tamper"],
    "video_edge_unknown": ["motion_detected", "tamper"],
    "combi_protect": ["motion_detected", "glass_break", "tamper"],
    "combi_protect_s": ["motion_detected", "glass_break", "tamper"],
    "combi_protect_fibra": ["motion_detected", "glass_break", "tamper"],
    "glass_protect": ["glass_break", "tamper"],
    "glass_protect_s": ["glass_break", "tamper"],
    "glass_protect_fibra": ["glass_break", "tamper"],
    "fire_protect": ["smoke_detected", "high_temperature", "tamper"],
    "fire_protect_plus": ["smoke_detected", "co_detected", "high_temperature", "tamper"],
    # FireProtect 2 family. Ajax's hub catalog uses both `_2` (legacy) and
    # `_two*` (current) naming for the same generation of detectors.
    #
    # CO is the OPTIONAL cell across this line — only the SKUs whose object_type
    # name explicitly encodes it (`*_c*`, `*_hc*`, `*_hsc*`) physically have it.
    # The generic `fire_protect_two` case (which the cloud reports for plain
    # Heat/Smoke RB units that lack a dedicated enum variant) must therefore
    # NOT advertise a `co_detected` entity: doing so created a phantom CO sensor
    # stuck at "Clear" on detectors that have no CO cell at all (#231). Smoke +
    # heat are always present on a FireProtect 2, so they stay. A real CO alarm
    # on a CO-equipped unit still arrives via FCM push regardless of this map.
    "fire_protect_2": ["smoke_detected", "steam", "high_temperature", "tamper"],
    "fire_protect_two": ["smoke_detected", "steam", "high_temperature", "tamper"],
    "fire_protect_two_base": [
        "smoke_detected",
        "steam",
        "co_detected",
        "high_temperature",
        "tamper",
    ],
    "fire_protect_two_plus": [
        "smoke_detected",
        "steam",
        "co_detected",
        "high_temperature",
        "tamper",
    ],
    "fire_protect_two_plus_sb": [
        "smoke_detected",
        "steam",
        "co_detected",
        "high_temperature",
        "tamper",
    ],
    "fire_protect_two_sb": [
        "smoke_detected",
        "steam",
        "co_detected",
        "high_temperature",
        "tamper",
    ],
    "fire_protect_two_hcrb": [
        "smoke_detected",
        "steam",
        "co_detected",
        "high_temperature",
        "tamper",
    ],
    "fire_protect_two_hcsb": [
        "smoke_detected",
        "steam",
        "co_detected",
        "high_temperature",
        "tamper",
    ],
    "fire_protect_two_hrb": ["high_temperature", "tamper"],
    "fire_protect_two_hsb": ["high_temperature", "tamper"],
    "fire_protect_two_crb": ["co_detected", "tamper"],
    "fire_protect_two_csb": ["co_detected", "tamper"],
    "fire_protect_two_h_ac": ["high_temperature", "tamper"],
    "fire_protect_two_c_ac": ["co_detected", "tamper"],
    "fire_protect_two_hc_ac": ["co_detected", "high_temperature", "tamper"],
    "fire_protect_two_hs_ac": ["smoke_detected", "steam", "high_temperature", "tamper"],
    "fire_protect_two_hsc_ac": [
        "smoke_detected",
        "steam",
        "co_detected",
        "high_temperature",
        "tamper",
    ],
    # UL-listed (US-market) variants — same sensor set as the EU sibling.
    "fire_protect_two_c_rb_ul": ["co_detected", "tamper"],
    "fire_protect_two_h_rb_ul": ["high_temperature", "tamper"],
    "fire_protect_two_hs_ac_ul": ["smoke_detected", "steam", "high_temperature", "tamper"],
    "fire_protect_two_hs_rb_ul": ["smoke_detected", "steam", "high_temperature", "tamper"],
    "fire_protect_two_hs_sb_ul": ["smoke_detected", "steam", "high_temperature", "tamper"],
    "fire_protect_two_hsc_ac_ul": [
        "smoke_detected",
        "steam",
        "co_detected",
        "high_temperature",
        "tamper",
    ],
    "fire_protect_two_hsc_rb_ul": [
        "smoke_detected",
        "steam",
        "co_detected",
        "high_temperature",
        "tamper",
    ],
    "fire_protect_two_hsc_sb_ul": [
        "smoke_detected",
        "steam",
        "co_detected",
        "high_temperature",
        "tamper",
    ],
    # `leak_protect` is the device_type `parse_device` emits (the ObjectType
    # oneof field name, object_type.proto field 22). The old `leaks_protect`
    # key never matched, so no LeakProtect ever got its leak sensor (#211).
    "leak_protect": ["leak_detected", "tamper"],
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
    "transmitter": ["tamper", "wire_input_alert"],
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
    "hub": ["gsm_connected", "lid_opened"],
    "hub_plus": ["gsm_connected", "lid_opened"],
    "hub_4g": ["gsm_connected", "lid_opened"],
    "hub_lite": ["gsm_connected", "lid_opened"],
    "hub_two": ["gsm_connected", "lid_opened"],
    "hub_two_plus": ["gsm_connected", "lid_opened"],
    "hub_two_4g": ["gsm_connected", "lid_opened"],
    "hub_two_lte_rtk": ["gsm_connected", "lid_opened"],
    "hub_three": ["gsm_connected", "lid_opened"],
    "hub_fibra": ["gsm_connected", "lid_opened"],
    "hub_hybrid_2": ["gsm_connected", "lid_opened"],
    "hub_hybrid_4g": ["gsm_connected", "lid_opened"],
    "hub_mega": ["gsm_connected", "lid_opened"],
    "hub_void_4g": ["gsm_connected", "lid_opened"],
    "hub_yavir": ["gsm_connected", "lid_opened"],
    "hub_yavir_plus": ["gsm_connected", "lid_opened"],
    "hub_fire": ["gsm_connected", "lid_opened"],
    "hub_superior": ["gsm_connected", "lid_opened"],
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AjaxCobrandedCoordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for device_id, device in coordinator.devices.items():
        sensor_keys = _DEVICE_TYPE_SENSORS.get(device.device_type, ["tamper"])
        # #231 data probe: CO presence on the FireProtect 2 line is decided by
        # the object_type variant, but the cloud reports several units under
        # generic/non-sensor-encoded types (`fire_protect_two`, `_base`,
        # `_plus`, `_plus_sb`, `_sb`) where we can only guess whether a CO cell
        # exists. Log the exact type + our CO decision so a diagnostic from a
        # CO-equipped detector tells us which type it actually reports — the one
        # piece of data needed to confirm the `_base`/`_plus`/`_sb` mappings
        # (and whether a CO model can ever land on the generic case).
        if device.device_type.startswith(("fire_protect_two", "fire_protect_2")):
            _LOGGER.debug(
                "FireProtect 2 setup: device_type=%s co_sensor=%s (sensors=%s)",
                device.device_type,
                "co_detected" in sensor_keys,
                sensor_keys,
            )
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
                entities.append(AjaxCraConnectionSensor(coordinator, space.id, space.hub_id))
                entities.append(AjaxHubEthernetSensor(coordinator, space.hub_id))
                entities.append(AjaxHubWifiSensor(coordinator, space.hub_id))
                entities.append(AjaxHubPowerSensor(coordinator, space.hub_id))

    # #231: a previous version attached a CO sensor to the generic FireProtect 2
    # mapping. HA never evicts an entity its platform stopped providing, so on
    # upgrade those phantom CO sensors would linger as `unavailable`. Mirror the
    # bypass-switch eviction (switch.py) and drop CO entities this run no longer
    # provides. Only runs when devices are loaded so a transient empty snapshot
    # can't wipe a legitimate CO sensor (it would be recreated next setup anyway).
    if coordinator.devices:
        _evict_orphan_co_sensors(
            hass,
            entry,
            provided={
                e.unique_id
                for e in entities
                if isinstance(e, AjaxBinarySensor)
                and e._status_key == "co_detected"
                and e.unique_id is not None
            },
        )
    async_add_entities(entities)

    # SpaceControl keyfobs are HTS-only and discovered seconds after setup (when
    # the first SETTINGS_BODY arrives), so they are not in `coordinator.devices`
    # here. Add any already known, then listen on the generic SIGNAL_NEW_DEVICE
    # dispatcher to add each new one as it is discovered at runtime.
    seen_keyfobs: set[str] = set()

    def _add_keyfobs(keyfob_ids: list[str]) -> None:
        new = [
            AjaxKeyfobActiveSensor(coordinator, kid)
            for kid in keyfob_ids
            if kid not in seen_keyfobs and kid in coordinator.keyfobs
        ]
        if new:
            seen_keyfobs.update(e._keyfob_id for e in new)
            async_add_entities(new)

    _add_keyfobs(list(coordinator.keyfobs))
    entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_NEW_DEVICE, lambda device_id: _add_keyfobs([device_id])
        )
    )


def _evict_orphan_co_sensors(
    hass: HomeAssistant, entry: ConfigEntry, *, provided: set[str]
) -> None:
    """Remove `co_detected` binary sensors this run no longer provides (#231).

    FireProtect 2 detectors without a CO cell used to get a phantom CO sensor
    from the generic device-type mapping. HA leaves an entity its platform
    stopped providing in the registry as `unavailable` until the user deletes
    it by hand, so evict the stale CO entities at the registry level — same
    approach as the orphan bypass-switch cleanup.
    """
    entity_reg = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(entity_reg, entry.entry_id):
        if (
            reg_entry.domain == "binary_sensor"
            and reg_entry.unique_id.endswith("_co_detected")
            and reg_entry.unique_id not in provided
        ):
            _LOGGER.info(
                "Removing orphaned CO sensor %s — the device has no CO cell (#231)",
                reg_entry.entity_id,
            )
            entity_reg.async_remove(reg_entry.entity_id)


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


class AjaxKeyfobActiveSensor(CoordinatorEntity[AjaxCobrandedCoordinator], BinarySensorEntity):
    """EXPERIMENTAL "Active" state for an Ajax SpaceControl keyfob.

    Keyfobs are HTS-only (not in the gRPC device snapshot). They carry no
    per-device data (no battery/online), so rather than one HA device each they
    are grouped under a single virtual "Keyfobs" device (one per hub), with one
    entity per keyfob named after it. The `active` value is derived from an
    UNVERIFIED flag byte (`0x0b`) — every observed keyfob reads "active" and we
    have no deactivated sample, so the value is surfaced experimentally.
    `flags_hex` is exposed as an attribute so a user with a CRA-deactivated
    keyfob can confirm which byte actually flips.
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AjaxCobrandedCoordinator, keyfob_id: str) -> None:
        super().__init__(coordinator)
        self._keyfob_id = keyfob_id
        self._attr_unique_id = f"aegis_ajax_{keyfob_id}_active"
        keyfob = coordinator.keyfobs.get(keyfob_id)
        if keyfob:
            # The entity's own name is the keyfob name; with has_entity_name the
            # device page lists it (e.g. "Front fob") under the "Keyfobs" device.
            self._attr_name = keyfob.name
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{keyfob.hub_id}_keyfobs")},
                name="Keyfobs",
                manufacturer=MANUFACTURER,
                model="SpaceControl keyfobs (experimental)",
                via_device=(DOMAIN, keyfob.hub_id),
            )

    @property
    def available(self) -> bool:
        return self._keyfob_id in self.coordinator.keyfobs

    @property
    def is_on(self) -> bool:
        keyfob = self.coordinator.keyfobs.get(self._keyfob_id)
        return keyfob is not None and keyfob.active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        keyfob = self.coordinator.keyfobs.get(self._keyfob_id)
        if keyfob is None:
            return {}
        return {
            "index": keyfob.index,
            "flags_hex": keyfob.flags_hex,
            "experimental": True,
        }


class AjaxCraConnectionSensor(CoordinatorEntity[AjaxCobrandedCoordinator], BinarySensorEntity):
    """Live "Central receptora de alarmas → Conectada" status for a hub.

    The Ajax mobile app renders that same row from `monitoring.cms_active`
    on the hub's status snapshot — a real-time per-hub boolean of whether
    the CMS channel is currently up. We keep `space.has_monitoring` as a
    fallback for installs whose hub firmware doesn't emit `monitoring`
    in its statuses (some older / cobranded firmwares), so the entity
    still reflects "a monitoring company is on the account" in that path.

    The diagnostic `sensor.<hub>_compania_cra` exposes the actual company
    names + statuses for accounts that need to inspect approvals.
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "monitoring"

    def __init__(self, coordinator: AjaxCobrandedCoordinator, space_id: str, hub_id: str) -> None:
        super().__init__(coordinator)
        self._space_id = space_id
        self._hub_id = hub_id
        self._attr_unique_id = f"aegis_ajax_{hub_id}_monitoring_active"
        hub_device = coordinator.devices.get(hub_id)
        if hub_device:
            self._attr_device_info = build_device_info(hub_device, coordinator.rooms)

    @property
    def available(self) -> bool:
        hub = self.coordinator.devices.get(self._hub_id)
        # Primary signal is on the hub — available once we have a fresh
        # snapshot that includes its `monitoring` status oneof. Fall back
        # to the space-level snapshot for firmwares that don't emit it,
        # avoiding a misleading `off` before either source has loaded.
        if hub is not None and "monitoring_active" in hub.statuses:
            return True
        space = self.coordinator.spaces.get(self._space_id)
        return space is not None and space.monitoring_companies_loaded

    @property
    def is_on(self) -> bool:
        hub = self.coordinator.devices.get(self._hub_id)
        if hub is not None and "monitoring_active" in hub.statuses:
            return bool(hub.statuses["monitoring_active"])
        space = self.coordinator.spaces.get(self._space_id)
        return space.has_monitoring if space is not None else False


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


@dataclass(frozen=True)
class _HubBinSpec:
    """Describes one hub-network binary sensor.

    `translation_key` doubles as the unique_id suffix (per
    `aegis_ajax_{hub_id}_{translation_key}`). `require_hts_alive`
    means the entity reports `unavailable` when HTS is disconnected
    instead of returning the cached last-known state — see #146 for
    why mains_power needs that and the connectivity sensors don't.
    """

    translation_key: str
    state_attr: str
    device_class: BinarySensorDeviceClass
    require_hts_alive: bool = False


_HUB_BIN_SPECS_BY_KEY: dict[str, _HubBinSpec] = {
    "ethernet": _HubBinSpec(
        translation_key="ethernet",
        state_attr="ethernet_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
    ),
    "wifi": _HubBinSpec(
        translation_key="wifi",
        state_attr="wifi_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
    ),
    "mains_power": _HubBinSpec(
        translation_key="mains_power",
        state_attr="externally_powered",
        device_class=BinarySensorDeviceClass.PLUG,
        require_hts_alive=True,
    ),
}


class AjaxHubNetworkBinarySensor(_HubNetworkBinarySensor):
    """Generic descriptor-driven hub-network binary sensor."""

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str, spec_key: str) -> None:
        super().__init__(coordinator, hub_id)
        spec = _HUB_BIN_SPECS_BY_KEY[spec_key]
        self._spec = spec
        self._attr_device_class = spec.device_class
        self._attr_translation_key = spec.translation_key
        self._attr_unique_id = f"aegis_ajax_{hub_id}_{spec.translation_key}"

    @property
    def available(self) -> bool:
        base = super().available
        if self._spec.require_hts_alive:
            # #146: don't keep reporting cached "on" through an HTS outage
            # for sensors whose stale value could silence an automation.
            return base and self.coordinator.is_hts_alive
        return base

    @property
    def is_on(self) -> bool:
        state = self.coordinator.hub_network.get(self._hub_id)
        if state is None:
            return False
        return bool(getattr(state, self._spec.state_attr, False))


# Backwards-compatible aliases — async_setup_entry and tests instantiate
# these by name and stored entity unique_ids depend on the constructor
# wiring up the right spec.
class AjaxHubEthernetSensor(AjaxHubNetworkBinarySensor):
    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id, "ethernet")


class AjaxHubWifiSensor(AjaxHubNetworkBinarySensor):
    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id, "wifi")


class AjaxHubPowerSensor(AjaxHubNetworkBinarySensor):
    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator, hub_id, "mains_power")
