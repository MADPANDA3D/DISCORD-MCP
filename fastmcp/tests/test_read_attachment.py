import base64
import importlib
import io
import os
import sys
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock


def import_server():
    os.environ["MCP_PUBLIC_MODE"] = "false"
    os.environ["DISCORD_TOKEN"] = "attachment-test-token"
    os.environ["DISCORD_GUILD_ID"] = "123456789012345678"
    fastmcp_dir = Path(__file__).resolve().parents[1]
    if str(fastmcp_dir) not in sys.path:
        sys.path.insert(0, str(fastmcp_dir))
    return importlib.import_module("discord_mcp_server")


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

    async def test_read_attachment_returns_reusable_base64_and_redacts_urls(self):
        server = self.server
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
            id=1542679644640247860,
            guild=SimpleNamespace(id=123456789012345678),
            fetch_message=AsyncMock(return_value=message),
        )
        originals = {
            "get_message_target": server.get_message_target,
            "require_read_allowed": server.require_read_allowed,
            "record_api_success": server.record_api_success,
            "log_action": server.log_action,
        }
        server.get_message_target = AsyncMock(return_value=channel)
        server.require_read_allowed = lambda *args, **kwargs: None
        server.record_api_success = lambda *args, **kwargs: None
        server.log_action = lambda *args, **kwargs: None
        try:
            result = await server.read_attachment(
                channel_id=str(channel.id),
                message_id=str(message.id),
            )
        finally:
            for name, value in originals.items():
                setattr(server, name, value)

        self.assertTrue(result["ok"])
        self.assertEqual(base64.b64decode(result["data"]["content_base64"]), content)
        self.assertEqual(result["data"]["archive"]["entries"][0]["name"], "SKILL.md")
        self.assertNotIn("url", result["data"]["attachment"])
        self.assertNotIn("secret", str(result))


if __name__ == "__main__":
    unittest.main()
