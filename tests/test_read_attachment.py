import base64
import importlib
import io
import os
import sys
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

GUILD_ID = 123_456_789_012_345_678
CHANNEL_ID = 123_456_789_012_345_679


def import_server():
    os.environ["MCP_MODE"] = "standalone"
    os.environ["MCP_ACCESS_TOKEN"] = "read-attachment-access-" + ("a" * 32)
    os.environ["DISCORD_TOKEN"] = "read-attachment-discord-" + ("b" * 32)
    os.environ["DISCORD_GUILD_ID"] = str(GUILD_ID)
    os.environ["DISCORD_ALLOWED_CHANNEL_IDS"] = str(CHANNEL_ID)
    src_dir = Path(__file__).resolve().parents[1] / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    return importlib.import_module("madpanda_discord_mcp.server")


def zip_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SKILL.md", "# Memory Skill\nPreserve this.")
        archive.writestr("image.bin", b"\x00\x01")
    return buffer.getvalue()


class ReadAttachmentTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = import_server()

    def test_zip_inspection_extracts_text_without_writing(self):
        result = self.server.inspect_zip_attachment(zip_bytes())

        self.assertEqual(result["format"], "zip")
        self.assertEqual(result["entry_count"], 2)
        self.assertEqual(result["entries"][0]["name"], "SKILL.md")
        self.assertIn("Preserve this", result["entries"][0]["text"])
        self.assertNotIn("text", result["entries"][1])

    async def test_read_attachment_returns_reusable_base64_without_cdn_urls(self):
        content = zip_bytes()
        attachment = SimpleNamespace(
            filename="MemPalace-Memory-Skill.zip",
            content_type="application/zip",
            size=len(content),
            width=None,
            height=None,
            url="https://cdn.discord.example/secret",
            proxy_url="https://proxy.discord.example/secret",
            read=AsyncMock(return_value=content),
        )
        message = SimpleNamespace(id=1543824221258645584, attachments=[attachment])
        channel = SimpleNamespace(
            id=CHANNEL_ID,
            guild=SimpleNamespace(id=GUILD_ID),
            fetch_message=AsyncMock(return_value=message),
        )
        with (
            patch.object(self.server, "ALLOW_REQUEST_OVERRIDES", False),
            patch.object(self.server, "get_message_target", AsyncMock(return_value=channel)),
            patch.object(self.server, "require_read_allowed", return_value=None),
            patch.object(self.server, "record_api_success"),
            patch.object(self.server, "log_action"),
        ):
            result = await self.server.read_attachment(
                channel_id=str(channel.id),
                message_id=str(message.id),
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(base64.b64decode(result["data"]["content_base64"]), content)
        self.assertEqual(result["data"]["archive"]["entries"][0]["name"], "SKILL.md")
        self.assertNotIn("url", result["data"]["attachment"])
        self.assertNotIn("secret", str(result))

    async def test_read_attachment_chunks_content_to_output_ceiling(self):
        content = b"x" * 20_000
        attachment = SimpleNamespace(
            filename="large.bin",
            content_type="application/octet-stream",
            size=len(content),
            width=None,
            height=None,
            read=AsyncMock(return_value=content),
        )
        message = SimpleNamespace(id=1543824221258645584, attachments=[attachment])
        channel = SimpleNamespace(
            id=CHANNEL_ID,
            guild=SimpleNamespace(id=GUILD_ID),
            fetch_message=AsyncMock(return_value=message),
        )
        with (
            patch.object(self.server, "MCP_TOOL_OUTPUT_MAX_BYTES", 4_096),
            patch.object(self.server, "ALLOW_REQUEST_OVERRIDES", False),
            patch.object(self.server, "get_message_target", AsyncMock(return_value=channel)),
            patch.object(self.server, "require_read_allowed", return_value=None),
            patch.object(self.server, "record_api_success"),
            patch.object(self.server, "log_action"),
        ):
            first = await self.server.read_attachment(
                channel_id=str(channel.id), message_id=str(message.id)
            )
            second = await self.server.read_attachment(
                channel_id=str(channel.id),
                message_id=str(message.id),
                byte_offset=str(first["data"]["next_byte_offset"]),
            )

        self.assertTrue(first["data"]["content_truncated"])
        self.assertEqual(second["data"]["content_offset"], first["data"]["next_byte_offset"])
        self.assertLessEqual(self.server.serialized_tool_result_size(first), 4_096)


if __name__ == "__main__":
    unittest.main()
