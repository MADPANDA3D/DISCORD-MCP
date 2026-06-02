import base64
import importlib
import os
import sys
import unittest
from pathlib import Path


CHANNEL_ID = "1414768421584764958"


def import_server():
    os.environ.setdefault("DISCORD_TOKEN", "test-token")
    os.environ.setdefault("DISCORD_GUILD_ID", "123456789012345678")
    os.environ.setdefault("DISCORD_ALLOWED_CHANNEL_IDS", CHANNEL_ID)
    os.environ.setdefault("MCP_REQUIRE_CONFIRM", "false")

    fastmcp_dir = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(fastmcp_dir))
    return importlib.import_module("discord_mcp_server")


class FakeGuild:
    id = 123456789012345678


class FakePermissions:
    view_channel = True
    read_message_history = True
    send_messages = True
    embed_links = True
    attach_files = True
    add_reactions = True
    manage_messages = False
    create_public_threads = False
    create_private_threads = False


class FakeSentMessage:
    id = 9001
    jump_url = "https://discord.test/channels/123/456/9001"


class FakeChannel:
    id = int(CHANNEL_ID)
    guild = FakeGuild()

    def __init__(self):
        self.sent = []

    def permissions_for(self, _member):
        return FakePermissions()

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return FakeSentMessage()


class SendMessageAttachmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_message_attaches_base64_pdf(self):
        server = import_server()
        fake_channel = FakeChannel()

        async def fake_get_text_channel(channel_id):
            self.assertEqual(channel_id, int(CHANNEL_ID))
            return fake_channel

        async def fake_get_bot_member(_guild):
            return object()

        server.get_text_channel = fake_get_text_channel
        server.get_bot_member = fake_get_bot_member
        server.log_action = lambda *args, **kwargs: None

        pdf_bytes = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
        result = await server.send_message(
            channel_id=CHANNEL_ID,
            message="Literature PDF attached.",
            file_base64=base64.b64encode(pdf_bytes).decode("ascii"),
            file_name="norton-introduction-to-literature.pdf",
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(len(fake_channel.sent), 1)
        sent_file = fake_channel.sent[0]["file"]
        self.assertEqual(sent_file.filename, "norton-introduction-to-literature.pdf")
        self.assertEqual(
            result["data"]["attachments"],
            [
                {
                    "filename": "norton-introduction-to-literature.pdf",
                    "source": "base64",
                    "size_bytes": len(pdf_bytes),
                }
            ],
        )
        self.assertEqual(result["data"]["diagnostics"]["attachments_count"], 1)


if __name__ == "__main__":
    unittest.main()
