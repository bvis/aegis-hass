"""Tests for config flow."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

from custom_components.aegis_ajax.api.session import AuthenticationError, TwoFactorRequiredError
from custom_components.aegis_ajax.config_flow import (
    AjaxCobrandedConfigFlow,
    AjaxCobrandedOptionsFlow,
)
from custom_components.aegis_ajax.const import DOMAIN


class TestConfigFlowInit:
    def test_domain(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        assert flow.DOMAIN == DOMAIN

    def test_has_user_step(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        assert hasattr(flow, "async_step_user")

    def test_has_2fa_step(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        assert hasattr(flow, "async_step_2fa")

    def test_has_select_spaces_step(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        assert hasattr(flow, "async_step_select_spaces")

    def test_version(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        assert flow.VERSION == 2


class TestAsyncStepDhcp:
    @staticmethod
    def _discovery(
        ip: str = "192.168.1.42",
        mac: str = "9c:75:6e:1b:60:c4",
        hostname: str | None = None,
    ) -> MagicMock:
        info = MagicMock()
        info.ip = ip
        info.macaddress = mac
        info.hostname = hostname
        return info

    @pytest.mark.asyncio
    async def test_first_discovery_forwards_into_user_step(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        flow.hass = MagicMock()
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow._async_current_entries = MagicMock(return_value=[])
        flow.async_step_user = AsyncMock(return_value={"type": "form"})
        flow.context = {}

        await flow.async_step_dhcp(self._discovery(hostname="ajax-hub-01"))

        # Per-MAC unique_id deduplicates repeat DHCP packets
        flow.async_set_unique_id.assert_awaited_once()
        flow._abort_if_unique_id_configured.assert_called_once()
        flow.async_step_user.assert_awaited_once()
        assert flow.context["title_placeholders"] == {"name": "ajax-hub-01"}

    @pytest.mark.asyncio
    async def test_no_hostname_falls_back_to_ip_in_title(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        flow.hass = MagicMock()
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow._async_current_entries = MagicMock(return_value=[])
        flow.async_step_user = AsyncMock(return_value={"type": "form"})
        flow.context = {}

        await flow.async_step_dhcp(self._discovery(hostname=None))

        assert flow.context["title_placeholders"] == {"name": "Ajax hub (192.168.1.42)"}

    @pytest.mark.asyncio
    async def test_existing_entry_aborts_already_configured(self) -> None:
        """A user with at least one Aegis account configured doesn't get nagged."""
        flow = AjaxCobrandedConfigFlow()
        flow.hass = MagicMock()
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow._async_current_entries = MagicMock(return_value=[MagicMock()])
        flow.async_abort = MagicMock(return_value={"type": "abort"})
        flow.async_step_user = AsyncMock()
        flow.context = {}

        await flow.async_step_dhcp(self._discovery())

        flow.async_abort.assert_called_once_with(reason="already_configured")
        flow.async_step_user.assert_not_awaited()


class TestAsyncStepUser:
    @pytest.mark.asyncio
    async def test_step_user_no_input_shows_form(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        await flow.async_step_user(None)
        flow.async_show_form.assert_called_once()
        assert flow.async_show_form.call_args[1]["step_id"] == "user"

    @pytest.mark.asyncio
    async def test_step_user_invalid_auth(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()

        mock_client = MagicMock()
        mock_client.connect = AsyncMock(side_effect=AuthenticationError("invalid"))

        with patch(
            "custom_components.aegis_ajax.config_flow.AjaxGrpcClient", return_value=mock_client
        ):
            await flow.async_step_user({"email": "a@b.com", "password": "bad"})

        assert flow.async_show_form.call_args[1]["errors"]["base"] == "invalid_auth"

    @pytest.mark.asyncio
    async def test_step_user_cannot_connect(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()

        mock_client = MagicMock()
        mock_client.connect = AsyncMock(side_effect=ConnectionError("refused"))

        with patch(
            "custom_components.aegis_ajax.config_flow.AjaxGrpcClient", return_value=mock_client
        ):
            await flow.async_step_user({"email": "a@b.com", "password": "pass"})

        assert flow.async_show_form.call_args[1]["errors"]["base"] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_step_user_unknown_error(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()

        mock_client = MagicMock()
        mock_client.connect = AsyncMock(side_effect=RuntimeError("unexpected"))

        with patch(
            "custom_components.aegis_ajax.config_flow.AjaxGrpcClient", return_value=mock_client
        ):
            await flow.async_step_user({"email": "a@b.com", "password": "pass"})

        assert flow.async_show_form.call_args[1]["errors"]["base"] == "unknown"

    @pytest.mark.asyncio
    async def test_step_user_2fa_required(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()

        mock_client = MagicMock()
        mock_client.connect = AsyncMock(side_effect=TwoFactorRequiredError("req-123"))

        with patch(
            "custom_components.aegis_ajax.config_flow.AjaxGrpcClient", return_value=mock_client
        ):
            await flow.async_step_user({"email": "a@b.com", "password": "pass"})

        # Should have shown 2fa form
        assert flow._request_id == "req-123"

    @pytest.mark.asyncio
    async def test_step_user_stores_password_hash_not_plaintext(self) -> None:
        """Ensure plaintext password is never stored; only the hash is kept."""
        flow = AjaxCobrandedConfigFlow()
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()

        mock_client = MagicMock()
        mock_client.connect = AsyncMock(side_effect=ConnectionError("refused"))

        with patch(
            "custom_components.aegis_ajax.config_flow.AjaxGrpcClient", return_value=mock_client
        ):
            await flow.async_step_user({"email": "a@b.com", "password": "mypassword"})

        expected_hash = hashlib.sha256(b"mypassword").hexdigest()
        assert flow._password_hash == expected_hash
        # The flow object should not have _password attribute with plaintext
        assert not hasattr(flow, "_password") or getattr(flow, "_password", None) != "mypassword"


class TestAsyncStep2FA:
    @pytest.mark.asyncio
    async def test_step_2fa_no_input_shows_form(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        await flow.async_step_2fa(None)
        flow.async_show_form.assert_called_once()
        assert flow.async_show_form.call_args[1]["step_id"] == "2fa"

    @pytest.mark.asyncio
    async def test_step_2fa_invalid_totp(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        flow._email = "test@example.com"
        flow._request_id = "req-123"
        flow.async_show_form = MagicMock(return_value={"type": "form"})

        mock_client = MagicMock()
        mock_client.login_totp = AsyncMock(side_effect=AuthenticationError("Invalid TOTP code"))
        flow._client = mock_client

        await flow.async_step_2fa({"totp_code": "000000"})
        assert flow.async_show_form.call_args[1]["errors"]["base"] == "invalid_totp"
        mock_client.login_totp.assert_called_once_with(
            email="test@example.com",
            request_id="req-123",
            totp_code="000000",
        )

    @pytest.mark.asyncio
    async def test_step_2fa_success(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        flow._email = "test@example.com"
        flow._request_id = "req-456"

        mock_client = MagicMock()
        mock_client.login_totp = AsyncMock()
        flow._client = mock_client
        flow.async_step_select_spaces = AsyncMock(return_value={"type": "form"})

        await flow.async_step_2fa({"totp_code": "123456"})
        mock_client.login_totp.assert_called_once_with(
            email="test@example.com",
            request_id="req-456",
            totp_code="123456",
        )
        flow.async_step_select_spaces.assert_called_once()

    @pytest.mark.asyncio
    async def test_step_2fa_unknown_error(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        flow._email = "test@example.com"
        flow._request_id = "req-789"
        flow.async_show_form = MagicMock(return_value={"type": "form"})

        mock_client = MagicMock()
        mock_client.login_totp = AsyncMock(side_effect=RuntimeError("unknown"))
        flow._client = mock_client

        await flow.async_step_2fa({"totp_code": "000000"})
        assert flow.async_show_form.call_args[1]["errors"]["base"] == "unknown"


class TestAsyncStepSelectSpaces:
    @pytest.mark.asyncio
    async def test_step_select_spaces_no_client_shows_form(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow._client = None

        await flow.async_step_select_spaces(None)
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_step_select_spaces_with_input_creates_entry(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        flow._email = "test@example.com"
        flow._password_hash = hashlib.sha256(b"secret").hexdigest()

        mock_client = MagicMock()
        mock_client.session.device_id = "dev-uuid"
        flow._client = mock_client

        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

        await flow.async_step_select_spaces({"spaces": ["space-1"]})
        flow.async_create_entry.assert_called_once()
        call_kwargs = flow.async_create_entry.call_args[1]
        assert call_kwargs["data"]["email"] == "test@example.com"
        assert call_kwargs["data"]["spaces"] == ["space-1"]
        # Ensure password_hash is stored, not plaintext password
        assert "password_hash" in call_kwargs["data"]
        assert "password" not in call_kwargs["data"]
        # Session token persisted to survive restarts (avoids re-login / 2FA)
        assert "session_token" in call_kwargs["data"]
        assert "user_hex_id" in call_kwargs["data"]

    @pytest.mark.asyncio
    async def test_step_select_spaces_with_client_loads_spaces(self) -> None:
        flow = AjaxCobrandedConfigFlow()
        flow.async_show_form = MagicMock(return_value={"type": "form"})

        mock_space = MagicMock()
        mock_space.id = "space-1"
        mock_space.name = "Home"

        mock_client = MagicMock()
        flow._client = mock_client

        mock_spaces_api = MagicMock()
        mock_spaces_api.list_spaces = AsyncMock(return_value=[mock_space])

        with patch(
            "custom_components.aegis_ajax.config_flow.SpacesApi", return_value=mock_spaces_api
        ):
            await flow.async_step_select_spaces(None)

        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_step_select_spaces_schema_starts_empty_with_dropdown_search(self) -> None:
        """Installer UX (#166): the schema must default to no spaces selected
        and render in dropdown mode (filterable chip input), not a checkbox
        list. Without this an account with many customer spaces auto-selects
        all of them and the installer can ship the wrong one by inertia.
        """
        from homeassistant.helpers.selector import SelectSelector, SelectSelectorMode

        flow = AjaxCobrandedConfigFlow()
        captured_schema: dict[str, object] = {}

        def _capture(**kwargs: object) -> dict[str, str]:
            captured_schema.update(kwargs)
            return {"type": "form"}

        flow.async_show_form = MagicMock(side_effect=_capture)

        mock_space_a = MagicMock()
        mock_space_a.id = "s1"
        mock_space_a.name = "A"
        mock_space_b = MagicMock()
        mock_space_b.id = "s2"
        mock_space_b.name = "B"
        flow._client = MagicMock()
        mock_spaces_api = MagicMock()
        mock_spaces_api.list_spaces = AsyncMock(return_value=[mock_space_a, mock_space_b])

        with patch(
            "custom_components.aegis_ajax.config_flow.SpacesApi", return_value=mock_spaces_api
        ):
            await flow.async_step_select_spaces(None)

        schema = captured_schema["data_schema"]
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

    @pytest.mark.asyncio
    async def test_step_select_spaces_rejects_empty_selection(self) -> None:
        """Counter-test for #166: starting empty must not let the user
        submit *without* choosing a space — a config entry with zero
        spaces is a do-nothing entry. The `default=[]` change weakens
        the implicit `vol.Required` "missing key fails" guarantee, so
        we re-establish it with an explicit `vol.Length(min=1)`.
        """
        flow = AjaxCobrandedConfigFlow()
        captured_schema: dict[str, object] = {}

        def _capture(**kwargs: object) -> dict[str, str]:
            captured_schema.update(kwargs)
            return {"type": "form"}

        flow.async_show_form = MagicMock(side_effect=_capture)
        flow._client = MagicMock()
        mock_spaces_api = MagicMock()
        mock_spaces_api.list_spaces = AsyncMock(return_value=[])

        with patch(
            "custom_components.aegis_ajax.config_flow.SpacesApi", return_value=mock_spaces_api
        ):
            await flow.async_step_select_spaces(None)

        schema = captured_schema["data_schema"]
        # Empty list explicitly — the path a user would hit by submitting
        # the form with no chips selected.
        with pytest.raises(vol.MultipleInvalid):
            schema({"spaces": []})
        # And the default-fill path (frontend ships nothing) must also fail.
        with pytest.raises(vol.MultipleInvalid):
            schema({})


class TestAsyncStepReauth:
    @staticmethod
    def _make_flow(entry_data: dict | None = None) -> AjaxCobrandedConfigFlow:
        """Build a flow with `_get_reauth_entry` stubbed to return a fake entry."""
        flow = AjaxCobrandedConfigFlow()
        entry = MagicMock()
        entry.data = entry_data or {"email": "user@example.com", "app_label": "Ajax"}
        flow._get_reauth_entry = MagicMock(return_value=entry)
        return flow

    @pytest.mark.asyncio
    async def test_step_reauth_seeds_email_and_calls_confirm(self) -> None:
        flow = self._make_flow()
        flow.async_step_reauth_confirm = AsyncMock(return_value={"type": "form"})

        await flow.async_step_reauth({"email": "user@example.com", "app_label": "Verux"})

        assert flow._email == "user@example.com"
        assert flow._app_label == "Verux"
        flow.async_step_reauth_confirm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_step_reauth_confirm_no_input_shows_form(self) -> None:
        flow = self._make_flow()
        flow.async_show_form = MagicMock(return_value={"type": "form"})

        await flow.async_step_reauth_confirm(None)

        flow.async_show_form.assert_called_once()
        assert flow.async_show_form.call_args[1]["step_id"] == "reauth_confirm"
        # Email surfaced in the prompt so the user knows which account they're re-auth'ing
        assert (
            flow.async_show_form.call_args[1]["description_placeholders"]["email"]
            == "user@example.com"
        )

    @pytest.mark.asyncio
    async def test_step_reauth_confirm_invalid_auth_shows_error(self) -> None:
        flow = self._make_flow()
        flow._email = "user@example.com"
        flow._app_label = "Ajax"
        flow.async_show_form = MagicMock(return_value={"type": "form"})

        mock_client = MagicMock()
        mock_client.connect = AsyncMock(side_effect=AuthenticationError("nope"))

        with patch(
            "custom_components.aegis_ajax.config_flow.AjaxGrpcClient", return_value=mock_client
        ):
            await flow.async_step_reauth_confirm({"password": "wrong"})

        assert flow.async_show_form.call_args[1]["errors"]["base"] == "invalid_auth"

    @pytest.mark.asyncio
    async def test_step_reauth_confirm_cannot_connect(self) -> None:
        flow = self._make_flow()
        flow._email = "user@example.com"
        flow._app_label = "Ajax"
        flow.async_show_form = MagicMock(return_value={"type": "form"})

        mock_client = MagicMock()
        mock_client.connect = AsyncMock(side_effect=ConnectionError("refused"))

        with patch(
            "custom_components.aegis_ajax.config_flow.AjaxGrpcClient", return_value=mock_client
        ):
            await flow.async_step_reauth_confirm({"password": "x"})

        assert flow.async_show_form.call_args[1]["errors"]["base"] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_step_reauth_confirm_2fa_required_forwards(self) -> None:
        flow = self._make_flow()
        flow._email = "user@example.com"
        flow._app_label = "Ajax"
        flow.async_step_reauth_2fa = AsyncMock(return_value={"type": "form"})

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.login = AsyncMock(side_effect=TwoFactorRequiredError("req-XYZ"))

        with patch(
            "custom_components.aegis_ajax.config_flow.AjaxGrpcClient", return_value=mock_client
        ):
            await flow.async_step_reauth_confirm({"password": "x"})

        assert flow._request_id == "req-XYZ"
        flow.async_step_reauth_2fa.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_step_reauth_confirm_success_updates_entry(self) -> None:
        flow = self._make_flow()
        flow._email = "user@example.com"
        flow._app_label = "Ajax"

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.login = AsyncMock()
        mock_client.close = AsyncMock()
        mock_client.session.session_token = "tok-fresh"
        mock_client.session.user_hex_id = "hex-99"

        flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})

        with patch(
            "custom_components.aegis_ajax.config_flow.AjaxGrpcClient", return_value=mock_client
        ):
            await flow.async_step_reauth_confirm({"password": "newpass"})

        flow.async_update_reload_and_abort.assert_called_once()
        kwargs = flow.async_update_reload_and_abort.call_args.kwargs
        assert kwargs["reason"] == "reauth_successful"
        new_data = kwargs["data"]
        # Same email + app_label preserved; password rotated; fresh session persisted
        assert new_data["email"] == "user@example.com"
        assert new_data["password_hash"] == hashlib.sha256(b"newpass").hexdigest()
        assert new_data["session_token"] == "tok-fresh"
        assert new_data["user_hex_id"] == "hex-99"
        # Plaintext password never lands on the entry
        assert "password" not in new_data

    @pytest.mark.asyncio
    async def test_step_reauth_2fa_invalid_totp(self) -> None:
        flow = self._make_flow()
        flow._email = "user@example.com"
        flow._request_id = "req-9"
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        mock_client = MagicMock()
        mock_client.login_totp = AsyncMock(side_effect=AuthenticationError("bad"))
        flow._client = mock_client

        await flow.async_step_reauth_2fa({"totp_code": "000000"})

        assert flow.async_show_form.call_args[1]["errors"]["base"] == "invalid_totp"
        assert flow.async_show_form.call_args[1]["step_id"] == "reauth_2fa"

    @pytest.mark.asyncio
    async def test_step_reauth_2fa_success_finishes_reauth(self) -> None:
        flow = self._make_flow()
        flow._email = "user@example.com"
        flow._app_label = "Ajax"
        flow._password_hash = hashlib.sha256(b"newpass").hexdigest()
        flow._request_id = "req-9"

        mock_client = MagicMock()
        mock_client.login_totp = AsyncMock()
        mock_client.close = AsyncMock()
        mock_client.session.session_token = "tok-2fa"
        mock_client.session.user_hex_id = "hex-2fa"
        flow._client = mock_client

        flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})

        await flow.async_step_reauth_2fa({"totp_code": "123456"})

        flow.async_update_reload_and_abort.assert_called_once()
        kwargs = flow.async_update_reload_and_abort.call_args.kwargs
        assert kwargs["reason"] == "reauth_successful"
        assert kwargs["data"]["session_token"] == "tok-2fa"


class TestOptionsFlow:
    @staticmethod
    def _make_flow(
        options: dict | None = None, data: dict | None = None
    ) -> tuple[AjaxCobrandedOptionsFlow, MagicMock]:
        config_entry = MagicMock()
        config_entry.options = options or {}
        config_entry.data = data or {}
        config_entry.entry_id = "test-entry-id"
        flow = AjaxCobrandedOptionsFlow(config_entry)
        flow.hass = MagicMock()
        # `async_step_init` awaits async_reload when data changes (#148),
        # so the default MagicMock that wraps it must be awaitable.
        flow.hass.config_entries.async_reload = AsyncMock()
        return flow, config_entry

    def test_options_flow_init(self) -> None:
        flow, config_entry = self._make_flow()
        assert flow._entry is config_entry

    @pytest.mark.asyncio
    async def test_options_flow_no_input_shows_form(self) -> None:
        flow, _ = self._make_flow(options={"poll_interval": 60, "use_pin_code": False})
        flow.async_show_form = MagicMock(return_value={"type": "form"})

        await flow.async_step_init(None)
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_options_flow_with_input_creates_entry(self) -> None:
        flow, _ = self._make_flow()
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

        await flow.async_step_init({"poll_interval": 60, "use_pin_code": True})
        flow.async_create_entry.assert_called_once_with(
            title="", data={"poll_interval": 60, "use_pin_code": True}
        )

    @pytest.mark.asyncio
    async def test_options_flow_clamps_poll_interval_to_minimum(self) -> None:
        flow, _ = self._make_flow()
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

        await flow.async_step_init({"poll_interval": 5, "use_pin_code": False})
        flow.async_create_entry.assert_called_once_with(
            title="", data={"poll_interval": 60, "use_pin_code": False}
        )

    @pytest.mark.asyncio
    async def test_options_flow_with_pin_code_stores_hash(self) -> None:
        """Ensure the pin code is stored as a hash, not plaintext."""
        flow, _ = self._make_flow()
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

        await flow.async_step_init({"poll_interval": 60, "use_pin_code": True, "pin_code": "1234"})
        flow.async_create_entry.assert_called_once()
        stored_data = flow.async_create_entry.call_args[1]["data"]
        assert "pin_code" not in stored_data
        expected_hash = hashlib.sha256(b"1234").hexdigest()
        assert stored_data["pin_code_hash"] == expected_hash

    @pytest.mark.asyncio
    async def test_options_flow_without_pin_code_no_hash(self) -> None:
        """Ensure no pin_code_hash is stored when pin_code is empty."""
        flow, _ = self._make_flow()
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

        await flow.async_step_init({"poll_interval": 60, "use_pin_code": False})
        stored_data = flow.async_create_entry.call_args[1]["data"]
        assert "pin_code" not in stored_data
        assert "pin_code_hash" not in stored_data

    @pytest.mark.asyncio
    async def test_options_flow_fcm_values_persisted_to_entry_data(self) -> None:
        """Non-empty FCM values move from form input into entry.data."""
        flow, entry = self._make_flow(data={"email": "x@y"})
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.hass.config_entries.async_update_entry = MagicMock()

        await flow.async_step_init(
            {
                "poll_interval": 60,
                "use_pin_code": False,
                "fcm_project_id": "proj",
                "fcm_app_id": "app",
                "fcm_api_key": "key",
                "fcm_sender_id": "123",
            }
        )

        flow.hass.config_entries.async_update_entry.assert_called_once()
        new_data = flow.hass.config_entries.async_update_entry.call_args[1]["data"]
        assert new_data["fcm_project_id"] == "proj"
        assert new_data["fcm_app_id"] == "app"
        assert new_data["fcm_api_key"] == "key"
        assert new_data["fcm_sender_id"] == "123"
        assert new_data["email"] == "x@y"
        # FCM keys must not leak into options
        stored_options = flow.async_create_entry.call_args[1]["data"]
        for k in ("fcm_project_id", "fcm_app_id", "fcm_api_key", "fcm_sender_id"):
            assert k not in stored_options

    @pytest.mark.asyncio
    async def test_options_flow_clearing_three_text_fcm_fields_removes_them(self) -> None:
        """Empty strings for the three text FCM fields remove them (#138).

        `fcm_api_key` is the password TextSelector — see
        `test_options_flow_empty_api_key_preserves_saved_value` for
        why an empty submission there is intentionally a no-op (#183).
        Only the three text fields (project_id, app_id, sender_id)
        round-trip their saved values via `suggested_value`, so a user
        actively emptying one of them is a deliberate clear.
        """
        flow, entry = self._make_flow(
            data={
                "email": "x@y",
                "fcm_project_id": "old_proj",
                "fcm_app_id": "old_app",
                "fcm_api_key": "old_key",
                "fcm_sender_id": "old_sender",
            }
        )
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.hass.config_entries.async_update_entry = MagicMock()

        await flow.async_step_init(
            {
                "poll_interval": 60,
                "use_pin_code": False,
                "fcm_project_id": "",
                "fcm_app_id": "",
                "fcm_api_key": "",
                "fcm_sender_id": "",
            }
        )

        flow.hass.config_entries.async_update_entry.assert_called_once()
        new_data = flow.hass.config_entries.async_update_entry.call_args[1]["data"]
        for k in ("fcm_project_id", "fcm_app_id", "fcm_sender_id"):
            assert k not in new_data, f"{k} should be removed when cleared"
        # fcm_api_key sticks (use the explicit toggle to wipe it).
        assert new_data["fcm_api_key"] == "old_key"
        assert new_data["email"] == "x@y"

    @pytest.mark.asyncio
    async def test_options_flow_empty_api_key_preserves_saved_value(self) -> None:
        """Empty `fcm_api_key` on resubmit must NOT wipe the saved key (#183).

        Reproduces the bug raven2k24 hit on `1.5.1`: HA's frontend
        leaves password TextSelectors blank when re-opening a form
        (it won't display saved secrets, even masked), so a benign
        re-submit — e.g. changing the poll interval — used to wipe the
        previously-saved API key because the empty submission ran
        through the `else: pop` branch. After this fix, only the
        explicit `Delete FCM credentials` toggle wipes the key.
        """
        flow, entry = self._make_flow(
            data={
                "fcm_project_id": "proj",
                "fcm_app_id": "1:99:android:abc",
                "fcm_api_key": "AIza-saved-secret",
                "fcm_sender_id": "99",
            }
        )
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.hass.config_entries.async_update_entry = MagicMock()

        await flow.async_step_init(
            {
                "poll_interval": 90,  # the change the user actually came for
                "use_pin_code": False,
                "fcm_project_id": "proj",
                "fcm_app_id": "1:99:android:abc",
                "fcm_api_key": "",  # password field: blank on re-open
                "fcm_sender_id": "99",
            }
        )

        # async_update_entry only fires when new_data != entry.data.
        # If we wipe api_key we mutate data and trigger a reload; if we
        # leave it alone, data is unchanged and no save happens. Either
        # branch must end with the saved api_key intact, so check both.
        if flow.hass.config_entries.async_update_entry.called:
            new_data = flow.hass.config_entries.async_update_entry.call_args[1]["data"]
            assert new_data["fcm_api_key"] == "AIza-saved-secret"
        else:
            assert entry.data["fcm_api_key"] == "AIza-saved-secret"

    @pytest.mark.asyncio
    async def test_options_flow_typed_api_key_still_overwrites_saved_value(self) -> None:
        """The no-op-on-empty guard only fires for empty submissions —
        if the user actually re-types the API key, the new value wins.
        """
        flow, entry = self._make_flow(
            data={
                "fcm_project_id": "proj",
                "fcm_app_id": "1:99:android:abc",
                "fcm_api_key": "AIza-old",
                "fcm_sender_id": "99",
            }
        )
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.hass.config_entries.async_update_entry = MagicMock()

        await flow.async_step_init(
            {
                "poll_interval": 60,
                "use_pin_code": False,
                "fcm_project_id": "proj",
                "fcm_app_id": "1:99:android:abc",
                "fcm_api_key": "AIza-new",
                "fcm_sender_id": "99",
            }
        )

        new_data = flow.hass.config_entries.async_update_entry.call_args[1]["data"]
        assert new_data["fcm_api_key"] == "AIza-new"

    @pytest.mark.asyncio
    async def test_options_flow_clear_fcm_toggle_removes_all_keys(self) -> None:
        """'Delete FCM credentials' toggle wipes all four keys regardless of field values (#138)."""
        flow, entry = self._make_flow(
            data={
                "email": "x@y",
                "fcm_project_id": "old_proj",
                "fcm_app_id": "old_app",
                "fcm_api_key": "old_key",
                "fcm_sender_id": "old_sender",
            }
        )
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.hass.config_entries.async_update_entry = MagicMock()

        # The toggle wins even when the form re-submits the prior FCM values
        # (selector-default round-trip case — what HA's password field does).
        await flow.async_step_init(
            {
                "poll_interval": 60,
                "use_pin_code": False,
                "fcm_project_id": "old_proj",
                "fcm_app_id": "old_app",
                "fcm_api_key": "old_key",
                "fcm_sender_id": "old_sender",
                "clear_fcm_credentials": True,
            }
        )

        flow.hass.config_entries.async_update_entry.assert_called_once()
        new_data = flow.hass.config_entries.async_update_entry.call_args[1]["data"]
        for k in ("fcm_project_id", "fcm_app_id", "fcm_api_key", "fcm_sender_id"):
            assert k not in new_data
        assert new_data["email"] == "x@y"
        # The toggle itself must not leak into entry.options
        stored_options = flow.async_create_entry.call_args[1]["data"]
        assert "clear_fcm_credentials" not in stored_options

    @pytest.mark.asyncio
    async def test_options_flow_clear_fcm_toggle_off_keeps_normal_update_path(self) -> None:
        flow, entry = self._make_flow(data={"fcm_project_id": "old"})
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.hass.config_entries.async_update_entry = MagicMock()

        await flow.async_step_init(
            {
                "poll_interval": 60,
                "use_pin_code": False,
                "fcm_project_id": "new_proj",
                "clear_fcm_credentials": False,
            }
        )

        new_data = flow.hass.config_entries.async_update_entry.call_args[1]["data"]
        assert new_data["fcm_project_id"] == "new_proj"

    @pytest.mark.asyncio
    async def test_options_flow_reloads_when_data_changes(self) -> None:
        """#148 — FCM creds in entry.data must trigger an explicit reload.

        The `_async_options_update_listener` would normally fire after
        `async_finish_flow` writes options, but when only `data` changes
        and `options` round-trip identical, the framework's second
        `async_update_entry(options=...)` short-circuits without firing
        a listener — leaving the FCM client running with stale
        credentials until the user manually reloads. Mirror the
        repair-flow pattern: await `async_reload` explicitly.
        """
        flow, entry = self._make_flow(data={"email": "x@y"})
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.hass.config_entries.async_update_entry = MagicMock()

        await flow.async_step_init(
            {
                "poll_interval": 60,
                "use_pin_code": False,
                "fcm_project_id": "proj",
                "fcm_app_id": "app",
                "fcm_api_key": "key",
                "fcm_sender_id": "123",
            }
        )

        flow.hass.config_entries.async_reload.assert_awaited_once_with("test-entry-id")

    @pytest.mark.asyncio
    async def test_options_flow_no_reload_when_data_unchanged(self) -> None:
        """Non-FCM option tweaks (poll_interval, etc.) leave entry.data alone.

        The existing `_async_options_update_listener` will still fire on
        options changes — we only want to add a second reload when data
        actually changed (the FCM-creds path). Otherwise users editing
        the polling interval would trigger redundant reloads.
        """
        flow, entry = self._make_flow(data={"email": "x@y"})
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.hass.config_entries.async_update_entry = MagicMock()

        await flow.async_step_init({"poll_interval": 90, "use_pin_code": False})

        # No data changes → async_update_entry should NOT be invoked,
        # and our explicit async_reload should NOT fire.
        flow.hass.config_entries.async_update_entry.assert_not_called()
        flow.hass.config_entries.async_reload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_options_flow_clearing_fcm_also_strips_legacy_options(self) -> None:
        """Legacy installs kept FCM in entry.options; clearing must also drop them there."""
        flow, entry = self._make_flow(
            options={
                "fcm_project_id": "legacy_proj",
                "fcm_app_id": "legacy_app",
                "fcm_api_key": "legacy_key",
                "fcm_sender_id": "legacy_sender",
            }
        )
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.hass.config_entries.async_update_entry = MagicMock()

        await flow.async_step_init(
            {
                "poll_interval": 60,
                "use_pin_code": False,
                "fcm_project_id": "",
                "fcm_app_id": "",
                "fcm_api_key": "",
                "fcm_sender_id": "",
            }
        )

        stored_options = flow.async_create_entry.call_args[1]["data"]
        for k in ("fcm_project_id", "fcm_app_id", "fcm_api_key", "fcm_sender_id"):
            assert k not in stored_options
