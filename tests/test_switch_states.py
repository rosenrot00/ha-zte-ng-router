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

    class CoordinatorEntity:
        @property
        def available(self):
            return self.coordinator.last_update_success

    symbols = {
        "homeassistant.util": {"dt": object()},
        "homeassistant.components.switch": {"SwitchEntity": type("SwitchEntity", (), {})},
        "homeassistant.config_entries": {"ConfigEntry": object},
        "homeassistant.core": {"HomeAssistant": object},
        "homeassistant.helpers.entity": {"DeviceInfo": dict},
        "homeassistant.helpers.entity_registry": {"async_get": None},
        "homeassistant.helpers.update_coordinator": {
            "CoordinatorEntity": CoordinatorEntity,
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
    def entity(self, key, data):
        entity = object.__new__(switches.ZteActionSwitch)
        entity._def = next(item for item in switches.SWITCH_DEFS if item.key == key)
        entity.coordinator = types.SimpleNamespace(data=data, last_update_success=True)
        return entity

    def state(self, key, data):
        return self.entity(key, data).is_on

    def test_missing_wifi_does_not_hide_other_switches(self):
        for key in ("wifi_master", "wifi_main_2g", "wifi_main_5g", "odu_led", "mobile_data"):
            with self.subTest(key=key):
                entity = self.entity(key, {"wifi_available": False})
                self.assertEqual(entity.available, not key.startswith("wifi_"))
                entity.coordinator.data = {}
                self.assertTrue(entity.available)
                entity.coordinator.last_update_success = False
                self.assertFalse(entity.available)

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

    def test_wifi_uses_fallback_per_field_and_keeps_unknown_separate_from_off(self):
        for missing in (None, "", "-"):
            data = {"wifi_module": {"lbd": "1", "wifi_onoff": missing},
                    "wlan": {"wifi_onoff": "1"}, "wifi_main_2g": {"disabled": "0"}}
            self.assertTrue(self.state("wifi_master", data))
            self.assertTrue(self.state("wifi_main_2g", data))
            self.assertIsNone(self.state("wifi_main_5g", data))
            data.pop("wlan")
            self.assertIsNone(self.state("wifi_master", data))
            self.assertIsNone(self.state("wifi_main_2g", data))
        self.assertFalse(self.state("wifi_master", {
            "wifi_module": {"wifi_onoff": "0"}, "wlan": {"wifi_onoff": "1"},
        }))

    def test_router_attributes_are_allowlisted_without_affecting_internal_data(self):
        wifi = {"disabled": "0", "key": "secret", "qrImageShow": "1"}
        data = {"wifi_module": {"wifi_onoff": "1"}, "wifi_main_2g": wifi}
        entity = self.entity("wifi_main_2g", data)
        self.assertEqual(entity.extra_state_attributes, {"disabled": "0"})
        self.assertEqual(entity._action_for_turn_on()["params"]["main_2g"]["qrImageShow"], "1")
        self.assertEqual(wifi["key"], "secret")
        led = self.entity("odu_led", {"odu_led": {"switch": "1", "offtime": "15", "secret": "x"}})
        self.assertEqual(led.extra_state_attributes, {"switch": "1", "offtime": "15"})


if __name__ == "__main__":
    unittest.main()
