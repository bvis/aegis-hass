"""Ajax Security Home Assistant integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform

from custom_components.aegis_ajax.api.client import AjaxGrpcClient
from custom_components.aegis_ajax.const import (
    CONF_AUTO_CREATE_LABELS,
    DEFAULT_AUTO_CREATE_LABELS,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    LABELS,
)
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.repairs import async_check_grpcio_version

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

_LOGGER = logging.getLogger(__name__)

_FCM_KEYS = {"fcm_project_id", "fcm_app_id", "fcm_api_key", "fcm_sender_id"}

PLATFORMS = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.LIGHT,
]

type AjaxCobrandedConfigEntry = ConfigEntry[AjaxCobrandedCoordinator]


def _resolve_target_space_ids(
    hass: HomeAssistant, call: ServiceCall
) -> list[tuple[AjaxCobrandedCoordinator, str]]:
    """Resolve target entity_ids to (coordinator, space_id) pairs.

    If no target is specified, returns all spaces from all entries.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    entity_ids: list[str] = call.data.get("entity_id", [])
    if isinstance(entity_ids, str):
        entity_ids = [entity_ids]

    entries = hass.config_entries.async_entries(DOMAIN)
    if not entity_ids:
        # No target: operate on all spaces (backwards-compatible)
        results: list[tuple[AjaxCobrandedCoordinator, str]] = []
        for entry in entries:
            coordinator: AjaxCobrandedCoordinator = entry.runtime_data
            for space_id in coordinator._space_ids:
                results.append((coordinator, space_id))
        return results

    # Map entity_id → space_id via unique_id pattern "aegis_ajax_alarm_{space_id}"
    entity_reg = er.async_get(hass)
    results = []
    for eid in entity_ids:
        entity_entry = entity_reg.async_get(eid)
        if entity_entry is None or entity_entry.platform != DOMAIN:
            continue
        uid = entity_entry.unique_id or ""
        # unique_id format: "aegis_ajax_alarm_{space_id}"
        if not uid.startswith("aegis_ajax_alarm_"):
            continue
        space_id = uid.removeprefix("aegis_ajax_alarm_")
        for entry in entries:
            coordinator = entry.runtime_data
            if space_id in coordinator._space_ids:
                results.append((coordinator, space_id))
                break
    return results


async def _async_handle_force_arm(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle force_arm service call (arm ignoring open sensors)."""
    targets = _resolve_target_space_ids(hass, call)
    refreshed: set[int] = set()
    for coordinator, space_id in targets:
        await coordinator.security_api.arm(space_id, ignore_alarms=True)
        cid = id(coordinator)
        if cid not in refreshed:
            await coordinator.async_request_refresh()
            refreshed.add(cid)


async def _async_handle_force_arm_night(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle force_arm_night service call (night mode ignoring open sensors)."""
    targets = _resolve_target_space_ids(hass, call)
    refreshed: set[int] = set()
    for coordinator, space_id in targets:
        await coordinator.security_api.arm_night_mode(space_id, ignore_alarms=True)
        cid = id(coordinator)
        if cid not in refreshed:
            await coordinator.async_request_refresh()
            refreshed.add(cid)


async def _async_handle_press_panic_button(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle press_panic_button service call.

    Hits the SpaceService/pressPanicButton endpoint — the same one the
    official Ajax app's red SOS button uses. This forwards a Panic / Hold-up
    alarm to the monitoring station (CRA), which on most contracts triggers
    police dispatch immediately and bypasses verification windows.

    A `confirm: true` field is required at the service level to prevent
    automations from triggering it accidentally. Without it the call is
    rejected via ServiceValidationError.
    """
    from homeassistant.exceptions import ServiceValidationError  # noqa: PLC0415

    if not call.data.get("confirm"):
        raise ServiceValidationError(
            "press_panic_button requires `confirm: true` to acknowledge that this "
            "forwards a panic alarm to the Ajax monitoring station (CRA), which on "
            "most contracts triggers police dispatch immediately."
        )

    latitude = call.data.get("latitude")
    longitude = call.data.get("longitude")

    targets = _resolve_target_space_ids(hass, call)
    if not targets:
        raise ServiceValidationError(
            "press_panic_button: no Aegis alarm panel found for the given target."
        )

    for coordinator, space_id in targets:
        await coordinator.spaces_api.press_panic_button(
            space_id,
            latitude=latitude,
            longitude=longitude,
        )


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry to newer version."""
    if entry.version == 1:
        # v1 → v2: Move FCM credentials from options to data
        new_data = dict(entry.data)
        new_options = dict(entry.options)
        migrated = False
        for key in _FCM_KEYS:
            if key in new_options and new_options[key]:
                new_data[key] = new_options.pop(key)
                migrated = True
            elif key in new_options:
                new_options.pop(key)
        if migrated:
            _LOGGER.info("Migrated FCM credentials from options to data")
        hass.config_entries.async_update_entry(entry, data=new_data, options=new_options, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AjaxCobrandedConfigEntry) -> bool:
    # Surface a Repair when HA's runtime grpcio is below the version we
    # need; mostly hits HA OS where the manifest's pip-level requirement
    # doesn't apply. Self-clears on the next setup if HA gets upgraded.
    async_check_grpcio_version(hass)

    # Migrate plaintext password to hash (one-time migration)
    if "password" in entry.data and "password_hash" not in entry.data:
        from custom_components.aegis_ajax.api.session import AjaxSession  # noqa: PLC0415

        new_data = dict(entry.data)
        new_data["password_hash"] = AjaxSession.hash_password(new_data.pop("password"))
        hass.config_entries.async_update_entry(entry, data=new_data)
        _LOGGER.warning(
            "Migrated plaintext password to hash for entry %s. Please reconfigure if issues arise.",
            entry.entry_id,
        )

    # Support legacy entries that stored plaintext password instead of hash
    if "password_hash" in entry.data:
        client = AjaxGrpcClient(
            email=entry.data["email"],
            password_hash=entry.data["password_hash"],
            device_id=entry.data.get("device_id"),
            app_label=entry.data.get("app_label", ""),
        )
    else:
        _LOGGER.warning(
            "Entry %s has neither password_hash nor password. Authentication may fail.",
            entry.entry_id,
        )
        client = AjaxGrpcClient(
            email=entry.data["email"],
            password=entry.data.get("password", ""),
            device_id=entry.data.get("device_id"),
            app_label=entry.data.get("app_label", ""),
        )
    # Restore session token from stored data to skip re-login (and 2FA) on restart
    if entry.data.get("session_token") and entry.data.get("user_hex_id"):
        _LOGGER.debug(
            "Restoring stored Ajax session for entry %s (user=%s)",
            entry.entry_id,
            entry.data["user_hex_id"],
        )
        client.session.set_session(str(entry.data["session_token"]), str(entry.data["user_hex_id"]))
    else:
        _LOGGER.debug(
            "No stored Ajax session for entry %s — coordinator will log in on first refresh",
            entry.entry_id,
        )
    await client.connect()

    def _persist_session(token: str, user_hex_id: str) -> None:
        """Write the latest session token back to the config entry.

        Called by the coordinator after every successful login so that a
        restart can reuse the freshest token instead of forcing another
        login (which would create yet another active session in Ajax).
        """
        if (
            entry.data.get("session_token") == token
            and entry.data.get("user_hex_id") == user_hex_id
        ):
            return
        new_data = {**entry.data, "session_token": token, "user_hex_id": user_hex_id}
        hass.config_entries.async_update_entry(entry, data=new_data)
        _LOGGER.debug("Persisted refreshed Ajax session for entry %s", entry.entry_id)

    coordinator = AjaxCobrandedCoordinator(
        hass=hass,
        client=client,
        space_ids=entry.data.get("spaces", []),
        poll_interval=entry.options.get("poll_interval", DEFAULT_POLL_INTERVAL),
        on_session_persist=_persist_session,
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Start FCM push notifications if configured (credentials live in data since v2)
    def _get_fcm(key: str) -> str:
        return str(entry.data.get(key, entry.options.get(key, "")))

    await coordinator.async_start_push_notifications(
        fcm_project_id=_get_fcm("fcm_project_id"),
        fcm_app_id=_get_fcm("fcm_app_id"),
        fcm_api_key=_get_fcm("fcm_api_key"),
        fcm_sender_id=_get_fcm("fcm_sender_id"),
        entry_id=entry.entry_id,
    )

    # Schedule photo cleanup
    from datetime import timedelta  # noqa: PLC0415

    from homeassistant.helpers.event import async_track_time_interval  # noqa: PLC0415

    from custom_components.aegis_ajax.photo_storage import cleanup_old_photos  # noqa: PLC0415

    retention_days = entry.options.get("photo_retention_days", 30)
    max_photos = entry.options.get("photo_max_per_device", 100)

    async def _photo_cleanup(_now: object = None) -> None:
        deleted = await cleanup_old_photos(hass, retention_days, max_photos)
        if deleted:
            _LOGGER.debug("Cleaned up %d old photos", len(deleted))

    # Schedule cleanup every 24h (first run deferred to avoid blocking startup)
    unsub_cleanup = async_track_time_interval(hass, _photo_cleanup, timedelta(hours=24))
    hass.async_create_task(_photo_cleanup())
    entry.async_on_unload(unsub_cleanup)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Auto-label entities for easy grouping in automations.
    # Users can disable this from the OptionsFlow when they prefer to manage
    # labels manually (the label registry is otherwise authoritative and
    # re-creates removed labels on every restart).
    if entry.options.get(CONF_AUTO_CREATE_LABELS, DEFAULT_AUTO_CREATE_LABELS):
        try:
            await _async_apply_labels(hass, entry)
        except Exception:
            _LOGGER.debug("Auto-labeling skipped (labels API not available)")

    async def _force_arm_handler(call: ServiceCall) -> None:
        await _async_handle_force_arm(hass, call)

    async def _force_arm_night_handler(call: ServiceCall) -> None:
        await _async_handle_force_arm_night(hass, call)

    async def _press_panic_button_handler(call: ServiceCall) -> None:
        await _async_handle_press_panic_button(hass, call)

    if not hass.services.has_service(DOMAIN, "force_arm"):
        hass.services.async_register(DOMAIN, "force_arm", _force_arm_handler)
        hass.services.async_register(DOMAIN, "force_arm_night", _force_arm_night_handler)
        hass.services.async_register(DOMAIN, "press_panic_button", _press_panic_button_handler)

    # Reload integration when options change (e.g. FCM credentials)
    entry.async_on_unload(entry.add_update_listener(_async_options_update_listener))

    return True


_LABEL_RULES: dict[str, set[str]] = {
    "aegis_alarm": {"alarm_control_panel", "event"},
    "aegis_hub": {"update"},
}

_DEVICE_CLASS_LABELS: dict[str, str] = {
    "door": "aegis_door",
    "window": "aegis_door",
    "garage_door": "aegis_door",
    "motion": "aegis_motion",
    "occupancy": "aegis_motion",
    "battery": "aegis_battery",
    "temperature": "aegis_temperature",
    "tamper": "aegis_tamper",
    "connectivity": "aegis_connectivity",
    "plug": "aegis_connectivity",
    "power": "aegis_connectivity",
}

_ENTITY_ID_LABELS: dict[str, str] = {
    "camera.": "aegis_camera",
    "button.": "aegis_camera",
    "_ethernet": "aegis_hub",
    "_wifi": "aegis_hub",
    "_wi_fi": "aegis_hub",
    "_ssid": "aegis_hub",
    "_celular": "aegis_hub",
    "_cellular": "aegis_hub",
    "_connection_type": "aegis_hub",
    "_tipo_de_conexion": "aegis_hub",
    "_tipo_de_red": "aegis_hub",
    "_alimentacion": "aegis_hub",
    "_mains_power": "aegis_hub",
    "_dns_": "aegis_hub",
    "_gateway": "aegis_hub",
    "_puerta_de_enlace": "aegis_hub",
    "_imei": "aegis_hub",
    "_cra": "aegis_hub",
    "_conexion_cra": "aegis_hub",
}


async def _async_apply_labels(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create labels and assign them to entities based on domain and device_class."""
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415
    from homeassistant.helpers import label_registry as lr  # noqa: PLC0415

    label_reg = lr.async_get(hass)
    entity_reg = er.async_get(hass)

    # Ensure labels exist
    for label_id, props in LABELS.items():
        if not label_reg.async_get_label(label_id):
            label_reg.async_create(
                name=props["name"],
                icon=props.get("icon"),
                color=props.get("color"),
            )

    # Assign labels to our entities
    entries = er.async_entries_for_config_entry(entity_reg, entry.entry_id)
    for entity_entry in entries:
        labels_to_add: set[str] = set()
        domain = entity_entry.entity_id.split(".")[0]

        # Rule 1: platform-based labels
        for label_id, domains in _LABEL_RULES.items():
            if domain in domains:
                labels_to_add.add(label_id)

        # Rule 2: device_class-based labels
        if entity_entry.original_device_class:
            dc = str(entity_entry.original_device_class).split(".")[-1]
            if dc in _DEVICE_CLASS_LABELS:
                labels_to_add.add(_DEVICE_CLASS_LABELS[dc])

        # Rule 3: entity_id pattern matching
        eid = entity_entry.entity_id
        for pattern, label_id in _ENTITY_ID_LABELS.items():
            if pattern in eid:
                labels_to_add.add(label_id)

        # Apply labels (union with existing to preserve user labels)
        if labels_to_add and not labels_to_add.issubset(entity_entry.labels):
            entity_reg.async_update_entity(
                entity_entry.entity_id,
                labels=entity_entry.labels | labels_to_add,
            )


async def _async_options_update_listener(
    hass: HomeAssistant, entry: AjaxCobrandedConfigEntry
) -> None:
    """Reload integration when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AjaxCobrandedConfigEntry) -> bool:
    remaining = hass.config_entries.async_entries(DOMAIN)
    if not any(e.entry_id != entry.entry_id for e in remaining):
        hass.services.async_remove(DOMAIN, "force_arm")
        hass.services.async_remove(DOMAIN, "force_arm_night")
        hass.services.async_remove(DOMAIN, "press_panic_button")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: AjaxCobrandedCoordinator = entry.runtime_data
        await coordinator.async_shutdown()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: AjaxCobrandedConfigEntry) -> None:
    """Invalidate the Ajax session server-side when the user removes the integration.

    Called only on permanent removal, not on reload — reloads route
    through async_unload_entry which deliberately keeps the session
    alive so the next setup can reuse the token. Without this hook the
    Ajax account would keep accumulating "Aegis" devices in its active
    sessions list every time someone uninstalls and reinstalls.
    """
    if "session_token" not in entry.data or "user_hex_id" not in entry.data:
        return

    common_kwargs = {
        "email": entry.data["email"],
        "device_id": entry.data.get("device_id"),
        "app_label": entry.data.get("app_label", ""),
    }
    try:
        if entry.data.get("password_hash"):
            client = AjaxGrpcClient(password_hash=entry.data["password_hash"], **common_kwargs)
        elif entry.data.get("password"):
            client = AjaxGrpcClient(password=entry.data["password"], **common_kwargs)
        else:
            return
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Logout skipped — could not rebuild client", exc_info=True)
        return

    client.session.set_session(str(entry.data["session_token"]), str(entry.data["user_hex_id"]))
    try:
        await client.connect()
        await client.logout()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Logout call failed during removal (best-effort)", exc_info=True)
    finally:
        await client.close()
