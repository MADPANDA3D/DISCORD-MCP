import importlib
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

GUILD_ID = 123_456_789_012_345_678
CHANNEL_ID = 123_456_789_012_345_679


def import_server():
    os.environ["MCP_MODE"] = "standalone"
    os.environ["MCP_ACCESS_TOKEN"] = "result-safety-access-" + ("a" * 32)
    os.environ["DISCORD_TOKEN"] = "result-safety-discord-" + ("b" * 32)
    os.environ["DISCORD_GUILD_ID"] = str(GUILD_ID)
    os.environ["DISCORD_ALLOWED_CHANNEL_IDS"] = str(CHANNEL_ID)

    src_dir = Path(__file__).resolve().parents[1] / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    return importlib.import_module("madpanda_discord_mcp.server")


class ResultSafetyTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = import_server()

    async def asyncSetUp(self):
        self.server.JOB_STORE.clear()
        self.server.JOB_TASKS.clear()
        self.server.AUDIT_JOB_STORE.clear()

    async def asyncTearDown(self):
        tasks = list(self.server.JOB_TASKS.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await self.server.asyncio.gather(*tasks, return_exceptions=True)
        self.server.JOB_STORE.clear()
        self.server.JOB_TASKS.clear()
        self.server.AUDIT_JOB_STORE.clear()

    def test_finalizer_counts_compact_utf8_bytes_and_fails_closed(self):
        payload = {"text": "é" * 20}
        exact_size = len(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
        self.assertEqual(self.server.serialized_tool_result_size(payload), exact_size)

        with patch.object(self.server, "MCP_TOOL_OUTPUT_MAX_BYTES", exact_size):
            self.assertEqual(self.server.finalize_tool_result(payload), payload)
        with patch.object(self.server, "MCP_TOOL_OUTPUT_MAX_BYTES", exact_size - 1):
            rejected = self.server.finalize_tool_result(payload)
        self.assertEqual(rejected["error"]["type"], "resource_exhausted")
        self.assertNotIn("é", json.dumps(rejected))
        self.assertEqual(rejected["meta"]["duration_ms"], 0)
        self.assertEqual(rejected["meta"]["rate_limit"], {"known": False})
        self.assertEqual(rejected["meta"]["warnings"], [])

        nonserializable = self.server.finalize_tool_result({"bad": object()})
        self.assertEqual(nonserializable["error"]["type"], "invalid_payload")

    def test_output_scrubber_removes_credentials_without_redacting_content(self):
        secret = "fake-active-secret-value"
        payload = {
            "content": "ordinary requested message content",
            "prompt_tokens": 7,
            "discordBotTokenConfigured": True,
            "authorization": f"Bearer {secret}",
            "provider_token": "unknown-provider-secret",
            "provider_credentials": "unknown-provider-credentials",
            "cookies": "unknown-cookie",
            "private_key": "unknown-private-key",
            "webhook_url": "https://discord.com/api/webhooks/1/credential",
            "provider_note": f"provider echoed {secret}",
        }
        with patch.object(self.server, "active_secret_values", return_value=(secret,)):
            safe = self.server.finalize_tool_result(payload)

        self.assertEqual(safe["content"], payload["content"])
        self.assertEqual(safe["prompt_tokens"], 7)
        self.assertTrue(safe["discordBotTokenConfigured"])
        self.assertEqual(safe["authorization"], "[REDACTED]")
        for key in (
            "provider_token",
            "provider_credentials",
            "cookies",
            "private_key",
            "webhook_url",
        ):
            self.assertEqual(safe[key], "[REDACTED]")
        self.assertNotIn(secret, json.dumps(safe))

    def test_output_and_state_scrubbers_remove_secret_mapping_keys(self):
        secret = "mapping-key-secret-value"
        webhook_url = (
            "https://discord.com/api/webhooks/123456789012345678/mapping-key-webhook-credential"
        )
        payload = {
            secret: "secret-key-value",
            webhook_url: "webhook-key-value",
            "ordinary": "preserved",
        }
        with patch.object(self.server, "active_secret_values", return_value=(secret,)):
            output_safe = self.server.finalize_tool_result(payload)
            state_safe = self.server.state_safe_payload(payload)

        for safe_payload in (output_safe, state_safe):
            serialized = json.dumps(safe_payload)
            self.assertNotIn(secret, serialized)
            self.assertNotIn("mapping-key-webhook-credential", serialized)
            self.assertEqual(safe_payload["ordinary"], "preserved")
            self.assertTrue(any(str(key).startswith("[REDACTED_KEY_") for key in safe_payload))

    async def test_provider_and_navigation_wrappers_share_schema_valid_boundary(self):
        self.assertTrue(self.server.is_navigation_tool("check_configuration"))
        self.assertFalse(self.server.is_navigation_tool("discord_job_status"))
        with patch.object(self.server, "MCP_TOOL_OUTPUT_MAX_BYTES", 100):
            navigation = await self.server.check_configuration()
            provider = await self.server.discord_job_status("")

        self.assertFalse(navigation["ok"])
        self.assertFalse(provider["ok"])
        for result in (navigation, provider):
            self.assertEqual(result["error"]["type"], "resource_exhausted")
            self.assertEqual(result["meta"]["duration_ms"], 0)
            self.assertIn("rate_limit", result["meta"])
            self.assertIn("warnings", result["meta"])

    async def test_generic_jobs_do_not_retain_oversized_results(self):
        job_id = "oversized-job"
        now = time.time()
        self.server.JOB_STORE[job_id] = {
            "task_id": job_id,
            "action": "fake_action",
            "status": "queued",
            "created_at": self.server.job_timestamp(),
            "created_at_ts": now,
            "_last_used_at_ts": now,
            "result": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
            "finished_at_ts": None,
        }

        async def oversized_action():
            return {"ok": True, "data": {"payload": "x" * 2_000}}

        with patch.object(self.server, "retained_job_output_max_bytes", return_value=512):
            await self.server.run_job(job_id, "fake_action", oversized_action, {})

        job = self.server.JOB_STORE[job_id]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["type"], "resource_exhausted")
        self.assertEqual(job["result"]["error"]["type"], "resource_exhausted")
        self.assertNotIn("x" * 100, json.dumps(job))

    async def test_generic_jobs_normalize_unexpected_exception_text(self):
        job_id = "oversized-exception-job"
        now = time.time()
        self.server.JOB_STORE[job_id] = {
            "task_id": job_id,
            "action": "fake_action",
            "status": "queued",
            "created_at": self.server.job_timestamp(),
            "created_at_ts": now,
            "_last_used_at_ts": now,
            "result": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
            "finished_at_ts": None,
        }

        async def failing_action():
            raise ValueError("exception-sentinel-" + ("z" * 2_000))

        await self.server.run_job(job_id, "fake_action", failing_action, {})

        retained = json.dumps(self.server.JOB_STORE[job_id])
        self.assertEqual(self.server.JOB_STORE[job_id]["status"], "failed")
        self.assertEqual(
            self.server.JOB_STORE[job_id]["error"]["type"],
            "internal_error",
        )
        self.assertIsNone(self.server.JOB_STORE[job_id]["result"])
        self.assertNotIn("exception-sentinel", retained)
        self.assertNotIn("z" * 100, retained)

    async def test_near_limit_failed_job_result_remains_retrievable_once(self):
        job_id = "near-limit-failed-job"
        now = time.time()
        self.server.JOB_STORE[job_id] = {
            "task_id": job_id,
            "action": "fake_action",
            "status": "queued",
            "created_at": self.server.job_timestamp(),
            "created_at_ts": now,
            "_last_used_at_ts": now,
            "_owner_fingerprint": self.server.current_tenant_fingerprint(),
            "result": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
            "finished_at_ts": None,
        }
        large_message = "provider-failure-" + ("q" * 42_000)

        async def failed_action():
            return {
                "ok": False,
                "error": {"type": "invalid_payload", "message": large_message},
                "meta": {
                    "duration_ms": 1,
                    "rate_limit": {"known": False},
                    "warnings": [],
                },
            }

        await self.server.run_job(job_id, "fake_action", failed_action, {})
        status = await self.server.discord_job_status(job_id, include_result=True)

        self.assertTrue(status["ok"])
        self.assertEqual(status["data"]["status"], "failed")
        self.assertIsNone(status["data"]["error"])
        self.assertEqual(
            status["data"]["result"]["error"]["message"],
            large_message,
        )
        self.assertLessEqual(
            self.server.serialized_tool_result_size(status),
            self.server.MCP_TOOL_OUTPUT_MAX_BYTES,
        )

    def test_audit_job_aggregate_is_bounded_before_mutation(self):
        job = {"results": {"first": {"payload": "a" * 120}}}
        original = json.loads(json.dumps(job["results"]))
        with patch.object(self.server, "retained_job_output_max_bytes", return_value=200):
            error = self.server.append_bounded_audit_job_result(
                job,
                CHANNEL_ID,
                {"payload": "b" * 120},
            )

        self.assertIsNotNone(error)
        self.assertEqual(error["error"]["type"], "resource_exhausted")
        self.assertEqual(job["results"], original)

    async def test_full_catalog_uses_only_its_bounded_portal_allowance(self):
        manifest = self.server.current_tool_manifest()
        result = await self.server.list_capabilities(include_descriptors=True)
        result_size = self.server.serialized_tool_result_size(result)

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["tools"], manifest["tools"])
        self.assertGreater(result_size, self.server.MCP_TOOL_OUTPUT_MAX_BYTES)
        self.assertLessEqual(
            result_size,
            self.server.MCP_FULL_CATALOG_OUTPUT_MAX_BYTES,
        )


if __name__ == "__main__":
    unittest.main()
