"""Cloud live-video signalling for Ajax video devices (#322, experimental).

The Ajax app pulls remote live video over WebRTC, negotiated on one
bidirectional ``StreamWebrtcService.execute`` stream: the client sends
``init`` with the streams it wants, the server answers with ICE servers and
the granted streams, the camera sends its offer, and the client answers.
ICE candidates travel both ways on the same stream.

The browser can't be that client: the camera answers a browser offer with
every track inactive and then offers a new video track and a data channel
of its own, and Home Assistant's camera API has no way to hand the browser
an offer (second field test, #322). So Home Assistant's go2rtc answers the
camera instead (see ``video_bridge``), the way the app does, and serves the
browser itself. Video passes through go2rtc without being re-encoded.

The outcome of every session is recorded (stage reached, codecs, the layout
of the camera's offer and of the answer) for diagnostics. No SDP, candidate
or ICE credential is ever logged or stored.

API delta: one stream per live view, opened when go2rtc asks for it. go2rtc
hangs up its signalling socket as soon as it is connected, so an answered
stream runs until Ajax ends it, the next live view replaces it, or
``MAX_SESSION_SECONDS`` passes, whichever comes first; never more than one
per camera.
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

# The camera's offer and our answer (or an explicit failure) must happen within
# this window after init; past it the session is abandoned instead of leaving
# the player spinning.
ANSWER_TIMEOUT = 20.0

# ponytail: fixed cap on an answered session; we can't see when go2rtc's last
# viewer leaves. Raise it, or end the session on go2rtc's stream list, if
# live views longer than this get cut.
MAX_SESSION_SECONDS = 600.0

# Id of the first live main stream (`<n>-l` + `m`); tracks come back as `<id>-v` / `<id>-a`.
LIVE_MAIN_STREAM_ID = "0-lm"

_CANDIDATE_TYPE_RE = re.compile(r" typ (\w+)")
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


def _count_candidate_type(counts: dict[str, int], candidate: str) -> None:
    match = _CANDIDATE_TYPE_RE.search(candidate)
    kind = match.group(1) if match else "unknown"
    counts[kind] = counts.get(kind, 0) + 1


@dataclass
class SessionOutcome:
    """PII-free record of how one cloud-video session went, for diagnostics."""

    started_at: float = field(default_factory=time.time)
    stage: str = "starting"
    error: str | None = None
    offer_codecs: list[str] = field(default_factory=list)
    answer_codecs: list[str] = field(default_factory=list)
    offer_shape: list[str] = field(default_factory=list)
    answer_shape: list[str] = field(default_factory=list)
    granted_streams: int | None = None
    ice_servers: int | None = None
    remote_candidates: int = 0
    local_candidates: int = 0
    # host / srflx / relay counts: which paths each side offered, no addresses.
    remote_candidate_types: dict[str, int] = field(default_factory=dict)
    local_candidate_types: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": round(self.started_at),
            "stage": self.stage,
            "error": self.error,
            "offer_codecs": self.offer_codecs,
            "answer_codecs": self.answer_codecs,
            "offer_shape": self.offer_shape,
            "answer_shape": self.answer_shape,
            "granted_streams": self.granted_streams,
            "ice_servers": self.ice_servers,
            "remote_candidates": self.remote_candidates,
            "local_candidates": self.local_candidates,
            "remote_candidate_types": self.remote_candidate_types,
            "local_candidate_types": self.local_candidate_types,
        }


@dataclass(frozen=True)
class RemoteCandidate:
    candidate: str
    sdp_mid: str | None
    sdp_mline_index: int | None


class CloudVideoSession:
    """One camera -> local peer signalling session over the Ajax cloud.

    The camera offers (``on_offer``); the local peer answers through
    ``send_answer``, as the app does.
    """

    def __init__(
        self,
        client: AjaxGrpcClient,
        *,
        space_id: str,
        video_edge_id: str,
        channel_id: str,
        on_offer: Callable[[str], None],
        on_candidate: Callable[[RemoteCandidate], None],
        on_error: Callable[[str, str], None],
    ) -> None:
        self._client = client
        self._space_id = space_id
        self._video_edge_id = video_edge_id
        self._channel_id = channel_id
        self._on_offer = on_offer
        self._on_candidate = on_candidate
        self._on_error = on_error
        self._outbox: asyncio.Queue[Any] = asyncio.Queue()
        self._closed = False
        self._task: asyncio.Task[None] | None = None
        self._deadline: asyncio.Timeout | None = None
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
        # The stream goes in init so the camera offers straight away, as it
        # does for the app. Same ICE filters the app sends.
        return request_pb2.StreamWebrtcRequest(
            init=request_pb2.StreamWebrtcRequest.Init(
                space_locator=space_locator_pb2.SpaceLocator(space_id=self._space_id),
                video_edge_id=self._video_edge_id,
                initial_streams=[self._live_stream()],
                ice_filters=filters(
                    type_filter=filters.TypeFilter(host=True, reflexive=True, relay=True),
                    protocol_filter=filters.ProtocolFilter(tcp=True, udp=True),
                ),
                allow_large_rtp_packets=False,
            )
        )

    @staticmethod
    def _answer_request(sdp: str) -> Any:  # noqa: ANN401
        from systems.ajax.api.mobile.v2.common.video.webrtc import (  # noqa: PLC0415
            session_description_pb2,
        )
        from v3.mobilegwsvc.service.stream_webrtc import request_pb2  # noqa: PLC0415

        return request_pb2.StreamWebrtcRequest(
            answer=request_pb2.StreamWebrtcRequest.Answer(
                session_description=session_description_pb2.SessionDescription(
                    type="answer", sdp=sdp
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

    @property
    def closed(self) -> bool:
        return self._closed

    def start(self) -> None:
        """Open the signalling stream and ask for the live stream."""
        self._outbox.put_nowait(self._init_request())
        self._task = asyncio.get_running_loop().create_task(self._run())

    def send_answer(self, sdp: str) -> None:
        """Answer the camera's offer; the session then runs until closed or capped."""
        if self._closed:
            return
        self.outcome.answer_codecs = sdp_codecs(sdp)
        self.outcome.answer_shape = sdp_shape(sdp)
        self.outcome.stage = "answered"
        self._outbox.put_nowait(self._answer_request(sdp))
        if self._deadline is not None:
            loop = asyncio.get_running_loop()
            self._deadline.reschedule(loop.time() + MAX_SESSION_SECONDS)

    def add_local_candidate(
        self, candidate: str, sdp_mid: str | None, sdp_mline_index: int | None
    ) -> None:
        """Forward one of the local peer's ICE candidates."""
        if self._closed or not candidate:
            return
        self.outcome.local_candidates += 1
        _count_candidate_type(self.outcome.local_candidate_types, candidate)
        self._outbox.put_nowait(self._candidate_request(candidate, sdp_mid, sdp_mline_index))

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
        elif kind == "offer":
            sdp = success.offer.session_description.sdp
            self.outcome.offer_codecs = sdp_codecs(sdp)
            self.outcome.offer_shape = sdp_shape(sdp)
            if self.outcome.stage == "answered":
                # A renegotiation after we answered: not handled yet, but
                # recorded so a field dump shows it.
                self._fail("renegotiation", "the camera renegotiated the session")
                return False
            self.outcome.stage = "offered"
            _LOGGER.info(
                "Cloud video (experimental, #322): camera offered, codecs %s",
                ", ".join(self.outcome.offer_codecs) or "none",
            )
            self._on_offer(sdp)
        elif kind == "new_ice_candidate":
            cand = success.new_ice_candidate.candidate
            self.outcome.remote_candidates += 1
            _count_candidate_type(self.outcome.remote_candidate_types, cand.sdp)
            self._on_candidate(
                RemoteCandidate(
                    candidate=cand.sdp,
                    sdp_mid=cand.sdp_mid or None,
                    sdp_mline_index=cand.sdp_mline_index,
                )
            )
        elif kind == "answer":
            # We never offer, so an answer means the camera and we disagree
            # on who leads; record it rather than guess.
            self._fail("unexpected_answer", "the camera sent an answer to an offer we never made")
            return False
        elif kind == "ask_streams_response":
            asked = success.ask_streams_response
            if asked.WhichOneof("response") == "failure":
                reason = asked.failure.WhichOneof("error") or "failure"
                self._fail(reason, f"Ajax refused the live stream ({reason})")
                return False
            self.outcome.granted_streams = len(asked.success.streams)
        return True

    async def _run(self) -> None:
        from v3.mobilegwsvc.service.stream_webrtc import endpoint_pb2_grpc  # noqa: PLC0415

        stub = endpoint_pb2_grpc.StreamWebrtcServiceStub(self._client._get_channel())
        metadata = self._client._session.get_call_metadata()
        call = stub.execute(self._requests(), metadata=metadata)
        try:
            async with asyncio.timeout(ANSWER_TIMEOUT) as deadline:
                self._deadline = deadline
                async for msg in call:
                    if not self._handle(msg):
                        return
            if not self._closed:
                self._fail("stream_ended", "Ajax closed the video session")
        except TimeoutError:
            if self.outcome.stage != "answered":
                self._fail(
                    "timeout", f"no answer to the camera's offer (stage {self.outcome.stage})"
                )
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
