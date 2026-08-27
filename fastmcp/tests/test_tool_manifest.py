import importlib
import json
import os
import sys
import unittest
from pathlib import Path


def import_server():
    os.environ["MCP_PUBLIC_MODE"] = "false"
    os.environ["DISCORD_TOKEN"] = "manifest-test-token"
    os.environ["DISCORD_GUILD_ID"] = "123456789012345678"
    fastmcp_dir = Path(__file__).resolve().parents[1]
    if str(fastmcp_dir) not in sys.path:
        sys.path.insert(0, str(fastmcp_dir))
    return importlib.import_module("discord_mcp_server")


class ToolManifestTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = import_server()
        cls.manifest_module = importlib.import_module("tool_manifest")

    def test_manifest_matches_registry_and_has_complete_contract_fields(self):
        manifest = self.server.current_tool_manifest()

        self.assertEqual(manifest["schemaVersion"], "1.0.0")
        self.assertEqual(manifest["serviceId"], "discord")
        self.assertEqual(manifest["counts"], {
            "raw": 55,
            "agentReady": 49,
            "legacy": 3,
            "hidden": 3,
            "documented": 55,
        })
        self.assertEqual(len(self.server.mcp._tool_manager._tools), 55)
        self.assertEqual(
            [tool["nativeToolName"] for tool in manifest["tools"]],
            list(self.manifest_module.TOOL_DEFINITIONS),
        )

        required = {
            "serviceId",
            "nativeToolName",
            "canonicalName",
            "aliases",
            "title",
            "description",
            "category",
            "deprecation",
            "inputSchema",
            "outputSchema",
            "annotations",
            "confirmation",
            "documentationUrl",
            "navigationRole",
            "catalogVersion",
            "tier",
            "descriptorHash",
        }
        for descriptor in manifest["tools"]:
            with self.subTest(tool=descriptor["nativeToolName"]):
                self.assertEqual(set(descriptor), required)
                self.assertEqual(
                    descriptor["canonicalName"],
                    f"discord.{descriptor['nativeToolName']}",
                )
                self.assertEqual(
                    list(descriptor["deprecation"]),
                    ["deprecated", "since", "replacement", "sunsetAt", "message"],
                )
                self.assertGreater(len(descriptor["description"]), 120)
                self.assertIn(descriptor["tier"], {"agent_ready", "legacy", "hidden"})
                self.assertTrue(descriptor["documentationUrl"].startswith("https://"))
                self.assertEqual(len(descriptor["descriptorHash"]), 64)
                self.assertEqual(
                    set(descriptor["annotations"]),
                    {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"},
                )
                self.assertTrue(all(isinstance(value, bool) for value in descriptor["annotations"].values()))
                self.assertEqual(
                    set(descriptor["confirmation"]),
                    {"required", "parameter", "exactPhrase", "when"},
                )
                self.assertTrue(descriptor["inputSchema"].get("description"))
                self.assertTrue(descriptor["outputSchema"].get("oneOf"))
                self.assertTrue(
                    descriptor["outputSchema"]["oneOf"][0]["properties"]["data"]["properties"]
                )
                for parameter in descriptor["inputSchema"].get("properties", {}).values():
                    self.assertTrue(parameter.get("description"))

        canonical_descriptors = [
            {
                key: value
                for key, value in descriptor.items()
                if key != "descriptorHash"
            }
            for descriptor in manifest["tools"]
        ]
        self.assertEqual(
            manifest["descriptorHash"],
            self.manifest_module.descriptor_hash(manifest["tools"]),
        )
        for descriptor, canonical in zip(
            manifest["tools"], canonical_descriptors, strict=True
        ):
            self.assertEqual(
                descriptor["descriptorHash"],
                self.manifest_module.descriptor_hash(canonical),
            )

    def test_aggregate_hash_matches_portal_materialized_wire_contract(self):
        manifest = self.server.current_tool_manifest()
        canonical_without_per_tool_hashes = [
            {
                key: value
                for key, value in descriptor.items()
                if key != "descriptorHash"
            }
            for descriptor in manifest["tools"]
        ]

        self.assertEqual(
            manifest["descriptorHash"],
            self.manifest_module.descriptor_hash(manifest["tools"]),
        )
        self.assertNotEqual(
            manifest["descriptorHash"],
            self.manifest_module.descriptor_hash(
                canonical_without_per_tool_hashes
            ),
            "Portal hashes materialized descriptors including each per-tool descriptorHash.",
        )

    def test_descriptor_hash_is_stable_across_builds_and_excludes_runtime_values(self):
        manager = self.server.mcp._tool_manager
        first = self.manifest_module.build_tool_manifest(
            manager, build_sha="1111111111111111111111111111111111111111"
        )
        second = self.manifest_module.build_tool_manifest(
            manager, build_sha="2222222222222222222222222222222222222222"
        )

        self.assertNotEqual(first["buildSha"], second["buildSha"])
        self.assertEqual(first["descriptorHash"], second["descriptorHash"])
        self.assertEqual(
            [tool["descriptorHash"] for tool in first["tools"]],
            [tool["descriptorHash"] for tool in second["tools"]],
        )
        serialized = json.dumps(first)
        self.assertNotIn("manifest-test-token", serialized)
        self.assertNotIn("123456789012345678", serialized)

    def test_native_descriptors_share_manifest_metadata(self):
        manifest = self.server.current_tool_manifest()
        by_name = {tool["nativeToolName"]: tool for tool in manifest["tools"]}

        for name, runtime_tool in self.server.mcp._tool_manager._tools.items():
            with self.subTest(tool=name):
                descriptor = by_name[name]
                self.assertEqual(runtime_tool.title, descriptor["title"])
                self.assertEqual(runtime_tool.description, descriptor["description"])
                self.assertEqual(
                    runtime_tool.annotations.model_dump(exclude_none=True),
                    descriptor["annotations"],
                )
                self.assertEqual(
                    runtime_tool.meta["madpanda"]["tier"],
                    descriptor["tier"],
                )
                for parameter in runtime_tool.parameters.get("properties", {}).values():
                    self.assertTrue(parameter.get("description"))

        for name in (
            "check_configuration",
            "list_capabilities",
            "get_endpoint_coverage",
            "get_tool_usage",
            "find_tools",
        ):
            self.assertIsNotNone(self.server.mcp._tool_manager._tools[name].output_schema)

    def test_server_management_action_enums_match_the_reviewed_registry(self):
        manifest = self.server.current_tool_manifest()
        by_name = {tool["nativeToolName"]: tool for tool in manifest["tools"]}
        expected = {
            "discord_server_read": self.manifest_module.READ_ACTIONS,
            "discord_server_write": self.manifest_module.WRITE_ACTIONS,
            "discord_server_destructive": self.manifest_module.DESTRUCTIVE_ACTIONS,
        }
        for tool_name, actions in expected.items():
            with self.subTest(tool=tool_name):
                self.assertEqual(
                    by_name[tool_name]["inputSchema"]["properties"]["action"]["enum"],
                    list(actions),
                )
                self.assertGreater(len(actions), 10)

    async def test_list_capabilities_can_return_lossless_manifest(self):
        result = await self.server.list_capabilities(include_descriptors=True)

        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["descriptorsIncluded"])
        self.assertEqual(result["data"]["schemaVersion"], "1.0.0")
        self.assertEqual(result["data"]["serviceId"], "discord")
        self.assertEqual(result["data"]["counts"]["raw"], 55)
        self.assertEqual(len(result["data"]["tools"]), 55)
        self.assertEqual(
            result["data"]["descriptorHash"],
            self.server.current_tool_manifest()["descriptorHash"],
        )

    async def test_find_tools_is_ranked_multitoken_and_hides_credential_tools(self):
        result = await self.server.find_tools(query="SEND-channel message", limit=8)

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["matches"][0]["toolName"], "send_message")
        self.assertNotIn(
            "send_webhook_message",
            [match["toolName"] for match in result["data"]["matches"]],
        )

        alias_result = await self.server.find_tools(query="send dm")
        self.assertEqual(alias_result["data"]["matches"][0]["toolName"], "send_private_message")

    async def test_check_configuration_reads_presence_without_provider_work_or_secrets(self):
        server = self.server
        original_values = {
            "PUBLIC_MODE": server.PUBLIC_MODE,
            "MCP_PORTAL_GRANT_TOKEN": server.MCP_PORTAL_GRANT_TOKEN,
            "REQUIRE_REQUEST_DISCORD_TOKEN": server.REQUIRE_REQUEST_DISCORD_TOKEN,
            "REQUIRE_REQUEST_GUILD_ID": server.REQUIRE_REQUEST_GUILD_ID,
            "get_http_headers": server.get_http_headers,
        }
        secret = "request-only-bot-secret"
        server.PUBLIC_MODE = True
        server.MCP_PORTAL_GRANT_TOKEN = "configured-but-never-returned"
        server.REQUIRE_REQUEST_DISCORD_TOKEN = True
        server.REQUIRE_REQUEST_GUILD_ID = True
        server.get_http_headers = lambda: {
            server.REQUEST_DISCORD_TOKEN_HEADER: secret,
            server.REQUEST_DISCORD_GUILD_ID_HEADER: "123456789012345678",
        }
        try:
            result = await server.check_configuration()
        finally:
            for key, value in original_values.items():
                setattr(server, key, value)

        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["ready"])
        self.assertTrue(result["data"]["configuration"]["discordBotTokenConfigured"])
        serialized = json.dumps(result)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("configured-but-never-returned", serialized)

    async def test_endpoint_coverage_reports_implemented_and_excluded_areas(self):
        result = await self.server.get_endpoint_coverage()
        statuses = {item["status"] for item in result["data"]["coverage"]}

        self.assertTrue(result["ok"])
        self.assertIn("covered", statuses)
        self.assertIn("covered_with_documented_exclusion", statuses)
        self.assertIn("technically_inapplicable", statuses)
        self.assertGreaterEqual(result["data"]["count"], 10)


if __name__ == "__main__":
    unittest.main()
