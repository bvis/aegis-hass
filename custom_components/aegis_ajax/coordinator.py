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
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from custom_components.aegis_ajax.api import devices_parser
from custom_components.aegis_ajax.api.devices import DevicesApi
from custom_components.aegis_ajax.api.hts.client import HtsClient
from custom_components.aegis_ajax.api.hub_object import (
    HubFirmwareUpdateInfo,
    HubObjectApi,
    SimCardInfo,
)
from custom_components.aegis_ajax.api.media import MediaApi
from custom_components.aegis_ajax.api.models import Device as DeviceModel
from custom_components.aegis_ajax.api.security import SecurityApi
from custom_components.aegis_ajax.api.session import AuthenticationError
from custom_components.aegis_ajax.api.spaces import SpacesApi
from custom_components.aegis_ajax.const import (
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    HUB_DEVICE_TEMP_REFRESH_INTERVAL,
    HUB_DEVICE_TEMPERATURE_DEVICE_TYPES,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    MOTION_PUSH_AUTO_OFF_SECONDS,
    SIGNAL_NEW_DEVICE,
    ChimeStatus,
    ConnectionStatus,
)
from custom_components.aegis_ajax.device_cache import DevicesCache
from custom_components.aegis_ajax.repairs import (
    async_clear_hts_chronic_failure,
    async_clear_hub_offline,
    async_register_hts_chronic_failure,
    async_register_hub_offline,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

    from custom_components.aegis_ajax.api.client import AjaxGrpcClient
    from custom_components.aegis_ajax.api.hts.hub_state import (
        DeviceReadings,
        HubNetworkState,
    )
    from custom_components.aegis_ajax.api.hts.keyfobs import Keyfob
    from custom_components.aegis_ajax.api.models import Device, Room, Space
    from custom_components.aegis_ajax.notification import AjaxNotificationListener

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
    ) -> None:
        poll_interval = max(MIN_POLL_INTERVAL, min(MAX_POLL_INTERVAL, poll_interval))
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=poll_interval)
        )
        self._poll_interval = poll_interval
        self._client = client
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
        # Pending hub firmware update keyed by hub_id (#updates). Absent
        # entry = the hub reports no pending update OR the streamHubObject
        # call hasn't completed yet. Refreshed on the same hourly cycle
        # as `sim_info`. Read-only; the integration never calls the
        # install RPC even though the proto exposes one.
        self.hub_firmware_updates: dict[str, HubFirmwareUpdateInfo] = {}
        self._notification_listener: AjaxNotificationListener | None = None
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
        # Per-device internal temperature (#220 sirens, #229 outdoor curtain
        # PIRs) — refreshed on a dedicated timer (`async_track_time_interval`),
        # NOT the poll cycle. On push-heavy hubs every HTS update resets HA's
        # poll timer, so the scheduled poll never fires again after startup; a
        # poll-driven refresh would be starved.
        self._unsub_hub_device_temp: CALLBACK_TYPE | None = None
        # Independent poll safety-net timer (#178). On active hubs every HTS
        # update reschedules HA's built-in poll timer faster than
        # `poll_interval`, starving the scheduled `_async_update_data`; this
        # dedicated timer drives a periodic refresh regardless of HTS chatter.
        self._unsub_poll_safety: CALLBACK_TYPE | None = None
        # HTS client for hub network data (ethernet, wifi, gsm, power)
        self._hts_client: HtsClient | None = None
        self._hts_task: asyncio.Task[None] | None = None
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
        _LOGGER.debug("Logging in to Ajax (fresh session)")
        await self._client.login()
        token = self._client.session.session_token
        user_hex_id = self._client.session.user_hex_id
        if self._on_session_persist and token and user_hex_id:
            try:
                self._on_session_persist(token, user_hex_id)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Failed to persist refreshed session", exc_info=True)

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
        if self._client.session.is_authenticated:
            return
        try:
            await self._login_and_persist()
        except AuthenticationError as err:
            self.update_interval = timedelta(minutes=30)
            _LOGGER.error("Authentication failed: %s — triggering reauth.", err)
            raise ConfigEntryAuthFailed(str(err)) from err
        configured = max(MIN_POLL_INTERVAL, min(MAX_POLL_INTERVAL, self._poll_interval))
        self.update_interval = timedelta(seconds=configured)

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
                # `unavailable` for the rest of the hour.
                if previous.groups or previous.group_mode_enabled:
                    s = dc_replace(
                        s,
                        groups=previous.groups,
                        group_mode_enabled=previous.group_mode_enabled,
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
        for space in self.spaces.values():
            if not space.hub_id:
                continue
            if space.hub_id not in self.sim_info:
                sim = await self._hub_object_api.get_sim_info(space.hub_id)
                if sim:
                    self.sim_info[space.hub_id] = sim
            fw = await self._hub_object_api.get_firmware_info(space.hub_id)
            if fw is None:
                self.hub_firmware_updates.pop(space.hub_id, None)
            else:
                self.hub_firmware_updates[space.hub_id] = fw
        self._sim_info_last_fetch = now

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
            self._async_refresh_hub_device_temperatures,
            timedelta(seconds=HUB_DEVICE_TEMP_REFRESH_INTERVAL),
        )
        self.hass.async_create_task(self._async_refresh_hub_device_temperatures())

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

        changed = False
        for device_id, device in list(self.devices.items()):
            if (
                device.device_type not in HUB_DEVICE_TEMPERATURE_DEVICE_TYPES
                or "temperature" in device.statuses
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
            self.devices[device_id] = dc_replace(
                current, statuses={**current.statuses, "temperature": temperature}
            )
            changed = True
        if changed:
            self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

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
        """
        streams_healthy = self._stream_tasks and all(not t.done() for t in self._stream_tasks)
        if streams_healthy:
            return
        all_devices: dict[str, Device] = {}
        for space_id in self.spaces:
            space_devices = await self._devices_api.get_devices_snapshot(space_id)
            for device in space_devices:
                all_devices[device.id] = device
        self.devices = all_devices

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
            result = await self._hts_client.connect()
            _LOGGER.info("HTS connected, %d hub(s)", len(result.hubs))
            self._clear_hts_chronic_failure()
            await self._hts_client.listen(
                on_state_update=self._on_hts_update,
                on_device_kv=self._on_hts_device_kv,
                on_chime_event=self._on_hts_space_event,
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

    def _on_hts_device_kv(self, hub_id: str, device_id_hex: str, kv: dict[int, bytes]) -> None:
        """Translate a per-device HTS kv block into `DeviceReadings` (#123).

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
            # Not a gRPC-modeled device — it may be a SpaceControl keyfob, which
            # only ever appears in the HTS SETTINGS_BODY (never in the gRPC
            # snapshot). Classify and surface it; everything else (users,
            # markers) is ignored. See api/hts/keyfobs.py.
            self._handle_keyfob_kv(hub_id, device_id_hex, kv)
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

    def _handle_keyfob_kv(self, hub_id: str, device_id_hex: str, kv: dict[int, bytes]) -> None:
        """Classify a non-gRPC SETTINGS_BODY row as a SpaceControl keyfob.

        Keyfobs are HTS-only — they never appear in the gRPC device snapshot, so
        they reach this path (where `self.devices` has no entry). A recognised
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
          alarm panel (observed live 2026-06-06). `async_request_refresh` is
          debounced, so a burst of frames coalesces into one re-read; the 300s
          poll backstops a missed nudge.

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
            # Force the next refresh to re-read group security states too (#266):
            # arming/disarming a single group changes only per-group state, which
            # the lighter `list_spaces` poll doesn't carry. Without this the group
            # panel would lag until the hourly snapshot when push is off.
            self._force_snapshot_refresh = True
            self.hass.async_create_task(self.async_request_refresh())
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
            try:
                task = await self._devices_api.start_device_stream(
                    space_id,
                    on_devices_snapshot=self._handle_devices_snapshot,
                    on_status_update=self._handle_status_update,
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

        space = self.spaces.get(space_id)
        if space is None:
            return
        # `time.monotonic()` is the same source `asyncio.BaseEventLoop.time()`
        # uses for the optimistic-state expiry stored from arm/disarm callsites.
        now = time.monotonic()
        opt = self._optimistic_space_states.get(space_id)
        if opt and opt[0] > now:
            return
        if space.security_state == new_state:
            return
        self.spaces[space_id] = dc_replace(space, security_state=new_state)
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

    def _handle_devices_snapshot(self, devices: list[Device]) -> None:
        """Handle initial snapshot or full device snapshot update from stream."""
        for device in devices:
            # Per-device temperature (#220, #229) comes from a separate
            # per-device RPC, not this stream, so a fresh snapshot would wipe
            # the value we merged in `_async_refresh_hub_device_temperatures`
            # and leave the sensor `unknown` until the next timer fire. Carry the
            # previously-merged temperature forward (it's slow-moving; the
            # periodic refresh still updates it).
            existing = self.devices.get(device.id)
            if (
                existing is not None
                and device.device_type in HUB_DEVICE_TEMPERATURE_DEVICE_TYPES
                and "temperature" not in device.statuses
                and "temperature" in existing.statuses
            ):
                from dataclasses import replace as dc_replace  # noqa: PLC0415

                device = dc_replace(
                    device,
                    statuses={
                        **device.statuses,
                        "temperature": existing.statuses["temperature"],
                    },
                )
            self.devices[device.id] = device
        # `DevicesApi` dedups video-doorbell twins per snapshot, but the merge
        # above only ever *adds* keys — a `motion_cam_video_*` ghost that was
        # warm-started from the cache before its `video_edge_*` sibling
        # appeared would never be removed, so it survives every restart and
        # keeps bubbling its `malfunctions=1` to the space counter (#173).
        # Re-run the dedup across the whole device set now that this snapshot
        # may have brought the sibling in.
        self._dedupe_video_doorbells()
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
            reg_device = device_reg.async_get_device(identifiers={(DOMAIN, ghost.id)})
            if reg_device is not None:
                _LOGGER.info(
                    "Removing duplicate video-doorbell ghost %s (%s) from the "
                    "device registry — a video_edge sibling now represents it (#173)",
                    ghost.id,
                    ghost.device_type,
                )
                device_reg.async_remove_device(reg_device.id)

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
