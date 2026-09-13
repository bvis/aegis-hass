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


# Coalesces bursts of stream updates into one disk write.
_SAVE_DEBOUNCE_SECONDS = 30

_SHAPES_STORAGE_VERSION = 1


def _shapes_storage_key(entry_id: str) -> str:
    return f"{DOMAIN}_battery_delta_shapes_{entry_id}"


_EMPTY_SHAPES: dict[str, int] = {
    "received": 0,
    "with_level": 0,
    "with_state": 0,
    "carried_nothing": 0,
}


class BatteryDeltaShapes:
    """What the hub's battery deltas actually contain (#506).

    `charge_level_percentage` is a plain proto3 scalar, so absent and zero are
    the same bytes on the wire; the delta handler therefore applies only the
    fields it can prove are present. Whether a real delta carries a level at
    all is unknown — no capture of one exists — and the outcome that matters
    is the silent one: deltas arriving that never carry a level, which leaves
    the reading exactly as stale as before the fix with nothing to say so.

    Counting it here means the answer arrives in any diagnostics dump instead
    of depending on someone running DEBUG at the moment a battery moves.

    Persisted, because the state it records is invisible by construction: a
    counter that resets on restart is what kept a month of undelivered push
    invisible in #437. Best-effort throughout — a storage problem must never
    stop the integration.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, int]] = Store(
            hass, _SHAPES_STORAGE_VERSION, _shapes_storage_key(entry_id)
        )
        self._counts: dict[str, int] = dict(_EMPTY_SHAPES)

    async def async_load(self) -> None:
        """Restore the counts. Never raises."""
        try:
            raw = await self._store.async_load()
        except Exception:  # noqa: BLE001
            return
        if not isinstance(raw, dict):
            return
        # Key by key: one unreadable counter must not throw away the others,
        # and a key we do not know is ignored rather than carried forward.
        for key in _EMPTY_SHAPES:
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                self._counts[key] = value

    def note(self, carried: dict[str, Any]) -> None:
        """Record one battery delta and what it carried.

        Attributes only plus a debounced save — called from the coordinator's
        status-update path, which runs on the event loop and must not grow a
        disk write per delta.
        """
        self._counts["received"] += 1
        if "level" in carried:
            self._counts["with_level"] += 1
        if "is_low" in carried:
            self._counts["with_state"] += 1
        if not carried:
            self._counts["carried_nothing"] += 1
        self._store.async_delay_save(lambda: dict(self._counts), _SAVE_DEBOUNCE_SECONDS)

    def as_dict(self) -> dict[str, int]:
        return dict(self._counts)


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
