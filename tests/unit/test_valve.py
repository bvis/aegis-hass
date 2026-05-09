"""Tests for the valve platform (Ajax WaterStop, read-only — #117)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.aegis_ajax.api.models import Device, Space
from custom_components.aegis_ajax.const import (
    ConnectionStatus,
    DeviceState,
    SecurityState,
)
from custom_components.aegis_ajax.valve import VALVE_DEVICE_TYPES, AjaxValve


def _make_device(device_type: str = "water_stop", **status_overrides: Any) -> Device:  # noqa: ANN401
    return Device(
        id="valve-1",
        hub_id="hub-1",
        name="Kitchen WaterStop",
        device_type=device_type,
        room_id=None,
        group_id=None,
        state=DeviceState.ONLINE,
        malfunctions=0,
        bypassed=False,
        statuses=dict(status_overrides),
        battery=None,
    )


def _make_coordinator(device: Device) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = {device.id: device}
    coordinator.rooms = {}
    coordinator.spaces = {
        "space-A": Space(
            id="space-A",
            hub_id=device.hub_id,
            name="Home",
            security_state=SecurityState.DISARMED,
            connection_status=ConnectionStatus.ONLINE,
            malfunctions_count=0,
            monitoring_companies=(),
            monitoring_companies_loaded=True,
            groups=(),
            group_mode_enabled=False,
        )
    }
    return coordinator


class TestValveDeviceTypes:
    def test_water_stop_in_valve_types(self) -> None:
        assert "water_stop" in VALVE_DEVICE_TYPES

    def test_water_stop_base_in_valve_types(self) -> None:
        # `water_stop_base` is the Fibra (wired) sibling — same channel
        # status shape, same parser path, must surface a valve entity too.
        assert "water_stop_base" in VALVE_DEVICE_TYPES


class TestAjaxValveState:
    def test_is_closed_true_when_valve_ch1_false(self) -> None:
        # Parser emits `valve_ch1=False` on `STATE_OFF` (water stopped).
        device = _make_device(valve_ch1=False)
        valve = AjaxValve(coordinator=_make_coordinator(device), device_id=device.id)
        assert valve.is_closed is True

    def test_is_closed_false_when_valve_ch1_true(self) -> None:
        # `valve_ch1=True` means STATE_ON — water flowing — valve open.
        device = _make_device(valve_ch1=True)
        valve = AjaxValve(coordinator=_make_coordinator(device), device_id=device.id)
        assert valve.is_closed is False

    def test_is_closed_unknown_when_key_absent(self) -> None:
        # `STATE_UNKNOWN` / `STATE_UNSPECIFIED` leave the key absent —
        # the entity must render as `unknown` rather than guess closed.
        device = _make_device()  # no valve_ch1 key
        valve = AjaxValve(coordinator=_make_coordinator(device), device_id=device.id)
        assert valve.is_closed is None


class TestAjaxValveTransitioning:
    def test_is_closing_when_open_and_transitioning(self) -> None:
        # Valve is currently open (STATE_ON) and motor is moving — must
        # be on its way to closed. Read-only, so no async_close call,
        # but the entity flag still drives the right HA card animation.
        device = _make_device(valve_ch1=True, valve_ch1_transitioning=True)
        valve = AjaxValve(coordinator=_make_coordinator(device), device_id=device.id)
        assert valve.is_closing is True
        assert valve.is_opening is False

    def test_is_opening_when_closed_and_transitioning(self) -> None:
        device = _make_device(valve_ch1=False, valve_ch1_transitioning=True)
        valve = AjaxValve(coordinator=_make_coordinator(device), device_id=device.id)
        assert valve.is_opening is True
        assert valve.is_closing is False

    def test_neither_when_not_transitioning(self) -> None:
        device = _make_device(valve_ch1=False, valve_ch1_transitioning=False)
        valve = AjaxValve(coordinator=_make_coordinator(device), device_id=device.id)
        assert valve.is_opening is False
        assert valve.is_closing is False


class TestAjaxValveAttributes:
    def test_stuck_attribute_exposed(self) -> None:
        device = _make_device(valve_ch1=False, valve_ch1_stuck=True)
        valve = AjaxValve(coordinator=_make_coordinator(device), device_id=device.id)
        assert (valve.extra_state_attributes or {}).get("stuck") is True

    def test_stuck_false_when_absent(self) -> None:
        device = _make_device(valve_ch1=True)
        valve = AjaxValve(coordinator=_make_coordinator(device), device_id=device.id)
        assert (valve.extra_state_attributes or {}).get("stuck") is False


class TestAjaxValveDeviceMissing:
    """If the coordinator drops the device between polls (rare — only on
    a snapshot that excludes it), every property must degrade gracefully
    instead of raising on `None.statuses`.
    """

    def _orphaned_valve(self) -> AjaxValve:
        device = _make_device(valve_ch1=True)
        coordinator = _make_coordinator(device)
        valve = AjaxValve(coordinator=coordinator, device_id=device.id)
        coordinator.devices = {}  # device gone
        return valve

    def test_is_closed_none(self) -> None:
        assert self._orphaned_valve().is_closed is None

    def test_is_closing_false(self) -> None:
        assert self._orphaned_valve().is_closing is False

    def test_is_opening_false(self) -> None:
        assert self._orphaned_valve().is_opening is False

    def test_extra_state_attributes_empty(self) -> None:
        assert self._orphaned_valve().extra_state_attributes == {}


class TestAjaxValveAvailability:
    def test_unavailable_when_device_offline(self) -> None:
        device = Device(
            id="valve-1",
            hub_id="hub-1",
            name="Kitchen WaterStop",
            device_type="water_stop",
            room_id=None,
            group_id=None,
            state=DeviceState.OFFLINE,
            malfunctions=0,
            bypassed=False,
            statuses={"valve_ch1": True},
            battery=None,
        )
        valve = AjaxValve(coordinator=_make_coordinator(device), device_id=device.id)
        assert valve.available is False


class TestAjaxValveSupportedFeatures:
    def test_no_supported_features(self) -> None:
        # Read-only path: no `SwitchWaterStopService` in v3 protos, so
        # exposing OPEN/CLOSE features would surface controls that fail
        # silently. Keep features at 0 until the command path is captured.
        device = _make_device(valve_ch1=True)
        valve = AjaxValve(coordinator=_make_coordinator(device), device_id=device.id)
        assert int(valve.supported_features) == 0

    def test_reports_position_disabled(self) -> None:
        # WaterStop is binary, not positional. HA renders a position bar
        # if `reports_position` is True — must stay False.
        device = _make_device(valve_ch1=True)
        valve = AjaxValve(coordinator=_make_coordinator(device), device_id=device.id)
        assert valve.reports_position is False


@pytest.mark.asyncio
async def test_async_setup_entry_creates_one_entity_per_water_stop() -> None:
    from homeassistant.const import Platform

    from custom_components.aegis_ajax.valve import async_setup_entry

    devices = {
        "v1": _make_device("water_stop", valve_ch1=True),
        "v2": _make_device("water_stop_base", valve_ch1=False),
        "x1": _make_device("door_protect"),  # not a valve — must be skipped
    }
    devices["v2"] = devices["v2"].__class__(  # noqa: SLF001  (frozen dataclass replace shorthand)
        **{**devices["v2"].__dict__, "id": "v2"}
    )
    devices["x1"] = devices["x1"].__class__(**{**devices["x1"].__dict__, "id": "x1"})

    coordinator = MagicMock()
    coordinator.devices = devices
    coordinator.rooms = {}
    coordinator.spaces = {}

    entry = MagicMock()
    entry.runtime_data = coordinator
    added: list[Any] = []

    def add_entities(items: list[Any]) -> None:
        added.extend(items)

    await async_setup_entry(MagicMock(), entry, add_entities)
    assert {e.unique_id for e in added} == {"aegis_ajax_v1_valve", "aegis_ajax_v2_valve"}
    assert all(isinstance(e, AjaxValve) for e in added)
    assert Platform.VALVE  # sanity: HA does ship the constant we'll register against
