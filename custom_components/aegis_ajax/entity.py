"""Shared entity helpers for the Aegis Ajax integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.aegis_ajax.const import (
    COMMAND_ERROR_TRANSLATION_KEYS,
    DOMAIN,
    MANUFACTURER,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from custom_components.aegis_ajax.api.models import Device, DeviceCommand, Room
    from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator


def build_device_info(
    device: Device,
    rooms: Mapping[str, Room] | None = None,
) -> DeviceInfo:
    """Build a HA DeviceInfo for an Ajax device.

    Sets `serial_number` from the Ajax device id (the hex hardware identifier
    shown in the Ajax app) and `suggested_area` from the device's Ajax room
    when available, so HA can auto-assign devices to matching areas.
    """
    is_hub = device.device_type.startswith("hub")
    info = DeviceInfo(
        identifiers={(DOMAIN, device.id)},
        name=device.name,
        manufacturer=MANUFACTURER,
        model=device.device_type.replace("_", " ").title(),
        serial_number=device.id,
    )
    if not is_hub:
        info["via_device"] = (DOMAIN, device.hub_id)
    if rooms and device.room_id:
        room = rooms.get(device.room_id) if isinstance(rooms, dict) else None
        if room is not None:
            info["suggested_area"] = room.name
    return info


async def async_send_device_command(
    coordinator: AjaxCobrandedCoordinator, command: DeviceCommand
) -> None:
    """Send a device command and refresh, mapping hub rejections to a clear,
    translated `HomeAssistantError`.

    A failure the hub reports (permission denied, hub offline, …) is surfaced
    with a factual message keyed off the server's reason; any unmapped reason
    falls back to `command_failed`, echoing the raw code. The coordinator is
    only refreshed on success.
    """
    from custom_components.aegis_ajax.api.devices import DeviceCommandError  # noqa: PLC0415

    try:
        await coordinator.devices_api.send_command(command)
    except DeviceCommandError as err:
        translation_key = COMMAND_ERROR_TRANSLATION_KEYS.get(err.reason or "")
        if translation_key is not None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=translation_key
            ) from err
        placeholders: dict[str, Any] = {"reason": err.reason or "unknown"}
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders=placeholders,
        ) from err
    await coordinator.async_request_refresh()
