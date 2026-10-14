"""Rules every device entity must follow, checked across all platforms.

See docs/invariants.md. Per-entity tests only cover the entity they were
written for; these build every entity of every device type and check them
all, so a new entity class is held to the rule without knowing it exists.
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.aegis_ajax.api.models import Device, DeviceState
from custom_components.aegis_ajax.device_handlers import _DEVICE_HANDLERS

PLATFORMS = (
    "binary_sensor",
    "button",
    "camera",
    "event",
    "light",
    "lock",
    "number",
    "select",
    "sensor",
    "switch",
    "update",
    "valve",
)
DEVICE_TYPES = (*_DEVICE_HANDLERS, "unmapped_unknown_device")

# Entities that stay available while their device is offline, and why.
OFFLINE_AVAILABLE = {
    # It is the entity that reports the device as offline.
    "AjaxConnectivitySensor",
    # Events mark moments; returning from unavailable replays the last one
    # as a new ring or press (see `_AlwaysAvailable`).
    "AjaxDoorbellEvent",
    "AjaxButtonPressEvent",
    # Informational: the installed firmware doesn't go stale while offline.
    "AjaxDeviceFirmwareUpdate",
}


class _EveryStatus(dict):
    """Claims every status key, so setup creates every optional entity."""

    def __contains__(self, key: object) -> bool:
        return True

    def get(self, key: object, default: object = None) -> object:
        return default


async def _device_entities(platform: str, state: DeviceState) -> list[Any]:
    module = importlib.import_module(f"custom_components.aegis_ajax.{platform}")
    entities: list[Any] = []
    for device_type in DEVICE_TYPES:
        device = Device(
            id="dev-1",
            hub_id="hub-1",
            name="Device",
            device_type=device_type,
            room_id=None,
            group_id=None,
            state=state,
            malfunctions=0,
            bypassed=False,
            statuses=_EveryStatus(),
            battery=None,
        )
        coordinator = MagicMock()
        coordinator.devices = {"dev-1": device}
        coordinator.spaces = {}
        coordinator.rooms = {}
        coordinator.hub_registry_id.return_value = "hub-reg"
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list[Any] = []
        await module.async_setup_entry(MagicMock(), entry, added.extend)
        entities.extend(e for e in added if "dev-1" in (e.unique_id or ""))
    return entities


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", PLATFORMS)
async def test_device_entities_unavailable_while_offline(platform: str) -> None:
    entities = await _device_entities(platform, DeviceState.OFFLINE)
    assert entities, f"{platform} created no device entities, so nothing was checked"
    leaks = sorted(
        {
            type(e).__name__
            for e in entities
            if e.available and type(e).__name__ not in OFFLINE_AVAILABLE
        }
    )
    assert leaks == [], f"available while the device is offline: {leaks}"


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", PLATFORMS)
async def test_device_entities_available_while_online(platform: str) -> None:
    # Guards the check above: an entity that is never available would pass it.
    entities = await _device_entities(platform, DeviceState.ONLINE)
    never = sorted({type(e).__name__ for e in entities if not e.available})
    assert never == []


@pytest.mark.asyncio
async def test_offline_exceptions_are_still_in_use() -> None:
    names = set()
    for platform in PLATFORMS:
        names |= {type(e).__name__ for e in await _device_entities(platform, DeviceState.OFFLINE)}
    assert names >= OFFLINE_AVAILABLE, f"stale exceptions: {OFFLINE_AVAILABLE - names}"
