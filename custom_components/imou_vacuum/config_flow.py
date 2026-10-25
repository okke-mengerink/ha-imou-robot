"""Config flow for the Imou Vacuum (Open Platform) integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import ImouAuthError, ImouClient, ImouConnectionError, ImouError
from .const import (
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_REGION,
    DEFAULT_REGION,
    DOMAIN,
    REGION_HOSTS,
)

_LOGGER = logging.getLogger(__name__)

REGION_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=list(REGION_HOSTS),
        translation_key=CONF_REGION,
        mode=SelectSelectorMode.DROPDOWN,
    )
)
SECRET_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_APP_ID): TextSelector(),
        vol.Required(CONF_APP_SECRET): SECRET_SELECTOR,
        vol.Required(CONF_REGION, default=DEFAULT_REGION): REGION_SELECTOR,
    }
)
STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_APP_SECRET): SECRET_SELECTOR})


async def _async_validate(
    hass: HomeAssistant, app_id: str, app_secret: str, region: str
) -> None:
    """Validate the credentials by requesting an access token."""
    client = ImouClient(
        async_get_clientsession(hass), app_id, app_secret, REGION_HOSTS[region]
    )
    await client.async_validate()


def _error_for(err: Exception) -> str:
    """Map a validation failure to a translated error key."""
    if isinstance(err, ImouConnectionError):
        return "cannot_connect"
    if isinstance(err, ImouAuthError):
        return "invalid_auth"
    return "unknown"


class ImouRobotConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Imou Vacuum (Open Platform)."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the App ID, App Secret and region."""
        errors: dict[str, str] = {}
        if user_input is not None:
            app_id = user_input[CONF_APP_ID].strip()
            app_secret = user_input[CONF_APP_SECRET].strip()
            region = user_input[CONF_REGION]
            await self.async_set_unique_id(app_id)
            self._abort_if_unique_id_configured()
            try:
                await _async_validate(self.hass, app_id, app_secret, region)
            except ImouError as err:
                errors["base"] = _error_for(err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error while validating Imou credentials")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title="Imou Vacuum (Open Platform)",
                    data={
                        CONF_APP_ID: app_id,
                        CONF_APP_SECRET: app_secret,
                        CONF_REGION: region,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA,
                {k: v for k, v in (user_input or {}).items() if k != CONF_APP_SECRET},
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication when Imou rejects the credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new App Secret."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            app_secret = user_input[CONF_APP_SECRET].strip()
            try:
                await _async_validate(
                    self.hass,
                    entry.data[CONF_APP_ID],
                    app_secret,
                    entry.data.get(CONF_REGION, DEFAULT_REGION),
                )
            except ImouError as err:
                errors["base"] = _error_for(err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error while validating Imou credentials")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_APP_SECRET: app_secret}
                )

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=STEP_REAUTH_SCHEMA, errors=errors
        )
