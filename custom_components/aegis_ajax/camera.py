"""Camera entities for Ajax Security (MotionCam photo on demand, cloud live view)."""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.web_rtc import async_get_ice_servers
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.api.media import is_valid_photo_url
from custom_components.aegis_ajax.api.webrtc import CloudVideoSession
from custom_components.aegis_ajax.const import CONF_CLOUD_VIDEO, DEFAULT_CLOUD_VIDEO
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.device_handlers import capabilities_for
from custom_components.aegis_ajax.entity import build_device_info
from custom_components.aegis_ajax.video_bridge import DATA_KEY, async_get_bridge

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.aegis_ajax.api.models import Device
    from custom_components.aegis_ajax.api.webrtc import RemoteCandidate

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AjaxCobrandedCoordinator = entry.runtime_data
    entities: list[Camera] = [
        AjaxCamera(
            coordinator=coordinator,
            device_id=device_id,
            hub_id=device.hub_id,
            device_type=device.device_type,
        )
        for device_id, device in coordinator.devices.items()
        if capabilities_for(device).is_camera
    ]
    if entry.options.get(CONF_CLOUD_VIDEO, DEFAULT_CLOUD_VIDEO):
        entities.extend(
            AjaxCloudVideoCamera(coordinator, device_id, source)
            for device_id, device in coordinator.devices.items()
            if (source := cloud_video_source(device)) is not None
        )
    async_add_entities(entities)


def cloud_video_source(device: Device) -> tuple[str, str] | None:
    """Return `(video_edge_id, channel_id)` to stream a video channel from.

    The camera's own (`primary`) source is preferred; an NVR-bridged channel
    falls back to the recorder's. Devices without a video source get nothing.
    """
    sources = device.statuses.get("video_sources") or []
    for kind in ("primary", "nvr"):
        for source in sources:
            if (
                source.get("kind") == kind
                and source.get("video_edge_id")
                and source.get("channel_id")
            ):
                return source["video_edge_id"], source["channel_id"]
    return None


class AjaxCamera(CoordinatorEntity[AjaxCobrandedCoordinator], Camera):
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self,
        coordinator: AjaxCobrandedCoordinator,
        device_id: str,
        hub_id: str,
        device_type: str,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._device_id = device_id
        self._hub_id = hub_id
        self._device_type = device_type
        self._attr_unique_id = f"aegis_ajax_{device_id}_camera"
        self._last_image_url: str | None = None
        self._last_image: bytes | None = None
        self._photo_revision = 0
        device = coordinator.devices.get(device_id)
        if device:
            self._attr_device_info = build_device_info(
                device, coordinator.rooms, via_device_id=coordinator.hub_registry_id(device.hub_id)
            )

    @property
    def _device(self) -> Device | None:
        return self.coordinator.devices.get(self._device_id)

    @property
    def available(self) -> bool:
        device = self._device
        return device is not None and device.is_online

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,  # noqa: ARG002
    ) -> bytes | None:
        """Return the last captured photo. Use the button entity to capture new photos."""
        # A manual historical-alarm import writes a fresh `last.jpg`. Discard
        # our in-memory copy so normal MotionCam hardware immediately exposes
        # it, without attempting an unsupported Photo on Demand capture.
        current_revision = self.coordinator.photo_revisions.get(self._device_id, 0)
        if current_revision != self._photo_revision:
            self._last_image = None
            self._last_image_url = None
            self._photo_revision = current_revision
        # Check if button.py just retrieved a new URL
        url = self.coordinator.last_photo_urls.pop(self._device_id, None)
        if url:
            return await self._download_image(url)
        return await self._get_last_image()

    async def _get_last_image(self) -> bytes | None:
        """Return cached image, or load persisted photo from disk."""
        if self._last_image is None:
            from custom_components.aegis_ajax.photo_storage import (  # noqa: PLC0415
                load_last_photo,
            )

            device = self.coordinator.devices.get(self._device_id)
            device_name = device.name if device else self._device_id
            self._last_image = await load_last_photo(self.hass, device_name)
        return self._last_image

    async def _download_image(self, url: str) -> bytes | None:
        """Download image from URL and cache it."""
        import aiohttp  # noqa: PLC0415

        if not is_valid_photo_url(url):
            # Log host only — never the query string (carries the S3 signature).
            from urllib.parse import urlparse  # noqa: PLC0415

            _LOGGER.warning(
                "Rejected photo URL with unexpected domain: %s", urlparse(url).hostname or "?"
            )
            return self._last_image
        self._last_image_url = url
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    self._last_image = await resp.read()
        except Exception:
            _LOGGER.exception("Failed to download photo")
        return self._last_image


class AjaxCloudVideoCamera(CoordinatorEntity[AjaxCobrandedCoordinator], Camera):
    """Live view of an Ajax video channel through the Ajax cloud (#322, experimental).

    Home Assistant's go2rtc is the WebRTC peer: it answers the camera's offer
    the way the app does and serves the browser itself, so this also works
    when Home Assistant is not on the camera's network. The stream source is
    a loopback URL of `video_bridge`, which relays the signalling to Ajax.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "cloud_video"
    _attr_supported_features = CameraEntityFeature.STREAM

    # A new session within this many seconds of the last one is refused, so a
    # player that keeps retrying can't open a stream of Ajax video sessions.
    MIN_SESSION_INTERVAL = 10.0

    def __init__(
        self,
        coordinator: AjaxCobrandedCoordinator,
        device_id: str,
        source: tuple[str, str],
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._device_id = device_id
        self._video_edge_id, self._channel_id = source
        self._attr_unique_id = f"aegis_ajax_{device_id}_cloud_video"
        self._token = secrets.token_urlsafe(24)
        self._last_session_at = 0.0
        self._sessions: set[CloudVideoSession] = set()
        device = coordinator.devices.get(device_id)
        if device:
            self._attr_device_info = build_device_info(
                device, coordinator.rooms, via_device_id=coordinator.hub_registry_id(device.hub_id)
            )

    @property
    def available(self) -> bool:
        device = self.coordinator.devices.get(self._device_id)
        return device is not None and device.is_online

    async def async_camera_image(
        self,
        width: int | None = None,  # noqa: ARG002
        height: int | None = None,  # noqa: ARG002
    ) -> bytes | None:
        # No still image: a snapshot would need a video session of its own.
        return None

    @property
    def use_stream_for_stills(self) -> bool:
        # Explicit, not HA's default: with go2rtc as the provider, stills from
        # the stream would open an Ajax video session for every thumbnail.
        return False

    def _space_id(self) -> str:
        # ponytail: video channels carry no space id, so a multi-space account
        # uses its first space; record the owning space per device if a
        # multi-space install ever reports `video_edge_not_found`.
        return next(iter(self.coordinator.spaces), "")

    async def stream_source(self) -> str | None:
        # Building the URL costs nothing: Ajax is only called once go2rtc
        # connects to it, which happens when someone opens the live view.
        bridge = await async_get_bridge(self.hass)
        bridge.register(self._token, self)
        servers = []
        for server in async_get_ice_servers(self.hass):
            entry = server.to_dict()
            urls = entry.pop("urls")
            # go2rtc 1.9.x only reads "urls" as a string and silently drops a
            # list, which left it without STUN: one server per URL.
            servers += [
                {**entry, "urls": url} for url in ([urls] if isinstance(urls, str) else urls)
            ]
        # go2rtc splits its URL parameters on "#", so a server containing one is left out.
        ice = json.dumps([s for s in servers if "#" not in json.dumps(s)], separators=(",", ":"))
        return bridge.url(self._token, ice)

    async def async_create_stream(self) -> None:
        # No HLS stream: Home Assistant offers HLS for every go2rtc camera, and
        # ffmpeg can't open the webrtc: source, so it would retry for ever.
        # go2rtc reads stream_source() itself, so the live view is unaffected.
        return None

    def open_session(
        self,
        *,
        on_offer: Callable[[str], None],
        on_candidate: Callable[[RemoteCandidate], None],
        on_error: Callable[[str, str], None],
    ) -> CloudVideoSession | None:
        now = time.monotonic()
        if now - self._last_session_at < self.MIN_SESSION_INTERVAL:
            _LOGGER.debug("Cloud video: session for %s refused, too soon", self.entity_id)
            return None
        self._last_session_at = now
        session = CloudVideoSession(
            self.coordinator.grpc_client,
            space_id=self._space_id(),
            video_edge_id=self._video_edge_id,
            channel_id=self._channel_id,
            on_offer=on_offer,
            on_candidate=on_candidate,
            on_error=on_error,
        )
        # Shared object: the dump shows how far the latest session got, even
        # while it is still running.
        self.coordinator.cloud_video_outcomes[self._device_id] = session.outcome
        # go2rtc only asks again once its previous producer is gone, so an
        # older session still open is idle: close it, one per camera.
        for old in self._sessions:
            old.close()
        self._sessions = {session}
        session.start()
        return session

    async def async_will_remove_from_hass(self) -> None:
        if bridge := self.hass.data.get(DATA_KEY):
            bridge.unregister(self._token)
        for session in self._sessions:
            session.close()
        self._sessions.clear()
        await super().async_will_remove_from_hass()
