"""Cloud live-video signalling for Ajax video devices (#322, experimental).

The Ajax app pulls remote live video over WebRTC, negotiated on one
bidirectional ``StreamWebrtcService.execute`` stream: the client sends
``init``, the server answers with ICE servers and the granted streams, then
SDP and ICE candidates travel both ways on the same stream.

This module only relays signalling. The browser that opened the camera in
Home Assistant is the WebRTC peer: its offer is forwarded to the camera as
the client offer, and the camera's answer and candidates are sent back to
it. Media flows browser <-> Ajax cloud and never passes through Home
Assistant.

The app normally lets the camera make the offer and only offers itself
when it renegotiates. Sending the streams in ``init`` makes the camera
offer straight away (first field test, #322), so the streams are asked for
only after the camera has answered the browser's offer. The outcome of
every session is recorded (stage reached, codecs, when the camera offered
on its own) for diagnostics. No SDP, candidate or ICE credential is ever
logged or stored.

API delta: one stream per camera view, opened when the frontend asks for
it and closed when the view closes. Nothing runs while nobody watches.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from custom_components.aegis_ajax.api.client import AjaxGrpcClient

_LOGGER = logging.getLogger(__name__)

# The camera's answer (or an explicit failure) must arrive within this window
# after the offer is sent; past it the session is abandoned and the browser
# told, instead of leaving the player spinning.
ANSWER_TIMEOUT = 20.0

# Id of the first live main stream (`<n>-l` + `m`); tracks come back as `<id>-v` / `<id>-a`.
LIVE_MAIN_STREAM_ID = "0-lm"

_RTPMAP_RE = re.compile(r"^a=rtpmap:\d+ ([A-Za-z0-9_-]+)/", re.MULTILINE)


def sdp_codecs(sdp: str) -> list[str]:
    """Return the distinct codec names an SDP offers, in order (no other data)."""
    seen: list[str] = []
    for name in _RTPMAP_RE.findall(sdp):
        upper = name.upper()
        if upper not in seen:
            seen.append(upper)
    return seen


_DIRECTIONS = ("sendrecv", "sendonly", "recvonly", "inactive")


def sdp_shape(sdp: str) -> list[str]:
    """Describe each media section as kind, mid, direction and codecs (no other data).

    The camera re-offers after the stream is asked for (#322); comparing the
    shape of its answer with its own offer shows what it wanted to change.
    Addresses, ports, ICE credentials and fingerprints are never kept.
    """
    sections: list[dict[str, Any]] = []
    for line in sdp.splitlines():
        line = line.strip()
        if line.startswith("m="):
            kind = line[2:].split(" ", 1)[0]
            sections.append(
                {"kind": kind, "mid": "?", "dir": "sendrecv", "msid": False, "codecs": []}
            )
            continue
        if not sections:
            continue
        current = sections[-1]
        if line.startswith("a=mid:"):
            current["mid"] = line[6:]
        elif line.startswith("a=") and line[2:] in _DIRECTIONS:
            current["dir"] = line[2:]
        elif line.startswith("a=msid:"):
            current["msid"] = True
        else:
            match = _RTPMAP_RE.match(line)
            if match and match.group(1).upper() not in current["codecs"]:
                current["codecs"].append(match.group(1).upper())
    out = []
    for sec in sections:
        parts = [sec["kind"], f"mid={sec['mid']}", sec["dir"]]
        if sec["msid"]:
            parts.append("msid")
        if sec["codecs"]:
            parts.append(",".join(sec["codecs"]))
        out.append(" ".join(parts))
    return out


@dataclass
class SessionOutcome:
    """PII-free record of how one cloud-video session went, for diagnostics."""

    started_at: float = field(default_factory=time.time)
    stage: str = "starting"
    error: str | None = None
    answer_codecs: list[str] = field(default_factory=list)
    offer_codecs: list[str] = field(default_factory=list)
    edge_offer_codecs: list[str] = field(default_factory=list)
    edge_offer_stage: str | None = None
    offer_shape: list[str] = field(default_factory=list)
    answer_shape: list[str] = field(default_factory=list)
    edge_offer_shape: list[str] = field(default_factory=list)
    granted_streams: int | None = None
    ice_servers: int | None = None
    edge_sent_offer: bool = False
    remote_candidates: int = 0
    local_candidates: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": round(self.started_at),
            "stage": self.stage,
            "error": self.error,
            "offer_codecs": self.offer_codecs,
            "answer_codecs": self.answer_codecs,
            "edge_offer_codecs": self.edge_offer_codecs,
            "edge_offer_stage": self.edge_offer_stage,
            "offer_shape": self.offer_shape,
            "answer_shape": self.answer_shape,
            "edge_offer_shape": self.edge_offer_shape,
            "granted_streams": self.granted_streams,
            "ice_servers": self.ice_servers,
            "edge_sent_offer": self.edge_sent_offer,
            "remote_candidates": self.remote_candidates,
            "local_candidates": self.local_candidates,
        }


@dataclass(frozen=True)
class RemoteCandidate:
    candidate: str
    sdp_mid: str | None
    sdp_mline_index: int | None


class CloudVideoSession:
    """One browser <-> camera signalling session over the Ajax cloud."""

    def __init__(
        self,
        client: AjaxGrpcClient,
        *,
        space_id: str,
        video_edge_id: str,
        channel_id: str,
        on_answer: Callable[[str], None],
        on_candidate: Callable[[RemoteCandidate], None],
        on_error: Callable[[str, str], None],
    ) -> None:
        self._client = client
        self._space_id = space_id
        self._video_edge_id = video_edge_id
        self._channel_id = channel_id
        self._on_answer = on_answer
        self._on_candidate = on_candidate
        self._on_error = on_error
        self._outbox: asyncio.Queue[Any] = asyncio.Queue()
        self._pending_candidates: list[Any] = []
        self._offer_sent = False
        self._closed = False
        self._task: asyncio.Task[None] | None = None
        self.outcome = SessionOutcome()

    # -- request builders ---------------------------------------------------

    def _live_stream(self) -> Any:  # noqa: ANN401
        from systems.ajax.api.mobile.v2.common.video import types_pb2  # noqa: PLC0415
        from systems.ajax.api.mobile.v2.common.video.webrtc import stream_pb2  # noqa: PLC0415

        return stream_pb2.Stream(
            id=LIVE_MAIN_STREAM_ID,
            channel_guid=self._channel_id,
            dcp_tag=1,
            type=types_pb2.ST_MAIN,
            filter=[types_pb2.FrameTypeId(frame_type=types_pb2.FT_VIDEO)],
            live=stream_pb2.Stream.Live(),
        )

    def _init_request(self) -> Any:  # noqa: ANN401
        from systems.ajax.api.mobile.v2.common.space import space_locator_pb2  # noqa: PLC0415
        from systems.ajax.api.mobile.v2.common.video.webrtc import (  # noqa: PLC0415
            ice_candidate_filters_pb2,
        )
        from v3.mobilegwsvc.service.stream_webrtc import request_pb2  # noqa: PLC0415

        filters = ice_candidate_filters_pb2.IceCandidateFilters
        # No initial streams: with them the camera offers right after init and
        # never answers the browser. Same ICE filters the app sends.
        return request_pb2.StreamWebrtcRequest(
            init=request_pb2.StreamWebrtcRequest.Init(
                space_locator=space_locator_pb2.SpaceLocator(space_id=self._space_id),
                video_edge_id=self._video_edge_id,
                ice_filters=filters(
                    type_filter=filters.TypeFilter(host=True, reflexive=True, relay=True),
                    protocol_filter=filters.ProtocolFilter(tcp=True, udp=True),
                ),
                allow_large_rtp_packets=False,
            )
        )

    def _ask_streams_request(self) -> Any:  # noqa: ANN401
        from v3.mobilegwsvc.service.stream_webrtc import request_pb2  # noqa: PLC0415

        return request_pb2.StreamWebrtcRequest(
            ask_streams=request_pb2.StreamWebrtcRequest.AskStreams(streams=[self._live_stream()])
        )

    @staticmethod
    def _offer_request(sdp: str) -> Any:  # noqa: ANN401
        from systems.ajax.api.mobile.v2.common.video.webrtc import (  # noqa: PLC0415
            session_description_pb2,
        )
        from v3.mobilegwsvc.service.stream_webrtc import request_pb2  # noqa: PLC0415

        return request_pb2.StreamWebrtcRequest(
            offer=request_pb2.StreamWebrtcRequest.Offer(
                session_description=session_description_pb2.SessionDescription(
                    type="offer", sdp=sdp
                )
            )
        )

    @staticmethod
    def _candidate_request(candidate: str, sdp_mid: str | None, sdp_mline_index: int | None) -> Any:  # noqa: ANN401
        from systems.ajax.api.mobile.v2.common.video.webrtc import (  # noqa: PLC0415
            ice_candidate_pb2,
        )
        from v3.mobilegwsvc.service.stream_webrtc import request_pb2  # noqa: PLC0415

        return request_pb2.StreamWebrtcRequest(
            new_ice_candidate=request_pb2.StreamWebrtcRequest.NewIceCandidate(
                candidate=ice_candidate_pb2.IceCandidate(
                    sdp=candidate,
                    sdp_mid=sdp_mid or "",
                    sdp_mline_index=sdp_mline_index or 0,
                )
            )
        )

    # -- public API ----------------------------------------------------------

    def start(self, offer_sdp: str) -> None:
        """Open the signalling stream and forward the browser's offer."""
        self.outcome.offer_codecs = sdp_codecs(offer_sdp)
        self.outcome.offer_shape = sdp_shape(offer_sdp)
        self._offer_sdp = offer_sdp
        self._outbox.put_nowait(self._init_request())
        self._task = asyncio.get_running_loop().create_task(self._run())

    def add_local_candidate(
        self, candidate: str, sdp_mid: str | None, sdp_mline_index: int | None
    ) -> None:
        """Forward a browser ICE candidate (buffered until the offer is sent)."""
        if self._closed or not candidate:
            return
        request = self._candidate_request(candidate, sdp_mid, sdp_mline_index)
        self.outcome.local_candidates += 1
        if self._offer_sent:
            self._outbox.put_nowait(request)
        else:
            self._pending_candidates.append(request)

    def close(self) -> None:
        """Close the stream; the camera stops sending."""
        if self._closed:
            return
        self._closed = True
        self._outbox.put_nowait(None)
        if self._task is not None and not self._task.done():
            self._task.cancel()

    # -- internals -------------------------------------------------------------

    async def _requests(self) -> AsyncIterator[Any]:
        while True:
            request = await self._outbox.get()
            if request is None:
                return
            yield request

    def _fail(self, code: str, message: str) -> None:
        if self.outcome.error is None:
            self.outcome.error = code
        _LOGGER.warning("Cloud video (experimental, #322): %s — %s", code, message)
        self._on_error(code, message)

    def _send_offer(self) -> None:
        self._outbox.put_nowait(self._offer_request(self._offer_sdp))
        self._offer_sent = True
        for request in self._pending_candidates:
            self._outbox.put_nowait(request)
        self._pending_candidates.clear()
        self.outcome.stage = "offer_sent"

    def _handle(self, msg: Any) -> bool:  # noqa: ANN401
        """Apply one server message; return False to end the session."""
        which = msg.WhichOneof("response")
        if which == "failure":
            reason = msg.failure.WhichOneof("error") or "failure"
            self._fail(reason, f"Ajax refused the video session ({reason})")
            return False
        if which != "success":
            return True
        success = msg.success
        kind = success.WhichOneof("signaling_message")
        if kind == "init":
            self.outcome.ice_servers = len(success.init.ice_servers)
            self.outcome.granted_streams = len(success.init.streams)
            self.outcome.stage = "init"
            self._send_offer()
        elif kind == "answer":
            sdp = success.answer.session_description.sdp
            self.outcome.answer_codecs = sdp_codecs(sdp)
            self.outcome.answer_shape = sdp_shape(sdp)
            self.outcome.stage = "answered"
            _LOGGER.info(
                "Cloud video (experimental, #322): camera answered the client offer, codecs %s",
                ", ".join(self.outcome.answer_codecs) or "none",
            )
            self._on_answer(sdp)
            self._outbox.put_nowait(self._ask_streams_request())
        elif kind == "new_ice_candidate":
            cand = success.new_ice_candidate.candidate
            self.outcome.remote_candidates += 1
            self._on_candidate(
                RemoteCandidate(
                    candidate=cand.sdp,
                    sdp_mid=cand.sdp_mid or None,
                    sdp_mline_index=cand.sdp_mline_index,
                )
            )
        elif kind == "offer":
            # The camera made its own offer instead of answering ours: the
            # browser can't take a remote offer through Home Assistant, so this
            # is the "client offers are not accepted" outcome.
            self.outcome.edge_sent_offer = True
            self.outcome.edge_offer_stage = self.outcome.stage
            self.outcome.edge_offer_codecs = sdp_codecs(success.offer.session_description.sdp)
            self.outcome.edge_offer_shape = sdp_shape(success.offer.session_description.sdp)
            self._fail(
                "edge_sent_offer",
                "the camera sent its own offer instead of answering the browser's",
            )
            return False
        elif kind == "ask_streams_response":
            asked = success.ask_streams_response
            if asked.WhichOneof("response") == "failure":
                reason = asked.failure.WhichOneof("error") or "failure"
                self._fail(reason, f"Ajax refused the live stream ({reason})")
                return False
            self.outcome.granted_streams = len(asked.success.streams)
            self.outcome.stage = "streaming"
        return True

    async def _run(self) -> None:
        from v3.mobilegwsvc.service.stream_webrtc import endpoint_pb2_grpc  # noqa: PLC0415

        stub = endpoint_pb2_grpc.StreamWebrtcServiceStub(self._client._get_channel())
        metadata = self._client._session.get_call_metadata()
        call = stub.execute(self._requests(), metadata=metadata)
        try:
            async with asyncio.timeout(ANSWER_TIMEOUT) as deadline:
                async for msg in call:
                    if not self._handle(msg):
                        return
                    if self.outcome.stage in ("answered", "streaming"):
                        # Negotiated: keep relaying candidates for as long as
                        # the browser keeps the view open.
                        deadline.reschedule(None)
            if not self._closed:
                self._fail("stream_ended", "Ajax closed the video session")
        except TimeoutError:
            self._fail("timeout", "no answer from the camera")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._fail("rpc_error", type(exc).__name__)
            _LOGGER.debug("Cloud video stream failed", exc_info=True)
        finally:
            self._closed = True
            self._outbox.put_nowait(None)
            with contextlib.suppress(Exception):
                call.cancel()
