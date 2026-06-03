import base64
import importlib
import os
import sys
import unittest
from pathlib import Path


CHANNEL_ID = "1414768421584764958"


def import_server():
    os.environ["DISCORD_TOKEN"] = "test-token"
    os.environ["DISCORD_GUILD_ID"] = "123456789012345678"
    os.environ["DISCORD_ALLOWED_CHANNEL_IDS"] = CHANNEL_ID
    os.environ["MCP_REQUIRE_CONFIRM"] = "false"

    fastmcp_dir = Path(__file__).resolve().parents[1]
    if str(fastmcp_dir) not in sys.path:
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
    def test_build_attachment_request_accepts_file_url(self):
        server = import_server()

        request = server.build_attachment_request(
            None,
            None,
            "",
            "https://example.com/textbook.pdf",
            "",
            "",
            "",
        )

        self.assertEqual(request.source, "url")
        self.assertEqual(request.value, "https://example.com/textbook.pdf")
        self.assertEqual(request.filename, "textbook.pdf")
        self.assertEqual(request.content_type, "application/pdf")

    def test_build_attachment_request_accepts_file_path(self):
        server = import_server()

        request = server.build_attachment_request(
            None,
            None,
            "/safe/textbook.pdf",
            "",
            "",
            "",
            "",
        )

        self.assertEqual(request.source, "path")
        self.assertEqual(request.value, "/safe/textbook.pdf")
        self.assertEqual(request.filename, "textbook.pdf")
        self.assertEqual(request.content_type, "application/pdf")

    def test_build_attachment_request_accepts_file_object(self):
        server = import_server()

        request = server.build_attachment_request(
            {"base64": "JVBERi0xLjcK", "filename": "textbook.pdf"},
            None,
            "",
            "",
            "",
            "",
            "",
        )

        self.assertEqual(request.source, "base64")
        self.assertEqual(request.value, "JVBERi0xLjcK")
        self.assertEqual(request.filename, "textbook.pdf")
        self.assertEqual(request.content_type, "application/pdf")

    def test_build_attachment_request_rejects_multiple_sources(self):
        server = import_server()

        with self.assertRaisesRegex(ValueError, "Provide only one attachment source"):
            server.build_attachment_request(
                None,
                None,
                "/safe/textbook.pdf",
                "https://example.com/textbook.pdf",
                "",
                "",
                "",
            )

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
