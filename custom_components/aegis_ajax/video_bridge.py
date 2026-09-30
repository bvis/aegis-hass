"""Local signalling endpoint that lets go2rtc answer an Ajax camera (#322).

The camera only streams to a peer that answers its own offer, which the
browser can't do through Home Assistant's camera API. go2rtc can: its
``webrtc:ws://…#format=openipc`` source waits for the remote side's offer,
answers it and relays the media to every viewer without re-encoding. This
module is that remote side, a WebSocket on 127.0.0.1 that translates the
openipc messages to the Ajax signalling stream:

    us -> go2rtc  {"reply": "webrtc_answer", "data": {"type": "offer", "sdp": …}}
    us -> go2rtc  {"reply": "webrtc_candidate", "data": {"candidate": …}}
    go2rtc -> us  {"req": "answer", "data": "<sdp>"}
    go2rtc -> us  {"req": "candidate", "data": "<candidate>"}

Each camera has a random token in its URL, and the socket only listens on
the loopback interface.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol

from aiohttp import WSMsgType, web
from homeassistant.const import EVENT_HOMEASSISTANT_STOP

from custom_components.aegis_ajax.const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import Event, HomeAssistant

    from custom_components.aegis_ajax.api.webrtc import CloudVideoSession, RemoteCandidate

_LOGGER = logging.getLogger(__name__)

DATA_KEY = f"{DOMAIN}_video_bridge"


class SessionOpener(Protocol):
    def open_session(
        self,
        *,
        on_offer: Callable[[str], None],
        on_candidate: Callable[[RemoteCandidate], None],
        on_error: Callable[[str, str], None],
    ) -> CloudVideoSession | None: ...


class VideoBridge:
    """One loopback WebSocket server shared by every cloud-video camera."""

    def __init__(self) -> None:
        self._cameras: dict[str, SessionOpener] = {}
        self._runner: web.AppRunner | None = None
        self.port: int | None = None

    def register(self, token: str, camera: SessionOpener) -> None:
        self._cameras[token] = camera

    def unregister(self, token: str) -> None:
        self._cameras.pop(token, None)

    def url(self, token: str) -> str:
        return f"webrtc:ws://127.0.0.1:{self.port}/{token}#format=openipc"

    async def async_start(self) -> None:
        app = web.Application()
        app.router.add_get("/{token}", self._handle)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = self._runner.addresses[0][1]

    async def async_stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        camera = self._cameras.get(request.match_info["token"])
        if camera is None:
            raise web.HTTPNotFound
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        outbox: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        def on_offer(sdp: str) -> None:
            outbox.put_nowait({"reply": "webrtc_answer", "data": {"type": "offer", "sdp": sdp}})

        def on_candidate(cand: RemoteCandidate) -> None:
            outbox.put_nowait(
                {
                    "reply": "webrtc_candidate",
                    "data": {
                        "candidate": cand.candidate,
                        "sdpMid": cand.sdp_mid,
                        "sdpMLineIndex": cand.sdp_mline_index,
                    },
                }
            )

        session = camera.open_session(
            on_offer=on_offer,
            on_candidate=on_candidate,
            on_error=lambda _code, _message: outbox.put_nowait(None),
        )
        if session is None:
            await ws.close()
            return ws

        async def write() -> None:
            while (item := await outbox.get()) is not None:
                await ws.send_json(item)
            await ws.close()

        writer = asyncio.get_running_loop().create_task(write())
        try:
            async for msg in ws:
                if msg.type is not WSMsgType.TEXT:
                    continue
                with contextlib.suppress(ValueError, AttributeError):
                    data = json.loads(msg.data)
                    if data.get("req") == "answer":
                        session.send_answer(data.get("data") or "")
                    elif data.get("req") == "candidate":
                        # go2rtc sends the bare candidate line; with BUNDLE
                        # every candidate belongs to the first section.
                        session.add_local_candidate(data.get("data") or "", "0", 0)
        finally:
            session.close()
            writer.cancel()
        return ws


async def async_get_bridge(hass: HomeAssistant) -> VideoBridge:
    """Return the shared bridge, starting it on first use."""
    lock: asyncio.Lock = hass.data.setdefault(f"{DATA_KEY}_lock", asyncio.Lock())
    async with lock:
        bridge: VideoBridge | None = hass.data.get(DATA_KEY)
        if bridge is None:
            bridge = VideoBridge()
            await bridge.async_start()
            hass.data[DATA_KEY] = bridge

            async def _stop(_event: Event) -> None:
                await bridge.async_stop()

            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop)
        return bridge
