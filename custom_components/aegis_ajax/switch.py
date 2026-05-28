"""Switch entities for Ajax Security."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.api.models import DeviceCommand
from custom_components.aegis_ajax.const import (
    BYPASS_REQUIRED_PERMISSION,
    BYPASS_SWITCHES_ALWAYS,
    BYPASS_SWITCHES_NEVER,
    CONF_BYPASS_SWITCHES,
    DEFAULT_BYPASS_SWITCHES,
)
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.entity import async_send_device_command, build_device_info

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.aegis_ajax.api.models import Device

_LOGGER = logging.getLogger(__name__)

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
    _evict_orphan_bypass_switches(
        hass,
        entry,
        provided={
            e.unique_id
            for e in entities
            if isinstance(e, AjaxBypassSwitch) and e.unique_id is not None
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
            self._attr_device_info = build_device_info(device, coordinator.rooms)

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

    `on` = device bypassed/deactivated (permanent, "engineering"), `off` =
    active. Mirrors the `bypassed` flag the snapshot reports.
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
            self._attr_device_info = build_device_info(device, coordinator.rooms)

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
        return bool(device.bypassed)

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
