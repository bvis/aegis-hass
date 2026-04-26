"""Alarm control panel for Ajax Security."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.alarm_control_panel import (  # type: ignore[attr-defined]
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.aegis_ajax.const import CONF_FORCE_ARM, DOMAIN, MANUFACTURER, SecurityState
from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator
from custom_components.aegis_ajax.entity import build_device_info

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.aegis_ajax.api.models import Space

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
    coordinator: AjaxCobrandedCoordinator = entry.runtime_data
    entities = [
        AjaxAlarmControlPanel(coordinator=coordinator, space_id=space_id)
        for space_id in coordinator.spaces
    ]
    async_add_entities(entities)


class AjaxAlarmControlPanel(CoordinatorEntity[AjaxCobrandedCoordinator], AlarmControlPanelEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY | AlarmControlPanelEntityFeature.ARM_NIGHT
    )

    def __init__(self, coordinator: AjaxCobrandedCoordinator, space_id: str) -> None:
        super().__init__(coordinator)
        self._space_id = space_id
        self._attr_unique_id = f"aegis_ajax_alarm_{space_id}"
        space = coordinator.spaces.get(space_id)
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

    def _get_options(self) -> dict[str, Any]:
        """Return config entry options, or empty dict if entry is unavailable."""
        entry = self.coordinator.config_entry
        if entry is None:
            return {}
        return dict(entry.options)

    @property
    def code_arm_required(self) -> bool:
        return bool(self._get_options().get("use_pin_code", False))

    def _validate_code(self, code: str | None) -> None:
        """Raise HomeAssistantError if the provided code does not match the stored hash."""
        if not self.code_arm_required:
            return
        stored_hash = self._get_options().get("pin_code_hash", "")
        computed = hashlib.sha256(code.encode()).hexdigest() if code else ""
        if not code or not hmac.compare_digest(computed, stored_hash):
            raise HomeAssistantError(self._translate_error("invalid_alarm_code"))

    @property
    def _space(self) -> Space | None:
        return self.coordinator.spaces.get(self._space_id)

    @property
    def available(self) -> bool:
        space = self._space
        return space is not None and space.is_online

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        space = self._space
        if space is None:
            return None
        return map_security_state(space.security_state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        space = self._space
        if space is None:
            return {}
        return {
            "hub_id": space.hub_id,
            "malfunctions": space.malfunctions_count,
            "connection_status": space.connection_status.name,
        }

    @property
    def _force_arm(self) -> bool:
        """Return True when the user opted to always ignore malfunctions."""
        return bool(self._get_options().get(CONF_FORCE_ARM, False))

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

        This avoids flicker when the server or stream returns stale state briefly
        after a successful arm/disarm command. The optimistic state is preserved
        for 10 seconds to survive async_refresh calls that may return stale data.
        """
        import asyncio  # noqa: PLC0415
        from dataclasses import replace  # noqa: PLC0415

        space = self._space
        if space is None:
            return
        try:
            self.coordinator.spaces[self._space_id] = replace(space, security_state=new_state)
        except TypeError:
            return  # space is not a real dataclass (e.g., during tests)
        # Protect the optimistic state from being overwritten by stale server data
        expiry = asyncio.get_running_loop().time() + 10
        self.coordinator._optimistic_space_states[self._space_id] = (expiry, new_state)
        # Notify HA of the state change (may not have hass during tests)
        if self.hass is not None:
            self.async_write_ha_state()
