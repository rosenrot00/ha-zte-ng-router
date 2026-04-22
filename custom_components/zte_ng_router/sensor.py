from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTemperature,
)
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

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
    ("nr_active_band", "NR Active Band", None, None, None),
    ("lte_primary_band", "LTE Primary Band", None, None, None),
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

    # NR / 5G metrics
    ("nr_pci", "NR PCI", None, None, None),
    ("nr_arfcn", "NR ARFCN", None, None, None),
    ("nr_rsrp", "NR RSRP", None, "dBm", SensorStateClass.MEASUREMENT),
    ("nr_rsrq", "NR RSRQ", None, "dB", SensorStateClass.MEASUREMENT),
    ("nr_sinr", "NR SINR", None, "dB", SensorStateClass.MEASUREMENT),
    ("nr_rssi", "NR RSSI", None, "dBm", SensorStateClass.MEASUREMENT),

    # WAN / system
    ("wan_ipv4", "WAN IPv4", None, None, None),
    ("wan_ipv6", "WAN IPv6", None, None, None),
    ("wan_status", "WAN Status", None, None, None),
    ("wan_link_state", "WAN Link State", None, None, None),
    ("modem_main_state", "Modem Main State", None, None, None),
    ("radio_off", "Radio Off", None, None, None),
    ("hightemp_datalimit_status", "High Temperature Limit Status", None, None, None),
    ("5g_modem_temperature", "5G Modem Temperature", SensorDeviceClass.TEMPERATURE,
     UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT),
    ("modem_temperature", "Modem Temperature", SensorDeviceClass.TEMPERATURE,
     UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT),
    ("pa_temp_level", "PA Temp Level", None, None, None),
    ("tj_temp_level", "TJ Temp Level", None, None, None),
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
    ("connected_time", "Connected Since", SensorDeviceClass.TIMESTAMP,
     None, None),
    ("hardware_version", "Hardware Version", None, None, None),
    ("wa_inner_version", "WA Inner Version", None, None, None),
    ("cpu_usage", "CPU Usage", None, PERCENTAGE, SensorStateClass.MEASUREMENT),
    ("cpu_temp", "CPU Temperature", SensorDeviceClass.TEMPERATURE,
     UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT),
    ("uptime", "Device Started", SensorDeviceClass.TIMESTAMP,
     None, None),
]


DIAGNOSTIC_SENSOR_KEYS = {
    # Wi-Fi configuration/status details; actual control lives on switch entities.
    "wifi_onoff",
    "main2g_ssid",
    "main5g_ssid",

    # Network identifiers and lock/debug values.
    "rmcc",
    "rmnc",
    "nr_active_band",
    "lte_primary_band",
    "nr5g_cell_id",
    "lac_code",
    "lte_band_lock",
    "gw_band_lock",

    # Low-level radio diagnostics.
    "lte_pci",
    "lte_earfcn",
    "lte_rsrp",
    "lte_rsrq",
    "lte_sinr",
    "lte_rssi",
    "nr_pci",
    "nr_arfcn",
    "nr_rsrp",
    "nr_rsrq",
    "nr_sinr",
    "nr_rssi",

    # Interface and device health details.
    "wan_ipv4",
    "wan_ipv6",
    "wan_link_state",
    "modem_main_state",
    "radio_off",
    "hightemp_datalimit_status",
    "5g_modem_temperature",
    "modem_temperature",
    "pa_temp_level",
    "tj_temp_level",
    "hardware_version",
    "wa_inner_version",
    "cpu_usage",
    "cpu_temp",
    "uptime",

    # SMS storage internals; unread/latest/count remain normal sensors.
    "sms_nv_total",
    "sms_sim_total",
    "sms_nv_used_total",
}


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

def _to_bit_per_s(value: Any) -> Any:
    """Convert router speed value to bit/s.

    Router values are byte/s. Convert to bit/s for HA data_rate sensors.
    """
    v = _as_number(value)
    if v is None:
        return None
    return int(v * 8.0)


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


def _first_number(*values: Any) -> Any:
    """Return the first valid numeric value from router payload candidates."""
    for value in values:
        v = _as_number(value)
        if v is not None:
            return v
    return None


def _first_number_or_text(*values: Any) -> Any:
    """Return first usable candidate, preferring numeric normalization."""
    for value in values:
        v = _as_number(value)
        if v is not None:
            return v
        txt = _as_text(value)
        if txt is not None:
            return txt
    return None


def _cpu_usage_from_cpuinfo(cpuinfo: Any) -> Any:
    """Return total CPU usage percentage from ZTE cpuinfo payload."""
    if isinstance(cpuinfo, list):
        total_entry = None
        for item in cpuinfo:
            if isinstance(item, dict) and str(item.get("name", "")).lower() == "all":
                total_entry = item
                break
        if total_entry is None:
            total_entry = next((item for item in cpuinfo if isinstance(item, dict)), None)
        if isinstance(total_entry, dict):
            direct = _first_number(
                total_entry.get("usage"),
                total_entry.get("cpu_usage"),
                total_entry.get("cpuUsage"),
            )
            if direct is not None:
                return max(0, min(100, round(direct, 2)))
            idle = _as_number(total_entry.get("idle"))
            if idle is not None:
                return max(0, min(100, round(100 - idle, 2)))

    if isinstance(cpuinfo, dict):
        direct = _first_number(
            cpuinfo.get("usage"),
            cpuinfo.get("cpu_usage"),
            cpuinfo.get("cpuUsage"),
        )
        if direct is not None:
            return max(0, min(100, round(direct, 2)))
        idle = _as_number(cpuinfo.get("idle"))
        if idle is not None:
            return max(0, min(100, round(100 - idle, 2)))

    return None


def _nr_band_from_arfcn(value: Any) -> int | None:
    """Map NR ARFCN to band number for common ZTE-reported bands."""
    try:
        arfcn = int(value)
    except (TypeError, ValueError):
        return None

    nr_bands = [
        (1, 422000, 434000),
        (3, 361000, 376000),
        (5, 173800, 178800),
        (7, 524000, 538000),
        (8, 185000, 192000),
        (28, 151600, 160600),
        (40, 460000, 480000),
        (41, 499200, 537999),
        (75, 286400, 303400),
        (78, 620000, 653333),
        (79, 693334, 733333),
    ]
    for band, start, end in nr_bands:
        if start <= arfcn <= end:
            return band
    return None


def _lte_band_from_earfcn(value: Any) -> int | None:
    """Map LTE EARFCN to band number for common ZTE-reported bands."""
    try:
        earfcn = int(value)
    except (TypeError, ValueError):
        return None

    lte_bands = [
        (1, 0, 599),
        (3, 1200, 1949),
        (4, 1950, 2399),
        (5, 2400, 2649),
        (7, 2750, 3449),
        (8, 3450, 3799),
        (20, 6150, 6449),
        (28, 9210, 9659),
        (32, 9920, 10359),
        (38, 37750, 38249),
        (40, 38650, 39649),
        (42, 41590, 43589),
        (43, 43590, 45589),
    ]
    for band, start, end in lte_bands:
        if start <= earfcn <= end:
            return band
    return None


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


def _seconds_ago_to_datetime(value: Any) -> datetime | None:
    """Convert a router-reported duration in seconds to an aware UTC datetime."""
    seconds = _as_number(value)
    if seconds is None or seconds < 0:
        return None
    return dt_util.utcnow() - timedelta(seconds=float(seconds))


def _extract_value(data: dict[str, Any], key: str) -> Any:
    """Map a logical key to a value inside the aggregated API data."""
    netinfo = data.get("netinfo") or {}
    wlan = data.get("wlan") or {}
    wifi_module = data.get("wifi_module") or {}
    thermal = data.get("thermal") or {}
    device = data.get("device") or {}
    sim_info = data.get("sim_info") or {}
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

    # Wi-Fi master switch follows the WebUI source: UCI wireless.zte_mbb.
    if key == "wifi_onoff":
        v = wifi_module.get("wifi_onoff", wlan.get("wifi_onoff"))
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

    if key == "nr_active_band":
        v = _as_text(netinfo.get("nr5g_action_band")) or _as_text(netinfo.get("wan_active_band"))
        if v is None:
            band = _nr_band_from_arfcn(netinfo.get("nr5g_action_channel"))
            return f"n{band}" if band is not None else None
        return v if v.lower().startswith("n") else f"n{v}"

    if key == "lte_primary_band":
        v = (
            _as_text(netinfo.get("lte_primary_band"))
            or _as_text(netinfo.get("lte_action_band"))
            or _as_text(netinfo.get("lte_pcell_band"))
            or _as_text(netinfo.get("network_lte_ca_pcell_band"))
        )
        if v is None:
            active_band = _as_text(netinfo.get("wan_active_band"))
            if active_band and not active_band.lower().startswith("n"):
                v = active_band
        if v is None:
            band = _lte_band_from_earfcn(netinfo.get("lte_action_channel"))
            return f"B{band}" if band is not None else None
        return v if v.lower().startswith("b") else f"B{v}"

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
        return _as_text(sim_info.get("modem_main_state")) or _as_text(netinfo.get("modem_main_state"))

    if key == "radio_off":
        # Newer UBUS firmwares expose radio state through zwrt_wlan.report,
        # not the older GoForm RadioOff field. Treat Wi-Fi off as radio off;
        # otherwise use per-band disabled flags when present.
        radio_off = wlan.get("RadioOff")
        if radio_off is not None:
            return str(radio_off) == "1"

        wifi_onoff = wifi_module.get("wifi_onoff", wlan.get("wifi_onoff"))
        if wifi_onoff is not None and str(wifi_onoff) == "0":
            return True

        radio2_disabled = wlan.get("radio2_disabled")
        radio5_disabled = wlan.get("radio5_disabled")
        if radio2_disabled is not None or radio5_disabled is not None:
            return str(radio2_disabled) == "1" and str(radio5_disabled) == "1"

        if wifi_onoff is not None:
            return False if str(wifi_onoff) == "1" else None
        return None

    if key == "hightemp_datalimit_status":
        return _as_text(device.get("hightemp_datalimit_status"))

    if key == "5g_modem_temperature":
        return _first_number(
            thermal.get("5g_modem_temperature"),
            thermal.get("modem_5g_temperature"),
            thermal.get("Z5g_modem_temperature"),
            thermal.get("nr5g_modem_temperature"),
            thermal.get("nr5g_modem_temp"),
            device.get("5g_modem_temperature"),
            device.get("modem_5g_temperature"),
            device.get("Z5g_modem_temperature"),
            device.get("nr5g_modem_temperature"),
            netinfo.get("5g_modem_temperature"),
            netinfo.get("modem_5g_temperature"),
            netinfo.get("Z5g_modem_temperature"),
            sim_info.get("5g_modem_temperature"),
            sim_info.get("modem_5g_temperature"),
        )

    if key == "modem_temperature":
        return _first_number(
            thermal.get("modem_temperature"),
            thermal.get("modem_temp"),
            device.get("modem_temperature"),
            device.get("modem_temp"),
            netinfo.get("modem_temperature"),
            netinfo.get("modem_temp"),
            sim_info.get("modem_temperature"),
            sim_info.get("modem_temp"),
        )

    if key == "pa_temp_level":
        return _first_number_or_text(
            thermal.get("pa_temp_level"),
            thermal.get("pa_temperature_level"),
            device.get("pa_temp_level"),
            device.get("pa_temperature_level"),
            netinfo.get("pa_temp_level"),
            sim_info.get("pa_temp_level"),
        )

    if key == "tj_temp_level":
        return _first_number_or_text(
            thermal.get("tj_temp_level"),
            thermal.get("tj_temperature_level"),
            device.get("tj_temp_level"),
            device.get("tj_temperature_level"),
            netinfo.get("tj_temp_level"),
            sim_info.get("tj_temp_level"),
        )

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
        # Represent the current session as the timestamp when the connection started.
        v = wan.get("real_time")
        if v in (None, "", "-"):
            wwandst = data.get("wwandst") or {}
            v = wwandst.get("real_time")
        return _seconds_ago_to_datetime(v)

    if key == "hardware_version":
        return common_config.get("hardware_version")

    if key == "wa_inner_version":
        return common_config.get("wa_inner_version")

    if key == "cpu_usage":
        v = _cpu_usage_from_cpuinfo(device.get("cpuinfo"))
        if v is not None:
            return v
        return _first_number(
            device.get("cpu_usage"),
            device.get("cpuUsage"),
            device.get("cpu_load"),
            device.get("cpuload"),
        )

    if key == "cpu_temp":
        return _as_number(thermal.get("cpuss_temp"))

    if key == "uptime":
        return _seconds_ago_to_datetime(device.get("device_uptime"))

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
    fast_keys = {"connected_time", "download_rate", "upload_rate", "cpu_usage"}
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
        if key in DIAGNOSTIC_SENSOR_KEYS:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> Any:
        data: dict[str, Any] = self.coordinator.data or {}
        return _extract_value(data, self._key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._key not in {
            "sms_count",
            "sms_latest",
            "sms_unread_total",
            "sms_nv_total",
            "sms_sim_total",
            "sms_nv_used_total",
        }:
            return None

        data: dict[str, Any] = self.coordinator.data or {}
        sms = data.get("sms") or {}
        capacity = sms.get("capacity") or {}
        messages = sms.get("messages") or []
        if not isinstance(messages, list):
            messages = []

        latest = sms.get("latest") if isinstance(sms.get("latest"), dict) else None
        attrs: dict[str, Any] = {
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
            attrs["latest_id"] = latest.get("id")
            attrs["latest_number"] = latest.get("number")
            attrs["latest_tag"] = latest.get("tag")
            attrs["latest_date"] = _parse_sms_date(latest.get("date")) or latest.get("date")
            attrs["latest_text"] = _truncate_text(latest.get("content_decoded"), 500)

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
        attrs["recent_messages"] = preview
        return attrs
