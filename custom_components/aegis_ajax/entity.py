"""Shared entity helpers for the Aegis Ajax integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.aegis_ajax.const import DOMAIN, MANUFACTURER

if TYPE_CHECKING:
    from collections.abc import Mapping

    from custom_components.aegis_ajax.api.models import Device, Room


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
