"""Config/options schema regressions using real voluptuous validation."""
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from test_auth import api_module

import voluptuous as vol


ROOT = Path(__file__).resolve().parents[1] / "custom_components/zte_ng_router"


def load_flow():
    class Flow:
        hass = None

        def __init_subclass__(cls, **kwargs):
            pass

        async def async_set_unique_id(self, unique_id):
            self.unique_id = unique_id

        def _abort_if_unique_id_configured(self):
            pass

        def async_create_entry(self, **kwargs):
            return {"type": "create_entry", **kwargs}

        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}

    entries = types.ModuleType("homeassistant.config_entries")
    entries.ConfigFlow = Flow
    entries.OptionsFlow = Flow
    entries.ConfigEntry = object
    ha = types.ModuleType("homeassistant")
    ha.config_entries = entries
    const = types.ModuleType("homeassistant.const")
    const.CONF_HOST = "host"
    const.CONF_PASSWORD = "password"
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda f: f

    class ZteAuthError(RuntimeError):
        pass

    class ZteRouterApi:
        """The config flow now verifies the login; these tests assume it succeeds."""

        def __init__(self, **kwargs):
            pass

        async def async_init_session(self):
            pass

        async def async_login(self):
            pass

        async def async_close(self):
            pass

        async def async_end_validation_session(self):
            pass

    zte_api = types.ModuleType("zte_config_test.zte_api")
    zte_api.ZteAuthError = ZteAuthError
    zte_api.ZteRouterApi = ZteRouterApi
    zte_api.ZteTlsError = api_module.ZteTlsError
    zte_api.normalize_tls_fingerprint = api_module.normalize_tls_fingerprint
    package = types.ModuleType("zte_config_test")
    package.__path__ = [str(ROOT)]
    spec = importlib.util.spec_from_file_location("zte_config_test.config_flow", ROOT / "config_flow.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"homeassistant": ha, "homeassistant.config_entries": entries,
                                 "homeassistant.const": const, "homeassistant.core": core,
                                 "zte_config_test": package,
                                 "zte_config_test.zte_api": zte_api, spec.name: module}):
        spec.loader.exec_module(module)
    return module


flow = load_flow()


class TrackingConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_fingerprint_setup_normalizes_and_stores_without_changing_http_defaults(self):
        config = flow.ZteNgRouterConfigFlow()
        form = await config.async_step_user()
        values = form["data_schema"]({
            "name": "Router", "host": "https://192.0.2.1", "password": "test",
            "tls_fingerprint": ":".join(["AB"] * 32),
        })
        result = await config.async_step_user(values)
        self.assertEqual(result["data"]["tls_fingerprint"], "ab" * 32)
        self.assertFalse(result["data"]["verify_tls"])
        values.update(host="http://192.0.2.1")
        result = await config.async_step_user(values)
        self.assertEqual(result["errors"], {"tls_fingerprint": "tls_fingerprint_requires_https"})
        values["tls_fingerprint"] = ""
        result = await config.async_step_user(values)
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["host"], "http://192.0.2.1")
        self.assertNotIn("allow_insecure_http", result["data"])

    async def test_invalid_pin_returns_field_error_and_is_not_saved(self):
        config = flow.ZteNgRouterConfigFlow()
        values = {"name": "Router", "host": "https://192.0.2.1", "password": "test",
                  "verify_tls": True, "scan_interval": 60, "tls_fingerprint": "invalid"}
        result = await config.async_step_user(values)
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"tls_fingerprint": "invalid_tls_fingerprint"})
        options = flow.ZteNgRouterOptionsFlow(types.SimpleNamespace(data={}, options={}))
        result = await options.async_step_init(values)
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"tls_fingerprint": "invalid_tls_fingerprint"})

    async def test_options_can_clear_a_pin_stored_in_data_and_keep_verification_choice(self):
        entry = types.SimpleNamespace(
            data={"host": "https://192.0.2.1", "password": "old", "tls_fingerprint": "ab" * 32},
            options={"verify_tls": True},
        )
        options = flow.ZteNgRouterOptionsFlow(entry)
        form = await options.async_step_init()
        values = form["data_schema"]({})
        self.assertEqual(values["tls_fingerprint"], "ab" * 32)
        # Updating unrelated options must not drop a pin from the original entry.
        unchanged = await options.async_step_init({key: value for key, value in values.items()
                                                   if key != "tls_fingerprint"})
        self.assertEqual(unchanged["data"]["tls_fingerprint"], "ab" * 32)
        values["tls_fingerprint"] = ""
        cleared = await options.async_step_init(values)
        self.assertEqual(cleared["data"]["tls_fingerprint"], "")
        self.assertTrue(cleared["data"]["verify_tls"])
        self.assertNotIn("password", cleared["data"])
        self.assertEqual(entry.data["tls_fingerprint"], "ab" * 32)

    async def test_initial_schema_defaults_to_opt_out_and_persists_opt_in(self):
        config = flow.ZteNgRouterConfigFlow()
        form = await config.async_step_user()
        values = form["data_schema"]({"name": "Router", "host": "http://192.0.2.1", "password": "test"})
        self.assertFalse(values["track_wifi_clients"])
        self.assertEqual(values["client_consider_home"], 180)
        values.update(track_wifi_clients=True, client_consider_home=120)
        result = await config.async_step_user(values)
        self.assertTrue(result["data"]["track_wifi_clients"])
        self.assertEqual(result["data"]["client_consider_home"], 120)

    async def test_options_preserve_credentials_and_existing_choices(self):
        entry = types.SimpleNamespace(data={"host": "http://192.0.2.1", "password": "original",
                                           "track_wifi_clients": True, "client_consider_home": 240},
                                      options={"password": "override", "other_option": "keep"})
        options = flow.ZteNgRouterOptionsFlow(entry)
        form = await options.async_step_init()
        values = form["data_schema"]({})
        self.assertTrue(values["track_wifi_clients"])
        self.assertEqual(values["client_consider_home"], 240)
        values["track_wifi_clients"] = False
        result = await options.async_step_init(values)
        self.assertFalse(result["data"]["track_wifi_clients"])
        self.assertEqual(result["data"]["password"], "override")
        self.assertEqual(result["data"]["other_option"], "keep")

    async def test_sms_options_default_off_and_are_independent(self):
        entry = types.SimpleNamespace(data={"host": "http://192.0.2.1"}, options={})
        options = flow.ZteNgRouterOptionsFlow(entry)
        form = await options.async_step_init()
        values = form["data_schema"]({})
        self.assertFalse(values["sms_event_content"])
        self.assertFalse(values["sms_legacy_preview"])
        values["sms_legacy_preview"] = True
        result = await options.async_step_init(values)
        self.assertFalse(result["data"]["sms_event_content"])
        self.assertTrue(result["data"]["sms_legacy_preview"])

    async def test_grace_bounds_and_translations(self):
        form = await flow.ZteNgRouterOptionsFlow(types.SimpleNamespace(data={}, options={})).async_step_init()
        for invalid in (-1, 3601, "invalid"):
            with self.assertRaises(vol.Invalid):
                form["data_schema"]({"client_consider_home": invalid})
        for valid in (0, 180, 3600):
            self.assertEqual(form["data_schema"]({"client_consider_home": valid})["client_consider_home"], valid)
        for path in (ROOT / "strings.json", ROOT / "translations/en.json", ROOT / "translations/de.json"):
            translations = json.loads(path.read_text())
            for section, step in (("config", "user"), ("options", "init")):
                labels = translations[section]["step"][step]["data"]
                self.assertIn("track_wifi_clients", labels)
                self.assertIn("client_consider_home", labels)
                self.assertEqual("sms_event_content" in labels, section == "options")
                self.assertEqual("sms_legacy_preview" in labels, section == "options")
                self.assertIn("tls_fingerprint", labels)
                errors = translations[section]["error"]
                self.assertIn("invalid_tls_fingerprint", errors)
                self.assertIn("tls_fingerprint_requires_https", errors)


if __name__ == "__main__":
    unittest.main()
