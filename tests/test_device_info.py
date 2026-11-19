"""Router metadata updates use synthetic poll results, never router requests."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


def load_device_info():
    root = Path(__file__).resolve().parents[1] / "custom_components/zte_ng_router"
    package = types.ModuleType("zte_device_info_test")
    package.__path__ = [str(root)]
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda function: function
    registry_module = types.ModuleType("homeassistant.helpers.device_registry")
    registry_module.async_get = Mock()
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.device_registry = registry_module
    spec = importlib.util.spec_from_file_location("zte_device_info_test.device_info", root / "device_info.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {
        package.__name__: package, spec.name: module, "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.device_registry": registry_module,
    }):
        spec.loader.exec_module(module)
    return module


device_info = load_device_info()


class DeviceInfoTests(unittest.TestCase):
    def setUp(self):
        self.hass = object()
        self.device = types.SimpleNamespace(id="device-id", sw_version="installed-old", hw_version="HW1.0")
        self.registry = types.SimpleNamespace(
            async_get_device_by_identifier=Mock(return_value=self.device),
            async_get_or_create=Mock(),
            async_update_device=Mock(),
        )
        self.get_registry = patch.object(device_info.dr, "async_get", return_value=self.registry).start()
        self.addCleanup(patch.stopall)

    def update(self, data, entry_id="entry"):
        device_info.async_update_device_info(self.hass, entry_id, "Router", data)

    def test_first_poll_registers_versions_with_unchanged_device_identity(self):
        self.registry.async_get_device_by_identifier.return_value = None
        data = {"common_config": {"wa_inner_version": " installed ", "hardware_version": " HW2.0 "},
                "firmware": {"dm_new_version": "offered-update"}, "sms": {"content": "private"}}
        original = deepcopy(data)
        self.update(data)
        self.get_registry.assert_called_once_with(self.hass)
        self.registry.async_get_device_by_identifier.assert_called_once_with((device_info.DOMAIN, "entry"), "entry")
        self.registry.async_get_or_create.assert_called_once_with(
            config_entry_id="entry", identifiers={(device_info.DOMAIN, "entry")},
            name="Router", manufacturer="ZTE", sw_version="installed", hw_version="HW2.0",
        )
        self.registry.async_update_device.assert_not_called()
        self.assertEqual(data, original)

    def test_changed_versions_update_existing_device_without_touching_other_metadata(self):
        self.update({"common_config": {"wa_inner_version": "installed-new", "hardware_version": "HW2.0"}})
        self.registry.async_update_device.assert_called_once_with(
            "device-id", sw_version="installed-new", hw_version="HW2.0",
        )
        self.registry.async_get_or_create.assert_not_called()

    def test_unchanged_versions_do_not_write_registry(self):
        self.update({"common_config": {"wa_inner_version": " installed-old ", "hardware_version": "HW1.0"}})
        self.registry.async_update_device.assert_not_called()
        self.registry.async_get_or_create.assert_not_called()

    def test_missing_or_invalid_versions_never_erase_registry_values(self):
        for value in (None, "", " ", "-", "--", "UNKNOWN", " unavailable ", "None", "null", "N/A",
                      False, 123, {}, [], ["HW2.0"]):
            with self.subTest(value=value):
                self.update({"common_config": {"wa_inner_version": value, "hardware_version": value}})
        for data in ({}, {"common_config": None, "device": []},
                     {"common_config": "invalid"}, {"device": False}):
            self.update(data)
        self.get_registry.assert_not_called()
        self.registry.async_update_device.assert_not_called()
        self.assertEqual(self.device.sw_version, "installed-old")
        self.assertEqual(self.device.hw_version, "HW1.0")

    def test_partial_reading_only_updates_the_supplied_version(self):
        self.update({"common_config": {"wa_inner_version": "installed-new", "hardware_version": ""}})
        self.registry.async_update_device.assert_called_once_with("device-id", sw_version="installed-new")
        self.registry.async_update_device.reset_mock()
        self.update({"common_config": {"wa_inner_version": None, "hardware_version": "HW2.0"}})
        self.registry.async_update_device.assert_called_once_with("device-id", hw_version="HW2.0")

    def test_device_payload_is_a_per_field_fallback_not_a_new_query(self):
        self.update({"common_config": {"wa_inner_version": "installed-new", "hardware_version": "-"},
                     "device": {"wa_inner_version": "ignore-conflict", "hardware_version": "HW2.0"}})
        self.registry.async_update_device.assert_called_once_with(
            "device-id", sw_version="installed-new", hw_version="HW2.0",
        )

    def test_offered_firmware_is_never_used_as_installed_version(self):
        self.update({"firmware": {"dm_new_version": "offered-update", "new_version_state": "1"}})
        self.get_registry.assert_not_called()

    def test_delayed_first_reading_registers_only_available_version(self):
        self.registry.async_get_device_by_identifier.return_value = None
        self.update({})
        self.registry.async_get_or_create.assert_not_called()
        self.update({"device": {"hardware_version": "HW2.0"}})
        self.registry.async_get_or_create.assert_called_once_with(
            config_entry_id="entry", identifiers={(device_info.DOMAIN, "entry")},
            name="Router", manufacturer="ZTE", hw_version="HW2.0",
        )

    def test_updates_are_scoped_to_the_config_entry(self):
        self.update({"common_config": {"wa_inner_version": "installed-new"}}, entry_id="second-router")
        self.registry.async_get_device_by_identifier.assert_called_once_with(
            (device_info.DOMAIN, "second-router"), "second-router",
        )

    def test_older_home_assistant_registry_lookup_still_works(self):
        del self.registry.async_get_device_by_identifier
        self.registry.async_get_device = Mock(return_value=self.device)
        self.update({"common_config": {"wa_inner_version": "installed-new"}})
        self.registry.async_get_device.assert_called_once_with(identifiers={(device_info.DOMAIN, "entry")})
        self.registry.async_update_device.assert_called_once_with("device-id", sw_version="installed-new")


if __name__ == "__main__":
    unittest.main()
