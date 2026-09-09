"""Per-device-family capability handlers for Ajax Security."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from custom_components.aegis_ajax.api.models import Device


@dataclass(frozen=True)
class DeviceCapabilities:
    """Capabilities provided by a device family."""

    binary_sensor_keys: tuple[str, ...] = ()
    is_lock: bool = False
    is_camera: bool = False
    is_phod: bool = False
    is_light: bool = False
    is_valve: bool = False
    is_doorbell: bool = False
    is_button_press: bool = False
    has_siren_settings: bool = False


class DeviceHandler(Protocol):
    """Protocol for per-device-type capability handlers."""

    device_types: frozenset[str]

    def capabilities(self, device: Device) -> DeviceCapabilities:
        """Return capabilities for the given device."""
        ...


class StaticDeviceHandler:
    """Handler returning static capabilities for a set of device types."""

    def __init__(
        self,
        device_types: tuple[str, ...],
        binary_sensor_keys: tuple[str, ...],
        *,
        is_lock: bool = False,
        is_camera: bool = False,
        is_phod: bool = False,
        is_light: bool = False,
        is_valve: bool = False,
        is_doorbell: bool = False,
        is_button_press: bool = False,
        has_siren_settings: bool = False,
    ) -> None:
        self.device_types = frozenset(device_types)
        self._capabilities = DeviceCapabilities(
            binary_sensor_keys=binary_sensor_keys,
            is_lock=is_lock,
            is_camera=is_camera,
            is_phod=is_phod,
            is_light=is_light,
            is_valve=is_valve,
            is_doorbell=is_doorbell,
            is_button_press=is_button_press,
            has_siren_settings=has_siren_settings,
        )

    def capabilities(self, device: Device) -> DeviceCapabilities:
        """Return static capabilities for this device family."""
        return self._capabilities


_DEFAULT_CAPABILITIES = DeviceCapabilities(binary_sensor_keys=("tamper",))


class DefaultDeviceHandler:
    """Fallback handler for unmapped device types (#434)."""

    device_types: frozenset[str] = frozenset()

    def capabilities(self, device: Device) -> DeviceCapabilities:
        """Return fallback capabilities (tamper only)."""
        return _DEFAULT_CAPABILITIES


_HANDLERS: tuple[DeviceHandler, ...] = (
    # DoorProtect
    StaticDeviceHandler(
        ("door_protect", "door_protect_fibra", "door_protect_s", "door_protect_g3"),
        (
            "door_opened",
            "tamper",
            "external_contact_broken",
            "external_contact_alert",
            "delay_when_leaving",
        ),
    ),
    # DoorProtect Plus
    StaticDeviceHandler(
        (
            "door_protect_plus",
            "door_protect_s_plus",
            "door_protect_plus_fibra",
            "door_protect_plus_g3_fibra",
        ),
        (
            "door_opened",
            "tamper",
            "vibration",
            "tilt",
            "external_contact_broken",
            "external_contact_alert",
            "delay_when_leaving",
        ),
    ),
    # MotionProtect
    StaticDeviceHandler(
        (
            "motion_protect",
            "motion_protect_plus",
            "motion_protect_fibra",
            "motion_protect_plus_fibra",
            "motion_protect_outdoor",
            "motion_protect_curtain",
            "motion_protect_curtain_base",
            "motion_protect_curtain_outdoor_base",
            "motion_protect_curtain_outdoor_mini",
            "motion_protect_curtain_outdoor_plus",
            "dual_curtain_outdoor",
            "motion_protect_g3",
            "motion_protect_g3_fibra",
            "motion_protect_g3_fibra_new",
            "motion_protect_plus_g3",
            "motion_protect_s",
            "motion_protect_s_plus",
        ),
        ("motion_detected", "tamper", "delay_when_leaving"),
    ),
    # MotionCam
    StaticDeviceHandler(
        ("motion_cam", "motion_cam_outdoor", "motion_cam_fibra"),
        ("motion_detected", "tamper", "delay_when_leaving"),
        is_camera=True,
    ),
    StaticDeviceHandler(
        ("motion_cam_phod", "motion_cam_outdoor_phod", "motion_cam_fibra_base"),
        ("motion_detected", "tamper", "delay_when_leaving"),
        is_camera=True,
        is_phod=True,
    ),
    StaticDeviceHandler(
        (
            "motion_cam_g3",
            "motion_cam_hd",
            "motion_cam_phod_fibra",
            "motion_cam_outdoor_two_four_phod",
            "motion_cam_s_phod",
            "motion_cam_s_phod_am",
            "motion_cam_superior_phod",
            "motion_cam_video_base",
            "motion_cam_video_indoor",
        ),
        ("motion_detected", "tamper", "delay_when_leaving"),
    ),
    # Split out of the group above only to carry `is_doorbell`; the binary
    # sensors are identical.
    StaticDeviceHandler(
        ("motion_cam_video_doorbell",),
        ("motion_detected", "tamper", "delay_when_leaving"),
        is_doorbell=True,
    ),
    # VideoEdge
    StaticDeviceHandler(
        (
            "video_edge_bullet",
            "video_edge_indoor",
            "video_edge_minidome",
            "video_edge_turret",
            "video_edge_unknown",
        ),
        ("motion_detected", "tamper"),
    ),
    # Split out of the group above only to carry `is_doorbell`; the binary
    # sensors are identical.
    StaticDeviceHandler(
        ("video_edge_doorbell",),
        ("motion_detected", "tamper"),
        is_doorbell=True,
    ),
    # CombiProtect
    StaticDeviceHandler(
        ("combi_protect", "combi_protect_s", "combi_protect_fibra"),
        ("motion_detected", "glass_break", "tamper", "delay_when_leaving"),
    ),
    # GlassProtect
    StaticDeviceHandler(
        ("glass_protect", "glass_protect_s", "glass_protect_fibra"),
        ("glass_break", "tamper"),
    ),
    # FireProtect legacy
    StaticDeviceHandler(
        ("fire_protect",),
        ("smoke_detected", "high_temperature", "tamper"),
    ),
    StaticDeviceHandler(
        ("fire_protect_plus",),
        ("smoke_detected", "co_detected", "high_temperature", "tamper"),
    ),
    # FireProtect 2
    StaticDeviceHandler(
        (
            "fire_protect_2",
            "fire_protect_two",
            "fire_protect_two_hs_ac",
            "fire_protect_two_hs_ac_ul",
            "fire_protect_two_hs_rb_ul",
            "fire_protect_two_hs_sb_ul",
        ),
        ("smoke_detected", "steam", "high_temperature", "tamper"),
    ),
    StaticDeviceHandler(
        (
            "fire_protect_two_base",
            "fire_protect_two_plus",
            "fire_protect_two_plus_sb",
            "fire_protect_two_sb",
            "fire_protect_two_hcrb",
            "fire_protect_two_hcsb",
            "fire_protect_two_hsc_ac",
            "fire_protect_two_hsc_ac_ul",
            "fire_protect_two_hsc_rb_ul",
            "fire_protect_two_hsc_sb_ul",
        ),
        ("smoke_detected", "steam", "co_detected", "high_temperature", "tamper"),
    ),
    StaticDeviceHandler(
        (
            "fire_protect_two_hrb",
            "fire_protect_two_hsb",
            "fire_protect_two_h_ac",
            "fire_protect_two_h_rb_ul",
        ),
        ("high_temperature", "tamper"),
    ),
    StaticDeviceHandler(
        (
            "fire_protect_two_crb",
            "fire_protect_two_csb",
            "fire_protect_two_c_ac",
            "fire_protect_two_c_rb_ul",
        ),
        ("co_detected", "tamper"),
    ),
    StaticDeviceHandler(
        ("fire_protect_two_hc_ac",),
        ("co_detected", "high_temperature", "tamper"),
    ),
    # LeakProtect
    StaticDeviceHandler(
        ("leak_protect",),
        ("leak_detected", "tamper"),
    ),
    # Sirens
    StaticDeviceHandler(
        (
            "home_siren",
            "home_siren_s",
            "home_siren_fibra",
            "home_siren_g3",
            "street_siren",
            "street_siren_fibra",
            "street_siren_plus_fibra",
            "street_siren_plus_g3",
            "street_siren_s",
            "street_siren_double_deck",
            "street_siren_s_double_deck",
            "street_siren_double_deck_fibra",
        ),
        ("tamper",),
        has_siren_settings=True,
    ),
    # `street_siren_plus` is a siren, but its oneof case is missing from the
    # HubDevice proto, so its settings are unreadable and `number` / `select`
    # would sit permanently empty. Same binary sensors, no settings entities —
    # see SIREN_DEVICE_TYPES in const.py.
    StaticDeviceHandler(
        ("street_siren_plus",),
        ("tamper",),
    ),
    # ReX / ReX 2 / LifeQuality / WaterStop — explicit no-capability handlers (#332)
    StaticDeviceHandler(
        (
            "rex",
            "rex_2",
            "range_extender",
            "range_extender_2",
            "life_quality",
            "life_quality_plus",
        ),
        (),
    ),
    # WaterStop — no binary sensors, one valve entity. Ajax ships two buckets,
    # `water_stop` (Jeweller, wireless) and `water_stop_base` (Fibra, wired):
    # same `WaterStopChannel` payload, same parser path, same entity surface.
    StaticDeviceHandler(
        ("water_stop", "water_stop_base"),
        (),
        is_valve=True,
    ),
    StaticDeviceHandler(
        ("range_extender_2_fire",),
        ("smoke_detected", "high_temperature", "tamper"),
    ),
    # SmartLock / LockBridge
    StaticDeviceHandler(
        ("smart_lock", "smart_lock_yale"),
        ("tamper",),
        is_lock=True,
    ),
    # Wired inputs
    StaticDeviceHandler(
        ("transmitter", "wire_input", "wire_input_rs"),
        ("tamper", "wire_input_alert"),
    ),
    StaticDeviceHandler(
        ("multi_transmitter", "multi_transmitter_fibra"),
        ("tamper",),
    ),
    StaticDeviceHandler(
        ("wire_input_mt",),
        ("tamper", "wire_input_alert", "external_contact_open"),
    ),
    # LightSwitch dimmer — previously unmapped, so it fell through to the
    # tamper-only default. Registered with that same tamper-only set so the
    # binary sensors do not move; the registration exists to carry `is_light`.
    StaticDeviceHandler(
        ("light_switch_dimmer",),
        ("tamper",),
        is_light=True,
    ),
    # Button — previously unmapped, same tamper-only default preserved; the
    # registration exists to carry `is_button_press`.
    StaticDeviceHandler(
        ("button",),
        ("tamper",),
        is_button_press=True,
    ),
    # Keypads
    StaticDeviceHandler(
        (
            "keypad_combi",
            "keypad_plus",
            "keypad_plus_g3",
            "keypad_s_plus",
            "keypad_outdoor",
            "keypad_outdoor_fibra",
            "keypad_touchscreen",
            "keypad_touchscreen_fibra",
            "keypad_touchscreen_g3",
        ),
        ("tamper",),
    ),
    # Hubs
    StaticDeviceHandler(
        (
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
        ),
        ("gsm_connected", "lid_opened"),
    ),
)


def _build_handler_map() -> dict[str, DeviceHandler]:
    handler_map: dict[str, DeviceHandler] = {}
    for handler in _HANDLERS:
        for device_type in handler.device_types:
            if device_type in handler_map:
                msg = f"Duplicate device handler registration for {device_type!r}"
                raise ValueError(msg)
            handler_map[device_type] = handler
    return handler_map


_DEVICE_HANDLERS: dict[str, DeviceHandler] = _build_handler_map()
_DEFAULT_HANDLER: DeviceHandler = DefaultDeviceHandler()


def get_device_handler(device_type: str) -> DeviceHandler:
    """Return the handler registered for device_type, falling back to DefaultDeviceHandler."""
    return _DEVICE_HANDLERS.get(device_type, _DEFAULT_HANDLER)


def capabilities_for(device: Device) -> DeviceCapabilities:
    """Return device capabilities for the given device."""
    return get_device_handler(device.device_type).capabilities(device)
