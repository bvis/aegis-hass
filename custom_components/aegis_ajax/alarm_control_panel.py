"""Alarm control panel for Ajax Security."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.alarm_control_panel import (  # type: ignore[attr-defined]
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from custom_components.aegis_ajax.const import (
    CONF_EXPOSE_ARM_HOME,
    CONF_FORCE_ARM,
    DEFAULT_EXPOSE_ARM_HOME,
    DOMAIN,
    MANUFACTURER,
    SecurityState,
)
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.delay_states import DelayKind
from custom_components.aegis_ajax.entity import build_device_info

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.aegis_ajax.api.models import Group, Space

_LOGGER = logging.getLogger(__name__)

_ARM_ERRORS: dict[str, dict[str, str]] = {
    "hub_detected_malfunctions": {
        "en": "Cannot arm: open sensors or malfunctions detected",
        "es": "No se puede armar: sensores abiertos o averías detectadas",
        "ca": "No es pot armar: sensors oberts o avaries detectades",
        "de": "Scharfschalten nicht möglich: offene Sensoren oder Störungen",
        "fr": "Impossible d'armer : capteurs ouverts ou dysfonctionnements",
        "it": "Impossibile inserire: sensori aperti o malfunzionamenti",
        "nl": "Kan niet inschakelen: open sensoren of storingen",
        "pl": "Nie można uzbroić: otwarte czujniki lub awarie",
        "pt": "Não é possível armar: sensores abertos ou avarias",
        "pt-BR": "Não é possível armar: sensores abertos ou falhas",
        "ro": "Nu se poate arma: senzori deschiși sau defecțiuni",
        "tr": "Kurma başarısız: açık sensörler veya arızalar",
        "uk": "Неможливо увімкнути: відкриті датчики або несправності",
        "cs": "Nelze zastřežit: otevřené senzory nebo poruchy",
    },
    "hub_not_connected": {
        "en": "Cannot arm: hub is offline",
        "es": "No se puede armar: hub desconectado",
        "ca": "No es pot armar: hub desconnectat",
        "de": "Scharfschalten nicht möglich: Hub offline",
        "fr": "Impossible d'armer : hub hors ligne",
        "it": "Impossibile inserire: hub offline",
        "nl": "Kan niet inschakelen: hub offline",
        "pl": "Nie można uzbroić: hub offline",
        "pt": "Não é possível armar: hub offline",
        "pt-BR": "Não é possível armar: hub offline",
        "ro": "Nu se poate arma: hub deconectat",
        "tr": "Kurma başarısız: hub çevrimdışı",
        "uk": "Неможливо увімкнути: хаб офлайн",
        "cs": "Nelze zastřežit: hub offline",
    },
    "hub_busy": {
        "en": "Hub is busy, try again in a few seconds",
        "es": "Hub ocupado, inténtalo en unos segundos",
        "ca": "Hub ocupat, torna-ho a provar en uns segons",
        "de": "Hub beschäftigt, versuchen Sie es in einigen Sekunden erneut",
        "fr": "Hub occupé, réessayez dans quelques secondes",
        "it": "Hub occupato, riprova tra qualche secondo",
        "nl": "Hub bezet, probeer het over een paar seconden opnieuw",
        "pl": "Hub zajęty, spróbuj ponownie za kilka sekund",
        "pt": "Hub ocupado, tente novamente em alguns segundos",
        "pt-BR": "Hub ocupado, tente novamente em alguns segundos",
        "ro": "Hub ocupat, încercați din nou în câteva secunde",
        "tr": "Hub meşgul, birkaç saniye sonra tekrar deneyin",
        "uk": "Хаб зайнятий, спробуйте через кілька секунд",
        "cs": "Hub je zaneprázdněn, zkuste to za několik sekund",
    },
    "another_transition_is_in_progress": {
        "en": "Another arm/disarm operation is in progress",
        "es": "Otra operación de armado/desarmado en curso",
        "ca": "Una altra operació d'armat/desarmat en curs",
        "de": "Eine andere Scharf-/Unscharfschaltung läuft",
        "fr": "Une autre opération d'armement/désarmement est en cours",
        "it": "Un'altra operazione di inserimento/disinserimento è in corso",
        "nl": "Een andere in-/uitschakelbewerking is bezig",
        "pl": "Inna operacja uzbrojenia/rozbrojenia w toku",
        "pt": "Outra operação de armar/desarmar em curso",
        "pt-BR": "Outra operação de armar/desarmar em andamento",
        "ro": "O altă operațiune de armare/dezarmare este în curs",
        "tr": "Başka bir kurma/devre dışı bırakma işlemi devam ediyor",
        "uk": "Інша операція увімкнення/вимкнення в процесі",
        "cs": "Probíhá jiná operace zastřežení/odstřežení",
    },
    "disarm_rejected": {
        "en": "Cannot disarm: command rejected by hub",
        "es": "No se puede desarmar: comando rechazado por el hub",
        "ca": "No es pot desarmar: comanda rebutjada pel hub",
        "de": "Unscharfschalten nicht möglich: Befehl vom Hub abgelehnt",
        "fr": "Impossible de désarmer : commande rejetée par le hub",
        "it": "Impossibile disinserire: comando rifiutato dall'hub",
        "nl": "Kan niet uitschakelen: opdracht geweigerd door hub",
        "pl": "Nie można rozbroić: polecenie odrzucone przez hub",
        "pt": "Não é possível desarmar: comando rejeitado pelo hub",
        "pt-BR": "Não é possível desarmar: comando rejeitado pelo hub",
        "ro": "Nu se poate dezarma: comandă respinsă de hub",
        "tr": "Devre dışı bırakılamıyor: komut hub tarafından reddedildi",
        "uk": "Неможливо вимкнути: команду відхилено хабом",
        "cs": "Nelze odstřežit: příkaz odmítnut hubem",
    },
    "invalid_alarm_code": {
        "en": "Invalid alarm code",
        "es": "Código de alarma incorrecto",
        "ca": "Codi d'alarma incorrecte",
        "de": "Ungültiger Alarmcode",
        "fr": "Code d'alarme invalide",
        "it": "Codice allarme non valido",
        "nl": "Ongeldige alarmcode",
        "pl": "Nieprawidłowy kod alarmu",
        "pt": "Código de alarme inválido",
        "pt-BR": "Código de alarme inválido",
        "ro": "Cod de alarmă invalid",
        "tr": "Geçersiz alarm kodu",
        "uk": "Невірний код тривоги",
        "cs": "Neplatný kód alarmu",
    },
}

_ISSUE_LABELS: dict[str, dict[str, str]] = {
    "open": {
        "en": "open",
        "es": "abierto",
        "ca": "obert",
        "de": "offen",
        "fr": "ouvert",
        "it": "aperto",
        "nl": "open",
        "pl": "otwarty",
        "pt": "aberto",
        "pt-BR": "aberto",
        "ro": "deschis",
        "tr": "açık",
        "uk": "відкритий",
        "cs": "otevřený",
    },
    "low_battery": {
        "en": "low battery",
        "es": "batería baja",
        "ca": "bateria baixa",
        "de": "Akku schwach",
        "fr": "batterie faible",
        "it": "batteria scarica",
        "nl": "lage batterij",
        "pl": "słaba bateria",
        "pt": "bateria fraca",
        "pt-BR": "bateria fraca",
        "ro": "baterie descărcată",
        "tr": "düşük pil",
        "uk": "низький заряд",
        "cs": "slabá baterie",
    },
    "malfunction": {
        "en": "malfunction",
        "es": "avería",
        "ca": "avaria",
        "de": "Störung",
        "fr": "dysfonctionnement",
        "it": "malfunzionamento",
        "nl": "storing",
        "pl": "awaria",
        "pt": "avaria",
        "pt-BR": "falha",
        "ro": "defecțiune",
        "tr": "arıza",
        "uk": "несправність",
        "cs": "porucha",
    },
    "tamper": {
        "en": "tamper",
        "es": "manipulación",
        "ca": "manipulació",
        "de": "Sabotage",
        "fr": "sabotage",
        "it": "manomissione",
        "nl": "sabotage",
        "pl": "sabotaż",
        "pt": "violação",
        "pt-BR": "violação",
        "ro": "sabotaj",
        "tr": "müdahale",
        "uk": "втручання",
        "cs": "sabotáž",
    },
}


_STATE_MAP = {
    SecurityState.ARMED: AlarmControlPanelState.ARMED_AWAY,
    SecurityState.DISARMED: AlarmControlPanelState.DISARMED,
    SecurityState.NIGHT_MODE: AlarmControlPanelState.ARMED_NIGHT,
    SecurityState.PARTIALLY_ARMED: AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
    SecurityState.AWAITING_EXIT_TIMER: AlarmControlPanelState.ARMING,
    SecurityState.AWAITING_SECOND_STAGE: AlarmControlPanelState.ARMING,
    SecurityState.TWO_STAGE_INCOMPLETE: AlarmControlPanelState.ARMING,
    SecurityState.AWAITING_VDS: AlarmControlPanelState.ARMING,
    SecurityState.NONE: AlarmControlPanelState.DISARMED,
}


def map_security_state(state: SecurityState) -> AlarmControlPanelState:
    return _STATE_MAP.get(state, AlarmControlPanelState.DISARMED)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    coordinator: AjaxCobrandedCoordinator = entry.runtime_data
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain != "alarm_control_panel" or entity.platform != DOMAIN:
            continue
        legacy_ids = {
            f"aegis_ajax_alarm_{sid}": f"aegis_ajax_alarm_{entry.entry_id}_{sid}"
            for sid in coordinator.spaces
        }
        for sid, space in coordinator.spaces.items():
            for group in space.groups:
                legacy_ids[f"aegis_ajax_alarm_{sid}_group_{group.id}"] = (
                    f"aegis_ajax_alarm_{entry.entry_id}_{sid}_group_{group.id}"
                )
        if entity.unique_id in legacy_ids:
            registry.async_update_entity(
                entity.entity_id, new_unique_id=legacy_ids[entity.unique_id]
            )
    entities: list[AlarmControlPanelEntity] = []
    for space_id, space in coordinator.spaces.items():
        # Always create the space-level panel. It carries night-mode support
        # (Ajax exposes night mode only at the space level) and acts as the
        # "arm everything" / "disarm everything" control. In group mode the
        # space-level arm/disarm cascades down to all groups server-side.
        entities.append(AjaxAlarmControlPanel(coordinator=coordinator, space_id=space_id))
        # In group mode, additionally expose one panel per group for
        # independent arm/disarm. Group panels deliberately do NOT expose
        # night mode — the protocol has no per-group night-mode endpoint.
        if space.group_mode_enabled and space.groups:
            entities.extend(
                AjaxGroupAlarmControlPanel(
                    coordinator=coordinator, space_id=space_id, group_id=group.id
                )
                for group in space.groups
            )
    async_add_entities(entities)


class _AjaxAlarmPanelBase(CoordinatorEntity[AjaxCobrandedCoordinator], AlarmControlPanelEntity):
    """Shared base for the space-level and per-group alarm panels.

    Holds the parts that are identical across both: PIN-code option
    plumbing, code validation, `_force_arm` flag, hub `device_info` setup,
    `_translate_error` machinery, and the malfunction-aware
    `_arm_error` helper. Subclasses override the small surface that
    differs — `available`, `alarm_state`, `extra_state_attributes`,
    the actual `async_alarm_*` methods (each calls a different API),
    and `_optimistic_state_update` (space vs group).
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: AjaxCobrandedCoordinator, space_id: str) -> None:
        super().__init__(coordinator)
        self._space_id = space_id
        if coordinator.config_entry is None:
            raise ValueError("Alarm panels require an owning config entry")
        self._account_entry_id = coordinator.config_entry.entry_id
        entry_data = getattr(coordinator.config_entry, "data", {})
        raw_label = entry_data.get("account_label") or entry_data.get("email", "account")
        self._account_label = slugify(str(raw_label)) or "account"
        space = coordinator.spaces.get(space_id)
        space_name = space.name if space and isinstance(space.name, str) else space_id
        self._attr_suggested_object_id = f"ajax_{self._account_label}_{slugify(str(space_name))}"
        hub_id = space.hub_id if space else space_id
        hub_device = coordinator.devices.get(hub_id)
        if hub_device:
            self._attr_device_info = build_device_info(hub_device, coordinator.rooms)
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, hub_id)},
                name=space.name if space else "Ajax Hub",
                manufacturer=MANUFACTURER,
                model="Hub",
            )

    @property
    def _space(self) -> Space | None:
        return self.coordinator.spaces.get(self._space_id)

    def _get_options(self) -> dict[str, Any]:
        entry = self.coordinator.config_entry
        if entry is None:
            return {}
        return dict(entry.options)

    @property
    def code_arm_required(self) -> bool:
        return bool(self._get_options().get("use_pin_code", False))

    @property
    def code_format(self) -> CodeFormat | None:
        """Numeric keypad when a PIN is configured, otherwise no code entry.

        Returning `CodeFormat.NUMBER` makes the Lovelace alarm card render a
        numeric keypad and lets the Nabu Casa / Alexa skill prompt for a PIN on
        disarm. `None` (no PIN configured) keeps the card keypad-free and is
        also what lets Alexa discover the panel — its skill only exposes panels
        that don't require a code to arm.
        """
        return CodeFormat.NUMBER if self.code_arm_required else None

    @property
    def _force_arm(self) -> bool:
        return bool(self._get_options().get(CONF_FORCE_ARM, False))

    @property
    def supported_features(self) -> AlarmControlPanelEntityFeature:
        """Advertise the panel's arm modes, optionally hiding ARM_HOME (#259).

        ARM_HOME duplicates Ajax's single partial mode and is advertised mainly
        so the Alexa skill discovers the panel (#221). Users without Alexa/Cloud
        can hide the redundant button via the `expose_arm_home` option; when off
        we mask the bit while keeping ARM_AWAY/ARM_NIGHT intact.
        """
        features = self._attr_supported_features
        expose = bool(self._get_options().get(CONF_EXPOSE_ARM_HOME, DEFAULT_EXPOSE_ARM_HOME))
        if not expose:
            features &= ~AlarmControlPanelEntityFeature.ARM_HOME
        return features

    def _validate_code(self, code: str | None) -> None:
        """Raise HomeAssistantError if the provided code does not match the stored hash."""
        from custom_components.aegis_ajax.service_security import validate_pin  # noqa: PLC0415

        try:
            validate_pin(self._get_options(), code)
        except HomeAssistantError as err:
            raise HomeAssistantError(self._translate_error("invalid_alarm_code")) from err

    def _issue_label(self, key: str) -> str:
        """Return a translated issue label for the current HA language."""
        lang = self.hass.config.language if self.hass else "en"
        return _ISSUE_LABELS.get(key, {}).get(lang, _ISSUE_LABELS.get(key, {}).get("en", key))

    def _describe_blocking_issues(self) -> str:
        """Scan devices for issues that prevent arming and return a description."""
        space = self._space
        if space is None:
            return ""
        issues: list[str] = []
        for device in self.coordinator.devices.values():
            if device.hub_id != space.hub_id:
                continue
            if device.malfunctions > 0:
                issues.append(f"{device.name}: {self._issue_label('malfunction')}")
            if device.battery and device.battery.is_low:
                issues.append(f"{device.name}: {self._issue_label('low_battery')}")
            if device.statuses.get("door_opened"):
                issues.append(f"{device.name}: {self._issue_label('open')}")
            if device.statuses.get("tamper"):
                issues.append(f"{device.name}: {self._issue_label('tamper')}")
        return "; ".join(issues[:5]) if issues else ""

    def _translate_error(self, error_type: str) -> str:
        """Translate an arm error type to the current HA language."""
        lang = self.hass.config.language if self.hass else "en"
        translations = _ARM_ERRORS.get(error_type, {})
        return translations.get(lang, translations.get("en", error_type))

    def _arm_error(self, err: Exception) -> HomeAssistantError:
        """Build a descriptive error, enriching malfunction errors with device details."""
        error_type = str(err)
        msg = self._translate_error(error_type)
        if "malfunction" in error_type:
            details = self._describe_blocking_issues()
            if details:
                msg = f"{msg} — {details}"
        return HomeAssistantError(msg)


class AjaxAlarmControlPanel(_AjaxAlarmPanelBase):
    _attr_name = None
    # ARM_HOME is advertised mainly for the Nabu Casa / Alexa skill, which
    # otherwise won't discover a panel exposing ARM_AWAY|ARM_NIGHT (#221).
    # Ajax has a single partial mode ("Night mode"), so ARM_HOME and ARM_NIGHT
    # both drive it — both settle to `armed_night`. See README.
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_NIGHT
        | AlarmControlPanelEntityFeature.ARM_HOME
    )

    def __init__(self, coordinator: AjaxCobrandedCoordinator, space_id: str) -> None:
        super().__init__(coordinator, space_id)
        self._attr_unique_id = f"aegis_ajax_alarm_{self._account_entry_id}_{space_id}"

    @property
    def available(self) -> bool:
        space = self._space
        return space is not None and space.is_online

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        space = self._space
        if space is None:
            return None
        # Intrusion alarm overlay (#426): the served security_state has no
        # alarm value, so `triggered` is derived from the intrusion push and
        # shown over any armed state. The disarmed guard is what makes a
        # disarm reflect instantly — including our own optimistic write —
        # even before the coordinator drops the overlay entry.
        if space.id in self.coordinator.alarmed_space_ids and space.security_state not in (
            SecurityState.DISARMED,
            SecurityState.NONE,
        ):
            return AlarmControlPanelState.TRIGGERED
        # Exit / entry delay overlay (#454, opt-in): the hub reports ARMED the
        # instant it arms and runs the per-detector delays internally, so
        # `arming` / `pending` are derived from its HTS delay events. Same
        # disarmed guard as above, for the same reason.
        if space.id in self.coordinator.delay_overlays and space.security_state not in (
            SecurityState.DISARMED,
            SecurityState.NONE,
        ):
            overlay = self.coordinator.delay_overlays[space.id]
            if overlay.kind is DelayKind.PENDING:
                return AlarmControlPanelState.PENDING
            return AlarmControlPanelState.ARMING
        return self._hub_state()

    def _hub_state(self) -> AlarmControlPanelState | None:
        """The panel state as the hub itself reports it — no overlays."""
        space = self._space
        if space is None:
            return None
        # In group mode the server reports PARTIALLY_ARMED while night mode
        # is active — identical to "some groups armed" in the lite state.
        # `night_mode_enabled` (snapshot + push events) disambiguates (#284).
        if space.security_state == SecurityState.PARTIALLY_ARMED and space.night_mode_enabled:
            return AlarmControlPanelState.ARMED_NIGHT
        return map_security_state(space.security_state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        space = self._space
        if space is None:
            return {}
        attrs: dict[str, Any] = {
            "hub_id": space.hub_id,
            "malfunctions": space.malfunctions_count,
            "connection_status": space.connection_status.name,
        }
        # With the delay overlay on (#454) the raw hub state stays readable,
        # along with the space's exit delay and the running delay's end.
        if self.coordinator.delay_panel_states is True:
            hub_state = self._hub_state()
            overlay = self.coordinator.delay_overlays.get(space.id)
            attrs["hub_state"] = hub_state.value if hub_state is not None else None
            attrs["exit_delay_seconds"] = self.coordinator.space_exit_delay_seconds(space.id)
            attrs["delay_ends_at"] = overlay.ends_at.isoformat() if overlay is not None else None
        return attrs

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        self._validate_code(code)
        from custom_components.aegis_ajax.api.security import SecurityError  # noqa: PLC0415

        try:
            await self.coordinator.security_api.arm(self._space_id, ignore_alarms=self._force_arm)
        except SecurityError as err:
            raise self._arm_error(err) from err
        self._optimistic_state_update(SecurityState.ARMED)
        await self.coordinator.async_request_refresh()

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        self._validate_code(code)
        from custom_components.aegis_ajax.api.security import SecurityError  # noqa: PLC0415

        try:
            await self.coordinator.security_api.arm_night_mode(
                self._space_id, ignore_alarms=self._force_arm
            )
        except SecurityError as err:
            raise self._arm_error(err) from err
        self._optimistic_state_update(SecurityState.NIGHT_MODE)
        await self.coordinator.async_request_refresh()

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm home — Ajax has no native home/stay mode, so this maps to its
        single partial mode (night). Same effect as `arm_night`; the state
        settles to `armed_night`. Exposed mainly so the Alexa skill discovers
        the panel and can arm/disarm by voice (#221)."""
        await self.async_alarm_arm_night(code)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        self._validate_code(code)
        from custom_components.aegis_ajax.api.security import SecurityError  # noqa: PLC0415

        try:
            await self.coordinator.security_api.disarm(self._space_id)
        except SecurityError as err:
            raise HomeAssistantError(self._translate_error(str(err))) from err
        self._optimistic_state_update(SecurityState.DISARMED)
        await self.coordinator.async_request_refresh()

    def _optimistic_state_update(self, new_state: SecurityState) -> None:
        """Update the space state optimistically so the UI reflects the change immediately.

        Avoids flicker when the server or stream returns stale state briefly
        after a successful arm/disarm command. The optimistic state is held
        for 10 seconds so subsequent stale polls don't overwrite it.
        """
        import asyncio  # noqa: PLC0415
        from dataclasses import replace  # noqa: PLC0415

        space = self._space
        if space is None:
            return
        # Mirror the push-event rule (#284): arming night sets the flag,
        # full arm / disarm clears it, so the debounced lite re-read
        # (PARTIALLY_ARMED in group mode) still maps to armed_night.
        night_mode_enabled = space.night_mode_enabled
        if new_state == SecurityState.NIGHT_MODE:
            night_mode_enabled = True
        elif new_state in (SecurityState.ARMED, SecurityState.DISARMED):
            night_mode_enabled = False
        try:
            self.coordinator.spaces[self._space_id] = replace(
                space, security_state=new_state, night_mode_enabled=night_mode_enabled
            )
        except TypeError:
            return  # space is not a real dataclass (e.g., during tests)
        # The user's own disarm is the clearest acknowledgment an intrusion
        # alarm can get (#426) — drop the overlay here rather than waiting for
        # a poll to observe the disarm, which the 10 s optimistic window can
        # outlast when the server briefly serves stale state.
        if new_state == SecurityState.DISARMED:
            self.coordinator.alarmed_space_ids.discard(self._space_id)
        # Our own arm is the earliest an exit delay can be seen starting (#454).
        self.coordinator.sync_delay_overlays()
        expiry = asyncio.get_running_loop().time() + 10
        self.coordinator._optimistic_space_states[self._space_id] = (expiry, new_state)
        if self.hass is not None:
            self.async_write_ha_state()
            # Other Aegis entries may authenticate the same Ajax Space. Their
            # panels must reflect this confirmed command immediately too.
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                other = getattr(entry, "runtime_data", None)
                if other is None or other is self.coordinator:
                    continue
                other_space = other.spaces.get(self._space_id)
                if other_space is None:
                    continue
                other.spaces[self._space_id] = replace(
                    other_space,
                    security_state=new_state,
                    night_mode_enabled=night_mode_enabled,
                )
                other._optimistic_space_states[self._space_id] = (expiry, new_state)
                other.async_set_updated_data({"spaces": other.spaces, "devices": other.devices})


class AjaxGroupAlarmControlPanel(_AjaxAlarmPanelBase):
    """Alarm control panel for a single Ajax security group.

    Created per `Group` when the parent space is in Group/Zone mode. Calls
    `SpaceSecurityService/armGroup` and `disarmGroup` so each panel can be
    armed/disarmed independently. Night mode is intentionally not exposed
    on per-group panels — the underlying flag is space-wide on Ajax, so
    surfacing it per group would mislead users about scope.
    """

    # Groups have a single arm mode (full group arm); ARM_HOME is advertised
    # so the Alexa skill discovers the per-group panel too, mapping to the same
    # group-arm command as ARM_AWAY (#221).
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY | AlarmControlPanelEntityFeature.ARM_HOME
    )

    def __init__(self, coordinator: AjaxCobrandedCoordinator, space_id: str, group_id: str) -> None:
        super().__init__(coordinator, space_id)
        self._group_id = group_id
        self._attr_unique_id = (
            f"aegis_ajax_alarm_{self._account_entry_id}_{space_id}_group_{group_id}"
        )
        space = coordinator.spaces.get(space_id)
        group = space.get_group(group_id) if space else None
        self._attr_name = group.name if group else f"Group {group_id}"
        self._attr_suggested_object_id = (
            f"ajax_{self._account_label}_{slugify(str(group.name if group else group_id))}"
        )

    @property
    def _group(self) -> Group | None:
        space = self._space
        return space.get_group(self._group_id) if space else None

    @property
    def available(self) -> bool:
        space = self._space
        return space is not None and space.is_online and self._group is not None

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        group = self._group
        if group is None:
            return None
        return map_security_state(group.security_state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        space = self._space
        group = self._group
        if space is None or group is None:
            return {}
        members = sorted(
            (d for d in self.coordinator.devices.values() if d.group_id == group.id),
            key=lambda d: d.name,
        )
        return {
            "hub_id": space.hub_id,
            "space_id": space.id,
            "group_id": group.id,
            "group_name": group.name,
            "connection_status": space.connection_status.name,
            # Which devices are in this group (#366). Ajax group membership
            # was readable only from the mobile app, so a finding about a
            # device's group — such as #348's siren activity counter — could
            # not be reproduced from Home Assistant alone. Rooms do not answer
            # it: a device has a room and a group independently.
            #
            # It lives here, on the one entity that already exists per group,
            # rather than as an attribute on every device's entities: there is
            # no shared entity base to hang that on today, and adding one is
            # #332's job. Template the inverse when you need it per device.
            "member_device_ids": [d.id for d in members],
            "member_device_names": [d.name for d in members],
        }

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        self._validate_code(code)
        from custom_components.aegis_ajax.api.security import SecurityError  # noqa: PLC0415

        try:
            await self.coordinator.security_api.arm_group(
                self._space_id, self._group_id, ignore_alarms=self._force_arm
            )
        except SecurityError as err:
            raise self._arm_error(err) from err
        self._optimistic_state_update(SecurityState.ARMED)
        await self.coordinator.async_request_refresh()

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm home — groups have a single arm mode, so this maps to the same
        group-arm command as arm_away (#221)."""
        await self.async_alarm_arm_away(code)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        self._validate_code(code)
        from custom_components.aegis_ajax.api.security import SecurityError  # noqa: PLC0415

        try:
            await self.coordinator.security_api.disarm_group(self._space_id, self._group_id)
        except SecurityError as err:
            raise HomeAssistantError(self._translate_error(str(err))) from err
        self._optimistic_state_update(SecurityState.DISARMED)
        await self.coordinator.async_request_refresh()

    def _optimistic_state_update(self, new_state: SecurityState) -> None:
        from dataclasses import replace  # noqa: PLC0415

        space = self._space
        group = self._group
        if space is None or group is None:
            return
        try:
            new_group = replace(group, security_state=new_state)
            new_groups = tuple(new_group if g.id == self._group_id else g for g in space.groups)
            self.coordinator.spaces[self._space_id] = replace(space, groups=new_groups)
        except TypeError:
            return  # not a real dataclass (e.g. during tests)
        if self.hass is not None:
            self.async_write_ha_state()
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                other = getattr(entry, "runtime_data", None)
                if other is None or other is self.coordinator:
                    continue
                other_space = other.spaces.get(self._space_id)
                if other_space is None:
                    continue
                other_group = other_space.get_group(self._group_id)
                if other_group is None:
                    continue
                other_groups = tuple(
                    replace(g, security_state=new_state) if g.id == self._group_id else g
                    for g in other_space.groups
                )
                other.spaces[self._space_id] = replace(other_space, groups=other_groups)
                other.async_set_updated_data({"spaces": other.spaces, "devices": other.devices})
