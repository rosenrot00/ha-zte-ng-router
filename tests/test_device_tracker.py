"""Offline WLAN tracker tests; no real device or router settings are touched."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from test_sms import api, ZteRouterApi


def load_trackers():
    root = Path(__file__).resolve().parents[1] / "custom_components/zte_ng_router"
    package = types.ModuleType("zte_tracker_test")
    package.__path__ = [str(root)]
    modules = {"zte_tracker_test": package}

    class Entity:
        async def async_added_to_hass(self):
            pass

    class CoordinatorEntity(Entity):
        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self):
            return self.coordinator.last_update_success

    class RestoreEntity(Entity):
        async def async_get_last_state(self):
            return getattr(self, "restored_state", None)

    registry = types.ModuleType("homeassistant.helpers.entity_registry")
    registry.async_get = lambda hass: hass.registry
    registry.async_entries_for_config_entry = lambda reg, entry_id: reg
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.entity_registry = registry
    modules.update({"homeassistant.helpers": helpers,
                    "homeassistant.helpers.entity_registry": registry})
    symbols = {
        "homeassistant.components.device_tracker": {
            "ScannerEntity": type("ScannerEntity", (Entity,), {}),
            "SourceType": types.SimpleNamespace(ROUTER="router"),
        },
        "homeassistant.core": {"callback": lambda f: f},
        "homeassistant.helpers.update_coordinator": {"CoordinatorEntity": CoordinatorEntity},
        "homeassistant.helpers.restore_state": {"RestoreEntity": RestoreEntity},
        "zte_tracker_test.zte_api": {"ZteRouterApi": ZteRouterApi},
    }
    for name, attributes in symbols.items():
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        modules[name] = module
    spec = importlib.util.spec_from_file_location("zte_tracker_test.device_tracker", root / "device_tracker.py")
    module = importlib.util.module_from_spec(spec)
    modules[spec.name] = module
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


trackers = load_trackers()
MAC = "02:12:34:56:78:90"
START = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def row(index=0):
    return {"mac_address": f"02:12:34:56:{index // 256:02x}:{index % 256:02x}",
            "hostname": "Laptop", "ip_address": "192.0.2.2", "ipv6_address": "2001:db8::2",
            "interface_type": "main_5g"}


def snapshot(seconds=0, present=True):
    return {"observed_at": START + timedelta(seconds=seconds), "clients": {
        MAC: {"hostname": "Phone", "ip_address": "192.0.2.3", "interface_type": "main_2g"},
    } if present else {}}


class TrackerApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_paginated_snapshot_is_complete_and_private(self):
        router = api()
        counts = {"access_total_num": 65, "wireless_num": 65}
        router.async_call_ubus_batch = AsyncMock(return_value=[
            {"success": True, "data": {"wireless_access_list_info": [row(i) for i in range(64)]}},
            {"success": True, "data": {"wireless_access_list_info": [row(64)]}},
            {"success": True, "data": counts},
        ])
        result = await router._async_read_wifi_clients_locked(counts)
        self.assertEqual(len(result["clients"]), 65)
        self.assertIsNotNone(result["observed_at"].utcoffset())
        call = router.async_call_ubus_batch.call_args
        self.assertEqual([c["params"] for c in call.args[0][:-1]],
                         [{"start_id": 1, "end_id": 64}, {"start_id": 65, "end_id": 128}])
        self.assertEqual(call.args[0][-1]["method"], "router_get_user_list_num")
        self.assertFalse(call.kwargs["log_raw_response"])
        self.assertFalse(call.kwargs["retry_on_access_denied"])
        self.assertEqual(call.kwargs["z_mode_override"], "0")

    async def test_confirmed_empty_list_is_not_an_error(self):
        router = api()
        counts = {"access_total_num": 4, "wireless_num": 0}
        router.async_call_ubus_batch = AsyncMock(return_value=[{"success": True, "data": counts}])
        result = await router._async_read_wifi_clients_locked(counts)
        self.assertEqual(result["clients"], {})
        self.assertEqual(len(router.async_call_ubus_batch.call_args.args[0]), 1)

    async def test_failure_partial_pages_count_changes_and_bad_rows_reject_snapshot(self):
        router = api()
        counts = {"access_total_num": 1, "wireless_num": 1}
        page = {"success": True, "data": {"wireless_access_list_info": [row()]}}
        final = {"success": True, "data": counts}
        for results in (
            [], [page], [{"success": False}, final],
            [page, {"success": False}],
            [page, {"success": True, "data": {"access_total_num": 2, "wireless_num": 2}}],
            [{"success": True, "data": {}}, final],
            [{"success": True, "data": {"wireless_access_list_info": []}}, final],
            [{"success": True, "data": {"wireless_access_list_info": [{"mac_address": "invalid"}]}}, final],
            [{"success": True, "data": {"wireless_access_list_info": [row()] * 65}}, final],
        ):
            router.async_call_ubus_batch = AsyncMock(return_value=results)
            self.assertIsNone(await router._async_read_wifi_clients_locked(counts))

    async def test_invalid_counts_and_goform_never_issue_guessed_calls(self):
        router = api()
        router.async_call_ubus_batch = AsyncMock()
        for counts in (None, {}, {"access_total_num": 1, "wireless_num": 2},
                       {"access_total_num": 99999, "wireless_num": 1},
                       {"access_total_num": True, "wireless_num": 0},
                       {"access_total_num": -1, "wireless_num": 0},
                       {"access_total_num": 1, "wireless_num": "1.5"}):
            self.assertIsNone(await router._async_read_wifi_clients_locked(counts))
        router._api_mode = "goform"
        self.assertIsNone(await router._async_read_wifi_clients_locked({"access_total_num": 1, "wireless_num": 1}))
        router.async_call_ubus_batch.assert_not_awaited()

    async def test_off_by_default_optional_failures_do_not_break_core_and_fast_never_tracks(self):
        router = api()
        router._async_read_wifi_clients_locked = AsyncMock(return_value=snapshot())

        async def batch(calls, **kwargs):
            return [{"success": True, "data": {}} for _ in calls]

        router.async_call_ubus_batch = AsyncMock(side_effect=batch)
        self.assertIsNone((await router.async_update_all())["wifi_clients"])
        router._async_read_wifi_clients_locked.assert_not_awaited()
        router.track_wifi_clients = True
        self.assertEqual((await router.async_update_all())["wifi_clients"], snapshot())
        router._async_read_wifi_clients_locked.assert_awaited_once()
        router._async_read_wifi_clients_locked.reset_mock()
        await router.async_update_fast()
        router._async_read_wifi_clients_locked.assert_not_awaited()
        router._async_read_wifi_clients_locked.side_effect = RuntimeError("read failed")
        result = await router.async_update_all()
        self.assertIsNone(result["wifi_clients"])
        self.assertIn("sms", result)
        self.assertIn("wwandst", result)

    def test_mac_and_address_normalization(self):
        for value in ("02:12:34:56:78:90", "02-12-34-56-78-90", "0212.3456.7890", "021234567890"):
            self.assertEqual(ZteRouterApi.normalize_client_mac(value), MAC)
        for value in (None, "", "ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00", "01:12:34:56:78:90", "x021234567890"):
            self.assertIsNone(ZteRouterApi.normalize_client_mac(value))
        normalized = ZteRouterApi._normalize_wifi_client({**row(), "ip_address": "0.0.0.0", "ipv6_address": "bad"})
        self.assertIsNone(normalized["ip_address"])
        self.assertIsNone(normalized["ipv6_address"])


class TrackerPresenceTests(unittest.TestCase):
    def test_arrival_grace_departure_and_return(self):
        tracking = trackers.WifiClientTracking(180)
        tracking.update(snapshot(), now=0)
        client = tracking.clients[MAC]
        self.assertTrue(client.connected)
        self.assertEqual(client.last_seen, START)
        tracking.update(snapshot(60, False), now=60)
        tracking.update(snapshot(239, False), now=239)
        self.assertTrue(client.connected)
        tracking.update(snapshot(240, False), now=240)
        self.assertFalse(client.connected)
        self.assertEqual(client.last_seen, START)
        tracking.update(snapshot(241), now=241)
        self.assertTrue(client.connected)
        self.assertEqual(client.last_seen, START + timedelta(seconds=241))

    def test_failed_snapshots_and_pause_reset_absence(self):
        for gap in ("failure", "pause"):
            tracking = trackers.WifiClientTracking(180)
            tracking.update(snapshot(), now=0)
            tracking.update(snapshot(60, False), now=60)
            if gap == "failure":
                tracking.update(None, now=120)
                self.assertFalse(tracking.available)
            else:
                tracking.reset_absence()
            tracking.update(snapshot(600, False), now=600)
            self.assertTrue(tracking.clients[MAC].connected)
            tracking.update(snapshot(780, False), now=780)
            self.assertFalse(tracking.clients[MAC].connected)

    def test_cached_snapshot_cannot_advance_absence_or_last_seen(self):
        tracking = trackers.WifiClientTracking(180)
        tracking.update(snapshot(), now=0)
        tracking.update(snapshot(60, False), now=60)
        tracking.update(snapshot(60, False), now=600)
        self.assertTrue(tracking.clients[MAC].connected)
        self.assertEqual(tracking.clients[MAC].last_seen, START)

    def test_wall_clock_correction_does_not_stop_tracking(self):
        tracking = trackers.WifiClientTracking(180)
        tracking.update(snapshot(600), now=0)
        tracking.update(snapshot(500, False), now=60)
        tracking.update(snapshot(501, False), now=240)
        self.assertFalse(tracking.clients[MAC].connected)


    def test_zero_grace_and_invalid_snapshot(self):
        tracking = trackers.WifiClientTracking(0)
        tracking.update(snapshot(), now=0)
        tracking.update({"clients": {}, "observed_at": datetime(2026, 1, 1)}, now=1)
        self.assertFalse(tracking.available)
        tracking.update({"clients": {"invalid": {}}, "observed_at": START + timedelta(seconds=1)}, now=1)
        self.assertFalse(tracking.available)
        tracking.update(snapshot(2, False), now=2)
        self.assertFalse(tracking.clients[MAC].connected)


class TrackerPlatformTests(unittest.IsolatedAsyncioTestCase):
    def setup(self, enabled=True, data=None, registry=None):
        listeners = []

        def listen(listener):
            listeners.append(listener)
            return lambda: listeners.remove(listener)

        coordinator = types.SimpleNamespace(data=data or {}, last_update_success=True, async_add_listener=listen)
        store = {"coordinator": coordinator, "name": "Router"}
        hass = types.SimpleNamespace(data={"zte_ng_router": {"entry": store}}, registry=registry or [])
        unloads = []
        entry = types.SimpleNamespace(entry_id="entry", data={}, options={"track_wifi_clients": enabled},
                                      async_on_unload=unloads.append)
        return hass, entry, coordinator, store, listeners, unloads

    async def test_opt_out_does_not_discover_or_subscribe(self):
        hass, entry, _, store, listeners, _ = self.setup(False, {"wifi_clients": snapshot()})
        entities = []
        await trackers.async_setup_entry(hass, entry, entities.extend)
        self.assertEqual(entities, [])
        self.assertEqual(listeners, [])
        self.assertNotIn("reset_wifi_client_absence", store)

    async def test_dynamic_discovery_unique_ids_disabled_default_and_cleanup(self):
        hass, entry, coordinator, store, listeners, unloads = self.setup(data={"wifi_clients": snapshot()})
        entities = []
        await trackers.async_setup_entry(hass, entry, entities.extend)
        entity = entities[0]
        self.assertFalse(entity.entity_registry_enabled_default)
        self.assertEqual(entity.unique_id, "entry_wifi_021234567890")
        self.assertEqual(entity.mac_address, MAC)
        self.assertEqual(entity.hostname, "Phone")
        self.assertEqual(entity.ip_address, "192.0.2.3")
        self.assertTrue(entity.is_connected)
        self.assertTrue(entity.available)
        self.assertEqual(entity.extra_state_attributes["last_seen"], START.isoformat())
        updated = snapshot(1)
        updated["clients"][MAC]["hostname"] = "Renamed phone"
        updated["clients"][MAC]["ip_address"] = "192.0.2.4"
        coordinator.data = {"wifi_clients": updated}
        listeners[0]()
        self.assertEqual(len(entities), 1)
        self.assertEqual(entity.hostname, "Renamed phone")
        self.assertEqual(entity.ip_address, "192.0.2.4")
        coordinator.data = {"wifi_clients": None}
        listeners[0]()
        self.assertFalse(entity.available)
        for unload in unloads:
            unload()
        self.assertEqual(listeners, [])
        self.assertNotIn("reset_wifi_client_absence", store)

    async def test_pause_hook_restarts_grace_and_discovery_continues(self):
        hass, entry, coordinator, store, listeners, _ = self.setup(data={"wifi_clients": snapshot()})
        entities = []
        with patch.object(trackers.time, "monotonic", return_value=0):
            await trackers.async_setup_entry(hass, entry, entities.extend)
        coordinator.data = {"wifi_clients": snapshot(60, False)}
        with patch.object(trackers.time, "monotonic", return_value=60):
            listeners[0]()
        store["reset_wifi_client_absence"]()
        coordinator.data = {"wifi_clients": snapshot(600, False)}
        with patch.object(trackers.time, "monotonic", return_value=600):
            listeners[0]()
        self.assertTrue(entities[0].is_connected)
        new = snapshot(601)
        new["clients"]["02:12:34:56:78:91"] = {"hostname": "New phone"}
        coordinator.data = {"wifi_clients": new}
        listeners[0]()
        self.assertEqual(len(entities), 2)

    async def test_offline_registered_trackers_reappear_and_restore_metadata_not_presence(self):
        registry = [types.SimpleNamespace(domain="device_tracker", platform="zte_ng_router",
                                         unique_id="entry_wifi_021234567890")]
        hass, entry, _, _, _, _ = self.setup(data={"wifi_clients": snapshot(0, False)}, registry=registry)
        entities = []
        await trackers.async_setup_entry(hass, entry, entities.extend)
        self.assertEqual(len(entities), 1)
        entity = entities[0]
        self.assertIsNone(entity.is_connected)
        entity.restored_state = types.SimpleNamespace(state="home", attributes={
            "last_seen": START.isoformat(), "host_name": "Old name", "ip": "192.0.2.5",
        })
        await entity.async_added_to_hass()
        self.assertEqual(entity.hostname, "Old name")
        self.assertEqual(entity.extra_state_attributes["last_seen"], START.isoformat())
        self.assertIsNone(entity.is_connected)


if __name__ == "__main__":
    unittest.main()
