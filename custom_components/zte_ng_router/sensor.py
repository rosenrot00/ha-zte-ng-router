from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import UnitOfDataRate, UnitOfInformation, UnitOfTemperature
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_NAME


# key, name, device_class, unit, state_class
SENSOR_DEFS = [
    # General network info
    ("network_provider", "Network Provider", None, None, None),
    ("connection_type", "Connection Type", None, None, None),

    # Wi-Fi (public zwrt_wlan/report)
    ("wifi_onoff", "WiFi Enabled", None, None, None),
    ("main2g_ssid", "WiFi 2.4 GHz SSID", None, None, None),
    ("main5g_ssid", "WiFi 5 GHz SSID", None, None, None),

    # Net identifiers / locks
    ("signalbar", "Signal Bars", None, None, SensorStateClass.MEASUREMENT),
    ("rmcc", "RMCC", None, None, None),
    ("rmnc", "RMNC", None, None, None),
    ("nr5g_cell_id", "NR5G Cell ID", None, None, None),
    ("lac_code", "LAC Code", None, None, None),
    ("lte_band_lock", "LTE Band Lock", None, None, None),
    ("gw_band_lock", "GW Band Lock", None, None, None),

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
    ("lte_primary_band", "LTE Primary Band", None, None, None),

    # NR / 5G metrics
    ("nr_pci", "NR PCI", None, None, None),
    ("nr_arfcn", "NR ARFCN", None, None, None),
    ("nr_rsrp", "NR RSRP", None, "dBm", SensorStateClass.MEASUREMENT),
    ("nr_rsrq", "NR RSRQ", None, "dB", SensorStateClass.MEASUREMENT),
    ("nr_sinr", "NR SINR", None, "dB", SensorStateClass.MEASUREMENT),
    ("nr_rssi", "NR RSSI", None, "dBm", SensorStateClass.MEASUREMENT),
    ("nr_active_band", "NR Active Band", None, None, None),

    # WAN / system
    ("wan_ipv4", "WAN IPv4", None, None, None),
    ("wan_ipv6", "WAN IPv6", None, None, None),
    ("wan_status", "WAN Status", None, None, None),
    ("wan_link_state", "WAN Link State", None, None, None),
    ("modem_main_state", "Modem Main State", None, None, None),
    ("radio_off", "Radio Off", None, None, None),
    ("connected_lan_devices", "Connected LAN Devices", None, None, SensorStateClass.MEASUREMENT),
    ("connected_wifi_devices", "Connected WiFi Devices", None, None, SensorStateClass.MEASUREMENT),
    ("download_rate", "Download Rate", SensorDeviceClass.DATA_RATE, UnitOfDataRate.BITS_PER_SECOND, SensorStateClass.MEASUREMENT),
    ("upload_rate", "Upload Rate", SensorDeviceClass.DATA_RATE, UnitOfDataRate.BITS_PER_SECOND, SensorStateClass.MEASUREMENT),
    ("monthly_download_mb", "Monthly Download", SensorDeviceClass.DATA_SIZE, UnitOfInformation.BYTES, SensorStateClass.MEASUREMENT),
    ("monthly_upload_mb", "Monthly Upload", SensorDeviceClass.DATA_SIZE, UnitOfInformation.BYTES, SensorStateClass.MEASUREMENT),
    ("sms_count", "SMS Count", None, None, SensorStateClass.MEASUREMENT),
    ("sms_unread_total", "SMS Unread", None, None, SensorStateClass.MEASUREMENT),
    ("sms_nv_total", "SMS NV Total", None, None, SensorStateClass.MEASUREMENT),
    ("sms_sim_total", "SMS SIM Total", None, None, SensorStateClass.MEASUREMENT),
    ("sms_nv_used_total", "SMS NV Used", None, None, SensorStateClass.MEASUREMENT),
    ("sms_latest", "Latest SMS", None, None, None),
    # Connected time in seconds (session duration) – Home Assistant can display as h/m
    ("connected_time", "Connected Time", SensorDeviceClass.DURATION,
     "s", SensorStateClass.MEASUREMENT),
    ("hardware_version", "Hardware Version", None, None, None),
    ("wa_inner_version", "WA Inner Version", None, None, None),
    ("cpu_temp", "CPU Temperature", SensorDeviceClass.TEMPERATURE,
     UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT),
    ("modem_temp", "Modem Temperature", SensorDeviceClass.TEMPERATURE,
     UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT),
    ("modem_5g_temp", "5G Modem Temperature", SensorDeviceClass.TEMPERATURE,
     UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT),
    ("pa_temp_level", "PA Temp Level", None, "%", SensorStateClass.MEASUREMENT),
    ("tj_temp_level", "TJ Temp Level", None, "%", SensorStateClass.MEASUREMENT),
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
        vl = v.lower()
        # Some firmwares return numeric values in hex (e.g. LTE PCI as "7c").
        if vl.startswith("0x"):
            try:
                return int(vl, 16)
            except ValueError:
                return None
        if "." not in v and any(c in "abcdefABCDEF" for c in v):
            try:
                return int(v, 16)
            except ValueError:
                return None
        try:
            return float(v)
        except ValueError:
            return None
    return None

def _to_bit_per_s(value: Any) -> Any:
    """Convert router speed value to bit/s.

    Router values are byte/s. Convert to bit/s for HA data_rate sensors.
    """
    v = _as_number(value)
    if v is None:
        return None
    return int(v * 8.0)


def _as_temperature(value: Any) -> Any:
    """Normalize temperature and hide known invalid modem sentinel values.

    Some ZTE firmwares report -40 when a temperature sensor is not available.
    """
    v = _as_number(value)
    if v is None:
        return None
    if abs(float(v) + 40.0) < 0.001:
        return None
    return v


def _bytes_counter(value: Any) -> Any:
    """Normalize byte counter to integer bytes."""
    v = _as_number(value)
    if v is None:
        return None
    if v < 0:
        return None
    return int(v)


def _as_text(value: Any) -> str | None:
    """Normalize router text values.

    Returns None for empty/placeholder values so HA shows 'unknown' instead of blank.
    """
    if value is None:
        return None
    s = str(value).strip()
    if s == "" or s == "-":
        return None
    return s


def _parse_sms_date(raw: Any) -> str | None:
    """Parse modem SMS timestamp format: 'yy,mm,dd,HH,MM,SS,+4'."""
    if raw is None:
        return None
    txt = str(raw).strip()
    if not txt:
        return None
    parts = [p.strip() for p in txt.split(",")]
    if len(parts) != 7:
        return txt
    try:
        year = 2000 + int(parts[0])
        month = int(parts[1])
        day = int(parts[2])
        hour = int(parts[3])
        minute = int(parts[4])
        second = int(parts[5])
        tz_quarters = int(parts[6])
        tz_delta = timedelta(minutes=abs(tz_quarters) * 15)
        if tz_quarters < 0:
            tz_delta = -tz_delta
        tz = timezone(tz_delta)
        dt = datetime(year, month, day, hour, minute, second, tzinfo=tz)
        return dt.isoformat()
    except (TypeError, ValueError):
        return txt


def _truncate_text(value: Any, max_len: int = 240) -> str | None:
    """Limit long SMS content so entity state/attributes stay compact."""
    txt = _as_text(value)
    if txt is None:
        return None
    if len(txt) <= max_len:
        return txt
    return f"{txt[:max_len - 3]}..."


DIAGNOSTIC_SENSOR_KEYS = {
    "rmcc",
    "rmnc",
    "lac_code",
    "lte_band_lock",
    "gw_band_lock",
    "wan_link_state",
    "modem_main_state",
    "radio_off",
    "hardware_version",
    "wa_inner_version",
    "lte_primary_band",
    "nr_active_band",
    "modem_temp",
    "modem_5g_temp",
    "pa_temp_level",
    "tj_temp_level",
}


SENSOR_DESCRIPTIONS: dict[str, str] = {
    "rmcc": "Mobile country code reported by the serving network.",
    "rmnc": "Mobile network code reported by the serving network.",
    "lac_code": "Location area code of the serving LTE/NR network.",
    "wa_inner_version": "Internal router software/firmware build version.",
    "hardware_version": "Router hardware revision reported by the device.",
    "wan_status": "Current WAN connection state reported by the modem/router stack.",
    "wan_link_state": "Physical/logical WAN link state of the modem uplink.",
    "modem_main_state": "Modem state machine status (e.g. init, registered, connected).",
    "radio_off": "Indicates whether cellular radio is disabled (1) or enabled (0).",
    "lte_band_lock": "Raw LTE band lock bitmap/value from router firmware.",
    "gw_band_lock": "Raw 2G/3G band lock bitmap/value from router firmware.",
    "lte_primary_band": "Primary LTE serving band.",
    "nr_active_band": "Active NR (5G) band.",
    "pa_temp_level": "Power amplifier thermal level indicator.",
    "tj_temp_level": "Junction temperature level indicator.",
}


SENSOR_VALUE_LEGENDS: dict[str, dict[str, str]] = {
    "radio_off": {
        "true": "Radio is disabled",
        "false": "Radio is enabled",
        "1": "Radio is disabled",
        "0": "Radio is enabled",
    },
    "wan_status": {
        "ppp_connected": "Data session connected",
        "ppp_connecting": "Data session is connecting",
        "ppp_disconnected": "Data session disconnected",
        "ppp_disconnecting": "Data session is disconnecting",
        "ipv4_connected": "IPv4 connected",
        "ipv6_connected": "IPv6 connected",
        "ipv4_ipv6_connected": "IPv4 + IPv6 connected",
        "disconnected": "No data session",
    },
    "wan_link_state": {
        "link_ok": "Ethernet/WAN link is up",
        "link_down": "Ethernet/WAN link is down",
    },
    "modem_main_state": {
        "modem_init_complete": "Modem initialized",
        "modem_waitpin": "SIM PIN required",
        "modem_waitpuk": "SIM PUK required",
        "modem_sim_undetected": "SIM card not detected",
    },
}


def _extract_value(data: dict[str, Any], key: str) -> Any:
    """Map a logical key to a value inside the aggregated API data."""
    netinfo = data.get("netinfo") or {}
    wlan = data.get("wlan") or {}
    thermal = data.get("thermal") or {}
    device = data.get("device") or {}
    wan = data.get("wan") or {}
    user_list_num = data.get("user_list_num") or {}
    wwandst_monthly = data.get("wwandst_monthly") or {}
    common_config = data.get("common_config") or {}
    sms = data.get("sms") or {}
    sms_capacity = sms.get("capacity") or {}

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

    # Wi-Fi (from zwrt_wlan/report)
    if key == "wifi_onoff":
        v = wlan.get("wifi_onoff")
        if v is None:
            return None
        # Router returns "0"/"1" strings
        return str(v) == "1"

    if key == "main2g_ssid":
        return wlan.get("main2g_ssid")

    if key == "main5g_ssid":
        return wlan.get("main5g_ssid")

    # Net identifiers / locks (from netinfo)
    if key == "signalbar":
        v = _as_number(netinfo.get("signalbar"))
        if v is None:
            return None
        # Router typically reports 0..5 bars; clamp to keep a stable range.
        return max(0, min(5, int(v)))

    if key == "rmcc":
        v = netinfo.get("rmcc")
        return None if v in (None, "", "-") else str(v)

    if key == "rmnc":
        v = netinfo.get("rmnc")
        return None if v in (None, "", "-") else str(v)

    if key == "nr5g_cell_id":
        v = netinfo.get("nr5g_cell_id")
        return None if v in (None, "", "-") else str(v)

    if key == "lac_code":
        v = netinfo.get("lac_code")
        return None if v in (None, "", "-") else str(v)

    if key == "lte_band_lock":
        return netinfo.get("lte_band_lock")

    if key == "gw_band_lock":
        return netinfo.get("gw_band_lock")

    # Bands & total bandwidth (derived in zte_api.update_all)
    if key == "bands_summary":
        v = data.get("bands_summary")
        if not v or v == "-":
            return None
        return v

    if key == "total_bandwidth":
        v = _as_number(data.get("total_bw_mhz"))
        if v is None or v <= 0:
            return None
        return int(v)

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
    if key == "lte_primary_band":
        return _as_text(netinfo.get("lte_action_band"))

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
    if key == "nr_active_band":
        return _as_text(netinfo.get("nr5g_action_band"))

    # WAN / system
    if key == "wan_ipv4":
        v = _as_text(wan.get("mwan_wanlan1_wan_ipaddr"))
        # Some firmwares return 0.0.0.0 when disconnected
        if v in ("0.0.0.0",):
            return None
        return v

    if key == "wan_ipv6":
        v = _as_text(wan.get("mwan_wanlan1_ipv6_wan_ipaddr"))
        # Some firmwares return 0::0 when disconnected
        if v in ("0::0", "0:0:0:0:0:0:0:0"):
            return None
        return v

    if key == "wan_status":
        return _as_text(wan.get("mwan_wanlan1_status")) or _as_text(wan.get("current_wan_status"))

    if key == "wan_link_state":
        return _as_text(wan.get("mwan_wanlan1_link_state"))

    if key == "modem_main_state":
        return _as_text(wan.get("lte_connect_status"))

    if key == "radio_off":
        v = _as_text(wan.get("radio_off"))
        if v in ("0", "1"):
            return v == "1"
        return v

    if key == "connected_lan_devices":
        lan_num = _as_number(user_list_num.get("lan_num"))
        if lan_num is None:
            return None
        return int(lan_num)

    if key == "connected_wifi_devices":
        wlan_num = _as_number(user_list_num.get("wireless_num"))
        if wlan_num is None:
            return None
        return int(wlan_num)

    if key == "download_rate":
        # Live rate comes from zwrt_data.get_wwandst(type=4) on this firmware
        wwandst = data.get("wwandst") or {}
        v = wwandst.get("real_rx_speed")
        if v in (None, "", "-"):
            v = wan.get("real_rx_speed")
        return _to_bit_per_s(v)

    if key == "upload_rate":
        # Live rate comes from zwrt_data.get_wwandst(type=4) on this firmware
        wwandst = data.get("wwandst") or {}
        v = wwandst.get("real_tx_speed")
        if v in (None, "", "-"):
            v = wan.get("real_tx_speed")
        return _to_bit_per_s(v)

    if key == "monthly_download_mb":
        # Monthly total received bytes from zwrt_data.get_wwandst(type=2)
        v = wwandst_monthly.get("month_rx_bytes")
        if v in (None, "", "-"):
            v = wan.get("month_rx_bytes")
        return _bytes_counter(v)

    if key == "monthly_upload_mb":
        # Monthly total transmitted bytes from zwrt_data.get_wwandst(type=2)
        v = wwandst_monthly.get("month_tx_bytes")
        if v in (None, "", "-"):
            v = wan.get("month_tx_bytes")
        return _bytes_counter(v)

    if key == "sms_count":
        messages = sms.get("messages") or []
        return len(messages) if isinstance(messages, list) else 0

    if key == "sms_unread_total":
        v = _as_number(sms_capacity.get("sms_dev_unread_num"))
        return None if v is None else int(v)

    if key == "sms_nv_total":
        v = _as_number(sms_capacity.get("sms_nv_total"))
        return None if v is None else int(v)

    if key == "sms_sim_total":
        v = _as_number(sms_capacity.get("sms_sim_total"))
        return None if v is None else int(v)

    if key == "sms_nv_used_total":
        v = _as_number(sms_capacity.get("sms_nvused_total"))
        return None if v is None else int(v)

    if key == "sms_latest":
        latest = sms.get("latest")
        if not isinstance(latest, dict):
            return None
        number = _as_text(latest.get("number")) or "Unknown"
        sms_date = _parse_sms_date(latest.get("date")) or _as_text(latest.get("date"))
        if sms_date:
            return f"{number} @ {sms_date}"
        return number

    if key == "connected_time":
        # Prefer router_get_status.real_time if present, otherwise use zwrt_data.get_wwandst(type=4).real_time
        v = wan.get("real_time")
        if v in (None, "", "-"):
            wwandst = data.get("wwandst") or {}
            v = wwandst.get("real_time")
        return _as_number(v)

    if key == "hardware_version":
        return common_config.get("hardware_version")

    if key == "wa_inner_version":
        return common_config.get("wa_inner_version")

    if key == "cpu_temp":
        return _as_temperature(thermal.get("cpuss_temp"))

    if key == "modem_temp":
        return _as_temperature(thermal.get("pm_sensor_mdm"))

    if key == "modem_5g_temp":
        return _as_temperature(thermal.get("pm_modem_5g"))

    if key == "pa_temp_level":
        return _as_number(thermal.get("therm_pa_level"))

    if key == "tj_temp_level":
        return _as_number(thermal.get("therm_tj_level"))

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
    coordinator_fast: DataUpdateCoordinator | None = data.get("coordinator_fast")
    fast_keys = {"connected_time", "download_rate", "upload_rate"}
    router_name: str = data["name"]  # name given in config flow

    entities: list[ZteNgRouterSensor] = []
    for key, name, dev_class, unit, state_class in SENSOR_DEFS:
        use_coordinator = (
            coordinator_fast
            if coordinator_fast is not None and key in fast_keys
            else coordinator
        )

        entities.append(
            ZteNgRouterSensor(
                coordinator=use_coordinator,
                entry_id=entry.entry_id,
                router_name=router_name,
                key=key,
                name=name,
                device_class=dev_class,
                unit=unit,
                state_class=state_class,
                entity_category=(EntityCategory.DIAGNOSTIC if key in DIAGNOSTIC_SENSOR_KEYS else None),
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
        entity_category: EntityCategory | None,
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
        if entity_category is not None:
            self._attr_entity_category = entity_category

    @property
    def native_value(self) -> Any:
        data: dict[str, Any] = self.coordinator.data or {}
        return _extract_value(data, self._key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs: dict[str, Any] = {}

        description = SENSOR_DESCRIPTIONS.get(self._key)
        if description:
            attrs["description"] = description

        legend = SENSOR_VALUE_LEGENDS.get(self._key)
        if legend:
            attrs["value_legend"] = legend

        if self._key not in {
            "sms_count",
            "sms_latest",
            "sms_unread_total",
            "sms_nv_total",
            "sms_sim_total",
            "sms_nv_used_total",
        }:
            return attrs or None

        data: dict[str, Any] = self.coordinator.data or {}
        sms = data.get("sms") or {}
        capacity = sms.get("capacity") or {}
        messages = sms.get("messages") or []
        if not isinstance(messages, list):
            messages = []

        latest = sms.get("latest") if isinstance(sms.get("latest"), dict) else None
        sms_attrs: dict[str, Any] = {
            "total_messages": len(messages),
            "capacity_sms_nv_total": capacity.get("sms_nv_total"),
            "capacity_sms_sim_total": capacity.get("sms_sim_total"),
            "capacity_sms_nvused_total": capacity.get("sms_nvused_total"),
            "capacity_sms_nv_rev_total": capacity.get("sms_nv_rev_total"),
            "capacity_sms_nv_send_total": capacity.get("sms_nv_send_total"),
            "capacity_sms_nv_draftbox_total": capacity.get("sms_nv_draftbox_total"),
            "capacity_sms_sim_rev_total": capacity.get("sms_sim_rev_total"),
            "capacity_sms_sim_send_total": capacity.get("sms_sim_send_total"),
            "capacity_sms_sim_draftbox_total": capacity.get("sms_sim_draftbox_total"),
            "capacity_sms_dev_unread_num": capacity.get("sms_dev_unread_num"),
            "capacity_sms_sim_unread_num": capacity.get("sms_sim_unread_num"),
        }

        if latest:
            sms_attrs["latest_id"] = latest.get("id")
            sms_attrs["latest_number"] = latest.get("number")
            sms_attrs["latest_tag"] = latest.get("tag")
            sms_attrs["latest_date"] = _parse_sms_date(latest.get("date")) or latest.get("date")
            sms_attrs["latest_text"] = _truncate_text(latest.get("content_decoded"), 500)

        # Keep only a compact preview in attributes.
        preview: list[dict[str, Any]] = []
        for msg in messages[:5]:
            if not isinstance(msg, dict):
                continue
            preview.append(
                {
                    "id": msg.get("id"),
                    "number": msg.get("number"),
                    "date": _parse_sms_date(msg.get("date")) or msg.get("date"),
                    "tag": msg.get("tag"),
                    "text": _truncate_text(msg.get("content_decoded"), 140),
                }
            )
        sms_attrs["recent_messages"] = preview
        attrs.update(sms_attrs)
        return attrs
