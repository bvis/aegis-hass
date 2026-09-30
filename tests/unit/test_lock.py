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
from custom_components.aegis_ajax.lock import AjaxLock, async_setup_entry


def _make_device(
    device_type: str, smart_lock_state: str | None = None, *, device_id: str = "lock-1"
) -> Device:
    statuses: dict = {}
    if smart_lock_state is not None:
        statuses["smart_lock_state"] = smart_lock_state
    return Device(
        id=device_id,
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


class TestLockSetup:
    @pytest.mark.asyncio
    async def test_setup_adds_only_registered_lock_capabilities(self) -> None:
        lock_device = _make_device("smart_lock")
        coordinator = _make_coordinator(lock_device)
        door_device = _make_device("door_protect", device_id="door-1")
        coordinator.devices[door_device.id] = door_device
        async_add_entities = MagicMock()
        entry = MagicMock(runtime_data=coordinator)

        await async_setup_entry(MagicMock(), entry, async_add_entities)

        entities = async_add_entities.call_args.args[0]
        assert [entity._device_id for entity in entities] == [lock_device.id]


class TestAjaxLockCommands:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("device_type", ["smart_lock", "smart_lock_yale"])
    async def test_async_lock_sends_device_off(self, device_type: str) -> None:
        # #219: lock = DeviceCommandDeviceOff keyed by device id. The Ajax app
        # routes every hub-attached lock (generic AND Yale) through one command
        # path that normalises to the generic `smart_lock` ObjectType on
        # CHANNEL_1, so we always send device_type="smart_lock" + channels=[1]
        # regardless of the stream's `smart_lock_yale` type. The polarity is
        # inverted vs a relay: the app sends Off to LOCK (On retracts the bolt).
        device = _make_device(device_type, "unlocked")
        coordinator = _make_coordinator(device)
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)

        await lock.async_lock()

        coordinator.devices_api.send_command.assert_awaited_once()
        cmd: DeviceCommand = coordinator.devices_api.send_command.await_args.args[0]
        assert cmd.action == "off"
        assert cmd.hub_id == "hub-1"
        assert cmd.device_id == "lock-1"
        assert cmd.device_type == "smart_lock"
        assert cmd.channels == [1]
        coordinator.devices_api.switch_smart_lock.assert_not_called()
        coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_unlock_sends_device_on(self) -> None:
        device = _make_device("smart_lock", "locked")
        coordinator = _make_coordinator(device)
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)

        await lock.async_unlock()

        cmd: DeviceCommand = coordinator.devices_api.send_command.await_args.args[0]
        assert cmd.action == "on"
        assert cmd.device_id == "lock-1"
        assert cmd.device_type == "smart_lock"
        assert cmd.channels == [1]
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

    def test_open_feature_not_advertised(self) -> None:
        # Unlatch only works via the cloud SwitchSmartLockService; the
        # hub-attached locks we expose can't reach it, so OPEN is not surfaced
        # (no phantom "Open" button that always fails). The unlatch machinery
        # below stays for future cloud-SmartLock support.
        from homeassistant.components.lock import LockEntityFeature

        device = _make_device("smart_lock", "locked")
        coordinator = _make_coordinator(device)
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)

        assert LockEntityFeature.OPEN not in lock.supported_features

    @pytest.mark.asyncio
    async def test_async_open_invokes_unlatch_action(self) -> None:
        # OPEN is not advertised, but the unlatch path is retained and still
        # functional for the day cloud-SmartLock support re-enables it.
        device = _make_device("smart_lock", "locked")
        coordinator = _make_coordinator(device)
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)

        await lock.async_open()

        coordinator.devices_api.switch_smart_lock.assert_awaited_once_with(
            space_id="space-A", smart_lock_id="lock-1", action=SMART_LOCK_ACTION_UNLATCH
        )

    @pytest.mark.asyncio
    async def test_lock_command_raises_on_device_command_error(self) -> None:
        # A refused unlock must fail the action, not look like it worked.
        from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415

        device = _make_device("smart_lock", "locked")
        coordinator = _make_coordinator(device)
        coordinator.devices_api.send_command.side_effect = DeviceCommandError("smart_lock_offline")
        lock = AjaxLock(coordinator=coordinator, device_id=device.id)

        with pytest.raises(HomeAssistantError):
            await lock.async_unlock()

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
