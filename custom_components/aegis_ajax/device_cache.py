"""Persistent cache of last-known device snapshot.

Restored on coordinator startup so the first poll cycle does not have
to await the gRPC `get_devices_snapshot` call before
`async_forward_entry_setups` runs. Cuts the integration's contribution
to HA's boot phase below the *"integration taking too long"* threshold
on multi-account installs (see #114). The cache is best-effort: any
deserialization failure falls back to "no cache" so the heavy path runs
exactly as it does today.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from custom_components.aegis_ajax.api.models import BatteryInfo, Device
from custom_components.aegis_ajax.const import DOMAIN, DeviceState

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_STORAGE_VERSION = 1


def _storage_key(entry_id: str) -> str:
    return f"{DOMAIN}_devices_{entry_id}"


_SAVE_DEBOUNCE_SECONDS = 30


class DevicesCache:
    """Wraps a per-entry Store with serialization for `Device`."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, _STORAGE_VERSION, _storage_key(entry_id))
        self._pending: dict[str, Device] = {}

    async def async_load(self) -> dict[str, Device] | None:
        raw = await self._store.async_load()
        if not raw:
            return None
        try:
            entries = raw["devices"]
            return {str(d["id"]): _deserialize_device(d) for d in entries}
        except (KeyError, TypeError, ValueError):
            return None

    async def async_save(self, devices: dict[str, Device]) -> None:
        await self._store.async_save(_build_payload(devices))

    def async_schedule_save(self, devices: dict[str, Device]) -> None:
        """Debounced save — coalesces bursts of stream snapshots into one
        disk write every ~30s. Use this on hot paths; `async_save` for
        the boot path where we want the first snapshot persisted now.
        """
        self._pending = devices
        self._store.async_delay_save(lambda: _build_payload(self._pending), _SAVE_DEBOUNCE_SECONDS)


def _build_payload(devices: dict[str, Device]) -> dict[str, Any]:
    return {"devices": [_serialize_device(d) for d in devices.values()]}


def _serialize_device(d: Device) -> dict[str, Any]:
    return {
        "id": d.id,
        "hub_id": d.hub_id,
        "name": d.name,
        "device_type": d.device_type,
        "room_id": d.room_id,
        "group_id": d.group_id,
        "state": str(d.state),
        "malfunctions": d.malfunctions,
        "bypassed": d.bypassed,
        "statuses": _serialize_statuses(d.statuses),
        "battery": (
            None if d.battery is None else {"level": d.battery.level, "is_low": d.battery.is_low}
        ),
    }


def _deserialize_device(data: dict[str, Any]) -> Device:
    battery = data.get("battery")
    return Device(
        id=str(data["id"]),
        hub_id=str(data["hub_id"]),
        name=str(data["name"]),
        device_type=str(data["device_type"]),
        room_id=data.get("room_id"),
        group_id=data.get("group_id"),
        state=DeviceState(str(data["state"])),
        malfunctions=int(data.get("malfunctions", 0)),
        bypassed=bool(data.get("bypassed", False)),
        statuses=dict(data.get("statuses") or {}),
        battery=(
            None
            if battery is None
            else BatteryInfo(level=int(battery["level"]), is_low=bool(battery["is_low"]))
        ),
    )


def _serialize_statuses(statuses: dict[str, Any]) -> dict[str, Any]:
    """Drop non-JSON values (e.g. datetimes) — next snapshot repopulates them."""
    safe: dict[str, Any] = {}
    for key, value in statuses.items():
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            continue
        safe[key] = value
    return safe
