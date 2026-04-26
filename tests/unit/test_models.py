"""Tests for API data models."""

from custom_components.aegis_ajax.api.models import (
    BatteryInfo,
    Device,
    DeviceCommand,
    Room,
    Space,
)
from custom_components.aegis_ajax.const import (
    ConnectionStatus,
    DeviceState,
    SecurityState,
)


class TestSpace:
    def test_creation(self) -> None:
        space = Space(
            id="space-1",
            hub_id="hub-1",
            name="Home",
            security_state=SecurityState.DISARMED,
            connection_status=ConnectionStatus.ONLINE,
            malfunctions_count=0,
        )
        assert space.id == "space-1"
        assert space.hub_id == "hub-1"
        assert space.name == "Home"
        assert space.security_state == SecurityState.DISARMED
        assert space.connection_status == ConnectionStatus.ONLINE
        assert space.is_online is True

    def test_is_online_false_when_offline(self) -> None:
        space = Space(
            id="s1",
            hub_id="h1",
            name="Office",
            security_state=SecurityState.ARMED,
            connection_status=ConnectionStatus.OFFLINE,
            malfunctions_count=0,
        )
        assert space.is_online is False

    def test_is_armed(self) -> None:
        for state in (SecurityState.ARMED, SecurityState.NIGHT_MODE, SecurityState.PARTIALLY_ARMED):
            space = Space(
                id="s",
                hub_id="h",
                name="X",
                security_state=state,
                connection_status=ConnectionStatus.ONLINE,
                malfunctions_count=0,
            )
            assert space.is_armed is True

    def test_is_not_armed(self) -> None:
        space = Space(
            id="s",
            hub_id="h",
            name="X",
            security_state=SecurityState.DISARMED,
            connection_status=ConnectionStatus.ONLINE,
            malfunctions_count=0,
        )
        assert space.is_armed is False


class TestDevice:
    def test_creation(self) -> None:
        device = Device(
            id="dev-1",
            hub_id="hub-1",
            name="Front Door",
            device_type="DoorProtect",
            room_id="room-1",
            group_id=None,
            state=DeviceState.ONLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=BatteryInfo(level=95, is_low=False),
        )
        assert device.id == "dev-1"
        assert device.name == "Front Door"
        assert device.device_type == "DoorProtect"
        assert device.is_online is True
        assert device.battery is not None
        assert device.battery.level == 95

    def test_is_online_false(self) -> None:
        device = Device(
            id="d",
            hub_id="h",
            name="X",
            device_type="MotionProtect",
            room_id=None,
            group_id=None,
            state=DeviceState.OFFLINE,
            malfunctions=0,
            bypassed=False,
            statuses={},
            battery=None,
        )
        assert device.is_online is False


class TestRoom:
    def test_creation(self) -> None:
        room = Room(id="r1", name="Kitchen", space_id="s1")
        assert room.id == "r1"
        assert room.name == "Kitchen"
        assert room.space_id == "s1"


class TestDeviceCommand:
    def test_on_command(self) -> None:
        cmd = DeviceCommand.on(hub_id="h1", device_id="d1", device_type="Relay", channels=[1])
        assert cmd.action == "on"
        assert cmd.hub_id == "h1"
        assert cmd.channels == [1]

    def test_off_command(self) -> None:
        cmd = DeviceCommand.off(hub_id="h1", device_id="d1", device_type="Socket")
        assert cmd.action == "off"
        assert cmd.channels == []

    def test_brightness_command(self) -> None:
        cmd = DeviceCommand.set_brightness(
            hub_id="h1",
            device_id="d1",
            device_type="LightSwitchDimmer",
            brightness=75,
            channels=[1],
        )
        assert cmd.action == "brightness"
        assert cmd.brightness == 75
