"""Focused tests for commands-only Ajax accounts."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

import pytest
from homeassistant.components.alarm_control_panel import AlarmControlPanelEntityFeature
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aegis_ajax.alarm_control_panel import AjaxAlarmControlPanel
from custom_components.aegis_ajax.api.models import Space
from custom_components.aegis_ajax.const import (
    ACCOUNT_ROLE_COMMANDS_ONLY,
    CONF_ACCOUNT_LABEL,
    CONF_ACCOUNT_ROLE,
    CONF_PRIMARY_ENTRY_ID,
    DOMAIN,
    ConnectionStatus,
    SecurityState,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture(autouse=True)
def setup_entry() -> None:
    """Do not start a real integration while exercising the config flow."""
    with patch("custom_components.aegis_ajax.async_setup_entry", return_value=True):
        yield


def _client() -> MagicMock:
    client = MagicMock()
    client.connect = AsyncMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.session.session_token = "tok"
    client.session.user_hex_id = "hex"
    client.session.device_id = "device"
    return client


@pytest.mark.asyncio
async def test_commands_only_flow_requires_primary_and_persists_label(hass: HomeAssistant) -> None:
    primary = MockConfigEntry(
        domain=DOMAIN,
        title="Ajax Security (main@example.com)",
        unique_id="main@example.com",
        data={"email": "main@example.com", "spaces": ["space-1"]},
    )
    primary.add_to_hass(hass)
    client = _client()
    space = MagicMock()
    space.id = "space-1"
    space.name = "Home"
    with (
        patch("custom_components.aegis_ajax.config_flow.AjaxGrpcClient", return_value=client),
        patch("custom_components.aegis_ajax.config_flow.SpacesApi") as spaces_api,
    ):
        spaces_api.return_value.list_spaces = AsyncMock(return_value=[space])
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "email": "commands@example.com",
                "password": "secret",
                CONF_ACCOUNT_ROLE: ACCOUNT_ROLE_COMMANDS_ONLY,
                CONF_PRIMARY_ENTRY_ID: primary.entry_id,
                CONF_ACCOUNT_LABEL: "Night shift",
            },
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "select_spaces"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"spaces": ["space-1"]}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ACCOUNT_ROLE] == ACCOUNT_ROLE_COMMANDS_ONLY
    assert result["data"][CONF_PRIMARY_ENTRY_ID] == primary.entry_id
    assert result["data"][CONF_ACCOUNT_LABEL] == "Night shift"
    assert result["data"]["spaces"] == ["space-1"]


@pytest.mark.asyncio
async def test_commands_only_flow_rejects_missing_primary(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "email": "commands@example.com",
            "password": "secret",
            CONF_ACCOUNT_ROLE: ACCOUNT_ROLE_COMMANDS_ONLY,
            CONF_ACCOUNT_LABEL: "Night shift",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "primary_account_required"


def test_commands_only_panel_is_labeled_and_arm_disarm_only() -> None:
    space = Space(
        id="space-1",
        hub_id="hub-1",
        name="Home",
        security_state=SecurityState.DISARMED,
        connection_status=ConnectionStatus.ONLINE,
        malfunctions_count=0,
    )
    primary = MagicMock()
    primary.spaces = {"space-1": space}
    primary.devices = {}
    primary.rooms = {}
    primary.alarmed_space_ids = set()
    primary.delay_overlays = {}
    primary.delay_panel_states = False
    primary._optimistic_space_states = {}
    primary.sync_delay_overlays = MagicMock()
    primary.async_set_updated_data = MagicMock()

    secondary = MagicMock()
    secondary.commands_only = True
    secondary.entry_id = "secondary-entry"
    secondary.account_label = "Night shift"
    secondary.spaces = primary.spaces
    secondary.devices = primary.devices
    secondary.rooms = primary.rooms
    secondary._primary_coordinator = primary
    secondary.primary_loaded = True
    secondary.config_entry.options = {}
    secondary.security_api.arm = AsyncMock()
    secondary.async_request_refresh = AsyncMock()

    panel = AjaxAlarmControlPanel(secondary, "space-1")

    assert panel.unique_id == "aegis_ajax_alarm_secondary-entry_space-1"
    assert panel.name == "Night shift"
    assert panel.supported_features == AlarmControlPanelEntityFeature.ARM_AWAY

