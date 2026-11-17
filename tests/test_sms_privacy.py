"""SMS privacy tests: no numbers/texts in entity state, events for new messages."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "custom_components/zte_ng_router"


def _load_sms_events():
    spec = importlib.util.spec_from_file_location("zte_sms_events_test", ROOT / "sms_events.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_sensors():
    package = types.ModuleType("zte_sms_privacy_test")
    package.__path__ = [str(ROOT)]
    modules = {"zte_sms_privacy_test": package}

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
    spec = importlib.util.spec_from_file_location("zte_sms_privacy_test.sensor", ROOT / "sensor.py")
    module = importlib.util.module_from_spec(spec)
    modules[spec.name] = module
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


sms_events = _load_sms_events()
sensors = _load_sensors()

NUMBER = "+491701234567"
SECRET = "Your verification code is 424242"


def _message(msg_id, tag="1", number=NUMBER, text=SECRET, date="26,11,16,10,30,00,+4"):
    return {
        "id": msg_id, "number": number, "date": date, "tag": tag,
        "mem_store": "nv", "content_decoded": text,
    }


class SmsEntityPrivacyTests(unittest.TestCase):
    def _sensor(self, messages, *, legacy=False):
        data = {"sms": {"messages": messages, "latest": messages[0] if messages else None,
                        "capacity": {"sms_nv_rev_total": "3"}}}
        coordinator = types.SimpleNamespace(data=data)
        return sensors.ZteNgRouterSensor(
            coordinator, "entry", "Router", "sms_latest", "Latest SMS", None, None, None,
            sms_legacy_preview=legacy,
        )

    def test_state_contains_only_the_timestamp(self):
        sensor = self._sensor([_message("7")])
        state = str(sensor.native_value)
        self.assertNotIn(NUMBER, state)
        self.assertNotIn("424242", state)
        self.assertIn("2026", state)

    def test_attributes_never_contain_number_or_text(self):
        sensor = self._sensor([_message("7"), _message("6")])
        attrs = sensor.extra_state_attributes
        self.assertEqual(attrs["total_messages"], 2)
        self.assertEqual(attrs["latest_id"], "7")
        self.assertNotIn(NUMBER, repr(attrs))
        self.assertNotIn("424242", repr(attrs))
        for key in ("latest_number", "latest_text", "recent_messages"):
            self.assertNotIn(key, attrs)

    def test_empty_inbox_has_no_state(self):
        sensor = self._sensor([])
        self.assertIsNone(sensor.native_value)
        self.assertEqual(sensor.extra_state_attributes["total_messages"], 0)

    def test_legacy_opt_in_restores_state_and_attributes(self):
        sensor = self._sensor([_message("7")], legacy=True)
        self.assertTrue(sensor.native_value.startswith(NUMBER + " @ "))
        attrs = sensor.extra_state_attributes
        self.assertEqual(attrs["latest_number"], NUMBER)
        self.assertEqual(attrs["latest_text"], SECRET)
        self.assertEqual(attrs["recent_messages"][0]["text"], SECRET)


class SmsEventDetectionTests(unittest.TestCase):
    def test_first_poll_seeds_without_events(self):
        new, seen = sms_events.collect_new_incoming_sms([_message("1"), _message("2")], None)
        self.assertEqual(new, [])
        self.assertEqual(len(seen), 2)

    def test_only_new_incoming_messages_fire_oldest_first(self):
        _, seen = sms_events.collect_new_incoming_sms([_message("1")], None)
        inbox = [_message("3", date="26,11,16,11,00,00,+4"),
                 _message("2", date="26,11,16,10,45,00,+4"), _message("1")]
        new, seen = sms_events.collect_new_incoming_sms(inbox, seen)
        self.assertEqual([m["id"] for m in new], ["2", "3"])
        again, _ = sms_events.collect_new_incoming_sms(inbox, seen)
        self.assertEqual(again, [])

    def test_sent_failed_and_draft_messages_do_not_fire(self):
        _, seen = sms_events.collect_new_incoming_sms([], None)
        inbox = [_message("4", tag="2"), _message("5", tag="3"), _message("6", tag="4"),
                 _message("7", tag="0")]
        new, _ = sms_events.collect_new_incoming_sms(inbox, seen)
        self.assertEqual([m["id"] for m in new], ["7"])

    def test_invalid_entries_are_ignored(self):
        _, seen = sms_events.collect_new_incoming_sms([], None)
        new, _ = sms_events.collect_new_incoming_sms(["x", {"id": None}, _message("9")], seen)
        self.assertEqual([m["id"] for m in new], ["9"])
        self.assertEqual(sms_events.collect_new_incoming_sms(None, seen)[0], [])

    def test_failed_startup_does_not_establish_an_empty_baseline(self):
        _, seen = sms_events.collect_new_incoming_sms(None, None)
        self.assertIsNone(seen)
        events, seen = sms_events.collect_new_incoming_sms([_message("old")], seen)
        self.assertEqual(events, [])
        self.assertEqual(len(seen), 1)

    def test_empty_and_partial_reads_do_not_replay_or_duplicate_messages(self):
        old = [_message("old")]
        _, seen = sms_events.collect_new_incoming_sms(old, None)
        for update in (None, [], [_message("other", tag="2")]):
            _, seen = sms_events.collect_new_incoming_sms(update, seen)
        new_message = _message("new")
        events, _ = sms_events.collect_new_incoming_sms([new_message, new_message, *old], seen)
        self.assertEqual(events, [new_message])

    def test_event_content_is_independently_opt_in(self):
        msg = _message("1")
        public = sms_events.sms_event_data(msg, "entry", "Router", include_content=False)
        self.assertEqual(set(public), {"entry_id", "name", "id", "date"})
        private = sms_events.sms_event_data(msg, "entry", "Router", include_content=True)
        self.assertEqual((private["number"], private["text"]), (NUMBER, SECRET))


if __name__ == "__main__":
    unittest.main()
