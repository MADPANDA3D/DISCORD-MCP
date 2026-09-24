import importlib
import json
import os
import unittest

from madpanda_discord_mcp.response_bounds import (
    bound_list_response,
    describe_admin_read_schema,
)


class ResponseBoundsTests(unittest.TestCase):
    def test_oversized_member_list_keeps_prefix_and_resume_cursor(self):
        members = [{"user": {"id": str(index)}, "bio": "x" * 1_000} for index in range(100, 200)]

        result = bound_list_response(members, redact=lambda value: value, max_response_bytes=8_000)

        self.assertTrue(result["wire_truncated"])
        self.assertGreater(result["returned_count"], 0)
        self.assertLess(result["returned_count"], 100)
        self.assertEqual(result["next_after"], result["items"][-1]["user"]["id"])
        self.assertLessEqual(
            len(json.dumps(result, separators=(",", ":")).encode("utf-8")),
            8_000,
        )

    def test_list_members_limit_and_pagination_are_documented(self):
        base = {
            "type": "object",
            "properties": {"query": {"type": "object", "description": "Query values."}},
        }

        result = describe_admin_read_schema(
            "discord_server_read", base, lambda _name, schema: dict(schema)
        )

        description = result["properties"]["query"]["description"]
        self.assertIn("between 1 and 100", description)
        self.assertIn("next_after", description)

    def test_entrypoint_refreshes_the_registered_native_schema(self):
        os.environ.setdefault("MCP_MODE", "standalone")
        os.environ.setdefault("MCP_ACCESS_TOKEN", "response-bounds-access-" + ("a" * 32))
        os.environ.setdefault("DISCORD_TOKEN", "response-bounds-token")
        os.environ.setdefault("DISCORD_GUILD_ID", str(123_456_789_012_345_678))
        os.environ.setdefault("DISCORD_ALLOWED_CHANNEL_IDS", "ALL")
        entrypoint = importlib.import_module("madpanda_discord_mcp.entrypoint")

        entrypoint._install_response_bounds()

        schema = entrypoint.server.mcp._tool_manager._tools["discord_server_read"].parameters
        description = schema["properties"]["query"]["description"]
        self.assertIn("between 1 and 100", description)
        self.assertIn("next_after", description)


if __name__ == "__main__":
    unittest.main()
