"""Legacy calls keep working while Repairs explain upcoming service requirements."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import issue_registry as ir

from custom_components.aegis_ajax import async_setup
from custom_components.aegis_ajax.const import DOMAIN
from custom_components.aegis_ajax.service_security import hash_pin

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from homeassistant.core import HomeAssistant

SENSITIVE = (
    ("force_arm", {}, "security_api", "arm"),
    ("force_arm_night", {}, "security_api", "arm_night_mode"),
    ("disarm_night_mode", {}, "security_api", "disarm_from_night_mode"),
    ("press_panic_button", {"confirm": True}, "spaces_api", "press_panic_button"),
    ("set_photo_on_demand_mode", {"user": True}, "devices_api", "set_photo_on_demand_mode"),
)


@pytest.fixture
async def accounts(hass: HomeAssistant) -> AsyncIterator[list[SimpleNamespace]]:
    entries = []
    for account, spaces in (("one", ["a", "b"]), ("two", ["c"])):
        coordinator = MagicMock()
        coordinator._space_ids = spaces
        coordinator.spaces = {space: SimpleNamespace(hub_id=space) for space in spaces}
        coordinator.async_request_refresh = AsyncMock()
        for _, _, api, method in SENSITIVE:
            setattr(getattr(coordinator, api), method, AsyncMock())
        entries.append(SimpleNamespace(entry_id=account, runtime_data=coordinator, options={}))
    registry = MagicMock()
    registry.async_get.side_effect = lambda eid: (
        SimpleNamespace(platform=DOMAIN, unique_id=f"aegis_ajax_alarm_{eid.split('.')[1]}")
        if eid in {"alarm_control_panel.a", "alarm_control_panel.b", "alarm_control_panel.c"}
        else None
    )
    with (
        patch.object(hass.config_entries, "async_loaded_entries", return_value=entries),
        patch("homeassistant.helpers.entity_registry.async_get", return_value=registry),
    ):
        await async_setup(hass, {})
        yield entries


@pytest.mark.parametrize(("service", "data", "api", "method"), SENSITIVE)
async def test_untargeted_actions_still_reach_every_space_and_raise_one_repair(
    hass: HomeAssistant,
    accounts: list[SimpleNamespace],
    service: str,
    data: dict,
    api: str,
    method: str,
) -> None:
    await hass.services.async_call(DOMAIN, service, data, blocking=True)
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, f"service_target_required:{service}")
    assert issue is not None and issue.is_persistent
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_placeholders == {"service": f"{DOMAIN}.{service}"}
    for entry in accounts:
        action = getattr(getattr(entry.runtime_data, api), method)
        assert [c.args[0] for c in action.await_args_list] == entry.runtime_data._space_ids
    assert len(registry.issues) == 1


@pytest.mark.parametrize(("service", "data", "api", "method"), SENSITIVE)
async def test_explicit_target_does_not_warn_or_reach_other_accounts(
    hass: HomeAssistant,
    accounts: list[SimpleNamespace],
    service: str,
    data: dict,
    api: str,
    method: str,
) -> None:
    await hass.services.async_call(
        DOMAIN, service, {**data, "entity_id": "alarm_control_panel.c"}, blocking=True
    )
    assert not ir.async_get(hass).issues
    getattr(getattr(accounts[0].runtime_data, api), method).assert_not_awaited()
    getattr(getattr(accounts[1].runtime_data, api), method).assert_awaited_once()


@pytest.mark.parametrize("service", ["force_arm", "force_arm_night"])
@pytest.mark.parametrize("code", [None, "", "wrong", 1234, "0123"])
@pytest.mark.parametrize("salted", [False, True])
async def test_pin_checks_only_warn_and_validate_in_executor(
    hass: HomeAssistant,
    accounts: list[SimpleNamespace],
    service: str,
    code: object,
    salted: bool,
) -> None:
    stored = (
        await hass.async_add_executor_job(hash_pin, "0123")
        if salted
        else hashlib.sha256(b"0123").hexdigest()
    )
    accounts[0].options = {"use_pin_code": True, "pin_code_hash": stored}
    with patch.object(
        hass, "async_add_executor_job", wraps=hass.async_add_executor_job
    ) as executor:
        await hass.services.async_call(
            DOMAIN,
            service,
            {"entity_id": ["alarm_control_panel.a", "alarm_control_panel.b"], "code": code},
            blocking=True,
        )
        assert executor.call_count == (1 if isinstance(code, str) and code else 0)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"service_pin_required:{service}:one")
    assert (issue is not None) == (code != "0123")
    if issue:
        assert issue.is_persistent and not issue.is_fixable
        assert issue.data is None
        assert issue.translation_placeholders == {
            "service": f"{DOMAIN}.{service}",
            "entry_id": "one",
        }
    action = (
        accounts[0].runtime_data.security_api.arm
        if service == "force_arm"
        else accounts[0].runtime_data.security_api.arm_night_mode
    )
    assert action.await_count == 2  # A bad PIN must not break existing calls yet.
    assert len(ir.async_get(hass).issues) <= 1  # One notice per account, not per space.


async def test_pin_warnings_only_cover_affected_accounts(
    hass: HomeAssistant,
    accounts: list[SimpleNamespace],
) -> None:
    accounts[1].options = {"use_pin_code": True, "pin_code_hash": "invalid"}
    await hass.services.async_call(
        DOMAIN, "force_arm", {"entity_id": "alarm_control_panel.a"}, blocking=True
    )
    assert not ir.async_get(hass).issues
    await hass.services.async_call(DOMAIN, "force_arm", {}, blocking=True)
    assert set(ir.async_get(hass).issues) == {
        (DOMAIN, "service_target_required:force_arm"),
        (DOMAIN, "service_pin_required:force_arm:two"),
    }


async def test_valid_calls_do_not_clear_warnings_from_other_automations(
    hass: HomeAssistant,
    accounts: list[SimpleNamespace],
) -> None:
    accounts[0].options = {
        "use_pin_code": True,
        "pin_code_hash": hashlib.sha256(b"0123").hexdigest(),
    }
    for _ in range(2):
        await hass.services.async_call(DOMAIN, "force_arm", {}, blocking=True)
    registry = ir.async_get(hass)
    assert len(registry.issues) == 2
    await hass.services.async_call(
        DOMAIN,
        "force_arm",
        {"entity_id": "alarm_control_panel.a", "code": "0123"},
        blocking=True,
    )
    assert len(registry.issues) == 2


async def test_rejected_and_unmatched_calls_do_not_raise_transition_repairs(
    hass: HomeAssistant,
    accounts: list[SimpleNamespace],
) -> None:
    accounts[0].options = {"use_pin_code": True}
    for service, data in (("press_panic_button", {}), ("set_photo_on_demand_mode", {})):
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(DOMAIN, service, data, blocking=True)
    await hass.services.async_call(
        DOMAIN, "force_arm", {"entity_id": "alarm_control_panel.missing"}, blocking=True
    )
    user = MagicMock(is_active=True)
    user.permissions.check_entity.return_value = False
    with (
        patch.object(hass.auth, "async_get_user", AsyncMock(return_value=user)),
        pytest.raises(Unauthorized),
    ):
        await hass.services.async_call(
            DOMAIN,
            "force_arm",
            {"entity_id": "alarm_control_panel.a"},
            blocking=True,
            context=Context(user_id="denied"),
        )
    assert not ir.async_get(hass).issues
    accounts[0].runtime_data.security_api.arm.assert_not_awaited()


async def test_read_only_resolver_and_empty_targets_do_not_warn(
    hass: HomeAssistant,
    accounts: list[SimpleNamespace],
) -> None:
    from homeassistant.core import ServiceCall

    from custom_components.aegis_ajax import _resolve_target_space_ids

    targets = await _resolve_target_space_ids(
        hass, ServiceCall(hass, DOMAIN, "refresh_alarm_images")
    )
    assert len(targets) == 3
    assert not ir.async_get(hass).issues
    for entry in accounts:
        entry.runtime_data._space_ids = []
    await hass.services.async_call(DOMAIN, "force_arm", {}, blocking=True)
    assert not ir.async_get(hass).issues


def test_transition_translations_keep_required_placeholders() -> None:
    root = Path(__file__).resolve().parents[2] / "custom_components" / DOMAIN
    for path in [root / "strings.json", *sorted((root / "translations").glob("*.json"))]:
        doc = json.loads(path.read_text())
        for key, placeholders in (
            ("service_target_required", {"service"}),
            ("service_pin_required", {"service", "entry_id"}),
        ):
            assert set(re.findall(r"\{(\w+)\}", doc["issues"][key]["description"])) == placeholders
