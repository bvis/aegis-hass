"""Tests for the experimental cloud live view (#322)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from systems.ajax.api.mobile.v2.common.video import types_pb2
from systems.ajax.api.mobile.v2.common.video.webrtc import (
    ice_candidate_pb2,
    ice_server_pb2,
    session_description_pb2,
    stream_pb2,
)
from v3.mobilegwsvc.commonmodels.response import response_pb2 as common_response_pb2
from v3.mobilegwsvc.service.stream_webrtc import response_pb2

from custom_components.aegis_ajax.api import webrtc
from custom_components.aegis_ajax.api.models import Device
from custom_components.aegis_ajax.api.webrtc import CloudVideoSession, RemoteCandidate, sdp_codecs
from custom_components.aegis_ajax.camera import cloud_video_source
from custom_components.aegis_ajax.const import DeviceState

if TYPE_CHECKING:
    from collections.abc import Callable

BROWSER_OFFER = (
    "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96 102\r\n"
    "a=rtpmap:96 VP8/90000\r\na=rtpmap:102 H264/90000\r\n"
)
CAMERA_ANSWER = "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 102\r\na=rtpmap:102 H264/90000\r\n"
Resp = response_pb2.StreamWebrtcResponse


def _success(**kwargs: Any) -> Any:  # noqa: ANN401
    return Resp(success=Resp.Success(**kwargs))


def _init_msg() -> Any:  # noqa: ANN401
    return _success(
        init=Resp.Success.Init(
            ice_servers=[ice_server_pb2.IceServer(urls=["turn:x"], username="u", credential="c")],
            streams=[stream_pb2.Stream(id="0-lm")],
        )
    )


def _answer_msg() -> Any:  # noqa: ANN401
    sd = session_description_pb2.SessionDescription(type="answer", sdp=CAMERA_ANSWER)
    return _success(answer=Resp.Success.Answer(session_description=sd))


def _candidate_msg() -> Any:  # noqa: ANN401
    cand = ice_candidate_pb2.IceCandidate(
        sdp="candidate:1 1 udp 1 1.2.3.4 5 typ relay", sdp_mid="0"
    )
    return _success(new_ice_candidate=Resp.Success.NewIceCandidate(candidate=cand))


class FakeCall:
    """Server side of the bidi stream: `react` maps each request to replies."""

    def __init__(self, requests: Any, react: Callable[[Any], list[Any]]) -> None:  # noqa: ANN401
        self.sent: list[Any] = []
        self.cancelled = False
        self._replies: asyncio.Queue[Any] = asyncio.Queue()
        self._pump = asyncio.get_running_loop().create_task(self._consume(requests, react))

    async def _consume(self, requests: Any, react: Callable[[Any], list[Any]]) -> None:  # noqa: ANN401
        async for request in requests:
            self.sent.append(request)
            for reply in react(request):
                await self._replies.put(reply)

    def __aiter__(self) -> FakeCall:
        return self

    async def __anext__(self) -> Any:  # noqa: ANN401
        reply = await self._replies.get()
        if reply is StopAsyncIteration:
            raise StopAsyncIteration
        return reply

    def cancel(self) -> None:
        self.cancelled = True
        self._pump.cancel()


def _server(react: Callable[[Any], list[Any]]) -> tuple[Any, list[FakeCall]]:  # noqa: ANN401
    calls: list[FakeCall] = []

    class Stub:
        def __init__(self, channel: Any) -> None:  # noqa: ANN401
            pass

        def execute(self, requests: Any, metadata: Any = None) -> FakeCall:  # noqa: ANN401
            call = FakeCall(requests, react)
            calls.append(call)
            return call

    return Stub, calls


def _happy(request: Any) -> list[Any]:  # noqa: ANN401
    kind = request.WhichOneof("signaling_message")
    if kind == "init":
        return [_init_msg()]
    if kind == "offer":
        return [_answer_msg(), _candidate_msg()]
    return []


def _session(**callbacks: Any) -> CloudVideoSession:  # noqa: ANN401
    client = MagicMock()
    client._session.get_call_metadata.return_value = [("k", "v")]
    return CloudVideoSession(
        client,
        space_id="space-1",
        video_edge_id="ve-1",
        channel_id="chan-1",
        on_answer=callbacks.get("on_answer", lambda _sdp: None),
        on_candidate=callbacks.get("on_candidate", lambda _c: None),
        on_error=callbacks.get("on_error", lambda _c, _m: None),
    )


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


def test_sdp_codecs_lists_distinct_names_only() -> None:
    assert sdp_codecs(BROWSER_OFFER) == ["VP8", "H264"]
    assert sdp_codecs("v=0\r\n") == []


@pytest.mark.asyncio
async def test_forwards_browser_offer_after_init_and_relays_answer() -> None:
    answers: list[str] = []
    candidates: list[RemoteCandidate] = []
    stub, calls = _server(_happy)
    session = _session(on_answer=answers.append, on_candidate=candidates.append)
    with patch(
        "v3.mobilegwsvc.service.stream_webrtc.endpoint_pb2_grpc.StreamWebrtcServiceStub", stub
    ):
        session.start(BROWSER_OFFER)
        # A browser candidate that arrives before the offer is sent is held back.
        session.add_local_candidate("candidate:9 1 udp 1 5.6.7.8 9 typ host", "0", 0)
        await _settle()

    sent = calls[0].sent
    assert [r.WhichOneof("signaling_message") for r in sent] == [
        "init",
        "offer",
        "new_ice_candidate",
    ]
    init = sent[0].init
    assert init.video_edge_id == "ve-1"
    assert init.space_locator.space_id == "space-1"
    stream = init.initial_streams[0]
    assert (stream.id, stream.channel_guid, stream.type) == ("0-lm", "chan-1", types_pb2.ST_MAIN)
    assert [f.frame_type for f in stream.filter] == [types_pb2.FT_VIDEO]
    assert sent[1].offer.session_description.sdp == BROWSER_OFFER
    assert answers == [CAMERA_ANSWER]
    assert candidates[0].candidate.endswith("typ relay")
    assert candidates[0].sdp_mid == "0"
    assert session.outcome.stage == "answered"
    assert session.outcome.answer_codecs == ["H264"]
    assert session.outcome.ice_servers == 1
    session.close()


@pytest.mark.asyncio
async def test_camera_offering_itself_is_reported_not_forwarded() -> None:
    errors: list[str] = []

    def react(request: Any) -> list[Any]:  # noqa: ANN401
        if request.WhichOneof("signaling_message") == "init":
            sd = session_description_pb2.SessionDescription(type="offer", sdp=CAMERA_ANSWER)
            return [_init_msg(), _success(offer=Resp.Success.Offer(session_description=sd))]
        return []

    stub, _calls = _server(react)
    session = _session(on_error=lambda code, _m: errors.append(code))
    with patch(
        "v3.mobilegwsvc.service.stream_webrtc.endpoint_pb2_grpc.StreamWebrtcServiceStub", stub
    ):
        session.start(BROWSER_OFFER)
        await _settle()
    assert errors == ["edge_sent_offer"]
    assert session.outcome.edge_sent_offer is True


@pytest.mark.asyncio
async def test_server_failure_reaches_the_browser() -> None:
    errors: list[str] = []
    failure = Resp(failure=Resp.Failure(permission_denied=common_response_pb2.Error()))
    stub, _calls = _server(
        lambda r: [failure] if r.WhichOneof("signaling_message") == "init" else []
    )
    session = _session(on_error=lambda code, _m: errors.append(code))
    with patch(
        "v3.mobilegwsvc.service.stream_webrtc.endpoint_pb2_grpc.StreamWebrtcServiceStub", stub
    ):
        session.start(BROWSER_OFFER)
        await _settle()
    assert errors == ["permission_denied"]


@pytest.mark.asyncio
async def test_no_answer_times_out() -> None:
    errors: list[str] = []
    stub, _calls = _server(lambda _r: [])
    session = _session(on_error=lambda code, _m: errors.append(code))
    with (
        patch(
            "v3.mobilegwsvc.service.stream_webrtc.endpoint_pb2_grpc.StreamWebrtcServiceStub", stub
        ),
        patch.object(webrtc, "ANSWER_TIMEOUT", 0.05),
    ):
        session.start(BROWSER_OFFER)
        await asyncio.sleep(0.2)
    assert errors == ["timeout"]


@pytest.mark.asyncio
async def test_close_ends_the_stream_without_an_error() -> None:
    errors: list[str] = []
    stub, _calls = _server(_happy)
    session = _session(on_error=lambda code, _m: errors.append(code))
    with patch(
        "v3.mobilegwsvc.service.stream_webrtc.endpoint_pb2_grpc.StreamWebrtcServiceStub", stub
    ):
        session.start(BROWSER_OFFER)
        await _settle()
        session.close()
        await _settle()
    assert errors == []
    assert session._task is not None and session._task.done()


def test_outcome_never_carries_sdp_or_credentials() -> None:
    session = _session()
    session.outcome.offer_codecs = sdp_codecs(BROWSER_OFFER)
    dumped = repr(session.outcome.as_dict())
    for secret in ("v=0", "candidate:", "turn:", "rtpmap"):
        assert secret not in dumped


def _video_device(sources: list[dict[str, Any]]) -> Device:
    return Device(
        id="dev-1",
        hub_id="dev-1",
        name="Cam",
        device_type="video_edge_turret",
        room_id=None,
        group_id=None,
        state=DeviceState.ONLINE,
        malfunctions=0,
        bypassed=False,
        statuses={"video_sources": sources},
        battery=None,
    )


def test_cloud_video_source_prefers_the_camera_over_the_recorder() -> None:
    device = _video_device(
        [
            {"kind": "nvr", "video_edge_id": "nvr-1", "channel_id": "n-ch"},
            {"kind": "primary", "video_edge_id": "cam-1", "channel_id": "c-ch"},
        ]
    )
    assert cloud_video_source(device) == ("cam-1", "c-ch")
    nvr_only = _video_device([{"kind": "nvr", "video_edge_id": "nvr-1", "channel_id": "n-ch"}])
    assert cloud_video_source(nvr_only) == ("nvr-1", "n-ch")
    assert cloud_video_source(_video_device([])) is None
