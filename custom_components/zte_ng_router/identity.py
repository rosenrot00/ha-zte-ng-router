"""Derive a stable, device-owned identifier from router poll data.

The config entry used to be identified by its address, so adding the same router
under a new IP created a duplicate entry. The router's own WLAN MAC (BSSID) is
available from ``zwrt_zte_mdm.api.get_sim_info`` in the normal poll and survives
address changes, reboots and firmware updates.

Entity unique IDs and the device identifier keep using the config entry ID;
only the entry's ``unique_id`` is derived from this value. This preserves entity
IDs, areas and long-term statistics when the entry is migrated.
"""

from __future__ import annotations

import re
from typing import Any

_MAC_HEX = re.compile(r"^[0-9a-f]{12}$", re.IGNORECASE)
_UUID_LIKE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Keys that may carry a device-owned identifier, in priority order. The WLAN
# MAC is preferred because it is present on virtually every firmware.
_IDENTIFIER_KEYS = (
    "wlan_mac_address",
    "lan_mac_address",
    "mac_address",
    "device_mac",
    "mac",
    "imei",
    "serial_number",
    "serial_no",
    "sn",
)


def normalize_mac(value: Any) -> str | None:
    """Return a canonical ``aa:bb:cc:dd:ee:ff`` MAC, or None if not a MAC."""
    if not isinstance(value, str):
        return None
    compact = re.sub(r"[^0-9a-fA-F]", "", value.strip())
    if not _MAC_HEX.fullmatch(compact):
        return None
    compact = compact.lower()
    return ":".join(compact[i:i + 2] for i in range(0, 12, 2))


def normalize_identifier(value: Any) -> str | None:
    """Normalize a candidate identifier; accept MACs and opaque device IDs."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.lower() in {"unknown", "unavailable", "none", "null", "n/a", "-"}:
        return None
    mac = normalize_mac(text)
    if mac is not None:
        return mac
    # IMEI/serial numbers stay as reported (digits/upper case are common).
    if len(text) > 64 or any(char.isspace() for char in text):
        return None
    if _UUID_LIKE.fullmatch(text.lower()):
        return text.lower()
    return text


def device_identifier_from_payload(payload: Any) -> str | None:
    """Extract a device identifier from a single API payload."""
    if not isinstance(payload, dict):
        return None
    for key in _IDENTIFIER_KEYS:
        identifier = normalize_identifier(payload.get(key))
        if identifier is not None:
            return identifier
    return None


def device_identifier_from_data(data: dict[str, Any] | None) -> str | None:
    """Extract a device identifier from a full coordinator data snapshot."""
    if not isinstance(data, dict):
        return None
    for section in ("sim_info", "device", "wan", "common_config"):
        identifier = device_identifier_from_payload(data.get(section))
        if identifier is not None:
            return identifier
    return device_identifier_from_payload(data)


def entry_unique_id(identifier: str) -> str:
    """Build the config entry unique ID for a device-owned identifier."""
    return f"zte_ng_router_{identifier}"


def should_migrate_unique_id(unique_id: str | None, identifier: str) -> bool:
    """Only legacy address-based IDs are migrated to a device identifier.

    A MAC-based ID that differs means the router was replaced; silently
    re-identifying it would merge two different devices.
    """
    if not unique_id or unique_id == entry_unique_id(identifier):
        return False
    if not unique_id.startswith("zte_ng_router_"):
        return False
    stored = unique_id.removeprefix("zte_ng_router_")
    return normalize_mac(stored) is None
