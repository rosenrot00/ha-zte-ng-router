from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SMS_COMPOSE_DEFAULT

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ZteActionButtonDef:
    key: str
    name: str
    icon: str
    action: dict[str, Any]
    kind: str = "action"


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
    ZteActionButtonDef(
        key="check_firmware_update", name="Check Firmware Update", icon="mdi:update",
        kind="check_firmware", action={},
    ),
    ZteActionButtonDef(
        key="start_firmware_update", name="Start Firmware Update", icon="mdi:cloud-download",
        kind="start_firmware", action={},
    ),
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
        if btn_def.kind in {"check_firmware", "start_firmware"}:
            self._attr_entity_category = EntityCategory.CONFIG

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=device_name,
            manufacturer="ZTE",
        )

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        if self._btn_def.kind in {"check_firmware", "start_firmware"}:
            firmware = (self.coordinator.data or {}).get("firmware")
            if not isinstance(firmware, dict) or not firmware:
                return False
            if self._btn_def.kind == "start_firmware":
                return self._api.firmware_update_action(firmware) is not None
        return True

    async def async_press(self) -> None:
        _LOGGER.info("Executing ZTE action button: %s", self._btn_def.key)

        if self._btn_def.kind in {"check_firmware", "start_firmware"}:
            try:
                if self._btn_def.kind == "check_firmware":
                    ok = await self._api.async_check_firmware_update()
                else:
                    ok = await self._api.async_start_firmware_update()
            except ValueError as exc:
                raise HomeAssistantError(str(exc)) from exc
            if not ok:
                raise HomeAssistantError("Router rejected the firmware command; see integration logs")
            await self.coordinator.async_request_refresh()
            return

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

            try:
                ok = await self._api.async_send_sms(number=number, message=message)
            except ValueError as exc:
                raise HomeAssistantError(str(exc)) from exc
            if not ok:
                _LOGGER.warning("ZTE action button failed: %s", self._btn_def.key)
            else:
                await self.coordinator.async_request_refresh()
            return

        ok = await self._api.async_execute_action_def(self._btn_def.action)
        if not ok:
            _LOGGER.warning("ZTE action button failed: %s", self._btn_def.key)
