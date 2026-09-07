import importlib
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

GUILD_ID = 123_456_789_012_345_678
FORUM_ID = 1_546_221_796_490_084_382
THREAD_ID = 1_546_221_796_490_084_383


def import_server():
    os.environ["MCP_MODE"] = "standalone"
    os.environ["MCP_ACCESS_TOKEN"] = "forum-search-access-" + ("a" * 32)
    os.environ["DISCORD_TOKEN"] = "forum-search-token"
    os.environ["DISCORD_GUILD_ID"] = str(GUILD_ID)
    os.environ["DISCORD_ALLOWED_CHANNEL_IDS"] = str(FORUM_ID)
    src_dir = Path(__file__).resolve().parents[1] / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    return importlib.import_module("madpanda_discord_mcp.server")


class FakeForumChannel:
    id = FORUM_ID
    guild = SimpleNamespace(id=GUILD_ID)

    def __init__(self, threads):
        self.threads = threads


class FakeForumThread:
    id = THREAD_ID

    def __init__(self, messages):
        self.messages = messages

    def history(self, *, limit, before, after):
        del before, after

        async def records():
            for message in self.messages[:limit]:
                yield message

        return records()


class SearchMessagesForumTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = import_server()

    async def test_search_messages_searches_forum_post_threads(self):
        message = SimpleNamespace(
            id=1_546_221_796_490_084_384,
            author=SimpleNamespace(id=1_546_221_796_490_084_385, name="operator"),
            created_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
            content="Linear set up notes",
            embeds=[],
            jump_url="https://discord.test/forum/thread/message",
            attachments=[],
        )
        thread = FakeForumThread([message])
        message.channel = thread
        forum = FakeForumChannel([thread])
        policy_ids = []

        def require_read_allowed(channel_id, *_args, **_kwargs):
            policy_ids.append(channel_id)
            return None

        with (
            patch.object(self.server.discord, "ForumChannel", FakeForumChannel),
            patch.object(self.server.discord, "Thread", FakeForumThread),
            patch.object(self.server, "get_search_target", AsyncMock(return_value=forum)),
            patch.object(self.server, "require_read_allowed", require_read_allowed),
            patch.object(self.server, "record_api_success"),
            patch.object(self.server, "log_action"),
        ):
            result = await self.server.search_messages(
                channel_id=str(FORUM_ID),
                query="Linear set up",
                include_threads=True,
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(policy_ids, [FORUM_ID])
        self.assertEqual(result["data"]["count"], 1)
        self.assertEqual(result["data"]["messages"][0]["thread_id"], str(THREAD_ID))


if __name__ == "__main__":
    unittest.main()
