"""Regression tests for account routing and service authorization boundaries."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
    UnknownUser,
)

from custom_components.aegis_ajax import (
    _async_handle_disarm_night_mode,
    _async_handle_force_arm,
    _async_handle_force_arm_night,
    _async_handle_list_client_sessions,
    _async_handle_press_panic_button,
    _async_handle_set_photo_on_demand_mode,
    _async_handle_terminate_client_session,
    _async_handle_terminate_other_client_sessions,
    _resolve_target_space_ids,
)

type Accounts = tuple[
    SimpleNamespace, list[SimpleNamespace], dict[str, SimpleNamespace], SimpleNamespace
]
type Handler = Callable[..., Awaitable[object]]


@pytest.fixture
def accounts() -> Iterator[Accounts]:
    entries = []
    entities = {}
    for name in ("alice", "bob"):
        coordinator = SimpleNamespace(
            _space_ids=["shared"],
            security_api=SimpleNamespace(
                arm=AsyncMock(), arm_night_mode=AsyncMock(), disarm_from_night_mode=AsyncMock()
            ),
            spaces_api=SimpleNamespace(press_panic_button=AsyncMock()),
            async_request_refresh=AsyncMock(),
        )
        entry = SimpleNamespace(
            entry_id=name,
            runtime_data=coordinator,
            options={
                "use_pin_code": True,
                "pin_code_hash": hashlib.sha256(b"1234").hexdigest(),
            },
        )
        entries.append(entry)
        entities[f"alarm_control_panel.{name}"] = SimpleNamespace(
            config_entry_id=name,
            platform="aegis_ajax",
            disabled_by=None,
            unique_id=f"aegis_ajax_alarm_{name}_shared",
        )
    user = SimpleNamespace(
        is_active=True,
        is_admin=False,
        permissions=SimpleNamespace(check_entity=MagicMock(return_value=True)),
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda domain: entries),
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user)),
    )
    registry = SimpleNamespace(async_get=entities.get)
    with patch("homeassistant.helpers.entity_registry.async_get", return_value=registry):
        yield hass, entries, entities, user


def make_call(data: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(data=data, context=Context(user_id="ha-user"))


@pytest.mark.parametrize(
    "handler,method",
    [
        (_async_handle_force_arm, "arm"),
        (_async_handle_force_arm_night, "arm_night_mode"),
        (_async_handle_disarm_night_mode, "disarm_from_night_mode"),
    ],
)
@pytest.mark.parametrize("code", [None, "", "wrong", 1234])
async def test_all_alarm_services_reject_invalid_pin(
    accounts: Accounts, handler: Handler, method: str, code: object
) -> None:
    hass, entries, _, _ = accounts
    with pytest.raises(HomeAssistantError, match="Invalid alarm code"):
        await handler(hass, make_call({"entity_id": "alarm_control_panel.bob", "code": code}))
    for entry in entries:
        getattr(entry.runtime_data.security_api, method).assert_not_awaited()


@pytest.mark.parametrize("reverse", [False, True])
async def test_owning_account_used_independent_of_order(accounts: Accounts, reverse: bool) -> None:
    hass, entries, _, _ = accounts
    alice, bob = entries
    if reverse:
        entries.reverse()
    await _async_handle_disarm_night_mode(
        hass,
        make_call(
            {
                "entity_id": "alarm_control_panel.bob",
                "code": "1234",
            }
        ),
    )
    bob.runtime_data.security_api.disarm_from_night_mode.assert_awaited_once_with("shared")
    alice.runtime_data.security_api.disarm_from_night_mode.assert_not_awaited()


async def test_preflight_checks_every_target_before_first_command(accounts: Accounts) -> None:
    hass, entries, _, user = accounts
    user.permissions.check_entity.side_effect = [True, False]
    with pytest.raises(Unauthorized):
        await _async_handle_force_arm(
            hass,
            make_call(
                {
                    "entity_id": ["alarm_control_panel.alice", "alarm_control_panel.bob"],
                    "code": "1234",
                }
            ),
        )
    for entry in entries:
        entry.runtime_data.security_api.arm.assert_not_awaited()


async def test_preflight_checks_every_accounts_pin(accounts: Accounts) -> None:
    hass, entries, _, _ = accounts
    entries[1].options["pin_code_hash"] = hashlib.sha256(b"9876").hexdigest()
    with pytest.raises(HomeAssistantError):
        await _async_handle_force_arm(
            hass,
            make_call(
                {
                    "entity_id": ["alarm_control_panel.alice", "alarm_control_panel.bob"],
                    "code": "1234",
                }
            ),
        )
    for entry in entries:
        entry.runtime_data.security_api.arm.assert_not_awaited()


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"entity_id": []},
        {"entity_id": "all"},
        {"device_id": "hub"},
        {"entity_id": "alarm_control_panel.bob", "area_id": "home"},
    ],
)
async def test_no_implicit_all_space_target(accounts: Accounts, data: dict[str, object]) -> None:
    hass, _, _, _ = accounts
    with pytest.raises(ServiceValidationError):
        await _resolve_target_space_ids(hass, make_call(data))


async def test_no_fallback_to_another_account_when_owner_unloaded(accounts: Accounts) -> None:
    hass, entries, _, _ = accounts
    entries[1].runtime_data = None
    with pytest.raises(ServiceValidationError, match="not loaded"):
        await _resolve_target_space_ids(hass, make_call({"entity_id": "alarm_control_panel.bob"}))


async def test_group_target_cannot_expand_to_whole_space(accounts: Accounts) -> None:
    hass, _, entities, _ = accounts
    entities["alarm_control_panel.bob"].unique_id += "_group_1"
    with pytest.raises(ServiceValidationError, match="group panel"):
        await _resolve_target_space_ids(hass, make_call({"entity_id": "alarm_control_panel.bob"}))


@pytest.mark.parametrize(
    "handler",
    [
        _async_handle_list_client_sessions,
        _async_handle_terminate_client_session,
        _async_handle_terminate_other_client_sessions,
        _async_handle_set_photo_on_demand_mode,
    ],
)
async def test_account_administration_requires_admin(accounts: Accounts, handler: Handler) -> None:
    hass, _, _, _ = accounts
    with pytest.raises(Unauthorized):
        await handler(hass, make_call({"confirm": True, "session_id": 1, "user": True}))


async def test_unknown_caller_rejected(accounts: Accounts) -> None:
    hass, _, _, _ = accounts
    hass.auth.async_get_user.return_value = None
    with pytest.raises(UnknownUser):
        await _resolve_target_space_ids(hass, make_call({"entity_id": "alarm_control_panel.bob"}))


async def test_internal_automation_still_requires_pin(accounts: Accounts) -> None:
    hass, entries, _, _ = accounts
    call = make_call({"entity_id": "alarm_control_panel.bob"})
    call.context = Context()
    with pytest.raises(HomeAssistantError):
        await _async_handle_disarm_night_mode(hass, call)
    entries[1].runtime_data.security_api.disarm_from_night_mode.assert_not_awaited()


@pytest.mark.parametrize("confirm", ["false", "true", 1, False, None])
async def test_panic_requires_boolean_confirmation(accounts: Accounts, confirm: object) -> None:
    hass, _, _, _ = accounts
    with pytest.raises(ServiceValidationError, match="confirm"):
        await _async_handle_press_panic_button(hass, make_call({"confirm": confirm}))


async def test_admin_can_list_selected_accounts_sessions(accounts: Accounts) -> None:
    hass, entries, _, user = accounts
    user.is_admin = True
    alice, bob = entries
    alice.runtime_data.async_list_client_sessions = AsyncMock(return_value=[{"id": 1}])
    bob.runtime_data.async_list_client_sessions = AsyncMock(return_value=[{"id": 2}])
    result = await _async_handle_list_client_sessions(hass, make_call({"entry_id": "bob"}))
    assert result == {"sessions": [{"id": 2}]}
    bob.runtime_data.async_list_client_sessions.assert_awaited_once()
    alice.runtime_data.async_list_client_sessions.assert_not_awaited()


async def test_legacy_entity_uses_registry_owner_and_deduplicates(accounts: Accounts) -> None:
    hass, entries, entities, _ = accounts
    entities["alarm_control_panel.bob"].unique_id = "aegis_ajax_alarm_shared"
    entries[1].options = {"use_pin_code": False}
    await _async_handle_force_arm(
        hass,
        make_call(
            {
                "entity_id": ["alarm_control_panel.bob", "alarm_control_panel.bob"],
            }
        ),
    )
    entries[1].runtime_data.security_api.arm.assert_awaited_once_with("shared", ignore_alarms=True)
    entries[0].runtime_data.security_api.arm.assert_not_awaited()


async def test_disabled_entity_is_rejected(accounts: Accounts) -> None:
    hass, _, entities, _ = accounts
    entities["alarm_control_panel.bob"].disabled_by = "user"
    with pytest.raises(ServiceValidationError):
        await _resolve_target_space_ids(hass, make_call({"entity_id": "alarm_control_panel.bob"}))
