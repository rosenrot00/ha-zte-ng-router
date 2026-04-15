from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SMS_COMPOSE_DEFAULT

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ZteActionButtonDef:
    key: str
    name: str
    icon: str
    action: dict[str, Any]
    kind: str = "action"  # "action" or "send_sms"


BUTTON_DEFS: list[ZteActionButtonDef] = [
    ZteActionButtonDef(
        key="restart",
        name="Restart",
        icon="mdi:restart",
        kind="action",
        action={
            "service": "zwrt_mc.device.manager",
            "method": "device_reboot",
            "params": {"moduleName": "web"},
        },
    ),
    ZteActionButtonDef(
        key="send_sms",
        name="Send SMS",
        icon="mdi:send",
        kind="send_sms",
        action={},
    ),
    # Add more buttons later by appending more ZteActionButtonDef(...)
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

    async_add_entities(
        [
            ZteActionButton(coordinator, api, entry, name, btn_def)
            for btn_def in BUTTON_DEFS
        ]
    )


class ZteActionButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        api,
        entry: ConfigEntry,
        device_name: str,
        btn_def: ZteActionButtonDef,
    ) -> None:
        super().__init__(coordinator)
        self.hass = coordinator.hass
        self._entry_id = entry.entry_id
        self._api = api
        self._btn_def = btn_def

        self._attr_name = btn_def.name
        self._attr_icon = btn_def.icon
        self._attr_unique_id = f"{entry.entry_id}_{btn_def.key}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=device_name,
            manufacturer="ZTE",
        )

    async def async_press(self) -> None:
        _LOGGER.info("Executing ZTE action button: %s", self._btn_def.key)

        if self._btn_def.kind == "send_sms":
            data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
            compose_value = str(data.get("sms_compose") or "")
            if compose_value.strip() == SMS_COMPOSE_DEFAULT:
                _LOGGER.warning("Cannot send SMS, compose value is still default helper text")
                return
            try:
                number, message = self._api.parse_sms_compose_input(compose_value)
            except ValueError as exc:
                _LOGGER.warning("Cannot send SMS, invalid compose value: %s", exc)
                return

            ok = await self._api.async_send_sms(number=number, message=message)
            if not ok:
                _LOGGER.warning("ZTE action button failed: %s", self._btn_def.key)
            else:
                await self.coordinator.async_request_refresh()
            return

        ok = await self._api.async_execute_action_def(self._btn_def.action)
        if not ok:
            _LOGGER.warning("ZTE action button failed: %s", self._btn_def.key)
