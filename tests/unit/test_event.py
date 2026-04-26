"""Tests for event platform."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.aegis_ajax.const import ALL_EVENT_TYPES, HUB_EVENT_TAG_MAP
from custom_components.aegis_ajax.event import AjaxSecurityEvent


class TestAjaxSecurityEvent:
    def _make_event_entity(self) -> AjaxSecurityEvent:
        coordinator = MagicMock()
        coordinator.spaces = {
            "space-1": MagicMock(hub_id="hub-1", name="Home"),
        }
        coordinator.devices = {}
        return AjaxSecurityEvent(coordinator=coordinator, space_id="space-1")

    def test_unique_id(self) -> None:
        entity = self._make_event_entity()
        assert entity.unique_id == "aegis_ajax_hub-1_event"

    def test_event_types(self) -> None:
        entity = self._make_event_entity()
        assert entity.event_types == ALL_EVENT_TYPES

    def test_has_entity_name(self) -> None:
        entity = self._make_event_entity()
        assert entity._attr_has_entity_name is True

    def test_translation_key(self) -> None:
        entity = self._make_event_entity()
        assert entity._attr_translation_key == "security_event"

    def test_handle_event_triggers_event(self) -> None:
        entity = self._make_event_entity()
        entity._trigger_event = MagicMock()
        entity.async_write_ha_state = MagicMock()
        entity.hass = MagicMock()

        entity.handle_event("alarm", {"raw_tag": "intrusion_alarm", "transition": "triggered"})

        entity._trigger_event.assert_called_once_with(
            "alarm", {"raw_tag": "intrusion_alarm", "transition": "triggered"}
        )
        entity.async_write_ha_state.assert_called_once()
        entity.hass.bus.async_fire.assert_called_once()

    def test_handle_event_with_source_info(self) -> None:
        entity = self._make_event_entity()
        entity._trigger_event = MagicMock()
        entity.async_write_ha_state = MagicMock()
        entity.hass = MagicMock()

        data = {
            "raw_tag": "door_opened",
            "transition": "triggered",
            "device_name": "HALLWAY",
            "device_id": "A1B2C3D4",
            "device_type": "MOTION_CAM_PHOD",
            "room_name": "Hall",
        }
        entity.handle_event("door_open", data)

        entity._trigger_event.assert_called_once_with("door_open", data)
        entity.async_write_ha_state.assert_called_once()
        # Verify bus event fired with correct data
        fire_call = entity.hass.bus.async_fire.call_args
        assert fire_call[0][0] == "aegis_ajax_event"
        assert fire_call[0][1]["event_type"] == "door_open"
        assert fire_call[0][1]["device_name"] == "HALLWAY"

    def test_handle_event_ignores_unknown_type(self) -> None:
        entity = self._make_event_entity()
        entity._trigger_event = MagicMock()
        entity.async_write_ha_state = MagicMock()

        entity.handle_event("unknown_type_xyz", {})

        entity._trigger_event.assert_not_called()

    def test_device_info(self) -> None:
        entity = self._make_event_entity()
        assert entity._attr_device_info is not None
        assert ("aegis_ajax", "hub-1") in entity._attr_device_info["identifiers"]


class TestEventConstants:
    def test_all_event_types_not_empty(self) -> None:
        assert len(ALL_EVENT_TYPES) > 0

    def test_hub_event_tag_map_has_arm(self) -> None:
        assert "arm" in HUB_EVENT_TAG_MAP

    def test_hub_event_tag_map_has_disarm(self) -> None:
        assert "disarm" in HUB_EVENT_TAG_MAP

    def test_hub_event_tag_map_has_intrusion_alarm(self) -> None:
        assert "intrusion_alarm" in HUB_EVENT_TAG_MAP

    def test_all_event_types_are_sorted(self) -> None:
        assert sorted(ALL_EVENT_TYPES) == ALL_EVENT_TYPES

    def test_all_mapped_values_in_event_types(self) -> None:
        for value in HUB_EVENT_TAG_MAP.values():
            assert value in ALL_EVENT_TYPES


class TestCoordinatorEventDispatch:
    def test_register_and_fire_event(self) -> None:
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        coordinator = MagicMock(spec=AjaxCobrandedCoordinator)
        coordinator._event_entities = {}

        # Call the real methods
        AjaxCobrandedCoordinator.register_event_entity(coordinator, "space-1", MagicMock())
        entity = coordinator._event_entities["space-1"]

        AjaxCobrandedCoordinator.fire_push_event(coordinator, "space-1", "alarm", {"test": True})
        entity.handle_event.assert_called_once_with("alarm", {"test": True})

    def test_fire_event_no_entity(self) -> None:
        from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

        coordinator = MagicMock(spec=AjaxCobrandedCoordinator)
        coordinator._event_entities = {}

        # Should not raise
        AjaxCobrandedCoordinator.fire_push_event(coordinator, "space-999", "alarm", {})


class TestNotificationEventParsing:
    def test_parse_and_fire_event_called(self) -> None:
        """Verify _parse_and_fire_event is called when ENCODED_DATA present."""
        import base64

        from custom_components.aegis_ajax.notification import AjaxNotificationListener

        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        coordinator = MagicMock()
        coordinator.async_request_refresh = MagicMock()

        listener = AjaxNotificationListener(
            hass=hass,
            coordinator=coordinator,
            fcm_project_id="p",
            fcm_app_id="a",
            fcm_api_key="k",
            fcm_sender_id="s",
        )
        listener._parse_and_fire_event = MagicMock()

        encoded = base64.b64encode(b"\x0a\x02\x08\x01").decode()
        listener._on_notification({"ENCODED_DATA": encoded}, "pid-1")

        listener._parse_and_fire_event.assert_called_once_with(encoded)

    def test_extract_event_raw_returns_none(self) -> None:
        from custom_components.aegis_ajax.notification import AjaxNotificationListener

        result = AjaxNotificationListener._extract_event_raw(b"\x00\x01\x02")
        assert result is None
