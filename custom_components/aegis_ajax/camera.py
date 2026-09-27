"""Camera entities for Ajax Security (MotionCam photo on demand, cloud live view)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.camera.webrtc import WebRTCAnswer, WebRTCCandidate, WebRTCError
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from webrtc_models import RTCIceCandidateInit

from custom_components.aegis_ajax.api.media import is_valid_photo_url
from custom_components.aegis_ajax.api.webrtc import CloudVideoSession, RemoteCandidate
from custom_components.aegis_ajax.const import CONF_CLOUD_VIDEO, DEFAULT_CLOUD_VIDEO
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.device_handlers import capabilities_for
from custom_components.aegis_ajax.entity import build_device_info

if TYPE_CHECKING:
    from homeassistant.components.camera.webrtc import WebRTCSendMessage
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.aegis_ajax.api.models import Device

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

    Home Assistant only relays signalling: the browser's WebRTC offer goes to
    the camera over the Ajax cloud, and the answer and ICE candidates come
    back. The video itself flows browser <-> Ajax and never through Home
    Assistant, which is why this also works when Home Assistant is not on the
    camera's network.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "cloud_video"
    _attr_supported_features = CameraEntityFeature.STREAM

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
        self._sessions: dict[str, CloudVideoSession] = {}
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

    def _space_id(self) -> str:
        # ponytail: video channels carry no space id, so a multi-space account
        # uses its first space; record the owning space per device if a
        # multi-space install ever reports `video_edge_not_found`.
        return next(iter(self.coordinator.spaces), "")

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        def on_answer(sdp: str) -> None:
            send_message(WebRTCAnswer(answer=sdp))

        def on_candidate(cand: RemoteCandidate) -> None:
            send_message(
                WebRTCCandidate(
                    RTCIceCandidateInit(
                        cand.candidate,
                        sdp_mid=cand.sdp_mid,
                        sdp_m_line_index=cand.sdp_mline_index,
                    )
                )
            )

        def on_error(code: str, message: str) -> None:
            send_message(WebRTCError(code=code, message=message))

        session = CloudVideoSession(
            self.coordinator.grpc_client,
            space_id=self._space_id(),
            video_edge_id=self._video_edge_id,
            channel_id=self._channel_id,
            on_answer=on_answer,
            on_candidate=on_candidate,
            on_error=on_error,
        )
        self._sessions[session_id] = session
        # Shared object: the dump shows how far the latest session got, even
        # while it is still running.
        self.coordinator.cloud_video_outcomes[self._device_id] = session.outcome
        session.start(offer_sdp)

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit
    ) -> None:
        if session := self._sessions.get(session_id):
            session.add_local_candidate(
                candidate.candidate, candidate.sdp_mid, candidate.sdp_m_line_index
            )

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        if session := self._sessions.pop(session_id, None):
            session.close()

    async def async_will_remove_from_hass(self) -> None:
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()
        await super().async_will_remove_from_hass()
