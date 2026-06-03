"""Lock entities for Ajax SmartLock / LockBridge devices."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.api.devices import (
    SMART_LOCK_ACTION_UNLATCH,
    DeviceCommandError,
    SmartLockError,
)
from custom_components.aegis_ajax.api.models import DeviceCommand
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.entity import build_device_info

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.aegis_ajax.api.models import Device

_LOGGER = logging.getLogger(__name__)

# Ajax catalog ships two SmartLock device-type buckets — `smart_lock` for
# generic LockBridge integrations and `smart_lock_yale` for Yale-branded
# locks. Both expose the same status oneof, so they share a single entity
# class. Lock/unlock is driven through the generic device on/off command
# (see `_async_switch`), unlatch through SwitchSmartLockService.
LOCK_DEVICE_TYPES: frozenset[str] = frozenset({"smart_lock", "smart_lock_yale"})


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AjaxCobrandedCoordinator = entry.runtime_data
    entities: list[AjaxLock] = []
    for device_id, device in coordinator.devices.items():
        if device.device_type in LOCK_DEVICE_TYPES:
            entities.append(AjaxLock(coordinator=coordinator, device_id=device_id))
    async_add_entities(entities)


class AjaxLock(CoordinatorEntity[AjaxCobrandedCoordinator], LockEntity):
    """Lock entity backed by the Ajax SmartLock status stream.

    State comes from the device status stream; lock/unlock is sent via the
    generic `DeviceCommandDeviceOn/Off` command (#219), unlatch via
    `SwitchSmartLockService`.
    """

    _attr_has_entity_name = True
    _attr_name = None
    # OPEN maps to UNLATCH on the Ajax side — pull the latch without keeping
    # the door deadbolted, e.g. for delivery drop-offs.
    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"aegis_ajax_{device_id}_lock"
        device = coordinator.devices.get(device_id)
        if device:
            self._attr_device_info = build_device_info(device, coordinator.rooms)

    @property
    def _device(self) -> Device | None:
        return self.coordinator.devices.get(self._device_id)

    def _resolve_space_id(self) -> str | None:
        device = self._device
        if device is None:
            return None
        for space in self.coordinator.spaces.values():
            if space.hub_id == device.hub_id:
                return space.id
        return None

    @property
    def available(self) -> bool:
        device = self._device
        return device is not None and device.is_online

    @property
    def is_locked(self) -> bool | None:
        device = self._device
        if device is None:
            return None
        state = device.statuses.get("smart_lock_state")
        if state == "locked":
            return True
        if state in ("unlocked", "unlatched"):
            return False
        return None

    @property
    def is_open(self) -> bool | None:
        device = self._device
        if device is None:
            return None
        return device.statuses.get("smart_lock_state") == "unlatched"

    async def _async_switch(self, *, lock: bool) -> None:
        """Lock/unlock via the generic device on/off command (#219).

        Hub-attached Jeweller locks (e.g. Yale modules added by an installer
        on a third-party backend) are not in the SmartLock cloud registry, so
        `SwitchSmartLockService` answers `smart_lock_not_found`. The Ajax app
        drives them with `DeviceCommandDeviceOn/Off` keyed by device id.

        Three details are decompiled from the app's hub-lock command path
        (`SmartLockSpreadInfoMini` → `SwitchDeviceStateApi.switchOn/Off`):
        it routes EVERY hub-attached lock (generic and Yale alike) through a
        single processor that emits the GENERIC `smart_lock` ObjectType, always
        on CHANNEL_1, and — the load-bearing detail this corrects — the
        polarity is **inverted**: the app sends On to UNLOCK and Off to LOCK
        (On energises the relay, retracting the bolt). An earlier attempt with
        lock=On was accepted by the hub but actuated the wrong way. So we
        override the stream's `smart_lock_yale` type to `smart_lock`, send
        `channels=[1]`, and map lock = Off / unlock = On.
        """
        device = self._device
        if device is None:
            return
        factory = DeviceCommand.off if lock else DeviceCommand.on
        command = factory(
            hub_id=device.hub_id,
            device_id=self._device_id,
            device_type="smart_lock",
            channels=[1],
        )
        try:
            await self.coordinator.devices_api.send_command(command)
        except DeviceCommandError as exc:
            _LOGGER.error(
                "SmartLock %s %s failed: %s",
                self._device_id,
                "lock" if lock else "unlock",
                exc,
            )
            return
        await self.coordinator.async_request_refresh()

    async def _send_action(self, action: int) -> None:
        """Unlatch (OPEN) via SwitchSmartLockService — the only unlatch path.

        Lock/unlock no longer use this (see `_async_switch`); it remains for
        the OPEN feature, which the generic on/off command can't express.
        """
        space_id = self._resolve_space_id()
        if space_id is None:
            _LOGGER.error(
                "Cannot resolve space for SmartLock %s — no matching hub in coordinator",
                self._device_id,
            )
            return
        try:
            await self.coordinator.devices_api.switch_smart_lock(
                space_id=space_id, smart_lock_id=self._device_id, action=action
            )
        except SmartLockError as exc:
            _LOGGER.error("SmartLock %s action %s failed: %s", self._device_id, action, exc)
            return
        await self.coordinator.async_request_refresh()

    async def async_lock(self, **kwargs: object) -> None:
        await self._async_switch(lock=True)

    async def async_unlock(self, **kwargs: object) -> None:
        await self._async_switch(lock=False)

    async def async_open(self, **kwargs: object) -> None:
        await self._send_action(SMART_LOCK_ACTION_UNLATCH)
