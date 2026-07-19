import importlib
import json
import os
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

GUILD_ID = 123_456_789_012_345_678
CHANNEL_A = 123_456_789_012_345_679
CHANNEL_B = 123_456_789_012_345_680
WEBHOOK_ID = 123_456_789_012_345_681
WEBHOOK_SECRET = "credential-like-webhook-token-value"
WEBHOOK_URL = f"https://discord.com/api/webhooks/{WEBHOOK_ID}/{WEBHOOK_SECRET}"


def import_server():
    os.environ["MCP_MODE"] = "standalone"
    os.environ["MCP_ACCESS_TOKEN"] = "webhook-test-access-" + ("a" * 32)
    os.environ["DISCORD_TOKEN"] = "webhook-test-discord-token"
    os.environ["DISCORD_GUILD_ID"] = str(GUILD_ID)
    os.environ["DISCORD_ALLOWED_CHANNEL_IDS"] = str(CHANNEL_A)
    src_dir = Path(__file__).resolve().parents[1] / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    return importlib.import_module("madpanda_discord_mcp.server")


class FakeGuild:
    id = GUILD_ID


class FakeWebhook:
    def __init__(self, channel_id=CHANNEL_A):
        self.id = WEBHOOK_ID
        self.name = "safe-webhook-name"
        self.url = WEBHOOK_URL
        self.channel_id = channel_id
        self.guild_id = GUILD_ID
        self.delete = AsyncMock()


class FakeChannel:
    id = CHANNEL_A
    guild = FakeGuild()

    def __init__(self, webhooks):
        self._webhooks = webhooks

    async def webhooks(self):
        return self._webhooks


class FakeClient:
    def __init__(self, webhook):
        self.fetch_webhook = AsyncMock(return_value=webhook)


class WebhookSafetyTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = import_server()

    def runtime_policy(self, *, admin=True):
        stack = ExitStack()
        values = {
            "DISCORD_CREDENTIAL_MODE": "server",
            "ALLOW_REQUEST_OVERRIDES": False,
            "ALLOW_ALL_CHANNELS": False,
            "ALLOWED_CHANNEL_IDS": {CHANNEL_A},
            "BLOCKED_CHANNEL_IDS": set(),
            "DEFAULT_GUILD_ID": GUILD_ID,
            "MCP_ADMIN_TOOLS_ENABLED": admin,
            "CONFIRM_REQUIRED": True,
        }
        for name, value in values.items():
            stack.enter_context(patch.object(self.server, name, value))
        return stack

    async def test_list_webhooks_never_returns_a_url_or_token(self):
        webhook = FakeWebhook()
        channel = FakeChannel([webhook])
        with (
            self.runtime_policy(),
            patch.object(self.server, "get_text_channel", AsyncMock(return_value=channel)),
        ):
            result = await self.server.list_webhooks(channel_id=str(CHANNEL_A))

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            result["data"]["webhooks"],
            [{"id": str(WEBHOOK_ID), "name": "safe-webhook-name"}],
        )
        serialized = json.dumps(result)
        self.assertNotIn(WEBHOOK_URL, serialized)
        self.assertNotIn(WEBHOOK_SECRET, serialized)

    async def test_delete_requires_admin_before_fetching_provider_data(self):
        client = FakeClient(FakeWebhook())
        provider = AsyncMock(return_value=client)
        with (
            self.runtime_policy(admin=False),
            patch.object(self.server, "get_client", provider),
        ):
            result = await self.server.delete_webhook(
                webhook_id=str(WEBHOOK_ID),
                confirm=self.server.CONFIRM_APPLY_VALUE,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "permission_denied")
        provider.assert_not_awaited()

    async def test_delete_refuses_webhook_outside_effective_channel_scope(self):
        webhook = FakeWebhook(channel_id=CHANNEL_B)
        client = FakeClient(webhook)
        with (
            self.runtime_policy(),
            patch.object(self.server, "get_client", AsyncMock(return_value=client)),
        ):
            result = await self.server.delete_webhook(
                webhook_id=str(WEBHOOK_ID),
                confirm=self.server.CONFIRM_APPLY_VALUE,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "permission_denied")
        webhook.delete.assert_not_awaited()

    async def test_delete_applies_to_allowed_webhook_without_returning_credentials(
        self,
    ):
        webhook = FakeWebhook(channel_id=CHANNEL_A)
        client = FakeClient(webhook)
        with (
            self.runtime_policy(),
            patch.object(self.server, "get_client", AsyncMock(return_value=client)),
        ):
            result = await self.server.delete_webhook(
                webhook_id=str(WEBHOOK_ID),
                confirm=self.server.CONFIRM_APPLY_VALUE,
            )

        self.assertTrue(result["ok"], result)
        webhook.delete.assert_awaited_once()
        serialized = json.dumps(result)
        self.assertNotIn(WEBHOOK_URL, serialized)
        self.assertNotIn(WEBHOOK_SECRET, serialized)


if __name__ == "__main__":
    unittest.main()
