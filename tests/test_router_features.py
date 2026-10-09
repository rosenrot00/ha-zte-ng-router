"""Feature regressions using synthetic data only, never real router actions."""

import importlib.util
import math
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from test_sensor_defaults import sensors
from test_sms import api


def load_buttons():
    root = Path(__file__).resolve().parents[1] / "custom_components/zte_ng_router"
    package = types.ModuleType("zte_button_test")
    package.__path__ = [str(root)]
    modules = {"zte_button_test": package}

    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self):
            return self.coordinator.last_update_success

    symbols = {
        "homeassistant.components.button": {"ButtonEntity": type("ButtonEntity", (), {})},
        "homeassistant.config_entries": {"ConfigEntry": object},
        "homeassistant.core": {"HomeAssistant": object},
        "homeassistant.exceptions": {"HomeAssistantError": type("HomeAssistantError", (Exception,), {})},
        "homeassistant.helpers.entity": {
            "DeviceInfo": dict, "EntityCategory": types.SimpleNamespace(CONFIG="config"),
        },
        "homeassistant.helpers.update_coordinator": {"CoordinatorEntity": CoordinatorEntity},
    }
    for name, attributes in symbols.items():
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        modules[name] = module
    spec = importlib.util.spec_from_file_location("zte_button_test.button", root / "button.py")
    module = importlib.util.module_from_spec(spec)
    modules[spec.name] = module
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


buttons = load_buttons()
OFFER = {"new_version_state": "version_has_new_optional_software", "current_upgrade_state": "fota_idle"}


class FeatureSensorTests(unittest.TestCase):
    def test_live_data_limit_shape_and_disabled_values_are_retained(self):
        data = {"data_limit": {"enable": 0, "type": 2, "value": "0", "ratio": 0, "overflow": 0},
                "traffic_reset": {"enable": 1, "clearday": 1}}
        expected = {"data_limit_enabled": "off", "data_limit_type": "data_volume",
                    "data_limit_value": 0, "data_limit_warning_percent": 0,
                    "data_limit_exceeded": "off", "traffic_auto_reset": "on", "traffic_reset_day": 1}
        for key, value in expected.items():
            self.assertEqual(sensors._extract_value(data, key), value)
            self.assertIsNone(sensors._extract_value({}, key))
        data["data_limit"].update(value="9007199254740993", ratio="80", overflow="1")
        self.assertEqual(sensors._extract_value(data, "data_limit_value"), 9007199254740993)
        self.assertEqual(sensors._extract_value(data, "data_limit_warning_percent"), 80)
        self.assertEqual(sensors._extract_value(data, "data_limit_exceeded"), "on")

    def test_data_limit_units_are_not_guessed(self):
        entity = object.__new__(sensors.ZteNgRouterSensor)
        entity._key = "data_limit_value"
        definition = next(d for d in sensors.SENSOR_DEFS if d[0] == entity._key)
        self.assertEqual(definition[2:], (None, None, None))
        for mode, name, unit in ((1, "connection_time", "s"), ("2", "data_volume", "router_native"),
                                  (3, None, None)):
            entity.coordinator = types.SimpleNamespace(data={"data_limit": {"type": mode, "value": "3600"}})
            self.assertEqual(entity.extra_state_attributes, {"limit_type": name, "value_unit": unit})
            self.assertEqual(sensors._extract_value(entity.coordinator.data, "data_limit_value"), 3600)

    def test_data_limit_invalid_fields_remain_unknown(self):
        fields = {"data_limit_value": ("value", [-1, "-1", "nan", "inf", "1.5", True]),
                  "data_limit_warning_percent": ("ratio", [-1, 101, "nan", "inf"]),
                  "data_limit_enabled": ("enable", [2, "invalid", True]),
                  "data_limit_exceeded": ("overflow", [2, "invalid"]),
                  "data_limit_type": ("type", [0, 3, "invalid"])}
        for key, (field, invalid) in fields.items():
            for value in [*invalid, None, ""]:
                with self.subTest(key=key, value=value):
                    self.assertIsNone(sensors._extract_value({"data_limit": {field: value}}, key))
        for value in (0, 32, 1.5, "nan", "inf", None, ""):
            self.assertIsNone(sensors._extract_value({"traffic_reset": {"clearday": value}}, "traffic_reset_day"))
        self.assertEqual(sensors._extract_value({"traffic_reset": {"clearday": "31"}}, "traffic_reset_day"), 31)

    def test_ram_matches_webui_and_ignores_free_memory(self):
        for total, available in ((1629816, 796060), (200, 1), (200, 199), (100, 0), (100, 100)):
            data = {"device": {"meminfo": {"total": str(total), "avaliable": str(available), "free": "0"}}}
            expected = 100 - math.floor(100 * available / total + 0.5)
            self.assertEqual(sensors._extract_value(data, "ram_usage"), expected)
        # JS Math.round(0.5) is 1, unlike Python's built-in round(0.5).
        self.assertEqual(sensors._ram_usage({"total": "200", "avaliable": "1"}), 99)

    def test_ram_missing_or_invalid_is_unknown_not_zero(self):
        for meminfo in (None, {}, {"total": "100", "free": "25"},
                        {"total": "0", "avaliable": "0"},
                        {"total": "100", "avaliable": "101"},
                        {"total": "nan", "avaliable": "20"}):
            self.assertIsNone(sensors._ram_usage(meminfo))
        self.assertEqual(sensors._ram_usage({"total": "100", "available": "80"}), 20)

    def test_daily_and_packet_counters_use_correct_scope(self):
        fields = {"daily_download": "day_rx_bytes", "daily_upload": "day_tx_bytes",
                  "rx_packet_errors": "total_rx_error_packets", "tx_packet_errors": "total_tx_error_packets",
                  "rx_packet_drops": "total_rx_drop_packets", "tx_packet_drops": "total_tx_drop_packets"}
        for key, field in fields.items():
            self.assertEqual(sensors._extract_value({"wwandst": {field: "0", "real_rx_bytes": 999}}, key), 0)
            self.assertIsNone(sensors._extract_value({}, key))
            self.assertEqual(sensors._extract_value({"wwandst": {field: "9007199254740993"}}, key), 9007199254740993)
            definition = next(d for d in sensors.SENSOR_DEFS if d[0] == key)
            self.assertEqual(definition[-1], "total_increasing")

    def test_counter_reset_is_exposed_and_invalid_values_are_unknown(self):
        self.assertEqual(sensors._extract_value({"wwandst": {"day_rx_bytes": 0}}, "daily_download"), 0)
        for value in (-1, "-1", "nan", "inf", "", None):
            self.assertIsNone(sensors._bytes_counter(value))

    def test_gnss_coordinates_include_last_known_position_without_claiming_fix(self):
        data = {"gnss": {"LAT": "-12.345", "LON": "0", "FIX": "0", "SOURCE": "GNSS", "FIX_TIME": "old fix\n"}}
        self.assertEqual(sensors._extract_value(data, "gnss_latitude"), -12.345)
        self.assertEqual(sensors._extract_value(data, "gnss_longitude"), 0)
        self.assertEqual(sensors._extract_value(data, "gnss_fix"), "0")
        entity = object.__new__(sensors.ZteNgRouterSensor)
        entity._key = "gnss_latitude"
        entity.coordinator = types.SimpleNamespace(data=data)
        self.assertEqual(entity.extra_state_attributes, {"fix": "0", "source": "GNSS", "fix_time": "old fix"})
        for field, key, limit in (("LAT", "gnss_latitude", 90), ("LON", "gnss_longitude", 180)):
            for value in (None, "", "nan", "inf", limit + 1, -limit - 1):
                self.assertIsNone(sensors._extract_value({"gnss": {field: value}}, key))

    def test_firmware_states_progress_and_invalid_version(self):
        data = {"firmware": {**OFFER, "dm_new_version": "BD_G5TCV1.0.0B23",
                             "dm_pkg_total_size": "400", "dm_download_pkg_size": "100"}}
        self.assertEqual(sensors._extract_value(data, "firmware_update_status"), OFFER["new_version_state"])
        self.assertEqual(sensors._extract_value(data, "firmware_latest_version"), "BD_G5TCV1.0.0B23")
        self.assertEqual(sensors._extract_value(data, "firmware_download_progress"), 25)
        data["firmware"]["current_upgrade_state"] = "downloading"
        self.assertEqual(sensors._extract_value(data, "firmware_update_status"), "downloading")
        data["firmware"]["dm_new_version"] = "version_number_abnormal"
        self.assertIsNone(sensors._extract_value(data, "firmware_latest_version"))
        for value in ("0", "nan", None):
            data["firmware"]["dm_pkg_total_size"] = value
        self.assertIsNone(sensors._extract_value(data, "firmware_download_progress"))


class PollingGroupTests(unittest.IsolatedAsyncioTestCase):
    async def test_entities_use_the_intended_polling_group(self):
        fast_keys = {"connected_time", "download_rate", "upload_rate", "cpu_usage", "ram_usage",
                     "daily_download", "daily_upload", "rx_packet_errors", "tx_packet_errors",
                     "rx_packet_drops", "tx_packet_drops"}
        slow = types.SimpleNamespace(data={})
        fast = types.SimpleNamespace(data={})
        for fast_coordinator in (fast, None):
            store = {"coordinator": slow, "coordinator_fast": fast_coordinator, "name": "Router"}
            hass = types.SimpleNamespace(data={"zte_ng_router": {"entry": store}})
            entities = []
            await sensors.async_setup_entry(hass, types.SimpleNamespace(entry_id="entry"), entities.extend)
            self.assertEqual(len(entities), len(sensors.SENSOR_DEFS))
            for entity in entities:
                expected = fast if fast_coordinator is not None and entity._key in fast_keys else slow
                self.assertIs(entity.coordinator, expected, entity._key)

    async def test_fast_poll_reuses_three_calls_and_requests_only_cpu_and_ram(self):
        router = api()
        router._async_ensure_logged_in = AsyncMock()
        traffic = {"day_rx_bytes": 123, "day_tx_bytes": 456, "total_rx_error_packets": 2,
                   "total_tx_error_packets": 3, "total_rx_drop_packets": 4, "total_tx_drop_packets": 5}
        router.async_call_ubus_batch = AsyncMock(return_value=[
            {"success": True, "data": {}}, {"success": True, "data": traffic},
            {"success": True, "data": {"meminfo": {"total": "100", "avaliable": "75"}}},
        ])
        data = await router.async_update_fast()
        router.async_call_ubus_batch.assert_awaited_once()
        calls = router.async_call_ubus_batch.call_args.args[0]
        self.assertEqual([c["method"] for c in calls], ["router_get_status", "get_wwandst", "get_device_info"])
        self.assertEqual(calls[-1]["params"], {"deviceInfoList": ["cpuinfo", "meminfo"]})
        for key, value in {"daily_download": 123, "daily_upload": 456, "ram_usage": 25,
                           "rx_packet_errors": 2, "tx_packet_errors": 3,
                           "rx_packet_drops": 4, "tx_packet_drops": 5}.items():
            self.assertEqual(sensors._extract_value(data, key), value)


class FirmwareApiTests(unittest.IsolatedAsyncioTestCase):
    def router(self):
        router = api()
        router._async_ensure_logged_in = AsyncMock()
        router.async_call_ubus = AsyncMock(return_value={"success": True})
        return router

    async def test_limit_reads_share_monthly_batch_and_do_not_write(self):
        router = self.router()
        payloads = {"get_wwandst_monthlimit": {"enable": 0, "type": 2, "value": "0", "ratio": 0, "overflow": 0},
                    "get_wwandst_clearday": {"enable": 1, "clearday": 1},
                    "get_wwandst": {"month_rx_bytes": 123}}

        async def batch(calls, **kwargs):
            return [{"success": True, "data": payloads.get(c["method"], {})} for c in calls]

        router.async_call_ubus_batch = AsyncMock(side_effect=batch)
        data = await router.async_update_all()
        monthly = next(c for c in router.async_call_ubus_batch.call_args_list if c.kwargs.get("batch_name") == "monthly")
        self.assertEqual([c["method"] for c in monthly.args[0]],
                         ["get_wwandst", "get_wwandst_monthlimit", "get_wwandst_clearday"])
        self.assertTrue(all(c["params"]["source_module"] == "web" and c["params"]["cid"] == 1
                            for c in monthly.args[0]))
        self.assertEqual(data["data_limit"], payloads["get_wwandst_monthlimit"])
        self.assertEqual(data["traffic_reset"], payloads["get_wwandst_clearday"])
        self.assertEqual(data["wwandst_monthly"], {"month_rx_bytes": 123})
        self.assertEqual(router.async_call_ubus_batch.await_count, 4)
        router.async_call_ubus.assert_not_awaited()

    async def test_failed_or_malformed_limit_reads_do_not_break_other_data(self):
        for payload in (None, [], "invalid", {"enable": 1}):
            router = self.router()

            async def batch(calls, **kwargs):
                return [{"success": False, "data": payload} if c["method"] == "get_wwandst_monthlimit" else
                        {"success": True, "data": payload} if c["method"] == "get_wwandst_clearday" else
                        {"success": True, "data": {"month_rx_bytes": 123}} if c["method"] == "get_wwandst" else
                        {"success": True, "data": {}} for c in calls]

            router.async_call_ubus_batch = AsyncMock(side_effect=batch)
            data = await router.async_update_all()
            self.assertEqual(data["data_limit"], {})
            self.assertEqual(data["traffic_reset"], payload if isinstance(payload, dict) else {})
            self.assertEqual(data["wwandst_monthly"]["month_rx_bytes"], 123)
            self.assertIn("sms", data)

    async def test_check_only_calls_check_new_version(self):
        router = self.router()
        self.assertTrue(await router.async_check_firmware_update())
        router.async_call_ubus.assert_awaited_once_with(
            {"service": "zwrt_zte_dm", "method": "check_new_version", "params": {}},
            retry_on_connreset_104=False,
        )

    async def test_start_revalidates_and_only_confirms_available_download(self):
        router = self.router()
        router.async_call_ubus.side_effect = [{"success": True, "data": OFFER}, {"success": True}]
        self.assertTrue(await router.async_start_firmware_update())
        self.assertEqual([c.args[0]["method"] for c in router.async_call_ubus.call_args_list],
                         ["get_update_info", "confirm_download"])

    async def test_downloaded_package_uses_webui_install_method(self):
        router = self.router()
        router.async_call_ubus.side_effect = [
            {"success": True, "data": {"current_upgrade_state": "download_completed"}},
            {"success": True, "data": {"result": "0"}},
        ]
        self.assertTrue(await router.async_start_firmware_update())
        call = router.async_call_ubus.call_args_list[-1].args[0]
        self.assertEqual(call, {"service": "zwrt_fota_res.api", "method": "start_update",
                                "params": {"moduleName": "zte_web"}})

    async def test_missing_busy_or_failed_read_never_starts_update(self):
        for info in ({}, {"new_version_state": "version_idle", "current_upgrade_state": "fota_idle"},
                     {**OFFER, "current_upgrade_state": "downloading"},
                     {**OFFER, "current_upgrade_state": "upgrading"},
                     {**OFFER, "current_upgrade_state": "unrecognized"}):
            router = self.router()
            router.async_call_ubus.return_value = {"success": True, "data": info}
            with self.assertRaises(ValueError):
                await router.async_start_firmware_update()
            self.assertEqual(router.async_call_ubus.await_count, 1)
        router.async_call_ubus.return_value = {"success": False, "error": {"code": -32002}}
        with self.assertRaises(ValueError):
            await router.async_start_firmware_update()

    async def test_goform_firmware_commands_are_not_guessed(self):
        for method in ("async_check_firmware_update", "async_start_firmware_update"):
            router = self.router()
            router._api_mode = "goform"
            with self.assertRaises(ValueError):
                await getattr(router, method)()
            router.async_call_ubus.assert_not_awaited()

    async def test_command_rejection_and_transport_failure_are_not_replayed(self):
        for result in ({"success": False, "error": {"message": "timeout"}},
                       {"success": True, "data": "fail"},
                       {"success": True, "data": {"result": "1"}},
                       {"success": True, "data": {"result": "failure"}}):
            router = self.router()
            router.async_call_ubus.return_value = result
            self.assertFalse(await router.async_check_firmware_update())
            self.assertEqual(router.async_call_ubus.await_count, 1)

    async def test_poll_adds_only_read_methods_and_does_not_log_coordinates(self):
        router = self.router()

        async def batch(calls, **kwargs):
            return [{"success": True, "data":
                     OFFER if c["method"] == "get_update_info" else
                     {"values": {"LAT": "12.34", "LON": "56.78", "FIX": "0"}}
                     if c.get("params", {}).get("config") == "zwrt_zte_topsw_gnss_gen" else {}}
                    for c in calls]

        router.async_call_ubus_batch = AsyncMock(side_effect=batch)
        data = await router.async_update_all()
        self.assertEqual(data["firmware"], OFFER)
        self.assertEqual(data["gnss"]["LAT"], "12.34")
        metadata = next(c for c in router.async_call_ubus_batch.call_args_list
                        if c.kwargs.get("batch_name") == "firmware_gnss")
        self.assertFalse(metadata.kwargs["log_raw_response"])
        self.assertFalse(metadata.kwargs["retry_on_access_denied"])
        self.assertEqual(metadata.kwargs["z_mode_override"], "0")
        self.assertEqual([c["method"] for c in metadata.args[0]], ["get_update_info", "get"])
        router.async_call_ubus.assert_not_awaited()

    async def test_failed_optional_reads_leave_core_poll_usable(self):
        router = self.router()

        async def batch(calls, **kwargs):
            return [{"success": kwargs.get("batch_name") != "firmware_gnss", "data": {}}
                    for _ in calls]

        router.async_call_ubus_batch = AsyncMock(side_effect=batch)
        data = await router.async_update_all()
        self.assertIsNone(data["firmware"])
        self.assertEqual(data["gnss"], {})
        self.assertIn("sms", data)

    async def test_malformed_optional_payloads_do_not_break_poll(self):
        for payload in ([], "invalid", {"values": "invalid"}):
            router = self.router()

            async def batch(calls, **kwargs):
                return [{"success": True, "data": payload if kwargs.get("batch_name") == "firmware_gnss" else {}}
                        for _ in calls]

            router.async_call_ubus_batch = AsyncMock(side_effect=batch)
            data = await router.async_update_all()
            self.assertEqual(data["gnss"], {})
            self.assertIn("sms", data)


class FirmwareButtonTests(unittest.IsolatedAsyncioTestCase):
    def button(self, key, info=None):
        router = api()
        router.async_check_firmware_update = AsyncMock(return_value=True)
        router.async_start_firmware_update = AsyncMock(return_value=True)
        router.async_execute_action_def = AsyncMock()
        coordinator = types.SimpleNamespace(data={"firmware": info}, last_update_success=True,
                                            hass=object(), async_request_refresh=AsyncMock())
        definition = next(d for d in buttons.BUTTON_DEFS if d.key == key)
        return buttons.ZteActionButton(coordinator, router, types.SimpleNamespace(entry_id="entry"), "Router", definition)

    async def test_check_button_does_not_install(self):
        entity = self.button("check_firmware_update", OFFER)
        await entity.async_press()
        entity._api.async_check_firmware_update.assert_awaited_once()
        entity._api.async_start_firmware_update.assert_not_awaited()
        entity._api.async_execute_action_def.assert_not_awaited()
        entity.coordinator.async_request_refresh.assert_awaited_once()

    async def test_start_button_requires_supported_ready_state(self):
        entity = self.button("start_firmware_update", OFFER)
        self.assertTrue(entity.available)
        await entity.async_press()
        entity._api.async_start_firmware_update.assert_awaited_once()
        for info in (None, {}, {"new_version_state": "version_idle"},
                     {**OFFER, "current_upgrade_state": "downloading"}):
            entity.coordinator.data = {"firmware": info}
            self.assertFalse(entity.available)
        entity.coordinator.data = {"firmware": OFFER}
        entity.coordinator.last_update_success = False
        self.assertFalse(entity.available)

    async def test_user_gets_error_when_command_rejected(self):
        entity = self.button("check_firmware_update", OFFER)
        entity._api.async_check_firmware_update.return_value = False
        with self.assertRaisesRegex(Exception, "Router rejected"):
            await entity.async_press()
        entity.coordinator.async_request_refresh.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
