import importlib
import os
import sys
import unittest
from pathlib import Path


def import_server():
    os.environ["MCP_MODE"] = "standalone"
    os.environ["MCP_ACCESS_TOKEN"] = "search-contract-access-" + ("a" * 32)
    os.environ["DISCORD_TOKEN"] = "search-contract-token"
    os.environ["DISCORD_GUILD_ID"] = str(123_456_789_012_345_678)
    os.environ["DISCORD_ALLOWED_CHANNEL_IDS"] = "ALL"
    src_dir = Path(__file__).resolve().parents[1] / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    return importlib.import_module("madpanda_discord_mcp.server")


class SearchMessagesContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = import_server()

    def test_search_messages_requires_a_nonblank_channel_id(self):
        schema = self.server.mcp._tool_manager._tools["search_messages"].parameters

        self.assertIn("channel_id", schema["required"])
        self.assertEqual(schema["properties"]["channel_id"]["minLength"], 1)
        self.assertEqual(schema["properties"]["channel_id"]["pattern"], r".*\S.*")

        descriptor = next(
            tool
            for tool in self.server.current_tool_manifest()["tools"]
            if tool["nativeToolName"] == "search_messages"
        )
        self.assertEqual(descriptor["inputSchema"], schema)


if __name__ == "__main__":
    unittest.main()
