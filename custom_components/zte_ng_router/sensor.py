from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_NAME


# key, name, device_class, unit, state_class
SENSOR_DEFS = [
    # General network info
    ("network_provider", "Network Provider", None, None, None),
    ("connection_type", "Connection Type", None, None, None),

    # Bands and total bandwidth
    ("bands_summary", "Bands", None, None, None),
    ("total_bandwidth", "Total Bandwidth", None, "MHz", SensorStateClass.MEASUREMENT),

    # Primary RSRP (LTE preferred, NR fallback)
    #("primary_rsrp", "Primary RSRP", None, "dBm", SensorStateClass.MEASUREMENT),

    # LTE metrics
    ("lte_pci", "LTE PCI", None, None, None),
    ("lte_earfcn", "LTE EARFCN", None, None, None),
    ("lte_rsrp", "LTE RSRP", None, "dBm", SensorStateClass.MEASUREMENT),
    ("lte_rsrq", "LTE RSRQ", None, "dB", SensorStateClass.MEASUREMENT),
    ("lte_sinr", "LTE SINR", None, "dB", SensorStateClass.MEASUREMENT),
    ("lte_rssi", "LTE RSSI", None, "dBm", SensorStateClass.MEASUREMENT),

    # NR / 5G metrics
    ("nr_pci", "NR PCI", None, None, None),
    ("nr_arfcn", "NR ARFCN", None, None, None),
    ("nr_rsrp", "NR RSRP", None, "dBm", SensorStateClass.MEASUREMENT),
    ("nr_rsrq", "NR RSRQ", None, "dB", SensorStateClass.MEASUREMENT),
    ("nr_sinr", "NR SINR", None, "dB", SensorStateClass.MEASUREMENT),
    ("nr_rssi", "NR RSSI", None, "dBm", SensorStateClass.MEASUREMENT),

    # WAN / system
    ("wan_ipv4", "WAN IPv4", None, None, None),
    ("cpu_temp", "CPU Temperature", SensorDeviceClass.TEMPERATURE,
     UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT),
    # Uptime in seconds – Home Assistant can convert/display as hours/days
    ("uptime", "Device Uptime", SensorDeviceClass.DURATION,
     "s", SensorStateClass.MEASUREMENT),
]


def _as_number(value: Any) -> Any:
    """Convert router value to float or return None for empty/invalid values.

    Home Assistant expects numeric sensors (measurement + unit) to expose either
    a real number or None (unknown), never an empty string or arbitrary text.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        v = value.strip()
        if v == "" or v == "-":
            return None
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _extract_value(data: dict[str, Any], key: str) -> Any:
    """Map a logical key to a value inside the aggregated API data."""
    netinfo = data.get("netinfo") or {}
    thermal = data.get("thermal") or {}
    device = data.get("device") or {}
    wan = data.get("wan") or {}

    # General
    if key == "network_provider":
        return netinfo.get("network_provider_fullname")

    if key == "connection_type":
        nt = netinfo.get("network_type")
        if nt == "SA":
            return "5G SA"
        if nt == "ENDC":
            return "5G NSA"
        return nt

    # Bands & total bandwidth (derived in zte_api.update_all)
    if key == "bands_summary":
        return data.get("bands_summary")

    if key == "total_bandwidth":
        return _as_number(data.get("total_bw_mhz"))

    if key == "primary_rsrp":
        # Prefer LTE RSRP, fall back to NR RSRP
        return _as_number(netinfo.get("lte_rsrp") or netinfo.get("nr5g_rsrp"))

    # LTE metrics (field names based on ZTE-Script-NG)
    if key == "lte_pci":
        return _as_number(netinfo.get("lte_pci"))
    if key == "lte_earfcn":
        return _as_number(netinfo.get("lte_action_channel"))
    if key == "lte_rsrp":
        return _as_number(netinfo.get("lte_rsrp"))
    if key == "lte_rsrq":
        return _as_number(netinfo.get("lte_rsrq"))
    if key == "lte_sinr":
        # Script uses "lte_snr" as SINR
        return _as_number(netinfo.get("lte_snr"))
    if key == "lte_rssi":
        return _as_number(netinfo.get("lte_rssi"))

    # NR / 5G metrics
    if key == "nr_pci":
        return _as_number(netinfo.get("nr5g_pci"))
    if key == "nr_arfcn":
        return _as_number(netinfo.get("nr5g_action_channel"))
    if key == "nr_rsrp":
        return _as_number(netinfo.get("nr5g_rsrp"))
    if key == "nr_rsrq":
        return _as_number(netinfo.get("nr5g_rsrq"))
    if key == "nr_sinr":
        return _as_number(netinfo.get("nr5g_snr"))
    if key == "nr_rssi":
        return _as_number(netinfo.get("nr5g_rssi"))

    # WAN / system (string / numeric mixed)
    if key == "wan_ipv4":
        return wan.get("mwan_wanlan1_wan_ipaddr")

    if key == "cpu_temp":
        return _as_number(thermal.get("cpuss_temp"))

    if key == "uptime":
        # device_uptime is in seconds – keep it numeric, HA handles display
        return _as_number(device.get("device_uptime"))

    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: DataUpdateCoordinator = data["coordinator"]
    router_name: str = data["name"]  # name given in config flow

    entities: list[ZteNgRouterSensor] = []
    for key, name, dev_class, unit, state_class in SENSOR_DEFS:
        entities.append(
            ZteNgRouterSensor(
                coordinator=coordinator,
                entry_id=entry.entry_id,
                router_name=router_name,
                key=key,
                name=name,
                device_class=dev_class,
                unit=unit,
                state_class=state_class,
            )
        )

    async_add_entities(entities)


class ZteNgRouterSensor(CoordinatorEntity, SensorEntity):
    """Single ZTE NG Router sensor entity reading from the coordinator."""

    _attr_should_poll = False

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry_id: str,
        router_name: str,
        key: str,
        name: str,
        device_class: SensorDeviceClass | None,
        unit: str | None,
        state_class: SensorStateClass | None,
    ) -> None:
        super().__init__(coordinator)
        self._key = key

        # Entity name: "<Router name> <Sensor name>"
        self._attr_name = f"{router_name} {name}"

        # unique_id includes entry_id so multiple routers can coexist
        self._attr_unique_id = f"{entry_id}_{key}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=router_name,
            manufacturer="ZTE",
        )

        if device_class is not None:
            self._attr_device_class = device_class
        if unit is not None:
            self._attr_native_unit_of_measurement = unit
        if state_class is not None:
            self._attr_state_class = state_class

    @property
    def native_value(self) -> Any:
        data: dict[str, Any] = self.coordinator.data or {}
        return _extract_value(data, self._key)
