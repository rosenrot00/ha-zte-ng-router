"""Keep router versions in the device registry using existing poll results."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

_MISSING_VERSIONS = {"", "-", "--", "unknown", "unavailable", "none", "null", "n/a"}


@callback
def async_update_device_info(
    hass: HomeAssistant,
    entry_id: str,
    router_name: str,
    data: dict[str, Any],
) -> None:
    """Update only supplied, changed versions; never clear a last known value."""
    versions: dict[str, str] = {}
    for attribute, field in (("sw_version", "wa_inner_version"),
                             ("hw_version", "hardware_version")):
        for source in (data.get("common_config"), data.get("device")):
            value = source.get(field) if isinstance(source, dict) else None
            if isinstance(value, str) and value.strip().lower() not in _MISSING_VERSIONS:
                versions[attribute] = value.strip()
                break
    if not versions:
        return

    registry = dr.async_get(hass)
    identifiers = {(DOMAIN, entry_id)}
    if hasattr(registry, "async_get_device_by_identifier"):
        device = registry.async_get_device_by_identifier((DOMAIN, entry_id), entry_id)
    else:
        device = registry.async_get_device(identifiers=identifiers)
    if device is None:
        registry.async_get_or_create(
            config_entry_id=entry_id,
            identifiers=identifiers,
            name=router_name,
            manufacturer="ZTE",
            **versions,
        )
        return

    changes = {key: value for key, value in versions.items()
               if getattr(device, key) != value}
    if changes:
        registry.async_update_device(device.id, **changes)
