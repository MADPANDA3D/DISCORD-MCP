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
        self.message = None

    def permissions_for(self, _member):
        return FakePermissions()

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(
            id=123_456_789_012_345_699,
            jump_url="https://discord.test/thread/message",
        )

    async def fetch_message(self, _message_id):
        return self.message


class FakeEditableMessage:
    def __init__(self, author_id):
        self.author = SimpleNamespace(id=author_id)
        self.edited_content = None

    async def edit(self, *, content):
        self.edited_content = content
        return SimpleNamespace(
            id=THREAD_ID,
            jump_url="https://discord.test/thread/starter",
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

    async def test_edit_message_accepts_forum_starter_and_authorizes_parent(self):
        bot_user = SimpleNamespace(id=123_456_789_012_345_681)
        client = SimpleNamespace(user=bot_user)
        thread = FakeThread()
        message = FakeEditableMessage(bot_user.id)
        thread.message = message
        policy_ids = []

        def require_write_allowed(channel_id, *_args, **_kwargs):
            policy_ids.append(channel_id)
            return None

        with (
            patch.object(self.server, "get_active_admin_tools_enabled", return_value=True),
            patch.object(self.server.discord, "Thread", FakeThread),
            patch.object(self.server, "ensure_client_ready", AsyncMock(return_value=client)),
            patch.object(self.server, "get_message_target", AsyncMock(return_value=thread)),
            patch.object(self.server, "get_bot_member", AsyncMock(return_value=object())),
            patch.object(self.server, "require_write_allowed", require_write_allowed),
            patch.object(self.server, "is_write_allowed", return_value=True),
            patch.object(self.server, "record_api_success"),
            patch.object(self.server, "log_action"),
        ):
            preview = await self.server.edit_message(
                channel_id=str(THREAD_ID),
                message_id=str(THREAD_ID),
                new_message="updated starter",
                confirm=self.server.CONFIRM_APPLY_VALUE,
                dry_run=True,
            )
            executed = await self.server.edit_message(
                channel_id=str(THREAD_ID),
                message_id=str(THREAD_ID),
                new_message="updated starter",
                confirm=self.server.CONFIRM_APPLY_VALUE,
            )

        self.assertTrue(preview["ok"], preview)
        self.assertTrue(executed["ok"], executed)
        self.assertEqual(policy_ids, [PARENT_CHANNEL_ID, PARENT_CHANNEL_ID])
        self.assertEqual(
            preview["data"]["diagnostics"]["policy_channel_id"], str(PARENT_CHANNEL_ID)
        )
        self.assertEqual(executed["data"]["channel_id"], str(THREAD_ID))
        self.assertEqual(message.edited_content, "updated starter")

    async def test_edit_message_rejects_blocked_thread_even_when_parent_is_allowed(self):
        bot_user = SimpleNamespace(id=123_456_789_012_345_681)
        thread = FakeThread()
        message = FakeEditableMessage(bot_user.id)
        thread.message = message
        get_bot_member = AsyncMock(return_value=object())

        with (
            patch.object(self.server, "get_active_admin_tools_enabled", return_value=True),
            patch.object(self.server.discord, "Thread", FakeThread),
            patch.object(
                self.server,
                "ensure_client_ready",
                AsyncMock(return_value=SimpleNamespace(user=bot_user)),
            ),
            patch.object(self.server, "get_message_target", AsyncMock(return_value=thread)),
            patch.object(self.server, "get_active_blocked_channel_ids", return_value={THREAD_ID}),
            patch.object(self.server, "get_bot_member", get_bot_member),
            patch.object(self.server, "log_action"),
        ):
            result = await self.server.edit_message(
                channel_id=str(THREAD_ID),
                message_id=str(THREAD_ID),
                new_message="must not be edited",
                confirm=self.server.CONFIRM_APPLY_VALUE,
            )

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["message"], "Channel is blocked from writes.")
        self.assertIsNone(message.edited_content)
        get_bot_member.assert_not_awaited()

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

    def test_effective_permissions_maps_public_user_id_to_member_target(self):
        user_id = "1229101510546423960"

        self.assertEqual(
            self.server._effective_permission_target(
                {"user_id": user_id, "role_id": "", "target_id": ""},
                {},
            ),
            (user_id, "member"),
        )

    def test_effective_permissions_preserves_explicit_role_target(self):
        role_id = "1504035993781534784"

        self.assertEqual(
            self.server._effective_permission_target(
                {"user_id": "", "role_id": "", "target_id": role_id},
                {"target_type": "role"},
            ),
            (role_id, "role"),
        )

    def test_effective_permissions_rejects_ambiguous_or_conflicting_targets(self):
        with self.assertRaisesRegex(ValueError, "only one"):
            self.server._effective_permission_target(
                {"user_id": "1229101510546423960", "role_id": "1504035993781534784"},
                {},
            )
        with self.assertRaisesRegex(ValueError, "selects target_type=member"):
            self.server._effective_permission_target(
                {"user_id": "1229101510546423960"},
                {"target_type": "role"},
            )


if __name__ == "__main__":
    unittest.main()
