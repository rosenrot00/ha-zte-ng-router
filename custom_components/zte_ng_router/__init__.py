from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.helpers.config_validation as cv

from homeassistant.const import CONF_HOST, CONF_PASSWORD

from .const import (
    DOMAIN,
    CONF_NAME,
    CONF_ROUTER_TYPE,
    CONF_VERIFY_TLS,
    CONF_SCAN_INTERVAL,
    CONF_FAST_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_FAST_SCAN_INTERVAL,
)
from .zte_api import ZteRouterApi

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[str] = ["sensor", "button", "switch", "text"]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration from YAML (not used, config flow only)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ZTE NG Router from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Ensure store exists early (coordinators may refresh before we finish setup)
    hass.data[DOMAIN].setdefault(
        entry.entry_id,
        {"pause_until": None},
    )

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
    fast_scan_interval: int = options.get(
        CONF_FAST_SCAN_INTERVAL,
        data.get(CONF_FAST_SCAN_INTERVAL, DEFAULT_FAST_SCAN_INTERVAL),
    )

    api = ZteRouterApi(
        hass=hass,
        base_url=host,
        password=password,
        router_type=router_type,
        verify_tls=verify_tls,
    )

    async def _async_update_data() -> dict[str, Any]:
        """Fetch data from the router."""
        pause_until: datetime | None = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("pause_until")
        if pause_until is not None and dt_util.utcnow() < pause_until:
            # Return last known data without polling
            return coordinator.data or {}
        try:
            data = await api.async_update_all()
            if data is None:
                raise UpdateFailed("No data returned from router")
            return data
        except Exception as err:
            _LOGGER.error("Error updating ZTE router data: %s", err)
            raise UpdateFailed(str(err)) from err

    async def _async_update_fast() -> dict[str, Any]:
        """Fetch fast-changing WAN stats from the router."""
        pause_until: datetime | None = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("pause_until")
        if pause_until is not None and dt_util.utcnow() < pause_until:
            # Return last known fast data without polling
            return coordinator_fast.data or {}
        try:
            fast_data = await api.async_update_fast()
            if fast_data is None:
                raise UpdateFailed("No fast data returned from router")
            return fast_data
        except Exception as err:
            _LOGGER.error("Error updating ZTE router fast data: %s", err)
            raise UpdateFailed(str(err)) from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"zte_ng_router_{name}",
        update_method=_async_update_data,
        update_interval=timedelta(seconds=scan_interval),
    )

    coordinator_fast = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"zte_ng_router_{name}_fast",
        update_method=_async_update_fast,
        update_interval=timedelta(seconds=fast_scan_interval),
    )

    await coordinator.async_config_entry_first_refresh()
    await coordinator_fast.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id].update(
        {
            "api": api,
            "coordinator": coordinator,
            "coordinator_fast": coordinator_fast,
            "name": name,
            "pause_until": hass.data[DOMAIN][entry.entry_id].get("pause_until"),
        }
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None) or {}
        api = data.get("api")
        if api is not None:
            await api.async_close()
    return unload_ok
