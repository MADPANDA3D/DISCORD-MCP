import importlib
import json
import os
import sys
import unittest
from pathlib import Path


TEST_GRANT = "test-portal-grant"


def import_server():
    # Container images declare empty credential variables, so assign explicit
    # test-only values instead of relying on setdefault.
    os.environ["MCP_PUBLIC_MODE"] = "false"
    os.environ["DISCORD_TOKEN"] = "private-test-token"
    os.environ["DISCORD_GUILD_ID"] = "123456789012345678"

    fastmcp_dir = Path(__file__).resolve().parents[1]
    if str(fastmcp_dir) not in sys.path:
        sys.path.insert(0, str(fastmcp_dir))
    return importlib.import_module("discord_mcp_server")


class TrackingMcpApp:
    def __init__(self):
        self.calls = 0

    async def __call__(self, scope, receive, send):
        self.calls += 1
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"ok":true}'})


async def invoke(app, *, grant=None, body=None, path="/mcp"):
    sent = []
    receive_calls = 0
    headers = [(b"content-type", b"application/json")]
    if grant is not None:
        headers.append((b"x-madpanda-portal-grant", grant.encode("utf-8")))
    encoded_body = json.dumps(body or {}).encode("utf-8")

    async def receive():
        nonlocal receive_calls
        receive_calls += 1
        return {"type": "http.request", "body": encoded_body, "more_body": False}

    async def send(message):
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": "POST",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": headers,
        },
        receive,
        send,
    )
    status = next(
        message["status"]
        for message in sent
        if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return status, response_body, receive_calls


class PortalGrantAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server = import_server()
        self.downstream = TrackingMcpApp()
        self.middleware = self.server.PortalGrantMiddleware(
            self.downstream,
            public_mode=True,
            grant_token=TEST_GRANT,
            grant_header="X-MADPANDA-PORTAL-GRANT",
        )

    async def test_missing_grant_blocks_initialize_list_and_call_before_body_read(self):
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
                status, body, receive_calls = await invoke(
                    self.middleware,
                    body=request,
                )
                self.assertEqual(status, 401)
                self.assertEqual(receive_calls, 0)
                self.assertIn(b"missing_portal_grant", body)
                self.assertNotIn(TEST_GRANT.encode("utf-8"), body)

        self.assertEqual(self.downstream.calls, 0)

    async def test_invalid_grant_blocks_downstream_without_validity_oracle(self):
        status, body, receive_calls = await invoke(
            self.middleware,
            grant="wrong-grant",
            body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )

        self.assertEqual(status, 401)
        self.assertEqual(receive_calls, 0)
        self.assertEqual(self.downstream.calls, 0)
        self.assertIn(b"invalid_portal_grant", body)
        self.assertNotIn(TEST_GRANT.encode("utf-8"), body)

    async def test_valid_grant_reaches_mcp_dispatch_once(self):
        status, body, _ = await invoke(
            self.middleware,
            grant=TEST_GRANT,
            body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )

        self.assertEqual(status, 200)
        self.assertEqual(body, b'{"ok":true}')
        self.assertEqual(self.downstream.calls, 1)

    async def test_unconfigured_public_runtime_fails_closed(self):
        middleware = self.server.PortalGrantMiddleware(
            self.downstream,
            public_mode=True,
            grant_token="",
            grant_header="X-MADPANDA-PORTAL-GRANT",
        )

        status, body, receive_calls = await invoke(middleware, grant=TEST_GRANT)

        self.assertEqual(status, 503)
        self.assertEqual(receive_calls, 0)
        self.assertEqual(self.downstream.calls, 0)
        self.assertIn(b"portal_grant_not_configured", body)

    async def test_build_app_wraps_mcp_and_keeps_health_public(self):
        original_public_mode = self.server.PUBLIC_MODE
        original_grant = self.server.MCP_PORTAL_GRANT_TOKEN
        self.server.PUBLIC_MODE = True
        self.server.MCP_PORTAL_GRANT_TOKEN = TEST_GRANT
        try:
            app = self.server.build_app()

            mcp_status, mcp_body, receive_calls = await invoke(
                app,
                body={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {},
                },
            )
            health_status, health_body, _ = await invoke(app, path="/health")
        finally:
            self.server.PUBLIC_MODE = original_public_mode
            self.server.MCP_PORTAL_GRANT_TOKEN = original_grant

        self.assertEqual(mcp_status, 401)
        self.assertEqual(receive_calls, 0)
        self.assertIn(b"missing_portal_grant", mcp_body)
        self.assertEqual(health_status, 200)
        health = json.loads(health_body)
        self.assertTrue(health["ok"])
        self.assertTrue(health["public_mode"])
        self.assertTrue(health["portal_grant_configured"])

    def test_public_mode_ignores_server_discord_fallbacks(self):
        original_public_mode = self.server.PUBLIC_MODE
        original_token = self.server.DISCORD_TOKEN
        original_guild_id = self.server.DEFAULT_GUILD_ID
        self.server.PUBLIC_MODE = True
        self.server.DISCORD_TOKEN = "server-token-must-not-be-used"
        self.server.DEFAULT_GUILD_ID = 987654321012345678
        try:
            self.assertIsNone(self.server.get_active_request_token())
            self.assertIsNone(self.server.get_active_guild_id())

            context_token = self.server.REQUEST_OVERRIDE_CONTEXT.set(
                {"token": "request-token", "guild_id": 123456789012345678}
            )
            try:
                self.assertEqual(
                    self.server.get_active_request_token(),
                    "request-token",
                )
                self.assertEqual(
                    self.server.get_active_guild_id(),
                    123456789012345678,
                )
            finally:
                self.server.REQUEST_OVERRIDE_CONTEXT.reset(context_token)
        finally:
            self.server.PUBLIC_MODE = original_public_mode
            self.server.DISCORD_TOKEN = original_token
            self.server.DEFAULT_GUILD_ID = original_guild_id


if __name__ == "__main__":
    unittest.main()
