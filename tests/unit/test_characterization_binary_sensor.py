"""Characterization tests for binary_sensor entity generation.

Snapshots the exact set of binary_sensor entities generated per device_type on
`main` before the DeviceHandler architecture refactor (#332).

Ensures 0 regression or behavior drift across all modelled and unmodelled
device families.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.aegis_ajax.api.models import Device, DeviceState
from custom_components.aegis_ajax.binary_sensor import async_setup_entry
from custom_components.aegis_ajax.device_handlers import _DEVICE_HANDLERS

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "binary_sensor_characterization.json"


def _load_fixture() -> dict[str, list[dict[str, str | bool | None]]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class TestBinarySensorCharacterization:
    """Characterization test suite for binary sensor setup."""

    def test_fixture_covers_all_known_device_types(self) -> None:
        """Verify the fixture covers every key in _DEVICE_HANDLERS plus unmapped."""
        fixture_data = _load_fixture()
        expected_keys = set(_DEVICE_HANDLERS.keys()) | {"unmapped_unknown_device"}
        assert set(fixture_data.keys()) == expected_keys

    @pytest.mark.asyncio
    @pytest.mark.parametrize("device_type", list(_load_fixture().keys()))
    async def test_device_type_entity_characterization(self, device_type: str) -> None:
        """Assert exact entity characterization snapshot for each device type."""
        fixture_data = _load_fixture()
        expected_entities = fixture_data[device_type]

        device_id = "test_device"
        mock_coordinator = MagicMock()
        mock_device = Device(
            id=device_id,
            hub_id="hub_1",
            name="Test Device",
            device_type=device_type,
            room_id="room_1",
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )
        mock_coordinator.devices = {device_id: mock_device}
        mock_coordinator.spaces = {}
        mock_coordinator.rooms = {}
        mock_coordinator.hub_registry_id.return_value = "hub_reg_1"

        entry = MagicMock()
        entry.entry_id = "entry_1"
        entry.runtime_data = mock_coordinator

        added_entities: list = []

        def _add_entities(new_entities: list) -> None:
            added_entities.extend(new_entities)

        await async_setup_entry(MagicMock(), entry, _add_entities)

        actual_entities = [
            {
                "unique_id": e.unique_id,
                "device_class": e.device_class.value
                if hasattr(e.device_class, "value")
                else e.device_class,
                "entity_category": e.entity_category.value
                if hasattr(e.entity_category, "value")
                else e.entity_category,
                "enabled_by_default": e.entity_registry_enabled_default,
            }
            for e in added_entities
        ]

        assert actual_entities == expected_entities

    @pytest.mark.asyncio
    @pytest.mark.parametrize("device_type", ["button", "light_switch_dimmer"])
    async def test_new_platform_registration_preserves_unmapped_binary_sensor_shape(
        self, monkeypatch: pytest.MonkeyPatch, device_type: str
    ) -> None:
        """New registrations retain the former tamper and standard-diagnostics fallback."""
        fixture_data = _load_fixture()
        monkeypatch.delitem(_DEVICE_HANDLERS, device_type)

        device_id = "test_device"
        mock_coordinator = MagicMock()
        mock_coordinator.devices = {
            device_id: Device(
                id=device_id,
                hub_id="hub_1",
                name="Test Device",
                device_type=device_type,
                room_id="room_1",
                group_id=None,
                state=DeviceState.ONLINE,
                malfunctions=0,
                bypassed=False,
                statuses={},
                battery=None,
            )
        }
        mock_coordinator.spaces = {}
        mock_coordinator.rooms = {}
        mock_coordinator.hub_registry_id.return_value = "hub_reg_1"
        entry = MagicMock(entry_id="entry_1", runtime_data=mock_coordinator)
        added_entities: list = []

        await async_setup_entry(MagicMock(), entry, added_entities.extend)

        actual_entities = [
            {
                "unique_id": entity.unique_id,
                "device_class": entity.device_class.value
                if hasattr(entity.device_class, "value")
                else entity.device_class,
                "entity_category": entity.entity_category.value
                if hasattr(entity.entity_category, "value")
                else entity.entity_category,
                "enabled_by_default": entity.entity_registry_enabled_default,
            }
            for entity in added_entities
        ]

        assert actual_entities == fixture_data[device_type]
