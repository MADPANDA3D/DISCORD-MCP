import importlib
import os
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

GUILD_ID = str(123_456_789_012_345_678)
CHANNEL_A = 123_456_789_012_345_679
CHANNEL_B = 123_456_789_012_345_680
CHANNEL_C = 123_456_789_012_345_681
REQUEST_TOKEN = "request-policy-test-token-" + ("x" * 32)


def import_server():
    os.environ["MCP_MODE"] = "standalone"
    os.environ["MCP_ACCESS_TOKEN"] = "policy-test-access-" + ("a" * 32)
    os.environ["DISCORD_TOKEN"] = "policy-test-discord-token"
    os.environ["DISCORD_GUILD_ID"] = GUILD_ID
    os.environ["DISCORD_ALLOWED_CHANNEL_IDS"] = str(CHANNEL_A)
    src_dir = Path(__file__).resolve().parents[1] / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    return importlib.import_module("madpanda_discord_mcp.server")


class RuntimePolicyTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = import_server()

    def policy_globals(self, **overrides):
        defaults = {
            "DISCORD_CREDENTIAL_MODE": "request",
            "ALLOW_ALL_CHANNELS": False,
            "ALLOWED_CHANNEL_IDS": {CHANNEL_A, CHANNEL_B},
            "BLOCKED_CHANNEL_IDS": set(),
            "PRIMARY_CHANNEL_ID": None,
            "DISCORD_ALLOW_ALL_READ": False,
            "DISCORD_DM_ENABLED": False,
            "MCP_ADMIN_TOOLS_ENABLED": False,
            "CONFIRM_REQUIRED": True,
        }
        defaults.update(overrides)
        stack = ExitStack()
        for name, value in defaults.items():
            stack.enter_context(patch.object(self.server, name, value))
        return stack

    def test_empty_server_allowlist_denies_reads_and_writes(self):
        with self.policy_globals(ALLOWED_CHANNEL_IDS=set()):
            token = self.server.REQUEST_OVERRIDE_CONTEXT.set(
                {"allow_all_channels": True, "allowed_channel_ids": set()}
            )
            try:
                self.assertFalse(self.server.is_write_allowed(CHANNEL_A))
                self.assertFalse(self.server.is_read_allowed(CHANNEL_A))
            finally:
                self.server.REQUEST_OVERRIDE_CONTEXT.reset(token)

    def test_request_scope_intersects_server_ceiling_and_blocklist(self):
        cases = (
            (False, {CHANNEL_A, CHANNEL_B}, False, {CHANNEL_B, CHANNEL_C}, {CHANNEL_B}),
            (True, set(), False, {CHANNEL_B}, {CHANNEL_B}),
            (False, {CHANNEL_A, CHANNEL_B}, True, set(), {CHANNEL_A, CHANNEL_B}),
        )
        for server_all, server_ids, request_all, request_ids, expected in cases:
            with (
                self.subTest(expected=expected),
                self.policy_globals(
                    ALLOW_ALL_CHANNELS=server_all,
                    ALLOWED_CHANNEL_IDS=server_ids,
                ),
            ):
                token = self.server.REQUEST_OVERRIDE_CONTEXT.set(
                    {
                        "allow_all_channels": request_all,
                        "allowed_channel_ids": request_ids,
                    }
                )
                try:
                    actual = {
                        channel_id
                        for channel_id in (CHANNEL_A, CHANNEL_B, CHANNEL_C)
                        if self.server.is_write_allowed(channel_id)
                    }
                    self.assertEqual(actual, expected)
                finally:
                    self.server.REQUEST_OVERRIDE_CONTEXT.reset(token)

        with self.policy_globals(BLOCKED_CHANNEL_IDS={CHANNEL_B}):
            token = self.server.REQUEST_OVERRIDE_CONTEXT.set(
                {
                    "allow_all_channels": False,
                    "allowed_channel_ids": {CHANNEL_B},
                }
            )
            try:
                self.assertFalse(self.server.is_write_allowed(CHANNEL_B))
                self.assertFalse(self.server.is_read_allowed(CHANNEL_B))
            finally:
                self.server.REQUEST_OVERRIDE_CONTEXT.reset(token)

    def test_primary_channel_is_a_default_not_authority(self):
        with self.policy_globals(ALLOWED_CHANNEL_IDS={CHANNEL_A}, PRIMARY_CHANNEL_ID=CHANNEL_C):
            token = self.server.REQUEST_OVERRIDE_CONTEXT.set(
                {
                    "allow_all_channels": True,
                    "allowed_channel_ids": set(),
                }
            )
            try:
                self.assertEqual(self.server.resolve_channel_id(""), CHANNEL_C)
                self.assertFalse(self.server.is_write_allowed(CHANNEL_C))
            finally:
                self.server.REQUEST_OVERRIDE_CONTEXT.reset(token)

    def test_request_flags_are_explicit_and_cannot_widen_server_policy(self):
        with self.policy_globals(
            DISCORD_ALLOW_ALL_READ=True,
            DISCORD_DM_ENABLED=True,
            MCP_ADMIN_TOOLS_ENABLED=True,
            CONFIRM_REQUIRED=True,
        ):
            token = self.server.REQUEST_OVERRIDE_CONTEXT.set({})
            try:
                self.assertFalse(self.server.get_active_allow_all_read())
                self.assertFalse(self.server.get_active_dm_enabled())
                self.assertFalse(self.server.get_active_admin_tools_enabled())
                self.assertTrue(self.server.get_active_confirm_required())
            finally:
                self.server.REQUEST_OVERRIDE_CONTEXT.reset(token)

            token = self.server.REQUEST_OVERRIDE_CONTEXT.set(
                {
                    "allow_all_read": True,
                    "dm_enabled": True,
                    "admin_tools_enabled": True,
                    "confirm_required": False,
                }
            )
            try:
                self.assertTrue(self.server.get_active_allow_all_read())
                self.assertTrue(self.server.get_active_dm_enabled())
                self.assertTrue(self.server.get_active_admin_tools_enabled())
                self.assertTrue(self.server.get_active_confirm_required())
            finally:
                self.server.REQUEST_OVERRIDE_CONTEXT.reset(token)

        with self.policy_globals(CONFIRM_REQUIRED=False):
            token = self.server.REQUEST_OVERRIDE_CONTEXT.set(
                {
                    "allow_all_read": True,
                    "dm_enabled": True,
                    "admin_tools_enabled": True,
                    "confirm_required": True,
                }
            )
            try:
                self.assertFalse(self.server.get_active_allow_all_read())
                self.assertFalse(self.server.get_active_dm_enabled())
                self.assertFalse(self.server.get_active_admin_tools_enabled())
                self.assertTrue(self.server.get_active_confirm_required())
            finally:
                self.server.REQUEST_OVERRIDE_CONTEXT.reset(token)

    async def test_request_mode_requires_strict_provider_and_channel_headers(self):
        required_globals = {
            "ALLOW_REQUEST_OVERRIDES": True,
            "REQUIRE_REQUEST_DISCORD_TOKEN": True,
            "REQUIRE_REQUEST_GUILD_ID": True,
            "REQUIRE_REQUEST_ALLOWED_CHANNELS": True,
        }
        with ExitStack() as stack:
            for name, value in required_globals.items():
                stack.enter_context(patch.object(self.server, name, value))
            stack.enter_context(
                patch.object(
                    self.server,
                    "get_http_headers",
                    return_value={
                        self.server.REQUEST_DISCORD_TOKEN_HEADER: REQUEST_TOKEN,
                        self.server.REQUEST_DISCORD_GUILD_ID_HEADER: GUILD_ID,
                    },
                )
            )
            with self.assertRaises(self.server.HeaderAuthError):
                await self.server.build_request_overrides()

        with ExitStack() as stack:
            for name, value in required_globals.items():
                stack.enter_context(patch.object(self.server, name, value))
            stack.enter_context(
                patch.object(
                    self.server,
                    "get_http_headers",
                    return_value={
                        self.server.REQUEST_DISCORD_TOKEN_HEADER: REQUEST_TOKEN,
                        self.server.REQUEST_DISCORD_GUILD_ID_HEADER: GUILD_ID,
                        self.server.REQUEST_DISCORD_ALLOWED_CHANNELS_HEADER: "ALL,123",
                    },
                )
            )
            with self.assertRaises(ValueError):
                await self.server.build_request_overrides()

    async def test_manifest_confirmation_and_admin_gates_run_before_provider(self):
        provider = AsyncMock(side_effect=AssertionError("provider must not run"))
        with (
            self.policy_globals(
                DISCORD_CREDENTIAL_MODE="server",
                ALLOWED_CHANNEL_IDS={CHANNEL_A},
                CONFIRM_REQUIRED=True,
            ),
            patch.object(self.server, "ALLOW_REQUEST_OVERRIDES", False),
            patch.object(self.server, "get_text_channel", provider),
        ):
            result = await self.server.send_message(
                channel_id=str(CHANNEL_A), message="confirmation required"
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["type"], "permission_denied")
            provider.assert_not_awaited()

            admin_result = await self.server.timeout_member(
                user_id=str(CHANNEL_B),
                duration_minutes="5",
                confirm=self.server.CONFIRM_APPLY_VALUE,
            )
            self.assertFalse(admin_result["ok"])
            self.assertIn("admin policy", admin_result["error"]["message"])
            provider.assert_not_awaited()

    def test_manifest_declares_the_exact_admin_inventory(self):
        expected = {
            "discord_smoke_test",
            "edit_message",
            "delete_message",
            "timeout_member",
            "remove_timeout",
            "kick_member",
            "ban_member",
            "unban_member",
            "add_role",
            "remove_role",
            "edit_nickname",
            "create_text_channel",
            "delete_channel",
            "create_category",
            "delete_category",
            "delete_webhook",
            "list_webhooks",
        }
        actual = {
            tool["nativeToolName"]
            for tool in self.server.current_tool_manifest()["tools"]
            if tool["access"]["adminRequired"]
        }
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
