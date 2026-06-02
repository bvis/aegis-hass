"""Tests for the lock platform (Ajax SmartLock / LockBridge)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aegis_ajax.api.devices import (
    SMART_LOCK_ACTION_UNLATCH,
    DeviceCommandError,
)
from custom_components.aegis_ajax.api.models import Device, DeviceCommand, Space
from custom_components.aegis_ajax.const import (
    ConnectionStatus,
    DeviceState,
    SecurityState,
)
from custom_components.aegis_ajax.lock import LOCK_DEVICE_TYPES, AjaxLock


def _make_device(device_type: str, smart_lock_state: str | None = None) -> Device:
    statuses: dict = {}
    if smart_lock_state is not None:
        statuses["smart_lock_state"] = smart_lock_state
    return Device(
        id="lock-1",
        hub_id="hub-1",
        name="Front Door Lock",
        device_type=device_type,
        room_id=None,
        group_id=None,
        state=DeviceState.ONLINE,
        malfunctions=0,
        bypassed=False,
        statuses=statuses,
        battery=None,
    )


def _make_coordinator(device: Device) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = {device.id: device}
    coordinator.rooms = {}
    coordinator.spaces = {
        "space-A": Space(
            id="space-A",
            hub_id=device.hub_id,
            name="Home",
            security_state=SecurityState.DISARMED,
            connection_status=ConnectionStatus.ONLINE,
            malfunctions_count=0,
            monitoring_companies=(),
            monitoring_companies_loaded=True,
            groups=(),
            group_mode_enabled=False,
        )
    }
    coordinator.devices_api = MagicMock()
    coordinator.devices_api.switch_smart_lock = AsyncMock()
    coordinator.devices_api.send_command = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


class TestLockDeviceTypes:
    def test_smart_lock_in_lock_types(self) -> None:
        assert "smart_lock" in LOCK_DEVICE_TYPES

    def test_smart_lock_yale_in_lock_types(self) -> None:
        assert "smart_lock_yale" in LOCK_DEVICE_TYPES


class TestAjaxLockState:
    @pytest.mark.parametrize("device_type", ["smart_lock", "smart_lock_yale"])
    def test_is_locked_true(self, device_type: str) -> None:
        device = _make_device(device_type, "locked")
        coordinator = _make_coordinator(device)
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)
        assert lock.is_locked is True

    @pytest.mark.parametrize("state", ["unlocked", "unlatched"])
    def test_is_locked_false(self, state: str) -> None:
        device = _make_device("smart_lock", state)
        coordinator = _make_coordinator(device)
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)
        assert lock.is_locked is False

    def test_is_locked_unknown_when_state_missing(self) -> None:
        device = _make_device("smart_lock", smart_lock_state=None)
        coordinator = _make_coordinator(device)
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)
        assert lock.is_locked is None

    def test_is_open_unlatched(self) -> None:
        device = _make_device("smart_lock", "unlatched")
        coordinator = _make_coordinator(device)
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)
        assert lock.is_open is True

    def test_is_open_false_when_locked(self) -> None:
        device = _make_device("smart_lock", "locked")
        coordinator = _make_coordinator(device)
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)
        assert lock.is_open is False

    def test_unavailable_when_offline(self) -> None:
        device = Device(
            id="lock-1",
            hub_id="hub-1",
            name="Front Door Lock",
            device_type="smart_lock",
            room_id=None,
            group_id=None,
            state=DeviceState.OFFLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )
        coordinator = _make_coordinator(device)
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)
        assert lock.available is False


class TestAjaxLockCommands:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("device_type", ["smart_lock", "smart_lock_yale"])
    async def test_async_lock_sends_device_on(self, device_type: str) -> None:
        # #219: lock = DeviceCommandDeviceOn keyed by device id, no channels.
        device = _make_device(device_type, "unlocked")
        coordinator = _make_coordinator(device)
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)

        await lock.async_lock()

        coordinator.devices_api.send_command.assert_awaited_once()
        cmd: DeviceCommand = coordinator.devices_api.send_command.await_args.args[0]
        assert cmd.action == "on"
        assert cmd.hub_id == "hub-1"
        assert cmd.device_id == "lock-1"
        assert cmd.device_type == device_type
        assert cmd.channels == []
        coordinator.devices_api.switch_smart_lock.assert_not_called()
        coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_unlock_sends_device_off(self) -> None:
        device = _make_device("smart_lock", "locked")
        coordinator = _make_coordinator(device)
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)

        await lock.async_unlock()

        cmd: DeviceCommand = coordinator.devices_api.send_command.await_args.args[0]
        assert cmd.action == "off"
        assert cmd.device_id == "lock-1"
        assert cmd.channels == []
        coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lock_works_without_a_resolved_space(self) -> None:
        # The device on/off command is keyed by hub_id, not space_id, so the
        # lock no longer depends on resolving a space (unlike unlatch).
        device = _make_device("smart_lock", "unlocked")
        coordinator = _make_coordinator(device)
        coordinator.spaces = {}
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)

        await lock.async_lock()

        coordinator.devices_api.send_command.assert_awaited_once()
        coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_open_invokes_unlatch_action(self) -> None:
        device = _make_device("smart_lock", "locked")
        coordinator = _make_coordinator(device)
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)

        await lock.async_open()

        coordinator.devices_api.switch_smart_lock.assert_awaited_once_with(
            space_id="space-A", smart_lock_id="lock-1", action=SMART_LOCK_ACTION_UNLATCH
        )

    @pytest.mark.asyncio
    async def test_lock_command_swallows_device_command_error(self) -> None:
        # Surface the failure through logs rather than raising — the entity
        # must not crash HA's service pipeline. Next poll corrects state.
        device = _make_device("smart_lock", "locked")
        coordinator = _make_coordinator(device)
        coordinator.devices_api.send_command.side_effect = DeviceCommandError("smart_lock_offline")
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)

        await lock.async_unlock()  # must not raise

        coordinator.async_request_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_open_no_op_when_space_unresolvable(self) -> None:
        device = _make_device("smart_lock", "locked")
        coordinator = _make_coordinator(device)
        coordinator.spaces = {}  # no space matches the hub_id
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)

        await lock.async_open()

        coordinator.devices_api.switch_smart_lock.assert_not_called()
        coordinator.async_request_refresh.assert_not_called()
