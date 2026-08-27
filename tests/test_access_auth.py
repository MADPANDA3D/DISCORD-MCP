import importlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_ACCESS_TOKEN = "standalone-test-access-" + ("a" * 32)
TEST_PORTAL_GRANT = "portal-test-grant-" + ("b" * 32)
TEST_GUILD_ID = str(123_456_789_012_345_678)
TEST_CHANNEL_ID = str(123_456_789_012_345_679)


def import_modules():
    os.environ["MCP_MODE"] = "standalone"
    os.environ["MCP_ACCESS_TOKEN"] = TEST_ACCESS_TOKEN
    os.environ["DISCORD_TOKEN"] = "private-test-discord-token"
    os.environ["DISCORD_GUILD_ID"] = TEST_GUILD_ID
    os.environ["DISCORD_ALLOWED_CHANNEL_IDS"] = TEST_CHANNEL_ID
    os.environ["MCP_REQUIRE_CONFIRM"] = "false"

    src_dir = Path(__file__).resolve().parents[1] / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    return (
        importlib.import_module("madpanda_discord_mcp.server"),
        importlib.import_module("madpanda_discord_mcp.runtime_security"),
    )


class TrackingMcpApp:
    def __init__(self):
        self.calls = 0
        self.received_body = b""

    async def __call__(self, scope, receive, send):
        self.calls += 1
        if scope.get("method") == "POST":
            while True:
                message = await receive()
                self.received_body += message.get("body", b"")
                if not message.get("more_body", False):
                    break
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"ok":true}'})


def build_config(security, mode="portal", **overrides):
    environment = {
        "MCP_MODE": mode,
        "MCP_ACCESS_TOKEN": TEST_ACCESS_TOKEN,
        "MCP_PORTAL_GRANT_TOKEN": TEST_PORTAL_GRANT,
        "MCP_REQUEST_BODY_MAX_BYTES": "1024",
    }
    environment.update(overrides)
    return security.load_runtime_security_config(environment)


async def invoke(
    app,
    *,
    headers=None,
    extra_headers=None,
    body=None,
    chunks=None,
    path="/mcp",
    method="POST",
):
    sent = []
    receive_calls = 0
    request_headers = {"content-type": "application/json", **(headers or {})}
    if not any(key.lower() == "host" for key in request_headers):
        request_headers["host"] = "localhost:8085"
    encoded_body = body if isinstance(body, bytes) else json.dumps(body or {}).encode("utf-8")
    pending = list(chunks or [(encoded_body, False)])

    async def receive():
        nonlocal receive_calls
        receive_calls += 1
        if pending:
            chunk, more_body = pending.pop(0)
            return {
                "type": "http.request",
                "body": chunk,
                "more_body": more_body,
            }
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": method,
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [
                (key.lower().encode("latin-1"), value.encode("latin-1"))
                for key, value in request_headers.items()
            ]
            + list(extra_headers or []),
        },
        receive,
        send,
    )
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return status, response_body, receive_calls


class AccessConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.security = import_modules()

    def test_invalid_or_missing_mode_fails_startup(self):
        for mode in ("", "public", "PORTAL typo"):
            with (
                self.subTest(mode=mode),
                self.assertRaises(self.security.RuntimeConfigurationError),
            ):
                self.security.load_runtime_security_config({"MCP_MODE": mode})

    def test_selected_mode_requires_a_long_service_credential(self):
        for mode, variable in (
            ("standalone", "MCP_ACCESS_TOKEN"),
            ("portal", "MCP_PORTAL_GRANT_TOKEN"),
        ):
            with (
                self.subTest(mode=mode),
                self.assertRaises(self.security.RuntimeConfigurationError),
            ):
                self.security.load_runtime_security_config(
                    {"MCP_MODE": mode, variable: "too-short"}
                )

    def test_security_header_names_cannot_collide_or_reuse_reserved_headers(self):
        for portal_header in ("Authorization", "Origin"):
            with (
                self.subTest(portal_header=portal_header),
                self.assertRaises(self.security.RuntimeConfigurationError),
            ):
                build_config(self.security, MCP_PORTAL_GRANT_HEADER=portal_header)

        config = build_config(self.security)
        invalid_configurations = (
            {
                "MCP_DISCORD_TOKEN_HEADER": config.portal_grant_header,
                "MCP_DISCORD_GUILD_ID_HEADER": "x-discord-guild-id",
            },
            {
                "MCP_DISCORD_TOKEN_HEADER": "x-discord-credential",
                "MCP_DISCORD_GUILD_ID_HEADER": "X-DISCORD-CREDENTIAL",
            },
            {
                "MCP_DISCORD_TOKEN_HEADER": "authorization",
            },
            {
                "MCP_DISCORD_TOKEN_HEADER": "invalid header name",
            },
        )
        for configured_headers in invalid_configurations:
            with (
                self.subTest(configured_headers=configured_headers),
                self.assertRaises(self.security.RuntimeConfigurationError),
            ):
                self.security.validate_request_header_configuration(
                    config,
                    configured_headers,
                )

        self.assertEqual(
            self.security.validate_request_header_configuration(
                config,
                {
                    "MCP_DISCORD_TOKEN_HEADER": "X-Discord-Bot-Token",
                    "MCP_OPENAI_API_HEADER": "x-openai-api",
                },
            ),
            ("x-discord-bot-token", "x-openai-api"),
        )

    def test_enabled_openai_vision_requires_a_safe_https_endpoint(self):
        invalid_endpoints = (
            "http://127.0.0.1:9999/collect",
            "https://user:secret@example.com/v1/chat/completions",
            "https://example.com/v1/chat/completions#fragment",
            "https:///missing-host",
            "https://bad host.example/v1/chat/completions",
            "https://example.com:99999/v1/chat/completions",
        )
        for endpoint in invalid_endpoints:
            with (
                self.subTest(endpoint=endpoint),
                self.assertRaises(self.security.RuntimeConfigurationError),
            ):
                self.server.validate_openai_vision_api_url(endpoint)

        self.assertEqual(
            self.server.validate_openai_vision_api_url(
                "https://models.internal:8443/v1/chat/completions?api-version=2026-07-18"
            ),
            "https://models.internal:8443/v1/chat/completions?api-version=2026-07-18",
        )

        environment = os.environ.copy()
        environment.update(
            {
                "MCP_MODE": "standalone",
                "MCP_ACCESS_TOKEN": TEST_ACCESS_TOKEN,
                "DISCORD_CREDENTIAL_MODE": "server",
                "DISCORD_TOKEN": "synthetic-startup-discord-token",
                "DISCORD_GUILD_ID": TEST_GUILD_ID,
                "DISCORD_ALLOWED_CHANNEL_IDS": TEST_CHANNEL_ID,
                "OPENAI_VISION_ENABLED": "true",
                "OPENAI_VISION_API_URL": "http://127.0.0.1:9999/collect",
            }
        )
        completed = subprocess.run(
            [sys.executable, "-c", "import madpanda_discord_mcp.server"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("OPENAI_VISION_API_URL must use https", completed.stderr)

    def test_wildcard_browser_origins_are_forbidden(self):
        with self.assertRaises(self.security.RuntimeConfigurationError):
            build_config(self.security, MCP_ALLOWED_ORIGINS="*")

    def test_configured_origins_are_exact_and_normalized(self):
        config = build_config(
            self.security,
            MCP_ALLOWED_ORIGINS="HTTPS://Browser.Example:443/,http://localhost:3000",
        )
        self.assertEqual(
            config.allowed_origins,
            ("https://browser.example", "http://localhost:3000"),
        )
        for origin in (
            "https://user@browser.example",
            "https://browser.example/path",
            "https://browser.example?query=value",
        ):
            with (
                self.subTest(origin=origin),
                self.assertRaises(self.security.RuntimeConfigurationError),
            ):
                build_config(self.security, MCP_ALLOWED_ORIGINS=origin)


class AccessControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server, self.security = import_modules()
        self.downstream = TrackingMcpApp()

    async def test_portal_rejects_missing_and_invalid_grants_before_body_read(self):
        middleware = self.security.AccessControlMiddleware(
            self.downstream,
            build_config(self.security, "portal"),
        )
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "discord_health_check", "arguments": {}},
            },
        ]

        for request in requests:
            with self.subTest(method=request["method"]):
                status, body, receive_calls = await invoke(middleware, body=request)
                self.assertEqual(status, 401)
                self.assertEqual(receive_calls, 0)
                self.assertIn(b"missing_portal_grant", body)

        status, body, receive_calls = await invoke(
            middleware,
            headers={"X-MADPANDA-PORTAL-GRANT": "wrong-grant"},
        )
        self.assertEqual(status, 401)
        self.assertEqual(receive_calls, 0)
        self.assertIn(b"invalid_portal_grant", body)
        self.assertNotIn(TEST_PORTAL_GRANT.encode(), body)
        self.assertEqual(self.downstream.calls, 0)

    async def test_valid_portal_grant_replays_body_once(self):
        middleware = self.security.AccessControlMiddleware(
            self.downstream,
            build_config(self.security, "portal"),
        )
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        status, body, receive_calls = await invoke(
            middleware,
            headers={"X-MADPANDA-PORTAL-GRANT": TEST_PORTAL_GRANT},
            body=request,
        )

        self.assertEqual(status, 200)
        self.assertEqual(body, b'{"ok":true}')
        self.assertEqual(receive_calls, 1)
        self.assertEqual(self.downstream.calls, 1)
        self.assertEqual(self.downstream.received_body, json.dumps(request).encode())

    async def test_standalone_requires_strict_bearer_auth(self):
        middleware = self.security.AccessControlMiddleware(
            self.downstream,
            build_config(self.security, "standalone"),
        )
        for authorization, expected_code in (
            (None, b"missing_access_token"),
            ("Basic value", b"invalid_access_token"),
            ("Bearer wrong-token", b"invalid_access_token"),
            (f"Bearer {TEST_ACCESS_TOKEN} extra", b"invalid_access_token"),
        ):
            headers = {} if authorization is None else {"Authorization": authorization}
            with self.subTest(authorization=authorization):
                status, body, receive_calls = await invoke(middleware, headers=headers)
                self.assertEqual(status, 401)
                self.assertIn(expected_code, body)
                self.assertEqual(receive_calls, 0)

        status, _, receive_calls = await invoke(
            middleware,
            headers={"Authorization": f"Bearer {TEST_ACCESS_TOKEN}"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(receive_calls, 1)
        self.assertEqual(self.downstream.calls, 1)

    async def test_origin_and_declared_oversize_fail_before_body_read(self):
        middleware = self.security.AccessControlMiddleware(
            self.downstream,
            build_config(self.security, "standalone"),
        )
        auth = {"Authorization": f"Bearer {TEST_ACCESS_TOKEN}"}

        status, body, receive_calls = await invoke(
            middleware,
            headers={**auth, "Origin": "https://browser.example"},
        )
        self.assertEqual(status, 403)
        self.assertIn(b"origin_not_allowed", body)
        self.assertEqual(receive_calls, 0)

        status, body, receive_calls = await invoke(
            middleware,
            headers={**auth, "Host": "attacker.example"},
        )
        self.assertEqual(status, 421)
        self.assertIn(b"host_not_allowed", body)
        self.assertEqual(receive_calls, 0)

        status, body, receive_calls = await invoke(
            middleware,
            headers={**auth, "Content-Length": "1025"},
        )
        self.assertEqual(status, 413)
        self.assertIn(b"request_body_too_large", body)
        self.assertEqual(receive_calls, 0)

    async def test_duplicate_security_headers_fail_before_auth_or_body_read(self):
        middleware = self.security.AccessControlMiddleware(
            self.downstream,
            build_config(self.security, "standalone"),
        )
        status, body, receive_calls = await invoke(
            middleware,
            headers={"Authorization": f"Bearer {TEST_ACCESS_TOKEN}"},
            extra_headers=[(b"authorization", f"Bearer {TEST_ACCESS_TOKEN}".encode())],
        )

        self.assertEqual(status, 400)
        self.assertIn(b"duplicate_security_header", body)
        self.assertEqual(receive_calls, 0)
        self.assertEqual(self.downstream.calls, 0)

    async def test_duplicate_provider_headers_fail_before_body_read(self):
        middleware = self.security.AccessControlMiddleware(
            self.downstream,
            build_config(self.security, "standalone"),
            singleton_headers=("x-discord-bot-token",),
        )
        status, body, receive_calls = await invoke(
            middleware,
            headers={
                "Authorization": f"Bearer {TEST_ACCESS_TOKEN}",
                "x-discord-bot-token": "first-provider-token",
            },
            extra_headers=[(b"x-discord-bot-token", b"second-provider-token")],
        )

        self.assertEqual(status, 400)
        self.assertIn(b"duplicate_security_header", body)
        self.assertEqual(receive_calls, 0)
        self.assertEqual(self.downstream.calls, 0)

    async def test_chunked_oversize_is_bounded_before_dispatch(self):
        middleware = self.security.AccessControlMiddleware(
            self.downstream,
            build_config(self.security, "standalone"),
        )
        status, body, receive_calls = await invoke(
            middleware,
            headers={"Authorization": f"Bearer {TEST_ACCESS_TOKEN}"},
            chunks=[(b"a" * 600, True), (b"b" * 600, False)],
        )

        self.assertEqual(status, 413)
        self.assertIn(b"request_body_too_large", body)
        self.assertEqual(receive_calls, 2)
        self.assertEqual(self.downstream.calls, 0)

    async def test_build_app_keeps_health_public_and_mcp_authenticated(self):
        app = self.server.build_app()
        mcp_status, mcp_body, receive_calls = await invoke(
            app,
            body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        health_status, health_body, _ = await invoke(app, path="/health")

        self.assertEqual(mcp_status, 401)
        self.assertEqual(receive_calls, 0)
        self.assertIn(b"missing_access_token", mcp_body)
        self.assertEqual(health_status, 200)
        health = json.loads(health_body)
        self.assertTrue(health["ok"])
        self.assertEqual(health["service_id"], "discord")
        self.assertEqual(health["raw_tool_count"], 55)
        self.assertEqual(health["exposed_tool_count"], 55)
        self.assertEqual(health["agent_ready_tool_count"], 49)
        self.assertEqual(health["documented_tool_count"], 55)
        self.assertEqual(health["version"], self.server.__version__)
        self.assertEqual(self.server.mcp._mcp_server.version, self.server.__version__)
        self.assertTrue(health["configuration_ready"])
        self.assertEqual(health["configuration"]["provider_credentials"], "server_scoped")
        self.assertTrue(health["configuration"]["server_channel_policy_ready"])
        self.assertFalse(health["public_mode"])
        self.assertFalse(health["portal_grant_configured"])

        with patch.dict(
            os.environ,
            {
                "MCP_SERVER_VERSION": "secret-version-value",
                "MCP_SOURCE_FINGERPRINT": "secret-source-value",
                "MCP_IMAGE_REFERENCE": "secret-image-value",
            },
        ):
            _, unsafe_body, _ = await invoke(app, path="/health")
        safe_health = json.loads(unsafe_body)
        self.assertEqual(safe_health["version"], self.server.__version__)
        self.assertEqual(safe_health["source_fingerprint"], "unknown")
        self.assertEqual(safe_health["image_reference"], "unknown")
        self.assertNotIn("secret", unsafe_body.decode())

        with (
            patch.object(self.server, "DISCORD_CREDENTIAL_MODE", "request"),
            patch.object(self.server, "PUBLIC_MODE", False),
        ):
            _, request_mode_body, _ = await invoke(app, path="/health")
        request_mode_health = json.loads(request_mode_body)
        self.assertEqual(request_mode_health["status"], "healthy")
        self.assertEqual(
            request_mode_health["configuration"]["provider_credentials"],
            "request_scoped",
        )

        with (
            patch.object(self.server, "DISCORD_CREDENTIAL_MODE", "request"),
            patch.object(self.server, "PUBLIC_MODE", False),
            patch.object(self.server, "ALLOW_ALL_CHANNELS", False),
            patch.object(self.server, "ALLOWED_CHANNEL_IDS", set()),
        ):
            _, unscoped_request_body, _ = await invoke(app, path="/health")
        unscoped_request_health = json.loads(unscoped_request_body)
        self.assertEqual(unscoped_request_health["status"], "degraded")
        self.assertFalse(unscoped_request_health["configuration_ready"])
        self.assertFalse(unscoped_request_health["configuration"]["server_channel_policy_ready"])


if __name__ == "__main__":
    unittest.main()
