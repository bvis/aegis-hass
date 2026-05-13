"""HA Repairs integration — surface diagnosable problems in the UI.

Each helper wraps `homeassistant.helpers.issue_registry` with the
domain pre-bound and a stable issue id per category, so callers don't
have to repeat boilerplate.

The FCM-credentials repair is fixable: the user clicks "Submit" on
the Repair card, fills the four FCM fields in a dedicated form, and
the integration reloads with the new credentials — no Options-menu
detour. Hub-offline / HTS-chronic stay informational because their
fix is physical (check hub power, firewall, etc.).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from custom_components.aegis_ajax.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResult

DOCS_BASE_URL = "https://github.com/bvis/aegis-hass#"

ISSUE_HUB_OFFLINE_24H = "hub_offline_24h"
ISSUE_HTS_CHRONIC_FAILURE = "hts_chronic_failure"
ISSUE_FCM_CREDENTIALS_INVALID = "fcm_credentials_invalid"
ISSUE_FCM_NOT_CONFIGURED = "fcm_not_configured"
ISSUE_GRPCIO_VERSION_MISMATCH = "grpcio_version_mismatch"

# Floor below which the integration's gRPC calls have historically failed
# in ways that surface as cryptic stack traces (HTTP/2 framing errors,
# UNKNOWN status, etc) rather than clean errors. Matches the manifest's
# `grpcio>=1.60.0` requirement; bump in lockstep with manifest.json.
MIN_GRPCIO_VERSION = "1.60.0"


def _issue_id(prefix: str, scope: str | None) -> str:
    """Build a stable per-scope issue id (e.g. one per space)."""
    return f"{prefix}:{scope}" if scope else prefix


def async_register_hub_offline(
    hass: HomeAssistant,
    *,
    space_id: str,
    hub_name: str,
    hours_offline: int,
) -> None:
    """Hub has been reported offline for >= 24h on a healthy snapshot poll."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(ISSUE_HUB_OFFLINE_24H, space_id),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_HUB_OFFLINE_24H,
        translation_placeholders={
            "hub_name": hub_name,
            "hours_offline": str(hours_offline),
        },
        learn_more_url=f"{DOCS_BASE_URL}troubleshooting",
    )


def async_clear_hub_offline(hass: HomeAssistant, *, space_id: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, _issue_id(ISSUE_HUB_OFFLINE_24H, space_id))


def async_register_hts_chronic_failure(
    hass: HomeAssistant,
    *,
    space_id: str,
    minutes_failing: int,
) -> None:
    """HTS reconnect has failed for an extended window."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(ISSUE_HTS_CHRONIC_FAILURE, space_id),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_HTS_CHRONIC_FAILURE,
        translation_placeholders={
            "space_id": space_id,
            "minutes_failing": str(minutes_failing),
        },
        learn_more_url=f"{DOCS_BASE_URL}hub-network",
    )


def async_clear_hts_chronic_failure(hass: HomeAssistant, *, space_id: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, _issue_id(ISSUE_HTS_CHRONIC_FAILURE, space_id))


def async_register_fcm_credentials_invalid(hass: HomeAssistant, *, entry_id: str) -> None:
    """FCM credentials were configured but registration / push start failed.

    Scoped per entry_id so multi-account installs see one repair per
    affected account. Fixable in-place via `FcmCredentialsRepairFlow`
    so users don't have to detour through the Options menu.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(ISSUE_FCM_CREDENTIALS_INVALID, entry_id),
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_FCM_CREDENTIALS_INVALID,
        # Fix-flow needs the entry id to write the new creds back; the
        # issue_id already encodes it but `data` is the canonical channel.
        data={"entry_id": entry_id},
        learn_more_url=f"{DOCS_BASE_URL}push-notifications-fcm",
    )


def async_clear_fcm_credentials_invalid(hass: HomeAssistant, *, entry_id: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, _issue_id(ISSUE_FCM_CREDENTIALS_INVALID, entry_id))


def async_register_fcm_not_configured(hass: HomeAssistant, *, entry_id: str) -> None:
    """FCM credentials are not set on this entry, so real-time pushes are off.

    Distinct from `fcm_credentials_invalid` (which means keys were tried and
    rejected): this fires when the four FCM fields are empty. Real-time
    events (doorbell ring, arm/disarm pushes, alarm) won't reach HA until
    keys are entered. Fixable in-place via `FcmCredentialsRepairFlow`,
    same form as the invalid-credentials repair.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(ISSUE_FCM_NOT_CONFIGURED, entry_id),
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_FCM_NOT_CONFIGURED,
        data={"entry_id": entry_id},
        learn_more_url=f"{DOCS_BASE_URL}push-notifications-fcm",
    )


def async_clear_fcm_not_configured(hass: HomeAssistant, *, entry_id: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, _issue_id(ISSUE_FCM_NOT_CONFIGURED, entry_id))


def _parse_version(value: str) -> tuple[int, ...]:
    """Parse '1.60.0' / '1.60.0rc1' / etc into a comparable int tuple.

    Strips PEP-440 pre/post/dev suffixes by keeping only leading digits per
    component. Bad shapes ('foo', '') return `(0,)` so the caller treats them
    as definitely-too-old and surfaces the repair (better loud than silent).
    """
    parts: list[int] = []
    for raw in value.split("."):
        digits = ""
        for ch in raw:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else (0,)


def async_check_grpcio_version(hass: HomeAssistant) -> None:
    """Surface a Repair when the installed grpcio is below `MIN_GRPCIO_VERSION`.

    Mostly hits HA OS users — Core / Container installs honour the
    manifest's `grpcio>=1.60.0` requirement at install time; HA OS ships
    grpcio system-wide and ignores per-integration manifest pins. When
    that mismatch caused real-user failures (#26) the symptom was a
    stack trace at first login. With this check the user gets a
    one-line Repair card naming the version they need to install.

    Idempotent and self-clearing: a later HA upgrade calls back through
    here and the Repair vanishes once `grpc.__version__` crosses the
    floor.
    """
    try:
        import grpc  # noqa: PLC0415
    except ImportError:
        # Manifest forces grpcio to be installed; if the import itself
        # fails the user has bigger problems and HA core will surface
        # them. Don't shadow with our own repair.
        return
    current = getattr(grpc, "__version__", "0")
    if _parse_version(current) >= _parse_version(MIN_GRPCIO_VERSION):
        ir.async_delete_issue(hass, DOMAIN, ISSUE_GRPCIO_VERSION_MISMATCH)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_GRPCIO_VERSION_MISMATCH,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_GRPCIO_VERSION_MISMATCH,
        translation_placeholders={
            "current": current,
            "required": MIN_GRPCIO_VERSION,
        },
        learn_more_url=f"{DOCS_BASE_URL}troubleshooting",
    )


_FCM_FIX_SCHEMA = vol.Schema(
    {
        vol.Required("fcm_project_id"): str,
        vol.Required("fcm_app_id"): str,
        vol.Required("fcm_api_key"): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required("fcm_sender_id"): str,
    }
)


class FcmCredentialsRepairFlow(RepairsFlow):
    """Guided repair flow for `fcm_credentials_invalid`.

    Re-prompts the four FCM fields, writes them to the entry data, and
    reloads — which kicks `notification.async_start()` and either
    re-raises the repair (creds still wrong) or clears it (creds work
    now).
    """

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry is None:
                # The entry vanished between the repair being raised and
                # the user clicking Submit. Nothing to fix.
                return self.async_abort(reason="entry_missing")
            self.hass.config_entries.async_update_entry(entry, data={**entry.data, **user_input})
            await self.hass.config_entries.async_reload(self._entry_id)
            return self.async_create_entry(data={})

        # Pre-fill with the currently-stored values so the user only
        # changes what's wrong, instead of re-typing everything.
        suggested: dict[str, Any] = {}
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is not None:
            for key in ("fcm_project_id", "fcm_app_id", "fcm_api_key", "fcm_sender_id"):
                value = entry.data.get(key, entry.options.get(key, ""))
                if value:
                    suggested[key] = value
        schema = self.add_suggested_values_to_schema(_FCM_FIX_SCHEMA, suggested)
        return self.async_show_form(step_id="init", data_schema=schema)


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """HA discovery hook — returns the right flow for each fixable issue."""
    for prefix in (ISSUE_FCM_CREDENTIALS_INVALID, ISSUE_FCM_NOT_CONFIGURED):
        if issue_id.startswith(f"{prefix}:"):
            entry_id = (data or {}).get("entry_id") or issue_id.split(":", 1)[1]
            return FcmCredentialsRepairFlow(str(entry_id))
    # Fall back: HA's built-in confirm-only flow. Should not be hit since
    # we only mark FCM as fixable today.
    from homeassistant.components.repairs import ConfirmRepairFlow  # noqa: PLC0415

    return ConfirmRepairFlow()
