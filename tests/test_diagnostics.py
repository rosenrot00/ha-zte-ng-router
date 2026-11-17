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
    def redact(value, keys):
        if isinstance(value, dict):
            return {
                key: "**REDACTED**" if key in keys else redact(item, keys)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item, keys) for item in value]
        return value

    diagnostics_component = types.ModuleType("homeassistant.components.diagnostics")
    diagnostics_component.async_redact_data = redact
    components = types.ModuleType("homeassistant.components")
    components.diagnostics = diagnostics_component
    const = types.ModuleType("homeassistant.const")
    const.CONF_PASSWORD = "password"
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    homeassistant = types.ModuleType("homeassistant")
    homeassistant.components = components

    package = types.ModuleType("zte_diagnostics_test")
    package.__path__ = [str(ROOT)]
    spec = importlib.util.spec_from_file_location(
        "zte_diagnostics_test.diagnostics", ROOT / "diagnostics.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.diagnostics": diagnostics_component,
            "homeassistant.config_entries": config_entries,
            "homeassistant.const": const,
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
        data={"host": "https://192.0.2.1", "password": "do-not-export"},
        options={"token": "do-not-export"},
    )


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_config_secrets_are_redacted_and_raw_router_data_is_omitted(self):
        hass = _hass(
            {"api": Api(), "coordinator": Coordinator(), "coordinator_fast": Coordinator()}
        )

        result = await diagnostics.async_get_config_entry_diagnostics(hass, _entry())

        self.assertEqual(result["config"]["data"]["password"], "**REDACTED**")
        self.assertEqual(result["config"]["options"]["token"], "**REDACTED**")
        self.assertEqual(result["connection"]["login_method"], "ubus")
        self.assertEqual(result["connection"]["session_status"], "authenticated")
        self.assertEqual(result["connection"]["firmware"]["model"], "G5TS")
        self.assertNotIn("sms", result)
        self.assertNotIn("network_clients", result)
        self.assertNotIn("private", repr(result))

    async def test_entry_that_has_not_finished_setup_still_returns_diagnostics(self):
        result = await diagnostics.async_get_config_entry_diagnostics(_hass({}), _entry())

        self.assertEqual(result["config"]["data"]["password"], "**REDACTED**")
        self.assertIsNone(result["connection"]["login_method"])
        self.assertEqual(result["connection"]["session_status"], "not_authenticated")
        self.assertEqual(result["connection"]["firmware"], {})


if __name__ == "__main__":
    unittest.main()
