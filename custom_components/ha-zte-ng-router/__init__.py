from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from homeassistant.const import CONF_HOST, CONF_PASSWORD

from .const import (
    DOMAIN,
    CONF_NAME,
    CONF_ROUTER_TYPE,
    CONF_VERIFY_TLS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
)
from .zte_api import ZteRouterApi

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor"]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration from YAML (not used, config flow only)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ZTE NG Router from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    data = entry.data
    options = entry.options

    name: str = data[CONF_NAME]
    router_type: str = data.get(CONF_ROUTER_TYPE, "g5tc")

    # Use options if available, otherwise fall back to data
    host: str = options.get(CONF_HOST, data[CONF_HOST])
    password: str = options.get(CONF_PASSWORD, data[CONF_PASSWORD])
    verify_tls: bool = options.get(
        CONF_VERIFY_TLS,
        data.get(CONF_VERIFY_TLS, False),
    )
    scan_interval: int = options.get(
        CONF_SCAN_INTERVAL,
        data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )

    api = ZteRouterApi(
        base_url=host,
        password=password,
        router_type=router_type,
        verify_tls=verify_tls,
    )

    async def _async_update_data() -> dict[str, Any]:
        """Fetch data from the router in an executor thread."""
        try:
            data = await hass.async_add_executor_job(api.update_all)
            if data is None:
                raise UpdateFailed("No data returned from router")
            return data
        except Exception as err:
            _LOGGER.error("Error updating ZTE router data: %s", err)
            raise UpdateFailed(str(err)) from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"zte_ng_router_{name}",
        update_method=_async_update_data,
        update_interval=timedelta(seconds=scan_interval),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "name": name,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
