"""Tests for device automation triggers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.aegis_ajax.const import ALL_EVENT_TYPES, DOMAIN


def _hass_with_hub(hub_id: str = "hub-1") -> MagicMock:
    """A mock hass whose single aegis config entry knows `hub_id` as a hub."""
    hass = MagicMock()
    coordinator = MagicMock()
    space = MagicMock()
    space.hub_id = hub_id
    coordinator.spaces = {"space-1": space}
    coordinator.devices = {}
    entry = MagicMock()
    entry.runtime_data = coordinator
    hass.config_entries.async_entries.return_value = [entry]
    return hass


def _registry_returning(identifiers: set) -> MagicMock:
    registry = MagicMock()
    device = MagicMock()
    device.identifiers = identifiers
    registry.async_get.return_value = device
    return registry


class TestAsyncGetTriggers:
    @pytest.mark.asyncio
    async def test_hub_device_exposes_a_trigger_per_event_type(self) -> None:
        from custom_components.aegis_ajax import device_trigger

        hass = _hass_with_hub("hub-1")
        registry = _registry_returning({(DOMAIN, "hub-1")})
        with patch.object(device_trigger.dr, "async_get", return_value=registry):
            triggers = await device_trigger.async_get_triggers(hass, "ha-device-xyz")

        types = {t["type"] for t in triggers}
        assert types == set(ALL_EVENT_TYPES)
        for t in triggers:
            assert t["platform"] == "device"
            assert t["domain"] == DOMAIN
            assert t["device_id"] == "ha-device-xyz"

    @pytest.mark.asyncio
    async def test_non_hub_aegis_device_has_no_triggers(self) -> None:
        from custom_components.aegis_ajax import device_trigger

        hass = _hass_with_hub("hub-1")
        # identifier is some other device, not the hub
        registry = _registry_returning({(DOMAIN, "310A8DF4")})
        with patch.object(device_trigger.dr, "async_get", return_value=registry):
            triggers = await device_trigger.async_get_triggers(hass, "ha-device-doorbell")

        assert triggers == []

    @pytest.mark.asyncio
    async def test_unknown_device_returns_empty(self) -> None:
        from custom_components.aegis_ajax import device_trigger

        hass = _hass_with_hub("hub-1")
        registry = MagicMock()
        registry.async_get.return_value = None
        with patch.object(device_trigger.dr, "async_get", return_value=registry):
            triggers = await device_trigger.async_get_triggers(hass, "ghost")

        assert triggers == []

    @pytest.mark.asyncio
    async def test_foreign_device_returns_empty(self) -> None:
        from custom_components.aegis_ajax import device_trigger

        hass = _hass_with_hub("hub-1")
        registry = _registry_returning({("some_other_domain", "x")})
        with patch.object(device_trigger.dr, "async_get", return_value=registry):
            triggers = await device_trigger.async_get_triggers(hass, "foreign")

        assert triggers == []


class TestEventTriggerConfig:
    def test_builds_event_config_filtered_by_event_type(self) -> None:
        from custom_components.aegis_ajax import device_trigger

        config = {
            "platform": "device",
            "domain": DOMAIN,
            "device_id": "ha-device-xyz",
            "type": "doorbell_pressed",
        }
        event_config = device_trigger._event_trigger_config(config)

        assert event_config["platform"] == "event"
        assert event_config["event_type"] == f"{DOMAIN}_event"
        assert event_config["event_data"] == {"event_type": "doorbell_pressed"}


class TestTriggerSchema:
    def test_accepts_known_type(self) -> None:
        from custom_components.aegis_ajax import device_trigger

        cfg = device_trigger.TRIGGER_SCHEMA(
            {
                "platform": "device",
                "domain": DOMAIN,
                "device_id": "ha-device-xyz",
                "type": "alarm",
            }
        )
        assert cfg["type"] == "alarm"

    def test_rejects_unknown_type(self) -> None:
        import voluptuous as vol

        from custom_components.aegis_ajax import device_trigger

        with pytest.raises(vol.Invalid):
            device_trigger.TRIGGER_SCHEMA(
                {
                    "platform": "device",
                    "domain": DOMAIN,
                    "device_id": "ha-device-xyz",
                    "type": "not_a_real_event",
                }
            )
