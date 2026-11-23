from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback

from .const import (
    DOMAIN,
    CONF_NAME,
    CONF_VERIFY_TLS,
    CONF_TLS_FINGERPRINT,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    CONF_TRACK_CLIENTS,
    CONF_CLIENT_CONSIDER_HOME,
    DEFAULT_CLIENT_CONSIDER_HOME,
    CONF_SMS_EVENT_CONTENT,
    CONF_SMS_LEGACY_PREVIEW,
)
from .zte_api import ZteAuthError, ZteRouterApi, ZteTlsError, normalize_tls_fingerprint

_LOGGER = logging.getLogger(__name__)


async def _async_validate_login(
    hass: HomeAssistant, *, host: str, password: str, verify_tls: bool,
    tls_fingerprint: str = "",
) -> str | None:
    """Try a real login. Return an error key, or None when the router accepts it."""
    api = None
    try:
        api = ZteRouterApi(
            hass=hass, base_url=host, password=password, verify_tls=verify_tls,
            tls_fingerprint=tls_fingerprint,
        )
        await api.async_init_session()
        await api.async_login()
        await api.async_end_validation_session()
        return None
    except ZteAuthError:
        return "invalid_auth"
    except ZteTlsError as exc:
        return exc.reason
    except Exception as exc:  # noqa: BLE001 - any other failure: router not usable
        # Do not log the exception text; it may echo router response content.
        _LOGGER.debug("Router login check failed: %s", type(exc).__name__)
        return "cannot_connect"
    finally:
        if api is not None:
            await api.async_close()


class ZteNgRouterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for ZTE NG Router."""

    VERSION = 1
    # Reloading is driven by the update listener in __init__.py, so Home Assistant
    # must not reload again after the options flow finishes.
    options_flow_reloads = False

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

            login_error: str | None = None
            fingerprint = ""
            try:
                fingerprint = normalize_tls_fingerprint(user_input.get(CONF_TLS_FINGERPRINT, ""), host)
            except ValueError as exc:
                errors[CONF_TLS_FINGERPRINT] = str(exc)
            if not errors and MIN_SCAN_INTERVAL <= scan_interval <= MAX_SCAN_INTERVAL:
                login_error = await _async_validate_login(
                    self.hass,
                    host=host,
                    password=user_input[CONF_PASSWORD],
                    verify_tls=user_input[CONF_VERIFY_TLS],
                    tls_fingerprint=fingerprint,
                )

            if scan_interval < MIN_SCAN_INTERVAL or scan_interval > MAX_SCAN_INTERVAL:
                errors["base"] = "invalid_scan_interval"
            elif login_error:
                errors["base"] = login_error
            elif not errors:
                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_NAME: name,
                        CONF_HOST: host,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_VERIFY_TLS: user_input[CONF_VERIFY_TLS],
                        CONF_TLS_FINGERPRINT: fingerprint,
                        CONF_SCAN_INTERVAL: scan_interval,
                        CONF_TRACK_CLIENTS: user_input.get(CONF_TRACK_CLIENTS, False),
                        CONF_CLIENT_CONSIDER_HOME: user_input.get(
                            CONF_CLIENT_CONSIDER_HOME, DEFAULT_CLIENT_CONSIDER_HOME
                        ),
                    },
                )

        # Keep what the user typed (except the password) after a failed attempt.
        previous = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_NAME, default=previous.get(CONF_NAME, "ZTE NG Router")
                ): str,
                vol.Required(
                    CONF_HOST, default=previous.get(CONF_HOST, "http://192.168.0.1")
                ): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(
                    CONF_VERIFY_TLS, default=previous.get(CONF_VERIFY_TLS, False)
                ): bool,
                vol.Optional(
                    CONF_TLS_FINGERPRINT, default=previous.get(CONF_TLS_FINGERPRINT, "")
                ): str,
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=previous.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
                vol.Optional(
                    CONF_TRACK_CLIENTS, default=previous.get(CONF_TRACK_CLIENTS, False)
                ): bool,
                vol.Optional(
                    CONF_CLIENT_CONSIDER_HOME,
                    default=previous.get(
                        CONF_CLIENT_CONSIDER_HOME, DEFAULT_CLIENT_CONSIDER_HOME
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        """Start reauthentication after the stored password stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        """Ask for the router password again and verify it with a live login."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            # Same precedence as setup: options override the original entry data.
            host = entry.options.get(CONF_HOST, entry.data[CONF_HOST])
            verify_tls = entry.options.get(
                CONF_VERIFY_TLS, entry.data.get(CONF_VERIFY_TLS, False)
            )
            password = user_input[CONF_PASSWORD]
            login_error = await _async_validate_login(
                self.hass, host=host, password=password, verify_tls=verify_tls,
                tls_fingerprint=entry.options.get(CONF_TLS_FINGERPRINT, entry.data.get(CONF_TLS_FINGERPRINT, "")),
            )
            if login_error:
                errors["base"] = login_error
            else:
                # Store the new password in data and drop a stale copy in options,
                # which would otherwise keep taking precedence.
                new_options = {
                    key: value
                    for key, value in entry.options.items()
                    if key != CONF_PASSWORD
                }
                return self.async_update_reload_and_abort(
                    entry,
                    data={**entry.data, CONF_PASSWORD: password},
                    options=new_options,
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"name": entry.title},
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
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                fingerprint = normalize_tls_fingerprint(
                    user_input.get(CONF_TLS_FINGERPRINT, self._config_entry.options.get(
                        CONF_TLS_FINGERPRINT, self._config_entry.data.get(CONF_TLS_FINGERPRINT, "")
                    )),
                    user_input[CONF_HOST],
                )
            except ValueError as exc:
                errors[CONF_TLS_FINGERPRINT] = str(exc)
        if user_input is not None and not errors:
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
            existing[CONF_TLS_FINGERPRINT] = fingerprint
            existing[CONF_SCAN_INTERVAL] = user_input[CONF_SCAN_INTERVAL]
            existing[CONF_TRACK_CLIENTS] = user_input.get(CONF_TRACK_CLIENTS, False)
            existing[CONF_CLIENT_CONSIDER_HOME] = user_input.get(
                CONF_CLIENT_CONSIDER_HOME, DEFAULT_CLIENT_CONSIDER_HOME
            )
            for key in (CONF_SMS_EVENT_CONTENT, CONF_SMS_LEGACY_PREVIEW):
                existing[key] = user_input.get(key, existing.get(key, False))

            return self.async_create_entry(title="", data=existing)

        # Current values: prefer options, fallback to data
        data = self._config_entry.data
        options = dict(self._config_entry.options)
        options.update({key: value for key, value in (user_input or {}).items() if key != CONF_PASSWORD})

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
                vol.Optional(
                    CONF_TLS_FINGERPRINT,
                    default=options.get(CONF_TLS_FINGERPRINT, data.get(CONF_TLS_FINGERPRINT, "")),
                ): str,
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=current_scan_interval,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
                vol.Optional(
                    CONF_TRACK_CLIENTS,
                    default=options.get(CONF_TRACK_CLIENTS, data.get(CONF_TRACK_CLIENTS, False)),
                ): bool,
                vol.Optional(
                    CONF_CLIENT_CONSIDER_HOME,
                    default=options.get(CONF_CLIENT_CONSIDER_HOME, data.get(
                        CONF_CLIENT_CONSIDER_HOME, DEFAULT_CLIENT_CONSIDER_HOME
                    )),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
                vol.Optional(CONF_SMS_EVENT_CONTENT, default=options.get(CONF_SMS_EVENT_CONTENT, False)): bool,
                vol.Optional(CONF_SMS_LEGACY_PREVIEW, default=options.get(CONF_SMS_LEGACY_PREVIEW, False)): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
