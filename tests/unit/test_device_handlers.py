"""Tests for device capability handlers."""

from __future__ import annotations

import pytest

from custom_components.aegis_ajax import device_handlers
from custom_components.aegis_ajax.api.models import Device
from custom_components.aegis_ajax.const import DeviceState


def _device(device_type: str) -> Device:
    return Device(
        id="device-1",
        hub_id="hub-1",
        name="Test device",
        device_type=device_type,
        room_id=None,
        group_id=None,
        state=DeviceState.ONLINE,
        malfunctions=0,
        bypassed=False,
        statuses={},
        battery=None,
    )


def test_build_handler_map_rejects_duplicate_device_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handler collisions must fail instead of silently changing capabilities."""
    handler = device_handlers.StaticDeviceHandler(("duplicate_type",), ())
    monkeypatch.setattr(device_handlers, "_HANDLERS", (handler, handler))

    with pytest.raises(
        ValueError, match="Duplicate device handler registration for 'duplicate_type'"
    ):
        device_handlers._build_handler_map()


@pytest.mark.parametrize("device_type", ["smart_lock", "smart_lock_yale"])
def test_lock_capability_is_registered(device_type: str) -> None:
    assert device_handlers.capabilities_for(_device(device_type)).is_lock


def test_non_lock_does_not_have_lock_capability() -> None:
    assert not device_handlers.capabilities_for(_device("door_protect")).is_lock


@pytest.mark.parametrize(
    "device_type",
    [
        "motion_cam",
        "motion_cam_outdoor",
        "motion_cam_fibra",
        "motion_cam_phod",
        "motion_cam_outdoor_phod",
        "motion_cam_fibra_base",
        "motion_cam_outdoor_two_four_phod",
    ],
)
def test_camera_capability_is_registered(device_type: str) -> None:
    assert device_handlers.capabilities_for(_device(device_type)).is_camera


@pytest.mark.parametrize(
    "device_type",
    [
        "motion_cam_phod",
        "motion_cam_outdoor_phod",
        "motion_cam_fibra_base",
        "motion_cam_outdoor_two_four_phod",
    ],
)
def test_phod_capability_is_registered(device_type: str) -> None:
    capabilities = device_handlers.capabilities_for(_device(device_type))
    assert capabilities.is_camera
    assert capabilities.is_phod


@pytest.mark.parametrize(
    "device_type", ["motion_cam", "motion_cam_outdoor", "motion_cam_fibra", "motion_cam_g3"]
)
def test_non_phod_motion_camera_has_no_phod_capability(device_type: str) -> None:
    assert not device_handlers.capabilities_for(_device(device_type)).is_phod


@pytest.mark.parametrize("device_type", ["water_stop", "water_stop_base"])
def test_valve_capability_is_registered(device_type: str) -> None:
    assert device_handlers.capabilities_for(_device(device_type)).is_valve


@pytest.mark.parametrize("device_type", ["life_quality", "rex_2", "leak_protect"])
def test_non_valve_has_no_valve_capability(device_type: str) -> None:
    assert not device_handlers.capabilities_for(_device(device_type)).is_valve


def test_light_capability_is_registered() -> None:
    assert device_handlers.capabilities_for(_device("light_switch_dimmer")).is_light


@pytest.mark.parametrize("device_type", ["door_protect", "keypad_combi", "home_siren"])
def test_other_registered_family_has_no_light_capability(device_type: str) -> None:
    assert not device_handlers.capabilities_for(_device(device_type)).is_light


@pytest.mark.parametrize("device_type", ["motion_cam_video_doorbell", "video_edge_doorbell"])
def test_doorbell_capability_is_registered(device_type: str) -> None:
    assert device_handlers.capabilities_for(_device(device_type)).is_doorbell


@pytest.mark.parametrize(
    "device_type", ["motion_cam_video_indoor", "video_edge_indoor", "motion_cam"]
)
def test_non_doorbell_camera_has_no_doorbell_capability(device_type: str) -> None:
    """The doorbells were split out of larger camera groups; the rest must not move."""
    assert not device_handlers.capabilities_for(_device(device_type)).is_doorbell


def test_button_press_capability_is_registered() -> None:
    assert device_handlers.capabilities_for(_device("button")).is_button_press


@pytest.mark.parametrize("device_type", ["door_protect", "keypad_combi"])
def test_other_registered_family_has_no_button_press_capability(device_type: str) -> None:
    assert not device_handlers.capabilities_for(_device(device_type)).is_button_press


def test_unmapped_family_has_none_of_the_new_capabilities() -> None:
    """Keyfobs, relays and sockets are still unmapped and must stay capability-free."""
    capabilities = device_handlers.capabilities_for(_device("unmapped_unknown_device"))
    assert not capabilities.is_light
    assert not capabilities.is_valve
    assert not capabilities.is_doorbell
    assert not capabilities.is_button_press
    assert not capabilities.has_siren_settings


@pytest.mark.parametrize("device_type", ["home_siren", "street_siren_double_deck_fibra"])
def test_siren_settings_capability_is_registered(device_type: str) -> None:
    assert device_handlers.capabilities_for(_device(device_type)).has_siren_settings


def test_street_siren_plus_has_no_siren_settings() -> None:
    """It is a siren, but its oneof case is missing from the HubDevice proto.

    Its settings are unreadable, so `number` / `select` would sit permanently
    empty — which is why it has no siren-settings capability. Its binary
    sensors are unaffected.
    """
    capabilities = device_handlers.capabilities_for(_device("street_siren_plus"))
    assert not capabilities.has_siren_settings
    assert capabilities.binary_sensor_keys == ("tamper",)


@pytest.mark.parametrize("device_type", ["light_switch_dimmer", "button"])
def test_newly_registered_family_keeps_the_unmapped_binary_sensors(device_type: str) -> None:
    """These two were unmapped before (#332 PR-4), so they fell through to the
    tamper-only default. The registration exists only to carry a capability;
    the binary sensors must be exactly what the default handler produced.
    """
    default = device_handlers.DefaultDeviceHandler().capabilities(_device(device_type))
    registered = device_handlers.capabilities_for(_device(device_type))
    assert registered.binary_sensor_keys == default.binary_sensor_keys == ("tamper",)


class TestHtsDerivedCapabilities:
    """#332 PR-5: the sensor platform's three gates, derived from the HTS tables.

    They are not declared in `_HANDLERS` on purpose — `api/hts/hub_state.py`
    already decides which families have electrical readings and which have no
    gRPC temperature — so what these tests pin is that the derivation happens
    and that it adds nothing else.
    """

    @pytest.mark.parametrize(
        "device_type",
        ["wall_switch", "relay", "socket", "socket_outlet_type_e", "socket_outlet_type_f"],
    )
    def test_electrical_family_has_electrical_readings(self, device_type: str) -> None:
        assert device_handlers.capabilities_for(_device(device_type)).has_electrical_readings

    @pytest.mark.parametrize("device_type", ["socket_outlet_type_e", "socket_outlet_type_f"])
    def test_outlet_reports_power_directly(self, device_type: str) -> None:
        assert device_handlers.capabilities_for(_device(device_type)).has_direct_power

    @pytest.mark.parametrize("device_type", ["wall_switch", "relay", "socket"])
    def test_wallswitch_family_power_is_derived_not_direct(self, device_type: str) -> None:
        capabilities = device_handlers.capabilities_for(_device(device_type))
        assert capabilities.has_electrical_readings
        assert not capabilities.has_direct_power

    def test_direct_power_is_a_subset_of_electrical(self) -> None:
        """`capabilities_for` only reaches `has_direct_power` inside the electrical branch."""
        from custom_components.aegis_ajax.api.hts.hub_state import (
            DIRECT_POWER_DEVICE_TYPES,
            ELECTRICAL_DEVICE_TYPES,
        )

        assert DIRECT_POWER_DEVICE_TYPES <= ELECTRICAL_DEVICE_TYPES

    @pytest.mark.parametrize(
        "device_type",
        ["street_siren", "motion_protect_outdoor", "motion_protect_curtain_outdoor_plus"],
    )
    def test_hts_temperature_family_is_flagged(self, device_type: str) -> None:
        assert device_handlers.capabilities_for(_device(device_type)).has_hts_temperature

    @pytest.mark.parametrize("device_type", ["door_protect", "keypad_combi", "motion_protect"])
    def test_grpc_temperature_family_is_not_flagged(self, device_type: str) -> None:
        """Families that already get temperature over gRPC must not gain the HTS gate."""
        assert not device_handlers.capabilities_for(_device(device_type)).has_hts_temperature

    def test_derivation_matches_the_hts_tables_exactly(self) -> None:
        from custom_components.aegis_ajax.api.hts.hub_state import (
            DIRECT_POWER_DEVICE_TYPES,
            ELECTRICAL_DEVICE_TYPES,
            HTS_TEMPERATURE_DEVICE_TYPES,
        )

        candidates = (
            set(device_handlers._DEVICE_HANDLERS)
            | set(ELECTRICAL_DEVICE_TYPES)
            | set(HTS_TEMPERATURE_DEVICE_TYPES)
        )
        electrical = set()
        direct = set()
        hts_temperature = set()
        for device_type in candidates:
            capabilities = device_handlers.capabilities_for(_device(device_type))
            if capabilities.has_electrical_readings:
                electrical.add(device_type)
            if capabilities.has_direct_power:
                direct.add(device_type)
            if capabilities.has_hts_temperature:
                hts_temperature.add(device_type)
        assert electrical == set(ELECTRICAL_DEVICE_TYPES)
        assert direct == set(DIRECT_POWER_DEVICE_TYPES)
        assert hts_temperature == set(HTS_TEMPERATURE_DEVICE_TYPES)

    @pytest.mark.parametrize(
        "device_type", ["wall_switch", "socket_outlet_type_e", "street_siren", "door_protect"]
    )
    def test_derivation_does_not_disturb_the_declared_capabilities(self, device_type: str) -> None:
        """The derived flags are added on top; nothing declared may change."""
        declared = device_handlers.get_device_handler(device_type).capabilities(
            _device(device_type)
        )
        derived = device_handlers.capabilities_for(_device(device_type))
        assert derived.binary_sensor_keys == declared.binary_sensor_keys
        assert derived.is_lock == declared.is_lock
        assert derived.is_camera == declared.is_camera
        assert derived.is_phod == declared.is_phod
        assert derived.is_light == declared.is_light
        assert derived.is_valve == declared.is_valve
        assert derived.is_doorbell == declared.is_doorbell
        assert derived.is_button_press == declared.is_button_press
        assert derived.has_siren_settings == declared.has_siren_settings
