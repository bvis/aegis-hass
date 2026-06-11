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
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

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


def attach_fcm_log_guard() -> None:
    """Attach the throttle to firebase-messaging's push-client logger.

    Idempotent: reloads and supervised client restarts call this freely
    without stacking duplicate filters.
    """
    logger = logging.getLogger(FCM_PUSH_LOGGER_NAME)
    if any(isinstance(f, FcmExceptionLogThrottle) for f in logger.filters):
        return
    logger.addFilter(FcmExceptionLogThrottle())
