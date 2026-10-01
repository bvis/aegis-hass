"""Shared PIN and caller authorization for sensitive operations."""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError, Unauthorized, UnknownUser

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.auth.models import User
    from homeassistant.core import HomeAssistant, ServiceCall


def validate_pin(options: Mapping[str, Any], code: object) -> None:
    """Reject missing, malformed or incorrect codes whenever a PIN is enabled."""
    if not options.get("use_pin_code", False):
        return
    stored = options.get("pin_code_hash", "")
    if not isinstance(code, str) or not code or not isinstance(stored, str):
        raise HomeAssistantError("Invalid alarm code")
    computed = hashlib.sha256(code.encode()).hexdigest()
    if not hmac.compare_digest(computed, stored):
        raise HomeAssistantError("Invalid alarm code")


async def async_call_user(hass: HomeAssistant, call: ServiceCall) -> User | None:
    """Resolve an API caller; no user ID denotes a trusted internal HA action."""
    if call.context.user_id is None:
        return None
    user = await hass.auth.async_get_user(call.context.user_id)
    if user is None or not user.is_active:
        raise UnknownUser(context=call.context)
    return user


async def async_require_admin(hass: HomeAssistant, call: ServiceCall) -> None:
    """Restrict account/session and privacy administration to HA admins."""
    user = await async_call_user(hass, call)
    if user is not None and not user.is_admin:
        raise Unauthorized(context=call.context)
