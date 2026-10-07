"""Data models for the Ajax gRPC API."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from custom_components.aegis_ajax.const import (
    DEACTIVATED_KEY,
    DEACTIVATION_STATUS_KEYS,
    ChimeStatus,
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
    """Represents a monitoring company attached to a space.

    `hex_id` is the stable per-company opaque identifier the Ajax cloud uses
    to address this company on every endpoint that returns or accepts a
    `SpaceMonitoringCompany`.
    """

    name: str
    status: MonitoringCompanyStatus
    hex_id: str = ""


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
    # Night mode currently active (#284). In group mode the server's
    # DisplayedSpaceSecurityState reports PARTIALLY_ARMED while night mode is
    # on — this flag is the only wire signal that tells the two apart.
    night_mode_enabled: bool = False
    # Hub-wide Chime on/off setting (#239). UNSPECIFIED = the hub doesn't
    # expose the feature, so no Chime switch is created for it.
    chime_status: ChimeStatus = ChimeStatus.UNSPECIFIED

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
    groups: tuple[Group, ...] = field(default_factory=tuple)
    group_mode_enabled: bool = False
    # Night mode currently active, read off `mode.group_mode` (#284).
    night_mode_enabled: bool = False
    # Hub-wide Chime status read off the full snapshot's hub device (#239).
    chime_status: ChimeStatus = ChimeStatus.UNSPECIFIED


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


def is_device_deactivated(device: Any) -> bool:  # noqa: ANN401
    """True when the panel has this device excluded from protection (#338).

    Two independent sources say so and either is sufficient:
    `profile.bypassed` (set when the deactivation came through the path the
    snapshot models as a bypass) and the `*_deactivation_*` statuses (what a
    device deactivated from the Ajax app actually reports — `bypassed` stays
    False in that case, which is the whole reason #338 existed).

    The granular statuses are checked as well as the folded key they normally
    arrive with, so a device whose statuses came from an older persisted cache
    (written before the fold existed) still reads correctly.

    Takes the device structurally rather than as a `Device` method so the
    entity layer and the coordinator share one definition of "deactivated".
    """
    return bool(
        device.bypassed or device.statuses.get(DEACTIVATED_KEY) or device_deactivation_kinds(device)
    )


def device_deactivation_kinds(device: Any) -> list[str]:  # noqa: ANN401
    """Which deactivation modes are in force, in `DEACTIVATION_STATUS_KEYS`
    order (#338). Empty when the device is active, or when it is deactivated
    only via the snapshot's `bypassed` flag, which carries no mode.
    """
    return [key for key in DEACTIVATION_STATUS_KEYS if device.statuses.get(key)]


@dataclass(frozen=True)
class DeviceCommand:
    """Represents a command to send to a device."""

    action: str
    hub_id: str
    device_id: str
    device_type: str
    channels: list[int] = field(default_factory=list)
    brightness: int | None = None
    # True = deactivate (bypass) the device, False = reactivate it. Only set
    # for action="bypass".
    bypass_enable: bool | None = None
    # Writable siren settings (#310). Only set for action="siren_settings";
    # each is optional so a command can change one without touching the other.
    # `alarm_duration` is seconds; `siren_volume_level` is the
    # `CommonSirenPart.SirenVolumeLevel` enum value.
    alarm_duration: int | None = None
    siren_volume_level: int | None = None

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

    @classmethod
    def bypass(cls, hub_id: str, device_id: str, device_type: str, enable: bool) -> DeviceCommand:
        """Deactivate (`enable=True`) or reactivate (`enable=False`) a device."""
        return cls(
            action="bypass",
            hub_id=hub_id,
            device_id=device_id,
            device_type=device_type,
            bypass_enable=enable,
        )

    @classmethod
    def set_siren_settings(
        cls,
        hub_id: str,
        device_id: str,
        device_type: str,
        *,
        alarm_duration: int | None = None,
        siren_volume_level: int | None = None,
    ) -> DeviceCommand:
        """Change a siren's alarm duration and/or volume level (#310).

        Both values are optional so a single setting can be updated without
        resending the other. `alarm_duration` is seconds; `siren_volume_level`
        is the `CommonSirenPart.SirenVolumeLevel` enum value.
        """
        return cls(
            action="siren_settings",
            hub_id=hub_id,
            device_id=device_id,
            device_type=device_type,
            alarm_duration=alarm_duration,
            siren_volume_level=siren_volume_level,
        )
