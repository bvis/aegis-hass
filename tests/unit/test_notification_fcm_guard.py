"""Tests for the firebase-messaging reconnect-storm logging guard (#285).

The guard defuses the CPU bomb described in #285: firebase-messaging's
listen loop logging a full, ever-growing traceback on every iteration while
its stream reader is poisoned. Stripping `exc_info` in a logging.Filter
(which runs before handlers format the record) keeps the one-line message
but skips the quadratic traceback formatting on Python 3.14.
"""

from __future__ import annotations

import binascii
import inspect
import logging
import os
import sys
from base64 import urlsafe_b64decode, urlsafe_b64encode
from types import SimpleNamespace

import pytest

from custom_components.aegis_ajax.notification_fcm_guard import (
    FCM_PUSH_LOGGER_NAME,
    GUARD_LOGGER_NAME,
    FcmExceptionLogThrottle,
    _pad_urlsafe_b64,
    attach_fcm_log_guard,
    install_fcm_decrypt_guard,
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


class TestPadUrlsafeB64:
    """`_pad_urlsafe_b64` is the root-cause fix for #373.

    `crypto-key` / `encryption` header values are URL-safe base64 that may
    legitimately arrive without trailing `=`. The library pads the two stored
    key values it decodes but not these two, so an unpadded header raises
    `binascii.Error` — which is a `ValueError`, so the listen loop's
    `except (OSError, EOFError)` misses it and the client shuts down.
    """

    def test_pads_length_two_remainder(self) -> None:
        assert urlsafe_b64decode(_pad_urlsafe_b64("YWJjZA")) == b"abcd"

    def test_pads_length_three_remainder(self) -> None:
        assert urlsafe_b64decode(_pad_urlsafe_b64("YWJjZGU")) == b"abcde"

    def test_already_padded_is_unchanged_in_value(self) -> None:
        assert urlsafe_b64decode(_pad_urlsafe_b64("YWJj")) == b"abc"

    def test_explicit_padding_survives(self) -> None:
        assert urlsafe_b64decode(_pad_urlsafe_b64("YWJjZA==")) == b"abcd"

    def test_unpadded_input_raises_without_the_fix(self) -> None:
        """Pins the bug itself, so the test fails if the premise changes."""
        with pytest.raises(binascii.Error):
            urlsafe_b64decode("YWJjZGU")


def _b64(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@pytest.fixture
def push() -> tuple[dict[str, dict[str, str]], str, str, bytes]:
    """Credentials plus one push encrypted the way the sender does (aesgcm)."""
    http_ece = pytest.importorskip("http_ece")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    receiver = ec.generate_private_key(ec.SECP256R1())
    sender = ec.generate_private_key(ec.SECP256R1())
    secret, salt = os.urandom(16), os.urandom(16)
    point = serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    raw = http_ece.encrypt(
        b"payload",
        salt=salt,
        private_key=sender,
        dh=receiver.public_key().public_bytes(*point),
        version="aesgcm",
        auth_secret=secret,
    )
    der = receiver.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    credentials = {"keys": {"private": _b64(der), "secret": _b64(secret)}}
    return credentials, _b64(sender.public_key().public_bytes(*point)), _b64(salt), raw


def _guarded_client() -> SimpleNamespace:
    client = SimpleNamespace()
    install_fcm_decrypt_guard(client)
    return client


class TestFcmDecryptGuard:
    @pytest.mark.parametrize(
        "crypto_key",
        [
            "{dh}",  # unpadded, as the library hands it over after slicing "dh="
            "{dh}==",  # padded
            "{dh}; p256ecdsa={vapid}",  # signed push: the library keeps the VAPID key
            "6ecdsa={vapid};dh={dh}",  # signed, other order: "p25" sliced off instead
        ],
    )
    def test_decrypts_unpadded_padded_and_signed_headers(
        self, push: tuple[dict[str, dict[str, str]], str, str, bytes], crypto_key: str
    ) -> None:
        credentials, dh, salt, raw = push
        header = crypto_key.format(dh=dh, vapid="B" * 87)
        assert _guarded_client()._decrypt_raw_data(credentials, header, salt, raw) == b"payload"

    def test_undecodable_frame_returns_empty_instead_of_raising(
        self, push: tuple[dict[str, dict[str, str]], str, str, bytes]
    ) -> None:
        """The whole point: the listen loop must reach its acknowledgement.

        A frame we genuinely cannot decrypt must not propagate, because the
        exception would escape `_handle_data_message` before the library acks
        the message — leaving it unacked and redelivered forever (#373).
        """
        credentials, _dh, salt, raw = push
        assert _guarded_client()._decrypt_raw_data(credentials, "A" * 87, salt, raw) == b""

    def test_non_base64_garbage_is_swallowed(
        self, push: tuple[dict[str, dict[str, str]], str, str, bytes]
    ) -> None:
        credentials, _dh, salt, raw = push
        client = _guarded_client()
        assert client._decrypt_raw_data(credentials, "!!!not base64!!!", salt, raw) == b""

    def test_failure_logs_a_warning_once_then_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        """Symptom is invisible at HA's default level, so the first one is a
        WARNING; repeats drop to DEBUG so a persistent sender can't spam."""
        pytest.importorskip("http_ece")
        client = _guarded_client()
        with caplog.at_level(logging.DEBUG, logger=GUARD_LOGGER_NAME):
            for _ in range(3):
                client._decrypt_raw_data({}, "YWJj", "YWJj", b"")
        records = [r for r in caplog.records if r.name == GUARD_LOGGER_NAME]
        assert [r.levelno for r in records] == [
            logging.WARNING,
            logging.DEBUG,
            logging.DEBUG,
        ]


class TestFcmDecryptGuardAgainstRealLibrary:
    """Characterisation against the real `FcmPushClient`.

    The library calls `self._decrypt_raw_data`, so the guard set on an
    instance is what runs, and the shared class stays as shipped: another
    integration patching it never chains with us.
    """

    def test_unpadded_input_raises_in_the_unpatched_library(self) -> None:
        """The premise of #373. Fails if upstream ever fixes it, which is the
        signal to drop our decrypt."""
        pytest.importorskip("firebase_messaging")
        from firebase_messaging.fcmpushclient import FcmPushClient

        with pytest.raises(binascii.Error):
            FcmPushClient._decrypt_raw_data({}, "YWJjZGU", "YWJjZA", b"")

    def test_guard_lands_on_the_instance_and_leaves_the_class_alone(self) -> None:
        pytest.importorskip("firebase_messaging")
        from firebase_messaging.fcmpushclient import FcmPushClient

        shipped = inspect.getattr_static(FcmPushClient, "_decrypt_raw_data")
        client = FcmPushClient.__new__(FcmPushClient)
        install_fcm_decrypt_guard(client)

        assert client._decrypt_raw_data({}, "YWJjZGU", "YWJjZA", b"") == b""
        assert inspect.getattr_static(FcmPushClient, "_decrypt_raw_data") is shipped
