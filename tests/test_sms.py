"""Offline SMS regression tests; no Home Assistant instance or router needed."""

import base64
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def load_api():
    # Load only the API module; HTTP is deliberately unavailable in these tests.
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientError = Exception
    aiohttp.ClientSession = object
    aiohttp.CookieJar = object
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    path = Path(__file__).resolve().parents[1] / "custom_components/zte_ng_router/zte_api.py"
    spec = importlib.util.spec_from_file_location("zte_sms_test_api", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"aiohttp": aiohttp, "homeassistant.core": core}):
        spec.loader.exec_module(module)
    return module.ZteRouterApi


ZteRouterApi = load_api()
KEY = "12" * 32
NEW_KEY = "34" * 32


def seal(value, key=KEY):
    iv = bytes(range(12))
    encrypted = AESGCM(bytes.fromhex(key)).encrypt(iv, value.encode(), None)
    return base64.b64encode(iv + encrypted[-16:] + encrypted[:-16]).decode()


def unseal(value, key=KEY):
    raw = base64.b64decode(value, validate=True)
    return AESGCM(bytes.fromhex(key)).decrypt(
        raw[:12], raw[28:] + raw[12:28], None
    ).decode()


def api(model="g5tc"):
    result = ZteRouterApi(session=object())
    result._update_sms_encryption_from_device({"model": model})
    result._http_encryption_key = KEY
    result._logged_in = True
    result._session_id = "test-session"
    return result


class SmsFieldsTests(unittest.TestCase):
    def test_polled_metadata_identifies_empty_inbox_without_user_selection(self):
        for identity in ("G51F", "BD_MC7510_V1.0.0", "ZTE G51F", "BD_G51FV1.0", "MC7510HW1.0"):
            router = api(identity)
            self.assertEqual(unseal(router._encrypt_sms_field("+48123")), "+48123")
        for identity in ("G5TC", "BD_MC8830_V1.0.0", "G5TS", "G5C", "G5 Max", "G5 Ultra",
                         "BD_G5TCV1.0.0B22", "G5TCHW1.0"):
            router = api(identity)
            self.assertEqual(router._encrypt_sms_field("+48123"), "+48123")

    def test_unknown_empty_router_never_guesses_send_format(self):
        for identity in ("", "unknown", "MC75100", "XXG51FXX"):
            router = api(identity)
            with self.assertRaisesRegex(ValueError, "no SMS sent"):
                router._encrypt_sms_field("+48123")

    def test_inbox_detects_format_when_metadata_is_unknown(self):
        router = api("unknown")
        router._parse_sms_message({"number": "+48123", "content": "00480069"})
        self.assertEqual(router._encrypt_sms_field("+48123"), "+48123")
        router._parse_sms_message({"number": seal("002B00340038"), "content": seal("00480069")})
        self.assertEqual(unseal(router._encrypt_sms_field("+48123")), "+48123")
        router._update_sms_encryption_from_device({"model": "G5TC"})
        self.assertEqual(router._sms_encryption, "aes_gcm")

    def test_invalid_inbox_does_not_select_a_format(self):
        router = api("unknown")
        for value in ("", "!" * 40, seal("hello", NEW_KEY), "00010002"):
            router._parse_sms_message({"number": "+48123", "content": value})
        self.assertIsNone(router._sms_encryption)

    def test_capacity_uses_components_like_webui_without_mutating_response(self):
        raw = {"sms_nvused_total": 0, "sms_nv_rev_total": "12",
               "sms_nv_send_total": 2, "sms_nv_draftbox_total": "1",
               "sms_dev_unread_num": 4}
        capacity = ZteRouterApi._normalize_sms_capacity(raw)
        self.assertEqual(capacity["sms_nvused_total"], 15)
        self.assertEqual(capacity["sms_dev_unread_num"], 4)
        self.assertEqual(raw["sms_nvused_total"], 0)

    def test_capacity_preserves_firmware_total_when_components_unavailable(self):
        for counts in ({}, {"sms_nv_rev_total": 2},
                       {"sms_nv_rev_total": "bad", "sms_nv_send_total": 0,
                        "sms_nv_draftbox_total": 0},
                       {"sms_nv_rev_total": -1, "sms_nv_send_total": 0,
                        "sms_nv_draftbox_total": 0}):
            raw = {"sms_nvused_total": "7", **counts}
            self.assertEqual(ZteRouterApi._normalize_sms_capacity(raw), raw)
        self.assertEqual(ZteRouterApi._normalize_sms_capacity(None), {})
        self.assertEqual(ZteRouterApi._normalize_sms_capacity([]), {})

    def test_plain_router_keeps_plain_sms_even_with_session_key(self):
        router = api()
        for value in ("+48123456789", "00480065006C006C006F"):
            self.assertEqual(router._encrypt_sms_field(value), value)
        message = router._parse_sms_message({"number": "+48123456789", "content": "00480069"})
        self.assertEqual(message["number"], "+48123456789")
        self.assertEqual(message["content_decoded"], "Hi")

    def test_g51f_read_decrypts_sender_and_content(self):
        router = api("g51f")
        for sender in ("+48123456789", "Orange"):
            text = "Hello \u0141\u00f3d\u017a \U0001f600"
            raw = seal(text.encode("utf-16-be").hex())
            message = router._parse_sms_message({
                "number": seal(sender.encode("utf-16-be").hex()), "content": raw,
            })
            self.assertEqual(message["number"], sender)
            self.assertEqual(message["content_decoded"], text)
            self.assertEqual(message["content_raw"], raw)
        self.assertEqual(unseal(router._encrypt_sms_field("+48123")), "+48123")

    def test_g51f_invalid_fields_are_preserved_without_changing_profile(self):
        router = api("g51f")
        tampered = bytearray(base64.b64decode(seal("hello")))
        tampered[-1] ^= 1
        values = [None, "", "+48123456789", "12345678", "004F00720061006E00670065",
                  "!" * 60, "A" * 40, seal("hello", NEW_KEY),
                  base64.b64encode(tampered).decode()]
        for value in values:
            self.assertEqual(router._decrypt_sms_field(value), value)
        self.assertEqual(unseal(router._encrypt_sms_field("hello")), "hello")

    def test_number_decoder_preserves_plain_numbers(self):
        for value in ("1234", "12345678", "004812345678", "+48123456789", "Orange", "0001"):
            self.assertEqual(ZteRouterApi._decode_sms_number(value), value)
        self.assertEqual(ZteRouterApi._decode_sms_number("004F00720061006E00670065"), "Orange")

    def test_profiles_and_missing_key(self):
        router = api("g51f")
        first = router._encrypt_sms_field("hello")
        second = router._encrypt_sms_field("hello")
        self.assertNotEqual(first, second)
        self.assertEqual(unseal(first), "hello")
        router._http_encryption_key = None
        with self.assertRaises(ValueError):
            router._encrypt_sms_field("hello")
        self.assertEqual(router._decrypt_sms_field(first), first)
        plain = api("g5tc")
        self.assertEqual(plain._encrypt_sms_field("hello"), "hello")
        self.assertEqual(plain._decrypt_sms_field(first), "hello")
        self.assertEqual(plain._sms_encryption, "aes_gcm")


class SmsSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_sms_format_stops_before_any_send_request(self):
        router = api("unknown")
        router._async_ensure_logged_in = AsyncMock()
        router.async_call_ubus = AsyncMock()
        with self.assertRaisesRegex(ValueError, "no SMS sent"):
            await router.async_send_sms("+48123", "Hi")
        router.async_call_ubus.assert_not_awaited()

    async def test_session_key_is_used_only_after_router_accepts_it(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        for accepted in (False, True):
            router = api("g51f")
            router._http_encryption_key = None
            router.async_call_ubus = AsyncMock(side_effect=[
                {"success": True, "data": {"result": public_key}},
                {"success": accepted},
            ])
            await router._async_setup_http_encryption()
            if accepted:
                call = router.async_call_ubus.call_args_list[-1].args[0]
                negotiated = private_key.decrypt(
                    base64.b64decode(call["params"]["web_enstr"]), padding.PKCS1v15(),
                ).decode()
                self.assertEqual(router._http_encryption_key, negotiated)
                self.assertEqual(len(bytes.fromhex(negotiated)), 32)
            else:
                self.assertIsNone(router._http_encryption_key)
                with self.assertRaises(ValueError):
                    router._encrypt_sms_field("hello")

    async def test_login_precedes_encryption_and_status_success(self):
        router = api("g51f")
        router._http_encryption_key = None

        async def login(**kwargs):
            router._http_encryption_key = KEY

        router._async_ensure_logged_in = AsyncMock(side_effect=login)
        router.async_call_ubus = AsyncMock(side_effect=[
            {"success": True}, {"success": True, "data": {"sms_cmd_status_result": "3"}},
        ])
        with patch("asyncio.sleep", new_callable=AsyncMock):
            self.assertTrue(await router.async_send_sms("+48123", "Hello \U0001f600"))
        payload = router.async_call_ubus.call_args_list[0].args[0]["params"]
        self.assertEqual(unseal(payload["number"]), "+48123")
        self.assertEqual(unseal(payload["message_body"]), router._encode_sms_message("Hello \U0001f600"))
        self.assertEqual(payload["encode_type"], "UNICODE")

    async def test_access_denied_rebuilds_ciphertext_with_new_key(self):
        router = api("g51f")

        async def login(*, force=False):
            if force:
                router._http_encryption_key = NEW_KEY

        router._async_ensure_logged_in = AsyncMock(side_effect=login)
        router.async_call_ubus = AsyncMock(side_effect=[
            {"success": False, "error": {"code": -32002}}, {"success": True},
            {"success": True, "data": {"sms_cmd_status_result": 3}},
        ])
        with patch("asyncio.sleep", new_callable=AsyncMock):
            self.assertTrue(await router.async_send_sms("+48123", "Hi"))
        calls = router.async_call_ubus.call_args_list
        self.assertEqual(unseal(calls[0].args[0]["params"]["number"]), "+48123")
        self.assertEqual(unseal(calls[1].args[0]["params"]["number"], NEW_KEY), "+48123")
        self.assertFalse(calls[0].kwargs["retry_on_connreset_104"])
        self.assertFalse(calls[0].kwargs["retry_on_access_denied"])

    async def test_plain_send_unchanged_and_failed_status_is_not_resent(self):
        router = api()
        router._async_ensure_logged_in = AsyncMock()
        router.async_call_ubus = AsyncMock(side_effect=[
            {"success": True}, {"success": True, "data": {"sms_cmd_status_result": 2}},
        ])
        with patch("asyncio.sleep", new_callable=AsyncMock):
            self.assertFalse(await router.async_send_sms("+48123", "Hi"))
        self.assertEqual(router.async_call_ubus.await_count, 2)
        payload = router.async_call_ubus.call_args_list[0].args[0]["params"]
        self.assertEqual(payload["number"], "+48123")
        self.assertEqual(payload["message_body"], "00480069")

    async def test_transport_failure_and_repeated_denial_are_not_resent(self):
        for response, count in (({"success": False, "error": {"message": "timeout"}}, 1),
                                ({"success": False, "error": {"code": -32002}}, 2)):
            router = api("g51f")
            router._async_ensure_logged_in = AsyncMock()
            router.async_call_ubus = AsyncMock(return_value=response)
            self.assertFalse(await router.async_send_sms("+48123", "Hi"))
            self.assertEqual(router.async_call_ubus.await_count, count)

    async def test_g51f_poll_reads_stay_batched_and_decrypt_sms(self):
        router = api("g51f")

        async def batch(calls, **kwargs):
            return [
                {"success": True, "data": {"messages": [{
                    "number": seal("002B00340038"), "content": seal("00480069"),
                }]} if call["method"] == "zte_libwms_get_sms_data" else {
                    "sms_nvused_total": 0, "sms_nv_rev_total": 12,
                    "sms_nv_send_total": 0, "sms_nv_draftbox_total": 0,
                } if call["method"] == "zwrt_wms_get_wms_capacity" else {}}
                for call in calls
            ]

        router.async_call_ubus_batch = AsyncMock(side_effect=batch)
        data = await router.async_update_all()
        self.assertEqual(data["sms"]["latest"]["number"], "+48")
        self.assertEqual(data["sms"]["latest"]["content_decoded"], "Hi")
        self.assertEqual(data["sms"]["capacity"]["sms_nvused_total"], 12)
        self.assertEqual(router.async_call_ubus_batch.await_count, 4)
        sms_batch = router.async_call_ubus_batch.call_args_list[-1]
        self.assertEqual(len(sms_batch.args[0]), 2)
        self.assertEqual(sms_batch.kwargs["z_mode_override"], "0")
        self.assertFalse(sms_batch.kwargs["log_raw_response"])


if __name__ == "__main__":
    unittest.main()
