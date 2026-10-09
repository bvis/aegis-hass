"""Button entities for Ajax Security (photo on-demand trigger)."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.const import DOMAIN
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.device_handlers import capabilities_for
from custom_components.aegis_ajax.entity import (
    async_start_sound_test,
    build_device_info,
)

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
        if capabilities_for(device).is_phod
    ]
    # Sirens whose type the command can address (#549).
    entities.extend(
        AjaxSirenSoundTestButton(coordinator=coordinator, device_id=device_id)
        for device_id, device in coordinator.devices.items()
        if capabilities_for(device).has_siren_settings
    )
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
        entities.append(AjaxRestoreAfterAlarmButton(coordinator=coordinator, hub_id=hub_id))
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


class AjaxRestoreAfterAlarmButton(CoordinatorEntity[AjaxCobrandedCoordinator], ButtonEntity):
    """The app's *Restore* after an alarm or malfunction (#572).

    One command per press; presses closer together than `MIN_PRESS_INTERVAL`
    are dropped so a looping automation can't flood the hub.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "restore_after_alarm"

    MIN_PRESS_INTERVAL = 10.0

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._attr_unique_id = f"aegis_ajax_{hub_id}_restore_after_alarm"
        self._last_press_at = -self.MIN_PRESS_INTERVAL
        hub_device = coordinator.devices.get(hub_id)
        if hub_device is not None:
            self._attr_device_info = build_device_info(hub_device, coordinator.rooms)

    @property
    def available(self) -> bool:
        return self.coordinator.is_hts_alive

    async def async_press(self) -> None:
        now = time.monotonic()
        if now - self._last_press_at < self.MIN_PRESS_INTERVAL:
            _LOGGER.debug("Restore on %s dropped, pressed too soon", self._hub_id)
            return
        self._last_press_at = now
        await self.coordinator.async_restore_after_alarm(self._hub_id)


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
            self._attr_device_info = build_device_info(
                device, coordinator.rooms, via_device_id=coordinator.hub_registry_id(device.hub_id)
            )

    async def async_press(self) -> None:
        """Trigger photo capture, retrieve the URL, download and save it.

        A button press is an explicit user action, so every failure path
        raises `HomeAssistantError` (surfaced as a UI notification) instead
        of returning silently. Before this, a capture that the hub never
        completed — common on some camera firmwares where the on-demand
        request is rejected — left the user staring at an empty media folder
        with nothing in the default-level log to explain why.
        """
        _LOGGER.debug("Capture photo button pressed for %s", self._device_id)

        # Check the delivery half BEFORE asking the hub for anything (#524).
        # The photo comes back over the FCM push channel, so with no
        # credentials the capture is a request that cannot produce a photo —
        # and the user used to wait out the 15 s timeout only to be told to
        # check whether the camera was online. Setup starts the listener
        # unconditionally, credentials or not, so the object existing proves
        # nothing; `has_fcm_credentials` is the flag that decides (#509).
        listener = self.coordinator.notification_listener
        if listener is None or not listener.has_fcm_credentials:
            _LOGGER.warning(
                "Photo capture for %s needs FCM push notifications, which are not configured",
                self._device_id,
            )
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="photo_no_push")

        result = await self.coordinator.devices_api.capture_photo(
            self._hub_id, self._device_id, self._device_type
        )
        if not result:
            _LOGGER.warning("Photo capture request not accepted by hub for %s", self._device_id)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="photo_capture_failed"
            )

        # The hub delivers the captured photo's id asynchronously via an FCM push.
        notification_id = await listener.wait_for_notification_id(self._device_id, timeout=15.0)
        if not notification_id:
            _LOGGER.warning(
                "No photo notification arrived for %s within the timeout", self._device_id
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="photo_capture_timeout"
            )

        url = await self.coordinator.media_api.get_photo_url(
            notification_id, self._hub_id, timeout=60.0
        )
        if not url:
            _LOGGER.warning("No photo URL returned by the hub for %s", self._device_id)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="photo_capture_failed"
            )

        _LOGGER.debug("Photo URL retrieved for %s: %s", self._device_id, url[:80])
        from homeassistant.helpers.aiohttp_client import (  # noqa: PLC0415
            async_get_clientsession,
        )

        from custom_components.aegis_ajax.photo_download import (  # noqa: PLC0415
            async_download_photo,
        )
        from custom_components.aegis_ajax.photo_storage import (  # noqa: PLC0415
            save_photo,
        )

        image_bytes = await async_download_photo(async_get_clientsession(self.hass), url)
        if image_bytes is None:
            _LOGGER.warning("Photo download for %s failed", self._device_id)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="photo_capture_failed"
            )

        device = self.coordinator.devices.get(self._device_id)
        device_name = device.name if device else self._device_id
        await save_photo(self.hass, image_bytes, self._device_id, device_name)
        self.coordinator.last_photo_urls[self._device_id] = url


class AjaxSirenSoundTestButton(CoordinatorEntity[AjaxCobrandedCoordinator], ButtonEntity):
    """Play a siren's test sound, as the app's *Test* button does (#549).

    One request per press. Presses closer together than `MIN_PRESS_INTERVAL`
    are dropped, so an automation on every camera detection can't send Ajax
    a stream of commands.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "siren_sound_test"

    MIN_PRESS_INTERVAL = 10.0

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"aegis_ajax_{device_id}_siren_sound_test"
        self._last_press_at = -self.MIN_PRESS_INTERVAL
        device = coordinator.devices.get(device_id)
        if device:
            self._attr_device_info = build_device_info(
                device, coordinator.rooms, via_device_id=coordinator.hub_registry_id(device.hub_id)
            )

    @property
    def available(self) -> bool:
        device = self.coordinator.devices.get(self._device_id)
        return super().available and device is not None and device.is_online

    async def async_press(self) -> None:
        device = self.coordinator.devices.get(self._device_id)
        if device is None:
            return
        now = time.monotonic()
        if now - self._last_press_at < self.MIN_PRESS_INTERVAL:
            _LOGGER.debug("Sound test for %s dropped, pressed too soon", self._device_id)
            return
        self._last_press_at = now
        await async_start_sound_test(self.coordinator, device)
