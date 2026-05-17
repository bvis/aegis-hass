"""Tests for the read-only update.py platform (hub firmware)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.aegis_ajax.api.hub_object import (
    HUB_FW_STATE_DOWNLOADING,
    HUB_FW_STATE_NOT_STARTED,
    HubFirmwareUpdateInfo,
)
from custom_components.aegis_ajax.update import AjaxHubFirmwareUpdate


class TestAjaxHubFirmwareUpdate:
    @staticmethod
    def _make_coordinator(
        info: HubFirmwareUpdateInfo | None,
        hub_id: str = "002B1A51",
    ) -> MagicMock:
        from custom_components.aegis_ajax.api.models import Device
        from custom_components.aegis_ajax.const import DeviceState

        coordinator = MagicMock()
        coordinator.rooms = {}
        coordinator.devices = {
            hub_id: Device(
                id=hub_id,
                hub_id=hub_id,
                name="Hub",
                device_type="hub",
                room_id=None,
                group_id=None,
                state=DeviceState.ONLINE,
                malfunctions=0,
                bypassed=False,
                statuses={},
                battery=None,
            )
        }
        coordinator.hub_firmware_updates = {hub_id: info} if info else {}
        return coordinator

    def test_unique_id_namespaced_by_hub(self) -> None:
        info = HubFirmwareUpdateInfo(target_version="2.17.0", state=HUB_FW_STATE_NOT_STARTED)
        coordinator = self._make_coordinator(info)
        entity = AjaxHubFirmwareUpdate(coordinator, "002B1A51")
        assert entity._attr_unique_id == "aegis_ajax_002B1A51_firmware"

    def test_installed_version_always_none(self) -> None:
        """Ajax stream doesn't expose installed version — HA renders just `latest`."""
        info = HubFirmwareUpdateInfo(target_version="2.17.0", state=HUB_FW_STATE_NOT_STARTED)
        coordinator = self._make_coordinator(info)
        entity = AjaxHubFirmwareUpdate(coordinator, "002B1A51")
        assert entity.installed_version is None

    def test_latest_version_reflects_pending_update(self) -> None:
        info = HubFirmwareUpdateInfo(target_version="2.17.0", state=HUB_FW_STATE_NOT_STARTED)
        coordinator = self._make_coordinator(info)
        entity = AjaxHubFirmwareUpdate(coordinator, "002B1A51")
        assert entity.latest_version == "2.17.0"

    def test_latest_version_none_when_no_pending_update(self) -> None:
        """Absence of `hub_firmware_updates` entry == hub is up to date."""
        coordinator = self._make_coordinator(None)
        entity = AjaxHubFirmwareUpdate(coordinator, "002B1A51")
        assert entity.latest_version is None
        assert entity.in_progress is False

    def test_in_progress_true_when_downloading(self) -> None:
        info = HubFirmwareUpdateInfo(target_version="2.17.0", state=HUB_FW_STATE_DOWNLOADING)
        coordinator = self._make_coordinator(info)
        entity = AjaxHubFirmwareUpdate(coordinator, "002B1A51")
        assert entity.in_progress is True

    def test_in_progress_false_when_not_started(self) -> None:
        info = HubFirmwareUpdateInfo(target_version="2.17.0", state=HUB_FW_STATE_NOT_STARTED)
        coordinator = self._make_coordinator(info)
        entity = AjaxHubFirmwareUpdate(coordinator, "002B1A51")
        assert entity.in_progress is False

    def test_supported_features_excludes_install(self) -> None:
        """The entity is read-only by design — no INSTALL feature exposed."""
        from homeassistant.components.update import UpdateEntityFeature

        info = HubFirmwareUpdateInfo(target_version="2.17.0", state=HUB_FW_STATE_NOT_STARTED)
        coordinator = self._make_coordinator(info)
        entity = AjaxHubFirmwareUpdate(coordinator, "002B1A51")
        assert not (entity.supported_features & UpdateEntityFeature.INSTALL)
        assert entity.supported_features == UpdateEntityFeature(0)

    def test_device_class_firmware(self) -> None:
        from homeassistant.components.update import UpdateDeviceClass

        info = HubFirmwareUpdateInfo(target_version="2.17.0", state=HUB_FW_STATE_NOT_STARTED)
        coordinator = self._make_coordinator(info)
        entity = AjaxHubFirmwareUpdate(coordinator, "002B1A51")
        assert entity.device_class is UpdateDeviceClass.FIRMWARE

    def test_empty_target_version_renders_as_none(self) -> None:
        """Defensive: an empty version string is reported as no latest available."""
        info = HubFirmwareUpdateInfo(target_version="", state=HUB_FW_STATE_NOT_STARTED)
        coordinator = self._make_coordinator(info)
        entity = AjaxHubFirmwareUpdate(coordinator, "002B1A51")
        assert entity.latest_version is None


class TestAsyncSetupEntry:
    @pytest.mark.asyncio
    async def test_setup_creates_one_entity_per_hub(self) -> None:
        from custom_components.aegis_ajax.api.models import Device, Space
        from custom_components.aegis_ajax.const import (
            ConnectionStatus,
            DeviceState,
            SecurityState,
        )
        from custom_components.aegis_ajax.update import async_setup_entry

        hub_id = "002B1A51"
        coordinator = MagicMock()
        coordinator.rooms = {}
        coordinator.spaces = {
            "s1": Space(
                id="s1",
                hub_id=hub_id,
                name="Home",
                security_state=SecurityState.DISARMED,
                connection_status=ConnectionStatus.ONLINE,
                malfunctions_count=0,
            )
        }
        coordinator.devices = {
            hub_id: Device(
                id=hub_id,
                hub_id=hub_id,
                name="Hub",
                device_type="hub",
                room_id=None,
                group_id=None,
                state=DeviceState.ONLINE,
                malfunctions=0,
                bypassed=False,
                statuses={},
                battery=None,
            )
        }
        coordinator.hub_firmware_updates = {}
        entry = MagicMock(runtime_data=coordinator)
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], AjaxHubFirmwareUpdate)

    @pytest.mark.asyncio
    async def test_setup_skips_spaces_without_hub_device(self) -> None:
        from custom_components.aegis_ajax.api.models import Space
        from custom_components.aegis_ajax.const import ConnectionStatus, SecurityState
        from custom_components.aegis_ajax.update import async_setup_entry

        coordinator = MagicMock()
        coordinator.rooms = {}
        coordinator.spaces = {
            "s1": Space(
                id="s1",
                hub_id="HUB1",
                name="Home",
                security_state=SecurityState.DISARMED,
                connection_status=ConnectionStatus.ONLINE,
                malfunctions_count=0,
            )
        }
        # No hub device yet — the hub-id-keyed lookup misses.
        coordinator.devices = {}
        coordinator.hub_firmware_updates = {}
        entry = MagicMock(runtime_data=coordinator)
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)
        entities = async_add_entities.call_args[0][0]
        assert entities == []
