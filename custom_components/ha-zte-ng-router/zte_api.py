from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_LOGGER = logging.getLogger(__name__)


class ZteRouterApi:
    """Low-level API wrapper for ZTE 5G routers (e.g. G5TC)."""

    def __init__(self, base_url: str, password: str, router_type: str, verify_tls: bool) -> None:
        # base_url like "http://192.168.254.1" or "https://192.168.254.1"
        self.base_url = base_url.rstrip("/")
        self.password = password
        self.router_type = router_type
        self.verify_tls = verify_tls

        self.session = requests.Session()
        self.session.verify = self.verify_tls

        self._session_id: Optional[str] = None
        self._logged_in: bool = False

        # Headers similar to the JS script environment
        self._base_headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Z-Mode": "1",
            "Origin": self.base_url,
            "Referer": self.base_url + "/",
        }

    # --------------------------------------------------------------------
    # Helper: ubus URL
    # --------------------------------------------------------------------
    def _ubus_url(self) -> str:
        # double t marker like the JS script uses
        return f"{self.base_url}/ubus/?t=1&t=2"

    # --------------------------------------------------------------------
    # Helper: hashing
    # --------------------------------------------------------------------
    @staticmethod
    def sha256(text: str) -> str:
        import hashlib
        return hashlib.sha256(text.encode("utf-8")).hexdigest().upper()

    # --------------------------------------------------------------------
    # Band helpers (simplified)
    # --------------------------------------------------------------------
    def _convert_lte_earfcn_to_band(self, earfcn: int | None) -> int | None:
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

    def _convert_nr_arfcn_to_band(self, arfcn: int | None) -> int | None:
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
                bands.append(f"N{b}")
            total_bw += nr_bw

        bands_summary = " + ".join(bands) if bands else "-"

        return bands_summary, total_bw

    # --------------------------------------------------------------------
    # Authentication
    # --------------------------------------------------------------------
    def init_session(self) -> None:
        """Re-create requests.Session and fetch the start page to get cookies/CSRF."""
        self.session = requests.Session()
        self.session.verify = self.verify_tls

        url = self.base_url + "/"
        try:
            resp = self.session.get(url, timeout=10)
            _LOGGER.debug(
                "init_session: status=%s cookies=%s",
                resp.status_code,
                self.session.cookies.get_dict(),
            )
        except Exception as exc:
            _LOGGER.warning("init_session GET %s failed: %s", url, exc)

    def login(self) -> None:
        """Perform normal web_login to ZTE router and store ubus session ID."""

        # 1) Salt holen
        salt_req = {
            "service": "zwrt_web",
            "method": "web_login_info",
            "params": {},
        }
        salt_res = self.call_ubus(
            salt_req,
            session_id="0" * 32,
            retry_on_access_denied=False,
        )

        data = salt_res.get("data") or {}
        salt = data.get("zte_web_sault")
        if not salt:
            raise RuntimeError("Could not retrieve login salt")

        # 2) finaler Hash = SHA256(SHA256(password) + salt)
        pw_hash = self.sha256(self.password)
        final = self.sha256(pw_hash + salt)

        # 3) Login
        login_req = {
            "service": "zwrt_web",
            "method": "web_login",
            "params": {"password": final},
        }
        login_res = self.call_ubus(
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
        _LOGGER.info("ZTE NG Router login successful")

    # --------------------------------------------------------------------
    # ubus caller with automatic re-login on -32002
    # --------------------------------------------------------------------
    def call_ubus(
        self,
        call: dict,
        session_id: Optional[str] = None,
        retry_on_access_denied: bool = True,
    ) -> dict:
        """Call ubus. If access denied (-32002), try a re-login and retry once."""

        if session_id is None:
            session_id = self._session_id

        req = [
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "call",
                "params": [
                    session_id or "0" * 32,
                    call["service"],
                    call["method"],
                    call.get("params", {}) or {},
                ],
            }
        ]

        url = self._ubus_url()
        try:
            resp = self.session.post(
                url,
                json=req,
                headers=self._base_headers,
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as exc:
            _LOGGER.warning("HTTP error while calling ubus: %s", exc)
            return {"success": False, "data": None}

        try:
            res_list = resp.json()
            res0 = res_list[0]
        except Exception:
            _LOGGER.warning("Invalid JSON from ubus")
            return {"success": False, "data": None}

        # Error case
        if "error" in res0:
            err = res0["error"]
            code = err.get("code")
            msg = err.get("message")
            _LOGGER.warning("ubus error: code=%s msg=%s", code, msg)

            # Access denied → re-login
            if code == -32002 and retry_on_access_denied:
                _LOGGER.warning("Access denied, attempting re-login")

                self._logged_in = False
                self._session_id = None

                try:
                    self.init_session()
                    self.login()
                except Exception as exc:
                    _LOGGER.error("Re-login failed: %s", exc)
                    return {"success": False, "data": None}

                # Retry once with new session
                return self.call_ubus(call, self._session_id, retry_on_access_denied=False)

            return {"success": False, "data": None}

        # Normal result
        result = res0.get("result") or []
        if result and result[0] == 0:
            return {"success": True, "data": result[1]}

        return {"success": False, "data": None}

    # --------------------------------------------------------------------
    # Public API used by the HA DataUpdateCoordinator
    # --------------------------------------------------------------------
    def update_all(self) -> Dict[str, Any]:
        """Fetch all relevant router data for Home Assistant in one go."""

        if not self._logged_in:
            self.init_session()
            self.login()

        netinfo_res = self.call_ubus(
            {"service": "zte_nwinfo_api", "method": "nwinfo_get_netinfo"}
        )
        temp_res = self.call_ubus(
            {"service": "zwrt_bsp.thermal", "method": "get_cpu_temp"}
        )
        dev_res = self.call_ubus(
            {"service": "zwrt_mc.device.manager", "method": "get_device_info"}
        )
        wan_res = self.call_ubus(
            {"service": "zwrt_router.api", "method": "router_get_status"}
        )

        netinfo = netinfo_res.get("data") or {}
        bands_summary, total_bw_mhz = self._compute_bands_and_bw(netinfo)

        return {
            "netinfo": netinfo,
            "thermal": temp_res.get("data"),
            "device": dev_res.get("data"),
            "wan": wan_res.get("data"),
            # derived fields
            "bands_summary": bands_summary,
            "total_bw_mhz": total_bw_mhz,
        }
