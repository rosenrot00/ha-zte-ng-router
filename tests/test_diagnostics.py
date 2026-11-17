"""Diagnostics must expose connection state without exporting router secrets."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "zte_ng_router"
DOMAIN = "zte_ng_router"


def load_diagnostics():
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object

    package = types.ModuleType("zte_diagnostics_test")
    package.__path__ = [str(ROOT)]
    spec = importlib.util.spec_from_file_location(
        "zte_diagnostics_test.diagnostics", ROOT / "diagnostics.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "homeassistant.config_entries": config_entries,
            "homeassistant.core": core,
            "zte_diagnostics_test": package,
            spec.name: module,
        },
    ):
        spec.loader.exec_module(module)
    return module


diagnostics = load_diagnostics()


class Coordinator:
    data = {
        "common_config": {"model": "G5TS", "wa_inner_version": "firmware-test"},
        "sms": {"content": "private message"},
        "network_clients": {"AA:BB:CC:DD:EE:FF": {"hostname": "private"}},
    }
    last_update_success = True


class Api:
    login_method = "ubus"
    is_logged_in = True


def _hass(store):
    return types.SimpleNamespace(data={DOMAIN: {"entry-id": store}})


def _entry():
    return types.SimpleNamespace(
        entry_id="entry-id",
        data={"host": "https://user:url-secret@192.0.2.1", "password": "data-secret",
              "scan_interval": 30, "verify_tls": True},
        options={"host": "https://example.org/?token=option-secret", "token": "do-not-export",
                 "future_setting": "future-secret", "scan_interval": 60,
                 "fast_scan_interval": "not-a-number-secret"},
    )


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_config_secrets_are_redacted_and_raw_router_data_is_omitted(self):
        hass = _hass(
            {"api": Api(), "coordinator": Coordinator(), "coordinator_fast": Coordinator()}
        )

        result = await diagnostics.async_get_config_entry_diagnostics(hass, _entry())

        self.assertEqual(result["config"], {"scan_interval": 60, "verify_tls": True})
        self.assertNotIn("secret", repr(result))
        self.assertEqual(result["connection"]["login_method"], "ubus")
        self.assertEqual(result["connection"]["session_status"], "authenticated")
        self.assertEqual(result["connection"]["firmware"]["model"], "G5TS")
        self.assertNotIn("sms", result)
        self.assertNotIn("network_clients", result)
        self.assertNotIn("private", repr(result))

    async def test_entry_that_has_not_finished_setup_still_returns_diagnostics(self):
        result = await diagnostics.async_get_config_entry_diagnostics(_hass({}), _entry())

        self.assertEqual(result["config"], {"scan_interval": 60, "verify_tls": True})
        self.assertIsNone(result["connection"]["login_method"])
        self.assertEqual(result["connection"]["session_status"], "not_authenticated")
        self.assertEqual(result["connection"]["firmware"], {})


if __name__ == "__main__":
    unittest.main()
