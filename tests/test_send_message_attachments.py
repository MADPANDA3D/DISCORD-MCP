import asyncio
import base64
import importlib
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

CHANNEL_ID = str(123_456_789_012_345_679)


def import_server():
    os.environ["MCP_MODE"] = "standalone"
    os.environ["MCP_ACCESS_TOKEN"] = "attachment-test-access-" + ("a" * 32)
    os.environ["DISCORD_TOKEN"] = "test-token"
    os.environ["DISCORD_GUILD_ID"] = str(123_456_789_012_345_678)
    os.environ["DISCORD_ALLOWED_CHANNEL_IDS"] = CHANNEL_ID
    os.environ["MCP_REQUIRE_CONFIRM"] = "false"

    src_dir = Path(__file__).resolve().parents[1] / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    return importlib.import_module("madpanda_discord_mcp.server")


class FakeGuild:
    id = 123_456_789_012_345_678


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


class FakeResponseContent:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.iterated = False

    async def iter_chunked(self, _size):
        self.iterated = True
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, status=200, headers=None, chunks=()):
        self.status = status
        self.headers = dict(headers or {})
        self.content = FakeResponseContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeSession:
    def __init__(self, response, capture, *, get_error=None):
        self.response = response
        self.capture = capture
        self.get_error = get_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def get(self, url, **kwargs):
        self.capture["get"] = {"url": url, **kwargs}
        if self.get_error is not None:
            raise self.get_error
        return self.response


class SendMessageAttachmentTests(unittest.IsolatedAsyncioTestCase):
    def test_build_attachment_request_accepts_file_url(self):
        server = import_server()

        request = server.build_attachment_request(
            None,
            None,
            "",
            "https://cdn.discordapp.com/textbook.pdf",
            "",
            "",
            "",
        )

        self.assertEqual(request.source, "url")
        self.assertEqual(request.value, "https://cdn.discordapp.com/textbook.pdf")
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
                "https://cdn.discordapp.com/textbook.pdf",
                "",
                "",
                "",
            )

    def test_url_policy_rejects_unsafe_authority_and_special_hosts(self):
        server = import_server()
        invalid_urls = (
            "http://cdn.discordapp.com/file.pdf",
            "https://user:password@cdn.discordapp.com/file.pdf",
            "https://cdn.discordapp.com:8443/file.pdf",
            "https://cdn.discordapp.com/file.pdf#fragment",
            "https://cdn.discordapp.com/file.pdf#",
            "https://cdn.example.com/file.pdf",
            "https://cdn.example.net/file.pdf",
            "https://cdn.example.org/file.pdf",
            "https://127.0.0.1/file.pdf",
            "https://[::1]/file.pdf",
            "https://8.8/file.pdf",
            "https://010.010/file.pdf",
            "https://0x08080808.1/file.pdf",
            "https://metadata.google.internal/file.pdf",
            "https://host.local/file.pdf",
            "https://localhost/file.pdf",
            "https://single-label/file.pdf",
            "https://cdn.discordapp.com\\@127.0.0.1/file.pdf",
        )
        for url in invalid_urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                server.validate_attachment_url(url)

        self.assertEqual(
            server.validate_attachment_url(
                "https://cdn.discordapp.com:443/file.pdf?signature=opaque"
            ),
            ("cdn.discordapp.com", 443),
        )

    async def test_dns_policy_requires_every_address_to_be_global(self):
        server = import_server()
        loop = asyncio.get_running_loop()

        def record(address, family=socket.AF_INET):
            sockaddr = (address, 443) if family == socket.AF_INET else (address, 443, 0, 0)
            return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)

        unsafe_answers = (
            [record("10.0.0.1")],
            [record("100.64.0.1")],
            [record("::1", socket.AF_INET6)],
            [record("93.184.216.34"), record("192.168.1.10")],
        )
        for answers in unsafe_answers:
            with (
                self.subTest(answers=answers),
                patch.object(loop, "getaddrinfo", AsyncMock(return_value=answers)),
                self.assertRaises(ValueError),
            ):
                await server.resolve_public_attachment_addresses("cdn.discordapp.com", 443)

        with (
            patch.object(loop, "getaddrinfo", AsyncMock(return_value=[])),
            self.assertRaises(server.ProviderUnavailableError) as unavailable,
        ):
            await server.resolve_public_attachment_addresses("cdn.discordapp.com", 443)
        self.assertEqual(
            server.exception_to_error(unavailable.exception)["type"],
            "provider_unavailable",
        )

        with (
            patch.object(
                loop,
                "getaddrinfo",
                AsyncMock(side_effect=socket.gaierror("private-dns-detail")),
            ),
            self.assertRaises(server.ProviderUnavailableError) as dns_failure,
        ):
            await server.resolve_public_attachment_addresses("cdn.discordapp.com", 443)
        normalized_dns = server.exception_to_error(dns_failure.exception)
        self.assertEqual(normalized_dns["type"], "provider_unavailable")
        self.assertNotIn("private-dns-detail", str(normalized_dns))

        public_answers = [
            record("93.184.216.34"),
            record("2606:2800:220:1:248:1893:25c8:1946", socket.AF_INET6),
        ]
        with patch.object(loop, "getaddrinfo", AsyncMock(return_value=public_answers)):
            resolved = await server.resolve_public_attachment_addresses("cdn.discordapp.com", 443)

        self.assertEqual(
            {(item.host, item.family) for item in resolved},
            {
                ("93.184.216.34", socket.AF_INET),
                ("2606:2800:220:1:248:1893:25c8:1946", socket.AF_INET6),
            },
        )

    async def test_pinned_resolver_refuses_any_rebound_hostname(self):
        server = import_server()
        resolver = server.PinnedAttachmentResolver(
            "cdn.discordapp.com",
            (server.ResolvedAttachmentAddress(host="93.184.216.34", family=socket.AF_INET),),
        )

        resolved = await resolver.resolve("cdn.discordapp.com", 443, family=socket.AF_UNSPEC)
        self.assertEqual(resolved[0]["host"], "93.184.216.34")
        with self.assertRaises(OSError):
            await resolver.resolve("media.discordapp.net", 443)

        connector = server.aiohttp.TCPConnector(
            resolver=resolver,
            family=socket.AF_UNSPEC,
            force_close=True,
        )
        try:
            self.assertIs(connector._resolver, resolver)
        finally:
            await connector.close()

    async def test_url_fetch_pins_dns_disables_redirects_and_streams_with_a_cap(self):
        server = import_server()
        request = server.AttachmentRequest(
            source="url",
            value="https://cdn.discordapp.com/textbook.pdf?signature=opaque",
            filename="textbook.pdf",
            content_type="application/pdf",
        )
        addresses = (server.ResolvedAttachmentAddress(host="93.184.216.34", family=socket.AF_INET),)
        response = FakeResponse(headers={"Content-Length": "7"}, chunks=(b"abc", b"defg"))
        capture = {}
        connector = object()

        def session_factory(**kwargs):
            capture["session"] = kwargs
            return FakeSession(response, capture)

        with (
            patch.object(
                server,
                "resolve_public_attachment_addresses",
                AsyncMock(return_value=addresses),
            ) as resolve_mock,
            patch.object(server.aiohttp, "TCPConnector", return_value=connector) as connector_mock,
            patch.object(server.aiohttp, "ClientSession", side_effect=session_factory),
        ):
            data = await server.fetch_url_attachment(request)

        self.assertEqual(data, b"abcdefg")
        resolve_mock.assert_awaited_once_with("cdn.discordapp.com", 443)
        resolver = connector_mock.call_args.kwargs["resolver"]
        self.assertIsInstance(resolver, server.PinnedAttachmentResolver)
        self.assertEqual(resolver.addresses, addresses)
        self.assertIs(capture["session"]["connector"], connector)
        self.assertFalse(capture["session"]["trust_env"])
        self.assertFalse(capture["get"]["allow_redirects"])

    async def test_url_fetch_rejects_redirects_and_oversized_bodies(self):
        server = import_server()
        request = server.AttachmentRequest(
            source="url",
            value="https://cdn.discordapp.com/file.bin",
            filename="file.bin",
        )
        addresses = (server.ResolvedAttachmentAddress(host="93.184.216.34", family=socket.AF_INET),)

        async def run_fetch(response, *, max_bytes=4):
            capture = {}

            def session_factory(**_kwargs):
                return FakeSession(response, capture)

            with (
                patch.object(
                    server,
                    "resolve_public_attachment_addresses",
                    AsyncMock(return_value=addresses),
                ),
                patch.object(server.aiohttp, "TCPConnector", return_value=object()),
                patch.object(server.aiohttp, "ClientSession", side_effect=session_factory),
                patch.object(server, "DISCORD_ATTACHMENT_MAX_BYTES", max_bytes),
                patch.object(server, "DISCORD_ATTACHMENT_MAX_MB", 1),
            ):
                return await server.fetch_url_attachment(request)

        redirect = FakeResponse(
            status=302,
            headers={"Location": "https://127.0.0.1/private"},
            chunks=(b"redirect",),
        )
        with self.assertRaises(server.ProviderUnavailableError) as redirect_error:
            await run_fetch(redirect)
        self.assertFalse(redirect.content.iterated)
        self.assertEqual(
            server.exception_to_error(redirect_error.exception)["type"],
            "provider_unavailable",
        )

        declared_oversize = FakeResponse(headers={"Content-Length": "5"}, chunks=(b"12345",))
        with self.assertRaisesRegex(ValueError, "exceeds"):
            await run_fetch(declared_oversize)
        self.assertFalse(declared_oversize.content.iterated)

        streamed_oversize = FakeResponse(chunks=(b"123", b"45"))
        with self.assertRaisesRegex(ValueError, "exceeds"):
            await run_fetch(streamed_oversize)

    async def test_url_fetch_normalizes_timeout_without_echoing_the_url(self):
        server = import_server()
        request = server.AttachmentRequest(
            source="url",
            value="https://cdn.discordapp.com/private-name.pdf?secret=sensitive",
            filename="private-name.pdf",
        )
        addresses = (server.ResolvedAttachmentAddress(host="93.184.216.34", family=socket.AF_INET),)
        capture = {}

        def session_factory(**_kwargs):
            return FakeSession(FakeResponse(), capture, get_error=asyncio.TimeoutError())

        with (
            patch.object(
                server,
                "resolve_public_attachment_addresses",
                AsyncMock(return_value=addresses),
            ),
            patch.object(server.aiohttp, "TCPConnector", return_value=object()),
            patch.object(server.aiohttp, "ClientSession", side_effect=session_factory),
        ):
            with self.assertRaises(server.ProviderTimeoutError) as raised:
                await server.fetch_url_attachment(request)

        self.assertEqual(
            server.exception_to_error(raised.exception)["type"],
            "timeout",
        )
        self.assertNotIn("sensitive", str(raised.exception))

    async def test_url_fetch_timeout_also_bounds_dns_resolution(self):
        server = import_server()
        request = server.AttachmentRequest(
            source="url",
            value="https://cdn.discordapp.com/file.pdf",
            filename="file.pdf",
        )
        loop = asyncio.get_running_loop()

        async def never_resolve(*_args, **_kwargs):
            await asyncio.Event().wait()

        with (
            patch.object(loop, "getaddrinfo", AsyncMock(side_effect=never_resolve)),
            patch.object(server, "DISCORD_ATTACHMENT_URL_TIMEOUT_SECONDS", 0.01),
        ):
            with self.assertRaises(server.ProviderTimeoutError):
                await asyncio.wait_for(server.fetch_url_attachment(request), timeout=0.5)

    def test_local_paths_are_operator_only_and_remain_beneath_allowed_dirs(self):
        server = import_server()
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            allowed = root / "allowed"
            allowed.mkdir()
            local_file = allowed / "document.pdf"
            local_file.write_bytes(b"safe")
            outside_file = root / "outside.pdf"
            outside_file.write_bytes(b"outside")
            (allowed / "escape.pdf").symlink_to(outside_file)

            common = (patch.object(server, "MCP_ATTACHMENT_ALLOWED_DIRS", (allowed,)),)
            with common[0], patch.object(server, "PUBLIC_MODE", True):
                with self.assertRaisesRegex(ValueError, "standalone"):
                    server.resolve_local_attachment_path(str(local_file))

            with (
                patch.object(server, "MCP_ATTACHMENT_ALLOWED_DIRS", (allowed,)),
                patch.object(server, "PUBLIC_MODE", False),
                patch.object(server, "DISCORD_CREDENTIAL_MODE", "request"),
            ):
                with self.assertRaisesRegex(ValueError, "server-credential"):
                    server.resolve_local_attachment_path(str(local_file))

            with (
                patch.object(server, "MCP_ATTACHMENT_ALLOWED_DIRS", (allowed,)),
                patch.object(server, "PUBLIC_MODE", False),
                patch.object(server, "DISCORD_CREDENTIAL_MODE", "server"),
            ):
                self.assertEqual(
                    server.resolve_local_attachment_path(str(local_file)),
                    local_file.resolve(),
                )
                with self.assertRaisesRegex(ValueError, "outside"):
                    server.resolve_local_attachment_path(str(allowed / "escape.pdf"))

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
