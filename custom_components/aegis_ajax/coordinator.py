"""Data update coordinator for Ajax Security."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.aegis_ajax.api.devices import DevicesApi
from custom_components.aegis_ajax.api.hts.client import HtsClient
from custom_components.aegis_ajax.api.hub_object import HubObjectApi, SimCardInfo
from custom_components.aegis_ajax.api.media import MediaApi
from custom_components.aegis_ajax.api.models import Device as DeviceModel
from custom_components.aegis_ajax.api.security import SecurityApi
from custom_components.aegis_ajax.api.session import AuthenticationError
from custom_components.aegis_ajax.api.spaces import SpacesApi
from custom_components.aegis_ajax.const import (
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

    from custom_components.aegis_ajax.api.client import AjaxGrpcClient
    from custom_components.aegis_ajax.api.hts.hub_state import HubNetworkState
    from custom_components.aegis_ajax.api.models import Device, Room, Space
    from custom_components.aegis_ajax.notification import AjaxNotificationListener

_LOGGER = logging.getLogger(__name__)

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
}

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
        self._notification_listener: AjaxNotificationListener | None = None
        self._stream_tasks: list[asyncio.Task[None]] = []
        self._streams_started: bool = False
        self._event_entities: dict[str, Any] = {}
        self.last_photo_urls: dict[str, str] = {}
        # space_id -> (expiry_time, security_state)
        self._optimistic_space_states: dict[str, tuple[float, Any]] = {}
        # SIM info is mostly static — cache and refresh once per hour
        self._sim_info_last_fetch: float = 0.0
        # Rooms rarely change — cache and refresh once per hour. None means
        # never fetched yet so the first poll always populates rooms.
        self._rooms_last_fetch: float | None = None
        # HTS client for hub network data (ethernet, wifi, gsm, power)
        self._hts_client: HtsClient | None = None
        self._hts_task: asyncio.Task[None] | None = None
        self.hub_network: dict[str, HubNetworkState] = {}

    @property
    def security_api(self) -> SecurityApi:
        return self._security_api

    @property
    def spaces_api(self) -> SpacesApi:
        return self._spaces_api

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
            # Login if not authenticated
            if not self._client.session.is_authenticated:
                try:
                    await self._login_and_persist()
                    # Restore normal interval after successful re-auth
                    configured = max(MIN_POLL_INTERVAL, min(MAX_POLL_INTERVAL, self._poll_interval))
                    self.update_interval = timedelta(seconds=configured)
                except AuthenticationError as err:
                    # Slow down retries to prevent account lockout
                    self.update_interval = timedelta(minutes=30)
                    _LOGGER.error(
                        "Authentication failed: %s — next retry in 30 min. "
                        "Fix credentials via Settings → Integrations → Ajax → Reconfigure.",
                        err,
                    )
                    raise UpdateFailed(f"Authentication failed: {err}") from err

            # Refresh spaces — if the saved token is stale Ajax replies with
            # UNAUTHENTICATED; force a fresh login (and persist the new token)
            # then retry once. Without this recovery the integration would
            # raise UpdateFailed and the next restart would re-login again,
            # piling up active sessions in the user's Ajax account.
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
                await self._login_and_persist()
                all_spaces = await self._spaces_api.list_spaces()
            now = asyncio.get_running_loop().time()
            new_spaces: dict[str, Space] = {}
            for s in all_spaces:
                if s.id not in self._space_ids:
                    continue
                # Preserve optimistic security_state if it hasn't expired
                opt = self._optimistic_space_states.get(s.id)
                if opt and opt[0] > now and s.security_state != opt[1]:
                    from dataclasses import replace as dc_replace  # noqa: PLC0415

                    s = dc_replace(s, security_state=opt[1])
                elif opt and opt[0] <= now:
                    self._optimistic_space_states.pop(s.id, None)
                new_spaces[s.id] = s
            self.spaces = new_spaces

            # Fetch SIM info for each hub (cached, refresh once per hour)
            sim_refresh_interval = 3600.0
            if now - self._sim_info_last_fetch > sim_refresh_interval:
                for space in self.spaces.values():
                    if space.hub_id and space.hub_id not in self.sim_info:
                        sim = await self._hub_object_api.get_sim_info(space.hub_id)
                        if sim:
                            self.sim_info[space.hub_id] = sim
                self._sim_info_last_fetch = now

            # Fetch rooms for each space (cached, refresh once per hour). Used
            # to set `suggested_area` on device entries so HA can auto-assign
            # devices to areas matching their Ajax rooms.
            rooms_refresh_interval = 3600.0
            if (
                self._rooms_last_fetch is None
                or now - self._rooms_last_fetch > rooms_refresh_interval
            ):
                refreshed_rooms: dict[str, Room] = {}
                for space_id in self.spaces:
                    try:
                        space_rooms = await self._spaces_api.list_rooms(space_id)
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("Failed to fetch rooms for space %s", space_id, exc_info=True)
                        continue
                    for room in space_rooms:
                        refreshed_rooms[room.id] = room
                self.rooms = refreshed_rooms
                self._rooms_last_fetch = now

            # Start persistent device streams on first update (once only)
            if not self._streams_started:
                self._streams_started = True
                # Fetch initial device snapshot synchronously so entities
                # are created with real data (avoids unavailable on reload)
                initial_devices: dict[str, Device] = {}
                for space_id in self.spaces:
                    space_devices = await self._devices_api.get_devices_snapshot(space_id)
                    for device in space_devices:
                        initial_devices[device.id] = device
                self.devices = initial_devices
                # Then start persistent streams for real-time updates
                await self._start_device_streams()
                # Start HTS for hub network data (non-blocking, graceful degradation)
                await self._start_hts()
                return {"spaces": self.spaces, "devices": self.devices}

            # Skip device snapshot if all persistent streams are alive
            streams_healthy = self._stream_tasks and all(not t.done() for t in self._stream_tasks)
            if not streams_healthy:
                # Fallback poll: refresh devices from snapshot for each space
                all_devices: dict[str, Device] = {}
                for space_id in self.spaces:
                    space_devices = await self._devices_api.get_devices_snapshot(space_id)
                    for device in space_devices:
                        all_devices[device.id] = device
                self.devices = all_devices

            if self._hts_task and self._hts_task.done():
                self._handle_hts_disconnect()
            if self._hts_client is None:
                await self._start_hts()

            return {"spaces": self.spaces, "devices": self.devices}
        except Exception as err:
            raise UpdateFailed("Error fetching Ajax data") from err

    async def _start_hts(self) -> None:
        """Start HTS connection for hub network data (graceful degradation)."""
        try:
            session = self._client.session
            token_hex = session.session_token
            if not token_hex:
                _LOGGER.debug("No session token, skipping HTS")
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
            result = await self._hts_client.connect()
            _LOGGER.info("HTS connected, %d hub(s)", len(result.hubs))
            self._hts_task = asyncio.create_task(
                self._hts_client.listen(on_state_update=self._on_hts_update)
            )
            self._hts_task.add_done_callback(self._handle_hts_task_done)
        except Exception:
            _LOGGER.debug("HTS connection failed (network sensors unavailable)", exc_info=True)
            self._handle_hts_disconnect(reconnect=False)
            self._hts_client = None

    def _on_hts_update(self, hub_id: str, state: HubNetworkState) -> None:
        """Handle hub network state update from HTS."""
        self.hub_network[hub_id] = state
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

    def _handle_hts_task_done(self, task: asyncio.Task[None]) -> None:
        """Clear stale HTS state when the listen task exits."""
        if task.cancelled():
            return
        with contextlib.suppress(Exception):
            task.result()
        self._handle_hts_disconnect()

    def _handle_hts_disconnect(self, *, reconnect: bool = True) -> None:
        """Drop stale HTS state so hub network entities become unavailable."""
        self._hts_task = None
        self._hts_client = None
        if self.hub_network:
            self.hub_network.clear()
            self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})
        if reconnect:
            # Schedule reconnect on next poll cycle rather than immediate retry
            _LOGGER.debug("HTS disconnected; will reconnect on next poll cycle")

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

    def _handle_devices_snapshot(self, devices: list[Device]) -> None:
        """Handle initial snapshot or full device snapshot update from stream."""
        for device in devices:
            self.devices[device.id] = device
        self.async_set_updated_data({"spaces": self.spaces, "devices": self.devices})

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

    async def async_start_push_notifications(
        self,
        *,
        fcm_project_id: str = "",
        fcm_app_id: str = "",
        fcm_api_key: str = "",
        fcm_sender_id: str = "",
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
        )
        await self._notification_listener.async_start()

    async def async_shutdown(self) -> None:
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
