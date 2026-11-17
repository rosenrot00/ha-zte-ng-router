"""Interpret WLAN status without treating missing data as off."""

from typing import Any


def wifi_flag(value: Any) -> bool | None:
    """Read a ZTE 0/1 flag and leave any other value unknown."""
    if type(value) is bool:
        return value
    if type(value) in (int, str):
        if str(value) == "1":
            return True
        if str(value) == "0":
            return False
    return None


def wifi_master_state(data: dict[str, Any]) -> bool | None:
    """Use the UCI field if present, otherwise use the WLAN report field."""
    for source in ("wifi_module", "wlan"):
        state = wifi_flag((data.get(source) or {}).get("wifi_onoff"))
        if state is not None:
            return state
    return None
