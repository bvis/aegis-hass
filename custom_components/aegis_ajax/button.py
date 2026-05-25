"""Button entities for Ajax Security (photo on-demand trigger)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.camera import PHOD_DEVICE_TYPES
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
    entities: list[ButtonEntity] = [
        AjaxCapturePhotoButton(
            coordinator=coordinator,
            device_id=device_id,
            hub_id=device.hub_id,
            device_type=device.device_type,
        )
        for device_id, device in coordinator.devices.items()
        if device.device_type in PHOD_DEVICE_TYPES
    ]
    # One refresh button per hub — bridges the gap between the 60s
    # periodic STATUS_BODY refresh and the user wanting a fresh reading
    # immediately after toggling an appliance (#179).
    seen_hubs: set[str] = set()
    for space in coordinator.spaces.values():
        hub_id = space.hub_id
        if not hub_id or hub_id in seen_hubs:
            continue
        if coordinator.devices.get(hub_id) is None:
            continue
        seen_hubs.add(hub_id)
        entities.append(AjaxRefreshHubButton(coordinator=coordinator, hub_id=hub_id))
    async_add_entities(entities)


class AjaxRefreshHubButton(CoordinatorEntity[AjaxCobrandedCoordinator], ButtonEntity):
    """Per-hub button that triggers an on-demand HTS STATUS_BODY refresh.

    The integration refreshes each hub every 60 s on its own. This
    button exists so the user (or an automation) can request a fresh
    snapshot immediately — useful right after toggling an appliance
    when waiting for the next periodic tick would feel sluggish. The
    coordinator enforces a 60 s rate-limit per hub so a stuck
    automation can't hammer the hub.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "refresh_hub"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._attr_unique_id = f"aegis_ajax_{hub_id}_refresh_hub"
        hub_device = coordinator.devices.get(hub_id)
        if hub_device is not None:
            self._attr_device_info = build_device_info(hub_device, coordinator.rooms)

    @property
    def available(self) -> bool:
        # Pressing while HTS is down would just raise; reflecting that
        # in `available` keeps the UI consistent with `mains_power` and
        # other HTS-gated entities (#146 pattern).
        return self.coordinator.is_hts_alive

    async def async_press(self) -> None:
        await self.coordinator.async_request_manual_refresh(self._hub_id)


class AjaxCapturePhotoButton(CoordinatorEntity[AjaxCobrandedCoordinator], ButtonEntity):
    """Button to trigger photo on-demand capture."""

    _attr_has_entity_name = True
    _attr_translation_key = "capture_photo"

    def __init__(
        self,
        coordinator: AjaxCobrandedCoordinator,
        device_id: str,
        hub_id: str,
        device_type: str,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._hub_id = hub_id
        self._device_type = device_type
        self._attr_unique_id = f"aegis_ajax_{device_id}_capture_photo"
        device = coordinator.devices.get(device_id)
        if device:
            self._attr_device_info = build_device_info(device, coordinator.rooms)

    async def async_press(self) -> None:
        """Trigger photo capture and retrieve the photo URL."""
        _LOGGER.debug("Capture photo button pressed for %s", self._device_id)
        result = await self.coordinator.devices_api.capture_photo(
            self._hub_id, self._device_id, self._device_type
        )
        if not result:
            _LOGGER.debug("Photo capture failed for %s", self._device_id)
            return

        listener = self.coordinator.notification_listener
        if not listener:
            return

        # Wait for notification_id from FCM push
        notification_id = await listener.wait_for_notification_id(self._device_id, timeout=15.0)
        if not notification_id:
            _LOGGER.debug("No notification_id received for %s", self._device_id)
            return

        # Get photo URL via media stream
        url = await self.coordinator.media_api.get_photo_url(
            notification_id, self._hub_id, timeout=60.0
        )
        if url:
            _LOGGER.debug("Photo URL retrieved for %s: %s", self._device_id, url[:80])
            # Download and save the photo
            import aiohttp  # noqa: PLC0415
            from homeassistant.helpers.aiohttp_client import (  # noqa: PLC0415
                async_get_clientsession,
            )

            from custom_components.aegis_ajax.photo_storage import (  # noqa: PLC0415
                save_photo,
            )

            session = async_get_clientsession(self.hass)
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        image_bytes = await resp.read()
                        # Save with timestamp overlay
                        device = self.coordinator.devices.get(self._device_id)
                        device_name = device.name if device else self._device_id
                        await save_photo(self.hass, image_bytes, self._device_id, device_name)
                        # Store for camera entity
                        self.coordinator.last_photo_urls[self._device_id] = url
            except Exception:
                _LOGGER.exception("Failed to download photo for %s", self._device_id)
        else:
            _LOGGER.debug("No photo URL from media stream for %s", self._device_id)
