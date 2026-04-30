"""Data models for the Ajax gRPC API."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from custom_components.aegis_ajax.const import (
    ConnectionStatus,
    DeviceState,
    SecurityState,
)


class MonitoringCompanyStatus(IntEnum):
    """Ajax monitoring-company lifecycle states."""

    UNSPECIFIED = 0
    PENDING_APPROVAL = 1
    APPROVED = 2
    PENDING_DELETION = 3


@dataclass(frozen=True)
class MonitoringCompany:
    """Represents a monitoring company attached to a space."""

    name: str
    status: MonitoringCompanyStatus


@dataclass(frozen=True)
class Group:
    """Represents an Ajax security group inside a space.

    A group is a subset of devices that can be armed/disarmed independently.
    The Ajax mobile app exposes this feature as "Group Mode" on older
    firmwares and "Zone Mode" on newer ones; the underlying gRPC payload is
    the same `GroupSecurity` message in both cases.
    """

    id: str
    space_id: str
    name: str
    security_state: SecurityState
    sorting_key: str = ""

    @property
    def is_armed(self) -> bool:
        return self.security_state in (
            SecurityState.ARMED,
            SecurityState.NIGHT_MODE,
            SecurityState.PARTIALLY_ARMED,
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
    monitoring_companies: tuple[MonitoringCompany, ...] = field(default_factory=tuple)
    monitoring_companies_loaded: bool = False
    groups: tuple[Group, ...] = field(default_factory=tuple)
    group_mode_enabled: bool = False

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

    @property
    def approved_monitoring_companies(self) -> tuple[MonitoringCompany, ...]:
        return tuple(
            company
            for company in self.monitoring_companies
            if company.status == MonitoringCompanyStatus.APPROVED
        )

    @property
    def has_monitoring(self) -> bool:
        return bool(self.approved_monitoring_companies)

    def get_group(self, group_id: str) -> Group | None:
        return next((g for g in self.groups if g.id == group_id), None)


@dataclass(frozen=True)
class Room:
    """Represents an Ajax room within a space."""

    id: str
    name: str
    space_id: str


@dataclass(frozen=True)
class SpaceSnapshot:
    """Subset of full space snapshot data used by the integration."""

    rooms: tuple[Room, ...] = field(default_factory=tuple)
    monitoring_companies: tuple[MonitoringCompany, ...] = field(default_factory=tuple)
    monitoring_companies_loaded: bool = False
    groups: tuple[Group, ...] = field(default_factory=tuple)
    group_mode_enabled: bool = False


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
