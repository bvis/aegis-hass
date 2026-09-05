"""Data update coordinator for Ajax Security."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from custom_components.aegis_ajax.api import devices_parser
from custom_components.aegis_ajax.api.devices import DevicesApi
from custom_components.aegis_ajax.api.hts.client import HtsClient
from custom_components.aegis_ajax.api.hts.hub_events import (
    HUB_EVENT_ENTRY_DELAY_STARTED,
    HUB_EVENT_EXIT_DELAY_COMPLETE,
    HubEvent,
)
from custom_components.aegis_ajax.api.hub_object import (
    DeviceFirmwareUpdateInfo,
    HubFirmwareUpdateInfo,
    HubObjectApi,
    SimCardInfo,
)
from custom_components.aegis_ajax.api.media import MediaApi
from custom_components.aegis_ajax.api.models import Device as DeviceModel
from custom_components.aegis_ajax.api.models import (
    device_deactivation_kinds,
    is_device_deactivated,
)
from custom_components.aegis_ajax.api.security import SecurityApi
from custom_components.aegis_ajax.api.session import (
    AuthenticationError,
    TwoFactorRequiredError,
    log_fingerprint,
)
from custom_components.aegis_ajax.api.spaces import SpacesApi
from custom_components.aegis_ajax.const import (
    BUTTON_PRESS_DEVICE_TYPES,
    BUTTON_PRESS_EVENT_TYPE,
    BYPASS_CONFIRM_DELAY,
    DEACTIVATED_KEY,
    DEACTIVATION_STATUS_KEYS,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    HUB_DEVICE_TEMP_REFRESH_INTERVAL,
    HUB_DEVICE_TEMPERATURE_DEVICE_TYPES,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    MOTION_PUSH_AUTO_OFF_SECONDS,
    SECURITY_EVENT_REFRESH_COOLDOWN,
    SIGNAL_NEW_DEVICE,
    SIREN_ALARM_DURATION_KEY,
    SIREN_DEVICE_TYPES,
    SIREN_SETTINGS_CONFIRM_DELAY,
    SIREN_VOLUME_LEVEL_KEY,
    ChimeStatus,
    ConnectionStatus,
)
from custom_components.aegis_ajax.delay_states import (
    DELAY_OVERLAY_GRACE_SECONDS,
    ArmDelays,
    DelayKind,
    DelayOverlay,
    parse_arm_delays,
)
from custom_components.aegis_ajax.device_cache import DevicesCache
from custom_components.aegis_ajax.entity import async_get_registered_device
from custom_components.aegis_ajax.repairs import (
    async_clear_hts_chronic_failure,
    async_clear_hub_offline,
    async_register_hts_chronic_failure,
    async_register_hub_offline,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.util.json import JsonArrayType

    from custom_components.aegis_ajax.api.client import AjaxGrpcClient
    from custom_components.aegis_ajax.api.hts.hub_state import (
        DeviceReadings,
        HubNetworkState,
    )
    from custom_components.aegis_ajax.api.hts.keyfobs import Keyfob
    from custom_components.aegis_ajax.api.models import Device, Room, Space
    from custom_components.aegis_ajax.notification import AjaxNotificationListener
    from custom_components.aegis_ajax.persistent_notification import AjaxPersistentNotifier

_LOGGER = logging.getLogger(__name__)

# Sustained-failure thresholds before raising HA Repairs. Below these the
# integration just logs and recovers silently; above them the user is
# expected to take action (check hub power, firewall, etc).
_HUB_OFFLINE_THRESHOLD_HOURS = 24
_HTS_CHRONIC_FAILURE_SECONDS = 30 * 60

# Minimum seconds between two user-triggered STATUS_BODY refresh requests
# on the same hub. The periodic refresh loop already runs every 60 s
# (`STATUS_REFRESH_INTERVAL` in `hts/client.py`), so a manual press more
# often than that surfaces no fresher data — the next periodic tick
# would have caught any change anyway. Capping at the same cadence
# stops a misbehaving automation from hammering the hub while letting
# users still bypass the wait once per minute when needed.
MANUAL_REFRESH_INTERVAL = 60

# Map proto status field name to internal key used by binary_sensor/sensor.
# Module-level constant to avoid recreating on every status update.
_STATUS_KEY_MAP: dict[str, str] = {
    "co_level_detected": "co_detected",
    "high_temperature_detected": "high_temperature",
    "case_drilling_detected": "case_drilling",
    "anti_masking_alert": "anti_masking",
    "interference_detected": "interference",
    "glass_break_detected": "glass_break",
    "vibration_detected": "vibration",
    "wire_input_status": "wire_input_alert",
    "transmitter_status": "wire_input_alert",
    "smart_lock": "smart_lock_state",
    "lock_control_status": "smart_lock_state",
}

# Status oneof cases that mean the device's case/mounting is being tampered
# with. The per-device tamper binary_sensor binds to the shared `tamper` key,
# which is NOT a real oneof case in any vendored proto revision — these
# granular signals are what the wire actually carries, so the delta handler
# mirrors them onto `tamper` (#339). Values are the internal status keys the
# same signals write through `_STATUS_KEY_MAP`, used to decide whether any
# other tamper source is still active before clearing on a REMOVE.
_TAMPER_SOURCE_KEYS: dict[str, str] = {
    "lid_opened": "lid_opened",
    "smart_bracket_unlocked": "smart_bracket_unlocked",
    "case_drilling_detected": "case_drilling",
}

# HTS per-device kv keys that a hardware capture tied to physical case
# tampering (#339): on a Hub Plus, `0x04` and `0x0f` both flipped `00` → `01`
# when a MotionProtect Curtain was pulled off its SmartBracket and back on
# re-attach, across two runs — on a hub whose gRPC status snapshot carried no
# tamper signal at all, which is why that hub's tamper sensor stays off even
# after the #340 fold. Which key is the lid and which the bracket is NOT
# established, and one hub is not enough to wire a user-visible alarm signal
# to: a key that means something else on another firmware would raise phantom
# tampers. So this stays read-only for now — the probe below logs the values
# so a reporter on DEBUG can confirm the semantics on their own hardware.
_HTS_TAMPER_CANDIDATE_KEYS: tuple[int, ...] = (0x04, 0x0F)

# The device families the keys above are actually *routed* for (#406). They
# are not a two-value tamper field everywhere, and reading them as one raises
# a permanent phantom alarm on an intact device: a reporter's MotionProtect
# holds `0x0f` at `01` across every probe cycle while the Ajax app shows no
# lid or bracket problem and a physical remount changes nothing, and a
# SpaceControl holds `0x04` at `80` the same way (#339, 244 consecutive
# readings) — caught there only because `80` is not one of the two values the
# routing acts on. So the routing is limited to the families where a capture
# tied a key to a *physical* tamper, both from #339: a MotionProtect Curtain
# flipping both keys `00` -> `01` on being pulled off its SmartBracket and
# back on re-attach across three runs, and a Transmitter raising and clearing
# the sensor when its enclosure was opened.
#
# Widen this per family, on a capture showing a key move with a physical
# tamper. A family that merely reads `00` is not evidence: it shows the keys
# are unset, not that a tamper would set them. The trade is deliberate — an
# unlisted family misses an HTS-only tamper, which is the recoverable half;
# the granular gRPC sources (`lid_opened`, `smart_bracket_unlocked`,
# `case_drilling`) are untouched by this gate and still drive the sensor
# wherever the hub reports them.
_HTS_TAMPER_ROUTED_DEVICE_TYPES: frozenset[str] = frozenset(
    {
        "motion_protect_curtain",
        "transmitter",
    }
)

# Per-device *measurements* carried across a full device snapshot that omits
# them (#403). `_handle_devices_snapshot` rebuilds each device from the snapshot,
# so a reading the stream does not repeat is gone until that device next sends
# one — and a battery sitting at 100% has nothing to send, which is why it is the
# reading that never comes back. @wip3out3r measured 1 of 13 batteries and 1 of
# 11 signal strengths still empty four hours later, while the only two devices
# that already had a carry-forward kept their values throughout. That contrast is
# what identified this function rather than the HTS disconnect handler, which
# preserves its caches by design.
#
# Named, rather than a blanket "merge instead of replace", because most of what
# rides in `statuses` must NOT survive a snapshot: `lid_opened`, `case_drilling`
# and `gsm_connected` are operational alerts, and an alert that has cleared has
# to clear here too. Membership follows the rule the report states better than we
# had: a snapshot omitting a *measurement* does not make the previous value
# wrong, only older. Anything whose absence is itself the signal stays out.
#
# `temperature` was excluded here at first and #403 stayed open for it: the
# worry was that a carry would leave a family with no per-device temperature
# source holding one. It cannot — this pass only fills a key already present in
# the existing statuses, so a device that never reported a temperature never
# gains one (pinned by
# `test_snapshot_does_not_invent_temperature_the_device_never_reported`). The
# staleness/immortality risk is exactly the one already accepted for the four
# keys above: a carried value is overwritten by the next row that reports one,
# and seven of the nine temperatures that emptied in #403 were light-stream
# families no siren-gated carry could reach. For the
# `HUB_DEVICE_TEMPERATURE_DEVICE_TYPES` families the value arrives from a
# separate per-device RPC (#220, #229), so EVERY light snapshot omits it — for
# them this carry is what keeps the sensor valued between timer fires, a job a
# siren-gated block used to do before this entry subsumed it.
#
# `tamper` and the siren settings keep their own blocks below — they carry
# provenance conditions this generic pass has no business reproducing.
# HTS per-device key carrying a MultiTransmitter wire input's contact state
# (#413): `WireInputMt.external_contact_state`. Hardware-validated by
# Taknok's four-state capture (2026-08-22, NC and NO, bistable): the hub
# applies the input's configured NO/NC polarity ITSELF before reporting, so
# `01` (CONTACT_DISRUPTED — the app's "Alerte") always means "the contact
# left its configured rest position" in both modes, and `00`/`02` mean at
# rest ("OK"). It rides the STATUS_BODY the client already requests every
# 60 s plus live STATUS_UPDATE deltas — consuming it adds no Ajax API
# traffic. STRICTLY per-family: the same byte is
# `external_sensor_power_broken` on the MultiTransmitter itself and other
# things elsewhere (HTS sub-keys are legacy proto field numbers, per family).
_HTS_CONTACT_STATE_KEY = 0x33

# The statuses key the routed value lands on, read by the `opening`
# binary sensor.
_EXTERNAL_CONTACT_OPEN_KEY = "external_contact_open"

#
# `external_contact_open` (#413) is HTS-only: no gRPC surface carries it, so
# a snapshot omitting it is NEVER a signal, and HTS itself updates or
# withdraws it on its ≤60 s cadence.
_SNAPSHOT_CARRY_FORWARD_STATUS_KEYS: frozenset[str] = frozenset(
    {
        "humidity",
        "co2",
        "signal_strength",
        "temperature",
        _EXTERNAL_CONTACT_OPEN_KEY,
    }
)

# HTS per-device kv key carrying the hub's own report of a device's engaged
# bypass modes (#419). Hardware-validated in #338: `01` ⇔ the panel has the
# device deactivated, `00` ⇔ protecting, 1:1 across every device that reports
# it at rest. It rides the STATUS_BODY the client already requests every 60 s,
# so consuming it adds no Ajax API traffic.
_HTS_BYPASS_STATE_KEY = 0xB7

# How long the last 0xB7 report may be trusted to corroborate a snapshot's
# silence (#419). STATUS_BODY refreshes every 60 s, so 15 minutes is ~15
# missed refreshes — generous against the longest observed drop-to-reconnect
# (3m24s, #403) while refusing to let an hours-stale report overrule a
# snapshot after HTS has been down long enough for the world to have moved.
_HTS_BYPASS_STATE_TRUST_WINDOW = 15 * 60.0

# HTS per-device kv keys that may carry a Button's last-activity timestamp
# (#348) — read-only probe, nothing is routed off them.
#
# A hardware log of an Ajax Button in control mode showed `0x39` on the
# device's STATUS_UPDATE row moving to the exact second of each press, twice,
# on two independent presses: a press logged at 08:18:25 local carried
# `0x39=6a683b9f` (05:18:23Z) and one at 08:23:10 carried `0x39=6a683cbd`
# (05:23:09Z) on a UTC+3 install. So `0x39` decodes as a big-endian Unix epoch
# and it is the only signal a control-mode press produces at all — the
# `button_1_on` / `button_2_on` FCM tags never appeared, and the coincident
# gRPC delta is a plain `battery` status.
#
# Two things that capture could NOT establish, which is why this is a probe and
# not an event entity:
#
#   1. **Short vs long click are indistinguishable in it.** Both presses moved
#      `0x39` and nothing else; `0x40` stayed `00000000` on that device. So a
#      one-key signal cannot carry the two separate actions the issue asks for.
#      (`0x40` did hold an old epoch — 2026-07-03 — on the install's *other*
#      Button, so it means something rarer than a press, not the second action.)
#   2. **Whether it moves without a press.** Ajax peripherals ping the hub for
#      supervision, and if `0x39` were last-radio-contact rather than
#      last-press, routing an HA event off it would fire phantom presses on
#      every ping. Logging only *transitions* makes an idle install the control
#      case: silence over a quiet window falsifies the ping reading, while a
#      1:1 match with presses confirms it.
_HTS_BUTTON_ACTIVITY_CANDIDATE_KEYS: tuple[int, ...] = (0x39, 0x40)
# The one key of those two that the press event is actually wired to (#348).
# `0x40` is deliberately excluded: on a StreetSiren it is a 1-byte counter that
# advances in lockstep across sirens on the same hub, so it tracks something
# hub-wide and is not this device's activity.
_HTS_BUTTON_PRESS_KEY = 0x39

# SpaceControl settings keys on a *gRPC-modeled* keyfob's HTS row (#311) —
# read-only probe, nothing is routed off them.
#
# Keyfobs are HTS-only on some hubs but not all: `ObjectType` carries both
# `space_control` and `space_control_s`, and on a hub that reports one the
# keyfob is an ordinary modeled device, so its SETTINGS_BODY row never reaches
# the keyfob classifier in `api/hts/keyfobs.py` (see `_handle_keyfob_kv`).
# @wip3out3r's 47-key capture is that row, and it carries every one of these
# six — they are `SpaceControl`'s own field numbers in the hub's device model,
# which is what identifies the row as a SpaceControl's rather than as an
# unrecognised keyfob variant. The loose keyfob-candidate predicate cannot see
# it either: that row has no name key at all, and the predicate requires one.
#
# So this class of hub contributes nothing to the still-unverified activation
# flag (#311) unless its row is logged, which is what this does. None of the
# six is user-typed text, so the line carries no names.
_HTS_SPACE_CONTROL_SETTINGS_KEYS: dict[int, str] = {
    0x2E: "siren_triggers",
    0x31: "panic_enabled",
    0x33: "associated_group_id",
    0x34: "associated_user_id",
    0x35: "false_press_filter",
    0xC3: "subtype",
}
# `0xC3` cannot gate the settings probe: it rides the 60 s STATUS_BODY row as
# well as the settings row, so gating on the whole dict above left `present`
# permanently non-empty and the probe logged once a minute forever — measured by
# @wip3out3r on a modeled SpaceControl, five of six lines carrying nothing but
# `subtype` (#311). The other five keys appear only on the settings row, so
# requiring one of *them* is what makes the early-out do what it claims.
# `subtype` still rides along in the output when it is present.
_HTS_SPACE_CONTROL_GATING_KEYS: frozenset[int] = frozenset(
    _HTS_SPACE_CONTROL_SETTINGS_KEYS.keys() - {0xC3}
)
# The `0x0b..0x0e` quartet that the HTS-only keyfob path reads its experimental
# `Active` flag from (`keyfobs.KEYFOB_ACTIVE_SUBKEY` is `0x0b`). Tracked here
# because @wip3out3r found `0x0b` present on a *modeled* SpaceControl's status
# row reading `01` (#311). The reasoning until then was that a modeled
# SpaceControl never reaches the keyfob path, so the flag was unavailable on this
# class of hub; if this is the same byte, such a hub can supply the deactivated
# sample after all, from a row we already parse.
#
# Whether it IS the same byte is exactly what a deactivated capture would settle,
# and it is not assumed: the `0x40` precedent — a 4-byte epoch on one family, a
# 1-byte counter on another — is why a sub-key number matching across families is
# not evidence on its own. These live on the status row, which is why they are
# logged on *change* rather than on the settings gate above; the two rows are
# disjoint in the keys that matter.
_HTS_SPACE_CONTROL_FLAG_CANDIDATE_KEYS: tuple[int, ...] = (0x0B, 0x0C, 0x0D, 0x0E)
# The two `ObjectType` cases a keyfob arrives as when the snapshot models it.
_SPACE_CONTROL_DEVICE_TYPES: frozenset[str] = frozenset({"space_control", "space_control_s"})

# Bounds for treating a 4-byte HTS value as a big-endian Unix epoch. Deliberately
# wide — the point is only to reject values that clearly aren't timestamps (`00000000`,
# a counter, a bitfield) so the probe never dresses an unrelated key up as a date.
_HTS_EPOCH_MIN = 1_420_070_400  # 2015-01-01Z
_HTS_EPOCH_MAX = 4_102_444_800  # 2100-01-01Z


def _describe_hts_epoch(value: bytes) -> str:
    """Render a 4-byte HTS value as a big-endian UTC timestamp, or say why not.

    Used by the #348 Button probe so a DEBUG line can be lined up against the
    wall-clock moment of a press without hand-converting hex. Anything that is
    not a plausible epoch is reported as such rather than being forced into a
    date — see `_HTS_BUTTON_ACTIVITY_CANDIDATE_KEYS`.
    """
    if len(value) != 4:
        return f"not an epoch: {len(value)}b"
    seconds = int.from_bytes(value, "big")
    if not _HTS_EPOCH_MIN <= seconds <= _HTS_EPOCH_MAX:
        return f"not an epoch: {seconds}"
    return dt_util.utc_from_timestamp(seconds).isoformat()


# Marks a `tamper` status as sourced from the HTS status stream rather than the
# gRPC device stream. The gRPC snapshot on these hubs has no tamper field at
# all, so a fresh snapshot would silently wipe the status; this lets
# `_handle_devices_snapshot` carry it forward, and lets the HTS `00` withdraw
# only what HTS itself raised.
_HTS_CASE_TAMPER_KEY = "hts_case_tamper"


def _without_hts_case_tamper(statuses: dict[str, Any]) -> dict[str, Any]:
    """Strip the HTS case-tamper marker, and `tamper` with it when it was alone.

    `tamper` is shared: the device stream sets it from `lid_opened`,
    `smart_bracket_unlocked` or `case_drilling`. Dropping the HTS marker must
    not cancel a tamper one of those is still reporting, so `tamper` only goes
    when no granular source is left.
    """
    remaining = {k: v for k, v in statuses.items() if k != _HTS_CASE_TAMPER_KEY}
    if not any(remaining.get(key) for key in _TAMPER_SOURCE_KEYS.values()):
        remaining.pop("tamper", None)
    return remaining


# Mirror of `lock.LOCK_DEVICE_TYPES` (kept local to avoid a circular import
# with the lock platform, which imports the coordinator). Used only by the
# one-shot #206 Bug-B SmartLock id probe.
_LOCK_DEVICE_TYPES: frozenset[str] = frozenset({"smart_lock", "smart_lock_yale"})

# HTS `type=0x08` Chime-event state byte → ChimeStatus (#239). The hub stamps
# the new chime state into params[3] of the event frame the instant the chime
# is toggled (incl. from the Ajax app): 0x38 = on, 0x39 = off (BadFlo's
# capture). Decoding it directly reflects app-side toggles immediately and
# avoids re-reading the gRPC snapshot, which lags the toggle — that re-read
# returned a stale `ENABLED` right after an app-side OFF, so the switch never
# moved (#239 beta.2 regression). The gRPC re-read survives only as the
# fallback for an unrecognised byte.
_CHIME_EVENT_STATE_BYTE: dict[int, ChimeStatus] = {
    0x38: ChimeStatus.ENABLED,
    0x39: ChimeStatus.CAN_BE_ENABLED,
}

# The chime toggle and arm/disarm share the same `type=0x08` event frame; the
# state byte (params[3]) tells them apart. Chime is decoded directly above
# (idempotent + low-stakes). The security state is deliberately NOT decoded
# from the byte (#258): arm-initiated ≠ armed, a disarm during the exit delay
# emits no event, and events can be dropped on an HTS reconnect — so a decoded
# state can stick wrong on an alarm panel (observed live, 2026-06-06). Any
# non-chime event is used only as a real-time nudge to re-read the authoritative
# `security_state` over gRPC; the 300s poll backstops a missed nudge.

# Statuses whose snapshot parser writes more than the single mapped key.
# Used by the REMOVE op so stale sub-keys don't linger after the hub drops
# the parent status from the stream.
_STATUS_EXTRA_KEYS: dict[str, tuple[str, ...]] = {
    "motion_detected": ("motion_detected_at",),
    "life_quality": ("temperature", "humidity", "co2"),
    "gsm_status": ("mobile_network_type", "gsm_connected"),
    "wire_input_status": ("wire_input_alarm_type",),
    "transmitter_status": ("wire_input_alarm_type",),
}


class AjaxCobrandedCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: AjaxGrpcClient,
        space_ids: list[str],
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        on_session_persist: Callable[[str, str], None] | None = None,
        entry_id: str = "",
        delay_panel_states: bool = False,
    ) -> None:
        poll_interval = max(MIN_POLL_INTERVAL, min(MAX_POLL_INTERVAL, poll_interval))
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=poll_interval)
        )
        self._poll_interval = poll_interval
        self.entry_id = entry_id
        self._client = client
        self._reauth_required = False
        self._on_session_persist = on_session_persist
        self._space_ids = space_ids
        self._spaces_api = SpacesApi(client)
        self._security_api = SecurityApi(client)
        self._devices_api = DevicesApi(client)
        self._hub_object_api = HubObjectApi(client)
        self._media_api = MediaApi(client)
        self.spaces: dict[str, Space] = {}
        self.devices: dict[str, Device] = {}
        self.rooms: dict[str, Room] = {}
        self.sim_info: dict[str, SimCardInfo] = {}
        # Hubs whose SIM read has already failed once. Only used to keep the
        # warning to one line per hub (#379); cleared on a later success.
        self._sim_info_failed: set[str] = set()
        # Pending hub firmware update keyed by hub_id (#updates). Absent
        # entry = the hub reports no pending update OR the streamHubObject
        # call hasn't completed yet. Refreshed on the same hourly cycle
        # as `sim_info`. Read-only; the integration never calls the
        # install RPC even though the proto exposes one.
        self.hub_firmware_updates: dict[str, HubFirmwareUpdateInfo] = {}
        # Pending per-device firmware updates keyed by device_id (2.1).
        # Absent entry = the device reports no pending update. Rides the
        # same hourly `streamHubObject` cycle as `hub_firmware_updates`.
        # Read-only, disabled-by-default entities; the integration never
        # triggers the install RPC.
        self.device_firmware_updates: dict[str, DeviceFirmwareUpdateInfo] = {}
        self._notification_listener: AjaxNotificationListener | None = None
        # Optional persistent-notification manager (2.2). Attached by
        # async_setup_entry from the config-entry options; None means the
        # feature is off (the default) and event dispatch skips it.
        self._persistent_notifier: AjaxPersistentNotifier | None = None
        self._stream_tasks: list[asyncio.Task[None]] = []
        self._streams_started: bool = False
        self._event_entities: dict[str, Any] = {}
        # device_id -> per-device doorbell event entity (#173)
        self._device_event_entities: dict[str, Any] = {}
        # device_id -> cancel handle for a pending motion auto-off timer (#173)
        self._motion_off_cancels: dict[str, Any] = {}
        self.last_photo_urls: dict[str, str] = {}
        # space_id -> (expiry_time, security_state)
        self._optimistic_space_states: dict[str, tuple[float, Any]] = {}
        # Spaces with an intrusion alarm push not yet acknowledged by an
        # observed DISARMED (#426). Drives the panel's `triggered` overlay —
        # the served SecurityState cannot express "alarm firing". In memory
        # only, on purpose: never persisted or restored, so a reload/restart
        # cannot resurrect a stale alarm.
        self.alarmed_space_ids: set[str] = set()
        # Exit / entry delays as `arming` / `pending` panel states (#454),
        # opt-in. `delay_overlays` is the client-side overlay per space, driven
        # by the hub's HTS delay events and bounded by a timer; like the
        # alarm overlay it lives in memory only, so a restart shows the hub's
        # plain state. `_device_arm_delays` holds each detector's 0xAC/0xAD
        # seconds from its SETTINGS_BODY row (the fallback bound and the
        # `exit_delay_seconds` attribute). `_last_security_states` is the
        # previous observation per space, so an arm is detected wherever the
        # state was written (poll, push, our own optimistic write).
        self.delay_panel_states: bool = delay_panel_states
        self.delay_overlays: dict[str, DelayOverlay] = {}
        self._device_arm_delays: dict[str, ArmDelays] = {}
        self._last_security_states: dict[str, Any] = {}
        self._delay_overlay_cancels: dict[str, Any] = {}
        # SIM info is mostly static — cache and refresh once per hour
        self._sim_info_last_fetch: float = 0.0
        # Rooms rarely change — cache and refresh once per hour. None means
        # never fetched yet so the first poll always populates rooms.
        self._rooms_last_fetch: float | None = None
        # Per-group security state lives only on the hourly snapshot, not the
        # lighter `list_spaces` poll (like chime/groups). A space HTS event
        # (#258) re-reads the space state but not the groups, so without FCM
        # per-group panels lagged up to an hour (#266). The space-event handler
        # sets this flag so the next refresh bypasses the hourly snapshot gate
        # and re-reads group states immediately. Consumed in `_maybe_refresh_rooms`.
        self._force_snapshot_refresh: bool = False
        # Dedicated debouncer for event-triggered `security_state` re-reads (#270).
        # The shared request-refresh debouncer HA gives us has a 10 s cooldown,
        # which coalesced a rapid arm→disarm→arm burst into a single trailing
        # re-read that lagged the alarm panel by up to ~10 s when FCM push didn't
        # deliver promptly. A short dedicated cooldown fires the re-read ~1 s
        # after each event (still collapsing true sub-second duplicate frames),
        # and the small settle gives the gRPC snapshot time to propagate. Built
        # with the `hass` param (not `self.hass`, unset until `super().__init__`).
        self._security_refresh_debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=SECURITY_EVENT_REFRESH_COOLDOWN,
            immediate=False,
            function=self.async_refresh,
        )
        # Per-device internal temperature (#220 sirens, #229 outdoor curtain
        # PIRs) — refreshed on a dedicated timer (`async_track_time_interval`),
        # NOT the poll cycle. On push-heavy hubs every HTS update resets HA's
        # poll timer, so the scheduled poll never fires again after startup; a
        # poll-driven refresh would be starved.
        self._unsub_hub_device_temp: CALLBACK_TYPE | None = None
        # Sirens with a post-write settings confirm read in flight (single-flight
        # per device; see `schedule_siren_settings_confirm`).
        self._siren_confirm_pending: set[str] = set()
        # Sirens whose settings read has already failed and been warned about,
        # so a permanently unreadable one is reported once instead of every
        # 900 s sweep (#354). Cleared on a successful read so a device that
        # recovers and breaks again warns again.
        self._siren_settings_failed: set[str] = set()
        # Devices with a post-write bypass confirm read in flight (single-flight
        # per device; see `schedule_bypass_confirm`).
        self._bypass_confirm_pending: set[str] = set()
        # Independent poll safety-net timer (#178). On active hubs every HTS
        # update reschedules HA's built-in poll timer faster than
        # `poll_interval`, starving the scheduled `_async_update_data`; this
        # dedicated timer drives a periodic refresh regardless of HTS chatter.
        self._unsub_poll_safety: CALLBACK_TYPE | None = None
        # HTS client for hub network data (ethernet, wifi, gsm, power)
        self._hts_client: HtsClient | None = None
        self._hts_task: asyncio.Task[None] | None = None
        # Last hub-reported bypass state per device (#419): HTS 0xB7 as
        # (deactivated, monotonic seen-at). Gates the deactivation
        # carry-forward across gRPC snapshots; never creates state.
        self._hts_bypass_state: dict[str, tuple[bool, float]] = {}
        # Devices whose deactivation statuses exist ONLY because the
        # corroborated carry preserved them (#419). Membership licenses the
        # withdraw-on-0xB7=00 path; anything gRPC-fresh clears membership.
        self._hts_carried_deactivation_ids: set[str] = set()
        # Monotonic timestamp of the last user-triggered STATUS_BODY
        # refresh per hub. Read by `async_request_manual_refresh` to
        # rate-limit successive presses to `MANUAL_REFRESH_INTERVAL`.
        self._last_manual_refresh: dict[str, float] = {}
        self.hub_network: dict[str, HubNetworkState] = {}
        # Per-device electrical readings (current_ma / power_consumed_wh)
        # populated from HTS STATUS_BODY rows of WallSwitch / Socket
        # family devices (#123). Keyed by upper-case 8-char device id
        # (same shape as `self.devices` keys). Empty dict = no readings
        # snapshotted yet OR no electrical devices in the install.
        self.device_readings: dict[str, DeviceReadings] = {}
        # SpaceControl keyfobs (HTS-only; not in the gRPC device snapshot).
        # Populated from SETTINGS_BODY rows via `_on_hts_device_kv`. Keyed by
        # upper-case 8-char device id. The binary_sensor platform creates a
        # device + experimental "Active" sensor per entry, added at runtime via
        # the `SIGNAL_NEW_DEVICE` dispatcher as keyfobs are discovered.
        self.keyfobs: dict[str, Keyfob] = {}
        # Last-seen arm flag (HTS sub-key 0x06) per hub-internal space-security
        # object (00000001/00000002…). A keypad full-arm of a group reaches us
        # only as a STATUS_UPDATE flip of this flag — no type=0x08 space event
        # and no FCM push on no-FCM installs (#284) — so a *change* nudges the
        # authoritative re-read. Tracking the last value avoids re-nudging on
        # every 60s STATUS_BODY probe (which re-reports the same flag).
        self._space_security_arm_flags: dict[str, int] = {}
        # Last-seen value of each Button activity-candidate key (#348), keyed by
        # `(device_id_hex, kv_key)`. Only a *change* is logged, so an untouched
        # Button is silent and a quiet window becomes the control case that
        # tells a press apart from a supervision ping. See
        # `_HTS_BUTTON_ACTIVITY_CANDIDATE_KEYS`.
        self._hts_button_activity: dict[tuple[str, int], str] = {}
        # Last press epoch seen per Button, keyed by device id (#348). Separate
        # from the probe cache above on purpose: the probe consumes its own
        # entry before this runs, and this one holds a decoded int rather than
        # raw hex so the "time must move forward" check is a plain comparison.
        self._button_press_epochs: dict[str, int] = {}
        # Per-device Button press event entities, registered by the event
        # platform. Kept apart from `_device_event_entities` (doorbells) so the
        # two dispatch paths can never collide on a shared device id.
        self._button_event_entities: dict[str, Any] = {}
        # Devices whose row has been seen in at least one body snapshot. Needed
        # to tell a delta-only key apart from a key we simply haven't baselined
        # yet: once a device's body row has arrived without a given key, that
        # key lives only in deltas, and there its first sighting IS the event
        # rather than a baseline to swallow (#348).
        self._hts_button_activity_bodies_seen: set[str] = set()
        # Last-seen value of each SpaceControl activation-flag candidate (#311),
        # keyed by `(device_id_hex, kv_key)`. Same change-only contract as the
        # Button cache above and for a sharper reason: the flag is expected to
        # read `01` on every active keyfob, so a value-per-poll log would say
        # nothing while a *transition* is the one sample this issue has always
        # lacked. An active keyfob therefore stays silent indefinitely and the
        # line appears at the moment a CRA admin deactivates one.
        self._hts_space_control_flags: dict[tuple[str, int], str] = {}
        # One-shot guard for the #206 Bug-B SmartLock id probe (DEBUG-only).
        self._smart_lock_probe_done = False
        # Per-space monotonic timestamp of when the hub first reported
        # offline (cleared on the first ONLINE poll). Drives the
        # `hub_offline_24h` Repair surfaced after sustained downtime.
        self._first_offline_at: dict[str, float] = {}
        # Monotonic timestamp of the first HTS disconnect after a
        # healthy run; cleared whenever HTS reconnects. Drives the
        # `hts_chronic_failure` Repair surfaced after 30 min of
        # sustained reconnect failures.
        self._hts_first_failure_at: float | None = None
        # Wall-clock timestamp of the last successful `_async_update_data`
        # return, exposed as `last_update_success_time` for the System
        # Health card. HA's `DataUpdateCoordinator` only tracks the
        # success boolean, not when it last happened.
        self._last_update_success_time: datetime | None = None
        # Persistent device-snapshot cache (#114) — restored on first
        # refresh so platform setup doesn't have to await the gRPC
        # `get_devices_snapshot` call. Tests construct the coordinator
        # without an entry_id; in that mode the cache is disabled.
        self._devices_cache: DevicesCache | None = (
            DevicesCache(hass, entry_id) if entry_id else None
        )

    @property
    def security_api(self) -> SecurityApi:
        return self._security_api

    @property
    def spaces_api(self) -> SpacesApi:
        return self._spaces_api

    @property
    def doorbell_twin_aliases(self) -> dict[str, str]:
        """`{dropped video-doorbell twin id: surviving video_edge id}` (#173).

        Pushes carry the Jeweller twin id, which is gone after dedup; this lets
        `notification` resolve doorbell/motion pushes onto the real device.
        """
        return self._devices_api.doorbell_twin_aliases

    @property
    def devices_api(self) -> DevicesApi:
        return self._devices_api

    @property
    def hub_object_api(self) -> HubObjectApi:
        return self._hub_object_api

    @property
    def media_api(self) -> MediaApi:
        return self._media_api

    @property
    def notification_listener(self) -> AjaxNotificationListener | None:
        return self._notification_listener

    @property
    def is_hts_connected(self) -> bool:
        """True if HTS has an active connection feeding hub-network sensors."""
        return self._hts_client is not None and self._hts_task is not None

    async def async_list_client_sessions(self) -> JsonArrayType:
        """Return account sessions in a service-safe representation."""
        hts_client = self._require_hts_client()
        ajax_sessions = await hts_client.get_client_sessions()
        sessions: JsonArrayType = []
        for session in ajax_sessions:
            sessions.append(
                {
                    "session_id": session.session_id,
                    "device_model": session.device_model,
                    "operating_system": session.operating_system,
                    "application": session.application,
                    "version": session.version,
                    "created_at": session.created_at,
                    "expires_at": session.expires_at,
                    "last_active_at": session.last_active_at,
                    "is_current": session.is_current,
                }
            )
        return sessions

    async def async_terminate_client_session(self, session_id: int) -> None:
        """Terminate one selected non-current Ajax account session."""
        hts_client = self._require_hts_client()
        sessions = await hts_client.get_client_sessions()
        target = next((session for session in sessions if session.session_id == session_id), None)
        if target is None:
            raise ValueError("The selected Ajax session is no longer active.")
        if target.is_current:
            raise ValueError("Refusing to terminate the current Aegis session.")
        await hts_client.kill_client_sessions([session_id])
        _LOGGER.info("Terminated one other Ajax account session")

    async def async_terminate_other_client_sessions(self) -> int:
        """Terminate every session except the identified current Aegis session."""
        hts_client = self._require_hts_client()
        sessions = await hts_client.get_client_sessions()
        current_sessions = [session for session in sessions if session.is_current]
        if len(current_sessions) != 1:
            raise ValueError(
                "Could not uniquely identify the current Aegis session; "
                "refusing to terminate other sessions."
            )
        session_ids = [
            session.session_id
            for session in sessions
            if not session.is_current and session.session_id is not None
        ]
        if session_ids:
            await hts_client.kill_client_sessions(session_ids)
            _LOGGER.info("Terminated %d other Ajax account session(s)", len(session_ids))
        return len(session_ids)

    def _require_hts_client(self) -> HtsClient:
        """Return the active HTS client or explain why session management cannot run."""
        if self._hts_client is None or not self._hts_client.is_connected:
            raise RuntimeError(
                "Ajax session management is unavailable because the HTS connection is not ready."
            )
        return self._hts_client

    @property
    def last_update_success_time(self) -> datetime | None:
        """UTC datetime of the last successful poll, or None if never polled."""
        return self._last_update_success_time

    async def _login_and_persist(self) -> None:
        """Login fresh and notify the on_session_persist callback.

        Wrapping the bare client.login() call so every login site goes
        through the persistence path. Without it the in-memory token is
        the only copy and a restart re-logins (creating yet another
        active session in Ajax) instead of reusing the latest one.
        """
        _LOGGER.debug(
            "Logging in to Ajax (fresh session, device_id=%s, app_label=%r)",
            log_fingerprint(self._client.session.device_id),
            self._client.session.app_label,
        )
        await self._client.login()
        token = self._client.session.session_token
        user_hex_id = self._client.session.user_hex_id
        if self._on_session_persist and token and user_hex_id:
            try:
                self._on_session_persist(token, user_hex_id)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Failed to persist refreshed session", exc_info=True)

    @staticmethod
    def _describe_rpc_error(exc: Exception) -> str:
        """One-line cause for a failed RPC: type, plus gRPC status if any.

        Handlers that log a failure with `exc_info=True` alone put the only
        discriminating fact — the status code — at the *end* of a traceback,
        which is precisely the part a reporter's log viewer truncates (#354).
        Putting it on the message line survives the paste.
        """
        code = getattr(exc, "code", None)
        if callable(code):
            try:
                value = code()
            except Exception:  # noqa: BLE001
                value = None
            name = getattr(value, "name", None)
            if name:
                details = getattr(exc, "details", None)
                detail_text = ""
                if callable(details):
                    try:
                        detail_text = details() or ""
                    except Exception:  # noqa: BLE001
                        detail_text = ""
                suffix = f" ({detail_text})" if detail_text else ""
                return f"{type(exc).__name__}/{name}{suffix}"
        return f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _is_unauthenticated_error(exc: Exception) -> bool:
        """True when a gRPC error indicates the saved token is no longer valid."""
        # grpc.StatusCode.UNAUTHENTICATED == 16; gRPC raises grpc.aio.AioRpcError
        code = getattr(exc, "code", None)
        if callable(code):
            try:
                value = code()
            except Exception:  # noqa: BLE001
                return False
            return (
                getattr(value, "value", (None,))[0] == 16
                or getattr(value, "name", "") == "UNAUTHENTICATED"
            )
        return False

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            await self._ensure_authenticated()
            self.spaces = await self._refresh_spaces()
            self._drop_cleared_alarms()
            self.sync_delay_overlays()
            self._prune_manual_refresh()
            now = asyncio.get_running_loop().time()
            self._update_hub_offline_repairs(now)
            await self._maybe_refresh_sim_and_firmware(now)
            await self._maybe_refresh_rooms(now)
            if not self._streams_started:
                await self._first_startup_init()
                return {"spaces": self.spaces, "devices": self.devices}
            await self._maybe_fallback_device_snapshot()
            await self._maybe_restart_hts()
            self._last_update_success_time = dt_util.utcnow()
            return {"spaces": self.spaces, "devices": self.devices}
        except ConfigEntryAuthFailed:
            raise
        except asyncio.CancelledError:
            # A `CancelledError` here typically comes from a sub-call (most
            # often the gRPC stub) whose channel got closed mid-flight —
            # e.g. when the user clicks Reload, the previous client's
            # teardown can race with the new client's first refresh and
            # the in-flight RPC gets cancelled. `CancelledError` is a
            # `BaseException`, so the `except Exception` below would
            # never see it; without this branch it bubbles through
            # `async_config_entry_first_refresh` and leaves the entry in
            # a permanent failed state until HA is restarted (#148).
            #
            # If OUR task is the one being cancelled (HA shutdown,
            # reload interrupting us, etc.), we must let the cancellation
            # propagate — eating it would prevent the coroutine from
            # ever exiting cleanly. `Task.cancelling()` returns the
            # pending cancel-request count; non-zero means HA wants us
            # gone, zero means the cancellation originated below us and
            # we can surface it as a retryable update failure.
            current = asyncio.current_task()
            if current is not None and current.cancelling() > 0:
                raise
            raise UpdateFailed("Ajax gRPC call was cancelled mid-flight") from None
        except Exception as err:
            raise UpdateFailed("Error fetching Ajax data") from err

    # ------------------------------------------------------------------
    # _async_update_data sub-steps (extracted from a 227-line god method)
    # ------------------------------------------------------------------

    async def _ensure_authenticated(self) -> None:
        """Re-login when the session lost its token. Restore the normal
        poll interval after a successful re-auth, slow it down to 30 min
        and raise `ConfigEntryAuthFailed` (surfaces the HA "Reconfigure"
        banner) when credentials are no longer accepted.
        """
        if self._reauth_required:
            self.update_interval = timedelta(minutes=30)
            raise ConfigEntryAuthFailed("Two-factor authentication required")
        if self._client.session.is_authenticated:
            return
        try:
            await self._login_and_persist()
        except TwoFactorRequiredError as err:
            self._reauth_required = True
            self.update_interval = timedelta(minutes=30)
            _LOGGER.error(
                "Ajax requires a 2FA code to log in again — triggering reauth so the "
                "code can be entered."
            )
            raise ConfigEntryAuthFailed("Two-factor authentication required") from err
        except AuthenticationError as err:
            self.update_interval = timedelta(minutes=30)
            _LOGGER.error("Authentication failed: %s — triggering reauth.", err)
            raise ConfigEntryAuthFailed(str(err)) from err
        configured = max(MIN_POLL_INTERVAL, min(MAX_POLL_INTERVAL, self._poll_interval))
        self.update_interval = timedelta(seconds=configured)

    def _prune_manual_refresh(self) -> None:
        """Drop manual-refresh rate-limit entries for hubs no longer on the
        account (#276). `_last_manual_refresh` gains a key per hub on every
        manual-refresh button press; without pruning, a hub removed from
        the account keeps its entry for the life of the session.
        """
        live_hubs = {s.hub_id for s in self.spaces.values() if s.hub_id}
        for hub_id in list(self._last_manual_refresh):
            if hub_id not in live_hubs:
                del self._last_manual_refresh[hub_id]

    async def _refresh_spaces(self) -> dict[str, Space]:
        """List spaces, recover once from a stale-token `UNAUTHENTICATED`,
        and merge in optimistic state + previously-cached groups /
        monitoring_companies so the lighter `list_spaces` poll doesn't
        wipe data that only the hourly snapshot path delivers.
        """
        from dataclasses import replace as dc_replace  # noqa: PLC0415

        try:
            all_spaces = await self._spaces_api.list_spaces()
        except Exception as exc:  # noqa: BLE001
            if not self._is_unauthenticated_error(exc):
                raise
            _LOGGER.warning(
                "Stored Ajax session was rejected (UNAUTHENTICATED). "
                "Forcing a fresh login and retrying."
            )
            self._client.session.clear_session()
            try:
                await self._login_and_persist()
            except TwoFactorRequiredError as tfa_err:
                # Ajax wants a 2FA code, which only the config flow can
                # collect. Without this branch the error falls through to
                # the generic `UpdateFailed` handler and HA just keeps
                # retrying setup — and every retry asks Ajax for a *new*
                # 2FA code, invalidating the one the user is currently
                # typing into the reconfigure form. `ConfigEntryAuthFailed`
                # stops the polling and opens the reauth flow, which does
                # know how to ask for the code.
                self._reauth_required = True
                raise ConfigEntryAuthFailed("Two-factor authentication required") from tfa_err
            except AuthenticationError as auth_err:
                raise ConfigEntryAuthFailed(str(auth_err)) from auth_err
            all_spaces = await self._spaces_api.list_spaces()

        now = asyncio.get_running_loop().time()
        new_spaces: dict[str, Space] = {}
        for s in all_spaces:
            if s.id not in self._space_ids:
                continue
            opt = self._optimistic_space_states.get(s.id)
            if opt and opt[0] > now and s.security_state != opt[1]:
                s = dc_replace(s, security_state=opt[1])
            elif opt and opt[0] <= now:
                self._optimistic_space_states.pop(s.id, None)
            previous = self.spaces.get(s.id)
            if previous:
                if previous.monitoring_companies or previous.monitoring_companies_loaded:
                    s = dc_replace(
                        s,
                        monitoring_companies=previous.monitoring_companies,
                        monitoring_companies_loaded=previous.monitoring_companies_loaded,
                    )
                # Group definitions + group_mode_enabled only come from the
                # hourly snapshot path; without preservation across plain
                # `list_spaces` polls, per-group alarm panels go
                # `unavailable` for the rest of the hour. `night_mode_enabled`
                # rides along: LiteSpace doesn't carry it either, and losing
                # it flips the panel from armed_night to armed_custom_bypass
                # on every plain poll while night mode is on (#284).
                if previous.groups or previous.group_mode_enabled:
                    s = dc_replace(
                        s,
                        groups=previous.groups,
                        group_mode_enabled=previous.group_mode_enabled,
                        night_mode_enabled=previous.night_mode_enabled,
                    )
                # Chime status (#239) also only comes from the hourly snapshot;
                # `list_spaces` (LiteSpace) doesn't carry it, so preserve the
                # last known value or the Chime switch flips to UNSPECIFIED
                # (unavailable) on every plain poll.
                if previous.chime_status is not ChimeStatus.UNSPECIFIED:
                    s = dc_replace(s, chime_status=previous.chime_status)
            new_spaces[s.id] = s
        return new_spaces

    async def _maybe_refresh_sim_and_firmware(self, now: float) -> None:
        """Cached once-per-hour fetch of SIM info + pending firmware update
        per hub. Both ride the same `streamHubObject` snapshot so they share
        cadence. Firmware always re-runs because a pending update can be
        cleared between cycles (Ajax-scheduled installs); SIM info is
        cached after the first successful fetch per hub.
        """
        sim_refresh_interval = 3600.0
        if now - self._sim_info_last_fetch <= sim_refresh_interval:
            return
        # Rebuild the per-device firmware map from scratch each cycle so a
        # completed/cleared update (Ajax-scheduled installs finish between
        # cycles) drops out instead of lingering.
        device_updates: dict[str, DeviceFirmwareUpdateInfo] = {}
        # Multiple spaces can share one hub (group mode); dedupe so the
        # per-hub `streamHubObject` snapshot is fetched once per cycle.
        seen_hubs: set[str] = set()
        for space in self.spaces.values():
            if not space.hub_id or space.hub_id in seen_hubs:
                continue
            seen_hubs.add(space.hub_id)
            if space.hub_id not in self.sim_info:
                await self._fetch_sim_info(space.hub_id)
            fw = await self._hub_object_api.get_firmware_info(space.hub_id)
            if fw is None:
                self.hub_firmware_updates.pop(space.hub_id, None)
            else:
                self.hub_firmware_updates[space.hub_id] = fw
            for dfu in await self._hub_object_api.get_device_firmware_updates(space.hub_id):
                # `.upper()` on write (and on the entity's read): the hex
                # device id here comes from `streamHubObject` while the
                # entities key off `Device.id` from the devices snapshot —
                # two services whose id casing is not guaranteed to match.
                device_updates[dfu.device_id.upper()] = dfu
        self.device_firmware_updates = device_updates
        self._sim_info_last_fetch = now

    async def _fetch_sim_info(self, hub_id: str) -> None:
        """Read a hub's SIM info once, and say out loud when it can't be read.

        The IMEI sensor is only created for hubs present in `sim_info`, so a
        failure here means the entity silently never appears — and if it was
        created on an earlier boot it stays `unavailable` forever, because
        Home Assistant does not evict entities a platform stops offering.
        That is the whole of #379, and it used to be invisible: the read
        swallowed every exception into a DEBUG line that named neither the
        cause nor the gRPC status code.

        First failure per hub is a warning naming the cause; repeats drop to
        debug so a hub that can never report a SIM doesn't fill the log.
        A hub that simply has no modem returns `None` without raising and is
        not an error — it is logged once at debug and nothing is created.
        """
        try:
            sim = await self._hub_object_api.get_sim_info(hub_id)
        except Exception as exc:  # noqa: BLE001
            first_failure = hub_id not in self._sim_info_failed
            self._sim_info_failed.add(hub_id)
            _LOGGER.log(
                logging.WARNING if first_failure else logging.DEBUG,
                "Failed to read SIM info for hub %s: %s%s",
                hub_id,
                self._describe_rpc_error(exc),
                (
                    " — its IMEI sensor will not be created, and an IMEI sensor "
                    "from an earlier start will stay unavailable, until a read "
                    "succeeds. Further failures for this hub are logged at debug "
                    "level."
                    if first_failure
                    else ""
                ),
            )
            return

        if sim:
            self._sim_info_failed.discard(hub_id)
            self.sim_info[hub_id] = sim
            return

        _LOGGER.debug("Hub %s reported no SIM section; no IMEI sensor will be created", hub_id)

    async def _maybe_refresh_rooms(self, now: float) -> None:
        """Cached once-per-hour room + monitoring_companies + groups refresh
        via the heavier `get_space_snapshot`. Drives `suggested_area` on
        device entries (HA auto-area assignment) and refreshes the group
        + CRA-company snapshot that `list_spaces` doesn't return.
        """
        from dataclasses import replace as dc_replace  # noqa: PLC0415

        rooms_refresh_interval = 3600.0
        if (
            not self._force_snapshot_refresh
            and self._rooms_last_fetch is not None
            and now - self._rooms_last_fetch <= rooms_refresh_interval
        ):
            return
        # Consume the event-triggered override (#266): a space arm/disarm event
        # forces this one snapshot read so per-group panels follow immediately;
        # the next event re-sets it. Consumed even if the snapshot below fails,
        # so a transient error can't pin the integration into snapshotting on
        # every poll — the 300s poll and hourly gate remain the backstops.
        self._force_snapshot_refresh = False
        refreshed_rooms: dict[str, Room] = {}
        for space_id in self.spaces:
            try:
                snapshot = await self._spaces_api.get_space_snapshot(space_id)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Failed to fetch rooms for space %s", space_id, exc_info=True)
                continue
            for room in snapshot.rooms:
                refreshed_rooms[room.id] = room
            current_space = self.spaces.get(space_id)
            if current_space is not None:
                self.spaces[space_id] = dc_replace(
                    current_space,
                    monitoring_companies=snapshot.monitoring_companies,
                    monitoring_companies_loaded=snapshot.monitoring_companies_loaded,
                    groups=snapshot.groups,
                    group_mode_enabled=snapshot.group_mode_enabled,
                    night_mode_enabled=snapshot.night_mode_enabled,
                    chime_status=snapshot.chime_status,
                )
        self.rooms = refreshed_rooms
        self._rooms_last_fetch = now

    def _schedule_hub_device_temperature_refresh(self) -> None:
        """Start the dedicated per-device-temperature refresh timer (#220, #229).

        The refresh runs on its own `async_track_time_interval` timer rather
        than inside `_async_update_data`: on push-heavy hubs every HTS update
        calls `async_set_updated_data`, which resets HA's poll timer, so the
        scheduled poll never fires again after startup and a poll-driven
        refresh is starved (the sensor never materialises). The timer's first
        fire is one full interval out, so we also kick a non-blocking initial
        refresh so the sensor appears within seconds of startup.
        """
        if self._unsub_hub_device_temp is not None:
            return
        self._unsub_hub_device_temp = async_track_time_interval(
            self.hass,
            self._async_refresh_per_device_snapshots,
            timedelta(seconds=HUB_DEVICE_TEMP_REFRESH_INTERVAL),
        )
        self.hass.async_create_task(self._async_refresh_per_device_snapshots())

    async def _async_refresh_per_device_snapshots(self, _now: datetime | None = None) -> None:
        """Timer-driven refresh of values sourced from `StreamHubDevice` (#220, #310).

        Both the per-device internal temperature (#220, #229) and the writable
        siren settings (#310) live only in the rich per-device snapshot, not the
        continuous `StreamLightDevices` stream, so they share one throttled timer.
        """
        await self._async_refresh_hub_device_temperatures(_now)
        await self._async_refresh_siren_settings(_now)

    async def _async_refresh_hub_device_temperatures(self, _now: datetime | None = None) -> None:
        """Fetch + merge per-device internal temperature (#220, #229), timer-driven.

        Sirens (#220) and outdoor curtain PIRs (#229) don't carry a
        `temperature` status in the `StreamLightDevices` stream the way indoor
        motion/door sensors do, so the auto-created temperature sensor never
        appears for them. The value lives in the rich per-device
        `StreamHubDevice` snapshot instead. We pull it for each such device that
        doesn't already have a temperature and merge it into
        `device.statuses["temperature"]` — all `sensor.py` needs to materialise
        the sensor. When anything changed, push it to listeners so the entity
        platform picks it up immediately. The `async_track_time_interval`
        cadence is the throttle; there is no separate rate-limit.
        """
        from dataclasses import replace as dc_replace  # noqa: PLC0415

        from custom_components.aegis_ajax.api.hts.hub_state import (  # noqa: PLC0415
            HTS_TEMPERATURE_DEVICE_TYPES,
        )

        changed = False
        for device_id, device in list(self.devices.items()):
            # Families sourced from HTS 0x02 (sirens #312, Curtain Plus/Base
            # #229) are authoritative there — don't fetch their gRPC board
            # temperature, which is wrong (runs hotter) and a wasted RPC.
            if (
                device.device_type not in HUB_DEVICE_TEMPERATURE_DEVICE_TYPES
                or device.device_type in HTS_TEMPERATURE_DEVICE_TYPES
            ):
                continue
            try:
                temperature = await self._devices_api.get_hub_device_temperature(
                    device.hub_id, device_id
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Failed to fetch temperature for device %s", device_id, exc_info=True)
                continue
            if temperature is None:
                continue
            current = self.devices.get(device_id)
            if current is None:
                continue
            # Refresh on change, not once: the temperature must not freeze at the
            # first reading (#312). An unchanged value is skipped to avoid
            # churning listeners every interval.
            if current.statuses.get("temperature") == temperature:
                continue
            self.devices[device_id] = dc_replace(
                current, statuses={**current.statuses, "temperature": temperature}
            )
            changed = True
        if changed:
            self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    async def _async_refresh_siren_settings(self, _now: datetime | None = None) -> None:
        """Fetch + merge each siren's writable settings (#310), timer-driven.

        Sirens expose their alarm duration and volume level only in the rich
        per-device `StreamHubDevice` snapshot, not the continuous
        `StreamLightDevices` stream, so — like the per-device temperature
        (#220) — a dedicated read merges them into `device.statuses`
        (`siren_alarm_duration` / `siren_volume_level`). Their presence is what
        the `number`/`select` platforms use to materialise the entities. Only
        siren families are streamed; an unchanged snapshot skips the listener
        push so we don't churn every interval.
        """
        changed = False
        for device_id, device in list(self.devices.items()):
            if device.device_type not in SIREN_DEVICE_TYPES:
                continue
            changed = await self._async_fetch_and_merge_siren_settings(device_id) or changed
        if changed:
            self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    async def _async_fetch_and_merge_siren_settings(self, device_id: str) -> bool:
        """Fetch one siren's settings snapshot and merge it into `device.statuses`.

        Shared by the 900 s timer sweep and the post-write confirm read. Returns
        True when a value actually changed (the caller decides when to push to
        listeners). A fetch failure or empty snapshot is swallowed with a debug
        log — both callers treat it as "keep the current value; the timer will
        retry".
        """
        from dataclasses import replace as dc_replace  # noqa: PLC0415

        device = self.devices.get(device_id)
        if device is None:
            return False
        try:
            settings = await self._devices_api.get_hub_device_siren_settings(
                device.hub_id, device_id
            )
        except Exception as exc:  # noqa: BLE001
            # First failure per device is user-visible: its Siren volume and
            # Alarm duration entities sit on `unknown` until this succeeds, and
            # at HA's default level a DEBUG-only line makes that undiagnosable
            # without first asking the reporter to enable debug (#354). Repeats
            # drop to DEBUG so a permanently unreadable siren doesn't spam.
            first_failure = device_id not in self._siren_settings_failed
            self._siren_settings_failed.add(device_id)
            _LOGGER.log(
                logging.WARNING if first_failure else logging.DEBUG,
                "Failed to fetch siren settings for device %s: %s%s",
                device_id,
                self._describe_rpc_error(exc),
                (
                    " — its Siren volume and Alarm duration will show as unknown "
                    "until a read succeeds. Further failures for this device are "
                    "logged at debug level."
                    if first_failure
                    else ""
                ),
                exc_info=True,
            )
            return False
        self._siren_settings_failed.discard(device_id)
        if not settings:
            return False
        current = self.devices.get(device_id)
        if current is None:
            return False
        if all(current.statuses.get(k) == v for k, v in settings.items()):
            return False
        self.devices[device_id] = dc_replace(current, statuses={**current.statuses, **settings})
        return True

    @callback
    def schedule_siren_settings_confirm(self, device_id: str) -> None:
        """Schedule a targeted settings re-read shortly after a successful write.

        The siren-settings write path (#310) updates the hub, but the values are
        only read back on the shared 900 s snapshot timer — so after an accepted
        write the entity kept showing the previous value for up to ~15 minutes,
        indistinguishable in the UI from a rejected write. Re-read just this
        device after a short settle delay so the entity confirms the actual hub
        value within seconds. Deliberately a read-back rather than an optimistic
        set: `UpdateHubDevice` has a real accept-but-inert failure mode on this
        service, so the entity must only ever show what an independent read
        returns. Single-flight per device — a second write while a confirm is
        pending rides the already-scheduled read (worst case the 900 s timer
        corrects it).
        """
        if device_id in self._siren_confirm_pending:
            return
        self._siren_confirm_pending.add(device_id)
        self.hass.async_create_task(self._async_confirm_siren_settings(device_id))

    async def _async_confirm_siren_settings(self, device_id: str) -> None:
        try:
            await asyncio.sleep(SIREN_SETTINGS_CONFIRM_DELAY)
            if await self._async_fetch_and_merge_siren_settings(device_id):
                self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})
        finally:
            self._siren_confirm_pending.discard(device_id)

    @callback
    def schedule_bypass_confirm(self, device_id: str, *, expected: bool) -> None:
        """Schedule an independent read-back after a bypass write (#338).

        `DeviceCommandBypass` has an accept-but-inert failure mode: the hub
        answers success and applies nothing when the account can't set the
        requested deactivation mode. A successful command is therefore not
        evidence of a state change, and an optimistic entity update would
        actively lie about whether a sensor is protecting the property. Re-read
        the device instead and let the switch show whatever the panel really
        says; on a disagreement, warn so the silent no-op is visible in the log
        rather than only in the (unchanged) hardware.

        Single-flight per device — a second write while a confirm is pending
        rides the scheduled read.
        """
        if device_id in self._bypass_confirm_pending:
            return
        self._bypass_confirm_pending.add(device_id)
        self.hass.async_create_task(self._async_confirm_bypass(device_id, expected=expected))

    async def _async_confirm_bypass(self, device_id: str, *, expected: bool) -> None:
        try:
            await asyncio.sleep(BYPASS_CONFIRM_DELAY)
            device = self.devices.get(device_id)
            if device is None:
                return
            space_id = next((s.id for s in self.spaces.values() if s.hub_id == device.hub_id), None)
            if space_id is None:
                return
            try:
                fresh_devices = await self._devices_api.get_devices_snapshot(space_id)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Bypass confirm read failed for device %s", device_id, exc_info=True)
                return
            fresh = next((d for d in fresh_devices if d.id == device_id), None)
            if fresh is None:
                return
            self._handle_devices_snapshot([fresh])
            current = self.devices.get(device_id)
            if current is None:
                return
            actual = is_device_deactivated(current)
            if actual != expected:
                # Symptom only, deliberately no cause. The obvious guess —
                # "the account lacks the rights" — was disproved on hardware
                # (#338): the same account deactivates the very same device
                # from the Ajax app and it takes effect, so only the command
                # path is inert. Do not put a diagnosis back in this string
                # until one is actually established; users paste it into
                # issues and a wrong cause sends them chasing permissions.
                _LOGGER.warning(
                    "Bypass %s for device %s (%s) was accepted by the hub, but a "
                    "read-back still reports the device as %s — the command had no "
                    "effect. Deactivating the device from the Ajax app does work; "
                    "why the command path is ignored is still being investigated, "
                    "see https://github.com/bvis/aegis-hass/issues/338",
                    "enable" if expected else "disable",
                    device_id,
                    device.name,
                    "deactivated" if actual else "active",
                )
        finally:
            self._bypass_confirm_pending.discard(device_id)

    def _schedule_poll_safety_refresh(self) -> None:
        """Start the independent poll safety-net timer (#178).

        On any active hub every HTS network/device update calls
        `async_set_updated_data`, which reschedules HA's built-in poll timer.
        HTS pushes arrive every ~30-60 s — well under `poll_interval` — so the
        scheduled `_async_update_data` is starved and never fires on its own,
        leaving `security_state` and the hourly snapshot refresh
        (rooms/groups/chime/CRA/SIM/firmware) dependent 100% on FCM push with
        no safety net when push is delayed or absent (#178, #239).

        This dedicated `async_track_time_interval` fires on wall-clock time,
        independent of the coordinator's internal scheduler, and requests a
        refresh so `_async_update_data` runs on a fixed cadence regardless of
        HTS chatter. The startup refresh already populated state, so no initial
        kick is needed — the first fire is one interval out by design.

        `self._poll_interval` is already clamped to [MIN, MAX] in `__init__`.
        """
        if self._unsub_poll_safety is not None:
            return
        self._unsub_poll_safety = async_track_time_interval(
            self.hass,
            self._async_poll_safety_refresh,
            timedelta(seconds=self._poll_interval),
        )

    async def _async_poll_safety_refresh(self, _now: datetime | None = None) -> None:
        """Timer-driven safety-net refresh (#178). See `_schedule_poll_safety_refresh`.

        Routes through the public `async_request_refresh` so it reuses the
        whole polled path (`list_spaces` + hourly snapshot gating) and the
        coordinator's debouncer coalesces it with any concurrent refresh.
        """
        await self.async_request_refresh()

    async def _first_startup_init(self) -> None:
        """First-cycle bootstrap: warm devices cache, start persistent
        streams + HTS lifecycle, log a one-line startup summary.

        Warming the device cache (#114) lets entities materialise with
        real data on reload instead of `unavailable` while the streams
        connect. Streams deliver a fresh snapshot via
        `_handle_devices_snapshot` within seconds and overwrite cached
        values.
        """
        self._streams_started = True
        cached_devices: dict[str, Device] | None = None
        if self._devices_cache is not None:
            try:
                cached_devices = await self._devices_cache.async_load()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Failed to load devices cache", exc_info=True)
        if cached_devices:
            self.devices = cached_devices
            # A cache written before the #173 dedup (or before the
            # video_edge sibling first appeared) can carry a stale
            # motion_cam_video_* ghost. Drop it on load if the sibling is
            # also cached; otherwise the first stream snapshot resolves it.
            self._dedupe_video_doorbells()
        else:
            initial_devices: dict[str, Device] = {}
            for space_id in self.spaces:
                space_devices = await self._devices_api.get_devices_snapshot(space_id)
                for device in space_devices:
                    initial_devices[device.id] = device
            self.devices = initial_devices
            if self._devices_cache is not None and self.devices:
                try:
                    await self._devices_cache.async_save(self.devices)
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Failed to persist devices cache", exc_info=True)
        await self._probe_smart_locks_once()
        await self._start_device_streams()
        await self._start_hts()
        self._schedule_hub_device_temperature_refresh()
        self._schedule_poll_safety_refresh()
        self._last_update_success_time = dt_util.utcnow()
        # One-line summary so users debugging "HTS streams: 0/1" or
        # "FCM clients: 0/1" reports (#111) can see at a glance which
        # surfaces are coming up. HTS is async — `_start_hts` schedules
        # the lifecycle task and returns; the "HTS connected" line
        # appears once the task's connect awaits complete.
        _LOGGER.info(
            "Aegis startup: device streams %d/%d started, HTS lifecycle %s",
            len([t for t in self._stream_tasks if not t.done()]),
            len(self.spaces),
            "scheduled" if self._hts_task is not None else "skipped",
        )

    async def _probe_smart_locks_once(self) -> None:
        """#206 Bug B: one-shot read-only probe of `SmartLockService` to
        capture the id the command service expects (the hub-device id we send
        today yields `smart_lock_not_found`). Runs once, only when a lock
        device is present; the probe itself is DEBUG-gated and never raises.
        """
        if self._smart_lock_probe_done:
            return
        self._smart_lock_probe_done = True
        lock_ids_by_space: dict[str, list[str]] = {}
        for device in self.devices.values():
            if device.device_type not in _LOCK_DEVICE_TYPES:
                continue
            space_id = next((s.id for s in self.spaces.values() if s.hub_id == device.hub_id), None)
            if space_id:
                lock_ids_by_space.setdefault(space_id, []).append(device.id)
        for space_id, lock_ids in lock_ids_by_space.items():
            await self._devices_api.probe_smart_locks(space_id, lock_ids)

    async def _maybe_fallback_device_snapshot(self) -> None:
        """Refresh devices from a snapshot when no stream task is running —
        none started, or all exited their retry loop. Live-but-stalled
        transports are handled at the connection layer by gRPC keepalive
        (see `_KEEPALIVE_OPTIONS`): a wedged channel surfaces as an error the
        stream's own reconnect recovers from, rather than being detected here.
        This is a cheap no-op whenever a stream task is alive (the common case).

        Applies the snapshot through `_handle_devices_snapshot` rather than
        replacing `self.devices` wholesale: the wholesale replacement skipped
        every carry-forward that function accumulated (#220 temperature, #339
        tamper, #310 siren settings, #403 readings and battery) and left no
        log line, so with streams down each poll could silently blank the
        same readings #403 fixed on the stream path — one writer was gated,
        this one was not (the #406 trap). The one thing kept from the old
        behavior is removal: this is the resync-from-scratch path, so devices
        the snapshot no longer reports are dropped before the merge.
        """
        streams_healthy = self._stream_tasks and all(not t.done() for t in self._stream_tasks)
        if streams_healthy:
            return
        all_devices: dict[str, Device] = {}
        for space_id in self.spaces:
            space_devices = await self._devices_api.get_devices_snapshot(space_id)
            for device in space_devices:
                all_devices[device.id] = device
        _LOGGER.debug(
            "No live device stream; applying polled fallback snapshot (%d device(s))",
            len(all_devices),
        )
        self.devices = {
            device_id: device
            for device_id, device in self.devices.items()
            if device_id in all_devices
        }
        self._handle_devices_snapshot(list(all_devices.values()), notify_listeners=False)

    async def _maybe_restart_hts(self) -> None:
        """Reap a dead HTS task and re-start the client on the next cycle."""
        if self._hts_task and self._hts_task.done():
            self._handle_hts_disconnect()
        if self._hts_client is None:
            await self._start_hts()

    async def _start_hts(self) -> None:
        """Start HTS in the background — never block the caller.

        The HTS handshake is a TCP connect plus a custom application
        handshake that takes a few seconds in the happy path and up to
        20 s with the auth-handshake timeout from #74. Awaiting it
        directly inside `_async_update_data` extended the integration's
        first refresh past HA's "integration taking too long" boot
        threshold (#112). Wrap the connect-then-listen lifecycle in a
        single background task so the caller returns immediately and
        the listener establishes (or fails and self-reconnects) without
        blocking startup. Hub-network sensors stay `unavailable` for the
        couple of seconds it takes to connect, then become available the
        moment the connection succeeds.
        """
        if self._hts_task is not None and not self._hts_task.done():
            return
        try:
            session = self._client.session
            token_hex = session.session_token
            if not token_hex:
                _LOGGER.warning(
                    "HTS startup skipped — no Ajax session token available. "
                    "Hub network sensors will stay unavailable until authentication "
                    "succeeds. Look earlier in the log for the authentication failure."
                )
                return
            # Pre-create SSL context in executor to avoid blocking event loop
            if HtsClient._ssl_ctx is None:
                import ssl  # noqa: PLC0415

                HtsClient._ssl_ctx = await self.hass.async_add_executor_job(
                    ssl.create_default_context
                )
            self._hts_client = HtsClient(
                login_token=bytes.fromhex(token_hex),
                user_hex_id=session.user_hex_id or "",
                device_id=session.device_id,
                app_label=session.app_label,
            )
        except Exception as exc:
            _LOGGER.warning(
                "HTS pre-connect setup failed (%s) — hub network sensors unavailable",
                exc.__class__.__name__,
                exc_info=True,
            )
            self._hts_client = None
            return
        self._hts_task = asyncio.create_task(self._run_hts_lifecycle())
        self._hts_task.add_done_callback(self._handle_hts_task_done)

    async def _run_hts_lifecycle(self) -> None:
        """Connect, log success, then drive the listen loop until disconnect."""
        if self._hts_client is None:
            return
        try:
            # Seed last-known hub states so a reconnect doesn't reset fields
            # (e.g. externally_powered) to their defaults when the first
            # post-reconnect frame omits their TLV key (#323).
            if self.hub_network:
                self._hts_client.seed_hub_states(self.hub_network)
            result = await self._hts_client.connect()
            _LOGGER.info("HTS connected, %d hub(s)", len(result.hubs))
            self._clear_hts_chronic_failure()
            await self._hts_client.listen(
                on_state_update=self._on_hts_update,
                on_device_kv=self._on_hts_device_kv,
                on_chime_event=self._on_hts_space_event,
                on_hub_event=self._on_hts_hub_event,
            )
        except Exception as exc:
            # Surface at WARNING (#111) — a silent DEBUG made these failures
            # invisible to users debugging "HTS streams: 0/1" reports. The
            # previous behaviour required reproducing with custom debug
            # logging on multiple modules. Keep `exc_info=True` so the full
            # traceback still lands when DEBUG is enabled.
            _LOGGER.warning(
                "HTS connection failed (%s) — hub network sensors will be unavailable. "
                "The integration will retry on the next poll cycle.",
                exc.__class__.__name__,
                exc_info=True,
            )
            self._hts_client = None

    def _on_hts_update(self, hub_id: str, state: HubNetworkState) -> None:
        """Handle hub network state update from HTS."""
        self.hub_network[hub_id] = state
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    def _on_hts_device_kv(
        self,
        hub_id: str,
        device_id_hex: str,
        kv: dict[int, bytes],
        *,
        from_body: bool = False,
    ) -> None:
        """Translate a per-device HTS kv block into `DeviceReadings` (#123).

        `from_body` distinguishes a periodic body snapshot (STATUS_BODY /
        SETTINGS_BODY) from a live per-device delta (STATUS_UPDATE /
        SETTINGS_UPDATE). Only the #348 probe cares so far — for a key that
        appears only in deltas, a first sighting is an event and not a
        baseline. It defaults to False so a caller that doesn't know is treated
        as the more conservative case.

        Called once per non-hub device row inside a STATUS_BODY or
        SETTINGS_BODY. Looks up the device's type from the snapshot the
        gRPC stream populated; non-electrical types are filtered out by
        `parse_device_readings` returning `None`. When a new reading
        arrives that differs from the cached one, mutates
        `coordinator.device_readings` and fires `async_set_updated_data`
        so the sensor entities pick the change up immediately.
        """
        from custom_components.aegis_ajax.api.hts.hub_state import (  # noqa: PLC0415
            parse_device_readings,
        )

        device = self.devices.get(device_id_hex)
        if device is None:
            # A hub-internal space/group security object reporting an arm-flag
            # transition (#284) — nudge the authoritative re-read. Checked before
            # the keyfob path because these objects are never keyfobs.
            if self._maybe_nudge_space_security(hub_id, device_id_hex, kv):
                return
            # Not a gRPC-modeled device — it may be a SpaceControl keyfob, which
            # only ever appears in the HTS SETTINGS_BODY (never in the gRPC
            # snapshot). Classify and surface it; everything else (users,
            # markers) is ignored. See api/hts/keyfobs.py.
            self._handle_keyfob_kv(hub_id, device_id_hex, kv)
            return
        # Per-detector exit/entry delay seconds, 0xAC/0xAD (#454) — only the
        # SETTINGS_BODY row carries them; a row without the keys is left alone.
        self._track_arm_delays(device_id_hex, kv)
        # Case tampering via HTS 0x04/0x0f (#339), for hubs that carry the
        # signal only on the status stream. Runs before anything that can
        # return early so every device family is covered.
        self._maybe_apply_hts_device_tamper(device_id_hex, device, kv)
        # The hub's own bypass state, 0xB7 (#419) — tracked before anything
        # that can return early so every reporting family feeds the
        # deactivation carry-forward gate.
        self._maybe_track_hts_bypass_state(device_id_hex, device, kv)
        # A MultiTransmitter wire input's contact state, 0x33 (#413) — runs
        # after the writers above, so it re-reads the device in case one of
        # them replaced it.
        self._maybe_apply_hts_contact_state(device_id_hex, kv)
        # A modeled SpaceControl's settings row (#311) — read-only, logged
        # before anything that can return early: this hub class never reaches
        # the keyfob path, so this is the only place its row is ever visible.
        self._log_hts_space_control_settings(device_id_hex, device, kv)
        # The same device's activation-flag candidates (#311), which ride the
        # status row rather than the settings row — disjoint from the keys above,
        # hence a separate probe with a change-only contract.
        self._log_hts_space_control_flag_transitions(device_id_hex, device, kv)
        # Button activity-timestamp candidate keys (#348) — read-only, logged
        # before anything that can return early so every device family gets
        # probed; the keys are Button-specific in every capture so far.
        self._log_hts_button_activity_candidates(device_id_hex, device, kv, from_body=from_body)
        # Button control-mode press (#348) — fires the per-device event entity.
        self._maybe_fire_button_press(device_id_hex, device, kv)
        # Internal temperature via HTS 0x02 (#229), for device families with no
        # gRPC temperature source (Curtain Outdoor Plus/Base). Additive: only
        # fills when the device doesn't already carry a temperature, so devices
        # that get it over gRPC are untouched. Returns early if it applied a
        # value (a temperature device isn't also an electrical one).
        if self._maybe_apply_hts_device_temperature(device_id_hex, device, kv):
            return
        readings = parse_device_readings(
            device.device_type,
            kv,
            existing=self.device_readings.get(device_id_hex),
        )
        if readings is None:
            return
        if self.device_readings.get(device_id_hex) == readings:
            return
        self.device_readings[device_id_hex] = readings
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})
        _ = hub_id  # currently unused; kept in the signature for symmetry with on_state_update

    def _maybe_nudge_space_security(
        self, hub_id: str, device_id_hex: str, kv: dict[int, bytes]
    ) -> bool:
        """Nudge the authoritative re-read on a space-security arm-flag flip (#284).

        Hub-internal space/group security objects use low reserved ids
        (00000001..0000000F — six leading zero nibbles, unlike a real Jeweller
        device's random id or a keyfob's 2A.. id) and report the space/group arm
        state on HTS sub-key 0x06 (01 armed / 00 disarmed). A keypad full-arm of a
        group is delivered ONLY as a STATUS_UPDATE flip of this flag: it emits no
        type=0x08 space event (which app arm and keypad night/disarm do) and no FCM
        push on no-FCM installs, so without this the central panel sat on its stale
        state until the 300s poll.

        Returns True when the row is a space-security object — handled here, so the
        caller skips the keyfob path (these objects are never keyfobs). Only a
        *change* of the flag nudges; the first sighting (the boot snapshot is
        already authoritative) and an unchanged flag (re-reported on every 60s
        STATUS_BODY probe) do not, so the snapshot keeps its hourly cadence. The
        flag is never decoded into the panel — same rationale as
        `_on_hts_space_event`: it's a nudge to re-read ground truth, not a state.

        Runs on the event loop (HTS listen task).
        """
        if (
            len(device_id_hex) != 8
            or not device_id_hex.startswith("000000")
            or device_id_hex == "00000000"
            or 0x06 not in kv
            or not kv[0x06]
        ):
            return False
        arm = kv[0x06][0]
        previous = self._space_security_arm_flags.get(device_id_hex)
        self._space_security_arm_flags[device_id_hex] = arm
        if previous is not None and previous != arm:
            _LOGGER.debug(
                "Space security object %s arm flag 0x06 %02X->%02X on hub %s: "
                "requesting authoritative refresh",
                device_id_hex,
                previous,
                arm,
                hub_id,
            )
            self.request_security_snapshot_refresh()
        return True

    def _maybe_track_hts_bypass_state(
        self, device_id_hex: str, device: Device, kv: dict[int, bytes]
    ) -> None:
        """Track the hub's per-device bypass state, HTS `0xB7` (#419).

        Read-mostly by design. The stored value gates the deactivation
        carry-forward in `_handle_devices_snapshot` — see the carry there for
        why a gRPC snapshot's silence is ambiguous. The one write this method
        performs is a WITHDRAWAL: when the hub reports the bypass lifted
        (`00`) for a device whose deactivation statuses exist only because
        that carry preserved them, the carry is taken back on the spot, so it
        can never outlive the hub's own word by more than one status refresh.
        It never creates deactivation state — a `01` for a device the model
        shows as protecting stores the observation and does nothing else
        (#406's lesson: withdraw at the gate that applied the value).
        """
        raw = kv.get(_HTS_BYPASS_STATE_KEY)
        if raw is None:
            return
        deactivated = any(byte != 0 for byte in raw)
        self._hts_bypass_state[device_id_hex] = (deactivated, time.monotonic())
        if deactivated or device_id_hex not in self._hts_carried_deactivation_ids:
            return
        from dataclasses import replace as dc_replace  # noqa: PLC0415

        self._hts_carried_deactivation_ids.discard(device_id_hex)
        # Re-fetch: an earlier handler in this same kv pass (the #339 tamper
        # apply) may have replaced the model object the caller handed us.
        current = self.devices.get(device_id_hex, device)
        stripped = {
            key: value
            for key, value in current.statuses.items()
            if key not in DEACTIVATION_STATUS_KEYS and key != DEACTIVATED_KEY
        }
        self.devices[device_id_hex] = dc_replace(current, statuses=stripped, bypassed=False)
        _LOGGER.debug(
            "Withdrew carried deactivation for %s — the hub reports the bypass lifted",
            device_id_hex,
        )
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    def _hts_confirms_deactivation(self, device_id: str) -> bool:
        """True when a fresh HTS `0xB7` report says the bypass is engaged (#419)."""
        entry = self._hts_bypass_state.get(device_id)
        if entry is None:
            return False
        deactivated, seen_at = entry
        if not deactivated:
            return False
        return (time.monotonic() - seen_at) <= _HTS_BYPASS_STATE_TRUST_WINDOW

    def _log_hts_space_control_settings(
        self, device_id_hex: str, device: Device, kv: dict[int, bytes]
    ) -> None:
        """DEBUG-log a gRPC-modeled SpaceControl's HTS settings row (#311).

        Read-only by design — see `_HTS_SPACE_CONTROL_SETTINGS_KEYS`. Gated by
        device type and not by shape, because the same sub-key numbers mean
        unrelated things on other families.

        The deactivation state goes on the same line because the pairing is
        what makes a capture conclusive: on this class of hub the keyfob
        already has the bypass switch, so a row taken while the panel has it
        deactivated and one taken while it does not are the pair that would
        locate the activation flag — the flag the `Active` sensor guesses at
        on HTS-only hubs. The full key list (numbers only, no values) rides
        along so a capture shows whether the row's shape moved, without
        putting any field's contents in a log. Read the pairing at rest only:
        the bytes are fresh from the row, while `deactivated=`/`kinds=` come
        off the device model, which nothing in the HTS path writes — so on a
        transition the two halves can briefly disagree (#338 measured the
        model catching up ~1 ms after the line had logged).

        Gated on `_HTS_SPACE_CONTROL_GATING_KEYS` — the settings keys *minus*
        `0xC3`. Gating on the full set did not deliver the silence this docstring
        used to claim: `subtype` rides the 60 s status row too, so `present` was
        never empty and the probe logged a line a minute indefinitely, five of
        every six carrying nothing but `subtype` (measured by @wip3out3r, #311).
        The remaining five keys are settings-row-only, so a status row now
        returns early while a settings row still logs every field it carries.
        """
        if device.device_type not in _SPACE_CONTROL_DEVICE_TYPES:
            return
        if not any(key in kv for key in _HTS_SPACE_CONTROL_GATING_KEYS):
            return
        present = {
            name: kv[key].hex()
            for key, name in _HTS_SPACE_CONTROL_SETTINGS_KEYS.items()
            if key in kv
        }
        _LOGGER.debug(
            "HTS SpaceControl probe: device=%s type=%s settings=%s row_keys=%s "
            "deactivated=%s kinds=%s",
            device_id_hex,
            device.device_type,
            present,
            [f"0x{key:02x}" for key in sorted(kv)],
            is_device_deactivated(device),
            device_deactivation_kinds(device),
        )

    def _log_hts_space_control_flag_transitions(
        self, device_id_hex: str, device: Device, kv: dict[int, bytes]
    ) -> None:
        """DEBUG-log a modeled SpaceControl's activation-flag candidates on change (#311).

        Read-only, and change-only by design — see
        `_HTS_SPACE_CONTROL_FLAG_CANDIDATE_KEYS` for why these four keys are
        worth watching on a device class that never reaches the keyfob path.

        The change-only contract is what makes this cost nothing. Every keyfob
        observed so far reads `0x0b == 0x01`, so logging the value on each 60 s
        status row would repeat a constant forever — the same noise this probe's
        sibling was just fixed for. A transition is the sample #311 has always
        lacked: no `inactive` capture exists, because only a CRA admin can
        deactivate a keyfob and we have never had a log spanning one. So an
        active keyfob stays silent indefinitely, and the line appears at the
        exact moment the flag moves.

        A first sighting is recorded silently. The status row re-reports the same
        constant at every poll and after every restart, so treating a first
        sighting as an event would announce a deactivation that never happened —
        the same trap `_maybe_fire_button_press` guards against.

        Runs on the event loop (HTS listen task).
        """
        if device.device_type not in _SPACE_CONTROL_DEVICE_TYPES:
            return
        for key in _HTS_SPACE_CONTROL_FLAG_CANDIDATE_KEYS:
            value = kv.get(key)
            if value is None:
                continue
            current = value.hex()
            cache_key = (device_id_hex, key)
            previous = self._hts_space_control_flags.get(cache_key)
            self._hts_space_control_flags[cache_key] = current
            if previous is None or previous == current:
                continue
            _LOGGER.debug(
                "HTS SpaceControl flag probe: device=%s type=%s key=0x%02X %s -> %s "
                "deactivated=%s kinds=%s",
                device_id_hex,
                device.device_type,
                key,
                previous,
                current,
                is_device_deactivated(device),
                device_deactivation_kinds(device),
            )

    def _log_hts_button_activity_candidates(
        self, device_id_hex: str, device: Device, kv: dict[int, bytes], *, from_body: bool = False
    ) -> None:
        """DEBUG-log transitions of the Button activity-candidate keys (#348).

        Read-only by design — see `_HTS_BUTTON_ACTIVITY_CANDIDATE_KEYS`. Logs
        only when a value *changes*, which is the whole point: the reading to
        falsify is that these keys track supervision pings rather than presses,
        and that shows up as transitions on a Button nobody is touching.

        A first sighting **in a body snapshot** is recorded silently, because
        the snapshot re-reports whatever the last press left behind and would
        otherwise look like a press at every restart.

        A first sighting **in a delta**, for a key absent from a body row we
        have already seen, is logged: such a key lives only in deltas, so there
        is no baseline to swallow and that first sighting *is* the event. Before
        the device's first body row arrives the two cases are indistinguishable,
        so a delta seen that early is still recorded silently rather than
        guessed at. Reported by @wip3out3r, whose first press went unlogged
        because he pressed after a restart but before the key had ever appeared
        in a body — a delta-only key on his hardware.

        Each 4-byte value is also rendered as a big-endian Unix epoch, so the log
        can be compared against the wall-clock moment of a press without anyone
        having to convert hex by hand. Values that are not 4 bytes, or that fall
        outside a plausible epoch window, are reported as `raw` only — a key that
        carries something else on another firmware must not be dressed up as a
        timestamp.

        Silent when the row carries none of the keys, so device families that
        don't report them add no log noise.
        """
        # Recorded before the per-key loop, and for every device: a body row
        # that carries *none* of the keys is exactly what proves they are
        # delta-only on this firmware.
        body_seen = device_id_hex in self._hts_button_activity_bodies_seen
        if from_body:
            self._hts_button_activity_bodies_seen.add(device_id_hex)
        for key in _HTS_BUTTON_ACTIVITY_CANDIDATE_KEYS:
            value = kv.get(key)
            if value is None:
                continue
            current = value.hex()
            cache_key = (device_id_hex, key)
            previous = self._hts_button_activity.get(cache_key)
            self._hts_button_activity[cache_key] = current
            if previous == current:
                continue
            if previous is None and (from_body or not body_seen):
                # Baselining, not an event: either this is the snapshot itself,
                # or no snapshot has arrived yet to tell us the key is
                # delta-only.
                continue
            _LOGGER.debug(
                "HTS button probe: device=%s type=%s key=0x%02X %s -> %s (%s)",
                device_id_hex,
                device.device_type,
                key,
                previous,
                current,
                _describe_hts_epoch(value),
            )

    def _maybe_fire_button_press(
        self, device_id_hex: str, device: Device, kv: dict[int, bytes]
    ) -> None:
        """Fire the Button's press event when its activity epoch moves (#348).

        Gated on device type: the same sub-key carries unrelated data on other
        families, so a global read would fire phantom presses off a door
        sensor's roller-shutter flag. See `BUTTON_PRESS_DEVICE_TYPES`.

        Three guards, all of them there to avoid inventing a press:

        - **A first sighting never fires.** The snapshot re-reports whatever the
          last press left behind — 20 hours old in the reporter's capture — so
          firing on it would announce a press at every restart.
        - **The value must be a plausible epoch**, or the key is carrying
          something else on this firmware and we want no part of it.
        - **Time must move forward.** An equal value is the snapshot repeating
          itself; a lower one is not something a press can produce, so treat it
          as a new baseline rather than an event.

        Deliberately consumes both snapshot and delta rows. The deltas catch
        every individual press, while the snapshot samples once a minute and
        collapses a burst to its last value — so a delta the client missed can
        still surface via the snapshot, at the cost of a slightly older
        timestamp. Both paths share one cache, so whichever arrives first fires
        and the other sees no change.

        Runs on the event loop (HTS listen task), so touching entity state here
        needs no thread marshalling.
        """
        if device.device_type not in BUTTON_PRESS_DEVICE_TYPES:
            return
        raw = kv.get(_HTS_BUTTON_PRESS_KEY)
        if raw is None or len(raw) != 4:
            return
        seconds = int.from_bytes(raw, "big")
        if not _HTS_EPOCH_MIN <= seconds <= _HTS_EPOCH_MAX:
            return
        previous = self._button_press_epochs.get(device_id_hex)
        self._button_press_epochs[device_id_hex] = seconds
        if previous is None or seconds <= previous:
            return
        pressed_at = dt_util.utc_from_timestamp(seconds)
        _LOGGER.debug(
            "Button %s pressed at %s (0x39 %s -> %s)",
            device_id_hex,
            pressed_at.isoformat(),
            previous,
            seconds,
        )
        entity = self._button_event_entities.get(device_id_hex)
        if entity is None:
            # The Button exists but its event entity hasn't been added yet (or
            # the user disabled it). Nothing to do — the epoch is cached either
            # way, so the next press still fires.
            return
        entity.handle_event(
            BUTTON_PRESS_EVENT_TYPE,
            {"device_id": device_id_hex, "pressed_at": pressed_at.isoformat()},
        )

    def register_button_event_entity(self, device_id: str, entity: object) -> None:
        """Register a per-device Button press event entity (#348)."""
        self._button_event_entities[device_id] = entity

    def _maybe_apply_hts_device_tamper(
        self, device_id_hex: str, device: Device, kv: dict[int, bytes]
    ) -> None:
        """Route the HTS case-tamper keys onto the shared `tamper` status (#339).

        Only for the families in `_HTS_TAMPER_ROUTED_DEVICE_TYPES` (#406) —
        elsewhere the keys carry something else and the probe is the whole
        contribution. Within those, either key reading `01` means tampered
        and both reading `00` means intact. Any other value is ignored: only
        `00`/`01` were ever observed on a family we route, so a different
        byte means the key carries something else on that firmware, and
        guessing would raise a phantom tamper alert.

        Clearing mirrors the gRPC delta path: `tamper` only goes away once no
        granular gRPC source (`lid_opened`, `smart_bracket_unlocked`,
        `case_drilling`) is active either, so two independent sources can't
        cancel each other out. `_HTS_CASE_TAMPER_KEY` records that the value
        came from the status stream, which is what lets
        `_handle_devices_snapshot` carry it across a gRPC snapshot that has no
        tamper field to report.

        The DEBUG line is deliberately unconditional on the values, and sits
        ahead of the family gate so an unrouted family is still probed: on a
        hub that carries this signal only over HTS, "candidates flip while the
        gRPC-sourced statuses stay silent" is exactly the evidence a reporter
        needs to capture — and a candidate that *doesn't* move is how #406 was
        told apart from a real tamper.
        """
        from dataclasses import replace as dc_replace  # noqa: PLC0415

        present = {key: kv[key] for key in _HTS_TAMPER_CANDIDATE_KEYS if key in kv}
        if not present:
            return
        _LOGGER.debug(
            "HTS tamper probe: device=%s type=%s candidates=%s tamper_status=%s",
            device_id_hex,
            device.device_type,
            {f"0x{key:02X}": value.hex() for key, value in present.items()},
            device.statuses.get("tamper"),
        )
        if device.device_type not in _HTS_TAMPER_ROUTED_DEVICE_TYPES:
            # Not routed — but an earlier version may have left an HTS-sourced
            # tamper on this device, and `self.devices` is restored from a
            # persisted cache, so it survives every restart. Withdrawing it here
            # is the only way out: the branch below that normally clears it is
            # the one this gate skips. Costs nothing on a clean device, which is
            # every device on a fresh install.
            self._withdraw_hts_case_tamper(device_id_hex, device)
            return

        states = [value == b"\x01" for value in present.values() if value in (b"\x00", b"\x01")]
        if not states:
            return
        tampered = any(states)

        if tampered:
            if device.statuses.get(_HTS_CASE_TAMPER_KEY) and device.statuses.get("tamper"):
                return  # unchanged value re-reported on the routine refresh
            statuses = {**device.statuses, "tamper": True, _HTS_CASE_TAMPER_KEY: True}
        else:
            if not device.statuses.get(_HTS_CASE_TAMPER_KEY):
                return  # intact, and no HTS-sourced tamper to withdraw
            statuses = _without_hts_case_tamper(device.statuses)

        self.devices[device_id_hex] = dc_replace(device, statuses=statuses)
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    def _maybe_apply_hts_contact_state(self, device_id_hex: str, kv: dict[int, bytes]) -> None:
        """Route HTS `0x33` onto `external_contact_open` for MT wire inputs (#413).

        `0x33=00` is "left its rest position" — the app's "Alerte" — and maps
        to open; `0x33=01` is at rest ("OK") and maps to closed. The proto
        enum (CONTACT_DISRUPTED=1, CONTACT_NORMAL=2) does NOT describe this
        byte: firmware sends a bool whose values contradict the enum's names,
        and reading `01` as "disrupted" is the inversion both field reports
        saw on 1.17.1-beta.4/5 (a closed NC door showed "open"). Anything but
        `00`/`01` — the enum's `02`/`03`, whose names are no longer evidence,
        and the `80` sentinel the hub bursts on a settings write — says
        nothing about the contact's position and leaves the stored state
        untouched.

        ⚠️ **The hub applies the input's NO/NC polarity before reporting, so
        do NOT add polarity handling here.** Settled by Taknok's four-state
        capture with 2-minute dwells (2026-08-25, `1.17.1-beta.6`):

            NC + circuit open   -> 00    NO + circuit closed -> 00
            NC + circuit closed -> 01    NO + circuit open   -> 01

        Both modes agree on the *meaning*: `00` whenever the wire is away
        from the position the configured mode calls rest. Since NO/NC is
        chosen so the loop sits at rest with the door shut, `00` is always
        "door open" and the sensor reads correctly in both wirings.

        The same capture identified `0x4A` on the input's SETTINGS object as
        the NO/NC mode itself (`01` = NC, `00` = NO). Deliberately unused:
        XOR-ing it into `0x33` would recover the RAW circuit state, which
        inverts against the door on NO wiring — the opposite of what an
        `opening` sensor should say. Guarded by
        `test_the_no_nc_mode_key_is_not_consumed`.

        Family-gated hard to `wire_input_mt`: the same byte means
        `external_sensor_power_broken` on the MultiTransmitter itself and
        other things on other families (#406's lesson). Re-reads the device
        from `self.devices` because an earlier applier in the kv chain may
        have replaced the instance the caller holds. Runs on the event loop
        (HTS listen task).
        """
        from dataclasses import replace as dc_replace  # noqa: PLC0415

        device = self.devices.get(device_id_hex)
        if device is None or device.device_type != "wire_input_mt":
            return
        raw = kv.get(_HTS_CONTACT_STATE_KEY)
        if raw == b"\x00":
            contact_open = True
        elif raw == b"\x01":
            contact_open = False
        else:
            return
        if device.statuses.get(_EXTERNAL_CONTACT_OPEN_KEY) is contact_open:
            return
        self.devices[device_id_hex] = dc_replace(
            device, statuses={**device.statuses, _EXTERNAL_CONTACT_OPEN_KEY: contact_open}
        )
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    def _withdraw_hts_case_tamper(self, device_id_hex: str, device: Device) -> None:
        """Drop an HTS-sourced case tamper this build no longer stands behind.

        Used when the device's family is not in `_HTS_TAMPER_ROUTED_DEVICE_TYPES`
        but the stored statuses still carry the marker — an upgrade from a build
        that routed every family (#406). Silent and non-churning when there is
        nothing to withdraw, which is the normal case.

        Runs on the event loop (HTS listen task).
        """
        from dataclasses import replace as dc_replace  # noqa: PLC0415

        if not device.statuses.get(_HTS_CASE_TAMPER_KEY):
            return
        _LOGGER.debug(
            "Withdrawing an HTS-sourced case tamper from device=%s type=%s: "
            "this build does not read those keys as a tamper on that family (#406)",
            device_id_hex,
            device.device_type,
        )
        self.devices[device_id_hex] = dc_replace(
            device, statuses=_without_hts_case_tamper(device.statuses)
        )
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    def _maybe_apply_hts_device_temperature(
        self, device_id_hex: str, device: Device, kv: dict[int, bytes]
    ) -> bool:
        """Merge an HTS-sourced internal temperature into a device (#229).

        For device families that report temperature only over HTS (Curtain
        Outdoor Plus/Base — their gRPC `HubDevice` message has no
        `device_temperature` field), decode sub-key 0x02 and merge it into
        `device.statuses["temperature"]`, which is all `sensor.py` needs to
        materialise the temperature sensor. Mirrors the gRPC per-device-temp
        merge in `_async_refresh_hub_device_temperatures`; the carry-forward in
        the device-snapshot path keeps it across gRPC refreshes.

        Safe and live: only applies to families in `HTS_TEMPERATURE_DEVICE_TYPES`
        (0x02 is authoritative for them) and refreshes on change so the reading
        tracks the device rather than freezing at the first value (#312). An
        unchanged 0x02 (re-reported on every STATUS_BODY probe) is a no-op.
        Returns True when a value was applied. Runs on the loop (HTS listen task).
        """
        from dataclasses import replace as dc_replace  # noqa: PLC0415

        from custom_components.aegis_ajax.api.hts.hub_state import (  # noqa: PLC0415
            HTS_TEMPERATURE_DEVICE_TYPES,
            parse_device_temperature_c,
        )

        if device.device_type not in HTS_TEMPERATURE_DEVICE_TYPES:
            return False
        temperature = parse_device_temperature_c(device.device_type, kv)
        if temperature is None:
            # Gated type but no usable 0x02 — log once-per-row so a reporter on
            # DEBUG can confirm whether the firmware sends it at all (#229).
            _LOGGER.debug(
                "Device %s (%s): no usable HTS temperature in 0x02 (kv keys=%s)",
                device_id_hex,
                device.device_type,
                sorted(kv),
            )
            return False
        if device.statuses.get("temperature") == temperature:
            # Unchanged value re-reported on a routine probe — no listener churn.
            return False
        self.devices[device_id_hex] = dc_replace(
            device, statuses={**device.statuses, "temperature": temperature}
        )
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})
        return True

    def _handle_keyfob_kv(self, hub_id: str, device_id_hex: str, kv: dict[int, bytes]) -> None:
        """Classify a non-gRPC SETTINGS_BODY row as a SpaceControl keyfob.

        Reached only for rows the gRPC snapshot does not model. Keyfobs are
        HTS-only on some hubs but not all: a hub that reports the keyfob as an
        `ObjectType.space_control` device gives it a normal modeled device, and
        its row never arrives here — see `api/hts/keyfobs.py` and
        `_log_hts_space_control_settings` (#311). A recognised
        keyfob is stored in `self.keyfobs` and announced via `SIGNAL_NEW_DEVICE`
        so the binary_sensor platform can add its device + experimental "Active"
        sensor at runtime. Rows that merely *look* like keyfobs are DEBUG-logged
        (name redacted) so a user with a CRA-deactivated keyfob can share a log
        and let us confirm the still-unverified active flag — see keyfobs.py.
        """
        from custom_components.aegis_ajax.api.hts.keyfobs import (  # noqa: PLC0415
            looks_like_keyfob_candidate,
            parse_keyfob,
        )
        from custom_components.aegis_ajax.notification import _redact_printable  # noqa: PLC0415

        if looks_like_keyfob_candidate(device_id_hex, kv):
            _LOGGER.debug(
                "Keyfob candidate %s on hub %s: %s",
                device_id_hex,
                hub_id,
                {f"0x{k:02x}": _redact_printable(v) for k, v in sorted(kv.items())},
            )

        keyfob = parse_keyfob(device_id_hex, hub_id, kv)
        if keyfob is None:
            return
        if self.keyfobs.get(device_id_hex) == keyfob:
            return
        is_new = device_id_hex not in self.keyfobs
        self.keyfobs[device_id_hex] = keyfob
        if is_new:
            async_dispatcher_send(self.hass, SIGNAL_NEW_DEVICE, device_id_hex)
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    def request_security_snapshot_refresh(self) -> None:
        """Nudge the authoritative security re-read, group states included.

        Forces the next refresh to re-read group security states and
        `night_mode_enabled` via `get_space_snapshot` (#266) — arming or
        disarming can flip several groups at once plus the night flag, none
        of which the lighter `list_spaces` poll carries — and routes the
        re-read through the dedicated short-cooldown debouncer (#270), NOT
        the shared 10 s request-refresh debouncer (whose cooldown coalesced a
        rapid arm→disarm→arm burst into one trailing re-read that lagged the
        panel ~10 s).

        Called from the HTS 0x08 space-event path and, since #284/#287, from
        FCM security events: a push tells (at most) one group's new state, so
        a scenario / keypad / fob action that changes other groups or night
        mode would otherwise wait for the hourly snapshot (~9 min lag
        observed live on a scenario-driven group arm).

        Runs on the event loop.
        """
        self._force_snapshot_refresh = True
        self.hass.async_create_task(self._security_refresh_debouncer.async_call())

    def _on_hts_space_event(self, hub_id: str, payload_hex: str, candidate: int | None) -> None:
        """React to a hub `type=0x08` space event pushed over HTS in real time.

        One event frame carries different space changes, told apart by the state
        byte (params[3]):
        - **Chime toggle** (#239): 0x38 on / 0x39 off → decoded to `chime_status`
          directly. Chime is idempotent and low-stakes, so decoding from the
          event is safe and instant.
        - **Anything else** (arm / disarm / night / exit-delay …, #258): used
          only as a real-time NUDGE to re-read the authoritative `security_state`
          over gRPC. The byte is deliberately NOT decoded as state — arm-initiated
          ≠ armed, a disarm during the exit delay emits no event, and events can
          be dropped on an HTS reconnect, so a decoded state can stick wrong on an
          alarm panel (observed live 2026-06-06). The re-read goes through a
          dedicated short-cooldown debouncer (#270), so a burst of frames
          coalesces into a re-read ~1s after the last frame — fast enough that
          the panel doesn't visibly lag — instead of the up-to-10s lag the
          shared request-refresh debouncer caused; the 300s poll backstops a
          missed nudge.

        Runs on the event loop (HTS listen task).
        """
        from dataclasses import replace as dc_replace  # noqa: PLC0415

        space_id = next(
            (sid for sid, s in self.spaces.items() if s.hub_id == hub_id),
            None,
        )
        if space_id is None:
            return

        status = _CHIME_EVENT_STATE_BYTE.get(candidate) if candidate is not None else None
        if status is None:
            # Non-chime space event (#258) — re-read the authoritative state
            # instead of trusting the byte. Covers arm/disarm/night and any
            # unmapped transition; never shows a decoded-but-wrong state.
            _LOGGER.debug(
                "Space HTS event for space %s (byte %s): requesting authoritative refresh "
                "(payload=%s)",
                space_id,
                "none" if candidate is None else f"0x{candidate:02X}",
                payload_hex,
            )
            self.request_security_snapshot_refresh()
            return

        _LOGGER.debug(
            "Chime HTS event for space %s: state byte 0x%02X -> %s (decoded from stream)",
            space_id,
            candidate,
            status.name,
        )
        current = self.spaces.get(space_id)
        if current is None or current.chime_status == status:
            return
        self.spaces[space_id] = dc_replace(current, chime_status=status)
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    def _handle_hts_task_done(self, task: asyncio.Task[None]) -> None:
        """Clear stale HTS state when the listen task exits."""
        if task.cancelled():
            return
        with contextlib.suppress(Exception):
            task.result()
        self._handle_hts_disconnect()

    @property
    def is_hts_alive(self) -> bool:
        """True while the HTS stream client is in place (#146).

        Sensors whose semantics demand a live stream (operational alerts
        like `mains_power`) should AND their `available` with this so
        they go `unavailable` during transient HTS dropouts even though
        the cached value is still present. Diagnostic sensors (IP, SSID,
        signal level, electrical readings) ignore this and rely on the
        cached value until the next delta refreshes it.
        """
        return self._hts_client is not None

    async def async_request_manual_refresh(self, hub_id: str) -> None:
        """Trigger a one-shot STATUS_BODY refresh for `hub_id`, rate-limited.

        Backs the per-hub refresh button. The integration already runs a
        periodic refresh per hub every `STATUS_REFRESH_INTERVAL` seconds,
        so the button exists to bridge that gap when a user wants fresh
        readings *now* (e.g. immediately after switching an appliance
        on). To stop an automation looped on `button.press` from
        hammering the hub, two presses on the same hub within
        `MANUAL_REFRESH_INTERVAL` seconds raise `HomeAssistantError`
        with a translated message telling the user how long to wait.

        Raises:
            HomeAssistantError: HTS isn't connected, or another manual
                refresh is still inside the rate-limit window.
        """
        if self._hts_client is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="manual_refresh_hts_unavailable",
            )
        now = time.monotonic()
        last = self._last_manual_refresh.get(hub_id, 0.0)
        elapsed = now - last
        if elapsed < MANUAL_REFRESH_INTERVAL:
            wait = max(1, int(MANUAL_REFRESH_INTERVAL - elapsed))
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="manual_refresh_rate_limited",
                translation_placeholders={"seconds": str(wait)},
            )
        self._last_manual_refresh[hub_id] = now
        await self._hts_client.request_full_status(hub_id)

    def _handle_hts_disconnect(self, *, reconnect: bool = True) -> None:
        """Drop the live HTS client; preserve cached snapshots (#146).

        The hub keeps every value we cache here (network state, per-device
        electrical readings) across our socket outage, so wiping them on
        every transient reconnect blanked sensors for 5+ minutes even
        though the cached value was still the truth. We now keep them in
        place — the next STATUS_UPDATE / STATUS_BODY after reconnect
        refreshes them as deltas arrive. The only deliberate exception
        is `mains_power` (the operational alert), which opts into
        `unavailable` via `is_hts_alive` on its `available` property.
        """
        self._hts_task = None
        self._hts_client = None
        # Broadcast so coordinator entities re-evaluate `available` —
        # `mains_power` flips to unavailable here; everything else keeps
        # its cached state and renders the same value as before.
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})
        # Track the first failure of an otherwise-healthy run so we can
        # raise a Repair after a sustained outage. Successful reconnect
        # clears it via `_clear_hts_chronic_failure`. Uses time.monotonic
        # so the call works from sync task-done callbacks too.
        if self._hts_first_failure_at is None:
            self._hts_first_failure_at = time.monotonic()
        else:
            elapsed = time.monotonic() - self._hts_first_failure_at
            if elapsed >= _HTS_CHRONIC_FAILURE_SECONDS:
                for space_id in self._space_ids:
                    async_register_hts_chronic_failure(
                        self.hass,
                        space_id=space_id,
                        minutes_failing=int(elapsed // 60),
                    )
        if reconnect:
            # Schedule reconnect on next poll cycle rather than immediate retry
            _LOGGER.debug("HTS disconnected; will reconnect on next poll cycle")

    def _clear_hts_chronic_failure(self) -> None:
        """Called when HTS reconnects successfully — drop any active Repair."""
        if self._hts_first_failure_at is None:
            return
        self._hts_first_failure_at = None
        for space_id in self._space_ids:
            async_clear_hts_chronic_failure(self.hass, space_id=space_id)

    def _update_hub_offline_repairs(self, now: float) -> None:
        """Raise / clear `hub_offline_24h` Repairs based on current snapshot."""
        for space_id, space in self.spaces.items():
            if space.connection_status == ConnectionStatus.OFFLINE:
                first_seen = self._first_offline_at.setdefault(space_id, now)
                hours = (now - first_seen) / 3600
                if hours >= _HUB_OFFLINE_THRESHOLD_HOURS:
                    async_register_hub_offline(
                        self.hass,
                        space_id=space_id,
                        hub_name=space.name,
                        hours_offline=int(hours),
                    )
            else:
                if space_id in self._first_offline_at:
                    self._first_offline_at.pop(space_id, None)
                    async_clear_hub_offline(self.hass, space_id=space_id)

    async def _start_device_streams(self) -> None:
        """Start persistent device streams for all spaces."""
        for space_id in self._space_ids:

            def _on_snapshot(
                devices: list[Device],
                *,
                complete: bool = False,
                _space_id: str = space_id,
            ) -> None:
                # The stream's leading snapshot is the space's complete
                # device list — the one moment membership can be resynced
                # (#422). Single-device refreshes pass complete=False.
                self._handle_devices_snapshot(
                    devices, complete_for_space=_space_id if complete else None
                )

            try:
                task = await self._devices_api.start_device_stream(
                    space_id,
                    on_devices_snapshot=_on_snapshot,
                    on_status_update=self._handle_status_update,
                    on_device_removed=self._handle_device_removed,
                )
                self._stream_tasks.append(task)
                _LOGGER.debug("Device stream started for space %s", space_id)
            except Exception:
                _LOGGER.exception("Failed to start device stream for space %s", space_id)

    def apply_push_security_state(self, space_id: str, new_state: Any) -> None:  # noqa: ANN401
        """Apply a security_state derived from an FCM arm/disarm push event.

        Updates `coordinator.spaces[space_id]` in-memory and immediately notifies
        listeners via `async_set_updated_data`, so the alarm panel reflects the
        change without waiting for the next poll cycle. No-ops when:
        - the space is unknown to the coordinator,
        - the new state matches the current state,
        - an HA-initiated optimistic state is still active for that space (the
          push is treated as racing with our own command and ignored to avoid
          flicker; the next poll reconciles).
        """
        import time  # noqa: PLC0415
        from dataclasses import replace as dc_replace  # noqa: PLC0415

        from custom_components.aegis_ajax.const import SecurityState  # noqa: PLC0415

        space = self.spaces.get(space_id)
        if space is None:
            return
        # A push DISARM acknowledges any live intrusion alarm (#426) —
        # dropped before the no-change/optimistic early returns below, so a
        # disarm that races our own optimistic write still clears it.
        if new_state in (SecurityState.DISARMED, SecurityState.NONE):
            self.alarmed_space_ids.discard(space_id)
        # `time.monotonic()` is the same source `asyncio.BaseEventLoop.time()`
        # uses for the optimistic-state expiry stored from arm/disarm callsites.
        now = time.monotonic()
        opt = self._optimistic_space_states.get(space_id)
        if opt and opt[0] > now:
            return
        # Track night-mode activity off the push itself: the debounced lite
        # re-read that follows reports PARTIALLY_ARMED in group mode, and the
        # flag is what keeps the panel on `armed_night` (#284). PARTIALLY_ARMED
        # pushes are ambiguous (night mode vs subset of groups) and leave the
        # flag untouched; the hourly snapshot reconciles.
        night_mode_enabled = space.night_mode_enabled
        if new_state == SecurityState.NIGHT_MODE:
            night_mode_enabled = True
        elif new_state in (SecurityState.ARMED, SecurityState.DISARMED):
            night_mode_enabled = False
        if space.security_state == new_state and space.night_mode_enabled == night_mode_enabled:
            return
        self.spaces[space_id] = dc_replace(
            space, security_state=new_state, night_mode_enabled=night_mode_enabled
        )
        self.sync_delay_overlays()
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    def apply_push_group_security_state(
        self,
        space_id: str,
        group_id: str,
        new_state: Any,  # noqa: ANN401
    ) -> None:
        """Apply a per-group security_state derived from an FCM push event (#148).

        Mirrors `apply_push_security_state` for the per-group
        `AjaxGroupAlarmControlPanel` entities: updates the matching `Group`
        within `space.groups` and notifies listeners. No-ops when the space
        or group is unknown, or the new state matches the existing one. The
        space-level state is deliberately not changed here — arming a single
        group doesn't imply the whole space is armed; that resolves on the
        next poll.
        """
        from dataclasses import replace as dc_replace  # noqa: PLC0415

        space = self.spaces.get(space_id)
        if space is None or not space.groups:
            return
        target = next((g for g in space.groups if g.id == group_id), None)
        if target is None or target.security_state == new_state:
            return
        new_groups = tuple(
            dc_replace(g, security_state=new_state) if g.id == group_id else g for g in space.groups
        )
        self.spaces[space_id] = dc_replace(space, groups=new_groups)
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    def note_intrusion_alarm(self, space_id: str) -> None:
        """Mark a space as in-alarm from an intrusion push (#426).

        The served `SecurityState` cannot express "alarm firing", so the
        panel's `triggered` is a client-side overlay: set here from the
        `intrusion_alarm` / `intrusion_alarm_confirmed` push, shown while the
        space is in any armed state, and held until the space is next seen
        DISARMED — via the push path (`apply_push_security_state`), an
        HA-initiated disarm, or any poll re-read (`_drop_cleared_alarms`).
        Runs on the event loop (marshalled from the FCM worker thread by the
        caller). Unknown spaces are ignored — a push for a space this entry
        doesn't manage must not create state.
        """
        if space_id not in self.spaces:
            return
        self.alarmed_space_ids.add(space_id)
        # An alarm ends any running delay (#454): `triggered` wins, and the
        # entry delay it most likely grew out of is over.
        self._clear_delay_overlay(space_id)
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    def _drop_cleared_alarms(self) -> None:
        """Drop the `triggered` overlay for spaces now seen DISARMED (#426).

        Called after every full spaces rebuild so any server observation of a
        disarm — polled or nudged — acknowledges the alarm. A space that is
        no longer tracked has nothing to overlay either.
        """
        from custom_components.aegis_ajax.const import SecurityState  # noqa: PLC0415

        for space_id in list(self.alarmed_space_ids):
            space = self.spaces.get(space_id)
            if space is None or space.security_state in (
                SecurityState.DISARMED,
                SecurityState.NONE,
            ):
                self.alarmed_space_ids.discard(space_id)

    # ------------------------------------------------------------------
    # Exit / entry delays as panel states (#454)
    # ------------------------------------------------------------------

    def _track_arm_delays(self, device_id_hex: str, kv: dict[int, bytes]) -> None:
        """Remember a detector's 0xAC/0xAD/0xAE delay settings when a row carries them."""
        delays = parse_arm_delays(kv)
        if delays is None or self._device_arm_delays.get(device_id_hex) == delays:
            return
        self._device_arm_delays[device_id_hex] = delays
        _LOGGER.debug(
            "Device %s delays: leaving %ds, entering %ds, night %s",
            device_id_hex,
            delays.arm_delay_seconds,
            delays.alarm_delay_seconds,
            delays.night_mode,
        )

    def _space_arm_delays(self, space_id: str) -> list[ArmDelays]:
        space = self.spaces.get(space_id)
        if space is None:
            return []
        return [
            delays
            for device_id, delays in self._device_arm_delays.items()
            if (device := self.devices.get(device_id)) is not None and device.hub_id == space.hub_id
        ]

    def space_exit_delay_seconds(self, space_id: str, *, night_mode: bool = False) -> int:
        """Longest "Delay when leaving" among the space's detectors (0 = none).

        In night mode only detectors whose delays apply at night (0xAE) count;
        a detector whose flag was never seen is left out, so a night arm never
        shows a delay we cannot vouch for.
        """
        return max(
            (
                d.arm_delay_seconds
                for d in self._space_arm_delays(space_id)
                if not night_mode or d.night_mode
            ),
            default=0,
        )

    def _space_entry_delay_seconds(self, space_id: str) -> int:
        return max((d.alarm_delay_seconds for d in self._space_arm_delays(space_id)), default=0)

    def sync_delay_overlays(self) -> None:
        """Reconcile the delay overlays with the current space states (#454).

        Called after every write of a space's `security_state` — poll, push,
        the panel's optimistic write — so the detection does not depend on
        which path carried the arm. A disarm (however observed) ends any
        running delay; a transition from disarmed to any armed state starts
        `arming` when at least one detector on the space has an exit delay.
        The very first observation of a space is a baseline, not a transition:
        a restart while armed shows the hub's plain state.
        """
        from custom_components.aegis_ajax.const import SecurityState  # noqa: PLC0415

        disarmed = (SecurityState.DISARMED, SecurityState.NONE)
        for space_id in list(self.delay_overlays):
            if space_id not in self.spaces:
                self._clear_delay_overlay(space_id)
        for space_id, space in self.spaces.items():
            previous = self._last_security_states.get(space_id)
            current = space.security_state
            self._last_security_states[space_id] = current
            if not self.delay_panel_states:
                continue
            if current in disarmed:
                if self._clear_delay_overlay(space_id):
                    self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})
                continue
            if previous is None or previous not in disarmed:
                continue
            seconds = self.space_exit_delay_seconds(
                space_id, night_mode=current == SecurityState.NIGHT_MODE
            )
            if seconds <= 0:
                continue
            _LOGGER.debug(
                "Space %s armed with a %ds exit delay: showing `arming` until the hub "
                "reports the delay complete",
                space_id,
                seconds,
            )
            self._set_delay_overlay(space_id, DelayKind.ARMING, seconds, from_hub=False)

    def _on_hts_hub_event(self, event: HubEvent) -> None:
        """React to a hub-sourced HTS event (#454). Runs on the event loop."""
        from custom_components.aegis_ajax.const import SecurityState  # noqa: PLC0415

        space_id = next((sid for sid, s in self.spaces.items() if s.hub_id == event.hub_id), None)
        if space_id is None:
            return
        _LOGGER.debug(
            "Hub %s event 0x%02X (space %s): ts=%s expires=%s keys=%s",
            event.hub_id,
            event.code,
            space_id,
            event.hub_ts,
            event.expires_at,
            {f"0x{k:02X}": v.hex() for k, v in event.values.items()},
        )
        if not self.delay_panel_states:
            return
        overlay = self.delay_overlays.get(space_id)
        if event.code == HUB_EVENT_EXIT_DELAY_COMPLETE:
            if overlay is None or overlay.kind is not DelayKind.ARMING:
                return
            _LOGGER.debug("Space %s: hub reports the exit delay complete", space_id)
            self._clear_delay_overlay(space_id)
            self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})
            return
        if event.code == HUB_EVENT_ENTRY_DELAY_STARTED:
            space = self.spaces[space_id]
            if space.security_state in (SecurityState.DISARMED, SecurityState.NONE):
                return
            seconds = event.delay_seconds
            from_hub = seconds is not None
            if seconds is None:
                seconds = self._space_entry_delay_seconds(space_id)
            if seconds <= 0:
                _LOGGER.debug(
                    "Space %s: entry delay started but its length is unknown — not shown",
                    space_id,
                )
                return
            _LOGGER.debug(
                "Space %s: entry delay started, %ds (%s)",
                space_id,
                seconds,
                "hub expiry" if from_hub else "settings fallback",
            )
            self._set_delay_overlay(space_id, DelayKind.PENDING, seconds, from_hub=from_hub)

    def _set_delay_overlay(
        self, space_id: str, kind: DelayKind, seconds: int, *, from_hub: bool
    ) -> None:
        """Install an overlay that self-clears after `seconds` plus grace."""
        self._cancel_delay_timer(space_id)
        overlay = DelayOverlay(
            kind=kind,
            ends_at=dt_util.utcnow() + timedelta(seconds=seconds),
            from_hub=from_hub,
        )
        self.delay_overlays[space_id] = overlay
        self._delay_overlay_cancels[space_id] = self.hass.loop.call_later(
            seconds + DELAY_OVERLAY_GRACE_SECONDS,
            self._expire_delay_overlay,
            space_id,
            overlay,
        )
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    def _expire_delay_overlay(self, space_id: str, overlay: DelayOverlay) -> None:
        """Timer callback: drop `overlay` if it is still the one installed."""
        if self.delay_overlays.get(space_id) is not overlay:
            return
        _LOGGER.debug("Space %s: %s overlay expired on its timer", space_id, overlay.kind.value)
        self._clear_delay_overlay(space_id)
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    def _cancel_delay_timer(self, space_id: str) -> None:
        cancel = self._delay_overlay_cancels.pop(space_id, None)
        if cancel is not None:
            cancel.cancel()

    def _clear_delay_overlay(self, space_id: str) -> bool:
        """Drop a space's overlay and its timer. Returns True when one existed."""
        self._cancel_delay_timer(space_id)
        return self.delay_overlays.pop(space_id, None) is not None

    def set_chime_optimistic(self, space_id: str, *, enable: bool) -> None:
        """Optimistically reflect a hub Chime toggle we just issued (#239).

        The hub-wide Chime status only rides the hourly `get_space_snapshot`
        path — `list_spaces` (LiteSpace) doesn't carry it — so a plain
        `async_request_refresh` after the command wouldn't move the switch for
        up to an hour. Write the expected state in-memory and notify listeners
        so the toggle is reflected immediately; the next snapshot reconciles
        with the hub's authoritative value (and catches app-side changes).
        No-op for an unknown space.
        """
        from dataclasses import replace as dc_replace  # noqa: PLC0415

        space = self.spaces.get(space_id)
        if space is None:
            return
        new_status = ChimeStatus.ENABLED if enable else ChimeStatus.CAN_BE_ENABLED
        if space.chime_status == new_status:
            return
        self.spaces[space_id] = dc_replace(space, chime_status=new_status)
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    def _handle_devices_snapshot(
        self,
        devices: list[Device],
        *,
        notify_listeners: bool = True,
        complete_for_space: str | None = None,
    ) -> None:
        """Handle initial snapshot or full device snapshot update from stream.

        When `complete_for_space` names a space, `devices` is that space's
        complete device list and membership is resynced after the merge:
        devices of that hub the list no longer contains are dropped from
        tracking (#422).

        Emits one DEBUG line per snapshot, which is the observable this path was
        missing entirely (#403). @wip3out3r went looking for it and found there
        was nothing to find at any log level: `Device stream started for space`
        fires once per coordinator lifetime so an in-task reconnect never emits
        it, a clean server-side close falls through to the backoff with no line,
        this function had no logger call of its own, and the base coordinator's
        `Manually updated aegis_ajax data` fires on every update path and cannot
        identify a snapshot. So a full snapshot could arrive, replace every
        device and empty a fleet of readings without leaving a trace — which is
        exactly what happened to him, three seconds after a transport close, and
        why the cause of the *invocation* is still open. The line reports what was
        carried forward so the next occurrence is decisive rather than inferred.
        """
        carried_reading_count = 0
        carried_battery_count = 0
        carried_deactivation_count = 0
        for device in devices:
            existing = self.devices.get(device.id)
            # An HTS-sourced case tamper (#339) has no counterpart in this
            # stream — on the hubs that need it the snapshot carries no tamper
            # field at all — so a fresh snapshot would drop the sensor back to
            # off until the next 60 s status refresh. Carry it forward; the HTS
            # `00` is what withdraws it.
            #
            # Only for families this build actually routes (#406). Devices are
            # restored from a persisted cache, so an upgrade from a build that
            # routed every family arrives with the marker already set — carrying
            # that forward would re-raise, every snapshot, a tamper nothing can
            # withdraw any more.
            if (
                existing is not None
                and existing.statuses.get(_HTS_CASE_TAMPER_KEY)
                and device.device_type in _HTS_TAMPER_ROUTED_DEVICE_TYPES
                and "tamper" not in device.statuses
            ):
                from dataclasses import replace as dc_replace  # noqa: PLC0415

                device = dc_replace(
                    device,
                    statuses={
                        **device.statuses,
                        "tamper": True,
                        _HTS_CASE_TAMPER_KEY: True,
                    },
                )
            # Siren settings (#310) likewise come from the per-device snapshot,
            # not this stream — carry the merged values forward so a fresh light
            # snapshot doesn't wipe them (and the number/select entities) until
            # the next timer fire.
            if existing is not None and device.device_type in SIREN_DEVICE_TYPES:
                carried = {
                    key: existing.statuses[key]
                    for key in (SIREN_ALARM_DURATION_KEY, SIREN_VOLUME_LEVEL_KEY)
                    if key not in device.statuses and key in existing.statuses
                }
                if carried:
                    from dataclasses import replace as dc_replace  # noqa: PLC0415

                    device = dc_replace(device, statuses={**device.statuses, **carried})
            # Measurements the snapshot left out (#403). Last of the carry-forward
            # blocks on purpose: the specific ones above own their keys, and this
            # only ever fills what is still missing after them.
            if existing is not None:
                from dataclasses import replace as dc_replace  # noqa: PLC0415

                carried_readings = {
                    key: existing.statuses[key]
                    for key in _SNAPSHOT_CARRY_FORWARD_STATUS_KEYS
                    if key not in device.statuses and key in existing.statuses
                }
                if carried_readings:
                    carried_reading_count += len(carried_readings)
                    device = dc_replace(device, statuses={**device.statuses, **carried_readings})
                # Battery is a `Device` field rather than a status, so no status
                # carry can reach it — and it is the worst-affected reading for
                # exactly the reason it needs its own line here.
                if device.battery is None and existing.battery is not None:
                    carried_battery_count += 1
                    device = dc_replace(device, battery=existing.battery)
                # Deactivation statuses are deliberately NOT in the blanket
                # carry list above: on the update path a genuine reactivation
                # arrives as an explicit REMOVE op, but here absence is the
                # only signal there is — and #403 measured a degraded snapshot
                # whose silence meant nothing (three deactivated sensors read
                # as protecting for four hours, #419). The hub's own bypass
                # state (HTS 0xB7, hardware-validated in #338) disambiguates:
                # carry only while a fresh report says the bypass is still
                # engaged. A genuine reactivation reads `00` there and the
                # clear goes through unchanged, as it does for devices with no
                # report at all (family doesn't send it / HTS quiet beyond the
                # trust window). `_maybe_track_hts_bypass_state` withdraws the
                # carry the moment the hub reports the bypass lifted.
                if not any(key in device.statuses for key in DEACTIVATION_STATUS_KEYS):
                    had_deactivation = existing.bypassed or any(
                        existing.statuses.get(key) for key in DEACTIVATION_STATUS_KEYS
                    )
                    if had_deactivation and self._hts_confirms_deactivation(device.id):
                        carried_deactivation = {
                            key: existing.statuses[key]
                            for key in (*DEACTIVATION_STATUS_KEYS, DEACTIVATED_KEY)
                            if key in existing.statuses
                        }
                        device = dc_replace(
                            device,
                            statuses={**device.statuses, **carried_deactivation},
                            bypassed=existing.bypassed or device.bypassed,
                        )
                        self._hts_carried_deactivation_ids.add(device.id)
                        carried_deactivation_count += 1
                    else:
                        # Cleared (or nothing to clear) — any earlier carry is
                        # no longer what the model's state rests on.
                        self._hts_carried_deactivation_ids.discard(device.id)
                else:
                    # The snapshot itself reports deactivation — gRPC-fresh,
                    # so an earlier carry is superseded.
                    self._hts_carried_deactivation_ids.discard(device.id)
            self.devices[device.id] = device
        # A complete per-space snapshot is authoritative for membership: a
        # device of this hub the list no longer contains was deleted
        # panel-side, possibly while we were away (#422). Drop it from
        # tracking so its entities stop claiming state and its registry card
        # becomes deletable via `async_remove_config_entry_device`. The
        # registry entry is deliberately NOT removed here: absence is weaker
        # evidence than the stream's explicit REMOVE — a parse-skip absents a
        # live device (#383) — and a wrong registry delete costs the user's
        # area/name customizations. The hub itself never rides on absence,
        # whatever the list says.
        if complete_for_space is not None:
            space = self.spaces.get(complete_for_space)
            hub_id = space.hub_id if space is not None else None
            if hub_id:
                snapshot_ids = {device.id for device in devices}
                stale_ids = [
                    device_id
                    for device_id, device in self.devices.items()
                    if device.hub_id == hub_id
                    and device_id not in snapshot_ids
                    and not device.device_type.startswith("hub")
                ]
                for device_id in stale_ids:
                    del self.devices[device_id]
                    self._hts_carried_deactivation_ids.discard(device_id)
                if stale_ids:
                    _LOGGER.info(
                        "%d device(s) no longer in the hub's device list for "
                        "space %s; dropped from tracking — their Home Assistant "
                        "device entries can now be deleted from the device page "
                        "(#422)",
                        len(stale_ids),
                        complete_for_space,
                    )
            else:
                _LOGGER.debug(
                    "Complete snapshot for unknown space %s — membership resync skipped",
                    complete_for_space,
                )
        # `DevicesApi` dedups video-doorbell twins per snapshot, but the merge
        # above only ever *adds* keys — a `motion_cam_video_*` ghost that was
        # warm-started from the cache before its `video_edge_*` sibling
        # appeared would never be removed, so it survives every restart and
        # keeps bubbling its `malfunctions=1` to the space counter (#173).
        # Re-run the dedup across the whole device set now that this snapshot
        # may have brought the sibling in.
        self._dedupe_video_doorbells()
        _LOGGER.debug(
            "Device snapshot applied: %d device(s) replaced, carried forward "
            "%d reading(s) and %d battery value(s)",
            len(devices),
            carried_reading_count,
            carried_battery_count,
        )
        # Separate line on purpose — the one above is grepped by field
        # instrumentation (#403) and its shape must stay stable.
        if carried_deactivation_count:
            _LOGGER.debug(
                "Deactivation state carried across snapshot for %d device(s) "
                "on the hub's own bypass report (#419)",
                carried_deactivation_count,
            )
        # The polled fallback runs inside `_async_update_data`, whose return
        # value the coordinator broadcasts anyway — a second notification
        # from here would be redundant.
        if notify_listeners:
            self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})
        # Refresh the persisted cache so the next restart can warm-start
        # from real data instead of the previous boot's snapshot (#114).
        # Debounced — bursts of stream snapshots coalesce into one write.
        if self._devices_cache is not None:
            self._devices_cache.async_schedule_save(self.devices)

    def _dedupe_video_doorbells(self) -> None:
        """Re-apply the video-doorbell dedup across `self.devices` and evict
        any dropped ghost from HA's device registry.

        `DevicesApi._dedupe_video_doorbells` is the source of truth for which
        twin to drop; here we apply it to the merged device set (not a single
        snapshot) so a ghost that entered `self.devices` via the warm-start
        cache is removed once its `video_edge_*` sibling shows up. Devices the
        dedup drops are also removed from the device registry so their card
        and entities disappear without the user having to delete them by hand
        (the original #173 complaint — HA won't let you delete a device an
        active integration still provides).
        """
        current = list(self.devices.values())
        deduped, aliases = devices_parser._dedupe_video_doorbells(current)
        self._devices_api.doorbell_twin_aliases.update(aliases)
        if len(deduped) == len(current):
            return
        kept_ids = {d.id for d in deduped}
        dropped = [d for d in current if d.id not in kept_ids]
        self.devices = {d.id: d for d in deduped}
        device_reg = dr.async_get(self.hass)
        for ghost in dropped:
            reg_device = async_get_registered_device(device_reg, (DOMAIN, ghost.id), self.entry_id)
            if reg_device is not None:
                _LOGGER.info(
                    "Removing duplicate video-doorbell ghost %s (%s) from the "
                    "device registry — a video_edge sibling now represents it (#173)",
                    ghost.id,
                    ghost.device_type,
                )
                device_reg.async_remove_device(reg_device.id)

    def hub_registry_id(self, hub_id: str) -> str | None:
        """The hub's device-registry entry id, for children's `via_device_id` (#444).

        None when the hub has no registry entry yet (a hub that appeared after
        setup); the child is then created without the link rather than
        rejected, and gets it on the next reload.
        """
        entry = async_get_registered_device(
            dr.async_get(self.hass), (DOMAIN, hub_id), self.entry_id
        )
        return entry.id if entry is not None else None

    def _handle_device_removed(self, device_id: str) -> None:
        """Handle the stream's explicit device REMOVE (#422).

        Deleting a device in the Ajax app arrives as a `snapshot_update`
        with `update_type=REMOVE`. That is an affirmative server statement —
        unlike absence from a possibly-degraded snapshot (#419) — so it is
        the one signal trusted to delete the device everywhere: coordinator
        state, the warm-start cache, and the HA device registry (HA cascades
        the entity-registry cleanup). If it ever fired wrongly, the next
        snapshot re-adds the device and its entities return on reload.
        """
        device = self.devices.pop(device_id, None)
        self._hts_carried_deactivation_ids.discard(device_id)
        device_reg = dr.async_get(self.hass)
        reg_device = async_get_registered_device(device_reg, (DOMAIN, device_id), self.entry_id)
        if reg_device is not None:
            device_reg.async_remove_device(reg_device.id)
        if device is None and reg_device is None:
            _LOGGER.debug("Stream REMOVE for untracked device %s — nothing to drop", device_id)
            return
        _LOGGER.info(
            "Device %s (%s) was removed on the panel side; removed from Home Assistant (#422)",
            device_id,
            device.device_type if device is not None else "not tracked",
        )
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})
        if self._devices_cache is not None:
            self._devices_cache.async_schedule_save(self.devices)

    def _handle_status_update(self, device_id: str, status_name: str, data: dict[str, Any]) -> None:
        """Handle real-time status update from the persistent stream.

        data contains {"op": int} where 1=ADD, 2=UPDATE, 3=REMOVE.
        """
        device = self.devices.get(device_id)
        if not device:
            _LOGGER.debug("Status update for unknown device %s (status=%s)", device_id, status_name)
            return

        op = data.get("op", 2)
        new_statuses = dict(device.statuses)

        key = _STATUS_KEY_MAP.get(status_name, status_name)
        _LOGGER.debug(
            "Status update: device=%s status=%s key=%s op=%s",
            device_id,
            status_name,
            key,
            op,
        )

        if op == 3:  # REMOVE
            new_statuses.pop(key, None)
            for sub_key in _STATUS_EXTRA_KEYS.get(status_name, ()):
                new_statuses.pop(sub_key, None)
        elif "values" in data:
            new_statuses.update(data["values"])
        elif "value" in data:
            new_statuses[key] = data["value"]
        elif status_name in ("wire_input_status", "transmitter_status") and "is_alert" in data:
            # Respect the actual alert boolean so the entity toggles back to
            # off when the wired contact closes (op=UPDATE with is_alert=False).
            # Both oneofs map to the same `wire_input_alert` key via
            # `_STATUS_KEY_MAP`.
            new_statuses[key] = bool(data["is_alert"])
            if "alarm_type" in data:
                new_statuses["wire_input_alarm_type"] = data["alarm_type"]
        else:  # ADD (1) or UPDATE (2)
            new_statuses[key] = True

        # Case-tampering signals also drive the shared `tamper` key the
        # per-device tamper sensor binds to (#339). On REMOVE, only clear it
        # once no other tamper source remains active (lid could still be open
        # while the bracket delta clears, etc.).
        if status_name in _TAMPER_SOURCE_KEYS:
            if op == 3:
                if not any(new_statuses.get(k) for k in _TAMPER_SOURCE_KEYS.values()):
                    new_statuses.pop("tamper", None)
            else:
                new_statuses["tamper"] = True

        # Same fold for the deactivation statuses (#338): the bypass switch
        # binds to the shared `DEACTIVATED_KEY`, so a device deactivated from
        # the Ajax app while HA is running must reach it through this path too
        # — not only through the next full snapshot. On REMOVE the shared key
        # only clears once no other deactivation mode is still in force.
        if status_name in DEACTIVATION_STATUS_KEYS:
            if op == 3:
                if not any(new_statuses.get(k) for k in DEACTIVATION_STATUS_KEYS):
                    new_statuses.pop(DEACTIVATED_KEY, None)
            else:
                new_statuses[DEACTIVATED_KEY] = True
            # gRPC-fresh deactivation info — the model's state no longer
            # rests on a #419 snapshot carry, so the withdraw path must
            # not touch it.
            self._hts_carried_deactivation_ids.discard(device.id)

        updated = DeviceModel(
            id=device.id,
            hub_id=device.hub_id,
            name=device.name,
            device_type=device.device_type,
            room_id=device.room_id,
            group_id=device.group_id,
            state=device.state,
            malfunctions=device.malfunctions,
            bypassed=device.bypassed,
            statuses=new_statuses,
            battery=device.battery,
        )
        self.devices[device.id] = updated
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    def register_event_entity(self, space_id: str, entity: object) -> None:
        """Register an event entity for a space."""
        self._event_entities[space_id] = entity

    def set_persistent_notifier(self, notifier: AjaxPersistentNotifier | None) -> None:
        """Attach the persistent-notification manager (2.2), or None to disable."""
        self._persistent_notifier = notifier

    def notify_persistent_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Forward a dispatched event to the persistent-notification manager.

        No-op when the feature is disabled. The manager owns the event-type
        filtering, so the event-entity dispatch path can call this
        unconditionally. Best-effort: a notification failure must never break
        event dispatch or state updates.
        """
        notifier = self._persistent_notifier
        if notifier is None:
            return
        try:
            notifier.notify(event_type, data)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Persistent notification for %s failed", event_type, exc_info=True)

    def fire_push_event(self, space_id: str, event_type: str, data: dict[str, Any]) -> None:
        """Dispatch a push event to the corresponding event entity."""
        entity = self._event_entities.get(space_id)
        if entity is not None:
            entity.handle_event(event_type, data)
        else:
            _LOGGER.debug("No event entity for space %s", space_id)

    def register_device_event_entity(self, device_id: str, entity: object) -> None:
        """Register a per-device doorbell event entity (#173)."""
        self._device_event_entities[device_id] = entity

    def fire_push_device_event(self, device_id: str, event_type: str, data: dict[str, Any]) -> bool:
        """Dispatch a push event to a per-device event entity (#173).

        Returns True when a matching device entity handled it, so the caller
        can tell whether the event landed on the device card (vs only the
        hub-level event entity).
        """
        entity = self._device_event_entities.get(device_id)
        if entity is None:
            return False
        entity.handle_event(event_type, data)
        return True

    def apply_push_device_motion(self, device_id: str) -> None:
        """Flip a device's `motion_detected` status on from an FCM motion push.

        Video doorbells (and other video-edge devices) only report motion over
        FCM — never in the gRPC snapshot — so their `motion` binary_sensor
        stayed `off` forever. This sets `motion_detected=True` immediately,
        records the detection time, and schedules an auto-off after
        `MOTION_PUSH_AUTO_OFF_SECONDS` so the sensor self-clears like a PIR
        detector. No-ops for unknown devices. (#173)
        """
        import time  # noqa: PLC0415
        from dataclasses import replace as dc_replace  # noqa: PLC0415

        from homeassistant.helpers.event import async_call_later  # noqa: PLC0415

        device = self.devices.get(device_id)
        if device is None:
            return
        new_statuses = dict(device.statuses)
        new_statuses["motion_detected"] = True
        new_statuses["motion_detected_at"] = int(time.time())
        self.devices[device_id] = dc_replace(device, statuses=new_statuses)
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

        # Re-trigger extends the window: cancel a pending auto-off first.
        cancel = self._motion_off_cancels.pop(device_id, None)
        if cancel is not None:
            cancel()
        # The action MUST be a HA `@callback`: async_call_later classifies a
        # plain sync function as an executor job and runs it in a worker thread,
        # where `_clear_device_motion`'s `async_set_updated_data` would write
        # entity state off-loop (the RuntimeError storm in #173 on beta.8).
        self._motion_off_cancels[device_id] = async_call_later(
            self.hass,
            MOTION_PUSH_AUTO_OFF_SECONDS,
            callback(lambda _now: self._clear_device_motion(device_id)),
        )

    def _clear_device_motion(self, device_id: str) -> None:
        """Reset a device's `motion_detected` status to off (auto-off). (#173)"""
        from dataclasses import replace as dc_replace  # noqa: PLC0415

        self._motion_off_cancels.pop(device_id, None)
        device = self.devices.get(device_id)
        if device is None or not device.statuses.get("motion_detected"):
            return
        new_statuses = dict(device.statuses)
        new_statuses["motion_detected"] = False
        self.devices[device_id] = dc_replace(device, statuses=new_statuses)
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    async def async_start_push_notifications(
        self,
        *,
        fcm_project_id: str = "",
        fcm_app_id: str = "",
        fcm_api_key: str = "",
        fcm_sender_id: str = "",
        entry_id: str = "",
        app_label: str = "",
        disable_push_warning: bool = False,
    ) -> None:
        """Start FCM push notification listener."""
        from custom_components.aegis_ajax.notification import (
            AjaxNotificationListener,  # noqa: PLC0415
        )

        self._notification_listener = AjaxNotificationListener(
            hass=self.hass,
            coordinator=self,
            fcm_project_id=fcm_project_id,
            fcm_app_id=fcm_app_id,
            fcm_api_key=fcm_api_key,
            fcm_sender_id=fcm_sender_id,
            entry_id=entry_id,
            app_label=app_label,
            disable_push_warning=disable_push_warning,
        )
        await self._notification_listener.async_start()

    async def async_shutdown(self) -> None:
        # Stop the siren-temperature refresh timer (#220)
        if self._unsub_hub_device_temp is not None:
            self._unsub_hub_device_temp()
            self._unsub_hub_device_temp = None

        # Stop the poll safety-net timer (#178)
        if self._unsub_poll_safety is not None:
            self._unsub_poll_safety()
            self._unsub_poll_safety = None

        # Cancel any pending security-event re-read (#270)
        self._security_refresh_debouncer.async_shutdown()

        # Cancel all stream tasks
        for task in self._stream_tasks:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._stream_tasks.clear()

        # Stop HTS
        if self._hts_task and not self._hts_task.done():
            self._hts_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._hts_task
        if self._hts_client:
            await self._hts_client.close()

        if self._notification_listener:
            await self._notification_listener.async_stop()
        await self._client.close()
