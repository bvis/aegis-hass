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
    ],
)
def test_camera_capability_is_registered(device_type: str) -> None:
    assert device_handlers.capabilities_for(_device(device_type)).is_camera


@pytest.mark.parametrize(
    "device_type",
    ["motion_cam_phod", "motion_cam_outdoor_phod", "motion_cam_fibra_base"],
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
    empty — which is why it is excluded from `SIREN_DEVICE_TYPES` and must stay
    excluded here. Its binary sensors are unaffected.
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


class TestCapabilityParityWithConstSets:
    """`coordinator` and `notification` still gate on the `const.py` sets.

    Until those move too, the registry and the sets are two sources of truth
    for the same families, so pin them against each other: adding a family to
    one and not the other is the drift this catches.
    """

    @staticmethod
    def _types_with(capability: str) -> set[str]:
        return {
            device_type
            for device_type, handler in device_handlers._DEVICE_HANDLERS.items()
            if getattr(handler.capabilities(_device(device_type)), capability)
        }

    def test_siren_settings_matches_siren_device_types(self) -> None:
        from custom_components.aegis_ajax.const import SIREN_DEVICE_TYPES

        assert self._types_with("has_siren_settings") == set(SIREN_DEVICE_TYPES)

    def test_doorbell_matches_doorbell_device_types(self) -> None:
        from custom_components.aegis_ajax.const import DOORBELL_DEVICE_TYPES

        assert self._types_with("is_doorbell") == set(DOORBELL_DEVICE_TYPES)

    def test_button_press_matches_button_press_device_types(self) -> None:
        from custom_components.aegis_ajax.const import BUTTON_PRESS_DEVICE_TYPES

        assert self._types_with("is_button_press") == set(BUTTON_PRESS_DEVICE_TYPES)
