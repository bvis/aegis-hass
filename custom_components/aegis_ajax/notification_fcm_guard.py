"""Logging guard against firebase-messaging reconnect storms (#285).

On 2026-06-11 a Google-side MCS condition (connect succeeds, session resets
shortly after) drove `firebase_messaging==0.4.5`'s listen loop into logging a
full traceback on every iteration. asyncio re-raises the same exception
object stored on the poisoned `StreamReader`, so its `__traceback__` grows a
few frames per iteration, and on Python 3.14 traceback formatting runs
`ast.parse` per frame (caret anchors) — quadratic cost executed inside the
HA event loop. Result: event loop pegged at 100%, watchdog kill, crash loop.

Logging filters run before handlers format the record, so stripping
`exc_info` here defuses the CPU bomb regardless of what the library does.
The one-line message still gets through, keeping a trace for operators.

Revisit when the upstream fix ships (sdb9696/firebase-messaging#39 logs the
first occurrence only and yields per iteration): once the requirement is
bumped past a release carrying it, this throttle may be slimmable — tracked
in #297.
"""

from __future__ import annotations

import logging
import time
from base64 import urlsafe_b64decode
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

# Our own logger name, so tests can isolate the decrypt guard's records from
# the library's.
GUARD_LOGGER_NAME = __name__

# The module logger firebase-messaging's `_listen` loop emits on.
FCM_PUSH_LOGGER_NAME = "firebase_messaging.fcmpushclient"

# More than this many exception logs inside the window is a storm, not
# operations: healthy reconnects log at most one exception per reset cycle
# (3s+ apart), so 5-in-60s only triggers on pathological tight loops.
DEFAULT_MAX_EXCEPTIONS = 5
DEFAULT_WINDOW_SECONDS = 60.0


class FcmExceptionLogThrottle(logging.Filter):
    """Strip tracebacks from over-frequent exception records.

    Never drops a record — `filter` always returns True. Past the threshold
    the record loses `exc_info` (and the cached `exc_text` / `stack_info`)
    and gains a short suffix explaining the suppression, so the message
    itself remains visible at one line per occurrence.
    """

    def __init__(
        self,
        max_exceptions: int = DEFAULT_MAX_EXCEPTIONS,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._max = max_exceptions
        self._window = window_seconds
        self._clock = clock
        self._timestamps: deque[float] = deque()

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.exc_info or record.exc_info == (None, None, None):
            return True
        now = self._clock()
        while self._timestamps and now - self._timestamps[0] > self._window:
            self._timestamps.popleft()
        if len(self._timestamps) < self._max:
            self._timestamps.append(now)
            return True
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        record.msg = (
            f"{str(record.msg).rstrip()} "
            f"[traceback suppressed: more than {self._max} exception logs "
            f"in {self._window:.0f}s]"
        )
        return True


def _pad_urlsafe_b64(value: str) -> str:
    """Return `value` with enough `=` for `urlsafe_b64decode` to accept it.

    `crypto-key` and `encryption` header values are URL-safe base64 that may
    legitimately arrive with the trailing padding stripped, at which point
    `urlsafe_b64decode` raises `binascii.Error: Incorrect padding` (#373).
    Over-padding is harmless — base64 stops at the first complete group — and
    it is exactly what firebase-messaging already does for the two stored key
    values it decodes in the same function. This just extends the same
    treatment to the two it missed.
    """
    return value + "========"


def _header_param(value: str, name: str) -> str:
    """Return parameter `name` from an upstream-sliced `;`-separated header value.

    The library slices the leading `dh=` / `salt=` off by length and keeps the
    rest, so a VAPID-signed push (`dh=<key>; p256ecdsa=<key>`) arrives here as
    `<key>; p256ecdsa=<key>` and decodes to a key that is not a P-256 point
    (`Invalid EC key`). Fermax started signing its pushes this way in
    2026-10; this keeps Ajax pushes working if it ever does too.
    """
    segments = [segment.strip() for segment in value.split(";")]
    for segment in segments:
        if segment.startswith(f"{name}="):
            return segment[len(name) + 1 :]
    return segments[0]


def install_fcm_decrypt_guard(client: Any) -> None:  # noqa: ANN401
    """Give our push client a padding-tolerant, non-fatal decrypt (#373).

    Two failures are being contained here, and the second is the severe one:

    1. **Malformed header values.** Unpadded base64 (`_pad_urlsafe_b64`) and
       signed headers carrying a second parameter (`_header_param`).
    2. **A raise tearing down the whole client.** `binascii.Error` is a
       `ValueError`, so the listen loop's `except (OSError, EOFError)` misses
       it and it reaches the outer `except Exception`, which shuts the client
       down. Worse, the decrypt happens *before* the library appends the
       persistent id and sends the selective ack, so the message is never
       acknowledged — the server redelivers it on the next connection and
       kills the client again. Our supervision (#285) faithfully restarts into
       the same frame, which is how one bad message becomes a permanent push
       outage that survives a host reboot.

    Returning `b""` keeps the handler on its normal path: it logs its own
    "Failed to decrypt data" line, hands the callback an empty payload (which
    carries no `ENCODED_DATA`, so our notification handler ignores it), and —
    crucially — reaches the acknowledgement. One event is lost instead of all
    future ones.

    The decrypt is our own and lands on this client instance only (the library
    calls `self._decrypt_raw_data`). Patching the shared `FcmPushClient` class
    used to chain us with any other integration doing the same — Fermax Blue
    did — so each logged the other's push errors as its own.

    Upstream fixes the padding in sdb9696/firebase-messaging#37, open and
    mergeable since June with no release carrying it. Drop this once a
    release ships it and the signed headers, tracked in #373.
    """
    import http_ece  # noqa: PLC0415
    from cryptography.hazmat.primitives.serialization import (  # noqa: PLC0415
        load_der_private_key,
    )

    state = {"warned": False}

    def _decrypt(
        credentials: dict[str, Any],
        crypto_key_str: str,
        salt_str: str,
        raw_data: bytes,
    ) -> bytes:
        try:
            keys = credentials["keys"]
            private_key = load_der_private_key(
                urlsafe_b64decode(_pad_urlsafe_b64(keys["private"])), password=None
            )
            decrypted: bytes = http_ece.decrypt(
                raw_data,
                salt=urlsafe_b64decode(_pad_urlsafe_b64(_header_param(salt_str, "salt"))),
                private_key=private_key,
                dh=urlsafe_b64decode(_pad_urlsafe_b64(_header_param(crypto_key_str, "dh"))),
                version="aesgcm",
                auth_secret=urlsafe_b64decode(_pad_urlsafe_b64(keys["secret"])),
            )
            return decrypted
        except Exception as exc:  # noqa: BLE001
            # First one at WARNING: at HA's default level a DEBUG-only line
            # would make this invisible, and losing pushes deserves a trace.
            # Repeats at DEBUG so a persistently bad sender can't spam.
            if state["warned"]:
                _LOGGER.debug("Skipped an undecryptable push frame: %s", exc)
            else:
                state["warned"] = True
                _LOGGER.warning(
                    "Could not decrypt an incoming push message (%s: %s); skipping "
                    "that one message. Real-time push stays up — without this the "
                    "push client would shut down and the message would be "
                    "redelivered indefinitely. See issue #373.",
                    type(exc).__name__,
                    exc,
                )
            return b""

    client._decrypt_raw_data = _decrypt  # noqa: SLF001


def attach_fcm_log_guard() -> None:
    """Attach the throttle to firebase-messaging's push-client logger.

    Idempotent: reloads and supervised client restarts call this freely
    without stacking duplicate filters.
    """
    logger = logging.getLogger(FCM_PUSH_LOGGER_NAME)
    if any(isinstance(f, FcmExceptionLogThrottle) for f in logger.filters):
        return
    logger.addFilter(FcmExceptionLogThrottle())
