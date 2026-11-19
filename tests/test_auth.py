"""Login/auth tests: rejected passwords are reported distinctly and drive config + reauth flows."""

import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "zte_ng_router"


def load_api_module():
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientError = Exception
    aiohttp.ClientSession = object
    aiohttp.CookieJar = object
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    spec = importlib.util.spec_from_file_location("zte_auth_test_api", ROOT / "zte_api.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"aiohttp": aiohttp, "homeassistant.core": core}):
        spec.loader.exec_module(module)
    return module


api_module = load_api_module()
ZteAuthError = api_module.ZteAuthError
ZteRouterApi = api_module.ZteRouterApi


def router():
    return ZteRouterApi(session=object())


SALT_OK = {"success": True, "data": {"zte_web_sault": "salt", "login_fail_num": "4"}}
UBUS_REJECTED = {"success": True, "data": {"result": "1", "debug": "router-internal-detail"}}
LESS_ATTEMPTS = {"success": True, "data": {"zte_web_sault": "other", "login_fail_num": "3"}}


class ApiLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_temporary_session_is_logged_out_without_retry(self):
        api = router()
        api._logged_in = True
        api._session_id = "validation-only"
        api.async_call_ubus = AsyncMock()
        await api.async_end_validation_session()
        api.async_call_ubus.assert_awaited_once_with(
            {"service": "zwrt_web", "method": "web_logout", "params": {}},
            session_id="validation-only",
            retry_on_access_denied=False,
            retry_on_connreset_104=False,
        )
        self.assertFalse(api.is_logged_in)

    async def test_ubus_rejected_password_raises_auth_error_without_goform_fallback(self):
        api = router()
        api.async_call_ubus = AsyncMock(side_effect=[SALT_OK, UBUS_REJECTED, LESS_ATTEMPTS])
        api._async_goform_login = AsyncMock()

        with self.assertRaises(ZteAuthError):
            await api.async_login()

        api._async_goform_login.assert_not_awaited()
        self.assertFalse(api._logged_in)

    async def test_rejection_message_does_not_echo_router_response(self):
        api = router()
        api.async_call_ubus = AsyncMock(
            side_effect=[SALT_OK, UBUS_REJECTED, LESS_ATTEMPTS]
        )

        with self.assertRaises(ZteAuthError) as raised:
            await api.async_login()

        self.assertNotIn("router-internal-detail", str(raised.exception))

    async def test_ubus_timeout_and_incomplete_reply_still_fall_back(self):
        for reply in ({"success": False, "error": {"message": "timeout"}},
                      {"success": True, "data": {}}, {"success": True, "data": {"result": "0"}}):
            with self.subTest(reply=reply):
                api = router()
                api.async_call_ubus = AsyncMock(side_effect=[SALT_OK, reply])
                api._async_goform_login = AsyncMock()
                await api.async_login()
                api._async_goform_login.assert_awaited_once()

    async def test_ubus_ambiguous_refusal_neither_retries_credentials_nor_starts_reauth(self):
        api = router()
        api.async_call_ubus = AsyncMock(side_effect=[SALT_OK, UBUS_REJECTED, SALT_OK])
        api._async_goform_login = AsyncMock()
        with self.assertRaises(api_module.ZteLoginRejected) as raised:
            await api.async_login()
        self.assertNotIsInstance(raised.exception, ZteAuthError)
        api._async_goform_login.assert_not_awaited()

    async def test_confirmed_invalid_password_blocks_subsequent_login_attempts(self):
        api = router()
        api.async_init_session = AsyncMock()
        api.async_login = AsyncMock(side_effect=ZteAuthError("rejected"))
        for force in (False, True):
            with self.assertRaises(ZteAuthError):
                await api._async_ensure_logged_in(force=force)
        api.async_login.assert_awaited_once()

    async def test_expired_batch_session_propagates_auth_error(self):
        api = router()
        api._logged_in = True
        api._session_id = "old-session"
        api._async_ensure_logged_in = AsyncMock(side_effect=ZteAuthError("rejected"))
        response = types.SimpleNamespace(
            raise_for_status=lambda: None,
            text=AsyncMock(return_value='[{"id":1,"error":{"code":-32002}}]'),
        )
        context = AsyncMock()
        context.__aenter__.return_value = response
        api._session = types.SimpleNamespace(post=MagicMock(return_value=context))
        with self.assertRaises(ZteAuthError):
            await api.async_call_ubus_batch([{"service": "zwrt_router.api", "method": "router_get_status"}])
        api._async_ensure_logged_in.assert_awaited_once_with(force=True)

    async def test_unsupported_ubus_still_falls_back_to_goform(self):
        api = router()
        api.async_call_ubus = AsyncMock(return_value={"data": {}})  # no salt: no UBUS login
        api._async_goform_login = AsyncMock()

        await api.async_login()

        api._async_goform_login.assert_awaited_once()

    async def test_goform_rejected_password_raises_auth_error(self):
        api = router()
        api.async_call_ubus = AsyncMock(return_value={"data": {}})
        api._async_goform_init_session = AsyncMock()
        api._async_goform_get = AsyncMock(return_value={"LD": "nonce"})
        api._async_goform_set = AsyncMock(return_value={"result": "1"})

        with self.assertRaises(ZteAuthError):
            await api.async_login()

        self.assertFalse(api._logged_in)

    async def test_goform_success_sets_mode_and_login_state(self):
        api = router()
        api.async_call_ubus = AsyncMock(return_value={"data": {}})
        api._async_goform_init_session = AsyncMock()
        api._async_goform_get = AsyncMock(return_value={"LD": "nonce"})
        api._async_goform_set = AsyncMock(return_value={"result": "0"})

        await api.async_login()

        self.assertTrue(api._logged_in)
        self.assertEqual(api._api_mode, "goform")

    async def test_connection_problems_are_not_reported_as_auth_errors(self):
        api = router()
        api.async_call_ubus = AsyncMock(return_value={"data": {}})
        api._async_goform_init_session = AsyncMock()
        api._async_goform_get = AsyncMock(return_value={})  # unreachable: empty nonce

        with self.assertRaises(RuntimeError) as raised:
            await api.async_login()

        self.assertNotIsInstance(raised.exception, ZteAuthError)

    async def test_goform_missing_reply_duplicate_login_and_lockout_are_not_auth_errors(self):
        for reply in ({}, {"result": "2"}, {"result": "5"}):
            with self.subTest(reply=reply):
                api = router()
                api._async_goform_init_session = AsyncMock()
                api._async_goform_get = AsyncMock(return_value={"LD": "nonce"})
                api._async_goform_set = AsyncMock(return_value=reply)
                with self.assertRaises(RuntimeError) as raised:
                    await api._async_goform_login()
                self.assertNotIsInstance(raised.exception, ZteAuthError)


def load_config_flow(validate_api):
    """Load config_flow.py against stubbed Home Assistant and a fake ZteRouterApi."""

    class ConfigFlow:
        def __init_subclass__(cls, domain=None, **kwargs):
            super().__init_subclass__(**kwargs)

    class OptionsFlow:
        pass

    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigFlow = ConfigFlow
    config_entries.OptionsFlow = OptionsFlow
    config_entries.ConfigEntry = object
    const = types.ModuleType("homeassistant.const")
    const.CONF_HOST = "host"
    const.CONF_PASSWORD = "password"
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda func: func
    homeassistant = types.ModuleType("homeassistant")
    homeassistant.config_entries = config_entries

    package = types.ModuleType("zte_cf_test")
    package.__path__ = [str(ROOT)]
    const_spec = importlib.util.spec_from_file_location("zte_cf_test.const", ROOT / "const.py")
    const_module = importlib.util.module_from_spec(const_spec)
    const_spec.loader.exec_module(const_module)
    fake_api = types.ModuleType("zte_cf_test.zte_api")
    fake_api.ZteAuthError = ZteAuthError
    fake_api.ZteRouterApi = validate_api
    fake_api.ZteTlsError = api_module.ZteTlsError
    fake_api.normalize_tls_fingerprint = api_module.normalize_tls_fingerprint

    spec = importlib.util.spec_from_file_location("zte_cf_test.config_flow", ROOT / "config_flow.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "homeassistant": homeassistant,
            "homeassistant.config_entries": config_entries,
            "homeassistant.const": const,
            "homeassistant.core": core,
            "zte_cf_test": package,
            "zte_cf_test.const": const_module,
            "zte_cf_test.zte_api": fake_api,
            spec.name: module,
        },
    ):
        spec.loader.exec_module(module)
    return module


class FakeApi:
    """Stand-in for ZteRouterApi; ``outcome`` decides what async_login does."""

    outcome = None
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False
        self.logged_out = False
        FakeApi.instances.append(self)

    async def async_init_session(self):
        pass

    async def async_login(self):
        if FakeApi.outcome is not None:
            raise FakeApi.outcome

    async def async_close(self):
        self.closed = True

    async def async_end_validation_session(self):
        self.logged_out = True


config_flow = load_config_flow(FakeApi)

USER_INPUT = {
    "name": "My router",
    "host": "http://192.0.2.1",
    "password": "pw",
    "verify_tls": False,
    "scan_interval": 60,
    "track_wifi_clients": False,
    "client_consider_home": 180,
}


class ValidateLoginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeApi.outcome = None
        FakeApi.instances = []

    async def _validate(self):
        return await config_flow._async_validate_login(
            None, host="http://192.0.2.1", password="pw", verify_tls=False
        )

    async def test_success_returns_none_and_closes_the_api(self):
        self.assertIsNone(await self._validate())
        self.assertTrue(FakeApi.instances[0].closed)
        self.assertTrue(FakeApi.instances[0].logged_out)

    async def test_rejected_password_maps_to_invalid_auth(self):
        FakeApi.outcome = ZteAuthError("rejected")
        self.assertEqual(await self._validate(), "invalid_auth")
        self.assertTrue(FakeApi.instances[0].closed)
        self.assertFalse(FakeApi.instances[0].logged_out)

    async def test_other_failures_map_to_cannot_connect(self):
        for failure in (RuntimeError("x"), OSError("x"), TimeoutError()):
            with self.subTest(failure=type(failure).__name__):
                FakeApi.outcome = failure
                self.assertEqual(await self._validate(), "cannot_connect")

    async def test_tls_failures_are_not_mapped_to_password_errors(self):
        for reason in ("tls_error", "tls_fingerprint_mismatch", "tls_redirect"):
            FakeApi.outcome = api_module.ZteTlsError(reason)
            self.assertEqual(await self._validate(), reason)
            self.assertTrue(FakeApi.instances[-1].closed)

    async def test_validation_uses_the_pin_even_when_ca_verification_is_disabled(self):
        fingerprint = "ab" * 32
        self.assertIsNone(await config_flow._async_validate_login(
            None, host="https://192.0.2.1", password="pw", verify_tls=False,
            tls_fingerprint=fingerprint,
        ))
        self.assertEqual(FakeApi.instances[-1].kwargs["tls_fingerprint"], fingerprint)
        self.assertFalse(FakeApi.instances[-1].kwargs["verify_tls"])


def _flow(entry=None):
    flow = config_flow.ZteNgRouterConfigFlow()
    flow.hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_get_entry=lambda entry_id: entry)
    )
    flow.context = {"entry_id": "entry-id"}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    flow.async_show_form = MagicMock(return_value={"type": "form"})
    flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})
    return flow


def _schema_default(schema, key):
    for marker in schema.schema:
        if marker.schema == key:
            return marker.default()
    raise AssertionError(key)


class UserStepTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeApi.outcome = None
        FakeApi.instances = []

    async def test_valid_login_creates_the_entry(self):
        flow = _flow()
        await flow.async_step_user(dict(USER_INPUT))
        flow.async_create_entry.assert_called_once()
        flow.async_show_form.assert_not_called()

    async def test_wrong_password_shows_error_and_keeps_typed_values(self):
        FakeApi.outcome = ZteAuthError("rejected")
        flow = _flow()
        await flow.async_step_user(dict(USER_INPUT, host="https://router.example"))

        flow.async_create_entry.assert_not_called()
        kwargs = flow.async_show_form.call_args.kwargs
        self.assertEqual(kwargs["errors"], {"base": "invalid_auth"})
        self.assertEqual(_schema_default(kwargs["data_schema"], "host"), "https://router.example")
        self.assertEqual(_schema_default(kwargs["data_schema"], "name"), "My router")

    async def test_unreachable_router_shows_cannot_connect(self):
        FakeApi.outcome = RuntimeError("no route")
        flow = _flow()
        await flow.async_step_user(dict(USER_INPUT))
        flow.async_create_entry.assert_not_called()
        self.assertEqual(flow.async_show_form.call_args.kwargs["errors"], {"base": "cannot_connect"})

    async def test_invalid_scan_interval_does_not_try_to_log_in(self):
        flow = _flow()
        await flow.async_step_user(dict(USER_INPUT, scan_interval=1))
        self.assertEqual(FakeApi.instances, [])
        self.assertEqual(
            flow.async_show_form.call_args.kwargs["errors"], {"base": "invalid_scan_interval"}
        )


class ReauthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeApi.outcome = None
        FakeApi.instances = []
        self.entry = types.SimpleNamespace(
            title="My router",
            data={"host": "http://192.0.2.1", "password": "old", "verify_tls": False},
            options={"password": "stale-options-password"},
        )

    async def test_reauth_shows_the_password_form_first(self):
        flow = _flow(self.entry)
        await flow.async_step_reauth({})
        kwargs = flow.async_show_form.call_args.kwargs
        self.assertEqual(kwargs["step_id"], "reauth_confirm")
        self.assertEqual(kwargs["errors"], {})

    async def test_wrong_password_stays_on_the_form(self):
        FakeApi.outcome = ZteAuthError("rejected")
        flow = _flow(self.entry)
        await flow.async_step_reauth_confirm({"password": "still-wrong"})
        flow.async_update_reload_and_abort.assert_not_called()
        self.assertEqual(flow.async_show_form.call_args.kwargs["errors"], {"base": "invalid_auth"})

    async def test_new_password_replaces_data_and_drops_stale_options_copy(self):
        flow = _flow(self.entry)
        await flow.async_step_reauth_confirm({"password": "new"})

        args, kwargs = flow.async_update_reload_and_abort.call_args
        self.assertIs(args[0], self.entry)
        self.assertEqual(kwargs["data"]["password"], "new")
        self.assertEqual(kwargs["data"]["host"], "http://192.0.2.1")
        self.assertNotIn("password", kwargs["options"])
        # The live login used the host from the stored entry.
        self.assertEqual(FakeApi.instances[0].kwargs["base_url"], "http://192.0.2.1")

    async def test_reauth_prefers_options_pin_and_preserves_it_with_new_password(self):
        self.entry.data.update(host="https://192.0.2.1", tls_fingerprint="cd" * 32)
        self.entry.options["tls_fingerprint"] = "ab" * 32
        flow = _flow(self.entry)
        await flow.async_step_reauth_confirm({"password": "new"})
        self.assertEqual(FakeApi.instances[-1].kwargs["tls_fingerprint"], "ab" * 32)
        self.assertEqual(flow.async_update_reload_and_abort.call_args.kwargs["options"]["tls_fingerprint"], "ab" * 32)
        self.entry.options["tls_fingerprint"] = ""
        flow = _flow(self.entry)
        await flow.async_step_reauth_confirm({"password": "new"})
        self.assertEqual(FakeApi.instances[-1].kwargs["tls_fingerprint"], "")


class TranslationTests(unittest.TestCase):
    def test_new_config_flow_texts_exist_in_every_language_file(self):
        for name in ("strings.json", "translations/en.json", "translations/de.json"):
            with self.subTest(file=name):
                config = json.loads((ROOT / name).read_text(encoding="utf-8"))["config"]
                self.assertIn("password", config["step"]["reauth_confirm"]["data"])
                self.assertIn("title", config["step"]["reauth_confirm"])
                for key in ("invalid_auth", "cannot_connect", "invalid_scan_interval"):
                    self.assertIn(key, config["error"])
                self.assertIn("reauth_successful", config["abort"])


if __name__ == "__main__":
    unittest.main()
