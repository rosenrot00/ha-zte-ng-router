"""Diagnostics support for the ZTE NG Router integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CLIENT_CONSIDER_HOME,
    CONF_FAST_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_TRACK_CLIENTS,
    CONF_VERIFY_TLS,
    DOMAIN,
)

SAFE_SETTINGS = {
    CONF_CLIENT_CONSIDER_HOME: int,
    CONF_FAST_SCAN_INTERVAL: int,
    CONF_SCAN_INTERVAL: int,
    CONF_TRACK_CLIENTS: bool,
    CONF_VERIFY_TLS: bool,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return redacted connection diagnostics; raw router payloads are omitted."""
    store = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    api = store.get("api")
    coordinator = store.get("coordinator")
    coordinator_fast = store.get("coordinator_fast")

    slow_data = (coordinator.data if coordinator is not None else None) or {}
    firmware = {}
    for source in (slow_data.get("device"), slow_data.get("common_config")):
        if isinstance(source, dict):
            for key in ("model", "model_name", "hardware_version", "wa_inner_version"):
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    firmware[key] = value

    # Never export a raw config entry: even its host URL can carry credentials.
    settings = entry.data | entry.options

    return {
        "config": {
            key: settings[key] for key, value_type in SAFE_SETTINGS.items()
            if type(settings.get(key)) is value_type
        },
        "connection": {
            "login_method": api.login_method if api is not None else None,
            "session_status": (
                "authenticated" if api is not None and api.is_logged_in else "not_authenticated"
            ),
            "slow_polling_success": (
                coordinator.last_update_success if coordinator is not None else None
            ),
            "fast_polling_success": (
                coordinator_fast.last_update_success if coordinator_fast is not None else None
            ),
            "firmware": firmware,
        },
    }
