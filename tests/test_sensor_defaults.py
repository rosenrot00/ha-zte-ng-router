"""Offline sensor-default tests; no Home Assistant instance or router needed."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


def load_sensors():
    root = Path(__file__).resolve().parents[1] / "custom_components/zte_ng_router"
    package = types.ModuleType("zte_sensor_test")
    package.__path__ = [str(root)]
    modules = {"zte_sensor_test": package}

    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

    symbols = {
        "homeassistant.components.sensor": {
            "SensorEntity": type("SensorEntity", (), {}),
            "SensorDeviceClass": types.SimpleNamespace(
                TEMPERATURE="temperature", DATA_RATE="data_rate", DATA_SIZE="data_size",
                TIMESTAMP="timestamp",
            ),
            "SensorStateClass": types.SimpleNamespace(
                MEASUREMENT="measurement", TOTAL_INCREASING="total_increasing",
            ),
        },
        "homeassistant.const": {
            "DEGREE": "deg",
            "PERCENTAGE": "%",
            "UnitOfDataRate": types.SimpleNamespace(BITS_PER_SECOND="bit/s"),
            "UnitOfInformation": types.SimpleNamespace(BYTES="B"),
            "UnitOfTemperature": types.SimpleNamespace(CELSIUS="C"),
        },
        "homeassistant.helpers.update_coordinator": {
            "CoordinatorEntity": CoordinatorEntity, "DataUpdateCoordinator": object,
        },
        "homeassistant.helpers.entity": {
            "DeviceInfo": dict, "EntityCategory": types.SimpleNamespace(DIAGNOSTIC="diagnostic"),
        },
        "homeassistant.config_entries": {"ConfigEntry": object},
        "homeassistant.core": {"HomeAssistant": object},
        "homeassistant.helpers.entity_platform": {"AddEntitiesCallback": object},
        "homeassistant.util": {"dt": object()},
    }
    for name, attributes in symbols.items():
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        modules[name] = module
    spec = importlib.util.spec_from_file_location("zte_sensor_test.sensor", root / "sensor.py")
    module = importlib.util.module_from_spec(spec)
    modules[spec.name] = module
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


sensors = load_sensors()
OPTIONAL_KEYS = {"5g_modem_temperature", "modem_temperature", "pa_temp_level", "tj_temp_level"}


class SensorDefaultTests(unittest.TestCase):
    def test_g5tc_identification_from_polled_metadata(self):
        for section, field, identity in (
            ("common_config", "model_name", "G5TC"),
            ("common_config", "hardware_version", "G5TCHW1.0"),
            ("common_config", "wa_inner_version", "BD_G5TCV1.0.0B22"),
            ("device", "wa_inner_version", "BD_G5TC_V1.0"),
            ("device", "model", "zte g5tc"),
        ):
            with self.subTest(identity=identity):
                data = {section: {field: identity}}
                disabled = {key for key, *_ in sensors.SENSOR_DEFS
                            if not sensors._sensor_enabled_by_default(data, key)}
                self.assertEqual(disabled, OPTIONAL_KEYS)

    def test_other_and_unknown_models_keep_all_sensors_enabled(self):
        for identity in ("", "G51F", "MC7510", "G5TS", "XXG5TCXX", "G5TC2"):
            data = {"common_config": {"model_name": identity}}
            for key, *_ in sensors.SENSOR_DEFS:
                self.assertTrue(sensors._sensor_enabled_by_default(data, key))

    def test_each_supplied_value_keeps_only_its_sensor_enabled(self):
        for key in OPTIONAL_KEYS:
            for value in (0, "0", "28.5"):
                with self.subTest(key=key, value=value):
                    data = {"common_config": {"model_name": "G5TC"}, "thermal": {key: value}}
                    for candidate in OPTIONAL_KEYS:
                        self.assertEqual(sensors._sensor_enabled_by_default(data, candidate),
                                         candidate == key)

    def test_existing_aliases_and_sources_are_used(self):
        for key, section, alias, value in (
            ("5g_modem_temperature", "device", "Z5g_modem_temperature", "32"),
            ("modem_temperature", "netinfo", "modem_temp", "30"),
            ("pa_temp_level", "thermal", "pa_temperature_level", "normal"),
            ("tj_temp_level", "sim_info", "tj_temp_level", 0),
        ):
            data = {"common_config": {"model_name": "G5TC"}, section: {alias: value}}
            self.assertTrue(sensors._sensor_enabled_by_default(data, key))

    def test_empty_and_invalid_readings_do_not_enable_temperatures(self):
        for key in OPTIONAL_KEYS:
            for value in (None, "", "-"):
                data = {"common_config": {"model_name": "G5TC"}, "thermal": {key: value}}
                self.assertFalse(sensors._sensor_enabled_by_default(data, key))
        data = {"common_config": {"model_name": "G5TC"},
                "thermal": {"modem_temperature": "unavailable"}}
        self.assertFalse(sensors._sensor_enabled_by_default(data, "modem_temperature"))

    def test_entities_are_retained_with_same_ids_and_values(self):
        data = {"common_config": {"model_name": "G5TC"}}
        coordinator = types.SimpleNamespace(data=data)
        for key, name, device_class, unit, state_class in sensors.SENSOR_DEFS:
            entity = sensors.ZteNgRouterSensor(
                coordinator, "entry", "Router", key, name, device_class, unit, state_class,
            )
            self.assertEqual(entity._attr_unique_id, f"entry_{key}")
            self.assertEqual(entity._attr_entity_registry_enabled_default, key not in OPTIONAL_KEYS)
            if key in OPTIONAL_KEYS:
                self.assertIsNone(entity.native_value)
                data["thermal"] = {key: 23}
                self.assertEqual(entity.native_value, 23)
                data.pop("thermal")


if __name__ == "__main__":
    unittest.main()
