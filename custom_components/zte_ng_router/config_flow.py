from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import callback

from .const import (
    DOMAIN,
    CONF_NAME,
    CONF_ROUTER_TYPE,
    CONF_VERIFY_TLS,
    CONF_SCAN_INTERVAL,
    ROUTER_TYPES,
    DEFAULT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class ZteNgRouterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for ZTE NG Router."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step where the user enters basic config."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            name = user_input[CONF_NAME]
            scan_interval = user_input[CONF_SCAN_INTERVAL]

            # Avoid duplicate configurations for the same host
            await self.async_set_unique_id(f"zte_ng_router_{host}")
            self._abort_if_unique_id_configured()

            if scan_interval < MIN_SCAN_INTERVAL or scan_interval > MAX_SCAN_INTERVAL:
                errors["base"] = "invalid_scan_interval"
            else:
                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_NAME: name,
                        CONF_HOST: host,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_ROUTER_TYPE: user_input[CONF_ROUTER_TYPE],
                        CONF_VERIFY_TLS: user_input[CONF_VERIFY_TLS],
                        CONF_SCAN_INTERVAL: scan_interval,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="ZTE NG Router"): str,
                vol.Required(CONF_HOST, default="http://192.168.0.1"): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_ROUTER_TYPE, default="g5tc"): vol.In(ROUTER_TYPES),
                vol.Optional(CONF_VERIFY_TLS, default=False): bool,
                vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Return the options flow handler."""
        return ZteNgRouterOptionsFlow(config_entry)


class ZteNgRouterOptionsFlow(config_entries.OptionsFlow):
    """Options flow for adjusting settings after initial setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Store the config entry in a private attribute."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Handle options for the config entry (host, password, verify_tls, scan_interval)."""
        if user_input is not None:
            # Build merged options: start from existing options, then override
            existing = dict(self._config_entry.options)

            # Host is always taken from the form
            existing[CONF_HOST] = user_input[CONF_HOST]

            # Password: only override if user entered something
            new_password = user_input.get(CONF_PASSWORD, "")
            if new_password.strip():
                existing[CONF_PASSWORD] = new_password
            # else: keep existing password (from options or data via fallback in __init__.py)

            # TLS and scan interval always from the form
            existing[CONF_VERIFY_TLS] = user_input[CONF_VERIFY_TLS]
            existing[CONF_SCAN_INTERVAL] = user_input[CONF_SCAN_INTERVAL]

            return self.async_create_entry(title="", data=existing)

        # Current values: prefer options, fallback to data
        data = self._config_entry.data
        options = self._config_entry.options

        current_host = options.get(CONF_HOST, data.get(CONF_HOST, "http://192.168.0.1"))
        # We never show the current password in plain text; user can overwrite it if needed.
        current_verify_tls = options.get(
            CONF_VERIFY_TLS,
            data.get(CONF_VERIFY_TLS, False),
        )
        current_scan_interval = options.get(
            CONF_SCAN_INTERVAL,
            data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST,
                    default=current_host,
                ): str,
                vol.Optional(
                    CONF_PASSWORD,
                    default="",
                ): str,
                vol.Required(
                    CONF_VERIFY_TLS,
                    default=current_verify_tls,
                ): bool,
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=current_scan_interval,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
