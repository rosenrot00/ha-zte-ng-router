"""Diagnostics support for the ZTE NG Router integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT = {
    CONF_PASSWORD,
    "password",
    "cookie",
    "cookies",
    "token",
    "access_token",
    "session",
    "session_id",
    "ubus_rpc_session",
    "webtoken",
    "csrf",
    "csrf_token",
    "salt",
    "zte_web_sault",
    "latitude",
    "longitude",
    "phone_number",
    "number",
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
    firmware_data = slow_data.get("common_config") or slow_data.get("device") or {}
    firmware = {
        key: firmware_data[key]
        for key in ("model", "model_name", "hardware_version", "wa_inner_version")
        if isinstance(firmware_data, dict) and firmware_data.get(key) is not None
    }

    return {
        "config": async_redact_data(
            {"data": dict(entry.data), "options": dict(entry.options)},
            TO_REDACT,
        ),
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
