"""Update entities for Ajax hubs (read-only — #123 follow-up).

Surfaces the pending firmware update the Ajax cloud has queued for the
hub. The entity is informational only:

- No `install` feature is declared, so HA renders no install button.
- `async_install` is not implemented.
- Ajax controls update scheduling server-side; the cloud pushes updates
  to the hub on its own cadence. This integration deliberately never
  calls the install RPC even though the proto exposes one — firmware
  updates are higher-stakes than the rest of the surface and the user
  should manage them via the official app if they want to force one.

The Ajax stream doesn't carry the currently-installed firmware version,
so `installed_version` stays `None`; HA still renders the entity as
"<latest> available" with a progress bar when downloading.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.api.hub_object import (
    DEVICE_FW_STATE_DOWNLOADING,
    DEVICE_FW_STATE_INSTALLING,
    HUB_FW_STATE_DOWNLOADING,
    DeviceFirmwareUpdateInfo,
    HubFirmwareUpdateInfo,
)
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.entity import build_device_info

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)

# Sentinel value used for both `installed_version` and `latest_version`
# when the Ajax cloud reports no pending update. HA's `UpdateEntity`
# treats matching non-None versions as "up to date" and renders the
# entity state as `STATE_OFF`; with both versions left at `None` the
# entity would render as `unknown`, which is misleading because the
# absence of a pending update IS the "up to date" signal from Ajax.
# The placeholder is also surfaced on `installed_version` while an
# update IS pending so the state computation lands on `STATE_ON` —
# Ajax's `streamHubObject` does not carry the currently-installed
# firmware version, so this is the most truthful answer we can give.
_INSTALLED_VERSION_PLACEHOLDER = "current"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AjaxCobrandedCoordinator = entry.runtime_data
    entities: list[UpdateEntity] = []
    seen: set[str] = set()
    for space in coordinator.spaces.values():
        hub_id = space.hub_id
        if not hub_id or hub_id in seen:
            continue
        # Only attach when a hub device exists in the snapshot — otherwise
        # there's nothing to bind `device_info` to.
        if coordinator.devices.get(hub_id):
            entities.append(AjaxHubFirmwareUpdate(coordinator, hub_id))
            seen.add(hub_id)

    # Per-device firmware update entities (2.1). One per non-hub device,
    # disabled-by-default: a typical install has 10-30 devices and most
    # users only care when a specific device is stuck on old firmware.
    for device_id, device in coordinator.devices.items():
        if device.device_type.startswith("hub"):
            continue
        entities.append(AjaxDeviceFirmwareUpdate(coordinator, device_id))
    async_add_entities(entities)


class AjaxHubFirmwareUpdate(CoordinatorEntity[AjaxCobrandedCoordinator], UpdateEntity):
    """Read-only firmware update entity for an Ajax hub."""

    _attr_has_entity_name = True
    _attr_translation_key = "hub_firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    # No `INSTALL` feature — the entity is informational only.
    _attr_supported_features = UpdateEntityFeature(0)

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._attr_unique_id = f"aegis_ajax_{hub_id}_firmware"
        hub_device = coordinator.devices.get(hub_id)
        if hub_device:
            self._attr_device_info = build_device_info(hub_device, coordinator.rooms)

    @property
    def _info(self) -> HubFirmwareUpdateInfo | None:
        return self.coordinator.hub_firmware_updates.get(self._hub_id)

    @property
    def installed_version(self) -> str | None:
        # See `_INSTALLED_VERSION_PLACEHOLDER` for why this is always a
        # constant rather than `None`: HA's state computation needs a
        # non-`None` installed version to differentiate "up to date"
        # from "unknown".
        return _INSTALLED_VERSION_PLACEHOLDER

    @property
    def latest_version(self) -> str | None:
        info = self._info
        if info is None or not info.target_version:
            # No pending update from Ajax — mirror installed_version so
            # HA computes `STATE_OFF` and renders "Up to date".
            return _INSTALLED_VERSION_PLACEHOLDER
        return info.target_version

    @property
    def in_progress(self) -> bool:
        info = self._info
        return info is not None and info.state == HUB_FW_STATE_DOWNLOADING

    @property
    def release_summary(self) -> str | None:
        # The Ajax stream doesn't expose the currently-installed firmware
        # version, so "Up-to-date" here is shorthand for "Ajax has not
        # queued an update for this hub right now" — not a positive
        # confirmation that the hub is running the latest firmware Ajax
        # has ever published. The Ajax cloud schedules updates on its
        # own; this integration only mirrors what the cloud is telling
        # us, and the entity is informational (no install action).
        info = self._info
        if info is None:
            return (
                "Ajax has not queued a firmware update for this hub. "
                "The actual installed firmware version is not exposed by "
                "Ajax to the integration, so 'Up-to-date' reflects only "
                "the absence of a queued update."
            )
        return (
            f"Ajax has queued firmware {info.target_version} for this hub. "
            "The hub will install it on its own; this entity is "
            "informational and cannot trigger or skip the update."
        )


class AjaxDeviceFirmwareUpdate(CoordinatorEntity[AjaxCobrandedCoordinator], UpdateEntity):
    """Read-only firmware update entity for a single Ajax device (2.1).

    Same design as `AjaxHubFirmwareUpdate`: informational only (no
    `INSTALL` feature, no `async_install`), and Ajax does not expose the
    currently-installed version, so `installed_version` is a constant
    placeholder. Disabled by default — a typical install has many
    devices and most users only enable this when chasing a stuck update.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "device_firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature(0)
    # Disabled-by-default: opt-in per device to avoid 10-30 entities most
    # users don't want.
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"aegis_ajax_{device_id}_firmware"
        device = coordinator.devices.get(device_id)
        if device:
            self._attr_device_info = build_device_info(device, coordinator.rooms)

    @property
    def _info(self) -> DeviceFirmwareUpdateInfo | None:
        return self.coordinator.device_firmware_updates.get(self._device_id)

    @property
    def installed_version(self) -> str | None:
        # See `_INSTALLED_VERSION_PLACEHOLDER`: Ajax doesn't expose the
        # device's current version, so a constant is used to let HA
        # differentiate "up to date" from "unknown".
        return _INSTALLED_VERSION_PLACEHOLDER

    @property
    def latest_version(self) -> str | None:
        info = self._info
        if info is None or not info.target_version:
            # No pending update — mirror installed_version so HA renders
            # "Up to date" (STATE_OFF) rather than "unknown".
            return _INSTALLED_VERSION_PLACEHOLDER
        return info.target_version

    @property
    def in_progress(self) -> bool:
        info = self._info
        return info is not None and info.state in (
            DEVICE_FW_STATE_DOWNLOADING,
            DEVICE_FW_STATE_INSTALLING,
        )

    @property
    def update_percentage(self) -> int | None:
        # Only the download phase carries a 0-99 percentage; the install
        # phase has no progress signal, so HA shows an indeterminate bar.
        info = self._info
        if info is not None and info.state == DEVICE_FW_STATE_DOWNLOADING:
            return info.progress
        return None

    @property
    def release_summary(self) -> str | None:
        info = self._info
        if info is None:
            return (
                "Ajax has not queued a firmware update for this device. "
                "The actual installed firmware version is not exposed by "
                "Ajax to the integration, so 'Up-to-date' reflects only "
                "the absence of a queued update."
            )
        critical = " (security-critical)" if info.is_critical else ""
        return (
            f"Ajax has queued firmware {info.target_version} for this "
            f"device{critical}. The device will install it on its own; "
            "this entity is informational and cannot trigger or skip the "
            "update."
        )
