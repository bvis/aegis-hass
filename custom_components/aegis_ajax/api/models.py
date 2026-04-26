"""Data models for the Ajax gRPC API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from custom_components.aegis_ajax.const import (
    ConnectionStatus,
    DeviceState,
    SecurityState,
)


@dataclass(frozen=True)
class Space:
    """Represents an Ajax space (hub)."""

    id: str
    hub_id: str
    name: str
    security_state: SecurityState
    connection_status: ConnectionStatus
    malfunctions_count: int

    @property
    def is_online(self) -> bool:
        return self.connection_status == ConnectionStatus.ONLINE

    @property
    def is_armed(self) -> bool:
        return self.security_state in (
            SecurityState.ARMED,
            SecurityState.NIGHT_MODE,
            SecurityState.PARTIALLY_ARMED,
        )


@dataclass(frozen=True)
class Room:
    """Represents an Ajax room within a space."""

    id: str
    name: str
    space_id: str


@dataclass(frozen=True)
class BatteryInfo:
    """Battery status for a device."""

    level: int
    is_low: bool


@dataclass(frozen=True)
class Device:
    """Represents an Ajax device."""

    id: str
    hub_id: str
    name: str
    device_type: str
    room_id: str | None
    group_id: str | None
    state: DeviceState
    malfunctions: int
    bypassed: bool
    statuses: dict[str, Any]
    battery: BatteryInfo | None

    @property
    def is_online(self) -> bool:
        return self.state == DeviceState.ONLINE


@dataclass(frozen=True)
class DeviceCommand:
    """Represents a command to send to a device."""

    action: str
    hub_id: str
    device_id: str
    device_type: str
    channels: list[int] = field(default_factory=list)
    brightness: int | None = None

    @classmethod
    def on(
        cls, hub_id: str, device_id: str, device_type: str, channels: list[int] | None = None
    ) -> DeviceCommand:
        return cls(
            action="on",
            hub_id=hub_id,
            device_id=device_id,
            device_type=device_type,
            channels=channels or [],
        )

    @classmethod
    def off(
        cls, hub_id: str, device_id: str, device_type: str, channels: list[int] | None = None
    ) -> DeviceCommand:
        return cls(
            action="off",
            hub_id=hub_id,
            device_id=device_id,
            device_type=device_type,
            channels=channels or [],
        )

    @classmethod
    def set_brightness(
        cls,
        hub_id: str,
        device_id: str,
        device_type: str,
        brightness: int,
        channels: list[int] | None = None,
    ) -> DeviceCommand:
        return cls(
            action="brightness",
            hub_id=hub_id,
            device_id=device_id,
            device_type=device_type,
            channels=channels or [],
            brightness=brightness,
        )
