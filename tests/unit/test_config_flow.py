"""Tests for the config, reauth, reconfigure and options flows.

Everything goes through Home Assistant's flow manager, as a user would.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import grpc
import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_DHCP, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import SelectSelector, SelectSelectorMode
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aegis_ajax.api.session import AuthenticationError, TwoFactorRequiredError
from custom_components.aegis_ajax.config_flow import AjaxCobrandedConfigFlow
from custom_components.aegis_ajax.const import CONF_DELAY_PANEL_STATES, DOMAIN
from custom_components.aegis_ajax.service_security import validate_pin

if TYPE_CHECKING:
    from collections.abc import Iterator

    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

FLOW = "custom_components.aegis_ajax.config_flow"
FCM_KEYS = ("fcm_project_id", "fcm_app_id", "fcm_api_key", "fcm_sender_id")


@pytest.fixture(autouse=True)
def setup_entry() -> Iterator[AsyncMock]:
    """Entries created or reloaded by a flow don't reach Ajax."""
    with patch("custom_components.aegis_ajax.async_setup_entry", return_value=True) as mock:
        yield mock


def _client() -> MagicMock:
    """A client whose login works; tests override what they need."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.login = AsyncMock()
    client.login_totp = AsyncMock()
    client.close = AsyncMock()
    client.session.session_token = "tok"
    client.session.user_hex_id = "hex"
    client.session.device_id = "dev-uuid"
    return client


def _space(space_id: str, name: str) -> MagicMock:
    space = MagicMock()
    space.id = space_id
    space.name = name
    return space


@pytest.fixture
def client() -> Iterator[MagicMock]:
    mock = _client()
    with patch(f"{FLOW}.AjaxGrpcClient", return_value=mock) as cls:
        mock.cls = cls
        yield mock


@pytest.fixture
def spaces_api() -> Iterator[MagicMock]:
    api = MagicMock()
    api.list_spaces = AsyncMock(return_value=[_space("space-1", "Home")])
    with patch(f"{FLOW}.SpacesApi", return_value=api):
        yield api


async def _start(hass: HomeAssistant) -> dict[str, Any]:
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def _submit_user(
    hass: HomeAssistant, email: str = "a@b.com", password: str = "pass"
) -> dict[str, Any]:
    result = await _start(hass)
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": email, "password": password}
    )


def _entry(
    hass: HomeAssistant,
    data: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    data = data or {"email": "user@example.com", "app_label": "Ajax"}
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Ajax Security ({data.get('email', '')})",
        unique_id=data.get("email"),
        data=data,
        version=2,
        minor_version=2,
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


class TestConfigFlowInit:
    async def test_domain(self, hass: HomeAssistant) -> None:
        assert AjaxCobrandedConfigFlow.DOMAIN == DOMAIN
        result = await _start(hass)
        assert result["handler"] == DOMAIN

    async def test_has_user_step(self, hass: HomeAssistant) -> None:
        result = await _start(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

    async def test_has_2fa_step(self, hass: HomeAssistant, client: MagicMock) -> None:
        client.login.side_effect = TwoFactorRequiredError("req-1")
        result = await _submit_user(hass)
        assert result["step_id"] == "2fa"

    async def test_has_select_spaces_step(
        self, hass: HomeAssistant, client: MagicMock, spaces_api: MagicMock
    ) -> None:
        result = await _submit_user(hass)
        assert result["step_id"] == "select_spaces"

    async def test_version(
        self, hass: HomeAssistant, client: MagicMock, spaces_api: MagicMock
    ) -> None:
        result = await _submit_user(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"spaces": ["space-1"]}
        )
        assert result["result"].version == 2
        assert result["result"].minor_version == 2


class TestAsyncStepDhcp:
    @staticmethod
    def _discovery(hostname: str = "") -> DhcpServiceInfo:
        return DhcpServiceInfo(ip="192.168.1.42", hostname=hostname, macaddress="9c756e1b60c4")

    async def _discover(self, hass: HomeAssistant, hostname: str = "") -> dict[str, Any]:
        return await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_DHCP}, data=self._discovery(hostname)
        )

    async def test_first_discovery_forwards_into_user_step(self, hass: HomeAssistant) -> None:
        result = await self._discover(hass, "ajax-hub-01")

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        (flow,) = hass.config_entries.flow.async_progress()
        assert flow["context"]["title_placeholders"] == {"name": "ajax-hub-01"}
        # Per-MAC unique_id deduplicates repeat DHCP packets
        assert flow["context"]["unique_id"] == "9c:75:6e:1b:60:c4"
        repeat = await self._discover(hass, "ajax-hub-01")
        assert repeat["type"] is FlowResultType.ABORT
        assert repeat["reason"] == "already_in_progress"

    async def test_no_hostname_falls_back_to_ip_in_title(self, hass: HomeAssistant) -> None:
        await self._discover(hass)

        (flow,) = hass.config_entries.flow.async_progress()
        assert flow["context"]["title_placeholders"] == {"name": "Ajax hub (192.168.1.42)"}

    async def test_existing_entry_aborts_already_configured(self, hass: HomeAssistant) -> None:
        """A user with at least one Aegis account configured doesn't get nagged."""
        _entry(hass)

        result = await self._discover(hass)

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"


class TestAsyncStepUser:
    async def test_step_user_no_input_shows_form(self, hass: HomeAssistant) -> None:
        result = await _start(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {}

    async def test_step_user_invalid_auth(self, hass: HomeAssistant, client: MagicMock) -> None:
        client.connect.side_effect = AuthenticationError("invalid")
        result = await _submit_user(hass, password="bad")
        assert result["step_id"] == "user"
        assert result["errors"]["base"] == "invalid_auth"

    async def test_step_user_cannot_connect(self, hass: HomeAssistant, client: MagicMock) -> None:
        client.connect.side_effect = ConnectionError("refused")
        result = await _submit_user(hass)
        assert result["errors"]["base"] == "cannot_connect"

    async def test_step_user_login_timeout(self, hass: HomeAssistant, client: MagicMock) -> None:
        client.login.side_effect = TimeoutError()
        result = await _submit_user(hass)
        assert result["errors"]["base"] == "cannot_connect"
        client.close.assert_awaited_once()

    async def test_step_user_unknown_error(self, hass: HomeAssistant, client: MagicMock) -> None:
        client.connect.side_effect = RuntimeError("unexpected")
        result = await _submit_user(hass)
        assert result["errors"]["base"] == "unknown"

    async def test_step_user_client_construction_error(self, hass: HomeAssistant) -> None:
        """A client that can't even be built is an unknown error, with nothing to close."""
        with patch(f"{FLOW}.AjaxGrpcClient", side_effect=ValueError("bad label")):
            result = await _submit_user(hass)
        assert result["errors"]["base"] == "unknown"

    async def test_step_user_blacklisted_client_says_update(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        """Ajax refusing our client version must not read as "unexpected error" (#563)."""
        client.login.side_effect = grpc.aio.AioRpcError(  # type: ignore[call-arg]
            code=grpc.StatusCode.PERMISSION_DENIED,
            initial_metadata=grpc.aio.Metadata(),
            trailing_metadata=grpc.aio.Metadata(),
            details="Request is blacklisted",
        )
        result = await _submit_user(hass)
        assert result["errors"]["base"] == "client_rejected"

    async def test_step_user_closes_client_on_login_failure(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        """A failed login must close the channel; each retry creates a new client."""
        client.login.side_effect = AuthenticationError("invalid")
        result = await _submit_user(hass, password="bad")
        assert result["errors"]["base"] == "invalid_auth"
        client.close.assert_awaited_once()

    async def test_step_user_reraises_cancelled_error(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        """CancelledError must propagate so HA shutdown/reload cancels cleanly."""
        client.login.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await _submit_user(hass)
        client.close.assert_awaited_once()

    async def test_step_user_keeps_client_open_for_2fa(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        """The 2FA path reuses the same client, so it must NOT be closed."""
        client.login.side_effect = TwoFactorRequiredError("req-123")
        result = await _submit_user(hass)
        assert result["step_id"] == "2fa"
        client.close.assert_not_awaited()
        await hass.config_entries.flow.async_configure(result["flow_id"], {"totp_code": "1"})
        assert client.login_totp.call_args.kwargs["request_id"] == "req-123"

    async def test_step_user_2fa_required(self, hass: HomeAssistant, client: MagicMock) -> None:
        client.connect.side_effect = TwoFactorRequiredError("req-123")
        result = await _submit_user(hass)
        # Should have shown 2fa form, bound to the request Ajax issued
        assert result["step_id"] == "2fa"
        await hass.config_entries.flow.async_configure(result["flow_id"], {"totp_code": "1"})
        assert client.login_totp.call_args.kwargs["request_id"] == "req-123"

    async def test_step_user_stores_password_hash_not_plaintext(
        self, hass: HomeAssistant, client: MagicMock, spaces_api: MagicMock
    ) -> None:
        """Ensure plaintext password is never stored; only the hash is kept."""
        client.connect.side_effect = [ConnectionError("refused"), None]
        result = await _submit_user(hass, password="mypassword")
        expected_hash = hashlib.sha256(b"mypassword").hexdigest()
        assert client.cls.call_args.kwargs["password_hash"] == expected_hash

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "a@b.com", "password": "mypassword"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"spaces": ["space-1"]}
        )
        data = result["data"]
        assert data["password_hash"] == expected_hash
        assert "mypassword" not in data.values()

    async def test_step_user_aborts_when_account_already_added(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        _entry(hass, {"email": "a@b.com", "app_label": "Ajax"})
        result = await _submit_user(hass)
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"
        client.connect.assert_not_awaited()


class TestAsyncStep2FA:
    @staticmethod
    async def _at_2fa(hass: HomeAssistant, client: MagicMock, request_id: str) -> dict[str, Any]:
        client.login.side_effect = TwoFactorRequiredError(request_id)
        return await _submit_user(hass, email="test@example.com")

    async def test_step_2fa_no_input_shows_form(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        result = await self._at_2fa(hass, client, "req-1")
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "2fa"
        assert result["errors"] == {}

    async def test_step_2fa_invalid_totp(self, hass: HomeAssistant, client: MagicMock) -> None:
        result = await self._at_2fa(hass, client, "req-123")
        client.login_totp.side_effect = AuthenticationError("Invalid TOTP code")

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"totp_code": "000000"}
        )
        assert result["errors"]["base"] == "invalid_totp"
        client.login_totp.assert_called_once_with(
            email="test@example.com",
            request_id="req-123",
            totp_code="000000",
        )

    async def test_step_2fa_success(
        self, hass: HomeAssistant, client: MagicMock, spaces_api: MagicMock
    ) -> None:
        result = await self._at_2fa(hass, client, "req-456")

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"totp_code": "123456"}
        )
        client.login_totp.assert_called_once_with(
            email="test@example.com",
            request_id="req-456",
            totp_code="123456",
        )
        assert result["step_id"] == "select_spaces"

    async def test_step_2fa_timeout(self, hass: HomeAssistant, client: MagicMock) -> None:
        result = await self._at_2fa(hass, client, "req-1")
        client.login_totp.side_effect = TimeoutError()
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"totp_code": "000000"}
        )
        assert result["errors"]["base"] == "cannot_connect"

    async def test_step_2fa_unknown_error(self, hass: HomeAssistant, client: MagicMock) -> None:
        result = await self._at_2fa(hass, client, "req-789")
        client.login_totp.side_effect = RuntimeError("unknown")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"totp_code": "000000"}
        )
        assert result["errors"]["base"] == "unknown"


class TestAsyncStepSelectSpaces:
    async def test_step_select_spaces_no_client_shows_form(self, hass: HomeAssistant) -> None:
        """Guard for a step entered without a client.

        The flow manager can't produce that state (the step only follows a
        login), so this one drives the step directly.
        """
        flow = AjaxCobrandedConfigFlow()
        flow.hass = hass

        result = await flow.async_step_select_spaces(None)

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "select_spaces"

    async def test_step_select_spaces_with_input_creates_entry(
        self, hass: HomeAssistant, client: MagicMock, spaces_api: MagicMock
    ) -> None:
        result = await _submit_user(hass, email="test@example.com", password="secret")

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"spaces": ["space-1"]}
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "Ajax Security (test@example.com)"
        assert result["result"].unique_id == "test@example.com"
        # The entry builds its own client: the flow's channel is closed, once (#610)
        client.close.assert_awaited_once()
        data = result["data"]
        assert data["email"] == "test@example.com"
        assert data["spaces"] == ["space-1"]
        assert data["device_id"] == "dev-uuid"
        # Ensure password_hash is stored, not plaintext password
        assert data["password_hash"] == hashlib.sha256(b"secret").hexdigest()
        assert "password" not in data
        # Session token persisted to survive restarts (avoids re-login / 2FA)
        assert data["session_token"] == "tok"
        assert data["user_hex_id"] == "hex"

    async def test_step_select_spaces_without_session_token(
        self, hass: HomeAssistant, client: MagicMock, spaces_api: MagicMock
    ) -> None:
        client.session.session_token = None
        result = await _submit_user(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"spaces": ["space-1"]}
        )
        assert "session_token" not in result["data"]
        assert "user_hex_id" not in result["data"]

    async def test_step_select_spaces_with_client_loads_spaces(
        self, hass: HomeAssistant, client: MagicMock, spaces_api: MagicMock
    ) -> None:
        result = await _submit_user(hass)

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "select_spaces"
        spaces_api.list_spaces.assert_awaited_once()
        validator = next(iter(result["data_schema"].schema.values()))
        selector = next(v for v in validator.validators if isinstance(v, SelectSelector))
        assert selector.config["options"] == [{"value": "space-1", "label": "Home"}]

    async def test_step_select_spaces_list_error_aborts_cannot_connect(
        self, hass: HomeAssistant, client: MagicMock, spaces_api: MagicMock
    ) -> None:
        """A network error listing spaces aborts with cannot_connect, not "Unknown error" (#610)."""
        spaces_api.list_spaces.side_effect = ConnectionError("down")

        result = await _submit_user(hass)

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "cannot_connect"
        client.close.assert_awaited_once()

    async def test_abandoned_flow_closes_the_client(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        """Closing the dialog midway (e.g. at 2FA) closes the channel (#610)."""
        client.login.side_effect = TwoFactorRequiredError("req-1")
        result = await _submit_user(hass)
        client.close.assert_not_awaited()

        hass.config_entries.flow.async_abort(result["flow_id"])
        await hass.async_block_till_done()

        client.close.assert_awaited_once()

    async def test_abandoned_flow_tolerates_a_failing_close(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        client.login.side_effect = TwoFactorRequiredError("req-1")
        client.close.side_effect = OSError("already gone")
        result = await _submit_user(hass)

        hass.config_entries.flow.async_abort(result["flow_id"])
        await hass.async_block_till_done()

        client.close.assert_awaited_once()

    async def test_step_select_spaces_schema_starts_empty_with_dropdown_search(
        self, hass: HomeAssistant, client: MagicMock, spaces_api: MagicMock
    ) -> None:
        """Installer UX (#166): the schema must default to no spaces selected
        and render in dropdown mode (filterable chip input), not a checkbox
        list. Without this an account with many customer spaces auto-selects
        all of them and the installer can ship the wrong one by inertia.
        """
        spaces_api.list_spaces.return_value = [_space("s1", "A"), _space("s2", "B")]

        result = await _submit_user(hass)

        schema = result["data_schema"]
        spaces_marker = next(k for k in schema.schema if getattr(k, "schema", k) == "spaces")
        # `Required` with explicit empty default → nothing checked initially
        assert spaces_marker.default() == [], (
            "spaces selector must default to an empty list so the installer "
            "is forced to make an explicit choice (#166)"
        )
        # The marker's value is `vol.All(SelectSelector, vol.Length(min=1))`.
        # Pull the selector out so we can assert its config.
        validator = schema.schema[spaces_marker]
        selector = next(v for v in validator.validators if isinstance(v, SelectSelector))
        assert selector.config["mode"] == SelectSelectorMode.DROPDOWN, (
            "dropdown mode renders the chip input with the built-in name "
            "filter, which is what makes long space lists tractable (#166)"
        )
        assert selector.config["multiple"] is True
        assert selector.config["sort"] is True

    async def test_step_select_spaces_rejects_empty_selection(
        self, hass: HomeAssistant, client: MagicMock, spaces_api: MagicMock
    ) -> None:
        """Counter-test for #166: starting empty must not let the user
        submit *without* choosing a space — a config entry with zero
        spaces is a do-nothing entry. The `default=[]` change weakens
        the implicit `vol.Required` "missing key fails" guarantee, so
        we re-establish it with an explicit `vol.Length(min=1)`.
        """
        spaces_api.list_spaces.return_value = []
        result = await _submit_user(hass)

        schema = result["data_schema"]
        # Empty list explicitly — the path a user would hit by submitting
        # the form with no chips selected.
        with pytest.raises(vol.MultipleInvalid):
            schema({"spaces": []})
        # And the default-fill path (frontend ships nothing) must also fail.
        with pytest.raises(vol.MultipleInvalid):
            schema({})
        # The flow manager enforces it on submit.
        for submitted in ({"spaces": []}, {}):
            with pytest.raises(InvalidData):
                await hass.config_entries.flow.async_configure(result["flow_id"], submitted)
        assert not hass.config_entries.async_entries(DOMAIN)


class TestAsyncStepReauth:
    async def test_step_reauth_seeds_email_and_calls_confirm(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        entry = _entry(hass, {"email": "user@example.com", "app_label": "Verux"})

        result = await entry.start_reauth_flow(hass)
        assert result["step_id"] == "reauth_confirm"
        await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "x"})

        assert client.cls.call_args.kwargs["email"] == "user@example.com"
        assert client.cls.call_args.kwargs["app_label"] == "Verux"

    async def test_step_reauth_confirm_no_input_shows_form(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)

        result = await entry.start_reauth_flow(hass)

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        # Email surfaced in the prompt so the user knows which account they're re-auth'ing
        assert result["description_placeholders"]["email"] == "user@example.com"

    async def test_step_reauth_confirm_invalid_auth_shows_error(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        result = await _entry(hass).start_reauth_flow(hass)
        client.connect.side_effect = AuthenticationError("nope")

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "wrong"}
        )

        assert result["errors"]["base"] == "invalid_auth"

    async def test_step_reauth_confirm_cannot_connect(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        result = await _entry(hass).start_reauth_flow(hass)
        client.connect.side_effect = ConnectionError("refused")

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "x"}
        )

        assert result["errors"]["base"] == "cannot_connect"

    async def test_step_reauth_confirm_unknown_error(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        result = await _entry(hass).start_reauth_flow(hass)
        client.login.side_effect = RuntimeError("boom")

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "x"}
        )

        assert result["errors"]["base"] == "unknown"

    async def test_step_reauth_confirm_reuses_the_entry_device_id(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        """Ajax binds the session token to the `client-device-id` that asked for it.

        Logging in from a throwaway id yields a token that every later call
        rejects with UNAUTHENTICATED, which puts the entry in a permanent
        reauth loop.
        """
        entry = _entry(
            hass, {"email": "user@example.com", "app_label": "Ajax", "device_id": "dev-original"}
        )
        result = await entry.start_reauth_flow(hass)

        await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "x"})

        assert client.cls.call_args.kwargs["device_id"] == "dev-original"

    async def test_finish_reauth_persists_the_device_id(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        """The stored id must match the token, or the reload fails to authenticate."""
        entry = _entry(hass)
        client.session.session_token = "tok-new"
        client.session.user_hex_id = "hex-1"
        client.session.device_id = "dev-used"

        async def _close() -> None:
            # `AjaxGrpcClient.close()` wipes the session, and the reauth path
            # closes before persisting. Reading the token off the client
            # afterwards used to yield None, leaving the entry on its old,
            # rejected token.
            client.session.session_token = None
            client.session.user_hex_id = None

        client.close.side_effect = _close
        result = await entry.start_reauth_flow(hass)

        await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "x"})
        await hass.async_block_till_done()

        assert entry.data["session_token"] == "tok-new"
        assert entry.data["user_hex_id"] == "hex-1"
        assert entry.data["device_id"] == "dev-used"

    async def test_reauth_without_a_session_token_keeps_the_old_one(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        entry = _entry(
            hass, {"email": "user@example.com", "app_label": "Ajax", "session_token": "tok-old"}
        )
        client.session.session_token = None
        result = await entry.start_reauth_flow(hass)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "newpass"}
        )
        await hass.async_block_till_done()

        assert result["reason"] == "reauth_successful"
        assert entry.data["session_token"] == "tok-old"
        assert entry.data["password_hash"] == hashlib.sha256(b"newpass").hexdigest()

    async def test_reauth_2fa_persists_the_token_despite_close_clearing_it(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        """The channel is closed before persisting, and `close()` wipes the session.

        Reading the token off the client after closing yielded nothing, so the
        entry silently kept its old token. On reload that token was rejected,
        the coordinator forced a re-login, and Ajax demanded 2FA again — an
        unbreakable loop that no amount of reauthenticating could escape.
        """
        entry = _entry(hass)
        client.session.session_token = None
        client.session.user_hex_id = None
        client.session.device_id = "dev-used"
        client.login.side_effect = TwoFactorRequiredError("req-1")

        async def _login_totp(**_kwargs: object) -> None:
            client.session.session_token = "tok-fresh"
            client.session.user_hex_id = "hex-1"

        async def _close() -> None:
            client.session.session_token = None
            client.session.user_hex_id = None

        client.login_totp.side_effect = _login_totp
        client.close.side_effect = _close
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "x"}
        )
        assert result["step_id"] == "reauth_2fa"

        await hass.config_entries.flow.async_configure(result["flow_id"], {"totp_code": "123456"})
        await hass.async_block_till_done()

        assert client.login_totp.call_args.kwargs["request_id"] == "req-1"
        assert entry.data["session_token"] == "tok-fresh"
        assert entry.data["user_hex_id"] == "hex-1"
        assert entry.data["device_id"] == "dev-used"

    async def test_step_reauth_confirm_2fa_required_forwards(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        result = await _entry(hass).start_reauth_flow(hass)
        client.login.side_effect = TwoFactorRequiredError("req-XYZ")

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "x"}
        )

        assert result["step_id"] == "reauth_2fa"
        await hass.config_entries.flow.async_configure(result["flow_id"], {"totp_code": "1"})
        assert client.login_totp.call_args.kwargs["request_id"] == "req-XYZ"

    async def test_step_reauth_confirm_success_updates_entry(
        self, hass: HomeAssistant, client: MagicMock, setup_entry: AsyncMock
    ) -> None:
        entry = _entry(hass)
        client.session.session_token = "tok-fresh"
        client.session.user_hex_id = "hex-99"
        result = await entry.start_reauth_flow(hass)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "newpass"}
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        new_data = entry.data
        # Same email + app_label preserved; password rotated; fresh session persisted
        assert new_data["email"] == "user@example.com"
        assert new_data["app_label"] == "Ajax"
        assert new_data["password_hash"] == hashlib.sha256(b"newpass").hexdigest()
        assert new_data["session_token"] == "tok-fresh"
        assert new_data["user_hex_id"] == "hex-99"
        # Plaintext password never lands on the entry
        assert "password" not in new_data
        # The entry is reloaded with the new credentials
        setup_entry.assert_awaited_once()

    async def _at_reauth_2fa(self, hass: HomeAssistant, client: MagicMock) -> dict[str, Any]:
        result = await _entry(hass).start_reauth_flow(hass)
        client.login.side_effect = TwoFactorRequiredError("req-9")
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "newpass"}
        )

    async def test_step_reauth_2fa_invalid_totp(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        result = await self._at_reauth_2fa(hass, client)
        client.login_totp.side_effect = AuthenticationError("bad")

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"totp_code": "000000"}
        )

        assert result["errors"]["base"] == "invalid_totp"
        assert result["step_id"] == "reauth_2fa"

    @pytest.mark.parametrize(
        ("error", "key"), [(TimeoutError(), "cannot_connect"), (RuntimeError("x"), "unknown")]
    )
    async def test_step_reauth_2fa_errors(
        self, hass: HomeAssistant, client: MagicMock, error: Exception, key: str
    ) -> None:
        result = await self._at_reauth_2fa(hass, client)
        client.login_totp.side_effect = error

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"totp_code": "000000"}
        )

        assert result["errors"]["base"] == key

    async def test_step_reauth_2fa_success_finishes_reauth(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        client.session.session_token = "tok-2fa"
        client.session.user_hex_id = "hex-2fa"
        result = await self._at_reauth_2fa(hass, client)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"totp_code": "123456"}
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        (entry,) = hass.config_entries.async_entries(DOMAIN)
        assert entry.data["session_token"] == "tok-2fa"
        assert entry.data["password_hash"] == hashlib.sha256(b"newpass").hexdigest()


class TestAsyncFinishReconfigure:
    """Reconfiguring to a different account must refresh the entry's visible
    title and unique_id, not just its data (#241)."""

    @pytest.fixture(autouse=True)
    def _no_store(self) -> Iterator[AsyncMock]:
        with patch(
            "custom_components.aegis_ajax.monitoring_companies.async_remove_monitoring_companies",
            AsyncMock(),
        ) as remove:
            yield remove

    @staticmethod
    def _make_entry(hass: HomeAssistant, **data: str) -> MockConfigEntry:
        return _entry(hass, {"email": "old@example.com", "app_label": "Ajax", **data})

    @staticmethod
    async def _reconfigure(
        hass: HomeAssistant, entry: MockConfigEntry, email: str = "new@example.com"
    ) -> dict[str, Any]:
        result = await entry.start_reconfigure_flow(hass)
        assert result["step_id"] == "reconfigure"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": email, "password": "pw"}
        )
        await hass.async_block_till_done()
        return result

    async def test_reconfigure_form_prefills_the_entry(self, hass: HomeAssistant) -> None:
        entry = self._make_entry(hass, app_label="Verux")
        result = await entry.start_reconfigure_flow(hass)

        defaults = {str(k): k.default() for k in result["data_schema"].schema if k != "password"}
        assert defaults == {"email": "old@example.com", "app_label": "Verux"}

    async def test_finish_reconfigure_drops_the_stored_cra_companies(
        self, hass: HomeAssistant, client: MagicMock, _no_store: AsyncMock
    ) -> None:
        """The reload after a reconfigure fetches the CRA companies again (#561)."""
        entry = self._make_entry(hass)

        await self._reconfigure(hass, entry, "old@example.com")

        _no_store.assert_awaited_once_with(hass, entry.entry_id)

    async def test_finish_reconfigure_updates_title_and_unique_id(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        entry = self._make_entry(hass)

        result = await self._reconfigure(hass, entry)

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        # The new email must drive both the stored data AND the visible title /
        # unique_id, so the integration's front page reflects it immediately.
        assert entry.data["email"] == "new@example.com"
        assert entry.title == "Ajax Security (new@example.com)"
        assert entry.unique_id == "new@example.com"
        assert entry.data["password_hash"] == hashlib.sha256(b"pw").hexdigest()

    async def test_reconfigure_to_an_account_already_added_aborts(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        entry = self._make_entry(hass)
        _entry(hass, {"email": "new@example.com", "app_label": "Ajax"})

        result = await self._reconfigure(hass, entry)

        # Used to surface as "Unknown error": the abort was caught as unexpected.
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"
        assert entry.data["email"] == "old@example.com"

    async def test_reconfigure_2fa_to_an_account_already_added_aborts(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        _entry(hass, {"email": "new@example.com", "app_label": "Ajax"})
        entry, result = await self._at_reconfigure_2fa(hass, client)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"totp_code": "123456"}
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"
        assert entry.data["email"] == "old@example.com"

    async def test_finish_reconfigure_persists_the_device_id(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        """Same binding as reauth: a stored id that mismatches the token is fatal."""
        entry = self._make_entry(hass)
        client.session.device_id = "dev-used"

        async def _close() -> None:
            # The reconfigure path closes the channel before persisting, and
            # `close()` clears the session.
            client.session.session_token = None

        client.close.side_effect = _close

        await self._reconfigure(hass, entry)

        assert entry.data["session_token"] == "tok"
        assert entry.data["device_id"] == "dev-used"

    async def test_reconfigure_without_a_session_token_keeps_the_old_one(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        entry = self._make_entry(hass, session_token="tok-old")
        client.session.session_token = None

        await self._reconfigure(hass, entry)

        assert entry.data["session_token"] == "tok-old"
        assert entry.data["email"] == "new@example.com"

    async def test_step_reconfigure_reuses_the_entry_device_id(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        entry = self._make_entry(hass, device_id="dev-original")

        await self._reconfigure(hass, entry, "old@example.com")

        assert client.cls.call_args.kwargs["device_id"] == "dev-original"

    @pytest.mark.parametrize(
        ("error", "key"),
        [
            (AuthenticationError("nope"), "invalid_auth"),
            (ConnectionError("down"), "cannot_connect"),
            (TimeoutError(), "cannot_connect"),
            (RuntimeError("boom"), "unknown"),
        ],
    )
    async def test_step_reconfigure_errors(
        self, hass: HomeAssistant, client: MagicMock, error: Exception, key: str
    ) -> None:
        entry = self._make_entry(hass)
        client.login.side_effect = error

        result = await self._reconfigure(hass, entry)

        assert result["step_id"] == "reconfigure"
        assert result["errors"]["base"] == key
        assert entry.data["email"] == "old@example.com"

    async def _at_reconfigure_2fa(
        self, hass: HomeAssistant, client: MagicMock
    ) -> tuple[MockConfigEntry, dict[str, Any]]:
        entry = self._make_entry(hass)
        client.login.side_effect = TwoFactorRequiredError("req-r")
        result = await self._reconfigure(hass, entry)
        assert result["step_id"] == "reconfigure_2fa"
        return entry, result

    async def test_step_reconfigure_2fa_success(
        self, hass: HomeAssistant, client: MagicMock
    ) -> None:
        entry, result = await self._at_reconfigure_2fa(hass, client)
        client.session.session_token = "tok-2fa"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"totp_code": "123456"}
        )
        await hass.async_block_till_done()

        assert result["reason"] == "reconfigure_successful"
        client.login_totp.assert_awaited_once_with(
            email="new@example.com", request_id="req-r", totp_code="123456"
        )
        assert entry.data["session_token"] == "tok-2fa"
        assert entry.unique_id == "new@example.com"

    @pytest.mark.parametrize(
        ("error", "key"),
        [
            (AuthenticationError("bad"), "invalid_totp"),
            (TimeoutError(), "cannot_connect"),
            (RuntimeError("x"), "unknown"),
        ],
    )
    async def test_step_reconfigure_2fa_errors(
        self, hass: HomeAssistant, client: MagicMock, error: Exception, key: str
    ) -> None:
        _, result = await self._at_reconfigure_2fa(hass, client)
        client.login_totp.side_effect = error

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"totp_code": "000000"}
        )

        assert result["step_id"] == "reconfigure_2fa"
        assert result["errors"]["base"] == key


class TestOptionsFlow:
    @staticmethod
    def _make_entry(
        hass: HomeAssistant, options: dict | None = None, data: dict | None = None
    ) -> MockConfigEntry:
        return _entry(hass, data, options)

    @staticmethod
    async def _submit(
        hass: HomeAssistant, entry: MockConfigEntry, user_input: dict[str, Any]
    ) -> dict[str, Any]:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
        await hass.async_block_till_done()
        return result

    @pytest.fixture
    def reload(self, hass: HomeAssistant) -> Iterator[AsyncMock]:
        with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock:
            yield mock

    async def test_options_flow_init(self, hass: HomeAssistant) -> None:
        """The flow reads the entry it was opened for."""
        entry = self._make_entry(hass, options={"poll_interval": 120})

        result = await hass.config_entries.options.async_init(entry.entry_id)

        schema = result["data_schema"].schema
        key = next(k for k in schema if str(k) == "poll_interval")
        assert key.default() == 120

    async def test_options_flow_no_input_shows_form(self, hass: HomeAssistant) -> None:
        entry = self._make_entry(hass, options={"poll_interval": 60, "use_pin_code": False})

        result = await hass.config_entries.options.async_init(entry.entry_id)

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"

    async def test_options_flow_with_input_creates_entry(self, hass: HomeAssistant) -> None:
        entry = self._make_entry(hass)

        result = await self._submit(hass, entry, {"poll_interval": 60, "use_pin_code": True})

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == ""
        assert entry.options["poll_interval"] == 60
        assert entry.options["use_pin_code"] is True

    async def test_options_flow_offers_delay_panel_states_default_off(
        self, hass: HomeAssistant
    ) -> None:
        # #454: opt-in, default OFF — automations keyed on `armed_*` would
        # otherwise start firing seconds later than they do today.
        entry = self._make_entry(hass, options={})

        result = await hass.config_entries.options.async_init(entry.entry_id)

        schema = result["data_schema"].schema
        key = next(k for k in schema if str(k) == CONF_DELAY_PANEL_STATES)
        assert key.default() is False

    async def test_options_flow_stores_delay_panel_states(self, hass: HomeAssistant) -> None:
        entry = self._make_entry(hass)

        await self._submit(
            hass, entry, {"poll_interval": 60, "use_pin_code": False, CONF_DELAY_PANEL_STATES: True}
        )

        assert entry.options[CONF_DELAY_PANEL_STATES] is True

    async def test_options_flow_rejects_poll_interval_below_minimum(
        self, hass: HomeAssistant
    ) -> None:
        """Below 60 s never reaches the options: the form refuses it."""
        entry = self._make_entry(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)

        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(
                result["flow_id"], {"poll_interval": 5, "use_pin_code": False}
            )
        assert "poll_interval" not in entry.options

        await hass.config_entries.options.async_configure(
            result["flow_id"], {"poll_interval": 60, "use_pin_code": False}
        )
        assert entry.options["poll_interval"] == 60

    async def test_options_flow_with_pin_code_stores_hash(self, hass: HomeAssistant) -> None:
        """Ensure the pin code is stored as a hash, not plaintext."""
        entry = self._make_entry(hass)

        await self._submit(
            hass, entry, {"poll_interval": 60, "use_pin_code": True, "pin_code": "1234"}
        )

        assert "pin_code" not in entry.options
        stored = entry.options["pin_code_hash"]
        assert stored.startswith("scrypt$")
        validate_pin({"use_pin_code": True, "pin_code_hash": stored}, "1234")
        with pytest.raises(HomeAssistantError):
            validate_pin({"use_pin_code": True, "pin_code_hash": stored}, "4321")

    async def test_options_flow_without_pin_code_no_hash(self, hass: HomeAssistant) -> None:
        """Ensure no pin_code_hash is stored when pin_code is empty."""
        entry = self._make_entry(hass)

        await self._submit(hass, entry, {"poll_interval": 60, "use_pin_code": False})

        assert "pin_code" not in entry.options
        assert "pin_code_hash" not in entry.options

    async def test_options_flow_fcm_values_persisted_to_entry_data(
        self, hass: HomeAssistant, reload: AsyncMock
    ) -> None:
        """Non-empty FCM values move from form input into entry.data."""
        entry = self._make_entry(hass, data={"email": "x@y"})

        await self._submit(
            hass,
            entry,
            {
                "poll_interval": 60,
                "use_pin_code": False,
                "fcm_project_id": "proj",
                "fcm_app_id": "app",
                "fcm_api_key": "key",
                "fcm_sender_id": "123",
            },
        )

        assert entry.data["fcm_project_id"] == "proj"
        assert entry.data["fcm_app_id"] == "app"
        assert entry.data["fcm_api_key"] == "key"
        assert entry.data["fcm_sender_id"] == "123"
        assert entry.data["email"] == "x@y"
        # FCM keys must not leak into options
        for k in FCM_KEYS:
            assert k not in entry.options

    async def test_options_flow_trims_fcm_values(
        self, hass: HomeAssistant, reload: AsyncMock
    ) -> None:
        """Whitespace pasted with FCM credentials must not change their identity."""
        entry = self._make_entry(hass, data={"email": "x@y"})

        await self._submit(
            hass,
            entry,
            {
                "poll_interval": 60,
                "use_pin_code": False,
                "fcm_project_id": " project ",
                "fcm_app_id": " app ",
                "fcm_api_key": " key ",
                "fcm_sender_id": " sender ",
            },
        )

        assert {key: entry.data[key] for key in FCM_KEYS} == {
            "fcm_project_id": "project",
            "fcm_app_id": "app",
            "fcm_api_key": "key",
            "fcm_sender_id": "sender",
        }

    async def test_options_flow_clearing_three_text_fcm_fields_removes_them(
        self, hass: HomeAssistant, reload: AsyncMock
    ) -> None:
        """Empty strings for the three text FCM fields remove them (#138).

        `fcm_api_key` is the password TextSelector — see
        `test_options_flow_empty_api_key_preserves_saved_value` for
        why an empty submission there is intentionally a no-op (#183).
        Only the three text fields (project_id, app_id, sender_id)
        round-trip their saved values via `suggested_value`, so a user
        actively emptying one of them is a deliberate clear.
        """
        entry = self._make_entry(
            hass,
            data={
                "email": "x@y",
                "fcm_project_id": "old_proj",
                "fcm_app_id": "old_app",
                "fcm_api_key": "old_key",
                "fcm_sender_id": "old_sender",
            },
        )

        await self._submit(
            hass,
            entry,
            {
                "poll_interval": 60,
                "use_pin_code": False,
                "fcm_project_id": "",
                "fcm_app_id": "",
                "fcm_api_key": "",
                "fcm_sender_id": "",
            },
        )

        for k in ("fcm_project_id", "fcm_app_id", "fcm_sender_id"):
            assert k not in entry.data, f"{k} should be removed when cleared"
        # fcm_api_key sticks (use the explicit toggle to wipe it).
        assert entry.data["fcm_api_key"] == "old_key"
        assert entry.data["email"] == "x@y"
        reload.assert_awaited_once_with(entry.entry_id)

    async def test_options_flow_empty_api_key_preserves_saved_value(
        self, hass: HomeAssistant, reload: AsyncMock
    ) -> None:
        """Empty `fcm_api_key` on resubmit must NOT wipe the saved key (#183).

        Reproduces the bug raven2k24 hit on `1.5.1`: HA's frontend
        leaves password TextSelectors blank when re-opening a form
        (it won't display saved secrets, even masked), so a benign
        re-submit — e.g. changing the poll interval — used to wipe the
        previously-saved API key because the empty submission ran
        through the `else: pop` branch. After this fix, only the
        explicit `Delete FCM credentials` toggle wipes the key.
        """
        data = {
            "fcm_project_id": "proj",
            "fcm_app_id": "1:99:android:abc",
            "fcm_api_key": "AIza-saved-secret",
            "fcm_sender_id": "99",
        }
        entry = self._make_entry(hass, data=data)

        await self._submit(
            hass,
            entry,
            {
                "poll_interval": 90,  # the change the user actually came for
                "use_pin_code": False,
                "fcm_project_id": "proj",
                "fcm_app_id": "1:99:android:abc",
                "fcm_api_key": "",  # password field: blank on re-open
                "fcm_sender_id": "99",
            },
        )

        assert entry.data["fcm_api_key"] == "AIza-saved-secret"
        # Nothing in data changed, so nothing to reload for.
        assert dict(entry.data) == data
        reload.assert_not_awaited()
        assert entry.options["poll_interval"] == 90

    async def test_options_flow_typed_api_key_still_overwrites_saved_value(
        self, hass: HomeAssistant, reload: AsyncMock
    ) -> None:
        """The no-op-on-empty guard only fires for empty submissions —
        if the user actually re-types the API key, the new value wins.
        """
        entry = self._make_entry(
            hass,
            data={
                "fcm_project_id": "proj",
                "fcm_app_id": "1:99:android:abc",
                "fcm_api_key": "AIza-old",
                "fcm_sender_id": "99",
            },
        )

        await self._submit(
            hass,
            entry,
            {
                "poll_interval": 60,
                "use_pin_code": False,
                "fcm_project_id": "proj",
                "fcm_app_id": "1:99:android:abc",
                "fcm_api_key": "AIza-new",
                "fcm_sender_id": "99",
            },
        )

        assert entry.data["fcm_api_key"] == "AIza-new"

    async def test_options_flow_clear_fcm_toggle_removes_all_keys(
        self, hass: HomeAssistant, reload: AsyncMock
    ) -> None:
        """'Delete FCM credentials' toggle wipes all four keys regardless of field values (#138)."""
        entry = self._make_entry(
            hass,
            data={
                "email": "x@y",
                "fcm_project_id": "old_proj",
                "fcm_app_id": "old_app",
                "fcm_api_key": "old_key",
                "fcm_sender_id": "old_sender",
            },
        )

        # The toggle wins even when the form re-submits the prior FCM values
        # (selector-default round-trip case — what HA's password field does).
        await self._submit(
            hass,
            entry,
            {
                "poll_interval": 60,
                "use_pin_code": False,
                "fcm_project_id": "old_proj",
                "fcm_app_id": "old_app",
                "fcm_api_key": "old_key",
                "fcm_sender_id": "old_sender",
                "clear_fcm_credentials": True,
            },
        )

        for k in FCM_KEYS:
            assert k not in entry.data
        assert entry.data["email"] == "x@y"
        # The toggle itself must not leak into entry.options
        assert "clear_fcm_credentials" not in entry.options

    async def test_options_flow_clear_fcm_toggle_off_keeps_normal_update_path(
        self, hass: HomeAssistant, reload: AsyncMock
    ) -> None:
        entry = self._make_entry(hass, data={"fcm_project_id": "old"})

        await self._submit(
            hass,
            entry,
            {
                "poll_interval": 60,
                "use_pin_code": False,
                "fcm_project_id": "new_proj",
                "clear_fcm_credentials": False,
            },
        )

        assert entry.data["fcm_project_id"] == "new_proj"

    async def test_options_flow_reloads_when_data_changes(
        self, hass: HomeAssistant, reload: AsyncMock
    ) -> None:
        """#148 — FCM creds in entry.data must trigger an explicit reload.

        The `_async_options_update_listener` would normally fire after
        `async_finish_flow` writes options, but when only `data` changes
        and `options` round-trip identical, the framework's second
        `async_update_entry(options=...)` short-circuits without firing
        a listener — leaving the FCM client running with stale
        credentials until the user manually reloads. Mirror the
        repair-flow pattern: await `async_reload` explicitly.
        """
        entry = self._make_entry(hass, data={"email": "x@y"})

        await self._submit(
            hass,
            entry,
            {
                "poll_interval": 60,
                "use_pin_code": False,
                "fcm_project_id": "proj",
                "fcm_app_id": "app",
                "fcm_api_key": "key",
                "fcm_sender_id": "123",
            },
        )

        reload.assert_awaited_once_with(entry.entry_id)

    async def test_options_flow_no_reload_when_data_unchanged(
        self, hass: HomeAssistant, reload: AsyncMock
    ) -> None:
        """Non-FCM option tweaks (poll_interval, etc.) leave entry.data alone.

        The existing `_async_options_update_listener` will still fire on
        options changes — we only want to add a second reload when data
        actually changed (the FCM-creds path). Otherwise users editing
        the polling interval would trigger redundant reloads.
        """
        entry = self._make_entry(hass, data={"email": "x@y"})

        await self._submit(hass, entry, {"poll_interval": 90, "use_pin_code": False})

        # No data changes → entry.data untouched, and our explicit
        # async_reload should NOT fire.
        assert dict(entry.data) == {"email": "x@y"}
        reload.assert_not_awaited()

    async def test_options_flow_clearing_fcm_also_strips_legacy_options(
        self, hass: HomeAssistant, reload: AsyncMock
    ) -> None:
        """Legacy installs kept FCM in entry.options; clearing must also drop them there."""
        entry = self._make_entry(
            hass,
            options={
                "fcm_project_id": "legacy_proj",
                "fcm_app_id": "legacy_app",
                "fcm_api_key": "legacy_key",
                "fcm_sender_id": "legacy_sender",
            },
        )

        await self._submit(
            hass,
            entry,
            {
                "poll_interval": 60,
                "use_pin_code": False,
                "fcm_project_id": "",
                "fcm_app_id": "",
                "fcm_api_key": "",
                "fcm_sender_id": "",
            },
        )

        for k in FCM_KEYS:
            assert k not in entry.options
