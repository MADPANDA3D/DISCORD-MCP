import importlib
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

GUILD_ID = 123_456_789_012_345_678
PARENT_CHANNEL_ID = 123_456_789_012_345_679
THREAD_ID = 123_456_789_012_345_680


def import_server():
    os.environ["MCP_MODE"] = "standalone"
    os.environ["MCP_ACCESS_TOKEN"] = "ticket-regression-access-" + ("a" * 32)
    os.environ["DISCORD_TOKEN"] = "ticket-regression-discord-" + ("b" * 32)
    os.environ["DISCORD_GUILD_ID"] = str(GUILD_ID)
    os.environ["DISCORD_ALLOWED_CHANNEL_IDS"] = str(PARENT_CHANNEL_ID)
    src_dir = Path(__file__).resolve().parents[1] / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    return importlib.import_module("madpanda_discord_mcp.server")


class FakePermissions:
    view_channel = True
    read_message_history = True
    send_messages = True
    send_messages_in_threads = True
    embed_links = True
    attach_files = True
    add_reactions = True
    manage_messages = False
    create_public_threads = False
    create_private_threads = False


class FakeThread:
    id = THREAD_ID
    parent_id = PARENT_CHANNEL_ID
    guild = SimpleNamespace(id=GUILD_ID)

    def __init__(self):
        self.sent = []

    def permissions_for(self, _member):
        return FakePermissions()

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(
            id=123_456_789_012_345_699,
            jump_url="https://discord.test/thread/message",
        )


class FakeHistoryChannel:
    id = PARENT_CHANNEL_ID
    guild = SimpleNamespace(id=GUILD_ID)

    def __init__(self, messages):
        self.messages = messages
        self.oldest_first = None

    def history(self, *, limit, before, after, oldest_first):
        del before, after
        self.oldest_first = oldest_first

        async def records():
            for message in self.messages[:limit]:
                yield message

        return records()


def fake_message(index):
    embed = SimpleNamespace(
        title="T" * 256,
        description="D" * 4_096,
        url="https://discord.test/embed",
        fields=[SimpleNamespace(name="N" * 256, value="V" * 1_024, inline=False) for _ in range(8)],
        footer=None,
        author=None,
        color=None,
    )
    return SimpleNamespace(
        id=123_456_789_012_346_000 + index,
        author=SimpleNamespace(id=123_456_789_012_347_000 + index, name=f"member-{index}"),
        created_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
        content="C" * 2_000,
        embeds=[embed, embed],
        jump_url=f"https://discord.test/messages/{index}",
        attachments=[],
    )


class TicketRegressionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = import_server()

    async def test_send_message_accepts_thread_and_authorizes_its_parent(self):
        thread = FakeThread()
        policy_ids = []

        def require_write_allowed(channel_id, *_args, **_kwargs):
            policy_ids.append(channel_id)
            return None

        with (
            patch.object(self.server, "ALLOW_REQUEST_OVERRIDES", False),
            patch.object(self.server.discord, "Thread", FakeThread),
            patch.object(self.server, "get_message_target", AsyncMock(return_value=thread)),
            patch.object(self.server, "get_bot_member", AsyncMock(return_value=object())),
            patch.object(self.server, "require_write_allowed", require_write_allowed),
            patch.object(self.server, "is_write_allowed", return_value=True),
            patch.object(self.server, "record_api_success"),
            patch.object(self.server, "log_action"),
        ):
            result = await self.server.send_message(
                channel_id=str(THREAD_ID),
                message="thread delivery",
                confirm=self.server.CONFIRM_APPLY_VALUE,
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(policy_ids, [PARENT_CHANNEL_ID])
        self.assertEqual(result["data"]["channel_id"], str(THREAD_ID))
        self.assertEqual(thread.sent[0]["content"], "thread delivery")

    async def test_read_messages_returns_bounded_page_with_continuation(self):
        channel = FakeHistoryChannel([fake_message(index) for index in range(100)])
        with (
            patch.object(self.server, "ALLOW_REQUEST_OVERRIDES", False),
            patch.object(self.server, "get_message_target", AsyncMock(return_value=channel)),
            patch.object(self.server, "require_read_allowed", return_value=None),
            patch.object(self.server, "record_api_success"),
            patch.object(self.server, "log_action"),
        ):
            result = await self.server.read_messages(channel_id=str(PARENT_CHANNEL_ID), count="100")

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["data"]["truncated"])
        self.assertGreater(result["data"]["count"], 0)
        self.assertLess(result["data"]["count"], 100)
        self.assertEqual(
            result["data"]["next_before_message_id"],
            result["data"]["messages"][-1]["id"],
        )
        self.assertLessEqual(
            self.server.serialized_tool_result_size(result),
            self.server.MCP_TOOL_OUTPUT_MAX_BYTES,
        )

    async def test_read_messages_forces_reverse_order_for_after_pagination(self):
        channel = FakeHistoryChannel([fake_message(index) for index in range(3)])
        with (
            patch.object(self.server, "ALLOW_REQUEST_OVERRIDES", False),
            patch.object(self.server, "get_message_target", AsyncMock(return_value=channel)),
            patch.object(self.server, "require_read_allowed", return_value=None),
            patch.object(self.server, "record_api_success"),
            patch.object(self.server, "log_action"),
        ):
            result = await self.server.read_messages(
                channel_id=str(PARENT_CHANNEL_ID),
                count="3",
                after_message_id=str(PARENT_CHANNEL_ID),
            )

        self.assertTrue(result["ok"], result)
        self.assertFalse(channel.oldest_first)


if __name__ == "__main__":
    unittest.main()
