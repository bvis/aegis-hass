"""Switch entities for Ajax Security."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.api.models import (
    DeviceCommand,
    device_deactivation_kinds,
    is_device_deactivated,
)
from custom_components.aegis_ajax.const import (
    BYPASS_REQUIRED_PERMISSION,
    BYPASS_SWITCHES_ALWAYS,
    BYPASS_SWITCHES_NEVER,
    CONF_BYPASS_SWITCHES,
    DEFAULT_BYPASS_SWITCHES,
    DOMAIN,
)
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.device_handlers import capabilities_for
from custom_components.aegis_ajax.entity import (
    async_send_device_command,
    async_set_chimes_mode,
    build_device_info,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.aegis_ajax.api.models import Device, Space

_LOGGER = logging.getLogger(__name__)

# One action at a time: never send Ajax more requests than the app would.
PARALLEL_UPDATES = 1

SWITCH_DEVICE_TYPES: dict[str, int] = {
    "relay": 1,
    "relay_fibra_base": 1,
    "wall_switch": 1,
    "socket": 1,
    "socket_b": 1,
    "socket_g": 1,
    "socket_outlet_type_e": 1,
    "socket_outlet_type_f": 1,
    "socket_type_g_plus": 1,
    "light_switch": 1,
    "light_switch_one_gang": 1,
    "light_switch_one_gang_na": 1,
    "light_switch_2_way": 1,
    "light_switch_crossover": 1,
    "light_switch_three_way_na": 1,
    "light_switch_two_gang": 2,
    "light_switch_two_channel_two_way": 2,
    "light_switch_four_way_na": 4,
}


async def _resolve_bypass_hubs(
    coordinator: AjaxCobrandedCoordinator, entry: ConfigEntry, mode: str
) -> set[str] | None:
    """Which hubs should get per-device bypass switches, per the option (#bypass).

    Returns a set of hub ids to create switches for, or `None` meaning "all"
    (`always` mode). `never` → empty set. `auto` → only hubs whose space the
    logged-in user has `DEVICE_EDIT` on (a read-only permission lookup);
    failing the lookup is fail-open (the hub is included) so we never silently
    hide a capability the user actually has — the next bypass attempt would
    surface a clear error if they don't.
    """
    if mode == BYPASS_SWITCHES_NEVER:
        return set()
    if mode == BYPASS_SWITCHES_ALWAYS:
        return None
    # auto
    user_hex = entry.data.get("user_hex_id", "")
    allowed: set[str] = set()
    for space in coordinator.spaces.values():
        if not space.hub_id:
            continue
        perms = await coordinator.spaces_api.get_member_space_permissions(space.id, user_hex)
        if perms is None or BYPASS_REQUIRED_PERMISSION in perms:
            allowed.add(space.hub_id)
        else:
            _LOGGER.debug(
                "Skipping bypass switches for hub %s: user lacks %s",
                space.hub_id,
                BYPASS_REQUIRED_PERMISSION,
            )
    return allowed


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AjaxCobrandedCoordinator = entry.runtime_data
    hub_ids = {space.hub_id for space in coordinator.spaces.values() if space.hub_id}
    bypass_mode = entry.options.get(CONF_BYPASS_SWITCHES, DEFAULT_BYPASS_SWITCHES)
    bypass_hubs = await _resolve_bypass_hubs(coordinator, entry, bypass_mode)
    entities: list[SwitchEntity] = []
    for device_id, device in coordinator.devices.items():
        num_channels = SWITCH_DEVICE_TYPES.get(device.device_type, 0)
        for ch in range(1, num_channels + 1):
            entities.append(
                AjaxSwitch(
                    coordinator=coordinator,
                    device_id=device_id,
                    hub_id=device.hub_id,
                    device_type=device.device_type,
                    channel=ch,
                )
            )
        # Every non-hub device can be deactivated (bypassed) before arming,
        # subject to the `bypass_switches` option (None = create for all hubs).
        if device_id not in hub_ids and (bypass_hubs is None or device.hub_id in bypass_hubs):
            entities.append(
                AjaxBypassSwitch(
                    coordinator=coordinator,
                    device_id=device_id,
                    hub_id=device.hub_id,
                    device_type=device.device_type,
                )
            )
        if capabilities_for(device).has_night_mode_arm:
            entities.append(
                AjaxNightModeArmSwitch(
                    coordinator=coordinator,
                    device_id=device_id,
                    hub_id=device.hub_id,
                    device_type=device.device_type,
                )
            )
    # One hub-wide Chime switch per hub that exposes the feature (#239). Hubs
    # that don't report a Chime status (UNSPECIFIED) get none.
    for space in coordinator.spaces.values():
        if (
            space.hub_id
            and space.chime_status.is_supported
            and coordinator.devices.get(space.hub_id)
        ):
            entities.append(
                AjaxChimeSwitch(
                    coordinator=coordinator,
                    space_id=space.id,
                    hub_id=space.hub_id,
                )
            )
    _evict_orphan_bypass_switches(
        hass,
        entry,
        provided={
            e.unique_id
            for e in entities
            if isinstance(e, AjaxBypassSwitch) and e.unique_id is not None
        },
    )
    _evict_orphan_chime_switches(
        hass,
        entry,
        provided={
            e.unique_id
            for e in entities
            if isinstance(e, AjaxChimeSwitch) and e.unique_id is not None
        },
    )
    async_add_entities(entities)


def _evict_orphan_bypass_switches(
    hass: HomeAssistant, entry: ConfigEntry, *, provided: set[str]
) -> None:
    """Remove bypass-switch entities the current option no longer provides.

    When `bypass_switches` flips to `never`, or `auto` loses `DEVICE_EDIT` on a
    hub, this run stops creating the corresponding `AjaxBypassSwitch`. HA does
    NOT evict an entity its platform stopped providing — it lingers in the
    registry as `unavailable` until the user deletes it by hand. Mirror the
    video-doorbell device eviction (#173) at the entity-registry level so a
    stale bypass switch disappears on the reload that drops it (#bypass).
    """
    entity_reg = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(entity_reg, entry.entry_id):
        unique_id = reg_entry.unique_id
        if (
            reg_entry.domain == "switch"
            and unique_id.endswith("_bypass")
            and unique_id not in provided
        ):
            _LOGGER.info(
                "Removing orphaned bypass switch %s — the bypass_switches option "
                "no longer provides it (#bypass)",
                reg_entry.entity_id,
            )
            entity_reg.async_remove(reg_entry.entity_id)


def _evict_orphan_chime_switches(
    hass: HomeAssistant, entry: ConfigEntry, *, provided: set[str]
) -> None:
    """Remove Chime-switch entities no longer provided (#239).

    A hub that stops exposing the Chime feature (firmware change, hub removed
    from the space) drops its `AjaxChimeSwitch`. HA leaves the orphan in the
    registry as `unavailable` until the user deletes it; evict it here on the
    reload that drops it, mirroring the bypass-switch eviction.
    """
    entity_reg = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(entity_reg, entry.entry_id):
        unique_id = reg_entry.unique_id
        if (
            reg_entry.domain == "switch"
            and unique_id.endswith("_chime")
            and unique_id not in provided
        ):
            _LOGGER.info(
                "Removing orphaned Chime switch %s — the hub no longer exposes "
                "the Chime feature (#239)",
                reg_entry.entity_id,
            )
            entity_reg.async_remove(reg_entry.entity_id)


class AjaxSwitch(CoordinatorEntity[AjaxCobrandedCoordinator], SwitchEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AjaxCobrandedCoordinator,
        device_id: str,
        hub_id: str,
        device_type: str,
        channel: int,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._hub_id = hub_id
        self._device_type = device_type
        self._channel = channel
        self._attr_unique_id = f"aegis_ajax_{device_id}_switch_{channel}"
        total_channels = SWITCH_DEVICE_TYPES.get(device_type, 1)
        if total_channels > 1:
            self._attr_translation_key = f"channel_{channel}"
        else:
            self._attr_name = None
        device = coordinator.devices.get(device_id)
        if device:
            self._attr_device_info = build_device_info(
                device, coordinator.rooms, via_device_id=coordinator.hub_registry_id(device.hub_id)
            )

    @property
    def _device(self) -> Device | None:
        return self.coordinator.devices.get(self._device_id)

    @property
    def available(self) -> bool:
        device = self._device
        return device is not None and device.is_online

    @property
    def is_on(self) -> bool | None:
        device = self._device
        if device is None:
            return None
        return bool(device.statuses.get(f"switch_ch{self._channel}", False))

    async def async_turn_on(self, **kwargs: object) -> None:
        cmd = DeviceCommand.on(
            hub_id=self._hub_id,
            device_id=self._device_id,
            device_type=self._device_type,
            channels=[self._channel],
        )
        await async_send_device_command(self.coordinator, cmd)

    async def async_turn_off(self, **kwargs: object) -> None:
        cmd = DeviceCommand.off(
            hub_id=self._hub_id,
            device_id=self._device_id,
            device_type=self._device_type,
            channels=[self._channel],
        )
        await async_send_device_command(self.coordinator, cmd)


class AjaxBypassSwitch(CoordinatorEntity[AjaxCobrandedCoordinator], SwitchEntity):
    """Deactivate (bypass) a device so it's excluded while the system is armed.

    `on` = the device is excluded from protection, `off` = active. The state
    reflects the panel's reality regardless of who deactivated the device —
    the Ajax app, an installer, or this switch — because a device deactivated
    outside HA reports it through the `*_deactivation_*` statuses and leaves
    the snapshot's `bypassed` flag False (#338). Reading only that flag showed
    a disabled sensor as live protection.

    Writing goes out as the permanent ("engineering") deactivation; the
    granular mode in force is exposed as the `deactivation_kinds` attribute.
    **Both directions are honoured** — see `DevicesApi._device_bypass` for why
    the enum value that clears a deactivation is not the one its name suggests,
    and why sending the wrong one looked like the hub ignoring us (#338).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "bypass"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: AjaxCobrandedCoordinator,
        device_id: str,
        hub_id: str,
        device_type: str,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._hub_id = hub_id
        self._device_type = device_type
        self._attr_unique_id = f"aegis_ajax_{device_id}_bypass"
        device = coordinator.devices.get(device_id)
        if device:
            self._attr_device_info = build_device_info(
                device, coordinator.rooms, via_device_id=coordinator.hub_registry_id(device.hub_id)
            )

    @property
    def _device(self) -> Device | None:
        return self.coordinator.devices.get(self._device_id)

    @property
    def available(self) -> bool:
        device = self._device
        return device is not None and device.is_online

    @property
    def is_on(self) -> bool | None:
        device = self._device
        if device is None:
            return None
        return is_device_deactivated(device)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose which deactivation mode(s) the panel has in force (#338).

        Automations that only care about a fully disabled device can gate on
        `temporary_deactivation_whole` / `one_time_deactivation_whole` instead
        of the switch state, which also turns on for a tamper-only
        deactivation. Mind the inverted naming — see `DEACTIVATION_STATUS_KEYS`.
        """
        device = self._device
        kinds = device_deactivation_kinds(device) if device is not None else []
        return {"deactivation_kinds": kinds}

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._set_bypass(enable=True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._set_bypass(enable=False)

    async def _set_bypass(self, *, enable: bool) -> None:
        cmd = DeviceCommand.bypass(
            hub_id=self._hub_id,
            device_id=self._device_id,
            device_type=self._device_type,
            enable=enable,
        )
        await async_send_device_command(self.coordinator, cmd)
        # The hub can accept this command and apply nothing (#338), so the
        # entity must be corrected by an independent read rather than trusting
        # the success response. Only reached when the write did not raise.
        self.coordinator.schedule_bypass_confirm(self._device_id, expected=enable)


class AjaxNightModeArmSwitch(CoordinatorEntity[AjaxCobrandedCoordinator], SwitchEntity):
    """A detector's "Arm in Night Mode" setting (#579).

    `on` = the detector arms when the space or its group goes into Night
    Mode; `off` = it only protects in full arm. This is the hub's stored
    setting, the same one the Ajax app edits, not a temporary state: it stays
    as set until someone changes it again. Disabled by default because it
    lets Home Assistant weaken what Night Mode protects.

    The state comes from the device stream's `armed_in_night_mode` status, so
    reading it costs no requests. Writing needs device-edit permission; a
    refusal from the hub (e.g. while armed) is surfaced as a translated error.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "night_mode_arm"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: AjaxCobrandedCoordinator,
        device_id: str,
        hub_id: str,
        device_type: str,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._hub_id = hub_id
        self._device_type = device_type
        self._attr_unique_id = f"aegis_ajax_{device_id}_night_mode_arm"
        device = coordinator.devices.get(device_id)
        if device:
            self._attr_device_info = build_device_info(
                device, coordinator.rooms, via_device_id=coordinator.hub_registry_id(device.hub_id)
            )

    @property
    def available(self) -> bool:
        device = self.coordinator.devices.get(self._device_id)
        return device is not None and device.is_online

    @property
    def is_on(self) -> bool | None:
        device = self.coordinator.devices.get(self._device_id)
        if device is None:
            return None
        return bool(device.statuses.get("armed_in_night_mode"))

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._set(enable=True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._set(enable=False)

    async def _set(self, *, enable: bool) -> None:
        cmd = DeviceCommand.set_night_mode_arm(
            hub_id=self._hub_id,
            device_id=self._device_id,
            device_type=self._device_type,
            enable=enable,
        )
        try:
            await async_send_device_command(self.coordinator, cmd)
        except HomeAssistantError as err:
            # The hub refuses settings changes on an armed detector. Say so
            # plainly, and as a user error so HA doesn't log a traceback.
            if err.translation_key == "command_hub_wrong_state":
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="night_mode_arm_while_armed"
                ) from err
            raise


class AjaxChimeSwitch(CoordinatorEntity[AjaxCobrandedCoordinator], SwitchEntity):
    """Enable/disable the hub-wide Chime (#239).

    `on` = the hub plays the Chime tone on the configured detectors while
    disarmed; `off` = silenced. Hub-level setting, so one switch per hub.
    State is read from `space.chime_status` (the same field the Ajax app
    shows); toggling requires the account's `EDIT_CHIMES` permission — a
    limited account surfaces a clear `permission_denied` error on toggle.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "chime"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: AjaxCobrandedCoordinator,
        space_id: str,
        hub_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._space_id = space_id
        self._hub_id = hub_id
        self._attr_unique_id = f"aegis_ajax_{hub_id}_chime"
        hub_device = coordinator.devices.get(hub_id)
        if hub_device:
            self._attr_device_info = build_device_info(hub_device, coordinator.rooms)

    @property
    def _space(self) -> Space | None:
        return self.coordinator.spaces.get(self._space_id)

    @property
    def available(self) -> bool:
        space = self._space
        return space is not None and space.is_online and space.chime_status.is_supported

    @property
    def is_on(self) -> bool | None:
        space = self._space
        if space is None:
            return None
        return space.chime_status.is_on

    async def async_turn_on(self, **kwargs: object) -> None:
        await async_set_chimes_mode(self.coordinator, self._hub_id, enable=True)
        self.coordinator.set_chime_optimistic(self._space_id, enable=True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await async_set_chimes_mode(self.coordinator, self._hub_id, enable=False)
        self.coordinator.set_chime_optimistic(self._space_id, enable=False)
