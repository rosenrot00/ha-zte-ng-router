from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
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
        update_interval=None,
    )

    coordinator_fast = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"zte_ng_router_{name}_fast",
        update_method=_async_update_fast,
        update_interval=None,
    )

    store = hass.data[DOMAIN][entry.entry_id]
    poll_unsubs: list[Any] = []

    def _cancel_polling() -> None:
        """Cancel integration-owned polling timers."""
        while poll_unsubs:
            poll_unsubs.pop()()

    def _schedule_polling() -> None:
        """Start integration-owned polling timers."""
        _cancel_polling()

        async def _refresh_full(_now: datetime) -> None:
            await coordinator.async_refresh()

        async def _refresh_fast(_now: datetime) -> None:
            await coordinator_fast.async_refresh()

        poll_unsubs.extend(
            (
                async_track_time_interval(
                    hass,
                    _refresh_full,
                    timedelta(seconds=scan_interval),
                ),
                async_track_time_interval(
                    hass,
                    _refresh_fast,
                    timedelta(seconds=fast_scan_interval),
                ),
            )
        )

    try:
        await coordinator.async_config_entry_first_refresh()
        await coordinator_fast.async_config_entry_first_refresh()

        store.update(
            {
                "api": api,
                "coordinator": coordinator,
                "coordinator_fast": coordinator_fast,
                "name": name,
                "pause_until": store.get("pause_until"),
                "cancel_polling": _cancel_polling,
                "schedule_polling": _schedule_polling,
            }
        )

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _schedule_polling()
        return True
    except Exception:
        _cancel_polling()
        try:
            await api.async_close()
        finally:
            hass.data[DOMAIN].pop(entry.entry_id, None)
        raise


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    cancel_polling = data.get("cancel_polling")
    if callable(cancel_polling):
        cancel_polling()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None) or {}
        api = data.get("api")
        if api is not None:
            await api.async_close()
    else:
        schedule_polling = data.get("schedule_polling")
        if callable(schedule_polling):
            schedule_polling()
    return unload_ok
