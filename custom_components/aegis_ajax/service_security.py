"""Shared PIN and caller authorization for sensitive operations."""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ServiceValidationError

from custom_components.aegis_ajax.const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Mapping


def validate_pin(options: Mapping[str, Any], code: object) -> None:
    """Reject missing, malformed or incorrect codes whenever a PIN is enabled."""
    if not options.get("use_pin_code", False):
        return
    stored = options.get("pin_code_hash", "")
    if not isinstance(code, str) or not code or not isinstance(stored, str):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="invalid_alarm_code"
        )
    computed = hashlib.sha256(code.encode()).hexdigest()
    if not hmac.compare_digest(computed, stored):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="invalid_alarm_code"
        )
