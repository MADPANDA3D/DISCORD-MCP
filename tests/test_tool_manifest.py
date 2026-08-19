import hashlib
import importlib
import json
import os
import sys
import unittest
from pathlib import Path


def import_server():
    os.environ["MCP_MODE"] = "standalone"
    os.environ["MCP_ACCESS_TOKEN"] = "manifest-test-access-" + ("a" * 32)
    os.environ["DISCORD_TOKEN"] = "manifest-test-token"
    os.environ["DISCORD_GUILD_ID"] = str(123_456_789_012_345_678)
    os.environ["DISCORD_ALLOWED_CHANNEL_IDS"] = "ALL"
    src_dir = Path(__file__).resolve().parents[1] / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    return importlib.import_module("madpanda_discord_mcp.server")


class ToolManifestTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = import_server()
        cls.manifest_module = importlib.import_module("madpanda_discord_mcp.tool_manifest")

    def test_manifest_matches_registry_and_has_complete_contract_fields(self):
        manifest = self.server.current_tool_manifest()

        self.assertEqual(manifest["schemaVersion"], "1.0.0")
        self.assertEqual(manifest["serviceId"], "discord")
        self.assertEqual(manifest["catalogVersion"], "discord-2026.08.19.1")
        self.assertEqual(
            manifest["counts"],
            {
                "raw": 52,
                "agentReady": 46,
                "legacy": 3,
                "hidden": 3,
                "documented": 52,
            },
        )
        self.assertEqual(len(self.server.mcp._tool_manager._tools), 52)
        self.assertEqual(
            [tool["nativeToolName"] for tool in manifest["tools"]],
            list(self.manifest_module.TOOL_DEFINITIONS),
        )

    def test_preserved_legacy_contract_and_approved_controls_are_immutable(self):
        manifest = self.server.current_tool_manifest()
        compatibility = []
        controls = []
        for tool in sorted(manifest["tools"], key=lambda item: item["nativeToolName"]):
            compatibility.append(
                {
                    "name": tool["nativeToolName"],
                    "aliases": tool["aliases"],
                    "tier": tool["tier"],
                    "annotations": tool["annotations"],
                    "properties": sorted(tool["inputSchema"].get("properties", {})),
                    "required": sorted(tool["inputSchema"].get("required", [])),
                    "outputs": sorted(
                        tool["outputSchema"]["oneOf"][0]["properties"]["data"]["properties"]
                    ),
                }
            )
            controls.append(
                {
                    "name": tool["nativeToolName"],
                    "access": tool["access"],
                    "confirmation": tool["confirmation"],
                }
            )

        def projection_hash(value):
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            return hashlib.sha256(encoded).hexdigest()

        self.assertEqual(
            projection_hash(compatibility),
            "be7a0b26064d2a8b9ec167532cb1acce29007fe2d10b6f0ab24aa7f119393924",
        )
        self.assertEqual(
            projection_hash(controls),
            "48c0cf795764f4e822d8e54616d2aa2d6e1f53873da016073804575b8c8056a5",
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
            "access",
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
                    {
                        "readOnlyHint",
                        "destructiveHint",
                        "idempotentHint",
                        "openWorldHint",
                    },
                )
                self.assertTrue(
                    all(isinstance(value, bool) for value in descriptor["annotations"].values())
                )
                self.assertEqual(set(descriptor["access"]), {"adminRequired"})
                self.assertIsInstance(descriptor["access"]["adminRequired"], bool)
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

                self.assertNotIn("MAD MCP Portal request", descriptor["description"])
                if not descriptor["annotations"]["openWorldHint"]:
                    self.assertIn("does not contact Discord", descriptor["description"])
                    self.assertNotIn("through Discord's API", descriptor["description"])

        canonical_descriptors = [
            {key: value for key, value in descriptor.items() if key != "descriptorHash"}
            for descriptor in manifest["tools"]
        ]
        self.assertEqual(
            manifest["descriptorHash"],
            self.manifest_module.descriptor_hash(manifest["tools"]),
        )
        for descriptor, canonical in zip(manifest["tools"], canonical_descriptors, strict=True):
            self.assertEqual(
                descriptor["descriptorHash"],
                self.manifest_module.descriptor_hash(canonical),
            )

    def test_aggregate_hash_matches_portal_materialized_wire_contract(self):
        manifest = self.server.current_tool_manifest()
        canonical_without_per_tool_hashes = [
            {key: value for key, value in descriptor.items() if key != "descriptorHash"}
            for descriptor in manifest["tools"]
        ]

        self.assertEqual(
            manifest["descriptorHash"],
            self.manifest_module.descriptor_hash(manifest["tools"]),
        )
        self.assertNotEqual(
            manifest["descriptorHash"],
            self.manifest_module.descriptor_hash(canonical_without_per_tool_hashes),
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
        self.assertNotIn(str(123_456_789_012_345_678), serialized)

    def test_release_identity_values_are_validated_before_public_health_output(self):
        module = self.manifest_module
        digest = "a" * 64

        self.assertEqual(module.get_build_sha("ABCDEF1"), "abcdef1")
        self.assertEqual(module.get_build_sha("secret-build-value"), "unknown")
        self.assertEqual(module.get_source_fingerprint("development"), "development")
        self.assertEqual(module.get_source_fingerprint(digest), digest)
        self.assertEqual(module.get_source_fingerprint("secret-source-value"), "unknown")
        self.assertEqual(
            module.get_image_reference(f"ghcr.io/madpanda3d/discord-mcp-server@sha256:{digest}"),
            f"ghcr.io/madpanda3d/discord-mcp-server@sha256:{digest}",
        )
        self.assertEqual(module.get_image_reference("discord-mcp:latest"), "unknown")
        self.assertEqual(module.get_image_reference("secret-image-value"), "unknown")

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

    async def test_list_capabilities_can_return_lossless_manifest(self):
        manifest = self.server.current_tool_manifest()
        result = await self.server.list_capabilities(include_descriptors=True)

        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["descriptorsIncluded"])
        self.assertEqual(result["data"]["schemaVersion"], "1.0.0")
        self.assertEqual(result["data"]["serviceId"], "discord")
        self.assertEqual(result["data"]["counts"]["raw"], 52)
        self.assertEqual(result["data"]["tools"], manifest["tools"])
        self.assertEqual(
            result["data"]["descriptorHash"],
            manifest["descriptorHash"],
        )
        self.assertLessEqual(
            self.server.serialized_tool_result_size(result),
            self.server.MCP_FULL_CATALOG_OUTPUT_MAX_BYTES,
        )
        self.assertIsNone(result["data"]["nextAction"])

    async def test_find_tools_is_ranked_and_webhook_compatibility_tools_are_hidden(self):
        result = await self.server.find_tools(query="SEND-channel message", limit=8)

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["matches"][0]["toolName"], "send_message")
        names = set(self.server.mcp._tool_manager._tools)
        self.assertIn("send_webhook_message", names)
        self.assertIn("create_webhook", names)
        manifest_tools = {
            tool["nativeToolName"]: tool for tool in self.server.current_tool_manifest()["tools"]
        }
        self.assertEqual(manifest_tools["create_webhook"]["tier"], "hidden")
        self.assertEqual(manifest_tools["send_webhook_message"]["tier"], "hidden")
        list_webhooks = next(
            tool
            for tool in self.server.current_tool_manifest()["tools"]
            if tool["nativeToolName"] == "list_webhooks"
        )
        self.assertEqual(list_webhooks["tier"], "hidden")
        self.assertTrue(list_webhooks["annotations"]["readOnlyHint"])
        self.assertTrue(list_webhooks["confirmation"]["required"])

        alias_result = await self.server.find_tools(query="send dm")
        self.assertEqual(alias_result["data"]["matches"][0]["toolName"], "send_private_message")

        usage = await self.server.get_tool_usage("send_message")
        self.assertEqual(usage["data"]["nextAction"]["type"], "mcp_tool_call")
        self.assertEqual(usage["data"]["nextAction"]["toolName"], "send_message")
        self.assertNotIn("portal", json.dumps(usage["data"]["nextAction"]).lower())

    async def test_check_configuration_reads_presence_without_provider_work_or_secrets(
        self,
    ):
        server = self.server
        original_values = {
            "PUBLIC_MODE": server.PUBLIC_MODE,
            "DISCORD_CREDENTIAL_MODE": server.DISCORD_CREDENTIAL_MODE,
            "ALLOW_REQUEST_OVERRIDES": server.ALLOW_REQUEST_OVERRIDES,
            "MCP_PORTAL_GRANT_TOKEN": server.MCP_PORTAL_GRANT_TOKEN,
            "REQUIRE_REQUEST_DISCORD_TOKEN": server.REQUIRE_REQUEST_DISCORD_TOKEN,
            "REQUIRE_REQUEST_GUILD_ID": server.REQUIRE_REQUEST_GUILD_ID,
            "REQUIRE_REQUEST_ALLOWED_CHANNELS": server.REQUIRE_REQUEST_ALLOWED_CHANNELS,
            "get_http_headers": server.get_http_headers,
        }
        secret = "request-only-bot-secret"
        server.PUBLIC_MODE = True
        server.DISCORD_CREDENTIAL_MODE = "request"
        server.ALLOW_REQUEST_OVERRIDES = True
        server.MCP_PORTAL_GRANT_TOKEN = "configured-but-never-returned"
        server.REQUIRE_REQUEST_DISCORD_TOKEN = True
        server.REQUIRE_REQUEST_GUILD_ID = True
        server.REQUIRE_REQUEST_ALLOWED_CHANNELS = True
        server.get_http_headers = lambda: {
            server.REQUEST_DISCORD_TOKEN_HEADER: secret,
            server.REQUEST_DISCORD_GUILD_ID_HEADER: str(123_456_789_012_345_678),
            server.REQUEST_DISCORD_ALLOWED_CHANNELS_HEADER: str(123_456_789_012_345_678),
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
        self.assertIn("partial", statuses)
        self.assertNotIn("legacy_hidden", statuses)
        self.assertIn("intentionally_not_exposed", statuses)
        self.assertGreaterEqual(result["data"]["count"], 10)


if __name__ == "__main__":
    unittest.main()
