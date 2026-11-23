"""Config entry identity and reconfigure flow tests (issue #21)."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from test_auth import load_config_flow, ZteAuthError

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "zte_ng_router"


class ProbeApi:
    """Router API stand-in that reports a fixed device identifier."""

    identifier = "aa:bb:cc:dd:ee:ff"
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        ProbeApi.instances.append(self)

    async def async_init_session(self):
        pass

    async def async_login(self):
        pass

    async def async_call_ubus(self, call):
        assert call["method"] == "get_sim_info"
        return {"success": True, "data": {"wlan_mac_address": self.identifier}}

    async def async_end_validation_session(self):
        pass

    async def async_close(self):
        pass


config_flow = load_config_flow(ProbeApi)

USER_INPUT = {
    "name": "ZTE NG Router",
    "host": "https://192.168.0.1",
    "password": "password",
    "verify_tls": True,
    "tls_fingerprint": "",
    "scan_interval": 60,
}


class ConfigFlowIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ProbeApi.instances.clear()

    def _flow(self):
        flow = config_flow.ZteNgRouterConfigFlow()
        flow.hass = MagicMock()
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        return flow

    async def test_user_step_uses_router_mac_as_unique_id(self):
        flow = self._flow()
        await flow.async_step_user(dict(USER_INPUT))
        flow.async_set_unique_id.assert_awaited_once_with(
            "zte_ng_router_aa:bb:cc:dd:ee:ff"
        )
        flow._abort_if_unique_id_configured.assert_called_once()

    async def test_user_step_falls_back_to_host_without_mac(self):
        ProbeApi.identifier = "unavailable"
        try:
            flow = self._flow()
            await flow.async_step_user(dict(USER_INPUT))
            flow.async_set_unique_id.assert_awaited_once_with(
                "zte_ng_router_https://192.168.0.1"
            )
        finally:
            ProbeApi.identifier = "aa:bb:cc:dd:ee:ff"

    def test_reconfigure_rejects_a_different_router(self):
        flow = self._flow()
        self.assertTrue(
            flow._is_other_router(
                "zte_ng_router_aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"
            )
        )
        self.assertFalse(
            flow._is_other_router(
                "zte_ng_router_aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff"
            )
        )
        # Legacy address-based IDs cannot be compared, so they are not blocked.
        self.assertFalse(
            flow._is_other_router(
                "zte_ng_router_https://192.168.0.1", "aa:bb:cc:dd:ee:ff"
            )
        )


if __name__ == "__main__":
    unittest.main()
