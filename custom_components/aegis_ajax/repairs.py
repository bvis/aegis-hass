"""HA Repairs integration — surface diagnosable problems in the UI.

Each helper wraps `homeassistant.helpers.issue_registry` with the
domain pre-bound and a stable issue id per category, so callers don't
have to repeat boilerplate. All issues are non-fixable in this initial
slice — the description tells the user what to do (re-enter FCM
credentials via Options, check hub power, etc). A future pass can
turn the FCM and app-label cases into guided RepairsFlow with
`is_fixable=True`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import issue_registry as ir

from custom_components.aegis_ajax.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

DOCS_BASE_URL = "https://github.com/bvis/aegis-hass#"

ISSUE_HUB_OFFLINE_24H = "hub_offline_24h"
ISSUE_HTS_CHRONIC_FAILURE = "hts_chronic_failure"
ISSUE_FCM_CREDENTIALS_INVALID = "fcm_credentials_invalid"


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
    affected account.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(ISSUE_FCM_CREDENTIALS_INVALID, entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_FCM_CREDENTIALS_INVALID,
        learn_more_url=f"{DOCS_BASE_URL}push-notifications-fcm",
    )


def async_clear_fcm_credentials_invalid(hass: HomeAssistant, *, entry_id: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, _issue_id(ISSUE_FCM_CREDENTIALS_INVALID, entry_id))
