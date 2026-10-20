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
    hass.config_entries.async_loaded_entries.return_value = [entry]
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


def test_new_pins_are_salted() -> None:
    """The same PIN stored twice gives two different values, and both check out."""
    from custom_components.aegis_ajax.service_security import hash_pin, is_legacy_pin_hash

    first, second = hash_pin("1234"), hash_pin("1234")
    assert first != second
    for stored in (first, second):
        assert not is_legacy_pin_hash(stored)
        validate_pin({"use_pin_code": True, "pin_code_hash": stored}, "1234")
        with pytest.raises(HomeAssistantError):
            validate_pin({"use_pin_code": True, "pin_code_hash": stored}, "1235")


def test_old_format_still_accepted_and_flagged() -> None:
    from custom_components.aegis_ajax.service_security import is_legacy_pin_hash

    old = hashlib.sha256(b"1234").hexdigest()
    assert is_legacy_pin_hash(old)
    validate_pin({"use_pin_code": True, "pin_code_hash": old}, "1234")


def test_malformed_stored_value_rejects() -> None:
    with pytest.raises(HomeAssistantError):
        validate_pin({"use_pin_code": True, "pin_code_hash": "scrypt$bad"}, "1234")


@pytest.mark.parametrize(
    ("options", "expect_issue"),
    [
        ({"use_pin_code": True, "pin_code_hash": hashlib.sha256(b"1").hexdigest()}, True),
        ({"use_pin_code": True, "pin_code_hash": "scrypt$16384$8$1$00$00"}, False),
        ({"use_pin_code": False, "pin_code_hash": hashlib.sha256(b"1").hexdigest()}, False),
    ],
)
def test_old_pin_format_raises_a_repair(options: dict, expect_issue: bool) -> None:
    from custom_components.aegis_ajax.repairs import async_check_pin_code_format

    hass = MagicMock()
    with (
        patch("custom_components.aegis_ajax.repairs.ir.async_create_issue") as create,
        patch("custom_components.aegis_ajax.repairs.ir.async_delete_issue") as delete,
    ):
        async_check_pin_code_format(hass, entry_id="e1", options=options)
    assert create.called is expect_issue
    assert delete.called is not expect_issue
