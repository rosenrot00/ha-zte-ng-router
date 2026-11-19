"""Real loopback HTTP/TLS tests; never connect to a physical router."""

from datetime import datetime, timedelta, timezone
import importlib.util
import ipaddress
from pathlib import Path
import ssl
import sys
import asyncio
from tempfile import TemporaryDirectory
import types
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def load_api():
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    root = Path(__file__).resolve().parents[1] / "custom_components/zte_ng_router"
    spec = importlib.util.spec_from_file_location("zte_tls_test_api", root / "zte_api.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"homeassistant.core": core}):
        spec.loader.exec_module(module)
    return module


api = load_api()


class FingerprintTests(unittest.TestCase):
    def test_normalize_and_validate(self):
        digest = "ab" * 32
        formatted = ":".join(["AB"] * 32)
        self.assertEqual(api.normalize_tls_fingerprint(" " + formatted + " ", "https://router"), digest)
        for empty in (None, "", "  "):
            self.assertEqual(api.normalize_tls_fingerprint(empty, "http://router"), "")
        for invalid in ("ab" * 31, "ab" * 33, "zz" * 32, ":", "ab " * 32, 42):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "invalid_tls_fingerprint"):
                api.normalize_tls_fingerprint(invalid, "https://router")
        with self.assertRaisesRegex(ValueError, "tls_fingerprint_requires_https"):
            api.normalize_tls_fingerprint(digest, "http://router")


class TlsTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.files = TemporaryDirectory(prefix="zte-local-cert-")
        self.addCleanup(self.files.cleanup)
        private_key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ZTE local test")])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(private_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .sign(private_key, hashes.SHA256())
        )
        self.fingerprint = cert.fingerprint(hashes.SHA256()).hex()
        cert_file = Path(self.files.name) / "cert.pem"
        key_file = Path(self.files.name) / "key.pem"
        cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes(private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_file, key_file)
        self.requests = []
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.respond)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.addAsyncCleanup(self.runner.cleanup)
        secure = web.TCPSite(self.runner, "127.0.0.1", 0, ssl_context=context)
        plain = web.TCPSite(self.runner, "127.0.0.1", 0)
        await secure.start()
        await plain.start()
        self.https = f"https://127.0.0.1:{secure._server.sockets[0].getsockname()[1]}"
        self.http = f"http://127.0.0.1:{plain._server.sockets[0].getsockname()[1]}"

    async def respond(self, request):
        self.requests.append(request.path)
        if request.path == "/redirect":
            raise web.HTTPFound(self.http + "/unexpected")
        if request.path == "/ubus/":
            calls = await request.json()
            values = {
                "web_login_info": {"zte_web_sault": "local-test-salt"},
                "web_login": {"ubus_rpc_session": "local-test-session"},
                "router_get_status": {"current_wan_status": "ipv4_connected"},
            }
            return web.json_response([
                {"jsonrpc": "2.0", "id": call["id"], "result": [0, values.get(call["params"][2], {})]}
                for call in calls
            ])
        if request.path.endswith("goform_get_cmd_process"):
            return web.json_response({"LD": "local-test-nonce"})
        if request.path.endswith("goform_set_cmd_process"):
            # Synthetic login/logout responses only; no physical router action.
            form = await request.post()
            assert form["goformId"] in {"LOGIN", "LOGOUT"}
            return web.json_response({"result": "0"})
        return web.Response(text="Local test router")

    def router(self, **kwargs):
        router = api.ZteRouterApi(base_url=kwargs.pop("base_url", self.https), password="test", **kwargs)
        self.addAsyncCleanup(router.async_close)
        router._async_setup_http_encryption = AsyncMock()
        return router

    async def test_matching_pin_overrides_disabled_ca_check_and_applies_to_both_apis(self):
        for verify_tls in (False, True):
            with self.subTest(verify_tls=verify_tls):
                router = self.router(verify_tls=verify_tls, tls_fingerprint=self.fingerprint)
                await router.async_init_session()
                await router.async_login()
                self.assertTrue(router.is_logged_in)
                call = {"service": "zwrt_router.api", "method": "router_get_status"}
                self.assertTrue((await router.async_call_ubus(call))["success"])
                self.assertTrue((await router.async_call_ubus_batch([call]))[0]["success"])
                await router._async_goform_login()
                self.assertEqual(router.login_method, "goform")
                self.assertIs(router._goform_session.connector._ssl, router._ssl)
                await router.async_end_validation_session()

    async def test_mismatch_blocks_all_request_paths_before_http_data_is_sent(self):
        router = self.router(verify_tls=False, tls_fingerprint="00" * 32)
        router._session_id = "local-session"
        router._logged_in = True
        operations = (
            router.async_init_session,
            lambda: router.async_call_ubus({"service": "zwrt_web", "method": "web_login_info"}, session_id="0" * 32),
            lambda: router.async_call_ubus_batch([{"service": "zwrt_web", "method": "web_login_info"}]),
            router._async_goform_init_session,
            lambda: router._async_goform_get("LD"),
            lambda: router._async_goform_set({"goformId": "LOGIN", "password": "not-sent"}),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                router._session_id = "local-session"
                router._logged_in = True
                with self.assertRaises(api.ZteTlsError) as error:
                    await operation()
                self.assertEqual(error.exception.reason, "tls_fingerprint_mismatch")
                self.assertFalse(router.is_logged_in)
        self.assertEqual(self.requests, [])

    async def test_mismatch_or_ca_error_never_invokes_goform_fallback(self):
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()

        def handle_exception(loop, context):
            # The server sees an expected reset when the client rejects its CA.
            if isinstance(context.get("exception"), ConnectionResetError):
                return
            (previous_handler or (lambda loop, context: loop.default_exception_handler(context)))(loop, context)

        loop.set_exception_handler(handle_exception)
        try:
            for pin, reason in (("00" * 32, "tls_fingerprint_mismatch"), ("", "tls_error")):
                with self.subTest(pin=bool(pin)):
                    router = self.router(verify_tls=True, tls_fingerprint=pin)
                    router._async_goform_login = AsyncMock()
                    with self.assertRaises(api.ZteTlsError) as error:
                        await router.async_login()
                    self.assertEqual(error.exception.reason, reason)
                    router._async_goform_login.assert_not_awaited()
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous_handler)
        self.assertEqual(self.requests, [])

    async def test_existing_http_and_unverified_https_keep_working_without_a_pin(self):
        for base_url, verify_tls in ((self.http, False), (self.http, True), (self.https, False)):
            with self.subTest(base_url=base_url, verify_tls=verify_tls):
                router = self.router(base_url=base_url, verify_tls=verify_tls)
                await router.async_init_session()
                await router.async_login()
                self.assertTrue(router.is_logged_in)
                self.assertEqual(router._ssl, verify_tls)

    async def test_external_session_cannot_bypass_pin_and_is_not_closed_by_router(self):
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            router = self.router(session=session, verify_tls=False, tls_fingerprint="00" * 32)
            with self.assertRaises(api.ZteTlsError):
                await router.async_init_session()
            await router.async_close()
            self.assertFalse(session.closed)
        self.assertEqual(self.requests, [])

    async def test_pinned_redirect_is_refused_without_following_it(self):
        router = self.router(tls_fingerprint=self.fingerprint)
        with self.assertRaises(api.ZteTlsError) as error:
            async with router._request(router._session, "GET", self.https + "/redirect"):
                self.fail("Redirect should not be accepted")
        self.assertEqual(error.exception.reason, "tls_redirect")
        self.assertEqual(self.requests, ["/redirect"])


if __name__ == "__main__":
    unittest.main()
