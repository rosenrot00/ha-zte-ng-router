"""Offline switch-state tests; no router actions are executed."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


def load_switches():
    root = Path(__file__).resolve().parents[1] / "custom_components/zte_ng_router"
    package = types.ModuleType("zte_switch_test")
    package.__path__ = [str(root)]
    modules = {"zte_switch_test": package}
    symbols = {
        "homeassistant.util": {"dt": object()},
        "homeassistant.components.switch": {"SwitchEntity": type("SwitchEntity", (), {})},
        "homeassistant.config_entries": {"ConfigEntry": object},
        "homeassistant.core": {"HomeAssistant": object},
        "homeassistant.helpers.entity": {"DeviceInfo": dict},
        "homeassistant.helpers.entity_registry": {"async_get": None},
        "homeassistant.helpers.update_coordinator": {
            "CoordinatorEntity": type("CoordinatorEntity", (), {}),
        },
        "homeassistant.helpers.event": {"async_call_later": None},
        "zte_switch_test.zte_api": {"ZteRouterApi": object},
    }
    for name, attributes in symbols.items():
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        modules[name] = module
    spec = importlib.util.spec_from_file_location("zte_switch_test.switch", root / "switch.py")
    module = importlib.util.module_from_spec(spec)
    modules[spec.name] = module
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


switches = load_switches()


class SwitchStateTests(unittest.TestCase):
    def state(self, key, data):
        entity = object.__new__(switches.ZteActionSwitch)
        entity._def = next(item for item in switches.SWITCH_DEFS if item.key == key)
        entity.coordinator = types.SimpleNamespace(data=data)
        return entity.is_on

    def test_mobile_status_is_cellular_not_primary_wan_or_enable_flag(self):
        for connected in ("ipv4_connected", "ipv6_connected", "ipv4_ipv6_connected"):
            self.assertTrue(self.state("mobile_data", {
                "wan": {"current_wan_status": "ppp_disconnected"},
                "wwaniface": {"connect_status": connected, "enable": 0},
            }))
        self.assertFalse(self.state("mobile_data", {
            "wan": {"current_wan_status": "ipv4_connected"},
            "wwaniface": {"connect_status": "disconnected", "enable": 1},
        }))

    def test_mobile_legacy_fallbacks(self):
        self.assertFalse(self.state("mobile_data", {"wan": {
            "current_wan_status": "ipv4_connected", "lte_connect_status": "disconnected",
        }}))
        self.assertTrue(self.state("mobile_data", {
            "wan": {"current_wan_status": "ipv4_connected"},
        }))
        self.assertFalse(self.state("mobile_data", {}))

    def test_wifi_band_states_respect_master_and_inverted_disabled_flag(self):
        for band in ("wifi_main_2g", "wifi_main_5g"):
            for master, disabled, expected in (("0", "0", False), ("1", "0", True),
                                               ("1", "1", False)):
                self.assertEqual(self.state(band, {
                    "wifi_module": {"wifi_onoff": master}, band: {"disabled": disabled},
                }), expected)


if __name__ == "__main__":
    unittest.main()
