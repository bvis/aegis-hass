"""Account-specific panels retain existing registry identities during migration."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.aegis_ajax.alarm_control_panel import async_setup_entry
from custom_components.aegis_ajax.api.models import Group, Space
from custom_components.aegis_ajax.const import ConnectionStatus, SecurityState

if TYPE_CHECKING:
    from pathlib import Path


async def test_migration_preserves_entity_id_and_other_account(tmp_path: Path) -> None:
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = MagicMock()
    registry = er.async_get(hass)
    await registry.async_load()
    with patch.object(registry, "async_schedule_save"):
        alice = SimpleNamespace(entry_id="alice", pref_disable_new_entities=False, disabled_by=None)
        bob = SimpleNamespace(entry_id="bob", pref_disable_new_entities=False, disabled_by=None)
        old = registry.async_get_or_create(
            "alarm_control_panel",
            "aegis_ajax",
            "aegis_ajax_alarm_space",
            config_entry=alice,
            suggested_object_id="existing_alarm",
        )
        old_group = registry.async_get_or_create(
            "alarm_control_panel",
            "aegis_ajax",
            "aegis_ajax_alarm_space_group_group",
            config_entry=alice,
            suggested_object_id="existing_group",
        )
        other = registry.async_get_or_create(
            "alarm_control_panel",
            "aegis_ajax",
            "aegis_ajax_alarm_bob_space",
            config_entry=bob,
            suggested_object_id="bobs_alarm",
        )
        registry.async_update_entity(old.entity_id, name="Keep my name")
        space = Space(
            id="space",
            hub_id="hub",
            name="Home",
            security_state=SecurityState.DISARMED,
            connection_status=ConnectionStatus.ONLINE,
            malfunctions_count=0,
            group_mode_enabled=True,
            groups=(
                Group(
                    id="group",
                    space_id="space",
                    name="Downstairs",
                    security_state=SecurityState.DISARMED,
                ),
            ),
        )
        for entry in (alice, bob):
            entry.runtime_data = MagicMock()
            entry.runtime_data.config_entry = entry
            entry.runtime_data.spaces = {"space": space}
            entry.runtime_data.devices = {}
        panels = []
        await async_setup_entry(hass, alice, panels.extend)
        assert registry.async_get(old.entity_id).unique_id == "aegis_ajax_alarm_alice_space"
        assert registry.async_get(old.entity_id).name == "Keep my name"
        assert (
            registry.async_get(old_group.entity_id).unique_id
            == "aegis_ajax_alarm_alice_space_group_group"
        )
        assert registry.async_get(other.entity_id).unique_id == "aegis_ajax_alarm_bob_space"
        assert {panel.unique_id for panel in panels} == {
            "aegis_ajax_alarm_alice_space",
            "aegis_ajax_alarm_alice_space_group_group",
        }
        # Repeated setup must not create another migration or another identity.
        reloaded = []
        await async_setup_entry(hass, alice, reloaded.extend)
        assert [panel.unique_id for panel in reloaded] == [panel.unique_id for panel in panels]
        bobs_panels = []
        await async_setup_entry(hass, bob, bobs_panels.extend)
        assert {panel.unique_id for panel in panels}.isdisjoint(
            panel.unique_id for panel in bobs_panels
        )
