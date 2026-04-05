from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from homeassistant.util import dt as dt_util

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .zte_api import ZteRouterApi

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ZteActionSwitchDef:
    key: str
    name: str
    icon: str
    state_key: str
    on_value: str
    off_value: str
    get_path: Callable[[dict[str, Any]], dict[str, Any]]
    turn_on: dict[str, Any]
    turn_off: dict[str, Any]


SWITCH_DEFS: list[ZteActionSwitchDef] = [
    ZteActionSwitchDef(
        key="odu_led",
        name="LED",
        icon="mdi:led-on",
        state_key="switch",
        on_value="1",
        off_value="0",
        # where to read state from coordinator.data
        get_path=lambda data: data.get("odu_led", {}),
        turn_on={
            "service": "zwrt_led",
            "method": "set_ODU_switch_state",
            "params": {"switch": "1", "offtime": "15"},
        },
        turn_off={
            "service": "zwrt_led",
            "method": "set_ODU_switch_state",
            "params": {"switch": "0", "offtime": "15"},
        },
    ),
    ZteActionSwitchDef(
        key="wifi_master",
        name="WiFi",
        icon="mdi:wifi",
        state_key="wifi_onoff",
        on_value="1",
        off_value="0",
        # master state from zwrt_wlan/report (already stored as coordinator.data["wlan"])
        get_path=lambda data: data.get("wlan", {}),
        turn_on={
            "service": "zwrt_wlan",
            "method": "set",
            "params": {"zte_mbb": {"wifi_onoff": "1"}},
        },
        turn_off={
            "service": "zwrt_wlan",
            "method": "set",
            "params": {"zte_mbb": {"wifi_onoff": "0"}},
        },
    ),
    ZteActionSwitchDef(
        key="wifi_main_2g",
        name="WiFi 2 GHz",
        icon="mdi:wifi",
        state_key="disabled",
        # UCI uses disabled=0 (enabled) and disabled=1 (disabled)
        on_value="0",
        off_value="1",
        get_path=lambda data: data.get("wifi_main_2g", {}),
        turn_on={
            "service": "zwrt_wlan",
            "method": "set",
            "params": {"main_2g": {"disabled": "0"}},
        },
        turn_off={
            "service": "zwrt_wlan",
            "method": "set",
            "params": {"main_2g": {"disabled": "1"}},
        },
    ),
    ZteActionSwitchDef(
        key="wifi_main_5g",
        name="WiFi 5 GHz",
        icon="mdi:wifi",
        state_key="disabled",
        # UCI uses disabled=0 (enabled) and disabled=1 (disabled)
        on_value="0",
        off_value="1",
        get_path=lambda data: data.get("wifi_main_5g", {}),
        turn_on={
            "service": "zwrt_wlan",
            "method": "set",
            "params": {"main_5g": {"disabled": "0"}},
        },
        turn_off={
            "service": "zwrt_wlan",
            "method": "set",
            "params": {"main_5g": {"disabled": "1"}},
        },
    ),
    ZteActionSwitchDef(
        key="mobile_data",
        name="Mobile Data",
        icon="mdi:signal-cellular-3",
        state_key="enable",
        on_value="1",
        off_value="0",
        get_path=lambda data: data.get("wwaniface", {}),
        turn_on={
            "service": "zwrt_data",
            "method": "set_wwaniface",
            "params": {"source_module": "web", "cid": 1, "enable": 1},
        },
        turn_off={
            "service": "zwrt_data",
            "method": "set_wwaniface",
            "params": {"source_module": "web", "cid": 1, "enable": 0},
        },
    ),
    # weitere Switches einfach hier anhängen
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    coordinator = data["coordinator"]
    name = data.get("name", "ZTE Router")

    entities = [
        ZteActionSwitch(coordinator, api, entry, name, switch_def)
        for switch_def in SWITCH_DEFS
    ]

    # Pause polling for 5 minutes
    entities.append(ZtePausePollingSwitch(hass, entry, name))

    # Cell lock switches (use text entities for input)
    entities += [
        ZteCellLockSwitch(hass, coordinator, api, entry, name, kind="4g"),
        ZteCellLockSwitch(hass, coordinator, api, entry, name, kind="5g"),
    ]

    async_add_entities(entities)


class ZteActionSwitch(CoordinatorEntity, SwitchEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        api,
        entry: ConfigEntry,
        device_name: str,
        switch_def: ZteActionSwitchDef,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._def = switch_def

        self._attr_name = switch_def.name
        self._attr_icon = switch_def.icon
        self._attr_unique_id = f"{entry.entry_id}_{switch_def.key}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=device_name,
            manufacturer="ZTE",
        )

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data or {}

        # Mobile data state in WebUI is derived from WAN connect status, not get_wwaniface.enable.
        if self._def.key == "mobile_data":
            wan = data.get("wan") or {}
            status = str(wan.get("current_wan_status") or wan.get("lte_connect_status") or "")
            return status in {
                "ipv4_connected",
                "ipv6_connected",
                "ipv4_ipv6_connected",
                "ppp_connected",
                "connected",
            }

        # If WiFi master is OFF, force band switches to show OFF
        if self._def.key in ("wifi_main_2g", "wifi_main_5g"):
            wifi_onoff = (data.get("wlan") or {}).get("wifi_onoff")
            if str(wifi_onoff) != "1":
                return False

        node = self._def.get_path(data) or {}
        value = node.get(self._def.state_key)
        return str(value) == self._def.on_value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        return self._def.get_path(data)


    async def async_turn_on(self, **kwargs: Any) -> None:
        _LOGGER.info("Turning ON ZTE switch: %s", self._def.key)
        ok = await self._api.async_execute_action_def(self._def.turn_on)
        if ok:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.warning("Failed to turn ON ZTE switch: %s", self._def.key)

    async def async_turn_off(self, **kwargs: Any) -> None:
        _LOGGER.info("Turning OFF ZTE switch: %s", self._def.key)
        ok = await self._api.async_execute_action_def(self._def.turn_off)
        if ok:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.warning("Failed to turn OFF ZTE switch: %s", self._def.key)


class ZtePausePollingSwitch(SwitchEntity):
    """Switch to pause router polling for 5 minutes.

    While paused, coordinators return last known data and do not call the router API.
    """

    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, device_name: str) -> None:
        self.hass = hass
        self._entry_id = entry.entry_id
        self._unsub_auto_off = None

        self._attr_name = "Pause polling (5 min)"
        self._attr_icon = "mdi:pause-circle-outline"
        self._attr_unique_id = f"{entry.entry_id}_pause_polling"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=device_name,
            manufacturer="ZTE",
        )

    @property
    def _store(self) -> dict:
        return self.hass.data[DOMAIN][self._entry_id]

    @property
    def is_on(self) -> bool:
        pause_until: datetime | None = self._store.get("pause_until")
        return pause_until is not None and dt_util.utcnow() < pause_until

    async def async_turn_on(self, **kwargs: Any) -> None:
        # Cancel any existing auto-off timer
        if self._unsub_auto_off is not None:
            self._unsub_auto_off()
            self._unsub_auto_off = None

        self._store["pause_until"] = dt_util.utcnow() + timedelta(minutes=5)

        async def _auto_off(_now) -> None:
            # Auto-resume polling after 5 minutes
            self._store["pause_until"] = None
            self._unsub_auto_off = None

            coordinator = self._store.get("coordinator")
            coordinator_fast = self._store.get("coordinator_fast")
            api = self._store.get("api")

            # The router session may have been invalidated by browser activity during
            # the pause.  Force a re-login before the next coordinator refresh.
            if api is not None:
                api.invalidate_session()

            if coordinator is not None:
                await coordinator.async_request_refresh()
            if coordinator_fast is not None:
                await coordinator_fast.async_request_refresh()

            self.async_write_ha_state()

        # Schedule auto-off
        self._unsub_auto_off = async_call_later(self.hass, 5 * 60, _auto_off)

        # Request refresh; update functions will skip real polling while paused
        coordinator = self._store.get("coordinator")
        coordinator_fast = self._store.get("coordinator_fast")
        if coordinator is not None:
            await coordinator.async_request_refresh()
        if coordinator_fast is not None:
            await coordinator_fast.async_request_refresh()

        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        # Cancel auto-off timer if present
        if self._unsub_auto_off is not None:
            self._unsub_auto_off()
            self._unsub_auto_off = None

        # Resume polling immediately
        self._store["pause_until"] = None

        coordinator = self._store.get("coordinator")
        coordinator_fast = self._store.get("coordinator_fast")
        api = self._store.get("api")

        # The router session may have been invalidated by browser activity during
        # the pause.  Force a re-login before the next coordinator refresh.
        if api is not None:
            api.invalidate_session()

        if coordinator is not None:
            await coordinator.async_request_refresh()
        if coordinator_fast is not None:
            await coordinator_fast.async_request_refresh()

        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_auto_off is not None:
            self._unsub_auto_off()
            self._unsub_auto_off = None

# ----------------- Cell Lock Switches -----------------

class ZteCellLockSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable/disable 4G/5G cell lock.

    - ON: applies the value from the corresponding text entity
    - OFF: sets 0,0 (4G) or 0,0,0 (5G)
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator,
        api,
        entry: ConfigEntry,
        device_name: str,
        *,
        kind: str,
    ) -> None:
        super().__init__(coordinator)
        self.hass = hass
        self._api = api
        self._kind = kind  # "4g" or "5g"
        self._entry_id = entry.entry_id

        if kind not in ("4g", "5g"):
            raise ValueError("kind must be '4g' or '5g'")

        self._attr_name = "Cell Lock 4G" if kind == "4g" else "Cell Lock 5G"
        self._attr_icon = "mdi:cellphone-lock"
        self._attr_unique_id = f"{entry.entry_id}_cell_lock_{kind}_enabled"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=device_name,
            manufacturer="ZTE",
        )

        # Will be resolved lazily via entity registry
        self._text_entity_id: str | None = None

    def _netinfo(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        node = data.get("netinfo")
        return node if isinstance(node, dict) else {}

    async def _resolve_text_entity_id(self) -> str | None:
        """Resolve corresponding text entity_id via the entity registry using unique_id."""
        if self._text_entity_id:
            return self._text_entity_id

        # Must match text.py unique_id scheme
        text_unique_id = f"{self._entry_id}_cell_lock_{self._kind}_text"

        try:
            er = async_get_entity_registry(self.hass)
            ent_id = er.async_get_entity_id("text", DOMAIN, text_unique_id)
            self._text_entity_id = ent_id
            return ent_id
        except Exception as e:
            _LOGGER.debug("Could not resolve text entity id for %s: %s", text_unique_id, e)
            return None

    @property
    def is_on(self) -> bool:
        netinfo = self._netinfo()
        if self._kind == "4g":
            return ZteRouterApi.is_4g_cell_lock_active(netinfo)
        return ZteRouterApi.is_5g_cell_lock_active(netinfo)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        netinfo = self._netinfo()
        if self._kind == "4g":
            return {
                "lock_lte_cell": ZteRouterApi.get_4g_cell_lock_value(netinfo),
                "suggested": ZteRouterApi.suggest_4g_cell_lock_text(netinfo),
            }
        return {
            "lock_nr_cell": ZteRouterApi.get_5g_cell_lock_value(netinfo),
            "suggested": ZteRouterApi.suggest_5g_cell_lock_text(netinfo),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        # Resolve associated text entity and read its current value
        text_entity_id = await self._resolve_text_entity_id()
        if not text_entity_id:
            _LOGGER.warning("Cell lock %s: text entity not found; cannot enable", self._kind)
            return

        st = self.hass.states.get(text_entity_id)
        value = (st.state if st else "")
        value = (value or "").strip()

        try:
            if self._kind == "4g":
                ok = await self._api.async_set_4g_cell_lock_enabled(True, value=value)
            else:
                ok = await self._api.async_set_5g_cell_lock_enabled(True, value=value)
        except Exception as e:
            _LOGGER.warning("Failed to enable cell lock %s: %s", self._kind, e)
            ok = False

        if ok:
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            if self._kind == "4g":
                ok = await self._api.async_set_4g_cell_lock_enabled(False)
            else:
                ok = await self._api.async_set_5g_cell_lock_enabled(False)
        except Exception as e:
            _LOGGER.warning("Failed to disable cell lock %s: %s", self._kind, e)
            ok = False

        if ok:
            await self.coordinator.async_request_refresh()
