import json
import sys
import unittest
from pathlib import Path


FASTMCP_DIR = Path(__file__).resolve().parents[1]
if str(FASTMCP_DIR) not in sys.path:
    sys.path.insert(0, str(FASTMCP_DIR))

from discord_admin_api import (  # noqa: E402
    DESTRUCTIVE_ACTIONS,
    MAX_RESPONSE_BYTES,
    OPERATIONS,
    READ_ACTIONS,
    WRITE_ACTIONS,
    bound_response,
    build_operation_path,
    execute_operation,
    get_operation,
    validate_payload,
    validate_query,
)


SNOWFLAKE = "123456789012345678"


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status = status
        self.body = body
        self.headers = {"Content-Type": "application/json", **(headers or {})}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self, content_type=None):
        return self.body

    async def text(self):
        return json.dumps(self.body)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class DiscordAdminApiTests(unittest.IsolatedAsyncioTestCase):
    def test_registry_is_unique_risk_partitioned_and_explicit(self):
        self.assertEqual(len(OPERATIONS), len(set(OPERATIONS)))
        self.assertEqual(set(OPERATIONS), set(READ_ACTIONS) | set(WRITE_ACTIONS) | set(DESTRUCTIVE_ACTIONS))
        self.assertFalse(set(READ_ACTIONS) & set(WRITE_ACTIONS))
        self.assertFalse(set(READ_ACTIONS) & set(DESTRUCTIVE_ACTIONS))
        self.assertFalse(set(WRITE_ACTIONS) & set(DESTRUCTIVE_ACTIONS))
        self.assertIn("get_effective_channel_permissions", READ_ACTIONS)
        self.assertIn("start_forum_thread", WRITE_ACTIONS)
        self.assertIn("bulk_ban", DESTRUCTIVE_ACTIONS)
        for operation in OPERATIONS.values():
            with self.subTest(action=operation.action):
                self.assertIn(operation.method, {"GET", "POST", "PUT", "PATCH", "DELETE"})
                self.assertTrue(operation.path.startswith("/"))
                self.assertNotIn("..", operation.path)
                self.assertTrue(set(operation.required_body_fields) <= set(operation.body_fields))
                if operation.risk == "read":
                    self.assertEqual(operation.method, "GET")
                elif operation.risk == "write":
                    self.assertIn(operation.method, {"POST", "PUT"})

    def test_risk_mismatch_and_unknown_action_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "Unsupported write action"):
            get_operation("get_guild", "write")
        with self.assertRaisesRegex(ValueError, "Unsupported destructive action"):
            get_operation("arbitrary_http", "destructive")

    def test_identifier_binding_validates_and_encodes(self):
        operation = OPERATIONS["get_message"]
        path = build_operation_path(
            operation,
            {"channel_id": SNOWFLAKE, "message_id": "987654321098765432"},
        )
        self.assertEqual(path, "/channels/123456789012345678/messages/987654321098765432")
        with self.assertRaisesRegex(ValueError, "channel_id must be a Discord snowflake"):
            build_operation_path(operation, {"channel_id": "../../etc", "message_id": SNOWFLAKE})

    def test_query_and_payload_are_bounded_and_allowlisted(self):
        list_members = OPERATIONS["list_members"]
        self.assertEqual(validate_query(list_members, {"limit": 100}), {"limit": 100})
        with self.assertRaisesRegex(ValueError, "between 1 and 100"):
            validate_query(list_members, {"limit": 101})
        with self.assertRaisesRegex(ValueError, "Unsupported query fields"):
            validate_query(list_members, {"token": "secret"})
        with self.assertRaisesRegex(ValueError, "bot, member, or role"):
            validate_query(OPERATIONS["get_effective_channel_permissions"], {"target_type": "everyone"})
        with self.assertRaisesRegex(ValueError, "between 1 and 25"):
            validate_query(OPERATIONS["search_guild_messages"], {"limit": 26})
        with self.assertRaisesRegex(ValueError, "between 1 and 50"):
            validate_query(OPERATIONS["get_channel_pins"], {"limit": 51})

        create_role = OPERATIONS["create_role"]
        self.assertEqual(validate_payload(create_role, {"name": "operator"}), {"name": "operator"})
        with self.assertRaisesRegex(ValueError, "Unsupported payload fields"):
            validate_payload(create_role, {"name": "operator", "token": "secret"})
        with self.assertRaisesRegex(ValueError, "1 to 200"):
            validate_payload(OPERATIONS["bulk_ban"], {"user_ids": []})
        with self.assertRaisesRegex(ValueError, "2 to 100"):
            validate_payload(OPERATIONS["bulk_delete_messages"], {"messages": [SNOWFLAKE]})
        self.assertEqual(validate_payload(OPERATIONS["create_channel_invite"], None), {})
        with self.assertRaisesRegex(ValueError, "missing required fields: name"):
            validate_payload(OPERATIONS["create_role"], {"mentionable": True})

    def test_response_redacts_credentials_and_stays_within_wire_budget(self):
        response = bound_response(
            {
                "token": "never-return-this",
                "url": "https://cdn.discordapp.com/icons/safe.png",
                "webhook_url": "https://discord.com/api/webhooks/123456789012345678/secret-token",
                "items": [{"value": "x" * 5000} for _ in range(200)],
            }
        )
        serialized = json.dumps(response, separators=(",", ":"), ensure_ascii=False)
        self.assertNotIn("never-return-this", serialized)
        self.assertNotIn("secret-token", serialized)
        self.assertIn("https://cdn.discordapp.com/icons/safe.png", serialized)
        self.assertLessEqual(len(serialized.encode("utf-8")), MAX_RESPONSE_BYTES)

    async def test_execute_read_retries_one_bounded_rate_limit_and_returns_safe_shape(self):
        session = FakeSession(
            [
                FakeResponse(429, {"message": "slow down", "retry_after": 0.001}),
                FakeResponse(200, {"id": SNOWFLAKE, "token": "provider-secret"}),
            ]
        )
        result = await execute_operation(
            OPERATIONS["get_guild"],
            token="request-token",
            identifiers={"guild_id": SNOWFLAKE},
            session=session,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 200)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(result["data"]["token"], "[REDACTED]")
        self.assertEqual(session.calls[0][2]["headers"]["Authorization"], "Bot request-token")

    async def test_write_does_not_retry_and_rejects_unsupported_audit_reason(self):
        operation = OPERATIONS["trigger_typing"]
        session = FakeSession([FakeResponse(429, {"message": "slow down", "retry_after": 0.001})])
        result = await execute_operation(
            operation,
            token="request-token",
            identifiers={"channel_id": SNOWFLAKE},
            session=session,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(len(session.calls), 1)
        with self.assertRaisesRegex(ValueError, "does not support an audit-log reason"):
            await execute_operation(
                operation,
                token="request-token",
                identifiers={"channel_id": SNOWFLAKE},
                reason="unsupported",
                session=FakeSession([]),
            )


if __name__ == "__main__":
    unittest.main()
