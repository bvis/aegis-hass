"""Update entities for Ajax hubs (read-only — #123 follow-up).

Surfaces the pending firmware update the Ajax cloud has queued for the
hub. The entity is informational only:

- No `install` feature is declared, so HA renders no install button.
- `async_install` is not implemented.
- Ajax controls update scheduling server-side; the cloud pushes updates
  to the hub on its own cadence. This integration deliberately never
  calls the install RPC even though the proto exposes one — firmware
  updates are higher-stakes than the rest of the surface and the user
  should manage them via the official app if they want to force one.

The Ajax stream doesn't carry the currently-installed firmware version,
so `installed_version` stays `None`; HA still renders the entity as
"<latest> available" with a progress bar when downloading.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.api.hub_object import (
    DEVICE_FW_STATE_COMPLETED,
    DEVICE_FW_STATE_DOWNLOADING,
    DEVICE_FW_STATE_FAILED,
    DEVICE_FW_STATE_INSTALLING,
    HUB_FW_STATE_DOWNLOADING,
    DeviceFirmwareUpdateInfo,
    HubFirmwareUpdateInfo,
)
from custom_components.aegis_ajax.const import DOMAIN
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.entity import (
    async_get_registered_device,
    build_device_info,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)

# Entities only read coordinator data; nothing to throttle.
PARALLEL_UPDATES = 0

# Sentinel value used for both `installed_version` and `latest_version`
# when the Ajax cloud reports no pending update. HA's `UpdateEntity`
# treats matching non-None versions as "up to date" and renders the
# entity state as `STATE_OFF`; with both versions left at `None` the
# entity would render as `unknown`, which is misleading because the
# absence of a pending update IS the "up to date" signal from Ajax.
# The placeholder is also surfaced on `installed_version` while an
# update IS pending so the state computation lands on `STATE_ON` —
# Ajax's `streamHubObject` does not carry the currently-installed
# firmware version, so this is the most truthful answer we can give.
_INSTALLED_VERSION_PLACEHOLDER = "current"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AjaxCobrandedCoordinator = entry.runtime_data
    entities: list[UpdateEntity] = []
    seen: set[str] = set()
    for space in coordinator.spaces.values():
        hub_id = space.hub_id
        if not hub_id or hub_id in seen:
            continue
        # Only attach when a hub device exists in the snapshot — otherwise
        # there's nothing to bind `device_info` to.
        if coordinator.devices.get(hub_id):
            entities.append(AjaxHubFirmwareUpdate(coordinator, hub_id))
            seen.add(hub_id)

    # Per-device firmware update entities (2.1). One per non-hub device,
    # disabled-by-default: a typical install has 10-30 devices and most
    # users only care when a specific device is stuck on old firmware.
    # `device_id in seen` also guards a hub model newer than the vendored
    # proto: it parses as device_type "unknown", escapes the name filter,
    # and would otherwise get a second entity with the hub entity's
    # unique_id (HA then drops one of the two).
    for device_id, device in coordinator.devices.items():
        if device_id in seen or device.device_type.startswith("hub"):
            continue
        entities.append(AjaxDeviceFirmwareUpdate(coordinator, device_id))
    async_add_entities(entities)


class AjaxHubFirmwareUpdate(CoordinatorEntity[AjaxCobrandedCoordinator], UpdateEntity):
    """Read-only firmware update entity for an Ajax hub."""

    _attr_has_entity_name = True
    _attr_translation_key = "hub_firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    # No `INSTALL` feature — the entity is informational only. `PROGRESS`
    # is required for HA to honor the `in_progress` property at all:
    # without it, `UpdateEntity.state_attributes` ignores the property
    # and reports the internal install flag (always False here).
    _attr_supported_features = UpdateEntityFeature.PROGRESS

    def __init__(self, coordinator: AjaxCobrandedCoordinator, hub_id: str) -> None:
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._attr_unique_id = f"aegis_ajax_{hub_id}_firmware"
        hub_device = coordinator.devices.get(hub_id)
        if hub_device:
            self._attr_device_info = build_device_info(
                hub_device,
                coordinator.rooms,
                firmware_version=self._hub_reported_version,
            )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Covers the case where the version landed between construction and
        # the entity actually being added — `device_info` was already read
        # by then, so the registry needs telling either way.
        self._async_write_sw_version()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._async_write_sw_version()
        super()._handle_coordinator_update()

    @callback
    def _async_write_sw_version(self) -> None:
        """Push the hub's reported firmware into the device registry (#388).

        Home Assistant reads `device_info` exactly once, in
        `entity_platform._async_add_entity`, and hands it to
        `async_get_or_create`; it is never consulted again. The hub reports
        its firmware over HTS, whose handshake is still in flight when the
        platforms are forwarded, so the version is essentially never known
        at that moment. Writing it to the registry as it arrives is what
        actually gets it onto the hub's device page, and it keeps following
        the hub across firmware upgrades.
        """
        version = self._hub_reported_version
        if not version:
            return
        registry = dr.async_get(self.hass)
        entry = async_get_registered_device(
            registry, (DOMAIN, self._hub_id), self.coordinator.entry_id
        )
        # Guard on equality so a steady stream of HTS updates doesn't write
        # to (and re-save) the registry on every coordinator tick.
        if entry is None or entry.sw_version == version:
            return
        registry.async_update_device(entry.id, sw_version=version)

    @property
    def _info(self) -> HubFirmwareUpdateInfo | None:
        return self.coordinator.hub_firmware_updates.get(self._hub_id)

    @property
    def _hub_reported_version(self) -> str | None:
        """The firmware version the hub says it is running, if it says (#388).

        Comes from the hub's own status row on the HTS channel, not from the
        gRPC snapshot — same channel that already supplies ethernet, cellular
        and signal strength. Hub firmwares differ in which sub-keys they put
        in that row, so this is `None` whenever the value hasn't arrived.
        """
        state = self.coordinator.hub_network.get(self._hub_id)
        if state is None:
            return None
        return state.firmware_version or None

    @property
    def installed_version(self) -> str | None:
        # See `_INSTALLED_VERSION_PLACEHOLDER` for why the fallback is a
        # constant rather than `None`: HA's state computation needs a
        # non-`None` installed version to differentiate "up to date"
        # from "unknown".
        return self._hub_reported_version or _INSTALLED_VERSION_PLACEHOLDER

    @property
    def latest_version(self) -> str | None:
        info = self._info
        if info is None or not info.target_version:
            # No pending update from Ajax — mirror installed_version so
            # HA computes `STATE_OFF` and renders "Up to date". Mirroring
            # the property rather than the placeholder keeps the two sides
            # equal whether or not the hub reported a real version.
            return self.installed_version
        return info.target_version

    @property
    def in_progress(self) -> bool:
        info = self._info
        return info is not None and info.state == HUB_FW_STATE_DOWNLOADING

    @property
    def release_summary(self) -> str | None:
        # The Ajax stream doesn't expose the currently-installed firmware
        # version, so "Up-to-date" here is shorthand for "Ajax has not
        # queued an update for this hub right now" — not a positive
        # confirmation that the hub is running the latest firmware Ajax
        # has ever published. The Ajax cloud schedules updates on its
        # own; this integration only mirrors what the cloud is telling
        # us, and the entity is informational (no install action).
        info = self._info
        running = self._hub_reported_version
        if info is None:
            if running:
                # #388: with the hub's own version in hand the old caveat
                # would be untrue, so state what we actually know.
                return (
                    f"This hub reports it is running firmware {running}. "
                    "Ajax has not queued an update for it."
                )
            return (
                "Ajax has not queued a firmware update for this hub. "
                "The actual installed firmware version is not exposed by "
                "Ajax to the integration, so 'Up-to-date' reflects only "
                "the absence of a queued update."
            )
        queued = (
            f"Ajax has queued firmware {info.target_version} for this hub. "
            "The hub will install it on its own; this entity is "
            "informational and cannot trigger or skip the update."
        )
        if running:
            return f"This hub reports it is running firmware {running}. {queued}"
        return queued


class AjaxDeviceFirmwareUpdate(CoordinatorEntity[AjaxCobrandedCoordinator], UpdateEntity):
    """Read-only firmware update entity for a single Ajax device (2.1).

    Same design as `AjaxHubFirmwareUpdate`: informational only (no
    `INSTALL` feature, no `async_install`), and Ajax does not expose the
    currently-installed version, so `installed_version` is a constant
    placeholder. Disabled by default — a typical install has many
    devices and most users only enable this when chasing a stuck update.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "device_firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    # No `INSTALL` (informational only); `PROGRESS` so HA honors the
    # `in_progress`/`update_percentage` properties — without the flag
    # `UpdateEntity.state_attributes` ignores both.
    _attr_supported_features = UpdateEntityFeature.PROGRESS
    # Disabled-by-default: opt-in per device to avoid 10-30 entities most
    # users don't want.
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AjaxCobrandedCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"aegis_ajax_{device_id}_firmware"
        device = coordinator.devices.get(device_id)
        if device:
            self._attr_device_info = build_device_info(
                device, coordinator.rooms, via_device_id=coordinator.hub_registry_id(device.hub_id)
            )

    @property
    def available(self) -> bool:
        # A device deleted from the hub drops out of `coordinator.devices`
        # but its (opt-in) entity stays in the registry — HA never evicts
        # orphans on its own. Without this gate the orphan would keep
        # reporting a confident "Up to date" forever.
        return super().available and self._device_id in self.coordinator.devices

    @property
    def _info(self) -> DeviceFirmwareUpdateInfo | None:
        # `.upper()` on both sides (see coordinator write): the update map
        # comes from `streamHubObject` while `Device.id` comes from the
        # devices snapshot — two services whose hex-id casing is not
        # guaranteed to match.
        return self.coordinator.device_firmware_updates.get(self._device_id.upper())

    @property
    def installed_version(self) -> str | None:
        # See `_INSTALLED_VERSION_PLACEHOLDER`: Ajax doesn't expose the
        # device's current version, so a constant is used to let HA
        # differentiate "up to date" from "unknown".
        return _INSTALLED_VERSION_PLACEHOLDER

    @property
    def latest_version(self) -> str | None:
        info = self._info
        if info is None or not info.target_version or info.state == DEVICE_FW_STATE_COMPLETED:
            # No pending update (or the install just finished and the
            # entry hasn't dropped from the snapshot yet) — mirror
            # installed_version so HA renders "Up to date" (STATE_OFF)
            # rather than "unknown" or a stale "update available".
            return self.installed_version
        return info.target_version

    @property
    def in_progress(self) -> bool:
        info = self._info
        return info is not None and info.state in (
            DEVICE_FW_STATE_DOWNLOADING,
            DEVICE_FW_STATE_INSTALLING,
        )

    @property
    def update_percentage(self) -> int | None:
        # Only the download phase carries a 0-99 percentage; the install
        # phase has no progress signal, so HA shows an indeterminate bar.
        info = self._info
        if info is not None and info.state == DEVICE_FW_STATE_DOWNLOADING:
            return info.progress
        return None

    @property
    def release_summary(self) -> str | None:
        info = self._info
        if info is None:
            return (
                "Ajax has not queued a firmware update for this device. "
                "The actual installed firmware version is not exposed by "
                "Ajax to the integration, so 'Up-to-date' reflects only "
                "the absence of a queued update."
            )
        critical = " (security-critical)" if info.is_critical else ""
        if info.state == DEVICE_FW_STATE_COMPLETED:
            return (
                f"Firmware {info.target_version} was just installed on "
                "this device; Ajax will clear the entry shortly."
            )
        if info.state == DEVICE_FW_STATE_FAILED:
            return (
                f"The last attempt to install firmware {info.target_version}"
                f"{critical} on this device FAILED. Ajax retries on its "
                "own schedule; if it stays failed, check the device in "
                "the Ajax app."
            )
        return (
            f"Ajax has queued firmware {info.target_version} for this "
            f"device{critical}. The device will install it on its own; "
            "this entity is informational and cannot trigger or skip the "
            "update."
        )
