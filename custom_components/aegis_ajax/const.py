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
# Pin to "3.30" — the Ajax server gates parts of the snapshot response on
# `client-version-major`. Reporting a newer version (e.g. "3.46") causes the
# server to omit `monitoring_companies` from `SpaceService.stream`, leaving
# the CRA-company diagnostic sensor empty. Pinning to "3.30" restores the
# legacy response shape. Bump only after verifying the modern endpoint that
# replaces this data path.
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
# Maps `app_label` (the gRPC `application-label` header value the user
# picked in the config flow) to the Android package id of the
# corresponding co-branded APK. Used as the `X-Android-Package` header
# the integration sends on Firebase Installations calls so Google's
# api-key package restriction doesn't refuse the request with
# `API_KEY_ANDROID_APP_BLOCKED` + `androidPackage: <empty>` (#155,
# #182). The `firebase_messaging` library doesn't send that header
# itself — for api-keys without restriction it works fine, but the
# Ajax co-branded key on Project B (`elite-dreamer-676`,
# `mws-mobile-client---2` for some variants) has package restriction
# enabled and blocks blank-package requests outright.
#
# Verified end-to-end: only "Ajax" → `com.ajaxsystems` (zwagerzaken's
# beta.8 capture in #182, alt-BadBatch's original). The other entries
# are best-effort derived from the libnative-lib.so catalogue
# documented in the cobranded-firebase project memory. Add an entry
# only after confirming with the user that their `strings.xml` is in
# the actual APK named here. Missing entries fall back to no header,
# i.e. the pre-1.5.3-beta.10 behaviour.
APP_LABEL_TO_ANDROID_PACKAGE: dict[str, str] = {
    "Ajax": "com.ajaxsystems",
    "ajax_pro": "com.ajaxsystems.pro",
    "AIKO": "com.ajaxsystems.aiko",
    "Protegim_alarma": "com.ajaxsystems.protegim",
}

CLIENT_DEVICE_MODEL = "SM-A536B"  # Galaxy A53 — paired with CLIENT_VERSION="3.30"
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
# `space_group_*` tags are intentionally omitted from this map — they only
# affect a single subgroup, so the resulting *space-level* state (PARTIALLY_
# ARMED, ARMED, …) depends on the other groups; let the next poll resolve
# space-level. The same tags are mapped per-group in
# `RAW_TAG_TO_GROUP_SECURITY_STATE` below for the group-level panels (#148).
RAW_TAG_TO_SECURITY_STATE: dict[str, SecurityState] = {
    # HubEventTag tags (sub-incidents that imply a space-level transition)
    "arm": SecurityState.ARMED,
    "arm_attempt": SecurityState.ARMED,
    "arm_with_malfunctions": SecurityState.ARMED,
    "disarm": SecurityState.DISARMED,
    "duress_disarm": SecurityState.DISARMED,
    "night_mode_on": SecurityState.NIGHT_MODE,
    "night_mode_off": SecurityState.DISARMED,
    "duress_night_mode_off": SecurityState.DISARMED,
    # SpaceEventTag tags (the actual primary arm/disarm push from co-brand
    # FCM payloads — see #68; HubEventQualifier candidates in the same payload
    # only carry secondary zone-incident info such as `ext_contact_opened`).
    "space_armed": SecurityState.ARMED,
    "space_armed_with_malfunctions": SecurityState.ARMED,
    "space_auto_armed": SecurityState.ARMED,
    "space_auto_armed_with_malfunctions": SecurityState.ARMED,
    "space_disarmed": SecurityState.DISARMED,
    "space_auto_disarmed": SecurityState.DISARMED,
    "space_duress_disarmed": SecurityState.DISARMED,
    "space_night_mode_on": SecurityState.NIGHT_MODE,
    "space_night_mode_on_with_malfunctions": SecurityState.NIGHT_MODE,
    "space_night_mode_off": SecurityState.DISARMED,
    "space_duress_night_mode_off": SecurityState.DISARMED,
}


# Map `space_group_*` SpaceEventTag oneof field names to the resulting
# *group-level* `security_state`. Used to refresh the per-group alarm panel
# (`AjaxGroupAlarmControlPanel`) instantly from the FCM payload instead of
# waiting for the next poll (#148). The group_id comes from the
# `SpaceNotificationSource` wrapping the qualifier — see
# `notification.py::_extract_space_source_info`. Night-mode tags are absent
# because per-group panels intentionally don't expose night mode (the
# underlying flag is space-wide on Ajax).
RAW_TAG_TO_GROUP_SECURITY_STATE: dict[str, SecurityState] = {
    "space_group_armed": SecurityState.ARMED,
    "space_group_armed_with_malfunctions": SecurityState.ARMED,
    "space_group_auto_armed": SecurityState.ARMED,
    "space_group_auto_armed_with_malfunctions": SecurityState.ARMED,
    "space_group_disarmed": SecurityState.DISARMED,
    "space_group_auto_disarmed": SecurityState.DISARMED,
    "space_group_duress_disarmed": SecurityState.DISARMED,
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
    # Doorbell — Wireless DoorBell SKU (#119). The MotionCam Video
    # Doorbell fires its own ring through `VideoEventQualifier` instead;
    # see `VIDEO_EVENT_TAG_MAP` below.
    "ring_button_pressed": "doorbell_pressed",
}

# Map SpaceEventTag oneof field names to simplified HA event types. Used in
# parallel to HUB_EVENT_TAG_MAP because arm/disarm pushes carry a
# SpaceEventQualifier (in SpaceNotificationContent.qualifier), not a
# HubEventQualifier — the former is what we need for #68 to fire.
# Group-level events (`space_group_*`) also map to `arm` / `disarm` so the
# logbook / event entity render the same wording as space-wide events; the
# `raw_tag` field on the event payload still distinguishes them, and the
# per-group alarm panel is updated from `RAW_TAG_TO_GROUP_SECURITY_STATE`
# (#148).
SPACE_EVENT_TAG_MAP: dict[str, str] = {
    "space_armed": "arm",
    "space_armed_with_malfunctions": "arm",
    "space_auto_armed": "arm",
    "space_auto_armed_with_malfunctions": "arm",
    "space_disarmed": "disarm",
    "space_auto_disarmed": "disarm",
    "space_duress_disarmed": "disarm",
    "space_night_mode_on": "arm_night",
    "space_night_mode_on_with_malfunctions": "arm_night",
    "space_night_mode_off": "disarm_night",
    "space_duress_night_mode_off": "disarm_night",
    "space_panic_button_pressed": "panic",
    # Group-level arm/disarm (#148). Same downstream event_type as space-wide
    # so existing automations keep working; `raw_tag` differentiates.
    "space_group_armed": "arm",
    "space_group_armed_with_malfunctions": "arm",
    "space_group_auto_armed": "arm",
    "space_group_auto_armed_with_malfunctions": "arm",
    "space_group_disarmed": "disarm",
    "space_group_auto_disarmed": "disarm",
    "space_group_duress_disarmed": "disarm",
}

# Map VideoEventTag oneof field names to simplified HA event types. The
# MotionCam Video Doorbell (and any other Ajax video device) fires its
# events through `VideoEventQualifier` — distinct from the hub-level
# `HubEventQualifier` we already parse. Pass 4 of
# `_extract_event_with_compiled_protos` walks this. Only the events that
# have a HA-meaningful destination are mapped; the long tail of
# storage/temporary-access/firmware-update tags from `VideoEventTag` is
# intentionally left unmapped (we'd just be inflating `ALL_EVENT_TYPES`
# for events nobody automates on). Add more entries here when a real
# automation use case shows up.
VIDEO_EVENT_TAG_MAP: dict[str, str] = {
    "ring_button_pressed": "doorbell_pressed",
    "motion_detected": "motion",
    "human_detected": "motion",
}

# Map SmartLockEventTag oneof field names to simplified HA event types. Ajax
# SmartLock / LockBridge (Yale) variants with an integrated ring button fire
# the press through `SmartLockEventQualifier` — disjoint from
# `HubEventQualifier` and `VideoEventQualifier`. Pass 4 of
# `_extract_event_with_compiled_protos` walks this. The rest of the SmartLock
# tag vocabulary (locked_by_keypad, locked_automatically, …) is intentionally
# unmapped here: those transitions already surface via the `lock` entity's
# state, mirroring how `VIDEO_EVENT_TAG_MAP` only mirrors the events with a
# distinct automation hook.
SMARTLOCK_EVENT_TAG_MAP: dict[str, str] = {
    "doorbell_pressed": "doorbell_pressed",
}


# Semantic weight of each push tag. Used by the parser to pick the right
# match when a single FCM payload carries multiple valid qualifiers — most
# notably "sensor tripped while system was armed", where Ajax bundles a
# `SpaceEventQualifier` carrying the state context (e.g. `space_night_mode_
# on`) together with a `HubEventQualifier` carrying the sensor activity
# (e.g. `motion_detected`). Higher number wins; tags absent from this map
# default to weight 0, which preserves the previous "first match wins"
# fallback for anything not yet ranked.
#
# Tiers:
#   100 — confirmed incident: someone needs to act now
#    90 — critical detector: would normally trigger an alarm
#    80 — sensor activity worth surfacing (motion, doorbell, door open)
#    50 — space-level state change driven by a user action
#    40 — HubEventTag legacy arm/disarm — Ajax mostly sends these as
#         secondary context to the SpaceEventTag equivalents
#    30 — informational health signals (battery, connection loss)
TAG_PRIORITY: dict[str, int] = {
    # Tier 100 — confirmed incidents
    "intrusion_alarm": 100,
    "intrusion_alarm_confirmed": 100,
    "panic_button_pressed": 100,
    "space_panic_button_pressed": 100,
    # Tier 90 — critical detection
    "tamper_opened": 90,
    "front_tamper_opened": 90,
    "back_tamper_opened": 90,
    "smoke_detected": 90,
    "high_co_level_detected": 90,
    "leak_detected": 90,
    "glass_break_detected": 90,
    # Tier 80 — sensor / device activity
    "motion_detected": 80,
    "human_detected": 80,
    "door_opened": 80,
    "ring_button_pressed": 80,
    "doorbell_pressed": 80,
    # Tier 50 — user-driven space transitions
    "space_armed": 50,
    "space_armed_with_malfunctions": 50,
    "space_auto_armed": 50,
    "space_auto_armed_with_malfunctions": 50,
    "space_disarmed": 50,
    "space_auto_disarmed": 50,
    "space_duress_disarmed": 50,
    "space_night_mode_on": 50,
    "space_night_mode_on_with_malfunctions": 50,
    "space_night_mode_off": 50,
    "space_duress_night_mode_off": 50,
    "space_group_armed": 50,
    "space_group_armed_with_malfunctions": 50,
    "space_group_auto_armed": 50,
    "space_group_auto_armed_with_malfunctions": 50,
    "space_group_disarmed": 50,
    "space_group_auto_disarmed": 50,
    "space_group_duress_disarmed": 50,
    # Tier 40 — HubEventTag legacy arm/disarm (Ajax sends these as
    # secondary context; the SpaceEventTag variants above are the
    # canonical signal for the user action).
    "arm": 40,
    "arm_attempt": 40,
    "arm_with_malfunctions": 40,
    "group_arm": 40,
    "group_arm_with_malfunctions": 40,
    "disarm": 40,
    "duress_disarm": 40,
    "group_disarm": 40,
    "night_mode_on": 40,
    "night_mode_off": 40,
    "duress_night_mode_off": 40,
    # Tier 30 — informational
    "battery_low": 30,
    "device_communication_loss": 30,
    "server_connection_loss": 30,
    "gsm_connection_loss": 30,
    "ethernet_connection_loss": 30,
    "malfunction": 30,
}


ALL_EVENT_TYPES: list[str] = sorted(
    set(HUB_EVENT_TAG_MAP.values())
    | set(SPACE_EVENT_TAG_MAP.values())
    | set(VIDEO_EVENT_TAG_MAP.values())
    | set(SMARTLOCK_EVENT_TAG_MAP.values())
)
