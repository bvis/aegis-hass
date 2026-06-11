"""Tests for the firebase-messaging reconnect-storm logging guard (#285).

The guard defuses the CPU bomb described in #285: firebase-messaging's
listen loop logging a full, ever-growing traceback on every iteration while
its stream reader is poisoned. Stripping `exc_info` in a logging.Filter
(which runs before handlers format the record) keeps the one-line message
but skips the quadratic traceback formatting on Python 3.14.
"""

from __future__ import annotations

import logging
import sys

from custom_components.aegis_ajax.notification_fcm_guard import (
    FCM_PUSH_LOGGER_NAME,
    FcmExceptionLogThrottle,
    attach_fcm_log_guard,
)


def _make_record(
    *, with_exc: bool = True, msg: str = "Unexpected exception during read\n"
) -> logging.LogRecord:
    exc_info = None
    if with_exc:
        try:
            raise ConnectionResetError("Connection lost")
        except ConnectionResetError:
            exc_info = sys.exc_info()
    return logging.LogRecord(
        name=FCM_PUSH_LOGGER_NAME,
        level=logging.ERROR,
        pathname="fcmpushclient.py",
        lineno=717,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )


class TestFcmExceptionLogThrottle:
    def test_under_threshold_keeps_exc_info(self) -> None:
        throttle = FcmExceptionLogThrottle(max_exceptions=3, window_seconds=60, clock=lambda: 0.0)
        for _ in range(3):
            record = _make_record()
            assert throttle.filter(record) is True
            assert record.exc_info is not None

    def test_over_threshold_strips_exc_info_but_keeps_record(self) -> None:
        throttle = FcmExceptionLogThrottle(max_exceptions=3, window_seconds=60, clock=lambda: 0.0)
        for _ in range(3):
            throttle.filter(_make_record())

        record = _make_record()
        # The record must still be emitted (returns True) — only the
        # traceback is dropped, so operators keep a one-line trace.
        assert throttle.filter(record) is True
        assert record.exc_info is None
        assert record.exc_text is None
        assert record.stack_info is None
        assert "traceback suppressed" in record.msg

    def test_window_expiry_restores_exc_info(self) -> None:
        now = {"t": 0.0}
        throttle = FcmExceptionLogThrottle(
            max_exceptions=2, window_seconds=60, clock=lambda: now["t"]
        )
        throttle.filter(_make_record())
        throttle.filter(_make_record())
        suppressed = _make_record()
        throttle.filter(suppressed)
        assert suppressed.exc_info is None

        now["t"] = 61.0
        record = _make_record()
        assert throttle.filter(record) is True
        assert record.exc_info is not None

    def test_records_without_exc_info_pass_untouched_and_uncounted(self) -> None:
        throttle = FcmExceptionLogThrottle(max_exceptions=1, window_seconds=60, clock=lambda: 0.0)
        for _ in range(5):
            record = _make_record(with_exc=False)
            assert throttle.filter(record) is True
            assert "traceback suppressed" not in record.msg
        # The plain records above must not have consumed the budget.
        record = _make_record()
        assert throttle.filter(record) is True
        assert record.exc_info is not None


class TestEndToEndSuppression:
    def test_handler_never_formats_traceback_past_threshold(self) -> None:
        """Storm simulation through the real logging pipeline.

        Emulates fcmpushclient's `_logger.exception(...)` per-iteration storm
        and asserts the handler (where traceback formatting — the actual CPU
        cost — happens) only ever formats the allowed number of tracebacks.
        """
        import io

        logger = logging.getLogger(FCM_PUSH_LOGGER_NAME)
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        original_filters = list(logger.filters)
        original_propagate = logger.propagate
        logger.addHandler(handler)
        logger.propagate = False
        try:
            attach_fcm_log_guard()
            for _ in range(20):
                try:
                    raise ConnectionResetError("Connection lost")
                except ConnectionResetError:
                    logger.exception("Unexpected exception during read\n")
            output = stream.getvalue()
            assert output.count("Traceback") == 5
            assert output.count("traceback suppressed") == 15
        finally:
            logger.removeHandler(handler)
            logger.propagate = original_propagate
            for f in list(logger.filters):
                if f not in original_filters:
                    logger.removeFilter(f)


class TestAttachFcmLogGuard:
    def test_attach_is_idempotent(self) -> None:
        logger = logging.getLogger(FCM_PUSH_LOGGER_NAME)
        original_filters = list(logger.filters)
        try:
            attach_fcm_log_guard()
            attach_fcm_log_guard()
            guards = [f for f in logger.filters if isinstance(f, FcmExceptionLogThrottle)]
            assert len(guards) == 1
        finally:
            for f in list(logger.filters):
                if f not in original_filters:
                    logger.removeFilter(f)
