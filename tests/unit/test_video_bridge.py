"""Tests for the go2rtc signalling bridge of the cloud live view (#322)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.aegis_ajax.api.webrtc import RemoteCandidate
from custom_components.aegis_ajax.camera import AjaxCloudVideoCamera
from custom_components.aegis_ajax.video_bridge import VideoBridge

CAMERA = "custom_components.aegis_ajax.camera"


class FakeSession:
    def __init__(self) -> None:
        self.answers: list[str] = []
        self.candidates: list[tuple[str, str | None, int | None]] = []
        self.closed = False

    def send_answer(self, sdp: str) -> None:
        self.answers.append(sdp)

    def add_local_candidate(self, cand: str, mid: str | None, idx: int | None) -> None:
        self.candidates.append((cand, mid, idx))

    def close(self) -> None:
        self.closed = True


class FakeCamera:
    """Plays the camera side: offers and sends one candidate on open."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.session = FakeSession()
        self.refuse = refuse

    def open_session(self, *, on_offer: Any, on_candidate: Any, on_error: Any) -> Any:  # noqa: ANN401
        if self.refuse:
            return None
        on_offer("v=0 offer")
        on_candidate(RemoteCandidate("candidate:1 1 udp 1 1.2.3.4 5 typ relay", "2", 1))
        return self.session


@pytest.mark.asyncio
async def test_bridge_speaks_go2rtc_openipc() -> None:
    bridge = VideoBridge()
    await bridge.async_start()
    camera = FakeCamera()
    bridge.register("tok", camera)
    url = bridge.url("tok")
    assert url == f"webrtc:ws://127.0.0.1:{bridge.port}/tok#format=openipc"
    try:
        async with (
            aiohttp.ClientSession() as http,
            http.ws_connect(url.removeprefix("webrtc:").split("#")[0]) as ws,
        ):
            assert await ws.receive_json() == {
                "reply": "webrtc_answer",
                "data": {"type": "offer", "sdp": "v=0 offer"},
            }
            assert await ws.receive_json() == {
                "reply": "webrtc_candidate",
                "data": {
                    "candidate": "candidate:1 1 udp 1 1.2.3.4 5 typ relay",
                    "sdpMid": "2",
                    "sdpMLineIndex": 1,
                },
            }
            await ws.send_json({"req": "answer", "data": "v=0 answer"})
            await ws.send_json(
                {"req": "candidate", "data": "candidate:2 1 udp 1 5.6.7.8 9 typ host"}
            )
            await ws.send_str("not json")
            for _ in range(50):
                if camera.session.candidates:
                    break
                await asyncio.sleep(0.01)
        for _ in range(50):
            if camera.session.closed:
                break
            await asyncio.sleep(0.01)
    finally:
        await bridge.async_stop()
    assert camera.session.answers == ["v=0 answer"]
    assert camera.session.candidates == [("candidate:2 1 udp 1 5.6.7.8 9 typ host", "0", 0)]
    # go2rtc closes the socket as soon as it is connected, so an answered
    # session must keep running: the camera is streaming on it.
    assert not camera.session.closed


@pytest.mark.asyncio
async def test_bridge_closes_an_unanswered_session_with_the_socket() -> None:
    bridge = VideoBridge()
    await bridge.async_start()
    camera = FakeCamera()
    bridge.register("tok", camera)
    try:
        async with (
            aiohttp.ClientSession() as http,
            http.ws_connect(f"http://127.0.0.1:{bridge.port}/tok") as ws,
        ):
            await ws.receive_json()
        for _ in range(50):
            if camera.session.closed:
                break
            await asyncio.sleep(0.01)
    finally:
        await bridge.async_stop()
    assert camera.session.closed


def test_bridge_url_carries_the_ice_servers_for_go2rtc() -> None:
    bridge = VideoBridge()
    bridge.port = 1234
    ice = '[{"urls":["stun:stun.example:3478"]}]'
    assert (
        bridge.url("tok", ice) == f"webrtc:ws://127.0.0.1:1234/tok#format=openipc#ice_servers={ice}"
    )
    assert bridge.url("tok") == "webrtc:ws://127.0.0.1:1234/tok#format=openipc"


@pytest.mark.asyncio
async def test_camera_hands_home_assistants_ice_servers_to_go2rtc() -> None:
    from webrtc_models import RTCIceServer

    coordinator = MagicMock()
    coordinator.devices = {}
    camera = AjaxCloudVideoCamera(coordinator, "dev-1", ("ve-1", "chan-1"))
    camera.hass = MagicMock()
    bridge = VideoBridge()
    bridge.port = 1234
    servers = [
        RTCIceServer(urls=["stun:stun.example:3478", "stun:stun.example:80"]),
        RTCIceServer(urls="turn:turn.example:3478", username="u", credential="c"),
        # go2rtc splits its parameters on "#", so this one can't be passed.
        RTCIceServer(urls="turn:turn.example:3478", username="u", credential="a#b"),
    ]
    with (
        patch(f"{CAMERA}.async_get_bridge", AsyncMock(return_value=bridge)),
        patch(f"{CAMERA}.async_get_ice_servers", return_value=servers),
    ):
        url = await camera.stream_source()
    assert url is not None
    _, _, ice = url.partition("#ice_servers=")
    # go2rtc 1.9.x drops a list of URLs without a word, so each URL goes alone as a string.
    assert json.loads(ice) == [
        {"urls": "stun:stun.example:3478"},
        {"urls": "stun:stun.example:80"},
        {"urls": "turn:turn.example:3478", "username": "u", "credential": "c"},
    ]


def test_a_new_session_closes_the_previous_one() -> None:
    coordinator = MagicMock()
    coordinator.devices = {}
    coordinator.spaces = {"space-1": object()}
    coordinator.cloud_video_outcomes = {}
    camera = AjaxCloudVideoCamera(coordinator, "dev-1", ("ve-1", "chan-1"))
    noop = {
        "on_offer": lambda _s: None,
        "on_candidate": lambda _c: None,
        "on_error": lambda *_: None,
    }
    with patch(f"{CAMERA}.CloudVideoSession.start", lambda self: None):
        first = camera.open_session(**noop)
        camera._last_session_at = 0.0
        second = camera.open_session(**noop)
    assert first is not None and second is not None
    assert first.closed
    assert not second.closed


@pytest.mark.asyncio
async def test_bridge_rejects_unknown_token_and_refused_sessions() -> None:
    bridge = VideoBridge()
    await bridge.async_start()
    bridge.register("tok", FakeCamera(refuse=True))
    base = f"http://127.0.0.1:{bridge.port}"
    try:
        async with aiohttp.ClientSession() as http:
            with pytest.raises(aiohttp.WSServerHandshakeError):
                await http.ws_connect(f"{base}/wrong")
            async with http.ws_connect(f"{base}/tok") as ws:
                msg = await ws.receive()
                assert msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED)
    finally:
        await bridge.async_stop()


def test_camera_refuses_a_second_session_too_soon() -> None:
    coordinator = MagicMock()
    coordinator.devices = {}
    coordinator.spaces = {"space-1": object()}
    coordinator.cloud_video_outcomes = {}
    camera = AjaxCloudVideoCamera(coordinator, "dev-1", ("ve-1", "chan-1"))
    started: list[Any] = []
    noop = {
        "on_offer": lambda _s: None,
        "on_candidate": lambda _c: None,
        "on_error": lambda *_: None,
    }
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "custom_components.aegis_ajax.camera.CloudVideoSession.start",
            lambda self: started.append(self),
        )
        first = camera.open_session(**noop)
        second = camera.open_session(**noop)
    assert first is not None
    assert second is None
    assert len(started) == 1
    assert coordinator.cloud_video_outcomes["dev-1"] is first.outcome


@pytest.mark.asyncio
async def test_camera_still_image_never_opens_a_session() -> None:
    coordinator = MagicMock()
    coordinator.devices = {}
    camera = AjaxCloudVideoCamera(coordinator, "dev-1", ("ve-1", "chan-1"))
    assert await camera.async_camera_image() is None
    assert camera.use_stream_for_stills is False


@pytest.mark.asyncio
async def test_camera_declines_an_hls_stream() -> None:
    coordinator = MagicMock()
    coordinator.devices = {}
    camera = AjaxCloudVideoCamera(coordinator, "dev-1", ("ve-1", "chan-1"))
    camera.hass = MagicMock()
    camera.stream_source = AsyncMock(return_value="webrtc:ws://127.0.0.1:1/t#format=openipc")  # type: ignore[method-assign]
    assert await camera.async_create_stream() is None
    camera.stream_source.assert_not_awaited()
