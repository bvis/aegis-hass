"""Shared PIN and caller authorization for sensitive operations."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ServiceValidationError

from custom_components.aegis_ajax.const import (
    CONF_ARM_REQUIRES_PIN,
    DEFAULT_ARM_REQUIRES_PIN,
    DOMAIN,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOGGER = logging.getLogger(__name__)

# Wrong codes are counted per stored PIN, in memory: a restart or a new PIN
# starts again. Missing codes are refused without counting, so an automation
# that forgets the code can't lock anybody out.
MAX_FAILED_PIN_ATTEMPTS = 5
PIN_LOCKOUT_SECONDS = 300
_failed_attempts: dict[str, tuple[int, float]] = {}  # stored PIN -> (failures, locked until)
_failed_attempts_lock = threading.Lock()  # validate_pin runs in executor threads

# Salted scrypt. Older entries hold a plain SHA-256 and keep working until
# the PIN is set again in the options.
_SCRYPT_PREFIX = "scrypt"
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_pin(code: str) -> str:
    """Return the stored form of a PIN. CPU-heavy: call it in the executor."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(code.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return f"{_SCRYPT_PREFIX}${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def is_legacy_pin_hash(stored: object) -> bool:
    """True for a PIN stored in the old format, which should be set again."""
    return isinstance(stored, str) and bool(stored) and not stored.startswith(f"{_SCRYPT_PREFIX}$")


def _pin_matches(code: str, stored: str) -> bool:
    if not stored.startswith(f"{_SCRYPT_PREFIX}$"):
        return hmac.compare_digest(hashlib.sha256(code.encode()).hexdigest(), stored)
    try:
        _, n, r, p, salt, digest = stored.split("$")
        computed = hashlib.scrypt(
            code.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p)
        ).hex()
    except ValueError:
        return False
    return hmac.compare_digest(computed, digest)


def arm_requires_pin(options: Mapping[str, Any]) -> bool:
    """Whether arming needs the PIN: a PIN is on and not limited to disarm (#642)."""
    return bool(options.get("use_pin_code", False)) and bool(
        options.get(CONF_ARM_REQUIRES_PIN, DEFAULT_ARM_REQUIRES_PIN)
    )


def pin_is_correct(options: Mapping[str, Any], code: object) -> bool:
    """Whether the code is the stored PIN, without counting a wrong one.

    For checks that refuse nothing (the #568 transition warning). CPU-heavy.
    """
    stored = options.get("pin_code_hash", "")
    return (
        isinstance(code, str)
        and bool(code)
        and isinstance(stored, str)
        and _pin_matches(code, stored)
    )


def validate_pin(options: Mapping[str, Any], code: object) -> None:
    """Reject missing, malformed or incorrect codes whenever a PIN is enabled.

    After MAX_FAILED_PIN_ATTEMPTS wrong codes in a row every code is refused
    for PIN_LOCKOUT_SECONDS, the right one included.

    CPU-heavy when a PIN is set: call it in the executor.
    """
    if not options.get("use_pin_code", False):
        return
    stored = options.get("pin_code_hash", "")
    if not isinstance(code, str) or not code or not isinstance(stored, str):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="invalid_alarm_code"
        )
    with _failed_attempts_lock:
        _, locked_until = _failed_attempts.get(stored, (0, 0.0))
    remaining = locked_until - time.monotonic()
    if remaining > 0:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="alarm_code_locked",
            translation_placeholders={"minutes": str(int(remaining // 60) + 1)},
        )
    if _pin_matches(code, stored):
        with _failed_attempts_lock:
            _failed_attempts.pop(stored, None)
        return
    with _failed_attempts_lock:
        failures = _failed_attempts.get(stored, (0, 0.0))[0] + 1
        if failures >= MAX_FAILED_PIN_ATTEMPTS:
            _failed_attempts[stored] = (0, time.monotonic() + PIN_LOCKOUT_SECONDS)
        else:
            _failed_attempts[stored] = (failures, 0.0)
    if failures >= MAX_FAILED_PIN_ATTEMPTS:
        _LOGGER.warning(
            "Wrong alarm code entered %d times in a row: codes are refused for %d minutes",
            failures,
            PIN_LOCKOUT_SECONDS // 60,
        )
    else:
        _LOGGER.warning(
            "Wrong alarm code entered (%d of %d before codes are refused for %d minutes)",
            failures,
            MAX_FAILED_PIN_ATTEMPTS,
            PIN_LOCKOUT_SECONDS // 60,
        )
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_alarm_code")
