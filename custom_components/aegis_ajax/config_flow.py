"""Config flow for Ajax Security integration."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from custom_components.aegis_ajax.api.client import AjaxGrpcClient
from custom_components.aegis_ajax.api.session import (
    AjaxSession,
    AuthenticationError,
    TwoFactorRequiredError,
)
from custom_components.aegis_ajax.api.spaces import SpacesApi
from custom_components.aegis_ajax.const import (
    APPLICATION_LABEL,
    CONF_AUTO_CREATE_LABELS,
    CONF_FORCE_ARM,
    CONF_PHOTO_MAX_PER_DEVICE,
    CONF_PHOTO_RETENTION_DAYS,
    DEFAULT_AUTO_CREATE_LABELS,
    DEFAULT_PHOTO_MAX_PER_DEVICE,
    DEFAULT_PHOTO_RETENTION_DAYS,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    KNOWN_APP_LABELS,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required("email"): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
        vol.Required("password"): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        vol.Optional("app_label", default=APPLICATION_LABEL): SelectSelector(
            SelectSelectorConfig(options=KNOWN_APP_LABELS, custom_value=True, sort=True)
        ),
    }
)

TOTP_SCHEMA = vol.Schema(
    {
        vol.Required("totp_code"): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
    }
)


class AjaxCobrandedConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ajax Security."""

    VERSION = 2
    DOMAIN = DOMAIN

    def __init__(self) -> None:
        self._client: AjaxGrpcClient | None = None
        self._email: str = ""
        self._password_hash: str = ""
        self._app_label: str = APPLICATION_LABEL
        self._request_id: str = ""

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """Entry point when an Ajax hub is seen on the local network.

        HA invokes this when a DHCP packet matches the manifest's `dhcp`
        spec (Ajax Systems OUI 9C:75:6E). The flow doesn't have
        credentials at discovery time — its only job is to surface
        Aegis as a "Discovered" card with a hint so the user clicks
        through into the credential prompt instead of having to search
        for the integration by name.
        """
        # Per-MAC unique_id on the *flow* (not the eventual entry, which
        # uses the email) so HA dedupes repeat DHCP packets and avoids
        # showing two discovery cards for the same hub.
        await self.async_set_unique_id(format_mac(discovery_info.macaddress))
        self._abort_if_unique_id_configured()
        # Once the user has at least one Aegis account configured we
        # don't keep nagging on every hub renewal — additional spaces
        # under the same account already appear automatically.
        if self._async_current_entries(include_ignore=False):
            return self.async_abort(reason="already_configured")
        # Title placeholder is what HA renders on the "Discovered" card.
        self.context["title_placeholders"] = {
            "name": discovery_info.hostname or f"Ajax hub ({discovery_info.ip})"
        }
        return await self.async_step_user()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = user_input["email"]
            self._password_hash = AjaxSession.hash_password(user_input["password"])
            self._app_label = user_input.get("app_label", APPLICATION_LABEL)
            _LOGGER.debug("Config flow: app_label=%s", self._app_label)
            await self.async_set_unique_id(self._email)
            self._abort_if_unique_id_configured()
            try:
                self._client = AjaxGrpcClient(
                    email=self._email,
                    password_hash=self._password_hash,
                    app_label=self._app_label,
                )
                await self._client.connect()
                await asyncio.wait_for(self._client.login(), timeout=30)
                return await self.async_step_select_spaces()
            except TwoFactorRequiredError as e:
                self._request_id = e.request_id
                return await self.async_step_2fa()
            except AuthenticationError as e:
                _LOGGER.error("Authentication failed: %s", e)
                errors["base"] = "invalid_auth"
            except (ConnectionError, OSError) as e:
                _LOGGER.error("Connection failed: %s", e)
                errors["base"] = "cannot_connect"
            except TimeoutError:
                _LOGGER.error("Login timed out")
                errors["base"] = "cannot_connect"
            except asyncio.CancelledError:
                _LOGGER.error("Login was cancelled")
                errors["base"] = "unknown"
            except Exception as e:
                _LOGGER.error(
                    "Unexpected error during login: %s: %s",
                    type(e).__name__,
                    e,
                    exc_info=True,
                )
                errors["base"] = "unknown"
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_2fa(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                if self._client is None:
                    raise RuntimeError("Client not initialized")
                await asyncio.wait_for(
                    self._client.login_totp(
                        email=self._email,
                        request_id=self._request_id,
                        totp_code=user_input["totp_code"],
                    ),
                    timeout=30,
                )
                return await self.async_step_select_spaces()
            except AuthenticationError:
                errors["base"] = "invalid_totp"
            except TimeoutError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during 2FA")
                errors["base"] = "unknown"
        return self.async_show_form(step_id="2fa", data_schema=TOTP_SCHEMA, errors=errors)

    async def async_step_select_spaces(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            if self._client is None:
                raise RuntimeError("Client not initialized")
            data: dict[str, Any] = {
                "email": self._email,
                "password_hash": self._password_hash,
                "app_label": self._app_label,
                "spaces": user_input["spaces"],
                "device_id": self._client.session.device_id,
            }
            # Persist session token to avoid re-login (and 2FA) on restart
            if self._client.session.session_token:
                data["session_token"] = self._client.session.session_token
                data["user_hex_id"] = self._client.session.user_hex_id
            return self.async_create_entry(title=f"Ajax Security ({self._email})", data=data)
        if self._client:
            spaces_api = SpacesApi(self._client)
            spaces = await spaces_api.list_spaces()
            space_options = [SelectOptionDict(value=s.id, label=s.name) for s in spaces]
        else:
            space_options = []
        return self.async_show_form(
            step_id="select_spaces",
            data_schema=vol.Schema(
                {
                    vol.Required("spaces"): SelectSelector(
                        SelectSelectorConfig(
                            options=space_options,
                            multiple=True,
                        )
                    ),
                }
            ),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration (change credentials)."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            self._email = user_input["email"]
            self._password_hash = AjaxSession.hash_password(user_input["password"])
            self._app_label = user_input.get(
                "app_label", entry.data.get("app_label", APPLICATION_LABEL)
            )
            try:
                self._client = AjaxGrpcClient(
                    email=self._email,
                    password_hash=self._password_hash,
                    app_label=self._app_label,
                )
                await self._client.connect()
                await asyncio.wait_for(self._client.login(), timeout=30)
                await self._client.close()
                return await self._async_finish_reconfigure()
            except TwoFactorRequiredError as e:
                self._request_id = e.request_id
                return await self.async_step_reconfigure_2fa()
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except (ConnectionError, OSError, TimeoutError):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during reconfigure")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required("email", default=entry.data.get("email", "")): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.EMAIL)
                    ),
                    vol.Required("password"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(
                        "app_label",
                        default=entry.data.get("app_label", APPLICATION_LABEL),
                    ): SelectSelector(
                        SelectSelectorConfig(options=KNOWN_APP_LABELS, custom_value=True, sort=True)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure_2fa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle 2FA during reconfiguration."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                if self._client is None:
                    raise RuntimeError("Client not initialized")
                await asyncio.wait_for(
                    self._client.login_totp(
                        email=self._email,
                        request_id=self._request_id,
                        totp_code=user_input["totp_code"],
                    ),
                    timeout=30,
                )
                await self._client.close()
                return await self._async_finish_reconfigure()
            except AuthenticationError:
                errors["base"] = "invalid_totp"
            except TimeoutError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during reconfigure 2FA")
                errors["base"] = "unknown"
        return self.async_show_form(
            step_id="reconfigure_2fa", data_schema=TOTP_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Entry point when HA detects auth has gone stale.

        Triggered by the coordinator raising ``ConfigEntryAuthFailed``;
        HA shows the orange "Reconfigure" banner that runs this flow.
        """
        self._email = str(entry_data.get("email", ""))
        self._app_label = str(entry_data.get("app_label", APPLICATION_LABEL))
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-prompt for the password (and 2FA if required) keeping the same entry."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            self._password_hash = AjaxSession.hash_password(user_input["password"])
            try:
                self._client = AjaxGrpcClient(
                    email=self._email,
                    password_hash=self._password_hash,
                    app_label=self._app_label,
                )
                await self._client.connect()
                await asyncio.wait_for(self._client.login(), timeout=30)
                await self._client.close()
                return await self._async_finish_reauth()
            except TwoFactorRequiredError as e:
                self._request_id = e.request_id
                return await self.async_step_reauth_2fa()
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except (ConnectionError, OSError, TimeoutError):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("password"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            description_placeholders={"email": entry.data.get("email", self._email)},
            errors=errors,
        )

    async def async_step_reauth_2fa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle 2FA during reauth."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                if self._client is None:
                    raise RuntimeError("Client not initialized")
                await asyncio.wait_for(
                    self._client.login_totp(
                        email=self._email,
                        request_id=self._request_id,
                        totp_code=user_input["totp_code"],
                    ),
                    timeout=30,
                )
                await self._client.close()
                return await self._async_finish_reauth()
            except AuthenticationError:
                errors["base"] = "invalid_totp"
            except TimeoutError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during reauth 2FA")
                errors["base"] = "unknown"
        return self.async_show_form(step_id="reauth_2fa", data_schema=TOTP_SCHEMA, errors=errors)

    async def _async_finish_reauth(self) -> ConfigFlowResult:
        """Persist the refreshed credentials onto the existing entry and reload."""
        entry = self._get_reauth_entry()
        new_data: dict[str, Any] = {
            **entry.data,
            "password_hash": self._password_hash,
            "app_label": self._app_label,
        }
        if self._client and self._client.session.session_token:
            new_data["session_token"] = self._client.session.session_token
            new_data["user_hex_id"] = self._client.session.user_hex_id
        return self.async_update_reload_and_abort(entry, data=new_data, reason="reauth_successful")

    async def _async_finish_reconfigure(self) -> ConfigFlowResult:
        """Persist new credentials and session token, then reload."""
        entry = self._get_reconfigure_entry()
        if self._email != entry.unique_id:
            await self.async_set_unique_id(self._email)
            self._abort_if_unique_id_configured(updates={"email": self._email})
        new_data: dict[str, Any] = {
            **entry.data,
            "email": self._email,
            "password_hash": self._password_hash,
            "app_label": self._app_label,
        }
        if self._client and self._client.session.session_token:
            new_data["session_token"] = self._client.session.session_token
            new_data["user_hex_id"] = self._client.session.user_hex_id
        return self.async_update_reload_and_abort(entry, data=new_data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AjaxCobrandedOptionsFlow:
        return AjaxCobrandedOptionsFlow(config_entry)


_FCM_KEYS = {"fcm_project_id", "fcm_app_id", "fcm_api_key", "fcm_sender_id"}


class AjaxCobrandedOptionsFlow(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        super().__init__()
        self._entry = config_entry

    def _get_fcm(self, key: str) -> str:
        """Read FCM credential from data (preferred) or legacy options."""
        return str(self._entry.data.get(key, self._entry.options.get(key, "")))

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if "poll_interval" in user_input:
                user_input["poll_interval"] = max(
                    MIN_POLL_INTERVAL, min(MAX_POLL_INTERVAL, user_input["poll_interval"])
                )
            if user_input.get("pin_code"):
                user_input["pin_code_hash"] = hashlib.sha256(
                    user_input.pop("pin_code").encode()
                ).hexdigest()
            else:
                user_input.pop("pin_code", None)
            # Move FCM credentials into config_entry.data (encrypted storage)
            fcm_data = {k: user_input.pop(k) for k in _FCM_KEYS if k in user_input}
            if any(fcm_data.values()):
                self.hass.config_entries.async_update_entry(
                    self._entry,
                    data={**self._entry.data, **fcm_data},
                )
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "poll_interval",
                        default=self._entry.options.get("poll_interval", DEFAULT_POLL_INTERVAL),
                    ): vol.All(int, vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL)),
                    vol.Optional(
                        CONF_FORCE_ARM,
                        default=self._entry.options.get(CONF_FORCE_ARM, False),
                    ): bool,
                    vol.Optional(
                        "use_pin_code",
                        default=self._entry.options.get("use_pin_code", False),
                    ): bool,
                    vol.Optional("pin_code"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(
                        "fcm_project_id",
                        default=self._get_fcm("fcm_project_id"),
                    ): str,
                    vol.Optional(
                        "fcm_app_id",
                        default=self._get_fcm("fcm_app_id"),
                    ): str,
                    vol.Optional(
                        "fcm_api_key",
                        default=self._get_fcm("fcm_api_key"),
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                    vol.Optional(
                        "fcm_sender_id",
                        default=self._get_fcm("fcm_sender_id"),
                    ): str,
                    vol.Optional(
                        CONF_PHOTO_RETENTION_DAYS,
                        default=self._entry.options.get(
                            CONF_PHOTO_RETENTION_DAYS, DEFAULT_PHOTO_RETENTION_DAYS
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
                    vol.Optional(
                        CONF_PHOTO_MAX_PER_DEVICE,
                        default=self._entry.options.get(
                            CONF_PHOTO_MAX_PER_DEVICE, DEFAULT_PHOTO_MAX_PER_DEVICE
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=10000)),
                    vol.Optional(
                        CONF_AUTO_CREATE_LABELS,
                        default=self._entry.options.get(
                            CONF_AUTO_CREATE_LABELS, DEFAULT_AUTO_CREATE_LABELS
                        ),
                    ): bool,
                }
            ),
        )
