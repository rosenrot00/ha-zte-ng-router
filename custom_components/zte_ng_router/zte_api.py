from __future__ import annotations

import asyncio
import logging
import json
import time
import string
from datetime import datetime
from typing import Any, Optional

import aiohttp
from aiohttp import ClientError, ClientSession, CookieJar
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)


class ZteRouterApi:
    """Async low-level API wrapper for ZTE 5G routers (e.g. G5TC)."""
    _GSM7_TABLE_HEX = {
        "000A", "000C", "000D", "0020", "0021", "0022", "0023", "0024", "0025",
        "0026", "0027", "0028", "0029", "002A", "002B", "002C", "002D", "002E",
        "002F", "0030", "0031", "0032", "0033", "0034", "0035", "0036", "0037",
        "0038", "0039", "003A", "003B", "003C", "003D", "003E", "003F", "0040",
        "0041", "0042", "0043", "0044", "0045", "0046", "0047", "0048", "0049",
        "004A", "004B", "004C", "004D", "004E", "004F", "0050", "0051", "0052",
        "0053", "0054", "0055", "0056", "0057", "0058", "0059", "005A", "005B",
        "005C", "005D", "005E", "005F", "0061", "0062", "0063", "0064", "0065",
        "0066", "0067", "0068", "0069", "006A", "006B", "006C", "006D", "006E",
        "006F", "0070", "0071", "0072", "0073", "0074", "0075", "0076", "0077",
        "0078", "0079", "007A", "007B", "007C", "007D", "007E", "00A0", "00A1",
        "00A3", "00A4", "00A5", "00A7", "00BF", "00C4", "00C5", "00C6", "00C7",
        "00C9", "00D1", "00D6", "00D8", "00DC", "00DF", "00E0", "00E4", "00E5",
        "00E6", "00E8", "00E9", "00EC", "00F1", "00F2", "00F6", "00F8", "00F9",
        "00FC", "0393", "0394", "0398", "039B", "039E", "03A0", "03A3", "03A6",
        "03A8", "03A9", "20AC",
    }
    _GSM7_TABLE_EXT_HEX = {
        "007B", "007D", "005B", "005D", "007E", "005C", "005E", "20AC", "007C",
    }

    def __init__(
        self,
        hass: HomeAssistant | None = None,
        base_url: str = "",
        password: str = "",
        router_type: str = "",
        verify_tls: bool = True,
        session: ClientSession | None = None,
    ) -> None:
        # base_url like "http://192.168.254.1" or "https://192.168.254.1"
        self.hass = hass
        self.base_url = base_url.rstrip("/")
        self.password = password
        self.router_type = router_type
        self.verify_tls = verify_tls

        # Use Home Assistant managed aiohttp session when hass is available.
        # If hass is not provided (e.g. due to an integration bug), fall back to a standalone session.
        if session is not None:
            self._session = session
        elif hass is not None:
            # verify_tls=False allows self-signed certificates.
            self._session = async_get_clientsession(hass, verify_ssl=verify_tls)
        else:
            _LOGGER.warning(
                "ZteRouterApi initialized without hass; falling back to a standalone aiohttp session"
            )
            connector = aiohttp.TCPConnector(ssl=verify_tls)
            jar = CookieJar(unsafe=True)
            self._session = aiohttp.ClientSession(connector=connector, cookie_jar=jar)

        self._session_id: Optional[str] = None
        self._logged_in: bool = False
        self._webtoken: Optional[str] = None

        self._auth_lock = asyncio.Lock()

        # API mode: "ubus" (default) or "goform" (auto-detected during login)
        self._api_mode: str = "ubus"
        self._goform_session: Optional[aiohttp.ClientSession] = None

        # Headers similar to the JS script environment
        self._base_headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Z-Mode": "1",
            "Origin": self.base_url,
            "Referer": self.base_url + "/index.html",
        }

    # --------------------------------------------------------------------
    # Helper: ubus URL
    # --------------------------------------------------------------------
    def _ubus_url(self) -> str:
        # WebUI uses a cache-busting timestamp query param
        return f"{self.base_url}/ubus/?t={int(time.time() * 1000)}"

    # --------------------------------------------------------------------
    # Helper: hashing
    # --------------------------------------------------------------------
    @staticmethod
    def sha256(text: str) -> str:
        import hashlib

        return hashlib.sha256(text.encode("utf-8")).hexdigest().upper()

    @staticmethod
    def _is_conn_reset_104(exc: BaseException) -> bool:
        """Return True if exception chain contains errno 104 (Connection reset by peer)."""
        seen: set[int] = set()
        cur: BaseException | None = exc

        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))

            if isinstance(cur, ConnectionResetError) and getattr(cur, "errno", None) == 104:
                return True
            if isinstance(cur, OSError) and getattr(cur, "errno", None) == 104:
                return True

            cur = cur.__cause__ or cur.__context__

        return False

    def _update_webtoken_from_response(self, resp: aiohttp.ClientResponse) -> None:
        """Capture rotating 'webtoken' from response cookies (Set-Cookie)."""
        try:
            c = resp.cookies.get("webtoken")
            if c is not None and c.value:
                new_token = c.value.strip('"')
                if new_token and new_token != self._webtoken:
                    _LOGGER.debug("Updated webtoken from response")
                    self._webtoken = new_token
        except Exception:
            # Best-effort only
            pass

    def _apply_webtoken_cookie(self, headers: dict[str, str]) -> None:
        """Attach current webtoken cookie to request headers."""
        if self._webtoken:
            headers["Cookie"] = f'webtoken="{self._webtoken}"'

    @staticmethod
    def _decode_sms_content(raw_content: Any) -> str:
        """Decode SMS payload returned as hex string (typically UCS2/UTF-16BE)."""
        if raw_content is None:
            return ""

        text = str(raw_content).strip()
        if not text:
            return ""

        is_hex = len(text) % 2 == 0 and all(c in string.hexdigits for c in text)
        if not is_hex:
            return text

        try:
            payload = bytes.fromhex(text)
        except ValueError:
            return text

        for encoding in ("utf-16-be", "utf-8", "latin-1"):
            try:
                return payload.decode(encoding).strip("\x00\r\n")
            except UnicodeDecodeError:
                continue
        return text

    @staticmethod
    def parse_sms_compose_input(value: str) -> tuple[str, str]:
        """Parse and validate compose value in format 'number,message'."""
        raw = (value or "").strip()
        if "," not in raw:
            raise ValueError("Expected format: number,message")

        number_raw, message_raw = raw.split(",", 1)
        number = number_raw.strip().replace(" ", "")
        message = message_raw.strip()

        if not number:
            raise ValueError("Missing destination number")
        if not all(c.isdigit() or c == "+" for c in number):
            raise ValueError("Number may contain only '+' and digits")
        if number.count("+") > 1 or ("+" in number and not number.startswith("+")):
            raise ValueError("Invalid '+' placement in number")
        if not message:
            raise ValueError("Message must not be empty")

        return number, message

    @staticmethod
    def _build_sms_time_string() -> str:
        """Build router expected SMS time string: yy;MM;dd;HH;mm;ss;+TZ."""
        now = datetime.now().astimezone()
        offset = now.utcoffset()
        tz_hours = (offset.total_seconds() / 3600.0) if offset is not None else 0.0
        if abs(tz_hours - int(tz_hours)) < 1e-9:
            tz_num = str(int(tz_hours))
        else:
            tz_num = str(tz_hours).rstrip("0").rstrip(".")
        tz_part = f"+{tz_num}" if tz_hours >= 0 else tz_num
        return (
            f"{now.strftime('%y')};{now.strftime('%m')};{now.strftime('%d')};"
            f"{now.strftime('%H')};{now.strftime('%M')};{now.strftime('%S')};{tz_part}"
        )

    @staticmethod
    def _get_sms_encode_type(message: str) -> str:
        """Return modem encode type for SMS."""
        for ch in message:
            cp_hex = f"{ord(ch):04X}"
            if (
                cp_hex not in ZteRouterApi._GSM7_TABLE_HEX
                and cp_hex not in ZteRouterApi._GSM7_TABLE_EXT_HEX
            ):
                return "UNICODE"
        return "GSM7_default"

    @staticmethod
    def _encode_sms_message(message: str) -> str:
        """Encode SMS message like WebUI helper encodeMessage()."""
        return message.encode("utf-16-be", errors="ignore").hex().upper()

    # --------------------------------------------------------------------
    # Band helpers (simplified)
    # --------------------------------------------------------------------
    @staticmethod
    def _convert_lte_earfcn_to_band(earfcn: int | None) -> int | None:
        """Map LTE EARFCN to band number (subset of common bands)."""
        if earfcn is None:
            return None

        # [band, n_min, n_max]
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
        for band, nmin, nmax in lte_bands:
            if nmin <= earfcn <= nmax:
                return band

        return None

    @staticmethod
    def _convert_nr_arfcn_to_band(arfcn: int | None) -> int | None:
        """Map NR ARFCN to band number (subset of n1/n3/n28/n78...)."""
        if arfcn is None:
            return None

        # [band, n_min, n_max]
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

        for band, nmin, nmax in nr_bands:
            if nmin <= arfcn <= nmax:
                return band

        return None

    def _compute_bands_and_bw(self, netinfo: dict[str, Any]) -> tuple[str, float]:
        """Compute a simple band summary and total bandwidth in MHz."""
        bands: list[str] = []
        total_bw: float = 0.0

        # LTE primary
        try:
            lte_earfcn = int(netinfo.get("lte_action_channel"))
        except (TypeError, ValueError):
            lte_earfcn = None
        try:
            lte_bw = float(netinfo.get("lte_bandwidth"))
        except (TypeError, ValueError):
            lte_bw = None

        if lte_earfcn is not None and lte_bw is not None and lte_bw > 0:
            b = self._convert_lte_earfcn_to_band(lte_earfcn)
            if b is not None:
                bands.append(f"B{b}")
            total_bw += lte_bw

        # NR primary
        try:
            nr_arfcn = int(netinfo.get("nr5g_action_channel"))
        except (TypeError, ValueError):
            nr_arfcn = None
        try:
            nr_bw = float(netinfo.get("nr5g_bandwidth"))
        except (TypeError, ValueError):
            nr_bw = None

        if nr_arfcn is not None and nr_bw is not None and nr_bw > 0:
            b = self._convert_nr_arfcn_to_band(nr_arfcn)
            if b is not None:
                bands.append(f"n{b}")
            total_bw += nr_bw

        bands_summary = " + ".join(bands) if bands else "-"
        return bands_summary, total_bw

    # --------------------------------------------------------------------
    # Authentication
    # --------------------------------------------------------------------
    async def async_init_session(self) -> None:
        """Fetch the start page to get cookies/CSRF (if any)."""
        url = self.base_url + "/"
        try:
            async with self._session.get(url, timeout=10) as resp:
                await resp.read()
                self._update_webtoken_from_response(resp)
                _LOGGER.debug("async_init_session: status=%s", resp.status)
        except (ClientError, asyncio.TimeoutError, OSError) as exc:
            _LOGGER.warning("async_init_session GET %s failed: %s", url, exc)

    async def async_login(self) -> None:
        """Perform login.

        Try UBUS first for compatibility/performance. If UBUS is unavailable on this
        firmware (or otherwise fails), always attempt GoForm as fallback.
        """
        ubus_exc: Exception | None = None

        try:
            await self._async_ubus_login()
            self._api_mode = "ubus"
            return
        except Exception as exc:
            ubus_exc = exc
            _LOGGER.info("UBUS login failed (%s), trying GoForm fallback", exc)

        try:
            await self._async_goform_login()
            return
        except Exception as goform_exc:
            if ubus_exc is None:
                raise RuntimeError(f"GoForm login failed: {goform_exc}") from goform_exc
            raise RuntimeError(
                f"Login failed: UBUS={ubus_exc}; GoForm={goform_exc}"
            ) from goform_exc

    async def _async_ubus_login(self) -> None:
        """Perform UBUS-based web_login to ZTE router and store ubus session ID."""

        # 1) get salt
        salt_req = {
            "service": "zwrt_web",
            "method": "web_login_info",
            "params": {},
        }
        salt_res = await self.async_call_ubus(
            salt_req,
            session_id="0" * 32,
            retry_on_access_denied=False,
        )

        data = salt_res.get("data") or {}
        salt = data.get("zte_web_sault")
        if not salt:
            raise RuntimeError("Could not retrieve login salt")

        # 2) final hash = SHA256(SHA256(password) + salt)
        pw_hash = self.sha256(self.password)
        final = self.sha256(pw_hash + salt)

        # 3) login
        login_req = {
            "service": "zwrt_web",
            "method": "web_login",
            "params": {"password": final},
        }
        login_res = await self.async_call_ubus(
            login_req,
            session_id="0" * 32,
            retry_on_access_denied=False,
        )
        d = login_res.get("data") or {}
        sid = d.get("ubus_rpc_session")
        if not sid:
            raise RuntimeError(f"Login failed, response: {d}")

        self._session_id = sid
        self._logged_in = True
        _LOGGER.info("ZTE NG Router UBUS login successful")

    # --------------------------------------------------------------------
    # GoForm helpers
    # --------------------------------------------------------------------
    async def _async_get_goform_session(self) -> aiohttp.ClientSession:
        """Return (creating if needed) a dedicated aiohttp session with unsafe cookie jar."""
        if self._goform_session is None:
            ssl_ctx: bool = False if not self.verify_tls else True
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            jar = aiohttp.CookieJar(unsafe=True)
            self._goform_session = aiohttp.ClientSession(connector=connector, cookie_jar=jar)
        return self._goform_session

    async def _async_goform_get(
        self, cmd: str, extra_params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """GET /goform/goform_get_cmd_process?cmd=<cmd>&…"""
        session = await self._async_get_goform_session()
        params: dict[str, str] = {
            "isTest": "false",
            "cmd": cmd,
            "multi_data": "1",
            "_": str(int(time.time() * 1000)),
        }
        if extra_params:
            params.update(extra_params)
        url = f"{self.base_url}/goform/goform_get_cmd_process"
        headers = {
            "Referer": f"{self.base_url}/index.html",
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            async with session.get(
                url, params=params, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                return await resp.json(content_type=None)
        except Exception as exc:
            _LOGGER.warning("GoForm GET %s failed: %s", cmd, exc)
            return {}

    async def _async_goform_set(self, data: dict[str, str]) -> dict[str, Any]:
        """POST /goform/goform_set_cmd_process with URL-encoded form data."""
        session = await self._async_get_goform_session()
        url = f"{self.base_url}/goform/goform_set_cmd_process"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": f"{self.base_url}/index.html",
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            async with session.post(
                url, data=data, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                return await resp.json(content_type=None)
        except Exception as exc:
            _LOGGER.warning("GoForm POST failed: %s", exc)
            return {}

    async def _async_goform_init_session(self) -> None:
        """Load router index page to establish GoForm session cookie."""
        session = await self._async_get_goform_session()
        url = f"{self.base_url}/index.html"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                await resp.read()
                _LOGGER.debug("GoForm session init: status=%s", resp.status)
        except Exception as exc:
            _LOGGER.warning("GoForm session init GET %s failed: %s", url, exc)

    async def _async_goform_login(self) -> None:
        """Login via GoForm API (SHA256(SHA256(password).upper() + LD))."""
        import hashlib
        # 1. Establish session cookie
        await self._async_goform_init_session()
        # 2. Get LD nonce
        ld_resp = await self._async_goform_get("LD")
        ld = ld_resp.get("LD", "")
        if not ld:
            raise RuntimeError("GoForm: LD nonce is empty; cannot login")
        # 3. Hash: SHA256(SHA256(password).upper() + LD)
        h1 = hashlib.sha256(self.password.encode("utf-8")).hexdigest().upper()
        final = hashlib.sha256((h1 + ld).encode("utf-8")).hexdigest().upper()
        # 4. POST login
        login_resp = await self._async_goform_set({
            "isTest": "false",
            "goformId": "LOGIN",
            "password": final,
        })
        result = login_resp.get("result", "")
        if result != "0":
            raise RuntimeError(f"GoForm login failed: result={result!r}")
        self._logged_in = True
        self._api_mode = "goform"
        _LOGGER.info("ZTE GoForm login successful")

    @staticmethod
    def _goform_has_usable_payload(values: dict[str, Any]) -> bool:
        """Heuristic to detect empty/invalid GoForm responses.

        Some firmwares return mostly empty objects when the session expired.
        """
        probe_keys = (
            "wa_inner_version",
            "network_type",
            "network_provider_fullname",
            "ppp_status",
            "network_rmcc",
            "wifi_chip1_ssid1_ssid",
        )
        return any(values.get(k) not in (None, "", "-", "null") for k in probe_keys)

    async def _async_goform_update_all(self) -> dict[str, Any]:
        """Fetch all router data via GoForm API using SINGLE-field queries.
        
        IMPORTANT: GoForm API does NOT support multi-field queries (e.g., "field1,field2").
        We must query each field individually. To reduce latency, we use asyncio.gather()
        to make many requests in parallel (10+ concurrent requests).
        """
        # Define which fields we need (grouped for documentation)
        basic_fields = [
            "network_type", "network_signalbar", "network_provider_fullname",
            "lac_code", "lte_band_lock", "gw_band_lock",
            "lte_pci", "lte_rssi", "lte_rsrq", "lte_snr", "network_lte_rsrp",
            "Z5g_rsrp", "Z5g_rsrq", "Z5g_snr", "Z5g_SINR", "Z5g_rssi",
            "network_Z5g_PCI", "network_Z5g_CELL_ID", "nr5g_action_channel",
            "wan_active_channel", "network_lte_ca_pcell_arfcn",
            "network_lte_ca_pcell_band", "network_lte_ca_pcell_bandwidth",
            "nr5g_action_band", "nr5g_nsa_bandwidth",
            "network_rmcc", "network_rmnc",
            "wifi_onoff_state", "wifi_chip1_ssid1_ssid", "wifi_chip2_ssid1_ssid",
            "wifi_chip1_ssid1_switch_onoff", "wifi_chip2_ssid1_switch_onoff",
            "wifi_chip1_ssid1_access_sta_num", "wifi_chip2_ssid1_access_sta_num",
            "ODU_led_switch",
            "mwan_wanlan1_wan_ipaddr", "mwan_wanlan1_link_state", "mwan_wanlan1_ipv6_wan_ipaddr",
            "ppp_status", "mc_modem_main_state", "RadioOff", "wan_ipaddr", "ipv6_wan_ipaddr",
            "hardware_version", "wa_inner_version",
            "wifi_chip_temp", "pm_sensor_mdm", "pm_modem_5g", "therm_pa_level", "therm_tj_level",
            "system_uptime", "device_uptime",
            "flux_realtime_rx_thrpt", "flux_realtime_tx_thrpt", "flux_realtime_time",
            "flux_monthly_rx_bytes", "flux_monthly_tx_bytes",
        ]

        # Query all basic fields in parallel (GoForm API limitation: only single fields work)
        field_tasks = [self._async_goform_get(field) for field in basic_fields]
        field_results = await asyncio.gather(*field_tasks, return_exceptions=False)

        # Aggregate results into a single dict
        main = {}
        for field_name, result in zip(basic_fields, field_results):
            if isinstance(result, dict) and field_name in result:
                main[field_name] = result[field_name]
            else:
                main[field_name] = None

        if not self._goform_has_usable_payload(main):
            raise RuntimeError("GoForm returned no usable payload (session expired or unsupported response)")

        # Query special endpoints
        lan_resp = await self._async_goform_get("lan_station_list")
        sms_cap_resp = await self._async_goform_get("sms_capacity_info")
        sms_msgs_resp = await self._async_goform_get(
            "sms_data_total",
            {"page": "0", "data_per_page": "50", "mem_store": "1",
             "tags": "10", "order_by": "order by id desc"},
        )

        # Build response structure (same as UBUS update)
        netinfo: dict[str, Any] = {
            "network_type": main.get("network_type"),
            "network_provider_fullname": main.get("network_provider_fullname"),
            "signalbar": main.get("network_signalbar"),
            "rmcc": main.get("network_rmcc"),
            "rmnc": main.get("network_rmnc"),
            "lac_code": main.get("lac_code"),
            "lte_band_lock": main.get("lte_band_lock"),
            "gw_band_lock": main.get("gw_band_lock"),
            "nr5g_cell_id": main.get("network_Z5g_CELL_ID"),
            "lte_pci": main.get("lte_pci"),
            "lte_action_channel": main.get("wan_active_channel") or main.get("network_lte_ca_pcell_arfcn"),
            "lte_action_band": main.get("network_lte_ca_pcell_band"),
            "lte_bandwidth": main.get("network_lte_ca_pcell_bandwidth"),
            "lte_rsrp": main.get("network_lte_rsrp"),
            "lte_rsrq": main.get("lte_rsrq"),
            "lte_snr": main.get("lte_snr"),
            "lte_rssi": main.get("lte_rssi"),
            "nr5g_pci": main.get("network_Z5g_PCI"),
            "nr5g_action_channel": main.get("nr5g_action_channel"),
            "nr5g_action_band": main.get("nr5g_action_band"),
            "nr5g_bandwidth": main.get("nr5g_nsa_bandwidth"),
            "nr5g_rsrp": main.get("Z5g_rsrp"),
            "nr5g_rsrq": main.get("Z5g_rsrq"),
            "nr5g_snr": main.get("Z5g_SINR") or main.get("Z5g_snr"),
            "nr5g_rssi": main.get("Z5g_rssi"),
        }
        wlan: dict[str, Any] = {
            "wifi_onoff": main.get("wifi_onoff_state"),
            "main2g_ssid": main.get("wifi_chip1_ssid1_ssid"),
            "main5g_ssid": main.get("wifi_chip2_ssid1_ssid"),
        }
        wan: dict[str, Any] = {
            "mwan_wanlan1_wan_ipaddr": main.get("mwan_wanlan1_wan_ipaddr") or main.get("wan_ipaddr"),
            "mwan_wanlan1_ipv6_wan_ipaddr": main.get("mwan_wanlan1_ipv6_wan_ipaddr") or main.get("ipv6_wan_ipaddr"),
            "mwan_wanlan1_link_state": main.get("mwan_wanlan1_link_state"),
            "current_wan_status": main.get("ppp_status"),
            "lte_connect_status": main.get("mc_modem_main_state"),
            "radio_off": main.get("RadioOff"),
            "real_rx_speed": main.get("flux_realtime_rx_thrpt"),
            "real_tx_speed": main.get("flux_realtime_tx_thrpt"),
            "real_time": main.get("flux_realtime_time"),
        }
        lan_list = lan_resp.get("lan_station_list") if isinstance(lan_resp, dict) else None
        lan_count = len(lan_list) if isinstance(lan_list, list) else 0
        try:
            wifi2g = int(main.get("wifi_chip1_ssid1_access_sta_num") or 0)
        except (TypeError, ValueError):
            wifi2g = 0
        try:
            wifi5g = int(main.get("wifi_chip2_ssid1_access_sta_num") or 0)
        except (TypeError, ValueError):
            wifi5g = 0
        user_list_num: dict[str, Any] = {
            "lan_num": lan_count,
            "wireless_num": wifi2g + wifi5g,
        }
        common_config: dict[str, Any] = {
            "hardware_version": main.get("hardware_version"),
            "wa_inner_version": main.get("wa_inner_version"),
        }
        wwandst: dict[str, Any] = {
            "real_rx_speed": main.get("flux_realtime_rx_thrpt"),
            "real_tx_speed": main.get("flux_realtime_tx_thrpt"),
            "real_time": main.get("flux_realtime_time"),
        }
        wwandst_monthly: dict[str, Any] = {
            "month_rx_bytes": main.get("flux_monthly_rx_bytes"),
            "month_tx_bytes": main.get("flux_monthly_tx_bytes"),
        }
        sms_cap_raw = sms_cap_resp if isinstance(sms_cap_resp, dict) else {}
        try:
            nv_used = (
                int(sms_cap_raw.get("sms_nv_rev_total") or 0)
                + int(sms_cap_raw.get("sms_nv_send_total") or 0)
                + int(sms_cap_raw.get("sms_nv_draftbox_total") or 0)
            )
        except (TypeError, ValueError):
            nv_used = 0
        try:
            unread = (
                int(sms_cap_raw.get("sms_nv_rev_total") or 0)
                + int(sms_cap_raw.get("sms_sim_rev_total") or 0)
            )
        except (TypeError, ValueError):
            unread = 0
        sms_cap_mapped: dict[str, Any] = {
            "sms_nv_total": sms_cap_raw.get("sms_nv_total"),
            "sms_sim_total": sms_cap_raw.get("sms_sim_total"),
            "sms_nvused_total": str(nv_used),
            "sms_dev_unread_num": str(unread),
        }
        raw_messages = (sms_msgs_resp.get("messages") or []) if isinstance(sms_msgs_resp, dict) else []
        sms_messages: list[dict[str, Any]] = []
        for msg in raw_messages:
            if not isinstance(msg, dict):
                continue
            sms_messages.append({
                "id": msg.get("id"),
                "number": msg.get("number"),
                "date": msg.get("date"),
                "tag": msg.get("tag"),
                "mem_store": msg.get("mem_store"),
                "content_raw": msg.get("content"),
                "content_decoded": self._decode_sms_content(msg.get("content")),
            })

        wifi_2g_raw = main.get("wifi_chip1_ssid1_switch_onoff")
        wifi_5g_raw = main.get("wifi_chip2_ssid1_switch_onoff")
        led_raw = main.get("ODU_led_switch")
        ppp_status = str(main.get("ppp_status") or "").strip().lower()
        wwan_enable = "1" if ppp_status in {"ppp_connected", "ipv4_connected", "ipv6_connected", "ipv4_ipv6_connected", "connected"} else "0"

        wifi_main_2g: dict[str, Any] = {}
        if str(wifi_2g_raw) in {"0", "1"}:
            wifi_main_2g = {"disabled": "0" if str(wifi_2g_raw) == "1" else "1"}

        wifi_main_5g: dict[str, Any] = {}
        if str(wifi_5g_raw) in {"0", "1"}:
            wifi_main_5g = {"disabled": "0" if str(wifi_5g_raw) == "1" else "1"}

        odu_led: dict[str, Any] = {}
        if str(led_raw) in {"0", "1"}:
            odu_led = {"switch": str(led_raw)}

        thermal: dict[str, Any] | None = None
        if any(main.get(k) not in (None, "", "-") for k in (
            "wifi_chip_temp", "pm_sensor_mdm", "pm_modem_5g", "therm_pa_level", "therm_tj_level"
        )):
            thermal = {
                "cpuss_temp": main.get("wifi_chip_temp"),
                "pm_sensor_mdm": main.get("pm_sensor_mdm"),
                "pm_modem_5g": main.get("pm_modem_5g"),
                "therm_pa_level": main.get("therm_pa_level"),
                "therm_tj_level": main.get("therm_tj_level"),
            }

        device: dict[str, Any] | None = None
        uptime = main.get("system_uptime") or main.get("device_uptime")
        if uptime not in (None, "", "-"):
            device = {"device_uptime": uptime}

        bands_summary, total_bw_mhz = self._compute_bands_and_bw(netinfo)
        return {
            "netinfo": netinfo,
            "wlan": wlan,
            "wifi_main_2g": wifi_main_2g,
            "wifi_main_5g": wifi_main_5g,
            "odu_led": odu_led,
            "thermal": thermal,
            "device": device,
            "common_config": common_config,
            "wan": wan,
            "user_list_num": user_list_num,
            "wwandst": wwandst,
            "wwaniface": {"enable": wwan_enable},
            "wwandst_monthly": wwandst_monthly,
            "sms": {
                "messages": sms_messages,
                "latest": sms_messages[0] if sms_messages else None,
                "capacity": sms_cap_mapped,
            },
            "bands_summary": bands_summary,
            "total_bw_mhz": total_bw_mhz,
        }

    async def _async_goform_update_fast(self) -> dict[str, Any] | None:
        """Fetch only fast-changing stats via GoForm API."""
        rx_resp, tx_resp, time_resp = await asyncio.gather(
            self._async_goform_get("flux_realtime_rx_thrpt"),
            self._async_goform_get("flux_realtime_tx_thrpt"),
            self._async_goform_get("flux_realtime_time"),
        )
        if not any(
            node.get(key) not in (None, "", "-", "null")
            for node, key in (
                (rx_resp, "flux_realtime_rx_thrpt"),
                (tx_resp, "flux_realtime_tx_thrpt"),
                (time_resp, "flux_realtime_time"),
            )
        ):
            raise RuntimeError("GoForm fast update returned no usable values")
        return {
            "wan": {"real_time": time_resp.get("flux_realtime_time")},
            "wwandst": {
                "real_rx_speed": rx_resp.get("flux_realtime_rx_thrpt"),
                "real_tx_speed": tx_resp.get("flux_realtime_tx_thrpt"),
            },
        }

    async def _async_ensure_logged_in(self, *, force: bool = False) -> None:
        """Ensure we have a valid session (UBUS or GoForm).

        Uses a lock to prevent concurrent logins and avoids repeated logins within the same update cycle.
        """
        async with self._auth_lock:
            if not force and self._logged_in:
                # For UBUS mode also require a session_id
                if self._api_mode != "goform" and not self._session_id:
                    pass  # fall through to login
                else:
                    return

            # Discard the GoForm session so _async_goform_login() starts with a
            # completely fresh cookie jar.  Stale cookies from a previous session
            # prevent a successful re-login after a browser login has invalidated
            # the old session.
            if force and self._goform_session is not None:
                try:
                    await self._goform_session.close()
                except Exception:
                    pass
                self._goform_session = None

            await self.async_init_session()
            await self.async_login()

    # --------------------------------------------------------------------
    # ubus caller with automatic re-login on -32002
    # --------------------------------------------------------------------
    async def async_call_ubus(
        self,
        call: dict,
        session_id: Optional[str] = None,
        *,
        retry_on_access_denied: bool = True,
        retry_on_connreset_104: bool = True,
    ) -> dict:
        """Call ubus. If access denied (-32002), try a re-login and retry once."""

        if self._api_mode == "goform" and not self._session_id:
            _LOGGER.debug(
                "Skipping ubus call in goform mode without ubus session: %s.%s",
                call.get("service"),
                call.get("method"),
            )
            return {"success": False, "data": None}

        if session_id is None:
            # Lazily login only when we actually need an authenticated call.
            if not self._session_id or not self._logged_in:
                await self._async_ensure_logged_in()
            session_id = self._session_id

        req = [
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "call",
                "params": [
                    session_id,
                    call["service"],
                    call["method"],
                    call.get("params", {}) or {},
                ],
            }
        ]

        url = self._ubus_url()
        try:
            try:
                sid_preview = (session_id or "")[:8]
            except Exception:
                sid_preview = ""
            _LOGGER.debug(
                "ubus call: service=%s method=%s sid=%s",
                call.get("service"),
                call.get("method"),
                sid_preview,
            )
            headers = dict(self._base_headers)
            # Match WebUI behavior: Z-Mode=1 only for authenticated calls
            if self._logged_in and self._session_id and session_id != "0" * 32:
                headers["Z-Mode"] = "1"
            else:
                headers["Z-Mode"] = "0"

            # jQuery adds this header; some firmwares are picky for action calls
            headers.setdefault("X-Requested-With", "XMLHttpRequest")

            # Always apply webtoken cookie if available
            self._apply_webtoken_cookie(headers)

            # WebUI sets Z-Tag to the ubus method name (or UCI config name for uci.get)
            try:
                svc = str(call.get("service") or "")
                if svc == "uci":
                    ztag = str((call.get("params") or {}).get("config") or "")
                else:
                    ztag = str(call.get("method") or "")
                if ztag:
                    headers["Z-Tag"] = ztag
            except Exception:
                pass
            async with self._session.post(
                url,
                json=req,
                headers=headers,
                timeout=10,
            ) as resp:
                resp.raise_for_status()
                self._update_webtoken_from_response(resp)
                res_list = await resp.json(content_type=None)
        except Exception as exc:
            _LOGGER.warning("HTTP error while calling ubus: %s", exc)

            # Handle TCP reset-by-peer (errno 104) with a single retry
            if retry_on_connreset_104 and self._is_conn_reset_104(exc):
                _LOGGER.warning("Connection reset by peer (104), attempting re-login")

                self._logged_in = False
                self._session_id = None

                try:
                    await self._async_ensure_logged_in(force=True)
                except Exception as exc2:
                    _LOGGER.error("Re-login failed after 104: %s", exc2)
                    return {"success": False, "data": None}

                return await self.async_call_ubus(
                    call,
                    session_id=None,
                    retry_on_access_denied=retry_on_access_denied,
                    retry_on_connreset_104=False,
                )

            # Non-104 network error – mark session as stale so the next poll
            # triggers a re-login (e.g. router sent HTML redirect after a browser
            # login invalidated the HA session).
            self._logged_in = False
            self._session_id = None
            return {"success": False, "data": None}

        if not isinstance(res_list, list) or not res_list:
            _LOGGER.warning("Invalid JSON from ubus – marking session as stale for re-login")
            self._logged_in = False
            self._session_id = None
            return {"success": False, "data": None}

        res0 = res_list[0]

        _LOGGER.debug(
            "ubus response: service=%s method=%s has_error=%s",
            call.get("service"),
            call.get("method"),
            "error" in res0,
        )

        # Error case
        if "error" in res0:
            err = res0["error"]
            code = err.get("code")
            msg = err.get("message")

            # Access denied → re-login (expected on some firmwares when session expires)
            if code == -32002 and retry_on_access_denied:
                _LOGGER.debug(
                    "ubus access denied: service=%s method=%s code=%s msg=%s; attempting re-login",
                    call.get("service"),
                    call.get("method"),
                    code,
                    msg,
                )

                # Mark current session as invalid and perform a single forced re-login.
                self._logged_in = False
                self._session_id = None

                try:
                    await self._async_ensure_logged_in(force=True)
                except Exception as exc:
                    _LOGGER.error("Re-login failed: %s", exc)
                    return {"success": False, "data": None}

                return await self.async_call_ubus(
                    call,
                    session_id=None,
                    retry_on_access_denied=False,
                    retry_on_connreset_104=retry_on_connreset_104,
                )

            # Non-retryable error (or retry disabled)
            if code == -32002:
                # Avoid log spam: this can happen even after a re-login attempt on some firmwares.
                _LOGGER.debug(
                    "ubus access denied: service=%s method=%s code=%s msg=%s (no further retry)",
                    call.get("service"),
                    call.get("method"),
                    code,
                    msg,
                )
            else:
                _LOGGER.warning(
                    "ubus error: service=%s method=%s code=%s msg=%s",
                    call.get("service"),
                    call.get("method"),
                    code,
                    msg,
                )
            return {"success": False, "data": None}

        # Normal result
        result = res0.get("result") or []
        if isinstance(result, list) and result and result[0] == 0:
            # Some ubus methods (especially SET/actions) may return only [0] without a payload.
            data = result[1] if len(result) > 1 else None
            return {"success": True, "data": data}

        return {"success": False, "data": None}


    async def async_call_ubus_batch(
        self,
        calls: list[dict[str, Any]],
        *,
        batch_name: str | None = None,
        retry_on_connreset_104: bool = True,
        retry_on_access_denied: bool = True,
    ) -> list[dict[str, Any]]:
        """Send multiple ubus calls in a single JSON-RPC batch request.

        Returns a list of per-call results in the same order as `calls`, each item:
          {"success": bool, "data": Any, "error": Optional[dict]}

        Note: all calls in a batch share the same ubus session id.
        """

        if not self._session_id or not self._logged_in:
            await self._async_ensure_logged_in()
        session_id = self._session_id

        # Build JSON-RPC batch
        req: list[dict[str, Any]] = []
        id_to_index: dict[int, int] = {}
        for idx, call in enumerate(calls):
            rpc_id = idx
            id_to_index[rpc_id] = idx
            req.append(
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "method": "call",
                    "params": [
                        session_id,
                        call["service"],
                        call["method"],
                        call.get("params", {}) or {},
                    ],
                }
            )

        url = self._ubus_url()
        try:
            sid_preview = (session_id or "")[:8]
            _LOGGER.debug(
                "ubus batch call: name=%s n=%s sid=%s", batch_name or "-", len(req), sid_preview
            )
            headers = dict(self._base_headers)
            headers["Z-Mode"] = "1"
            headers.setdefault("X-Requested-With", "XMLHttpRequest")
            # Always apply webtoken cookie if available
            self._apply_webtoken_cookie(headers)
            if _LOGGER.isEnabledFor(logging.DEBUG):
                try:
                    _LOGGER.debug("ubus batch request payload: %s", json.dumps(req))
                except Exception:
                    pass
            async with self._session.post(
                url,
                json=req,
                headers=headers,
                timeout=10,
            ) as resp:
                resp.raise_for_status()
                self._update_webtoken_from_response(resp)
                raw_text = await resp.text()
                _LOGGER.debug("ubus batch raw response: %s", raw_text)
                res_list = json.loads(raw_text)
                # If the session expired, the router may return -32002 for batch items.
                if retry_on_access_denied and isinstance(res_list, list):
                    any_denied = False
                    for it in res_list:
                        if isinstance(it, dict) and "error" in it:
                            err = it.get("error") or {}
                            if err.get("code") == -32002:
                                any_denied = True
                                break

                    if any_denied:
                        _LOGGER.debug("ubus batch access denied (-32002) detected; re-login and retry once")
                        self._logged_in = False
                        self._session_id = None
                        await self._async_ensure_logged_in(force=True)
                        return await self.async_call_ubus_batch(
                            calls,
                            batch_name=batch_name,
                            retry_on_connreset_104=retry_on_connreset_104,
                            retry_on_access_denied=False,
                        )
        except Exception as exc:
            exc_name = type(exc).__name__
            _LOGGER.warning(
                "HTTP error while calling ubus batch '%s': %s: %s",
                batch_name or "-",
                exc_name,
                exc,
            )

            # Handle TCP reset-by-peer (errno 104) with a single retry
            if retry_on_connreset_104 and self._is_conn_reset_104(exc):
                _LOGGER.warning("Connection reset by peer (104) during batch, attempting re-login")
                self._logged_in = False
                self._session_id = None
                try:
                    await self._async_ensure_logged_in(force=True)
                except Exception as exc2:
                    _LOGGER.error("Re-login failed after 104 during batch: %s", exc2)
                    return [{"success": False, "data": None, "error": {"message": str(exc2)}} for _ in calls]
                return await self.async_call_ubus_batch(
                    calls,
                    batch_name=batch_name,
                    retry_on_connreset_104=False,
                    retry_on_access_denied=retry_on_access_denied,
                )

            # Non-104 network error – mark session as stale so the next poll
            # triggers a re-login (e.g. router sent HTML redirect after a browser
            # login invalidated the HA session).
            self._logged_in = False
            self._session_id = None
            return [{"success": False, "data": None, "error": {"message": str(exc)}} for _ in calls]

        if not isinstance(res_list, list):
            _LOGGER.warning("Invalid JSON from ubus batch – marking session as stale for re-login")
            self._logged_in = False
            self._session_id = None
            return [{"success": False, "data": None, "error": {"message": "invalid_json"}} for _ in calls]

        # Prepare output list
        out: list[dict[str, Any]] = [{"success": False, "data": None, "error": None} for _ in calls]

        for item in res_list:
            if not isinstance(item, dict):
                continue
            rpc_id = item.get("id")
            if rpc_id not in id_to_index:
                continue
            idx = id_to_index[rpc_id]

            if "error" in item:
                out[idx] = {"success": False, "data": None, "error": item.get("error")}
                continue

            result = item.get("result") or []
            if isinstance(result, list) and result and result[0] == 0:
                # Some ubus methods return only [0] (no payload) on success.
                data = result[1] if len(result) > 1 else None
                out[idx] = {"success": True, "data": data, "error": None}
            else:
                out[idx] = {"success": False, "data": None, "error": {"message": "nonzero_result"}}

        return out

    @staticmethod
    def _log_batch_failures(
        batch_name: str,
        calls: list[dict[str, Any]],
        results: list[dict[str, Any]],
    ) -> None:
        """Log failed calls in a named batch without aborting the whole update."""
        failed: list[str] = []
        for call, result in zip(calls, results, strict=False):
            if result.get("success"):
                continue
            failed.append(f"{call.get('service')}.{call.get('method')}")

        if failed:
            _LOGGER.warning(
                "ubus batch '%s' had failed calls: %s",
                batch_name,
                ", ".join(failed),
            )
    
    def invalidate_session(self) -> None:
        """Mark the current session as invalid to force a re-login on the next request.

        Call this whenever an external action (e.g. browser login, pause-polling end)
        may have invalidated the router session so that the next coordinator refresh
        will always perform a fresh login instead of reusing stale credentials.
        For GoForm mode the dedicated session is also discarded so that a fresh
        cookie jar is used on the next login attempt.
        """
        self._logged_in = False
        self._session_id = None
        # GoForm: schedule session teardown (fire-and-forget; no await here
        # because this is a sync method called from sync context).
        # The session will be lazily recreated by _async_get_goform_session().
        self._goform_session = None

    def build_ubus_call(
        self,
        service: str,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a ubus call dict in the format expected by `async_call_ubus`."""
        return {
            "service": service,
            "method": method,
            "params": params or {},
        }


    async def async_execute_ubus_action(
        self,
        call: dict[str, Any],
        *,
        success_key: str | None = None,
        success_values: set[str] | None = None,
    ) -> bool:
        """Execute an action-style ubus call and return True if it succeeded.

        Intended for HA Buttons/Services (reboot, poweroff, factory reset, start update, etc.).

        If `success_key` is provided, we additionally check the returned payload
        (e.g. {"result": "success"}).
        """
        res = await self.async_call_ubus(call)
        if not res.get("success"):
            return False

        if success_key is None:
            return True

        data = res.get("data") or {}
        v = data.get(success_key)
        if v is None:
            return False

        if success_values is None:
            return bool(v)

        return str(v) in success_values


    async def async_execute_action_def(self, action: dict[str, Any]) -> bool:
        """Execute an action definition.

        Expected shape:
        {
            "service": "...",
            "method": "...",
            "params": {...},               # optional
            "success_key": "result",       # optional
            "success_values": ["success"]  # optional (list/tuple/set)
        }
        """
        service = action.get("service")
        method = action.get("method")
        if not service or not method:
            _LOGGER.warning("Invalid action definition (missing service/method): %s", action)
            return False

        params = action.get("params")
        if params is not None and not isinstance(params, dict):
            _LOGGER.warning("Invalid action definition (params must be dict): %s", action)
            return False

        call = self.build_ubus_call(service, method, params)

        success_key = action.get("success_key")

        success_values = action.get("success_values")
        if success_values is not None:
            if isinstance(success_values, (list, tuple, set)):
                success_values = {str(x) for x in success_values}
            else:
                _LOGGER.warning(
                    "Invalid action definition (success_values must be list/tuple/set): %s",
                    action,
                )
                success_values = None

        return await self.async_execute_ubus_action(
            call,
            success_key=success_key,
            success_values=success_values,
        )

    async def async_update_fast(self) -> dict[str, Any] | None:
        """Fetch only fast-changing WAN stats (rates + connected time).

        Keeps the payload small and avoids polling heavy endpoints at high frequency.
        Returns a partial data dict containing at least:
          - "wan": router_get_status payload
          - "wwandst": get_wwandst(type=4) payload (for real_time on firmwares that omit it in router_get_status)
        """

        # Ensure we have an authenticated session.
        if not self._logged_in:
            await self._async_ensure_logged_in()

        if self._api_mode == "goform":
            try:
                return await self._async_goform_update_fast()
            except Exception as exc:
                _LOGGER.warning("GoForm fast update failed, retrying with re-login: %s", exc)
                self._logged_in = False
                await self._async_ensure_logged_in(force=True)
                return await self._async_goform_update_fast()

        batch_calls = [
            {"service": "zwrt_router.api", "method": "router_get_status"},
            {
                "service": "zwrt_data",
                "method": "get_wwandst",
                "params": {"source_module": "web", "cid": 1, "type": 4},
            },
        ]

        results = await self.async_call_ubus_batch(batch_calls)
        if not isinstance(results, list) or len(results) != 2:
            return None

        wan_res, wwandst_res = results
        wan = wan_res.get("data") or {}
        wwandst = wwandst_res.get("data") or {}

        return {
            "wan": wan,
            "wwandst": wwandst,
        }



    # --------------------------------------------------------------------
    # Public API used by the HA DataUpdateCoordinator
    # --------------------------------------------------------------------
    async def async_update_all(self) -> dict[str, Any]:
        """Fetch all relevant router data for Home Assistant in one go."""

        if not self._logged_in:
            await self._async_ensure_logged_in()

        if self._api_mode == "goform":
            try:
                return await self._async_goform_update_all()
            except Exception as exc:
                _LOGGER.warning("GoForm full update failed, retrying with re-login: %s", exc)
                self._logged_in = False
                await self._async_ensure_logged_in(force=True)
                return await self._async_goform_update_all()

        core_batch_calls = [
            {"service": "zte_nwinfo_api", "method": "nwinfo_get_netinfo"},
            {"service": "zwrt_wlan", "method": "report"},
            {"service": "zwrt_bsp.thermal", "method": "get_cpu_temp"},
            {"service": "zwrt_mc.device.manager", "method": "get_device_info"},
            {"service": "zwrt_router.api", "method": "router_get_status"},
            {"service": "zwrt_router.api", "method": "router_get_user_list_num"},
            {"service": "uci", "method": "get", "params": {"config": "zwrt_common_info", "section": "common_config"}},
            {"service": "zwrt_led", "method": "get_ODU_switch_state", "params": {}},
            {"service": "uci", "method": "get", "params": {"config": "wireless", "section": "main_2g"}},
            {"service": "uci", "method": "get", "params": {"config": "wireless", "section": "main_5g"}},
            {
                "service": "zwrt_data",
                "method": "get_wwandst",
                "params": {"source_module": "web", "cid": 1, "type": 4},
            },
            {
                "service": "zwrt_data",
                "method": "get_wwaniface",
                "params": {"source_module": "web", "cid": 1},
            },
        ]
        monthly_batch_calls = [
            {
                "service": "zwrt_data",
                "method": "get_wwandst",
                "params": {"source_module": "web", "cid": 1, "type": 2},
            },
        ]
        sms_batch_calls = [
            {
                "service": "zwrt_wms",
                "method": "zte_libwms_get_sms_data",
                "params": {
                    "page": 0,
                    "data_per_page": 50,
                    "mem_store": 1,
                    "tags": 10,
                    "order_by": "order by id desc",
                },
            },
            {
                "service": "zwrt_wms",
                "method": "zwrt_wms_get_wms_capacity",
                "params": {},
            },
        ]

        core_results = await self.async_call_ubus_batch(core_batch_calls, batch_name="core")
        monthly_results = await self.async_call_ubus_batch(monthly_batch_calls, batch_name="monthly")
        sms_results = await self.async_call_ubus_batch(sms_batch_calls, batch_name="sms")

        self._log_batch_failures("core", core_batch_calls, core_results)
        self._log_batch_failures("monthly", monthly_batch_calls, monthly_results)
        self._log_batch_failures("sms", sms_batch_calls, sms_results)

        results = [*core_results, *monthly_results, *sms_results]
        (
            netinfo_res,
            wlan_res,
            temp_res,
            dev_res,
            wan_res,
            user_list_num_res,
            uci_common_res,
            odu_led_res,
            uci_wifi_2g_res,
            uci_wifi_5g_res,
            wwandst_res,
            wwaniface_res,
            wwandst_monthly_res,
            sms_res,
            sms_capacity_res,
        ) = results

        wan = wan_res.get("data") or {}
        user_list_num = user_list_num_res.get("data") or {}
        common_config = (uci_common_res.get("data") or {}).get("values") or {}
        odu_led = odu_led_res.get("data") or {}
        wifi_main_2g = (uci_wifi_2g_res.get("data") or {}).get("values") or {}
        wifi_main_5g = (uci_wifi_5g_res.get("data") or {}).get("values") or {}
        wwandst = wwandst_res.get("data") or {}
        wwaniface = wwaniface_res.get("data") or {}
        wwandst_monthly = wwandst_monthly_res.get("data") or {}
        sms_payload = sms_res.get("data") or {}
        sms_capacity = sms_capacity_res.get("data") or {}
        raw_messages = sms_payload.get("messages") or []
        sms_messages: list[dict[str, Any]] = []
        if isinstance(raw_messages, list):
            for msg in raw_messages:
                if not isinstance(msg, dict):
                    continue
                sms_messages.append(
                    {
                        "id": msg.get("id"),
                        "number": msg.get("number"),
                        "date": msg.get("date"),
                        "tag": msg.get("tag"),
                        "mem_store": msg.get("mem_store"),
                        "content_raw": msg.get("content"),
                        "content_decoded": self._decode_sms_content(msg.get("content")),
                    }
                )

        netinfo = netinfo_res.get("data") or {}
        bands_summary, total_bw_mhz = self._compute_bands_and_bw(netinfo)

        wlan = wlan_res.get("data") or {}

        return {
            "netinfo": netinfo,
            "wlan": wlan,
            "wifi_main_2g": wifi_main_2g,
            "wifi_main_5g": wifi_main_5g,
            "odu_led": odu_led,
            "thermal": temp_res.get("data"),
            "device": dev_res.get("data"),
            "common_config": common_config,
            "wan": wan,
            "user_list_num": user_list_num,
            "wwandst": wwandst,
            "wwaniface": wwaniface,
            "wwandst_monthly": wwandst_monthly,
            "sms": {
                "messages": sms_messages,
                "latest": sms_messages[0] if sms_messages else None,
                "capacity": sms_capacity if isinstance(sms_capacity, dict) else {},
            },
            # derived fields
            "bands_summary": bands_summary,
            "total_bw_mhz": total_bw_mhz,
        }
    # --------------------------------------------------------------------
    # Cell lock helpers (4G / 5G)
    # --------------------------------------------------------------------

    _LTE_CELL_UNLOCKED = "0,0"
    _NR_CELL_UNLOCKED = "0,0,0"

    @staticmethod
    def _norm_cell_lock_str(value: Any) -> str:
        """Normalize a lock string returned by the router."""
        if value is None:
            return ""
        s = str(value).strip()
        # Some firmwares may return whitespace or trailing separators
        s = s.strip(";")
        return s

    @classmethod
    def get_4g_cell_lock_value(cls, netinfo: dict[str, Any] | None) -> str:
        """Return the raw LTE cell lock string (e.g. '123,1300') or '0,0' if unknown."""
        if not netinfo:
            return cls._LTE_CELL_UNLOCKED
        s = cls._norm_cell_lock_str(netinfo.get("lock_lte_cell"))
        return s or cls._LTE_CELL_UNLOCKED

    @classmethod
    def get_5g_cell_lock_value(cls, netinfo: dict[str, Any] | None) -> str:
        """Return the raw NR cell lock string (e.g. '12,630000,78') or '0,0,0' if unknown."""
        if not netinfo:
            return cls._NR_CELL_UNLOCKED
        s = cls._norm_cell_lock_str(netinfo.get("lock_nr_cell"))
        return s or cls._NR_CELL_UNLOCKED

    @classmethod
    def suggest_4g_cell_lock_text(cls, netinfo: dict[str, Any] | None) -> str:
        """Suggest a lock text 'PCI,EARFCN'.

        Preference order:
        1) If a LTE lock is already configured (lock_lte_cell not '0,0'), return that.
        2) Otherwise, suggest the currently serving LTE cell (lte_pci + lte_action_channel).
        """
        if not netinfo:
            return ""

        cur_lock = cls._norm_cell_lock_str(netinfo.get("lock_lte_cell"))
        if cur_lock and cur_lock != cls._LTE_CELL_UNLOCKED:
            return cur_lock

        pci = netinfo.get("lte_pci")
        earfcn = netinfo.get("lte_action_channel")
        try:
            if pci is None or earfcn is None:
                return ""
            return f"{int(pci)},{int(earfcn)}"
        except (TypeError, ValueError):
            return ""

    @classmethod
    def suggest_5g_cell_lock_text(cls, netinfo: dict[str, Any] | None) -> str:
        """Suggest a lock text 'PCI,ARFCN,BAND'.

        Preference order:
        1) If a NR lock is already configured (lock_nr_cell not '0,0,0'), return that.
        2) Otherwise, suggest the currently serving NR cell (nr5g_pci + nr5g_action_channel + band).
        """
        if not netinfo:
            return ""

        cur_lock = cls._norm_cell_lock_str(netinfo.get("lock_nr_cell"))
        if cur_lock and cur_lock != cls._NR_CELL_UNLOCKED:
            return cur_lock

        pci = netinfo.get("nr5g_pci")
        arfcn = netinfo.get("nr5g_action_channel")

        # Band can come as 'n78' or similar
        band_raw = netinfo.get("nr5g_action_band")
        band: int | None = None
        if band_raw:
            try:
                band = int(str(band_raw).strip().lower().lstrip("n"))
            except (TypeError, ValueError):
                band = None

        # If band is missing, try to infer from ARFCN (subset mapping)
        if band is None:
            try:
                if arfcn is not None:
                    band = cls._convert_nr_arfcn_to_band(int(arfcn))
            except Exception:
                band = None

        try:
            if pci is None or arfcn is None or band is None:
                return ""
            return f"{int(pci)},{int(arfcn)},{int(band)}"
        except (TypeError, ValueError):
            return ""
    @classmethod
    def is_4g_cell_lock_active(cls, netinfo: dict[str, Any] | None) -> bool:
        """Return True if LTE cell lock is active according to netinfo."""
        if not netinfo:
            return False
        s = cls._norm_cell_lock_str(netinfo.get("lock_lte_cell"))
        return bool(s) and s != cls._LTE_CELL_UNLOCKED

    @classmethod
    def is_5g_cell_lock_active(cls, netinfo: dict[str, Any] | None) -> bool:
        """Return True if NR cell lock is active according to netinfo."""
        if not netinfo:
            return False
        s = cls._norm_cell_lock_str(netinfo.get("lock_nr_cell"))
        return bool(s) and s != cls._NR_CELL_UNLOCKED

    @staticmethod
    def parse_4g_cell_lock_input(text: str) -> tuple[int, int]:
        """Parse 'PCI,EARFCN' into integers. Raises ValueError on invalid input."""
        if text is None:
            raise ValueError("empty")
        parts = [p.strip() for p in str(text).split(",")]
        if len(parts) != 2:
            raise ValueError("expected format: PCI,EARFCN")
        pci = int(parts[0])
        earfcn = int(parts[1])
        if pci < 0 or pci > 503:
            raise ValueError("LTE PCI out of range (0..503)")
        if earfcn < 0:
            raise ValueError("LTE EARFCN must be >= 0")
        return pci, earfcn

    @staticmethod
    def parse_5g_cell_lock_input(text: str) -> tuple[int, int, int]:
        """Parse 'PCI,ARFCN,BAND' into integers. Raises ValueError on invalid input."""
        if text is None:
            raise ValueError("empty")
        parts = [p.strip() for p in str(text).split(",")]
        if len(parts) != 3:
            raise ValueError("expected format: PCI,ARFCN,BAND")
        pci = int(parts[0])
        arfcn = int(parts[1])
        band = int(parts[2])
        if pci < 0 or pci > 1007:
            raise ValueError("NR PCI out of range (0..1007)")
        if arfcn < 0:
            raise ValueError("NR ARFCN must be >= 0")
        if band < 0:
            raise ValueError("NR band must be >= 0")
        return pci, arfcn, band

    async def async_lock_4g_cell(self, pci: int, earfcn: int) -> bool:
        """Lock LTE cell by PCI+EARFCN."""
        call = self.build_ubus_call(
            "zte_nwinfo_api",
            "nwinfo_lock_lte_cell",
            {
                "lock_lte_pci": str(int(pci)),
                "lock_lte_earfcn": str(int(earfcn)),
            },
        )
        return await self.async_execute_ubus_action(call)

    async def async_unlock_4g_cell(self) -> bool:
        """Disable LTE cell lock (sets 0,0)."""
        return await self.async_lock_4g_cell(0, 0)

    async def async_lock_5g_cell(self, pci: int, arfcn: int, band: int) -> bool:
        """Lock NR (5G) cell by PCI+ARFCN+Band."""
        call = self.build_ubus_call(
            "zte_nwinfo_api",
            "nwinfo_lock_nr_cell",
            {
                "lock_nr_pci": str(int(pci)),
                "lock_nr_earfcn": str(int(arfcn)),
                "lock_nr_cell_band": str(int(band)),
            },
        )
        return await self.async_execute_ubus_action(call)

    async def async_unlock_5g_cell(self) -> bool:
        """Disable NR (5G) cell lock (sets 0,0,0)."""
        return await self.async_lock_5g_cell(0, 0, 0)

    async def async_set_4g_cell_lock_from_text(self, value: str) -> bool:
        """Set LTE cell lock from UI text 'PCI,EARFCN'."""
        pci, earfcn = self.parse_4g_cell_lock_input(value)
        return await self.async_lock_4g_cell(pci, earfcn)

    async def async_set_5g_cell_lock_from_text(self, value: str) -> bool:
        """Set NR cell lock from UI text 'PCI,ARFCN,BAND'."""
        pci, arfcn, band = self.parse_5g_cell_lock_input(value)
        return await self.async_lock_5g_cell(pci, arfcn, band)

    async def async_set_4g_cell_lock_enabled(self, enabled: bool, *, value: str | None = None) -> bool:
        """Convenience for HA switch: enable uses `value`, disable sets 0,0."""
        if not enabled:
            return await self.async_unlock_4g_cell()
        if not value:
            raise ValueError("Missing value for enabling 4G cell lock")
        return await self.async_set_4g_cell_lock_from_text(value)

    async def async_send_sms(self, number: str, message: str, *, sms_id: str = "0") -> bool:
        """Send SMS and wait for command status completion."""
        payload = {
            "number": number,
            "sms_time": self._build_sms_time_string(),
            "message_body": self._encode_sms_message(message),
            "id": str(sms_id),
            "encode_type": self._get_sms_encode_type(message),
        }

        send_call = self.build_ubus_call("zwrt_wms", "zte_libwms_send_sms", payload)
        send_res = await self.async_call_ubus(send_call)
        if not send_res.get("success"):
            return False

        status_call = self.build_ubus_call("zwrt_wms", "zwrt_wms_get_cmd_status", {"sms_cmd": 4})
        for _ in range(20):
            status_res = await self.async_call_ubus(status_call)
            if status_res.get("success"):
                status_data = status_res.get("data") or {}
                cmd_result = str(status_data.get("sms_cmd_status_result"))
                if cmd_result == "3":
                    return True
                if cmd_result == "2":
                    return False
            await asyncio.sleep(1)

        return False

    async def async_set_5g_cell_lock_enabled(self, enabled: bool, *, value: str | None = None) -> bool:
        """Convenience for HA switch: enable uses `value`, disable sets 0,0,0."""
        if not enabled:
            return await self.async_unlock_5g_cell()
        if not value:
            raise ValueError("Missing value for enabling 5G cell lock")
        return await self.async_set_5g_cell_lock_from_text(value)
