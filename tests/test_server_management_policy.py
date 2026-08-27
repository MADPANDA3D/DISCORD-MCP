import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch


def import_server():
    os.environ.setdefault("MCP_MODE", "standalone")
    os.environ.setdefault("MCP_ACCESS_TOKEN", "server-management-policy-" + ("a" * 32))
    os.environ.setdefault("DISCORD_TOKEN", "synthetic-policy-token")
    os.environ.setdefault("DISCORD_GUILD_ID", str(123_456_789_012_345_678))
    os.environ.setdefault("DISCORD_ALLOWED_CHANNEL_IDS", "ALL")
    return importlib.import_module("madpanda_discord_mcp.server")


class FakeRole:
    def __init__(self, role_id: int, position: int):
        self.id = role_id
        self.position = position

    def __ge__(self, other):
        return self.position >= other.position


class ServerManagementPolicyTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = import_server()
        cls.registry = importlib.import_module("madpanda_discord_mcp.discord_admin_api")

    def test_actual_query_payload_and_position_channel_ids_are_discovered(self):
        decoy = "123456789012345701"
        actual = "123456789012345702"
        nested = "123456789012345703"

        search = self.registry.get_operation("search_guild_messages", "read")
        self.assertEqual(
            self.server._admin_referenced_channel_ids(
                search,
                {"channel_id": decoy},
                {"channel_id": [actual]},
                None,
            ),
            {int(decoy), int(actual)},
        )

        stage = self.registry.get_operation("create_stage_instance", "write")
        self.assertEqual(
            self.server._admin_referenced_channel_ids(
                stage,
                {"channel_id": ""},
                None,
                {"channel_id": actual},
            ),
            {int(actual)},
        )

        onboarding = self.registry.get_operation("modify_onboarding", "destructive")
        self.assertEqual(
            self.server._admin_referenced_channel_ids(
                onboarding,
                {"channel_id": ""},
                None,
                {"prompts": [{"options": [{"channel_ids": [nested]}]}]},
            ),
            {int(nested)},
        )

        positions = self.registry.get_operation("modify_channel_positions", "destructive")
        self.assertEqual(
            self.server._admin_referenced_channel_ids(
                positions,
                {"channel_id": ""},
                None,
                {"positions": [{"id": actual, "position": 1}]},
            ),
            {int(actual)},
        )

    def test_guild_wide_channel_results_are_filtered_to_read_scope(self):
        allowed = "123456789012345701"
        blocked = "123456789012345702"
        with patch.object(
            self.server, "is_read_allowed", side_effect=lambda value: value == int(allowed)
        ):
            channels = self.server._admin_filter_channel_scoped_result(
                "list_guild_channels",
                [{"id": allowed}, {"id": blocked}],
            )
            threads = self.server._admin_filter_channel_scoped_result(
                "list_active_threads",
                {
                    "threads": [
                        {"id": "123456789012345711", "parent_id": allowed},
                        {"id": "123456789012345712", "parent_id": blocked},
                    ],
                    "members": [
                        {"id": "123456789012345711"},
                        {"id": "123456789012345712"},
                    ],
                },
            )
            webhooks = self.server._admin_filter_channel_scoped_result(
                "list_guild_webhooks",
                [{"id": "1", "channel_id": allowed}, {"id": "2", "channel_id": blocked}],
            )

        self.assertEqual(channels, [{"id": allowed}])
        self.assertEqual([item["id"] for item in threads["threads"]], ["123456789012345711"])
        self.assertEqual([item["id"] for item in threads["members"]], ["123456789012345711"])
        self.assertEqual(webhooks, [{"id": "1", "channel_id": allowed}])

    def test_delete_overwrite_and_role_positions_guard_the_actual_roles(self):
        target = 123456789012345721
        position_role = 123456789012345722
        guild = SimpleNamespace(get_role=lambda role_id: FakeRole(role_id, 1))

        delete_overwrite = self.registry.get_operation("delete_channel_permission", "destructive")
        self.assertEqual(
            self.server._admin_role_ids_for_guard(
                delete_overwrite,
                {"role_id": "", "target_id": str(target)},
                None,
                guild,
            ),
            {target},
        )

        reorder = self.registry.get_operation("modify_role_positions", "destructive")
        self.assertTrue(reorder.role_guard)
        self.assertEqual(
            self.server._admin_role_ids_for_guard(
                reorder,
                {"role_id": "", "target_id": ""},
                {"positions": [{"id": str(position_role), "position": 2}]},
                guild,
            ),
            {position_role},
        )

    async def test_query_channel_decoy_cannot_bypass_policy(self):
        decoy = "123456789012345701"
        blocked = "123456789012345702"
        execute = AsyncMock()
        with (
            patch.object(
                self.server,
                "is_read_allowed",
                side_effect=lambda value: value == int(decoy),
            ),
            patch.object(self.server, "execute_admin_operation", execute),
        ):
            result = await self.server._run_server_management_action(
                "read",
                "search_guild_messages",
                guild_id="123456789012345678",
                channel_id=decoy,
                query={"channel_id": [blocked]},
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "permission_denied")
        execute.assert_not_awaited()

    async def test_successful_read_records_audit_trail_without_log_failure(self):
        execute = AsyncMock(
            return_value={
                "ok": True,
                "status": 200,
                "resource": "guild",
                "data": {"id": "123456789012345678"},
                "rate_limit": {"known": True},
            }
        )
        with (
            patch.object(self.server, "execute_admin_operation", execute),
            patch.object(self.server, "log_action") as log_action,
        ):
            result = await self.server._run_server_management_action(
                "read",
                "get_guild",
            )

        self.assertTrue(result["ok"])
        audit_trail_id = result["meta"]["audit_trail_id"]
        log_action.assert_called_once_with(
            "get_guild",
            ANY,
            "ok",
            guild_id=None,
            channel_id=None,
            audit_trail_id=audit_trail_id,
        )

    async def test_modify_member_roles_rejects_protected_payload_role(self):
        user_id = 123456789012345731
        protected_role_id = 123456789012345732
        bot_role = FakeRole(123456789012345733, 100)
        protected_role = FakeRole(protected_role_id, 10)
        bot_member = SimpleNamespace(
            id=123456789012345734,
            top_role=bot_role,
            guild_permissions=SimpleNamespace(manage_roles=True),
        )
        member = SimpleNamespace(id=user_id, roles=[])
        guild = SimpleNamespace(
            id=123456789012345678,
            owner_id=123456789012345799,
            get_role=lambda role_id: protected_role if role_id == protected_role_id else None,
        )
        execute = AsyncMock()
        with (
            patch.object(self.server, "get_active_admin_tools_enabled", return_value=True),
            patch.object(self.server, "get_guild", AsyncMock(return_value=guild)),
            patch.object(self.server, "get_bot_member", AsyncMock(return_value=bot_member)),
            patch.object(
                self.server,
                "get_member_or_error",
                AsyncMock(return_value=(member, None)),
            ),
            patch.object(self.server, "ensure_member_guardrails", return_value=None),
            patch.object(
                self.server,
                "ensure_bot_can_moderate",
                AsyncMock(return_value=(bot_member, None)),
            ),
            patch.object(self.server, "PROTECTED_ROLE_IDS", {protected_role_id}),
            patch.object(self.server, "execute_admin_operation", execute),
        ):
            result = await self.server._run_server_management_action(
                "destructive",
                "modify_member",
                guild_id=str(guild.id),
                user_id=str(user_id),
                role_id="123456789012345735",
                payload={"roles": [str(protected_role_id)]},
                confirm="CONFIRM APPLY",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "permission_denied")
        execute.assert_not_awaited()

    async def test_restricted_bulk_ban_rejects_non_member_ids(self):
        external_user_id = 123456789012345741
        allowed_role_id = 123456789012345742
        bot_member = SimpleNamespace(
            id=123456789012345743,
            top_role=FakeRole(123456789012345744, 100),
            guild_permissions=SimpleNamespace(ban_members=True),
        )
        guild = SimpleNamespace(
            id=123456789012345678,
            owner_id=123456789012345799,
        )
        execute = AsyncMock()
        with (
            patch.object(self.server, "get_active_admin_tools_enabled", return_value=True),
            patch.object(self.server, "get_guild", AsyncMock(return_value=guild)),
            patch.object(self.server, "get_bot_member", AsyncMock(return_value=bot_member)),
            patch.object(self.server, "fetch_member_optional", AsyncMock(return_value=None)),
            patch.object(self.server, "ALLOWED_TARGET_ROLE_IDS", {allowed_role_id}),
            patch.object(self.server, "execute_admin_operation", execute),
        ):
            result = await self.server._run_server_management_action(
                "destructive",
                "bulk_ban",
                guild_id=str(guild.id),
                payload={"user_ids": [str(external_user_id)]},
                confirm="CONFIRM APPLY",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "permission_denied")
        execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
