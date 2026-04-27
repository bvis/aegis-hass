"""Constants for the Ajax Security integration."""

from enum import IntEnum, StrEnum

DOMAIN = "aegis_ajax"
MANUFACTURER = "Ajax Systems"

# Labels for automatic entity categorization
LABEL_PREFIX = "aegis"
LABELS: dict[str, dict[str, str]] = {
    "aegis_door": {
        "name": "Aegis: Doors & Windows",
        "icon": "mdi:door",
        "color": "#1E88E5",
    },
    "aegis_motion": {
        "name": "Aegis: Motion",
        "icon": "mdi:motion-sensor",
        "color": "#FB8C00",
    },
    "aegis_camera": {
        "name": "Aegis: Cameras",
        "icon": "mdi:cctv",
        "color": "#8E24AA",
    },
    "aegis_battery": {
        "name": "Aegis: Batteries",
        "icon": "mdi:battery",
        "color": "#43A047",
    },
    "aegis_temperature": {
        "name": "Aegis: Temperature",
        "icon": "mdi:thermometer",
        "color": "#E53935",
    },
    "aegis_tamper": {
        "name": "Aegis: Tamper",
        "icon": "mdi:shield-alert",
        "color": "#D81B60",
    },
    "aegis_connectivity": {
        "name": "Aegis: Connectivity",
        "icon": "mdi:access-point-network",
        "color": "#00ACC1",
    },
    "aegis_hub": {
        "name": "Aegis: Hub",
        "icon": "mdi:server-network",
        "color": "#546E7A",
    },
    "aegis_alarm": {
        "name": "Aegis: Alarm",
        "icon": "mdi:shield-home",
        "color": "#C62828",
    },
}

GRPC_HOST = "mobile-gw.prod.ajax.systems"
GRPC_PORT = 443

CLIENT_OS = "Android"
CLIENT_VERSION = "3.30"
APPLICATION_LABEL = "Ajax"  # default (main Ajax app labelName)
KNOWN_APP_LABELS = [
    "Ajax",
    "AIKO",
    "3dAlarma",
    "E-Pro",
    "esahome",
    "G4S_SHIELDalarm",
    "GSS_Home",
    "HomeSecure",
    "Hus_Smart",
    "Novus_alarm",
    "Protegim_alarma",
    "SecureAjax",
    "Smart_Secure",
    "Verux",
    "Videotech_alarm",
    "kale_alarm_x",
    "ADT_Alarm",
    "ADT_Secure",
    "Yoigo_ADT_Alarma",
    "Masmovil_ADT_Alarma",
    "Euskaltel_ADT_Alarma",
    "Elotec",
    "Yavir",
    "Oryggi",
    "acacio",
    "Protecta",
    "ajax_pro",
]
CLIENT_DEVICE_MODEL = "SM-A536B"  # Generic Android model (Samsung Galaxy A53)
CLIENT_DEVICE_TYPE = "MOBILE"
CLIENT_APP_TYPE = "USER"

# Firebase/FCM config keys — credentials provided by user in options flow
CONF_FCM_PROJECT_ID = "fcm_project_id"
CONF_FCM_APP_ID = "fcm_app_id"
CONF_FCM_API_KEY = "fcm_api_key"
CONF_FCM_SENDER_ID = "fcm_sender_id"

SESSION_REFRESH_INTERVAL = 780  # 13 minutes in seconds
STREAM_RECONNECT_MAX_BACKOFF = 60  # seconds
MIN_POLL_INTERVAL = 60  # seconds
MAX_POLL_INTERVAL = 300  # seconds
DEFAULT_POLL_INTERVAL = 300  # seconds fallback (stream handles real-time updates)
GRPC_TIMEOUT = 10.0  # seconds
GRPC_STREAM_TIMEOUT = 30.0  # seconds
MAX_RETRIES = 3
RATE_LIMIT_REQUESTS = 60
RATE_LIMIT_WINDOW = 60  # seconds


class SecurityState(IntEnum):
    """Maps DisplayedSpaceSecurityState proto enum."""

    NONE = 0
    ARMED = 1
    DISARMED = 2
    NIGHT_MODE = 3
    PARTIALLY_ARMED = 4
    AWAITING_EXIT_TIMER = 5
    AWAITING_SECOND_STAGE = 6
    TWO_STAGE_INCOMPLETE = 7
    AWAITING_VDS = 8


class ConnectionStatus(IntEnum):
    """Maps mobile v2 ConnectionStatus proto enum."""

    UNSPECIFIED = 0
    ONLINE = 1
    OFFLINE = 2


class UserRole(IntEnum):
    """Maps UserRole proto enum."""

    UNSPECIFIED = 0
    USER = 1
    PRO = 2


class DeviceState(StrEnum):
    """Simplified device states from LightDeviceState."""

    ONLINE = "online"
    OFFLINE = "offline"
    LOCKED = "locked"
    SUSPENDED = "suspended"
    UPDATING = "updating"
    BATTERY_SAVING = "battery_saving"
    WALK_TEST = "walk_test"
    ADDING = "adding"
    NOT_MIGRATED = "not_migrated"
    UNKNOWN = "unknown"


CONF_FORCE_ARM = "force_arm"
CONF_PHOTO_RETENTION_DAYS = "photo_retention_days"
CONF_PHOTO_MAX_PER_DEVICE = "photo_max_per_device"
CONF_AUTO_CREATE_LABELS = "auto_create_labels"
DEFAULT_PHOTO_RETENTION_DAYS = 30
DEFAULT_PHOTO_MAX_PER_DEVICE = 100
DEFAULT_AUTO_CREATE_LABELS = True

EVENT_DOMAIN = f"{DOMAIN}_event"

# Map HubEventTag oneof field names to the resulting space `security_state`
# when the corresponding push event arrives. Used to refresh the alarm panel
# instantly from the FCM payload instead of waiting for the next poll.
# `group_*` tags are intentionally omitted — they only affect a subgroup, so
# the resulting space-level state (PARTIALLY_ARMED, ARMED, …) depends on the
# other groups; let the next poll resolve it.
RAW_TAG_TO_SECURITY_STATE: dict[str, SecurityState] = {
    "arm": SecurityState.ARMED,
    "arm_attempt": SecurityState.ARMED,
    "arm_with_malfunctions": SecurityState.ARMED,
    "disarm": SecurityState.DISARMED,
    "duress_disarm": SecurityState.DISARMED,
    "night_mode_on": SecurityState.NIGHT_MODE,
    "night_mode_off": SecurityState.DISARMED,
    "duress_night_mode_off": SecurityState.DISARMED,
}


# Map HubEventTag oneof field names to simplified HA event types
HUB_EVENT_TAG_MAP: dict[str, str] = {
    # Arming
    "arm": "arm",
    "arm_attempt": "arm",
    "arm_with_malfunctions": "arm",
    "group_arm": "arm",
    "group_arm_with_malfunctions": "arm",
    # Disarming
    "disarm": "disarm",
    "duress_disarm": "disarm",
    "group_disarm": "disarm",
    # Night mode
    "night_mode_on": "arm_night",
    "night_mode_off": "disarm_night",
    "duress_night_mode_off": "disarm_night",
    # Alarms
    "intrusion_alarm": "alarm",
    "intrusion_alarm_confirmed": "alarm",
    # Tamper
    "tamper_opened": "tamper",
    "front_tamper_opened": "tamper",
    "back_tamper_opened": "tamper",
    # Panic
    "panic_button_pressed": "panic",
    # Battery
    "battery_low": "battery_low",
    # Connection
    "device_communication_loss": "connection_lost",
    "server_connection_loss": "connection_lost",
    "gsm_connection_loss": "connection_lost",
    "ethernet_connection_loss": "connection_lost",
    # Malfunction
    "malfunction": "malfunction",
    # Fire/smoke
    "smoke_detected": "fire",
    # CO
    "high_co_level_detected": "co_alarm",
    # Water
    "leak_detected": "flood",
    # Glass
    "glass_break_detected": "glass_break",
    # Motion
    "motion_detected": "motion",
    # Door
    "door_opened": "door_open",
}

ALL_EVENT_TYPES: list[str] = sorted(set(HUB_EVENT_TAG_MAP.values()))
