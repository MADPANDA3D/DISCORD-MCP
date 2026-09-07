import importlib
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

CHANNEL_ID = 123_456_789_012_345_679
GUILD_ID = 123_456_789_012_345_678


def import_server():
    os.environ["MCP_MODE"] = "standalone"
    os.environ["MCP_ACCESS_TOKEN"] = "poll-test-access-" + ("a" * 32)
    os.environ["DISCORD_TOKEN"] = "poll-test-discord-" + ("b" * 32)
    os.environ["DISCORD_GUILD_ID"] = str(GUILD_ID)
    os.environ["DISCORD_ALLOWED_CHANNEL_IDS"] = str(CHANNEL_ID)
    os.environ["MCP_REQUIRE_CONFIRM"] = "false"
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


class FakeChannel:
    id = CHANNEL_ID
    guild = SimpleNamespace(id=GUILD_ID)

    def __init__(self):
        self.sent = []

    def permissions_for(self, _member):
        return FakePermissions()

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(
            id=123_456_789_012_345_700,
            jump_url="https://discord.test/channels/1/2/3",
            poll=kwargs.get("poll"),
        )


class NativePollTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = import_server()

    def test_poll_contract_validates_bounds(self):
        with self.assertRaisesRegex(ValueError, "2 to 10"):
            self.server.build_native_poll({"question": "One?", "answers": ["yes"]})
        with self.assertRaisesRegex(ValueError, "32 days"):
            self.server.build_native_poll(
                {"question": "Too long?", "answers": ["yes", "no"], "duration_hours": 769}
            )
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            self.server.build_native_poll(
                {"question": "Many?", "answers": ["yes", "no"], "allow_multiselect": "yes"}
            )

    async def test_send_message_creates_native_poll_and_returns_message_link(self):
        channel = FakeChannel()
        with (
            patch.object(self.server, "get_message_target", AsyncMock(return_value=channel)),
            patch.object(self.server, "get_bot_member", AsyncMock(return_value=object())),
            patch.object(self.server, "require_write_allowed", return_value=None),
            patch.object(self.server, "is_write_allowed", return_value=True),
            patch.object(self.server, "record_api_success"),
            patch.object(self.server, "log_action"),
        ):
            result = await self.server.send_message(
                channel_id=str(CHANNEL_ID),
                poll={
                    "question": "Ship it?",
                    "answers": ["Yes", "No"],
                    "duration_hours": 768,
                    "allow_multiselect": True,
                },
                confirm=self.server.CONFIRM_APPLY_VALUE,
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["message_id"], "123456789012345700")
        self.assertEqual(result["data"]["jump_url"], "https://discord.test/channels/1/2/3")
        self.assertEqual(channel.sent[0]["poll"].question, "Ship it?")
        self.assertTrue(channel.sent[0]["poll"].multiple)
        self.assertEqual(len(channel.sent[0]["poll"].answers), 2)

    async def test_dry_run_returns_poll_plan_without_sending(self):
        with patch.object(
            self.server, "get_message_target", AsyncMock(side_effect=RuntimeError("offline"))
        ):
            result = await self.server.send_message(
                channel_id=str(CHANNEL_ID),
                poll={
                    "question": "Ship it?",
                    "answers": ["Yes", "No"],
                    "durationHours": 24,
                    "allowMultiselect": False,
                },
                dry_run=True,
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["diagnostics"]["poll"]["duration_hours"], 24)

    def test_readback_serializes_poll_answer_state(self):
        media = SimpleNamespace(text="Yes", emoji=None)
        poll = SimpleNamespace(
            question=SimpleNamespace(text="Ship it?"),
            answers=[SimpleNamespace(id=1, media=media, vote_count=4, self_voted=True)],
            expires_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
            multiple=False,
            is_finalized=False,
        )
        result = self.server.serialize_native_poll(poll)
        self.assertEqual(result["question"], "Ship it?")
        self.assertEqual(result["answers"][0]["vote_count"], 4)
        self.assertTrue(result["answers"][0]["self_voted"])

        poll.is_finalized = lambda: False
        result = self.server.serialize_native_poll(poll)
        self.assertFalse(result["is_finalized"])

    def test_manifest_exposes_structured_poll_input(self):
        manifest = self.server.current_tool_manifest()
        descriptor = next(
            tool for tool in manifest["tools"] if tool["nativeToolName"] == "send_message"
        )
        poll_schema = next(
            branch
            for branch in descriptor["inputSchema"]["properties"]["poll"]["anyOf"]
            if branch.get("type") == "object"
        )
        self.assertEqual(poll_schema["properties"]["answers"]["minItems"], 2)
        self.assertEqual(poll_schema["properties"]["answers"]["maxItems"], 10)
        self.assertEqual(poll_schema["properties"]["duration_hours"]["maximum"], 768)
        for property_schema in poll_schema["properties"].values():
            self.assertTrue(property_schema["description"])
        self.assertFalse(poll_schema["additionalProperties"])
        output_poll = descriptor["outputSchema"]["oneOf"][0]["properties"]["data"][
            "properties"
        ]["poll"]
        self.assertIn("object", output_poll["type"])


if __name__ == "__main__":
    unittest.main()
