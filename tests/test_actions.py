"""Action failures use synthetic data and mocked writes only; no router access."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import ClientError

from ha_exceptions import HomeAssistantError, ServiceValidationError
from test_router_features import buttons
from test_sms import ZteRouterApi
from test_switch_states import switches


def coordinator():
    data = {
        "odu_led": {"switch": "1", "offtime": "30"},
        "wifi_module": {"wifi_onoff": "1", "lbd": "0", "mlo": "1"},
        "wifi_main_2g": {"disabled": "0"},
        "wifi_main_5g": {"disabled": "0"},
    }
    result = SimpleNamespace(data=data, async_request_refresh=AsyncMock())
    result.async_set_updated_data = Mock(side_effect=lambda data: setattr(result, "data", data))
    return result


def action_switch(definition):
    entity = object.__new__(switches.ZteActionSwitch)
    entity._def = definition
    entity._attr_translation_key = definition.key
    entity.coordinator = coordinator()
    entity._api = SimpleNamespace(async_execute_action_def=AsyncMock(return_value=True))
    return entity


def cell_lock(kind, value="12,630000,78"):
    entity = object.__new__(switches.ZteCellLockSwitch)
    entity._kind = kind
    entity._attr_name = f"Cell Lock {kind.upper()}"
    entity._resolve_text_entity_id = AsyncMock(return_value=f"text.cell_lock_{kind}")
    entity.hass = SimpleNamespace(states=SimpleNamespace(get=Mock(return_value=SimpleNamespace(state=value))))
    entity.coordinator = coordinator()
    entity._api = SimpleNamespace(**{
        f"async_set_{band}_cell_lock_enabled": AsyncMock(return_value=True)
        for band in ("4g", "5g")
    })
    return entity


def button(key, compose="+48123456789,private message"):
    entity = object.__new__(buttons.ZteActionButton)
    entity._btn_def = next(item for item in buttons.BUTTON_DEFS if item.key == key)
    entity._attr_translation_key = key
    entity._entry_id = "entry"
    entity.hass = SimpleNamespace(data={buttons.DOMAIN: {"entry": {"sms_compose": compose}}})
    entity.coordinator = coordinator()
    entity._api = SimpleNamespace(
        async_execute_action_def=AsyncMock(return_value=True),
        async_check_firmware_update=AsyncMock(return_value=True),
        async_start_firmware_update=AsyncMock(return_value=True),
        async_send_sms=AsyncMock(return_value=True),
        parse_sms_compose_input=ZteRouterApi.parse_sms_compose_input,
    )
    return entity


def assert_no_state_update(test, entity):
    entity.coordinator.async_request_refresh.assert_not_awaited()
    entity.coordinator.async_set_updated_data.assert_not_called()


class SwitchActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_writes_raise_without_changing_state_or_retrying(self):
        for definition in switches.SWITCH_DEFS:
            for enabled in (True, False):
                with self.subTest(key=definition.key, enabled=enabled):
                    entity = action_switch(definition)
                    original = deepcopy(entity.coordinator.data)
                    entity._api.async_execute_action_def.return_value = False
                    with patch.object(switches._LOGGER, "warning") as warning:
                        with self.assertRaises(HomeAssistantError) as caught:
                            await (entity.async_turn_on() if enabled else entity.async_turn_off())
                        warning.assert_not_called()
                    self.assertNotIsInstance(caught.exception, ServiceValidationError)
                    self.assertEqual(caught.exception.translation_key,
                                     "switch_on_failed" if enabled else "switch_off_failed")
                    self.assertEqual(caught.exception.translation_placeholders, {"name": entity.name})
                    entity._api.async_execute_action_def.assert_awaited_once()
                    self.assertEqual(entity.coordinator.data, original)
                    assert_no_state_update(self, entity)

    async def test_transport_and_session_failures_are_visible_without_leaking_details(self):
        for error in (ClientError("private URL"), TimeoutError("private URL"),
                      OSError("private URL"), RuntimeError("private URL")):
            for enabled in (True, False):
                entity = action_switch(switches.SWITCH_DEFS[0])
                entity._api.async_execute_action_def.side_effect = error
                with self.assertRaises(HomeAssistantError) as caught:
                    await (entity.async_turn_on() if enabled else entity.async_turn_off())
                self.assertIs(caught.exception.__cause__, error)
                self.assertNotIn("private URL", str(caught.exception))
                entity._api.async_execute_action_def.assert_awaited_once()
                assert_no_state_update(self, entity)

    async def test_success_preserves_existing_write_and_state_behavior(self):
        for definition in switches.SWITCH_DEFS:
            for enabled in (True, False):
                entity = action_switch(definition)
                expected_action = entity._action_for_turn_on() if enabled else entity._action_for_turn_off()
                await (entity.async_turn_on() if enabled else entity.async_turn_off())
                entity._api.async_execute_action_def.assert_awaited_once_with(expected_action)
                if definition.key == "mobile_data":
                    entity.coordinator.async_request_refresh.assert_awaited_once()
                    entity.coordinator.async_set_updated_data.assert_not_called()
                else:
                    self.assertIs(entity.is_on, enabled)
                    entity.coordinator.async_set_updated_data.assert_called_once()
                    entity.coordinator.async_request_refresh.assert_not_awaited()

    async def test_cancellation_and_unexpected_errors_are_not_hidden(self):
        for error in (asyncio.CancelledError(), TypeError("programming error")):
            entity = action_switch(switches.SWITCH_DEFS[0])
            entity._api.async_execute_action_def.side_effect = error
            with self.assertRaises(type(error)):
                await entity.async_turn_on()
            assert_no_state_update(self, entity)


class CellLockActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_text_entity_is_a_validation_error_without_writing(self):
        for kind in ("4g", "5g"):
            entity = cell_lock(kind)
            entity._resolve_text_entity_id.return_value = None
            with self.assertRaises(ServiceValidationError) as caught:
                await entity.async_turn_on()
            self.assertEqual(caught.exception.translation_key, "cell_lock_text_missing")
            getattr(entity._api, f"async_set_{kind}_cell_lock_enabled").assert_not_awaited()
            assert_no_state_update(self, entity)

    async def test_invalid_input_is_a_validation_error(self):
        for kind in ("4g", "5g"):
            entity = cell_lock(kind, value="invalid private value")
            write = getattr(entity._api, f"async_set_{kind}_cell_lock_enabled")
            write.side_effect = ValueError("invalid private value")
            with self.assertRaises(ServiceValidationError) as caught:
                await entity.async_turn_on()
            self.assertEqual(caught.exception.translation_key, "invalid_cell_lock")
            self.assertNotIn("invalid private value", str(caught.exception))
            assert_no_state_update(self, entity)

    async def test_enable_and_disable_failures_are_visible_and_success_still_refreshes(self):
        for kind in ("4g", "5g"):
            for enabled in (True, False):
                for outcome in (False, TimeoutError(), RuntimeError(), True):
                    with self.subTest(kind=kind, enabled=enabled, outcome=outcome):
                        entity = cell_lock(kind)
                        write = getattr(entity._api, f"async_set_{kind}_cell_lock_enabled")
                        if isinstance(outcome, Exception):
                            write.side_effect = outcome
                        else:
                            write.return_value = outcome
                        call = entity.async_turn_on() if enabled else entity.async_turn_off()
                        if outcome is True:
                            await call
                            entity.coordinator.async_request_refresh.assert_awaited_once()
                        else:
                            with self.assertRaises(HomeAssistantError):
                                await call
                            assert_no_state_update(self, entity)
                        write.assert_awaited_once_with(enabled, **({"value": "12,630000,78"} if enabled else {}))


class ButtonActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_and_transport_failed_buttons_report_errors_without_retrying(self):
        methods = {"restart": "async_execute_action_def", "send_sms": "async_send_sms",
                   "check_firmware_update": "async_check_firmware_update",
                   "start_firmware_update": "async_start_firmware_update"}
        for key, method in methods.items():
            for outcome in (False, ClientError("private URL"), TimeoutError(), RuntimeError()):
                with self.subTest(key=key, outcome=outcome):
                    entity = button(key)
                    write = getattr(entity._api, method)
                    if isinstance(outcome, Exception):
                        write.side_effect = outcome
                    else:
                        write.return_value = outcome
                    with patch.object(buttons._LOGGER, "warning") as warning:
                        with self.assertRaises(HomeAssistantError) as caught:
                            await entity.async_press()
                        warning.assert_not_called()
                    self.assertNotIsInstance(caught.exception, ServiceValidationError)
                    self.assertNotIn("private", str(caught.exception))
                    write.assert_awaited_once()
                    assert_no_state_update(self, entity)

    async def test_invalid_sms_is_a_validation_error_without_attempting_send(self):
        for value in ("", " ", buttons.SMS_COMPOSE_DEFAULT, "12345", "+48123456789,",
                      "invalid,+48123456789 private message", ",private message"):
            entity = button("send_sms", compose=value)
            with self.assertRaises(ServiceValidationError) as caught:
                await entity.async_press()
            self.assertEqual(caught.exception.translation_key, "invalid_sms")
            self.assertIsNone(caught.exception.translation_placeholders)
            entity._api.async_send_sms.assert_not_awaited()
            assert_no_state_update(self, entity)

    async def test_sms_preparation_failure_is_not_misreported_as_user_input_error(self):
        entity = button("send_sms")
        entity._api.async_send_sms.side_effect = ValueError("Encryption not determined")
        with self.assertRaises(HomeAssistantError) as caught:
            await entity.async_press()
        self.assertNotIsInstance(caught.exception, ServiceValidationError)
        self.assertEqual(caught.exception.translation_key, "sms_encryption_unavailable")
        entity._api.async_send_sms.assert_awaited_once()
        assert_no_state_update(self, entity)

    async def test_unavailable_firmware_command_is_a_validation_error(self):
        for key, method in (("check_firmware_update", "async_check_firmware_update"),
                            ("start_firmware_update", "async_start_firmware_update")):
            entity = button(key)
            getattr(entity._api, method).side_effect = ValueError("No update ready")
            with self.assertRaises(ServiceValidationError) as caught:
                await entity.async_press()
            self.assertEqual(caught.exception.translation_key, "firmware_command_unavailable")
            assert_no_state_update(self, entity)

    async def test_success_preserves_existing_payload_and_refresh_behavior(self):
        for key in ("restart", "send_sms", "check_firmware_update", "start_firmware_update"):
            entity = button(key)
            await entity.async_press()
            if key == "restart":
                entity._api.async_execute_action_def.assert_awaited_once_with(entity._btn_def.action)
                assert_no_state_update(self, entity)
            else:
                entity.coordinator.async_request_refresh.assert_awaited_once()
            if key == "send_sms":
                entity._api.async_send_sms.assert_awaited_once_with(number="+48123456789", message="private message")


class ActionTranslationTests(unittest.TestCase):
    def test_all_action_errors_have_matching_english_and_german_translations(self):
        root = Path(__file__).resolve().parents[1] / "custom_components/zte_ng_router"
        expected = {
            "switch_on_failed": {"name"}, "switch_off_failed": {"name"},
            "button_action_failed": {"name"}, "cell_lock_text_missing": {"kind"},
            "invalid_cell_lock": {"kind", "format"}, "invalid_sms": set(),
            "sms_send_failed": set(), "sms_encryption_unavailable": set(),
            "firmware_command_unavailable": set(),
        }
        documents = [json.loads((root / name).read_text())["exceptions"]
                     for name in ("strings.json", "translations/en.json", "translations/de.json")]
        self.assertEqual(documents[0], documents[1])
        for document in documents:
            for key, placeholders in expected.items():
                message = document[key]["message"]
                self.assertTrue(message)
                self.assertEqual({field for _, field, _, _ in Formatter().parse(message)
                                  if field is not None}, placeholders)


if __name__ == "__main__":
    unittest.main()
