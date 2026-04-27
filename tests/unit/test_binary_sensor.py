"""Tests for binary sensor entities."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.aegis_ajax.api.hts.hub_state import HubNetworkState
from custom_components.aegis_ajax.api.models import Device
from custom_components.aegis_ajax.binary_sensor import (
    _DEVICE_TYPE_SENSORS,
    BINARY_SENSOR_TYPES,
    AjaxBinarySensor,
    AjaxConnectivitySensor,
    AjaxHubWifiSensor,
    AjaxProblemSensor,
)
from custom_components.aegis_ajax.const import DeviceState


class TestBinarySensorTypes:
    def test_door_sensor_type_exists(self) -> None:
        assert "door_opened" in BINARY_SENSOR_TYPES

    def test_motion_sensor_type_exists(self) -> None:
        assert "motion_detected" in BINARY_SENSOR_TYPES

    def test_smoke_sensor_type_exists(self) -> None:
        assert "smoke_detected" in BINARY_SENSOR_TYPES

    def test_leak_sensor_type_exists(self) -> None:
        assert "leak_detected" in BINARY_SENSOR_TYPES

    def test_tamper_sensor_type_exists(self) -> None:
        assert "tamper" in BINARY_SENSOR_TYPES

    def test_co_sensor_type_exists(self) -> None:
        assert "co_detected" in BINARY_SENSOR_TYPES

    def test_high_temperature_type_exists(self) -> None:
        assert "high_temperature" in BINARY_SENSOR_TYPES

    def test_monitoring_active_type_exists(self) -> None:
        assert "monitoring_active" in BINARY_SENSOR_TYPES

    def test_gsm_connected_type_exists(self) -> None:
        assert "gsm_connected" in BINARY_SENSOR_TYPES

    def test_lid_opened_type_exists(self) -> None:
        assert "lid_opened" in BINARY_SENSOR_TYPES

    def test_external_contact_broken_type_exists(self) -> None:
        assert "external_contact_broken" in BINARY_SENSOR_TYPES

    def test_case_drilling_type_exists(self) -> None:
        assert "case_drilling" in BINARY_SENSOR_TYPES

    def test_anti_masking_type_exists(self) -> None:
        assert "anti_masking" in BINARY_SENSOR_TYPES

    def test_malfunction_type_exists(self) -> None:
        assert "malfunction" in BINARY_SENSOR_TYPES

    def test_interference_type_exists(self) -> None:
        assert "interference" in BINARY_SENSOR_TYPES

    def test_relay_stuck_type_exists(self) -> None:
        assert "relay_stuck" in BINARY_SENSOR_TYPES

    def test_always_active_type_exists(self) -> None:
        assert "always_active" in BINARY_SENSOR_TYPES

    def test_glass_break_sensor_type_exists(self) -> None:
        assert "glass_break" in BINARY_SENSOR_TYPES

    def test_vibration_sensor_type_exists(self) -> None:
        assert "vibration" in BINARY_SENSOR_TYPES

    def test_wire_input_alert_type_exists(self) -> None:
        assert "wire_input_alert" in BINARY_SENSOR_TYPES


class TestAjaxBinarySensor:
    def _make_device(self, statuses: dict) -> Device:
        return Device(
            id="dev-1",
            hub_id="hub-1",
            name="Front Door",
            device_type="door_protect",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses=statuses,
            battery=None,
        )

    def test_is_on_true(self) -> None:
        device = self._make_device({"door_opened": True})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="dev-1", status_key="door_opened"
        )
        assert sensor.is_on is True

    def test_is_on_false_when_key_absent(self) -> None:
        device = self._make_device({})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="dev-1", status_key="door_opened"
        )
        assert sensor.is_on is False

    def test_is_on_false_when_no_device(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="dev-1", status_key="door_opened"
        )
        assert sensor.is_on is False

    def test_unique_id(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": self._make_device({})}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="dev-1", status_key="door_opened"
        )
        assert sensor.unique_id == "aegis_ajax_dev-1_door_opened"

    def test_device_info_with_device(self) -> None:
        device = self._make_device({})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="dev-1", status_key="door_opened"
        )
        assert sensor._attr_device_info is not None
        assert ("aegis_ajax", "dev-1") in sensor._attr_device_info["identifiers"]

    def test_device_info_without_device(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="dev-1", status_key="door_opened"
        )
        assert not hasattr(sensor, "_attr_device_info") or sensor._attr_device_info is None

    def test_available_when_online(self) -> None:
        device = self._make_device({})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="dev-1", status_key="door_opened"
        )
        assert sensor.available is True

    def test_unavailable_when_device_missing(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="dev-1", status_key="door_opened"
        )
        assert sensor.available is False

    def test_tamper_has_translation_key(self) -> None:
        device = self._make_device({"tamper": True})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxBinarySensor(coordinator=coordinator, device_id="dev-1", status_key="tamper")
        assert sensor._attr_translation_key == "tamper"

    def test_motion_sensor(self) -> None:
        device = self._make_device({"motion_detected": True})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="dev-1", status_key="motion_detected"
        )
        assert sensor.is_on is True

    def test_tamper_sensor(self) -> None:
        device = self._make_device({"tamper": True})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxBinarySensor(coordinator=coordinator, device_id="dev-1", status_key="tamper")
        assert sensor.is_on is True

    def test_hub_device_has_no_via_device(self) -> None:
        from custom_components.aegis_ajax.api.models import Device
        from custom_components.aegis_ajax.const import DeviceState

        hub_device = Device(
            id="hub-1",
            hub_id="hub-1",
            name="Hub",
            device_type="hub_two_4g",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={"monitoring_active": True},
            battery=None,
        )
        coordinator = MagicMock()
        coordinator.devices = {"hub-1": hub_device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="hub-1", status_key="monitoring_active"
        )
        assert sensor._attr_device_info is not None
        assert "via_device" not in sensor._attr_device_info

    def test_non_hub_device_has_via_device(self) -> None:
        device = self._make_device({})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="dev-1", status_key="door_opened"
        )
        assert sensor._attr_device_info is not None
        assert sensor._attr_device_info.get("via_device") == ("aegis_ajax", "hub-1")

    def test_monitoring_sensor_has_translation_key(self) -> None:
        from custom_components.aegis_ajax.api.models import Device
        from custom_components.aegis_ajax.const import DeviceState

        hub_device = Device(
            id="hub-1",
            hub_id="hub-1",
            name="Hub",
            device_type="hub_two_4g",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )
        coordinator = MagicMock()
        coordinator.devices = {"hub-1": hub_device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="hub-1", status_key="monitoring_active"
        )
        assert sensor._attr_translation_key == "monitoring"

    def test_motion_sensor_extra_attributes_with_timestamp(self) -> None:
        device = self._make_device({"motion_detected": True, "motion_detected_at": 1700000000})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="dev-1", status_key="motion_detected"
        )
        attrs = sensor.extra_state_attributes
        assert attrs.get("detected_at") == 1700000000

    def test_motion_sensor_extra_attributes_without_timestamp(self) -> None:
        device = self._make_device({"motion_detected": True})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="dev-1", status_key="motion_detected"
        )
        attrs = sensor.extra_state_attributes
        assert attrs == {}

    def test_non_motion_sensor_no_extra_attributes(self) -> None:
        device = self._make_device({"tamper": True})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxBinarySensor(coordinator=coordinator, device_id="dev-1", status_key="tamper")
        assert sensor.extra_state_attributes == {}


class TestDeviceTypeSensors:
    def test_glass_protect_s_in_device_types(self) -> None:
        assert "glass_protect_s" in _DEVICE_TYPE_SENSORS

    def test_glass_protect_fibra_in_device_types(self) -> None:
        assert "glass_protect_fibra" in _DEVICE_TYPE_SENSORS

    def test_combi_protect_s_in_device_types(self) -> None:
        assert "combi_protect_s" in _DEVICE_TYPE_SENSORS

    def test_combi_protect_fibra_in_device_types(self) -> None:
        assert "combi_protect_fibra" in _DEVICE_TYPE_SENSORS

    def test_home_siren_in_device_types(self) -> None:
        assert "home_siren" in _DEVICE_TYPE_SENSORS

    def test_street_siren_in_device_types(self) -> None:
        assert "street_siren" in _DEVICE_TYPE_SENSORS

    def test_rex_in_device_types(self) -> None:
        assert "rex" in _DEVICE_TYPE_SENSORS

    def test_rex_2_in_device_types(self) -> None:
        assert "rex_2" in _DEVICE_TYPE_SENSORS

    def test_fire_protect_plus_has_co(self) -> None:
        assert "co_detected" in _DEVICE_TYPE_SENSORS["fire_protect_plus"]

    def test_leaks_protect_has_leak(self) -> None:
        assert "leak_detected" in _DEVICE_TYPE_SENSORS["leaks_protect"]

    def test_door_protect_s_in_device_types(self) -> None:
        assert "door_protect_s" in _DEVICE_TYPE_SENSORS

    def test_door_protect_g3_in_device_types(self) -> None:
        assert "door_protect_g3" in _DEVICE_TYPE_SENSORS

    def test_motion_cam_fibra_in_device_types(self) -> None:
        assert "motion_cam_fibra" in _DEVICE_TYPE_SENSORS

    def test_glass_protect_has_glass_break(self) -> None:
        assert "glass_break" in _DEVICE_TYPE_SENSORS["glass_protect"]

    def test_glass_protect_s_has_glass_break(self) -> None:
        assert "glass_break" in _DEVICE_TYPE_SENSORS["glass_protect_s"]

    def test_glass_protect_fibra_has_glass_break(self) -> None:
        assert "glass_break" in _DEVICE_TYPE_SENSORS["glass_protect_fibra"]

    def test_combi_protect_has_glass_break(self) -> None:
        assert "glass_break" in _DEVICE_TYPE_SENSORS["combi_protect"]

    def test_combi_protect_s_has_glass_break(self) -> None:
        assert "glass_break" in _DEVICE_TYPE_SENSORS["combi_protect_s"]

    def test_combi_protect_fibra_has_glass_break(self) -> None:
        assert "glass_break" in _DEVICE_TYPE_SENSORS["combi_protect_fibra"]

    def test_door_protect_plus_has_vibration(self) -> None:
        assert "vibration" in _DEVICE_TYPE_SENSORS["door_protect_plus"]

    def test_door_protect_plus_fibra_has_vibration(self) -> None:
        assert "vibration" in _DEVICE_TYPE_SENSORS["door_protect_plus_fibra"]

    def test_door_protect_s_plus_has_vibration(self) -> None:
        assert "vibration" in _DEVICE_TYPE_SENSORS["door_protect_s_plus"]

    @pytest.mark.parametrize(
        "device_type",
        [
            "door_protect",
            "door_protect_plus",
            "door_protect_fibra",
            "door_protect_s",
            "door_protect_s_plus",
            "door_protect_plus_fibra",
            "door_protect_g3",
            "door_protect_plus_g3_fibra",
        ],
    )
    def test_door_protect_family_has_external_contact_alert(self, device_type: str) -> None:
        assert "external_contact_alert" in _DEVICE_TYPE_SENSORS[device_type]

    # FireProtect 2 family — Ajax's catalog uses both `_2` (legacy) and
    # `_two*` (current) for the same generation. Every variant must surface
    # at least the tamper sensor; the multi-sensor models also expose smoke,
    # CO and heat. (Bug #51 — the cloud sends `fire_protect_two`, the older
    # mapping only knew `fire_protect_2`.)
    @pytest.mark.parametrize(
        "device_type",
        [
            "fire_protect_2",
            "fire_protect_two",
            "fire_protect_two_base",
            "fire_protect_two_plus",
            "fire_protect_two_plus_sb",
            "fire_protect_two_sb",
            "fire_protect_two_hcrb",
            "fire_protect_two_hcsb",
            "fire_protect_two_hrb",
            "fire_protect_two_hsb",
            "fire_protect_two_crb",
            "fire_protect_two_csb",
            "fire_protect_two_h_ac",
            "fire_protect_two_c_ac",
            "fire_protect_two_hc_ac",
            "fire_protect_two_hs_ac",
            "fire_protect_two_hsc_ac",
            "fire_protect_two_c_rb_ul",
            "fire_protect_two_h_rb_ul",
            "fire_protect_two_hs_ac_ul",
            "fire_protect_two_hs_rb_ul",
            "fire_protect_two_hs_sb_ul",
            "fire_protect_two_hsc_ac_ul",
            "fire_protect_two_hsc_rb_ul",
            "fire_protect_two_hsc_sb_ul",
        ],
    )
    def test_fire_protect_two_family_has_tamper(self, device_type: str) -> None:
        assert device_type in _DEVICE_TYPE_SENSORS
        assert "tamper" in _DEVICE_TYPE_SENSORS[device_type]

    @pytest.mark.parametrize(
        "device_type",
        [
            "fire_protect_2",
            "fire_protect_two",
            "fire_protect_two_base",
            "fire_protect_two_plus",
            "fire_protect_two_plus_sb",
            "fire_protect_two_sb",
            "fire_protect_two_hs_ac",
            "fire_protect_two_hsc_ac",
            "fire_protect_two_hcrb",
            "fire_protect_two_hcsb",
        ],
    )
    def test_fire_protect_two_smoke_variants_have_smoke(self, device_type: str) -> None:
        assert "smoke_detected" in _DEVICE_TYPE_SENSORS[device_type]

    # Hub family — `hub`, `hub_plus`, `hub_two_4g` were already mapped, but
    # the v3 catalog also names `hub_two`, `hub_two_plus`, `hub_hybrid_*`,
    # `hub_mega`, `hub_lite`, `hub_4g`, `hub_three`, etc. Anyone running a
    # Hub 2 / Hub 2 Plus was missing the monitoring/gsm/lid entities
    # because of the same legacy-vs-current naming mismatch.
    @pytest.mark.parametrize(
        "device_type",
        [
            "hub",
            "hub_plus",
            "hub_4g",
            "hub_lite",
            "hub_two",
            "hub_two_plus",
            "hub_two_4g",
            "hub_two_lte_rtk",
            "hub_three",
            "hub_fibra",
            "hub_hybrid_2",
            "hub_hybrid_4g",
            "hub_mega",
            "hub_void_4g",
            "hub_yavir",
            "hub_yavir_plus",
            "hub_fire",
            "hub_superior",
        ],
    )
    def test_hub_family_has_monitoring_sensors(self, device_type: str) -> None:
        sensors = _DEVICE_TYPE_SENSORS[device_type]
        assert "monitoring_active" in sensors
        assert "gsm_connected" in sensors
        assert "lid_opened" in sensors

    # Range Extender naming — `rex` / `rex_2` were the legacy keys; current
    # cloud naming is `range_extender` / `range_extender_2`. Both must be
    # accepted as known device types so we don't fall back to tamper-only.
    @pytest.mark.parametrize(
        "device_type",
        [
            "rex",
            "rex_2",
            "range_extender",
            "range_extender_2",
            "range_extender_2_fire",
        ],
    )
    def test_range_extender_aliases_known(self, device_type: str) -> None:
        assert device_type in _DEVICE_TYPE_SENSORS

    def test_wire_input_mt_in_device_types(self) -> None:
        assert "wire_input_mt" in _DEVICE_TYPE_SENSORS

    def test_wire_input_in_device_types(self) -> None:
        assert "wire_input" in _DEVICE_TYPE_SENSORS

    def test_wire_input_mt_has_alert_sensor(self) -> None:
        assert "wire_input_alert" in _DEVICE_TYPE_SENSORS["wire_input_mt"]

    def test_wire_input_has_alert_sensor(self) -> None:
        assert "wire_input_alert" in _DEVICE_TYPE_SENSORS["wire_input"]

    def test_wire_input_mt_keeps_tamper(self) -> None:
        # Backwards compatibility: wire_input_mt used to fall back to the
        # default ["tamper"] bucket. Keep the tamper entity so existing users
        # don't see orphaned "unavailable" entries after upgrade.
        assert "tamper" in _DEVICE_TYPE_SENSORS["wire_input_mt"]

    def test_wire_input_keeps_tamper(self) -> None:
        assert "tamper" in _DEVICE_TYPE_SENSORS["wire_input"]

    def test_transmitter_has_wire_input_alert_sensor(self) -> None:
        # Issue #65: Transmitter Jeweller exposes only tamper, not the
        # intrusion line carried by the wired sensor it bridges.
        assert "wire_input_alert" in _DEVICE_TYPE_SENSORS["transmitter"]

    def test_transmitter_keeps_tamper(self) -> None:
        assert "tamper" in _DEVICE_TYPE_SENSORS["transmitter"]


class TestWireInputAlertSensor:
    """Binary sensor behaviour for wired-input alerts (MultiTransmitter children)."""

    def _make_device(self, statuses: dict) -> Device:
        return Device(
            id="wi-1",
            hub_id="hub-1",
            name="Kitchen window",
            device_type="wire_input_mt",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses=statuses,
            battery=None,
        )

    def test_is_on_true(self) -> None:
        device = self._make_device({"wire_input_alert": True})
        coordinator = MagicMock()
        coordinator.devices = {"wi-1": device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="wi-1", status_key="wire_input_alert"
        )
        assert sensor.is_on is True

    def test_is_on_false(self) -> None:
        device = self._make_device({"wire_input_alert": False})
        coordinator = MagicMock()
        coordinator.devices = {"wi-1": device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="wi-1", status_key="wire_input_alert"
        )
        assert sensor.is_on is False

    def test_alarm_type_attribute(self) -> None:
        device = self._make_device({"wire_input_alert": True, "wire_input_alarm_type": "intrusion"})
        coordinator = MagicMock()
        coordinator.devices = {"wi-1": device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="wi-1", status_key="wire_input_alert"
        )
        assert sensor.extra_state_attributes == {"alarm_type": "intrusion"}

    def test_alarm_type_absent_no_attributes(self) -> None:
        device = self._make_device({"wire_input_alert": True})
        coordinator = MagicMock()
        coordinator.devices = {"wi-1": device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="wi-1", status_key="wire_input_alert"
        )
        assert sensor.extra_state_attributes == {}

    def test_is_on_via_external_contact_broken(self) -> None:
        # Some hub firmwares emit state changes through external_contact_broken
        # rather than wire_input_status. The wire_input_alert entity on a
        # wire_input_mt device must reflect it.
        device = self._make_device({"external_contact_broken": True})
        coordinator = MagicMock()
        coordinator.devices = {"wi-1": device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="wi-1", status_key="wire_input_alert"
        )
        assert sensor.is_on is True

    def test_is_on_via_external_contact_alert(self) -> None:
        device = self._make_device({"external_contact_alert": True})
        coordinator = MagicMock()
        coordinator.devices = {"wi-1": device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="wi-1", status_key="wire_input_alert"
        )
        assert sensor.is_on is True

    def test_is_on_false_when_all_sources_clear(self) -> None:
        device = self._make_device({})
        coordinator = MagicMock()
        coordinator.devices = {"wi-1": device}
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="wi-1", status_key="wire_input_alert"
        )
        assert sensor.is_on is False

    def test_transmitter_wire_input_alert_or_reduces_external_contact(self) -> None:
        # Issue #65: the Transmitter Jeweller may surface the intrusion line
        # via any of wire_input_status / external_contact_broken /
        # external_contact_alert depending on hub firmware. The unified
        # entity must reflect any of them.
        for source in ("wire_input_alert", "external_contact_broken", "external_contact_alert"):
            device = Device(
                id="tr-1",
                hub_id="hub-1",
                name="Transmitter",
                device_type="transmitter",
                room_id=None,
                group_id=None,
                state=DeviceState.ONLINE,
                malfunctions=0,
                bypassed=False,
                statuses={source: True},
                battery=None,
            )
            coordinator = MagicMock()
            coordinator.devices = {"tr-1": device}
            sensor = AjaxBinarySensor(
                coordinator=coordinator, device_id="tr-1", status_key="wire_input_alert"
            )
            assert sensor.is_on is True, f"OR-reduce missed {source} on transmitter"

    def test_door_protect_external_contact_broken_not_routed_as_alert(self) -> None:
        # Sanity check: the composite OR must apply ONLY to wire_input devices,
        # not to DoorProtect (where external_contact_broken is a distinct fault
        # indicator and is exposed as its own entity with PROBLEM class).
        device = Device(
            id="dp-1",
            hub_id="hub-1",
            name="Front door",
            device_type="door_protect",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={"external_contact_broken": True},
            battery=None,
        )
        coordinator = MagicMock()
        coordinator.devices = {"dp-1": device}
        # A wire_input_alert entity should not exist on door_protect, but if it
        # somehow did, external_contact_broken must not be OR'd into it.
        sensor = AjaxBinarySensor(
            coordinator=coordinator, device_id="dp-1", status_key="wire_input_alert"
        )
        assert sensor.is_on is False


class TestAjaxConnectivitySensor:
    def _make_device(self, state: DeviceState = DeviceState.ONLINE) -> Device:
        return Device(
            id="dev-1",
            hub_id="hub-1",
            name="Front Door",
            device_type="door_protect",
            room_id=None,
            group_id=None,
            state=state,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )

    def test_is_on_when_device_online(self) -> None:
        device = self._make_device(DeviceState.ONLINE)
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxConnectivitySensor(coordinator=coordinator, device_id="dev-1")
        assert sensor.is_on is True

    def test_is_off_when_device_offline(self) -> None:
        device = self._make_device(DeviceState.OFFLINE)
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxConnectivitySensor(coordinator=coordinator, device_id="dev-1")
        assert sensor.is_on is False

    def test_is_off_when_device_missing(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxConnectivitySensor(coordinator=coordinator, device_id="dev-1")
        assert sensor.is_on is False

    def test_unique_id(self) -> None:
        device = self._make_device()
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxConnectivitySensor(coordinator=coordinator, device_id="dev-1")
        assert sensor.unique_id == "aegis_ajax_dev-1_connectivity"

    def test_entity_category_is_diagnostic(self) -> None:
        from homeassistant.const import EntityCategory

        device = self._make_device()
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxConnectivitySensor(coordinator=coordinator, device_id="dev-1")
        assert sensor._attr_entity_category == EntityCategory.DIAGNOSTIC

    def test_translation_key(self) -> None:
        device = self._make_device()
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxConnectivitySensor(coordinator=coordinator, device_id="dev-1")
        assert sensor._attr_translation_key == "connectivity"

    def test_device_info_set(self) -> None:
        device = self._make_device()
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxConnectivitySensor(coordinator=coordinator, device_id="dev-1")
        assert sensor._attr_device_info is not None
        assert ("aegis_ajax", "dev-1") in sensor._attr_device_info["identifiers"]

    def test_hub_device_no_via_device(self) -> None:
        hub_device = Device(
            id="hub-1",
            hub_id="hub-1",
            name="Hub",
            device_type="hub_two_4g",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )
        coordinator = MagicMock()
        coordinator.devices = {"hub-1": hub_device}
        sensor = AjaxConnectivitySensor(coordinator=coordinator, device_id="hub-1")
        assert "via_device" not in sensor._attr_device_info


class TestAjaxProblemSensor:
    def _make_device(self, malfunctions: int = 0) -> Device:
        return Device(
            id="dev-1",
            hub_id="hub-1",
            name="Front Door",
            device_type="door_protect",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=malfunctions,
            bypassed=False,
            statuses={},
            battery=None,
        )

    def test_is_off_when_no_malfunctions(self) -> None:
        device = self._make_device(malfunctions=0)
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxProblemSensor(coordinator=coordinator, device_id="dev-1")
        assert sensor.is_on is False

    def test_is_on_when_malfunctions(self) -> None:
        device = self._make_device(malfunctions=2)
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxProblemSensor(coordinator=coordinator, device_id="dev-1")
        assert sensor.is_on is True

    def test_is_off_when_device_missing(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxProblemSensor(coordinator=coordinator, device_id="dev-1")
        assert sensor.is_on is False

    def test_unique_id(self) -> None:
        device = self._make_device()
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxProblemSensor(coordinator=coordinator, device_id="dev-1")
        assert sensor.unique_id == "aegis_ajax_dev-1_problem"

    def test_entity_category_is_diagnostic(self) -> None:
        from homeassistant.const import EntityCategory

        device = self._make_device()
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxProblemSensor(coordinator=coordinator, device_id="dev-1")
        assert sensor._attr_entity_category == EntityCategory.DIAGNOSTIC

    def test_translation_key(self) -> None:
        device = self._make_device()
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxProblemSensor(coordinator=coordinator, device_id="dev-1")
        assert sensor._attr_translation_key == "problem"

    def test_extra_attributes_with_malfunctions(self) -> None:
        device = self._make_device(malfunctions=3)
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxProblemSensor(coordinator=coordinator, device_id="dev-1")
        attrs = sensor.extra_state_attributes
        assert attrs == {"malfunctions_count": 3}

    def test_extra_attributes_empty_when_device_missing(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxProblemSensor(coordinator=coordinator, device_id="dev-1")
        assert sensor.extra_state_attributes == {}

    def test_device_info_set(self) -> None:
        device = self._make_device()
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxProblemSensor(coordinator=coordinator, device_id="dev-1")
        assert sensor._attr_device_info is not None
        assert ("aegis_ajax", "dev-1") in sensor._attr_device_info["identifiers"]


class TestAjaxHubWifiSensor:
    def _make_coordinator(self, wifi_connected: bool = True) -> MagicMock:
        hub_device = Device(
            id="hub-1",
            hub_id="hub-1",
            name="Hub Two Plus",
            device_type="hub_two_plus",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )
        coordinator = MagicMock()
        coordinator.devices = {"hub-1": hub_device}
        coordinator.hub_network = {"hub-1": HubNetworkState(wifi_connected=wifi_connected)}
        return coordinator

    def test_is_on_when_wifi_connected(self) -> None:
        sensor = AjaxHubWifiSensor(self._make_coordinator(True), "hub-1")
        assert sensor.is_on is True

    def test_is_off_when_wifi_not_connected(self) -> None:
        sensor = AjaxHubWifiSensor(self._make_coordinator(False), "hub-1")
        assert sensor.is_on is False

    def test_available_when_hub_network_exists(self) -> None:
        sensor = AjaxHubWifiSensor(self._make_coordinator(True), "hub-1")
        assert sensor.available is True

    def test_translation_key(self) -> None:
        sensor = AjaxHubWifiSensor(self._make_coordinator(True), "hub-1")
        assert sensor._attr_translation_key == "wifi"
