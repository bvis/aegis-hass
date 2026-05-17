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
    HUB_FW_STATE_DOWNLOADING,
    HubFirmwareUpdateInfo,
)
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.entity import build_device_info

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AjaxCobrandedCoordinator = entry.runtime_data
    entities: list[AjaxHubFirmwareUpdate] = []
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
        # The streamHubObject payload does not carry the currently-installed
        # firmware version. HA renders the entity with just `latest_version`
        # in that case, which is the intended behaviour.
        return None

    @property
    def latest_version(self) -> str | None:
        info = self._info
        if info is None:
            return None
        return info.target_version or None

    @property
    def in_progress(self) -> bool:
        info = self._info
        return info is not None and info.state == HUB_FW_STATE_DOWNLOADING
