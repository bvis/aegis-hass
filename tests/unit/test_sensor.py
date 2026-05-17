"""Tests for sensor entities."""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from custom_components.aegis_ajax.api.hts.hub_state import HubNetworkState
from custom_components.aegis_ajax.api.hub_object import SimCardInfo
from custom_components.aegis_ajax.api.models import (
    BatteryInfo,
    Device,
    MonitoringCompany,
    MonitoringCompanyStatus,
    Space,
)
from custom_components.aegis_ajax.const import ConnectionStatus, DeviceState, SecurityState
from custom_components.aegis_ajax.sensor import (
    SENSOR_TYPES,
    AjaxHubCellularNetworkSensor,
    AjaxHubConnectionTypeSensor,
    AjaxHubEthernetDnsSensor,
    AjaxHubEthernetGatewaySensor,
    AjaxHubEthernetIpSensor,
    AjaxHubMonitoringCompanySensor,
    AjaxHubWifiIpSensor,
    AjaxHubWifiSignalSensor,
    AjaxHubWifiSsidSensor,
    AjaxSensor,
    AjaxSimImeiSensor,
)


class TestSensorTypes:
    def test_battery_type_exists(self) -> None:
        assert "battery_level" in SENSOR_TYPES

    def test_temperature_type_exists(self) -> None:
        assert "temperature" in SENSOR_TYPES

    def test_humidity_type_exists(self) -> None:
        assert "humidity" in SENSOR_TYPES

    def test_co2_type_exists(self) -> None:
        assert "co2" in SENSOR_TYPES

    def test_signal_strength_type_exists(self) -> None:
        assert "signal_strength" in SENSOR_TYPES

    def test_mobile_network_type_exists(self) -> None:
        assert "mobile_network_type" in SENSOR_TYPES

    def test_wifi_signal_level_exists(self) -> None:
        assert "wifi_signal_level" in SENSOR_TYPES


class TestAjaxSensor:
    def _make_device(self, statuses: dict, battery: BatteryInfo | None = None) -> Device:
        return Device(
            id="dev-1",
            hub_id="hub-1",
            name="Sensor Device",
            device_type="life_quality",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses=statuses,
            battery=battery,
        )

    def test_battery_level(self) -> None:
        device = self._make_device({}, battery=BatteryInfo(level=85, is_low=False))
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxSensor(coordinator=coordinator, device_id="dev-1", sensor_key="battery_level")
        assert sensor.native_value == 85

    def test_temperature(self) -> None:
        device = self._make_device({"temperature": 22.5})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxSensor(coordinator=coordinator, device_id="dev-1", sensor_key="temperature")
        assert sensor.native_value == 22.5

    def test_humidity(self) -> None:
        device = self._make_device({"humidity": 60})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxSensor(coordinator=coordinator, device_id="dev-1", sensor_key="humidity")
        assert sensor.native_value == 60

    def test_co2(self) -> None:
        device = self._make_device({"co2": 800})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxSensor(coordinator=coordinator, device_id="dev-1", sensor_key="co2")
        assert sensor.native_value == 800

    def test_signal_strength(self) -> None:
        device = self._make_device({"signal_strength": "Normal"})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxSensor(
            coordinator=coordinator, device_id="dev-1", sensor_key="signal_strength"
        )
        assert sensor.native_value == "Normal"

    def test_native_value_returns_none_when_no_device(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxSensor(coordinator=coordinator, device_id="dev-1", sensor_key="temperature")
        assert sensor.native_value is None

    def test_native_value_returns_none_when_key_missing(self) -> None:
        device = self._make_device({})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxSensor(coordinator=coordinator, device_id="dev-1", sensor_key="temperature")
        assert sensor.native_value is None

    def test_unique_id(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxSensor(coordinator=coordinator, device_id="dev-1", sensor_key="battery_level")
        assert sensor.unique_id == "aegis_ajax_dev-1_battery_level"

    def test_device_info_with_device(self) -> None:
        device = self._make_device({})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        coordinator.rooms = {}
        sensor = AjaxSensor(coordinator=coordinator, device_id="dev-1", sensor_key="battery_level")
        assert sensor._attr_device_info is not None
        assert ("aegis_ajax", "dev-1") in sensor._attr_device_info["identifiers"]
        # Issue #55: expose Ajax device id as HA serial number
        assert sensor._attr_device_info.get("serial_number") == "dev-1"

    def test_device_info_includes_suggested_area_from_room(self) -> None:
        from custom_components.aegis_ajax.api.models import Room

        device = Device(
            id="dev-r",
            hub_id="hub-1",
            name="Hallway Sensor",
            device_type="motion_protect",
            room_id="room-9",
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )
        coordinator = MagicMock()
        coordinator.devices = {"dev-r": device}
        coordinator.rooms = {"room-9": Room(id="room-9", name="Hallway", space_id="s1")}
        sensor = AjaxSensor(coordinator=coordinator, device_id="dev-r", sensor_key="battery_level")
        assert sensor._attr_device_info is not None
        assert sensor._attr_device_info.get("suggested_area") == "Hallway"

    def test_device_info_without_device(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxSensor(coordinator=coordinator, device_id="dev-1", sensor_key="battery_level")
        assert not hasattr(sensor, "_attr_device_info") or sensor._attr_device_info is None

    def test_battery_sensor_is_diagnostic(self) -> None:
        from homeassistant.const import EntityCategory

        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxSensor(coordinator=coordinator, device_id="dev-1", sensor_key="battery_level")
        assert sensor._attr_entity_category == EntityCategory.DIAGNOSTIC

    def test_signal_strength_sensor_is_diagnostic(self) -> None:
        from homeassistant.const import EntityCategory

        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxSensor(
            coordinator=coordinator, device_id="dev-1", sensor_key="signal_strength"
        )
        assert sensor._attr_entity_category == EntityCategory.DIAGNOSTIC

    def test_temperature_sensor_has_no_entity_category(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxSensor(coordinator=coordinator, device_id="dev-1", sensor_key="temperature")
        assert sensor._attr_entity_category is None

    def test_signal_strength_disabled_by_default(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxSensor(
            coordinator=coordinator, device_id="dev-1", sensor_key="signal_strength"
        )
        assert sensor._attr_entity_registry_enabled_default is False

    def test_battery_sensor_enabled_by_default(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxSensor(coordinator=coordinator, device_id="dev-1", sensor_key="battery_level")
        assert sensor._attr_entity_registry_enabled_default is True

    def test_signal_strength_has_translation_key(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxSensor(
            coordinator=coordinator, device_id="dev-1", sensor_key="signal_strength"
        )
        assert sensor._attr_translation_key == "signal_strength"

    def test_available_when_online(self) -> None:
        device = self._make_device({})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxSensor(coordinator=coordinator, device_id="dev-1", sensor_key="temperature")
        assert sensor.available is True

    def test_unavailable_when_device_missing(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxSensor(coordinator=coordinator, device_id="dev-1", sensor_key="temperature")
        assert sensor.available is False

    def test_mobile_network_type_sensor(self) -> None:
        device = self._make_device({"mobile_network_type": "4G"})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxSensor(
            coordinator=coordinator, device_id="dev-1", sensor_key="mobile_network_type"
        )
        assert sensor.native_value == "4G"
        assert sensor._attr_translation_key == "mobile_network_type"

    def test_mobile_network_type_sensor_is_diagnostic(self) -> None:
        from homeassistant.const import EntityCategory

        coordinator = MagicMock()
        coordinator.devices = {}
        sensor = AjaxSensor(
            coordinator=coordinator, device_id="dev-1", sensor_key="mobile_network_type"
        )
        assert sensor._attr_entity_category == EntityCategory.DIAGNOSTIC

    def test_hub_sensor_has_no_via_device(self) -> None:
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
            statuses={"mobile_network_type": "4G"},
            battery=None,
        )
        coordinator = MagicMock()
        coordinator.devices = {"hub-1": hub_device}
        sensor = AjaxSensor(
            coordinator=coordinator, device_id="hub-1", sensor_key="mobile_network_type"
        )
        assert sensor._attr_device_info is not None
        assert "via_device" not in sensor._attr_device_info

    def test_non_hub_sensor_has_via_device(self) -> None:
        device = self._make_device({"temperature": 22.5})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        sensor = AjaxSensor(coordinator=coordinator, device_id="dev-1", sensor_key="temperature")
        assert sensor._attr_device_info is not None
        assert sensor._attr_device_info.get("via_device") == ("aegis_ajax", "hub-1")


def _make_hub_device(hub_id: str = "hub-1") -> Device:
    return Device(
        id=hub_id,
        hub_id=hub_id,
        name="Hub Plus",
        device_type="hub_plus",
        room_id=None,
        group_id=None,
        state=DeviceState.ONLINE,
        malfunctions=0,
        bypassed=False,
        statuses={},
        battery=None,
    )


class TestAjaxSimImeiSensor:
    def _make_coordinator(self, hub_id: str, sim: SimCardInfo | None) -> MagicMock:
        coordinator = MagicMock()
        coordinator.devices = {hub_id: _make_hub_device(hub_id)}
        coordinator.sim_info = {hub_id: sim} if sim else {}
        return coordinator

    def test_native_value_returns_imei(self) -> None:
        sim = SimCardInfo(active_sim=1, status=2, imei="352999001234567")
        coordinator = self._make_coordinator("hub-1", sim)
        sensor = AjaxSimImeiSensor(coordinator=coordinator, hub_id="hub-1")
        assert sensor.native_value == "352999001234567"

    def test_native_value_returns_none_when_no_sim_info(self) -> None:
        coordinator = self._make_coordinator("hub-1", None)
        sensor = AjaxSimImeiSensor(coordinator=coordinator, hub_id="hub-1")
        assert sensor.native_value is None

    def test_unique_id(self) -> None:
        coordinator = self._make_coordinator("hub-1", None)
        sensor = AjaxSimImeiSensor(coordinator=coordinator, hub_id="hub-1")
        assert sensor.unique_id == "aegis_ajax_hub-1_sim_imei"

    def test_translation_key(self) -> None:
        coordinator = self._make_coordinator("hub-1", None)
        sensor = AjaxSimImeiSensor(coordinator=coordinator, hub_id="hub-1")
        assert sensor._attr_translation_key == "sim_imei"

    def test_is_diagnostic(self) -> None:
        from homeassistant.const import EntityCategory

        coordinator = self._make_coordinator("hub-1", None)
        sensor = AjaxSimImeiSensor(coordinator=coordinator, hub_id="hub-1")
        assert sensor._attr_entity_category == EntityCategory.DIAGNOSTIC

    def test_available_when_sim_info_present(self) -> None:
        sim = SimCardInfo(active_sim=1, status=2, imei="123")
        coordinator = self._make_coordinator("hub-1", sim)
        sensor = AjaxSimImeiSensor(coordinator=coordinator, hub_id="hub-1")
        assert sensor.available is True

    def test_unavailable_when_no_sim_info(self) -> None:
        coordinator = self._make_coordinator("hub-1", None)
        sensor = AjaxSimImeiSensor(coordinator=coordinator, hub_id="hub-1")
        assert sensor.available is False


class TestHubWifiSensors:
    def _make_coordinator(self, hub_id: str = "hub-1") -> MagicMock:
        coordinator = MagicMock()
        coordinator.devices = {hub_id: _make_hub_device(hub_id)}
        coordinator.hub_network = {
            hub_id: HubNetworkState(
                wifi_connected=True,
                wifi_ssid="TestWiFi",
                wifi_signal_level="normal",
                wifi_ip="10.0.0.42",
            )
        }
        return coordinator

    def test_wifi_ssid_sensor_returns_ssid(self) -> None:
        coordinator = self._make_coordinator()
        sensor = AjaxHubWifiSsidSensor(coordinator, "hub-1")
        assert sensor.native_value == "TestWiFi"

    def test_wifi_signal_sensor_returns_signal(self) -> None:
        coordinator = self._make_coordinator()
        sensor = AjaxHubWifiSignalSensor(coordinator, "hub-1")
        assert sensor.native_value == "normal"

    def test_wifi_ip_sensor_returns_ip(self) -> None:
        coordinator = self._make_coordinator()
        sensor = AjaxHubWifiIpSensor(coordinator, "hub-1")
        assert sensor.native_value == "10.0.0.42"

    def test_hub_wifi_sensors_available_with_hts_state(self) -> None:
        coordinator = self._make_coordinator()
        assert AjaxHubWifiSsidSensor(coordinator, "hub-1").available is True
        assert AjaxHubWifiSignalSensor(coordinator, "hub-1").available is True
        assert AjaxHubWifiIpSensor(coordinator, "hub-1").available is True

    def test_hub_wifi_sensors_return_none_when_no_values(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {"hub-1": _make_hub_device("hub-1")}
        coordinator.hub_network = {"hub-1": HubNetworkState()}
        assert AjaxHubWifiSsidSensor(coordinator, "hub-1").native_value is None
        assert AjaxHubWifiIpSensor(coordinator, "hub-1").native_value is None
        assert AjaxHubWifiSignalSensor(coordinator, "hub-1").native_value == "unknown"


class TestHubNetworkSensors:
    def _make_coordinator(self, hub_id: str = "hub-1") -> MagicMock:
        coordinator = MagicMock()
        coordinator.devices = {hub_id: _make_hub_device(hub_id)}
        coordinator.hub_network = {
            hub_id: HubNetworkState(
                ethernet_connected=True,
                ethernet_ip="192.0.2.10",
                ethernet_gateway="192.0.2.1",
                ethernet_dns="192.0.2.53",
                gsm_network_type="4g",
            )
        }
        return coordinator

    def test_connection_type_sensor_returns_primary_connection(self) -> None:
        coordinator = self._make_coordinator()
        sensor = AjaxHubConnectionTypeSensor(coordinator, "hub-1")
        assert sensor.native_value == "ethernet"

    def test_hub_network_sensors_unavailable_when_hts_state_missing(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {"hub-1": _make_hub_device("hub-1")}
        coordinator.hub_network = {}

        assert AjaxHubConnectionTypeSensor(coordinator, "hub-1").available is False
        assert AjaxHubWifiSsidSensor(coordinator, "hub-1").available is False
        assert AjaxHubEthernetIpSensor(coordinator, "hub-1").available is False
        assert AjaxHubCellularNetworkSensor(coordinator, "hub-1").available is False

    def test_hub_network_sensors_share_availability_with_hts_state(self) -> None:
        coordinator = self._make_coordinator()

        assert AjaxHubConnectionTypeSensor(coordinator, "hub-1").available is True
        assert AjaxHubWifiSsidSensor(coordinator, "hub-1").available is True
        assert AjaxHubEthernetIpSensor(coordinator, "hub-1").available is True
        assert AjaxHubEthernetGatewaySensor(coordinator, "hub-1").available is True
        assert AjaxHubEthernetDnsSensor(coordinator, "hub-1").available is True
        assert AjaxHubCellularNetworkSensor(coordinator, "hub-1").available is True


class TestHubMonitoringCompanySensor:
    def _make_space(self, companies: tuple[MonitoringCompany, ...]) -> Space:
        return Space(
            id="space-1",
            hub_id="hub-1",
            name="Home",
            security_state=SecurityState.DISARMED,
            connection_status=ConnectionStatus.ONLINE,
            malfunctions_count=0,
            monitoring_companies=companies,
            monitoring_companies_loaded=True,
        )

    def _make_coordinator(self, companies: tuple[MonitoringCompany, ...]) -> MagicMock:
        coordinator = MagicMock()
        coordinator.devices = {"hub-1": _make_hub_device("hub-1")}
        coordinator.spaces = {"space-1": self._make_space(companies)}
        return coordinator

    def test_native_value_returns_company_name_for_single_approved_company(self) -> None:
        coordinator = self._make_coordinator(
            (
                MonitoringCompany(
                    name="Central One",
                    status=MonitoringCompanyStatus.APPROVED,
                ),
            )
        )
        sensor = AjaxHubMonitoringCompanySensor(coordinator, "space-1", "hub-1")
        assert sensor.native_value == "Central One"

    def test_native_value_returns_multiple_for_multiple_approved_companies(self) -> None:
        coordinator = self._make_coordinator(
            (
                MonitoringCompany(
                    name="Central One",
                    status=MonitoringCompanyStatus.APPROVED,
                ),
                MonitoringCompany(
                    name="Central Two",
                    status=MonitoringCompanyStatus.APPROVED,
                ),
            )
        )
        sensor = AjaxHubMonitoringCompanySensor(coordinator, "space-1", "hub-1")
        assert sensor.native_value == "multiple"

    def test_extra_state_attributes_group_companies_by_status(self) -> None:
        coordinator = self._make_coordinator(
            (
                MonitoringCompany(
                    name="Central One",
                    status=MonitoringCompanyStatus.APPROVED,
                ),
                MonitoringCompany(
                    name="Central Two",
                    status=MonitoringCompanyStatus.PENDING_APPROVAL,
                ),
                MonitoringCompany(
                    name="Central Three",
                    status=MonitoringCompanyStatus.PENDING_DELETION,
                ),
            )
        )
        sensor = AjaxHubMonitoringCompanySensor(coordinator, "space-1", "hub-1")
        assert sensor.extra_state_attributes == {
            "approved_companies": ["Central One"],
            "pending_approval_companies": ["Central Two"],
            "pending_removal_companies": ["Central Three"],
        }

    def test_state_payload_is_json_serializable(self) -> None:
        coordinator = self._make_coordinator(
            (
                MonitoringCompany(
                    name="Central One",
                    status=MonitoringCompanyStatus.APPROVED,
                ),
                MonitoringCompany(
                    name="Central Two",
                    status=MonitoringCompanyStatus.PENDING_APPROVAL,
                ),
            )
        )
        sensor = AjaxHubMonitoringCompanySensor(coordinator, "space-1", "hub-1")

        payload = {
            "state": sensor.native_value,
            "attributes": sensor.extra_state_attributes,
        }

        assert json.dumps(payload)

    def test_is_unavailable_until_monitoring_snapshot_loaded(self) -> None:
        coordinator = self._make_coordinator(())
        coordinator.spaces["space-1"] = replace(
            coordinator.spaces["space-1"], monitoring_companies_loaded=False
        )

        sensor = AjaxHubMonitoringCompanySensor(coordinator, "space-1", "hub-1")

        assert sensor.available is False


# ---------------------------------------------------------------------------
# Per-device electrical sensors (#123)
# ---------------------------------------------------------------------------


class TestAjaxDeviceElectricalSensors:
    """Current / energy / derived-power sensors for WallSwitch / Socket."""

    @staticmethod
    def _make_coordinator(
        device_type: str = "wall_switch",
        online: bool = True,
        current_ma: int | None = 40,
        power_consumed_wh: int | None = 2409,
        voltage_v: int | None = None,
    ) -> MagicMock:
        from custom_components.aegis_ajax.api.hts.hub_state import DeviceReadings
        from custom_components.aegis_ajax.api.models import Device

        coordinator = MagicMock()
        coordinator.rooms = {}
        device = Device(
            id="311B058D",
            hub_id="002B1A51",
            name="Relay",
            device_type=device_type,
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE if online else DeviceState.OFFLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )
        coordinator.devices = {"311B058D": device}
        if current_ma is not None or power_consumed_wh is not None or voltage_v is not None:
            coordinator.device_readings = {
                "311B058D": DeviceReadings(
                    current_ma=current_ma,
                    power_consumed_wh=power_consumed_wh,
                    voltage_v=voltage_v,
                )
            }
        else:
            coordinator.device_readings = {}
        return coordinator

    def test_current_sensor_scales_milliamps_to_amps(self) -> None:
        from custom_components.aegis_ajax.sensor import AjaxDeviceCurrentSensor

        coordinator = self._make_coordinator(current_ma=40)
        sensor = AjaxDeviceCurrentSensor(coordinator, "311B058D")
        assert sensor.native_value == 0.04

    def test_current_sensor_none_when_no_reading_yet(self) -> None:
        from custom_components.aegis_ajax.sensor import AjaxDeviceCurrentSensor

        coordinator = self._make_coordinator(current_ma=None, power_consumed_wh=None)
        sensor = AjaxDeviceCurrentSensor(coordinator, "311B058D")
        assert sensor.native_value is None

    def test_current_sensor_unavailable_when_no_reading_yet(self) -> None:
        from custom_components.aegis_ajax.sensor import AjaxDeviceCurrentSensor

        coordinator = self._make_coordinator(current_ma=None, power_consumed_wh=None)
        sensor = AjaxDeviceCurrentSensor(coordinator, "311B058D")
        assert sensor.available is False

    def test_current_sensor_unavailable_when_device_offline(self) -> None:
        from custom_components.aegis_ajax.sensor import AjaxDeviceCurrentSensor

        coordinator = self._make_coordinator(online=False)
        sensor = AjaxDeviceCurrentSensor(coordinator, "311B058D")
        assert sensor.available is False

    def test_energy_sensor_scales_watthours_to_kwh(self) -> None:
        from custom_components.aegis_ajax.sensor import AjaxDeviceEnergyConsumedSensor

        coordinator = self._make_coordinator(power_consumed_wh=2409)
        sensor = AjaxDeviceEnergyConsumedSensor(coordinator, "311B058D")
        assert sensor.native_value == 2.409

    def test_energy_sensor_state_class_is_total_increasing(self) -> None:
        # Required for HA Energy dashboard integration with a cumulative meter
        # (so a meter-reset is treated as a reset, not negative consumption).
        from homeassistant.components.sensor import SensorStateClass

        from custom_components.aegis_ajax.sensor import AjaxDeviceEnergyConsumedSensor

        coordinator = self._make_coordinator()
        sensor = AjaxDeviceEnergyConsumedSensor(coordinator, "311B058D")
        assert sensor.state_class is SensorStateClass.TOTAL_INCREASING

    def test_derived_power_uses_nominal_voltage(self) -> None:
        # No voltage reported by the device (older firmware) → falls back to
        # the labelled 230 V baseline, same as the Ajax app does.
        from custom_components.aegis_ajax.sensor import AjaxDeviceDerivedPowerSensor

        coordinator = self._make_coordinator(current_ma=40, voltage_v=None)
        sensor = AjaxDeviceDerivedPowerSensor(coordinator, "311B058D")
        assert sensor.native_value == pytest.approx(9.2)

    def test_derived_power_uses_real_voltage_when_present(self) -> None:
        # 0.04 A × 231 V = 9.24 W; PRO renders the line the same way.
        from custom_components.aegis_ajax.sensor import AjaxDeviceDerivedPowerSensor

        coordinator = self._make_coordinator(current_ma=40, voltage_v=231)
        sensor = AjaxDeviceDerivedPowerSensor(coordinator, "311B058D")
        assert sensor.native_value == pytest.approx(9.24)

    def test_derived_power_falls_back_when_voltage_is_zero(self) -> None:
        # Sentinel-zero is the firmware's "unset" marker, not a real
        # 0 V reading. Treat it as missing and use the nominal baseline.
        from custom_components.aegis_ajax.sensor import AjaxDeviceDerivedPowerSensor

        coordinator = self._make_coordinator(current_ma=40, voltage_v=0)
        sensor = AjaxDeviceDerivedPowerSensor(coordinator, "311B058D")
        assert sensor.native_value == pytest.approx(9.2)

    def test_voltage_sensor_returns_value(self) -> None:
        from custom_components.aegis_ajax.sensor import AjaxDeviceVoltageSensor

        coordinator = self._make_coordinator(voltage_v=230)
        sensor = AjaxDeviceVoltageSensor(coordinator, "311B058D")
        assert sensor.native_value == 230.0

    def test_voltage_sensor_none_when_not_reported(self) -> None:
        from custom_components.aegis_ajax.sensor import AjaxDeviceVoltageSensor

        coordinator = self._make_coordinator(current_ma=40, voltage_v=None)
        sensor = AjaxDeviceVoltageSensor(coordinator, "311B058D")
        assert sensor.native_value is None

    def test_voltage_sensor_device_class_voltage(self) -> None:
        from homeassistant.components.sensor import SensorDeviceClass

        from custom_components.aegis_ajax.sensor import AjaxDeviceVoltageSensor

        coordinator = self._make_coordinator(voltage_v=230)
        sensor = AjaxDeviceVoltageSensor(coordinator, "311B058D")
        assert sensor.device_class is SensorDeviceClass.VOLTAGE

    def test_derived_power_disabled_by_default(self) -> None:
        # The current+energy pair are the load-bearing entities; the
        # derived power is opt-in to avoid surfacing 3 sensors per relay
        # for the common case where users only want the consumption meter.
        from custom_components.aegis_ajax.sensor import AjaxDeviceDerivedPowerSensor

        coordinator = self._make_coordinator()
        sensor = AjaxDeviceDerivedPowerSensor(coordinator, "311B058D")
        assert sensor.entity_registry_enabled_default is False

    def test_unique_ids_distinct_across_four_entities(self) -> None:
        from custom_components.aegis_ajax.sensor import (
            AjaxDeviceCurrentSensor,
            AjaxDeviceDerivedPowerSensor,
            AjaxDeviceEnergyConsumedSensor,
            AjaxDeviceVoltageSensor,
        )

        coordinator = self._make_coordinator(voltage_v=230)
        uids = {
            AjaxDeviceCurrentSensor(coordinator, "311B058D")._attr_unique_id,
            AjaxDeviceVoltageSensor(coordinator, "311B058D")._attr_unique_id,
            AjaxDeviceEnergyConsumedSensor(coordinator, "311B058D")._attr_unique_id,
            AjaxDeviceDerivedPowerSensor(coordinator, "311B058D")._attr_unique_id,
        }
        assert len(uids) == 4
