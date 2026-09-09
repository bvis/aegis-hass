"""Tests for sensor entities."""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.aegis_ajax.api.hts.hub_state import (
    DIRECT_POWER_DEVICE_TYPES,
    ELECTRICAL_DEVICE_TYPES,
    HTS_TEMPERATURE_DEVICE_TYPES,
    HubNetworkState,
)
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

    def test_nvr_channel_count_types_exist(self) -> None:
        # #425: the NVR box's only per-row measurements are its channel
        # counts; without descriptors the parsed device renders a bare card.
        assert "channels_online" in SENSOR_TYPES
        assert "channels_total" in SENSOR_TYPES

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

    def test_nvr_channel_counts(self) -> None:
        # #425: the NVR box's channel counters ride `statuses` like any
        # other status-sourced reading.
        device = self._make_device({"channels_online": 6, "channels_total": 8})
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        online = AjaxSensor(
            coordinator=coordinator, device_id="dev-1", sensor_key="channels_online"
        )
        total = AjaxSensor(coordinator=coordinator, device_id="dev-1", sensor_key="channels_total")
        assert online.native_value == 6
        assert total.native_value == 8

    @pytest.mark.asyncio
    async def test_nvr_channel_count_sensors_created_at_setup(self) -> None:
        """Creation is gated on the keys being present, so only devices that
        actually report channel counts (the NVR box, #425) grow the sensors."""
        from custom_components.aegis_ajax.sensor import async_setup_entry

        nvr = Device(
            id="310B121D",
            hub_id="310B121D",
            name="NVR",
            device_type="video_edge_nvr",
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={"channels_online": 6, "channels_total": 8, "video_edge_box": True},
            battery=None,
        )
        coordinator = MagicMock()
        coordinator.devices = {"310B121D": nvr}
        coordinator.rooms = {}
        coordinator.spaces = {}
        coordinator.sim_info = {}
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        with patch("custom_components.aegis_ajax.sensor._remove_orphan_outlet_power_derived"):
            await async_setup_entry(MagicMock(), entry, added.extend)

        keys = {getattr(e, "_sensor_key", None) for e in added}
        assert "channels_online" in keys
        assert "channels_total" in keys

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
        with patch("custom_components.aegis_ajax.entity._VIA_DEVICE_ID_SUPPORTED", False):
            sensor = AjaxSensor(
                coordinator=coordinator, device_id="dev-1", sensor_key="temperature"
            )
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


class TestAjaxSimImeiSensorConstruction:
    def test_sensor_can_be_constructed_when_hub_device_exists_but_sim_info_is_empty(self) -> None:
        coordinator = MagicMock()
        coordinator.devices = {"hub-1": _make_hub_device("hub-1")}
        coordinator.sim_info = {}
        sensor = AjaxSimImeiSensor(coordinator=coordinator, hub_id="hub-1")
        assert sensor.unique_id == "aegis_ajax_hub-1_sim_imei"
        assert sensor.available is False

    @staticmethod
    async def _setup(sim_info: dict) -> list:
        """Run the real platform setup with a hub present and a given sim_info."""
        from custom_components.aegis_ajax.sensor import async_setup_entry

        coordinator = MagicMock()
        coordinator.devices = {"hub-1": _make_hub_device("hub-1")}
        coordinator.rooms = {}
        coordinator.spaces = {
            "space-1": Space(
                id="space-1",
                hub_id="hub-1",
                name="Home",
                security_state=SecurityState.DISARMED,
                connection_status=ConnectionStatus.ONLINE,
                malfunctions_count=0,
            )
        }
        coordinator.sim_info = sim_info

        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        with patch("custom_components.aegis_ajax.sensor._remove_orphan_outlet_power_derived"):
            await async_setup_entry(MagicMock(), entry, added.extend)
        return added

    @pytest.mark.asyncio
    async def test_imei_entity_is_created_even_when_the_sim_read_has_not_succeeded(self) -> None:
        """#379: creation must not depend on the SIM read having worked.

        This is the assertion that actually guards the fix. Gating creation on
        `sim_info` meant a failed or slow read produced no entity at all, and
        Home Assistant never removes an entity an integration stops offering —
        so one created on an earlier start sat `unavailable` forever with
        nothing to explain it.

        Note the sibling test above passes with or without the fix, because it
        constructs the sensor directly and never exercises the setup path where
        the condition lives.
        """
        added = await self._setup(sim_info={})

        assert any(isinstance(e, AjaxSimImeiSensor) for e in added)

    @pytest.mark.asyncio
    async def test_imei_entity_is_still_created_when_the_sim_read_did_succeed(self) -> None:
        # Negative control: the previously-working path must keep working.
        added = await self._setup(
            sim_info={"hub-1": SimCardInfo(active_sim=1, status=2, imei="123456789012345")}
        )

        assert any(isinstance(e, AjaxSimImeiSensor) for e in added)


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

    def test_native_value_joins_names_for_multiple_approved_companies(self) -> None:
        coordinator = self._make_coordinator(
            (
                MonitoringCompany(
                    name="Central Two",
                    status=MonitoringCompanyStatus.APPROVED,
                ),
                MonitoringCompany(
                    name="Central One",
                    status=MonitoringCompanyStatus.APPROVED,
                ),
            )
        )
        sensor = AjaxHubMonitoringCompanySensor(coordinator, "space-1", "hub-1")
        # Sorted alphabetically so the rendered state doesn't flicker between
        # equivalent polls that happen to return companies in a different order.
        assert sensor.native_value == "Central One, Central Two"

    def test_native_value_falls_back_to_count_when_joined_names_exceed_state_limit(self) -> None:
        # HA state strings are truncated at 255 chars. With absurdly long names
        # the joined form overflows; the sensor falls back to a count sentinel
        # so the value stays meaningful instead of being clipped mid-name.
        coordinator = self._make_coordinator(
            tuple(
                MonitoringCompany(
                    name=f"Central {'X' * 60} #{i}",
                    status=MonitoringCompanyStatus.APPROVED,
                )
                for i in range(4)
            )
        )
        sensor = AjaxHubMonitoringCompanySensor(coordinator, "space-1", "hub-1")
        assert sensor.native_value == "4 companies"

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
        power_w: int | None = None,
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
        if any(v is not None for v in (current_ma, power_consumed_wh, voltage_v, power_w)):
            coordinator.device_readings = {
                "311B058D": DeviceReadings(
                    current_ma=current_ma,
                    power_consumed_wh=power_consumed_wh,
                    voltage_v=voltage_v,
                    power_w=power_w,
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

    def test_relay_voltage_sensor_reports_volts_not_millivolts(self) -> None:
        """#325 end to end: the reporter's Relay row must not read 11,671 V.

        Goes through the parser rather than hand-building `DeviceReadings`, so
        the per-family unit conversion is part of what is under test.
        """
        from custom_components.aegis_ajax.api.hts.hub_state import (
            DEVICE_KEY_VOLTAGE_V,
            parse_device_readings,
        )
        from custom_components.aegis_ajax.sensor import AjaxDeviceVoltageSensor

        readings = parse_device_readings("relay", {DEVICE_KEY_VOLTAGE_V: b"\x2d\x97"})
        coordinator = self._make_coordinator(device_type="relay")
        coordinator.device_readings = {"311B058D": readings}

        sensor = AjaxDeviceVoltageSensor(coordinator, "311B058D")
        assert sensor.native_value == pytest.approx(11.671)

    def test_relay_derived_power_not_inflated_by_millivolts(self) -> None:
        """The derived-power sensor inherited the 1000x error via voltage (#325).

        0.04 A x 11.671 V = 0.467 W. Before the fix the same row produced
        ~467 W out of a dry-contact relay.
        """
        from custom_components.aegis_ajax.api.hts.hub_state import (
            DEVICE_KEY_CURRENT_MA,
            DEVICE_KEY_VOLTAGE_V,
            parse_device_readings,
        )
        from custom_components.aegis_ajax.sensor import AjaxDeviceDerivedPowerSensor

        readings = parse_device_readings(
            "relay",
            {DEVICE_KEY_VOLTAGE_V: b"\x2d\x97", DEVICE_KEY_CURRENT_MA: b"\x28"},
        )
        coordinator = self._make_coordinator(device_type="relay")
        coordinator.device_readings = {"311B058D": readings}

        sensor = AjaxDeviceDerivedPowerSensor(coordinator, "311B058D")
        assert sensor.native_value == pytest.approx(0.46684)

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

    def test_direct_power_sensor_returns_device_reported_value(self) -> None:
        # Outlet Type E reports instantaneous power directly (#179).
        from custom_components.aegis_ajax.sensor import AjaxDevicePowerSensor

        coordinator = self._make_coordinator(device_type="socket_outlet_type_e", power_w=2080)
        sensor = AjaxDevicePowerSensor(coordinator, "311B058D")
        assert sensor.native_value == 2080

    def test_direct_power_sensor_none_when_not_reported(self) -> None:
        from custom_components.aegis_ajax.sensor import AjaxDevicePowerSensor

        coordinator = self._make_coordinator(
            device_type="socket_outlet_type_e", current_ma=None, power_consumed_wh=None
        )
        sensor = AjaxDevicePowerSensor(coordinator, "311B058D")
        assert sensor.native_value is None

    def test_direct_power_sensor_enabled_by_default(self) -> None:
        # Direct readings are real (not derived) — surface them by default.
        from custom_components.aegis_ajax.sensor import AjaxDevicePowerSensor

        coordinator = self._make_coordinator(device_type="socket_outlet_type_e", power_w=15)
        sensor = AjaxDevicePowerSensor(coordinator, "311B058D")
        assert sensor.entity_registry_enabled_default is True

    def test_direct_power_sensor_unique_id_distinct_from_derived(self) -> None:
        # `_power` (Outlet) vs `_power_derived` (WallSwitch) — separate
        # entity registry entries so users running both families don't
        # collide.
        from custom_components.aegis_ajax.sensor import (
            AjaxDeviceDerivedPowerSensor,
            AjaxDevicePowerSensor,
        )

        coordinator_outlet = self._make_coordinator(device_type="socket_outlet_type_e", power_w=15)
        coordinator_ws = self._make_coordinator(device_type="wall_switch")
        outlet_sensor = AjaxDevicePowerSensor(coordinator_outlet, "311B058D")
        ws_sensor = AjaxDeviceDerivedPowerSensor(coordinator_ws, "311B058D")
        assert outlet_sensor.unique_id == "aegis_ajax_311B058D_power"
        assert ws_sensor.unique_id == "aegis_ajax_311B058D_power_derived"

    @pytest.mark.asyncio
    async def test_native_value_falls_back_to_restored_when_no_live_reading(self) -> None:
        """Bruno's case: hub never re-pushes constant readings (#123)."""
        from unittest.mock import AsyncMock as _AsyncMock

        from homeassistant.components.sensor import SensorExtraStoredData

        from custom_components.aegis_ajax.sensor import AjaxDeviceCurrentSensor

        coordinator = self._make_coordinator(current_ma=None, power_consumed_wh=None)
        sensor = AjaxDeviceCurrentSensor(coordinator, "311B058D")
        # Simulate HA RestoreSensor handing us last persisted value.
        sensor.async_get_last_sensor_data = _AsyncMock(
            return_value=SensorExtraStoredData(native_value=0.04, native_unit_of_measurement="A")
        )
        # Patch out the upstream subscribers/event loop touches on add.
        sensor.async_internal_added_to_hass = _AsyncMock()
        sensor.async_on_remove = MagicMock()
        sensor.hass = MagicMock()
        await sensor.async_added_to_hass()

        # No live reading → returns the restored 0.04 A instead of None.
        assert sensor.native_value == 0.04
        assert sensor.available is True

    @pytest.mark.asyncio
    async def test_live_reading_wins_over_restored_value(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock

        from homeassistant.components.sensor import SensorExtraStoredData

        from custom_components.aegis_ajax.sensor import AjaxDeviceVoltageSensor

        coordinator = self._make_coordinator(voltage_v=231)
        sensor = AjaxDeviceVoltageSensor(coordinator, "311B058D")
        sensor.async_get_last_sensor_data = _AsyncMock(
            return_value=SensorExtraStoredData(native_value=228, native_unit_of_measurement="V")
        )
        sensor.async_internal_added_to_hass = _AsyncMock()
        sensor.async_on_remove = MagicMock()
        sensor.hass = MagicMock()
        await sensor.async_added_to_hass()

        # Live reading 231 V > restored 228 V — fresh value wins.
        assert sensor.native_value == 231.0

    @pytest.mark.asyncio
    async def test_restore_skips_non_numeric_state(self) -> None:
        """`unknown` or `unavailable` strings can't be parsed back as floats."""
        from unittest.mock import AsyncMock as _AsyncMock

        from homeassistant.components.sensor import SensorExtraStoredData

        from custom_components.aegis_ajax.sensor import AjaxDeviceCurrentSensor

        coordinator = self._make_coordinator(current_ma=None, power_consumed_wh=None)
        sensor = AjaxDeviceCurrentSensor(coordinator, "311B058D")
        sensor.async_get_last_sensor_data = _AsyncMock(
            return_value=SensorExtraStoredData(
                native_value="unknown", native_unit_of_measurement="A"
            )
        )
        sensor.async_internal_added_to_hass = _AsyncMock()
        sensor.async_on_remove = MagicMock()
        sensor.hass = MagicMock()
        await sensor.async_added_to_hass()

        assert sensor.native_value is None
        assert sensor.available is False

    def test_sensor_stays_available_across_hts_disconnect(self) -> None:
        """#146 — transient HTS reconnect must not blank the readings sensors.

        The hub remembers device state across our socket outage, so the
        cached value remains the truth until a fresh STATUS_UPDATE delta
        lands when HTS comes back. Without this, every reconnect cycle
        (5+ min on busy installs) renders the sensor `unavailable` even
        though we have a perfectly good last-known value.
        """
        from unittest.mock import patch as _patch

        from custom_components.aegis_ajax.api.hts.hub_state import DeviceReadings
        from custom_components.aegis_ajax.api.models import Device
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
        from custom_components.aegis_ajax.sensor import AjaxDeviceCurrentSensor

        hass = MagicMock()
        client = MagicMock()
        with _patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
            return_value=None,
        ):
            coordinator = AjaxCobrandedCoordinator(
                hass=hass, client=client, space_ids=["s1"], poll_interval=30
            )
        coordinator.hass = hass
        coordinator.devices = {
            "311B058D": Device(
                id="311B058D",
                hub_id="002B1A51",
                name="Relay",
                device_type="wall_switch",
                room_id=None,
                group_id=None,
                state=DeviceState.ONLINE,
                malfunctions=0,
                bypassed=False,
                statuses={},
                battery=None,
            )
        }
        coordinator.device_readings["311B058D"] = DeviceReadings(
            current_ma=40, power_consumed_wh=2409
        )
        coordinator.async_set_updated_data = MagicMock()

        sensor = AjaxDeviceCurrentSensor(coordinator, "311B058D")
        assert sensor.available is True
        assert sensor.native_value == 0.04

        coordinator._handle_hts_disconnect(reconnect=False)

        # Readings preserved → sensor reports the cached value, not `unavailable`.
        assert sensor.available is True
        assert sensor.native_value == 0.04

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


class TestRemoveOrphanOutletPowerDerived:
    """Migration: drop legacy `_power_derived` entity for Outlet devices (#179)."""

    @staticmethod
    def _make_coordinator(devices: dict[str, str]) -> MagicMock:
        from custom_components.aegis_ajax.api.models import Device

        coordinator = MagicMock()
        coordinator.devices = {
            device_id: Device(
                id=device_id,
                hub_id="002B1A51",
                name=f"Device {device_id}",
                device_type=device_type,
                room_id=None,
                group_id=None,
                state=DeviceState.ONLINE,
                malfunctions=0,
                bypassed=False,
                statuses={},
                battery=None,
            )
            for device_id, device_type in devices.items()
        }
        return coordinator

    def test_removes_orphan_only_for_outlet_devices(self) -> None:
        from custom_components.aegis_ajax.sensor import _remove_orphan_outlet_power_derived

        coordinator = self._make_coordinator(
            {
                "OUTLET_E": "socket_outlet_type_e",
                "OUTLET_F": "socket_outlet_type_f",
                "WALLSWITCH": "wall_switch",
                "MOTION": "motion_protect",
            }
        )

        # Fake registry: tracks unique_id → entity_id and records removals.
        removed: list[str] = []

        def async_get_entity_id(domain: str, platform: str, unique_id: str) -> str | None:
            assert domain == "sensor"
            assert platform == "aegis_ajax"
            # Pretend every device has BOTH a `_power` and `_power_derived`
            # entity registered from a previous version.
            if unique_id.endswith("_power_derived"):
                return f"sensor.{unique_id}"
            return None

        registry = MagicMock()
        registry.async_get_entity_id.side_effect = async_get_entity_id
        registry.async_remove.side_effect = removed.append

        with patch("homeassistant.helpers.entity_registry.async_get", return_value=registry):
            _remove_orphan_outlet_power_derived(MagicMock(), coordinator)

        assert sorted(removed) == [
            "sensor.aegis_ajax_OUTLET_E_power_derived",
            "sensor.aegis_ajax_OUTLET_F_power_derived",
        ]
        # WallSwitch's own `_power_derived` is the canonical entity for
        # that family; must not be removed.
        assert "sensor.aegis_ajax_WALLSWITCH_power_derived" not in removed

    def test_no_op_when_orphan_already_absent(self) -> None:
        from custom_components.aegis_ajax.sensor import _remove_orphan_outlet_power_derived

        coordinator = self._make_coordinator({"OUTLET_E": "socket_outlet_type_e"})
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None  # Fresh install: nothing to remove.

        with patch("homeassistant.helpers.entity_registry.async_get", return_value=registry):
            _remove_orphan_outlet_power_derived(MagicMock(), coordinator)

        registry.async_remove.assert_not_called()


class TestTemperatureSensorCreationGate:
    """The temperature entity must exist before its first HTS value arrives.

    A device whose temperature is sourced from the hub's status stream
    (HTS sub-key 0x02) has no `temperature` in the gRPC snapshot at
    startup, so gating entity creation on `key in device.statuses` meant
    the entity was never created and the value had nowhere to land.
    Membership of `HTS_TEMPERATURE_DEVICE_TYPES` is the guarantee that a
    source exists, so it — not the current snapshot — decides creation.
    """

    @staticmethod
    def _make_device(device_id: str, device_type: str, statuses: dict | None = None) -> Device:
        return Device(
            id=device_id,
            hub_id="hub-1",
            name=f"Device {device_id}",
            device_type=device_type,
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses=statuses or {},
            battery=None,
        )

    @staticmethod
    async def _setup(devices: dict[str, Device]) -> list:
        from custom_components.aegis_ajax.sensor import async_setup_entry

        coordinator = MagicMock()
        coordinator.devices = devices
        coordinator.rooms = {}
        coordinator.spaces = {}
        coordinator.sim_info = {}

        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []

        with patch("custom_components.aegis_ajax.sensor._remove_orphan_outlet_power_derived"):
            await async_setup_entry(MagicMock(), entry, added.extend)
        return added

    @pytest.mark.asyncio
    @pytest.mark.parametrize("device_type", sorted(HTS_TEMPERATURE_DEVICE_TYPES))
    async def test_hts_sourced_family_gets_temperature_before_first_value(
        self, device_type: str
    ) -> None:
        """Every HTS-sourced temperature family, with an empty snapshot.

        The empty snapshot is the point: #375 created the entity only when a
        value was already present, so an HTS-sourced family never got one and
        the reading had nowhere to land. The three Double Deck variants are the
        original regression case; sweeping the whole gate means a family added
        later is covered without anyone remembering to extend a list.
        """
        added = await self._setup({"s1": self._make_device("s1", device_type)})

        assert "aegis_ajax_s1_temperature" in {e.unique_id for e in added}

    @pytest.mark.asyncio
    async def test_grpc_only_family_gets_no_temperature_until_a_value_arrives(self) -> None:
        """The Curtain Outdoor Mini must stay excluded (#269).

        It is the one member of `HUB_DEVICE_TEMPERATURE_DEVICE_TYPES`
        with no HTS source, so creating its entity up front would leave
        a permanently `unknown` sensor that Home Assistant never evicts.
        """
        added = await self._setup(
            {"m1": self._make_device("m1", "motion_protect_curtain_outdoor_mini")}
        )

        assert "aegis_ajax_m1_temperature" not in {e.unique_id for e in added}

    @pytest.mark.asyncio
    async def test_grpc_only_family_still_gets_temperature_once_reported(self) -> None:
        """The exclusion is about *timing*, not about dropping the family."""
        added = await self._setup(
            {
                "m1": self._make_device(
                    "m1", "motion_protect_curtain_outdoor_mini", {"temperature": 18.0}
                )
            }
        )

        assert "aegis_ajax_m1_temperature" in {e.unique_id for e in added}

    @pytest.mark.asyncio
    async def test_gate_does_not_leak_to_other_status_keys(self) -> None:
        """Only `temperature` is pre-created; the rest still need a value."""
        added = await self._setup({"s1": self._make_device("s1", "street_siren_double_deck")})

        unique_ids = {e.unique_id for e in added}
        assert "aegis_ajax_s1_humidity" not in unique_ids
        assert "aegis_ajax_s1_co2" not in unique_ids
        assert "aegis_ajax_s1_signal_strength" not in unique_ids


class TestElectricalSensorCreationGate:
    """Which families grow the electrical sensors, and which power entity they get.

    This gate had no test at all before #332 PR-5: deleting the condition
    outright left the whole suite green, so a refactor could have dropped every
    WallSwitch / Socket / Outlet electrical entity on a live install without CI
    noticing. The families come from the HTS sub-key map, so sweeping the set
    keeps a family added there covered without anyone extending a list here.
    """

    @staticmethod
    def _make_device(device_id: str, device_type: str) -> Device:
        return Device(
            id=device_id,
            hub_id="hub-1",
            name=f"Device {device_id}",
            device_type=device_type,
            room_id=None,
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )

    @staticmethod
    async def _setup(devices: dict[str, Device]) -> list:
        from custom_components.aegis_ajax.sensor import async_setup_entry

        coordinator = MagicMock()
        coordinator.devices = devices
        coordinator.rooms = {}
        coordinator.spaces = {}
        coordinator.sim_info = {}

        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []

        with patch("custom_components.aegis_ajax.sensor._remove_orphan_outlet_power_derived"):
            await async_setup_entry(MagicMock(), entry, added.extend)
        return added

    @pytest.mark.asyncio
    @pytest.mark.parametrize("device_type", sorted(ELECTRICAL_DEVICE_TYPES))
    async def test_every_electrical_family_gets_the_three_readings(self, device_type: str) -> None:
        added = await self._setup({"e1": self._make_device("e1", device_type)})

        unique_ids = {e.unique_id for e in added}
        assert "aegis_ajax_e1_current" in unique_ids
        assert "aegis_ajax_e1_voltage" in unique_ids
        assert "aegis_ajax_e1_energy_consumed" in unique_ids

    @pytest.mark.asyncio
    @pytest.mark.parametrize("device_type", sorted(DIRECT_POWER_DEVICE_TYPES))
    async def test_direct_power_family_gets_a_real_power_sensor(self, device_type: str) -> None:
        """The Outlet reports `power_w`, so it must not get the derived placeholder."""
        added = await self._setup({"e1": self._make_device("e1", device_type)})

        unique_ids = {e.unique_id for e in added}
        assert "aegis_ajax_e1_power" in unique_ids
        assert "aegis_ajax_e1_power_derived" not in unique_ids

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "device_type", sorted(ELECTRICAL_DEVICE_TYPES - DIRECT_POWER_DEVICE_TYPES)
    )
    async def test_wallswitch_family_gets_the_derived_power_sensor(self, device_type: str) -> None:
        """No `power_w` in the firmware's readings, so power is current × voltage."""
        added = await self._setup({"e1": self._make_device("e1", device_type)})

        unique_ids = {e.unique_id for e in added}
        assert "aegis_ajax_e1_power_derived" in unique_ids
        assert "aegis_ajax_e1_power" not in unique_ids

    @pytest.mark.asyncio
    @pytest.mark.parametrize("device_type", ["door_protect", "home_siren", "light_switch_dimmer"])
    async def test_non_electrical_family_gets_no_electrical_sensors(self, device_type: str) -> None:
        added = await self._setup({"d1": self._make_device("d1", device_type)})

        unique_ids = {e.unique_id for e in added}
        for suffix in ("current", "voltage", "energy_consumed", "power", "power_derived"):
            assert f"aegis_ajax_d1_{suffix}" not in unique_ids
