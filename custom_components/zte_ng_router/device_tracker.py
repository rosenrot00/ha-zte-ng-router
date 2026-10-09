"""Optional, read-only WLAN client tracking from complete slow-poll snapshots."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Any

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, CONF_TRACK_WIFI_CLIENTS, CONF_CLIENT_CONSIDER_HOME,
    DEFAULT_CLIENT_CONSIDER_HOME,
)
from .zte_api import ZteRouterApi


@dataclass
class WifiClient:
    """Cached client state; no presence decisions are made in entity properties."""

    mac: str
    metadata: dict[str, Any] = field(default_factory=dict)
    last_seen: datetime | None = None
    connected: bool | None = None
    missing_since: float | None = None


class WifiClientTracking:
    """Apply only complete snapshots and reset absence after observation gaps."""

    def __init__(self, consider_home: int) -> None:
        self.consider_home = consider_home
        self.clients: dict[str, WifiClient] = {}
        self.available = False
        self._last_observed: datetime | None = None

    def reset_absence(self) -> None:
        """Pauses and failed polls must not count toward confirmed absence."""
        for client in self.clients.values():
            client.missing_since = None

    def update(self, snapshot: Any, *, now: float | None = None) -> None:
        if not isinstance(snapshot, dict):
            self.available = False
            self.reset_absence()
            return
        observed = snapshot.get("observed_at")
        clients = snapshot.get("clients")
        if (not isinstance(observed, datetime) or observed.tzinfo is None
                or observed.utcoffset() is None or not isinstance(clients, dict)
                or any(ZteRouterApi.normalize_client_mac(mac) != mac or not isinstance(metadata, dict)
                       for mac, metadata in clients.items())):
            self.available = False
            self.reset_absence()
            return
        # An optimistic switch refresh or paused poll may reuse the same snapshot.
        if observed == self._last_observed:
            return
        self._last_observed = observed
        self.available = True
        now = time.monotonic() if now is None else now
        for mac, metadata in clients.items():
            client = self.clients.setdefault(mac, WifiClient(mac))
            client.metadata = dict(metadata)
            client.last_seen = observed.astimezone(timezone.utc)
            client.connected = True
            client.missing_since = None
        for mac, client in self.clients.items():
            if mac in clients:
                continue
            if client.missing_since is None:
                client.missing_since = now
            if now - client.missing_since >= self.consider_home:
                client.connected = False


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Discover WLAN clients only when the user opts in."""
    if not entry.options.get(CONF_TRACK_WIFI_CLIENTS, entry.data.get(CONF_TRACK_WIFI_CLIENTS, False)):
        return
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator = store["coordinator"]
    tracking = WifiClientTracking(entry.options.get(
        CONF_CLIENT_CONSIDER_HOME, entry.data.get(CONF_CLIENT_CONSIDER_HOME, DEFAULT_CLIENT_CONSIDER_HOME)
    ))
    prefix = f"{entry.entry_id}_wifi_"
    # Recreate existing trackers even when their clients are offline after a restart.
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain != "device_tracker" or entity.platform != DOMAIN or not entity.unique_id.startswith(prefix):
            continue
        mac = ZteRouterApi.normalize_client_mac(entity.unique_id[len(prefix):])
        if mac is not None:
            tracking.clients.setdefault(mac, WifiClient(mac))
    added: set[str] = set()

    @callback
    def update_clients() -> None:
        tracking.update((coordinator.data or {}).get("wifi_clients") if coordinator.last_update_success else None)
        new = tracking.clients.keys() - added
        if new:
            added.update(new)
            async_add_entities([
                ZteWifiClientTracker(coordinator, tracking, mac, entry.entry_id, store["name"])
                for mac in sorted(new)
            ])

    store["reset_wifi_client_absence"] = tracking.reset_absence
    entry.async_on_unload(lambda: store.pop("reset_wifi_client_absence", None))
    entry.async_on_unload(coordinator.async_add_listener(update_clients))
    update_clients()


class ZteWifiClientTracker(CoordinatorEntity, ScannerEntity, RestoreEntity):
    """HA-native tracker, identified by router config entry and normalized MAC."""

    _attr_source_type = SourceType.ROUTER
    _attr_entity_category = None

    def __init__(self, coordinator, tracking: WifiClientTracking, mac: str,
                 entry_id: str, router_name: str) -> None:
        super().__init__(coordinator)
        self._tracking = tracking
        self._mac = mac
        self._unique_id = f"{entry_id}_wifi_{mac.replace(':', '')}"
        self._router_name = router_name

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def entity_registry_enabled_default(self) -> bool:
        return False

    @property
    def _client(self) -> WifiClient:
        return self._tracking.clients[self._mac]

    @property
    def name(self) -> str:
        return f"{self._router_name} {self.hostname or self._mac}"

    @property
    def mac_address(self) -> str:
        return self._mac

    @property
    def hostname(self) -> str | None:
        return self._client.metadata.get("hostname")

    @property
    def ip_address(self) -> str | None:
        return self._client.metadata.get("ip_address")

    @property
    def is_connected(self) -> bool | None:
        return self._client.connected

    @property
    def available(self) -> bool:
        return super().available and self._tracking.available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_seen": self._client.last_seen.isoformat() if self._client.last_seen else None,
                "ipv6_address": self._client.metadata.get("ipv6_address"),
                "interface_type": self._client.metadata.get("interface_type"),
                "connection_type": "wifi"}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is None or self._client.last_seen is not None:
            return
        # Restore metadata, never assume a restored device is still connected.
        self._client.metadata = {
            "hostname": state.attributes.get("host_name"),
            "ip_address": state.attributes.get("ip"),
            "ipv6_address": state.attributes.get("ipv6_address"),
            "interface_type": state.attributes.get("interface_type"),
        }
        try:
            last_seen = datetime.fromisoformat(state.attributes.get("last_seen") or "")
        except (ValueError, TypeError):
            return
        if last_seen.tzinfo is not None and last_seen.utcoffset() is not None:
            self._client.last_seen = last_seen.astimezone(timezone.utc)
