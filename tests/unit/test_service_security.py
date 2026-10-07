"""Authorization and PIN boundaries without changing legacy service defaults."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import Context, ServiceCall
from homeassistant.exceptions import HomeAssistantError, Unauthorized, UnknownUser

from custom_components.aegis_ajax import _async_handle_force_arm
from custom_components.aegis_ajax.service_security import validate_pin


@pytest.mark.parametrize("code", [None, "", "wrong", 1234])
def test_pin_rejects_invalid_codes(code: object) -> None:
    options = {"use_pin_code": True, "pin_code_hash": hashlib.sha256(b"1234").hexdigest()}
    with pytest.raises(HomeAssistantError):
        validate_pin(options, code)


def test_pin_accepts_correct_code_and_disabled_pin() -> None:
    validate_pin(
        {"use_pin_code": True, "pin_code_hash": hashlib.sha256(b"1234").hexdigest()}, "1234"
    )
    validate_pin({}, None)


@pytest.mark.parametrize(
    "caller", ["allowed", "denied", "mixed", "unknown", "inactive", "internal"]
)
async def test_all_explicit_targets_authorized_before_action(caller: str) -> None:
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator._space_ids = ["one", "two"]
    coordinator.security_api.arm = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    # A configured PIN must not become a new requirement for force_arm in this PR.
    entry = SimpleNamespace(runtime_data=coordinator, options={"use_pin_code": True})
    hass.config_entries.async_entries.return_value = [entry]
    user = MagicMock(is_active=caller != "inactive")
    user.permissions.check_entity.side_effect = lambda eid, policy: (
        caller != "denied" and not (caller == "mixed" and eid.endswith("two"))
    )
    hass.auth.async_get_user = AsyncMock(return_value=None if caller == "unknown" else user)
    registry = MagicMock()
    registry.async_get.side_effect = lambda eid: SimpleNamespace(
        platform="aegis_ajax", unique_id="aegis_ajax_alarm_" + eid.split(".")[1]
    )
    call = ServiceCall(
        hass,
        "aegis_ajax",
        "force_arm",
        {"entity_id": ["alarm_control_panel.one", "alarm_control_panel.two"]},
        context=Context(user_id=None if caller == "internal" else "user"),
    )
    with patch("homeassistant.helpers.entity_registry.async_get", return_value=registry):
        if caller in {"unknown", "inactive"}:
            with pytest.raises(UnknownUser):
                await _async_handle_force_arm(hass, call)
        elif caller in {"denied", "mixed"}:
            with pytest.raises(Unauthorized):
                await _async_handle_force_arm(hass, call)
        else:
            await _async_handle_force_arm(hass, call)
    assert coordinator.security_api.arm.await_count == (
        2 if caller in {"allowed", "internal"} else 0
    )
    if caller == "internal":
        hass.auth.async_get_user.assert_not_called()
