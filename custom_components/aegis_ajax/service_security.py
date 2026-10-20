"""Shared PIN and caller authorization for sensitive operations."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ServiceValidationError

from custom_components.aegis_ajax.const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Mapping

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


def validate_pin(options: Mapping[str, Any], code: object) -> None:
    """Reject missing, malformed or incorrect codes whenever a PIN is enabled.

    CPU-heavy when a PIN is set: call it in the executor.
    """
    if not options.get("use_pin_code", False):
        return
    stored = options.get("pin_code_hash", "")
    if not isinstance(code, str) or not code or not isinstance(stored, str):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="invalid_alarm_code"
        )
    if not _pin_matches(code, stored):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="invalid_alarm_code"
        )
