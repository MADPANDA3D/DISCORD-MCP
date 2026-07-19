import asyncio
import importlib
import io
import json
import logging
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

GUILD_ID = 123_456_789_012_345_678
CHANNEL_ID = 123_456_789_012_345_679
MESSAGE_ID = 123_456_789_012_345_680
SENTINEL = "diagnostic-secret-sentinel"
SIGNED_URL = (
    "https://cdn.discordapp.com/attachments/123/456/image.png"
    "?ex=secret-expiry&is=secret-issued&hm=secret-signature"
)


def import_server():
    os.environ["MCP_MODE"] = "standalone"
    os.environ["MCP_ACCESS_TOKEN"] = "diagnostic-access-" + ("a" * 32)
    os.environ["DISCORD_TOKEN"] = "diagnostic-discord-" + ("b" * 32)
    os.environ["DISCORD_GUILD_ID"] = str(GUILD_ID)
    os.environ["DISCORD_ALLOWED_CHANNEL_IDS"] = str(CHANNEL_ID)
    os.environ["MCP_REQUIRE_CONFIRM"] = "false"

    src_dir = Path(__file__).resolve().parents[1] / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    return importlib.import_module("madpanda_discord_mcp.server")


class FakeResponseContent:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.iterated = False

    async def iter_chunked(self, _size):
        self.iterated = True
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, status, chunks=(), headers=None):
        self.status = status
        self.headers = dict(headers or {})
        self.content = FakeResponseContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


class FakeSession:
    def __init__(self, response, capture, **kwargs):
        self.response = response
        self.capture = capture
        self.capture["session_kwargs"] = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False

    def post(self, url, **kwargs):
        self.capture["post_url"] = url
        self.capture["post_kwargs"] = kwargs
        return self.response


class FakeBot:
    def is_ready(self):
        return False

    def is_closed(self):
        return False


class DiagnosticSafetyTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = import_server()

    def test_only_deliberate_client_errors_expose_messages(self):
        safe = self.server.exception_to_error(
            self.server.ClientInputError("channelId must be a Discord snowflake")
        )
        unsafe = self.server.exception_to_error(ValueError(SENTINEL))

        self.assertEqual(safe["type"], "invalid_payload")
        self.assertEqual(safe["message"], "channelId must be a Discord snowflake")
        self.assertEqual(unsafe["type"], "internal_error")
        self.assertNotIn(SENTINEL, json.dumps(unsafe))

    def test_application_logs_scrub_credentials_and_third_party_debug_stays_off(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        self.server.logger.addHandler(handler)
        try:
            with patch.object(self.server, "active_secret_values", return_value=(SENTINEL,)):
                self.server.logger.warning(
                    "provider failed token=%s webhook=%s",
                    SENTINEL,
                    "https://discord.com/api/webhooks/123/credential",
                )
        finally:
            self.server.logger.removeHandler(handler)

        captured = stream.getvalue()
        self.assertNotIn(SENTINEL, captured)
        self.assertNotIn("credential", captured)
        self.assertIn("[REDACTED]", captured)
        self.assertIn("[REDACTED_WEBHOOK_URL]", captured)
        self.assertGreaterEqual(logging.getLogger().getEffectiveLevel(), logging.WARNING)
        for name in ("aiohttp", "asyncio", "discord", "mcp", "uvicorn"):
            self.assertGreaterEqual(logging.getLogger(name).getEffectiveLevel(), logging.WARNING)

    def test_third_party_logs_scrub_urls_credentials_and_exception_details(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        third_party_logger = logging.getLogger("aiohttp.client")
        third_party_logger.addHandler(handler)
        try:
            with patch.object(self.server, "active_secret_values", return_value=(SENTINEL,)):
                try:
                    raise RuntimeError(f"{SENTINEL} {SIGNED_URL}")
                except RuntimeError:
                    third_party_logger.warning(
                        "request failed url=%s token=%s",
                        SIGNED_URL,
                        SENTINEL,
                        exc_info=True,
                    )
        finally:
            third_party_logger.removeHandler(handler)

        captured = stream.getvalue()
        self.assertIn("[REDACTED_DISCORD_CDN_URL]", captured)
        self.assertIn("[REDACTED]", captured)
        self.assertNotIn(SENTINEL, captured)
        self.assertNotIn("secret-signature", captured)
        self.assertNotIn("RuntimeError", captured)

    def test_structured_access_log_arguments_survive_secret_scrubbing(self):
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:12345", "GET", "/health", "1.1", 200),
            None,
        )

        self.server.APPLICATION_SECRET_LOG_FILTER.filter(record)

        self.assertEqual(len(record.args), 5)
        self.assertEqual(record.args[-1], 200)
        self.assertEqual(record.getMessage(), '127.0.0.1:12345 - "GET /health HTTP/1.1" 200')

    async def test_debug_snapshot_never_returns_task_exception_text_or_object_ids(self):
        async def fail():
            raise RuntimeError(SENTINEL)

        task = asyncio.create_task(fail())
        await asyncio.gather(task, return_exceptions=True)
        token = self.server.get_active_request_token()
        fingerprint = self.server.credential_fingerprint(token)
        self.server.BOT_POOL[fingerprint] = self.server.BotState(
            credential_fingerprint=fingerprint,
            bot=FakeBot(),
            task=task,
            lock=asyncio.Lock(),
            last_used=0.0,
        )
        try:
            snapshot = self.server.get_client_debug_snapshot()
        finally:
            self.server.BOT_POOL.pop(fingerprint, None)

        serialized = json.dumps(snapshot)
        self.assertEqual(snapshot["task_error_type"], "task_failed")
        self.assertNotIn(SENTINEL, serialized)
        self.assertNotIn("client_id", snapshot)
        self.assertNotIn("task_id", snapshot)
        self.assertNotIn("task_exception", snapshot)

    async def test_request_override_failure_is_normalized_at_tool_boundary(self):
        with (
            patch.object(self.server, "ALLOW_REQUEST_OVERRIDES", True),
            patch.object(
                self.server,
                "build_request_overrides",
                AsyncMock(side_effect=ValueError(SENTINEL)),
            ),
        ):
            result = await self.server.discord_job_status("")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "internal_error")
        self.assertNotIn(SENTINEL, json.dumps(result))

    async def test_missing_request_headers_preserve_structured_auth_error(self):
        with (
            patch.object(self.server, "ALLOW_REQUEST_OVERRIDES", True),
            patch.object(self.server, "REQUIRE_REQUEST_DISCORD_TOKEN", True),
            patch.object(self.server, "REQUIRE_REQUEST_GUILD_ID", True),
            patch.object(self.server, "REQUIRE_REQUEST_ALLOWED_CHANNELS", True),
            patch.object(self.server, "get_http_headers", return_value={}),
        ):
            result = await self.server.discord_job_status("")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "permission_denied")
        self.assertEqual(
            result["error"]["diagnostics"]["required_headers"],
            [
                self.server.REQUEST_DISCORD_TOKEN_HEADER,
                self.server.REQUEST_DISCORD_GUILD_ID_HEADER,
                self.server.REQUEST_DISCORD_ALLOWED_CHANNELS_HEADER,
            ],
        )

    async def test_invalid_client_snowflake_is_invalid_payload_before_provider_work(
        self,
    ):
        invalid_values = ("not-a-snowflake", "0", "-1", str(2**64), True)
        for value in invalid_values:
            with self.subTest(value=value):
                self.assertIsNone(self.server.parse_snowflake(value))

        with patch.object(self.server, "get_active_admin_tools_enabled", return_value=True):
            result = await self.server.delete_webhook(
                "not-a-snowflake", confirm=self.server.CONFIRM_APPLY_VALUE
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "invalid_payload")
        self.assertIn("webhookId", result["error"]["message"])

    async def test_openai_error_body_and_signed_url_are_never_returned_or_read(self):
        response = FakeResponse(
            401,
            chunks=[
                json.dumps(
                    {
                        "error": SENTINEL,
                        "api_key": "openai-test-key",
                        "signed_url": SIGNED_URL,
                    }
                ).encode()
            ],
        )
        capture = {}
        result = await self._analyze_with_response(response, capture)

        serialized = json.dumps(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "provider_unavailable")
        self.assertEqual(result["error"]["diagnostics"], {"status": 401})
        self.assertFalse(response.content.iterated)
        self.assertNotIn(SENTINEL, serialized)
        self.assertNotIn("openai-test-key", serialized)
        self.assertNotIn("secret-signature", serialized)
        self.assertFalse(capture["post_kwargs"]["allow_redirects"])
        self.assertFalse(capture["session_kwargs"]["trust_env"])

    async def test_openai_success_is_capped_allowlisted_and_has_safe_metadata(self):
        provider_payload = {
            "choices": [{"message": {"content": "x" * 33_000}}],
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 9,
                "total_tokens": 16,
                "input_tokens": -1,
                "output_tokens": True,
                "nested": {"secret": SENTINEL},
                "api_key": "provider-secret",
            },
        }
        response = FakeResponse(200, chunks=[json.dumps(provider_payload).encode()])
        result = await self._analyze_with_response(response, {})

        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertEqual(len(data["text"]), self.server.OPENAI_RESULT_MAX_CHARS)
        self.assertEqual(
            data["usage"],
            {"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16},
        )
        self.assertNotIn("url", data["attachment"])
        self.assertNotIn("proxy_url", data["attachment"])
        self.assertTrue(any("truncated" in item for item in result["meta"]["warnings"]))
        serialized = json.dumps(result)
        self.assertNotIn(SENTINEL, serialized)
        self.assertNotIn("provider-secret", serialized)
        self.assertNotIn("secret-signature", serialized)

    async def test_openai_response_reader_rejects_declared_and_streamed_oversize(self):
        declared = FakeResponse(
            200,
            headers={"Content-Length": str(self.server.OPENAI_RESPONSE_MAX_BYTES + 1)},
        )
        with self.assertRaises(self.server.ProviderResponseError):
            await self.server.read_bounded_openai_response(declared)
        self.assertFalse(declared.content.iterated)

        streamed = FakeResponse(
            200,
            chunks=[
                b"a" * (self.server.OPENAI_RESPONSE_MAX_BYTES - 1),
                b"bc",
            ],
        )
        with self.assertRaises(self.server.ProviderResponseError):
            await self.server.read_bounded_openai_response(streamed)
        self.assertTrue(streamed.content.iterated)

    async def test_health_reports_only_openai_endpoint_presence(self):
        client = SimpleNamespace(user=None)
        client.application_info = AsyncMock(side_effect=RuntimeError(SENTINEL))
        guild = SimpleNamespace(id=GUILD_ID, name="Test Guild")
        endpoint = f"https://user:{SENTINEL}@example.invalid/v1?token={SENTINEL}"
        with (
            patch.object(self.server, "OPENAI_VISION_API_URL", endpoint),
            patch.object(self.server, "get_client", AsyncMock(return_value=client)),
            patch.object(self.server, "get_guild", AsyncMock(return_value=guild)),
            patch.object(self.server, "effective_allowed_channel_ids", return_value=[]),
        ):
            result = await self.server.discord_health_check()

        serialized = json.dumps(result)
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["discord_config"]["openai_vision_api_configured"])
        self.assertNotIn(SENTINEL, serialized)
        self.assertNotIn("example.invalid", serialized)
        self.assertNotIn("openai_vision_api_url", serialized)

    async def _analyze_with_response(self, response, capture):
        attachment = SimpleNamespace(
            filename="image.png",
            content_type="image/png",
            size=1024,
            width=640,
            height=480,
            url=SIGNED_URL,
            proxy_url=SIGNED_URL + "&proxy=true",
        )
        message = SimpleNamespace(id=MESSAGE_ID, attachments=[attachment])
        channel = SimpleNamespace(
            id=CHANNEL_ID,
            parent_id=None,
            guild=SimpleNamespace(id=GUILD_ID),
        )

        def session_factory(**kwargs):
            return FakeSession(response, capture, **kwargs)

        with (
            patch.object(self.server, "OPENAI_VISION_ENABLED", True),
            patch.object(self.server, "get_openai_api_key", return_value="openai-test-key"),
            patch.object(self.server, "get_message_target", AsyncMock(return_value=channel)),
            patch.object(self.server, "require_read_allowed", return_value=None),
            patch.object(self.server, "retry_read", AsyncMock(return_value=message)),
            patch.object(self.server.aiohttp, "ClientSession", session_factory),
        ):
            return await self.server.analyze_attachment(
                channel_id=str(CHANNEL_ID),
                message_id=str(MESSAGE_ID),
            )


if __name__ == "__main__":
    unittest.main()
