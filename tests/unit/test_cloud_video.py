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
from custom_components.aegis_ajax.api.webrtc import (
    CloudVideoSession,
    RemoteCandidate,
    sdp_codecs,
    sdp_shape,
)
from custom_components.aegis_ajax.camera import cloud_video_source
from custom_components.aegis_ajax.const import DeviceState

if TYPE_CHECKING:
    from collections.abc import Callable

# Shape of the camera's real offer in the second field test (#322).
CAMERA_OFFER = (
    "v=0\r\n"
    "m=audio 9 UDP/TLS/RTP/SAVPF 9\r\na=mid:0\r\na=recvonly\r\na=rtpmap:9 G722/8000\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 102\r\na=mid:2\r\na=sendrecv\r\n"
    "a=msid:0-lm 0-lm-v\r\na=rtpmap:102 H264/90000\r\n"
    "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\na=mid:3\r\n"
)
LOCAL_ANSWER = (
    "v=0\r\nm=audio 0 UDP/TLS/RTP/SAVPF 9\r\na=mid:0\r\na=inactive\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 102\r\na=mid:2\r\na=recvonly\r\na=rtpmap:102 H264/90000\r\n"
)
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


def _offer_msg() -> Any:  # noqa: ANN401
    sd = session_description_pb2.SessionDescription(type="offer", sdp=CAMERA_OFFER)
    return _success(offer=Resp.Success.Offer(session_description=sd))


def _happy(request: Any) -> list[Any]:  # noqa: ANN401
    """The app's flow: init with the stream, camera offers, client answers."""
    kind = request.WhichOneof("signaling_message")
    if kind == "init":
        return [_init_msg(), _offer_msg(), _candidate_msg()]
    return []


def _session(**callbacks: Any) -> CloudVideoSession:  # noqa: ANN401
    client = MagicMock()
    client._session.get_call_metadata.return_value = [("k", "v")]
    return CloudVideoSession(
        client,
        space_id="space-1",
        video_edge_id="ve-1",
        channel_id="chan-1",
        on_offer=callbacks.get("on_offer", lambda _sdp: None),
        on_candidate=callbacks.get("on_candidate", lambda _c: None),
        on_error=callbacks.get("on_error", lambda _c, _m: None),
    )


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


STUB = "v3.mobilegwsvc.service.stream_webrtc.endpoint_pb2_grpc.StreamWebrtcServiceStub"


def test_sdp_shape_lists_each_media_section_only() -> None:
    sdp = (
        "v=0\r\no=- 1 2 IN IP4 10.0.0.1\r\na=ice-ufrag:secret\r\n"
        "m=audio 9 UDP/TLS/RTP/SAVPF 9\r\nc=IN IP4 10.0.0.1\r\na=mid:0\r\na=recvonly\r\n"
        "a=rtpmap:9 G722/8000\r\n"
        "m=video 9 UDP/TLS/RTP/SAVPF 102 26\r\na=mid:1\r\na=sendonly\r\n"
        "a=msid:0-lm 0-lm-v\r\na=rtpmap:102 H264/90000\r\na=rtpmap:26 JPEG/90000\r\n"
        "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\na=mid:2\r\n"
    )
    assert sdp_shape(sdp) == [
        "audio mid=0 recvonly G722",
        "video mid=1 sendonly msid H264,JPEG",
        "application mid=2 sendrecv",
    ]
    assert sdp_shape("v=0\r\n") == []


def test_sdp_codecs_lists_distinct_names_only() -> None:
    assert sdp_codecs(CAMERA_OFFER) == ["G722", "H264"]
    assert sdp_codecs("v=0\r\n") == []


@pytest.mark.asyncio
async def test_asks_for_the_stream_in_init_and_answers_the_camera_offer() -> None:
    offers: list[str] = []
    candidates: list[RemoteCandidate] = []
    stub, calls = _server(_happy)
    session = _session(on_offer=offers.append, on_candidate=candidates.append)
    with patch(STUB, stub):
        session.start()
        await _settle()
        assert offers == [CAMERA_OFFER]
        session.send_answer(LOCAL_ANSWER)
        session.add_local_candidate("candidate:9 1 udp 1 5.6.7.8 9 typ host", "0", 0)
        await _settle()

    sent = calls[0].sent
    assert [r.WhichOneof("signaling_message") for r in sent] == [
        "init",
        "answer",
        "new_ice_candidate",
    ]
    init = sent[0].init
    assert init.video_edge_id == "ve-1"
    assert init.space_locator.space_id == "space-1"
    stream = init.initial_streams[0]
    assert (stream.id, stream.channel_guid, stream.type) == ("0-lm", "chan-1", types_pb2.ST_MAIN)
    assert [f.frame_type for f in stream.filter] == [types_pb2.FT_VIDEO]
    assert sent[1].answer.session_description.sdp == LOCAL_ANSWER
    assert sent[1].answer.session_description.type == "answer"
    assert candidates[0].candidate.endswith("typ relay")
    assert candidates[0].sdp_mid == "0"
    outcome = session.outcome
    assert outcome.stage == "answered"
    assert outcome.error is None
    assert outcome.offer_shape == sdp_shape(CAMERA_OFFER)
    assert outcome.answer_shape == sdp_shape(LOCAL_ANSWER)
    assert (outcome.ice_servers, outcome.remote_candidates, outcome.local_candidates) == (1, 1, 1)
    session.close()


@pytest.mark.asyncio
async def test_answered_session_outlives_the_answer_timeout() -> None:
    errors: list[str] = []
    stub, _calls = _server(_happy)
    session = _session(on_error=lambda code, _m: errors.append(code))
    with patch(STUB, stub), patch.object(webrtc, "ANSWER_TIMEOUT", 0.05):
        session.start()
        await _settle()
        session.send_answer(LOCAL_ANSWER)
        await asyncio.sleep(0.2)
    assert errors == []
    session.close()


@pytest.mark.asyncio
async def test_unanswered_offer_times_out() -> None:
    errors: list[str] = []
    stub, _calls = _server(_happy)
    session = _session(on_error=lambda code, _m: errors.append(code))
    with patch(STUB, stub), patch.object(webrtc, "ANSWER_TIMEOUT", 0.05):
        session.start()
        await asyncio.sleep(0.2)
    assert errors == ["timeout"]
    assert session.outcome.stage == "offered"


@pytest.mark.asyncio
async def test_renegotiation_after_the_answer_is_reported() -> None:
    errors: list[str] = []

    def react(request: Any) -> list[Any]:  # noqa: ANN401
        if request.WhichOneof("signaling_message") == "answer":
            return [_offer_msg()]
        return _happy(request)

    stub, _calls = _server(react)
    session = _session(on_error=lambda code, _m: errors.append(code))
    with patch(STUB, stub):
        session.start()
        await _settle()
        session.send_answer(LOCAL_ANSWER)
        await _settle()
    assert errors == ["renegotiation"]


@pytest.mark.asyncio
async def test_server_failure_is_reported() -> None:
    errors: list[str] = []
    failure = Resp(failure=Resp.Failure(permission_denied=common_response_pb2.Error()))
    stub, _calls = _server(
        lambda r: [failure] if r.WhichOneof("signaling_message") == "init" else []
    )
    session = _session(on_error=lambda code, _m: errors.append(code))
    with patch(STUB, stub):
        session.start()
        await _settle()
    assert errors == ["permission_denied"]


@pytest.mark.asyncio
async def test_close_ends_the_stream_without_an_error() -> None:
    errors: list[str] = []
    stub, _calls = _server(_happy)
    session = _session(on_error=lambda code, _m: errors.append(code))
    with patch(STUB, stub):
        session.start()
        await _settle()
        session.close()
        await _settle()
    assert errors == []
    assert session.closed
    assert session._task is not None and session._task.done()


def test_outcome_never_carries_sdp_or_credentials() -> None:
    session = _session()
    session.outcome.offer_codecs = sdp_codecs(CAMERA_OFFER)
    session.outcome.offer_shape = sdp_shape(CAMERA_OFFER)
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
