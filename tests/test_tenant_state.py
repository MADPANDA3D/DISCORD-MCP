import asyncio
import importlib
import json
import os
import sys
import time
import unittest
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

GUILD_ID = 123_456_789_012_345_678
CHANNEL_A = 123_456_789_012_345_679
CHANNEL_B = 123_456_789_012_345_680
TOKEN_A = "tenant-state-token-a-" + ("a" * 32)
TOKEN_B = "tenant-state-token-b-" + ("b" * 32)


def import_server():
    os.environ["MCP_MODE"] = "standalone"
    os.environ["MCP_ACCESS_TOKEN"] = "tenant-state-access-" + ("x" * 32)
    os.environ["DISCORD_TOKEN"] = "tenant-state-server-token"
    os.environ["DISCORD_GUILD_ID"] = str(GUILD_ID)
    os.environ["DISCORD_ALLOWED_CHANNEL_IDS"] = str(CHANNEL_A)
    src_dir = Path(__file__).resolve().parents[1] / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    return importlib.import_module("madpanda_discord_mcp.server")


class FakeBot:
    def __init__(self):
        self.closed = False

    def is_closed(self):
        return self.closed

    async def close(self):
        self.closed = True


class FakeChannel:
    def __init__(self, channel_id: int, name: str):
        self.id = channel_id
        self.name = name


class FakeGuild:
    def __init__(self):
        self.id = GUILD_ID
        self.channels = []
        self.fetch_count = 0

    async def fetch_channels(self):
        self.fetch_count += 1
        return [FakeChannel(CHANNEL_A, f"tenant-{self.fetch_count}")]


class TenantStateTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = import_server()

    async def asyncSetUp(self):
        self.server.JOB_STORE.clear()
        self.server.JOB_TASKS.clear()
        self.server.AUDIT_JOB_STORE.clear()
        self.server.BOT_POOL.clear()
        self.server.CHANNEL_CACHE.clear()

    async def asyncTearDown(self):
        tasks = list(self.server.JOB_TASKS.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.server.JOB_STORE.clear()
        self.server.JOB_TASKS.clear()
        self.server.AUDIT_JOB_STORE.clear()
        self.server.BOT_POOL.clear()
        self.server.CHANNEL_CACHE.clear()

    def runtime_policy(self, **overrides):
        defaults = {
            "ALLOW_REQUEST_OVERRIDES": False,
            "DISCORD_CREDENTIAL_MODE": "request",
            "ALLOW_ALL_CHANNELS": False,
            "ALLOWED_CHANNEL_IDS": {CHANNEL_A},
            "BLOCKED_CHANNEL_IDS": set(),
            "DISCORD_ALLOW_ALL_READ": False,
        }
        defaults.update(overrides)
        stack = ExitStack()
        for name, value in defaults.items():
            stack.enter_context(patch.object(self.server, name, value))
        return stack

    @contextmanager
    def tenant(self, token: str, *, allowed_channels=None):
        context_token = self.server.REQUEST_OVERRIDE_CONTEXT.set(
            {
                "token": token,
                "guild_id": GUILD_ID,
                "allow_all_channels": False,
                "allowed_channel_ids": set(allowed_channels or {CHANNEL_A}),
            }
        )
        try:
            yield
        finally:
            self.server.REQUEST_OVERRIDE_CONTEXT.reset(context_token)

    def job_record(self, owner_fingerprint: str) -> dict:
        now = time.time()
        return {
            "task_id": "job-a",
            "action": "discord_health_check",
            "status": "succeeded",
            "created_at": self.server.job_timestamp(),
            "created_at_ts": now,
            "_last_used_at_ts": now,
            "_owner_fingerprint": owner_fingerprint,
            "started_at": self.server.job_timestamp(),
            "started_at_ts": now,
            "finished_at": self.server.job_timestamp(),
            "finished_at_ts": now,
            "result": {"ok": True},
            "error": None,
        }

    def audit_job_record(self, owner_fingerprint: str) -> dict:
        now = time.time()
        return {
            "task_id": "audit-a",
            "status": "queued",
            "created_at": self.server.job_timestamp(),
            "created_at_ts": now,
            "_last_used_at_ts": now,
            "_owner_fingerprint": owner_fingerprint,
            "finished_at": None,
            "finished_at_ts": None,
            "date": "",
            "timezone": "UTC",
            "total_channels": 1,
            "remaining_channel_ids": [CHANNEL_A],
            "processed_channel_ids": [],
            "results": {},
            "error": None,
        }

    async def test_bot_pool_uses_fingerprints_and_enforces_capacity(self):
        with (
            patch.object(self.server, "create_bot", side_effect=FakeBot),
            patch.object(self.server, "BOT_POOL_MAX_ENTRIES", 1),
        ):
            state = await self.server.get_bot_state(TOKEN_A)

            self.assertNotIn(TOKEN_A, self.server.BOT_POOL)
            self.assertNotIn(TOKEN_A, repr(self.server.BOT_POOL))
            self.assertNotIn(TOKEN_A, repr(vars(state)))
            self.assertFalse(hasattr(state, "token"))
            self.assertEqual(
                list(self.server.BOT_POOL),
                [self.server.credential_fingerprint(TOKEN_A)],
            )

            with self.assertRaisesRegex(RuntimeError, "capacity reached"):
                await self.server.get_bot_state(TOKEN_B)

            await self.server.close_bot_state(state)

    async def test_generic_job_status_is_indistinguishably_tenant_bound(self):
        with self.runtime_policy():
            with self.tenant(TOKEN_A):
                owner = self.server.current_tenant_fingerprint()
                self.server.JOB_STORE["job-a"] = self.job_record(owner)
                same_owner = await self.server.discord_job_status("job-a", include_result=True)

            with self.tenant(TOKEN_B):
                foreign = await self.server.discord_job_status("job-a")
                missing = await self.server.discord_job_status("missing")

        self.assertTrue(same_owner["ok"])
        self.assertNotIn("_owner_fingerprint", json.dumps(same_owner))
        self.assertFalse(foreign["ok"])
        self.assertEqual(foreign["error"]["type"], "not_found")
        self.assertEqual(foreign["error"]["message"], missing["error"]["message"])

    async def test_audit_status_and_cursor_are_tenant_bound_and_state_safe(self):
        webhook_secret = "discord-webhook-secret-value"
        webhook_url = "https://discord.com/api/webhooks/123456789012345678/" + webhook_secret
        with self.runtime_policy():
            with self.tenant(TOKEN_A):
                owner = self.server.current_tenant_fingerprint()
                self.server.AUDIT_JOB_STORE["audit-a"] = self.audit_job_record(owner)

            provider = AsyncMock(
                return_value={
                    "ok": True,
                    "data": {
                        "authorization": f"Bearer {TOKEN_A}",
                        "note": f"provider returned {TOKEN_A}",
                        "webhook": webhook_url,
                    },
                }
            )
            with patch.object(self.server, "channel_daily_audit", provider):
                with self.tenant(TOKEN_B):
                    foreign_status = await self.server.daily_audit_job_status(
                        "audit-a", include_results=True
                    )
                    foreign_next = await self.server.daily_audit_job_next("audit-a")

                self.assertEqual(
                    self.server.AUDIT_JOB_STORE["audit-a"]["remaining_channel_ids"],
                    [CHANNEL_A],
                )
                provider.assert_not_awaited()

                with self.tenant(TOKEN_A):
                    owner_next = await self.server.daily_audit_job_next("audit-a")

        self.assertEqual(foreign_status["error"]["type"], "not_found")
        self.assertEqual(foreign_next["error"]["type"], "not_found")
        provider.assert_awaited_once()
        self.assertTrue(owner_next["ok"])
        self.assertNotIn("_owner_fingerprint", json.dumps(owner_next["data"]["job"]))
        retained = json.dumps(self.server.AUDIT_JOB_STORE["audit-a"])
        self.assertNotIn(TOKEN_A, retained)
        self.assertNotIn(webhook_secret, retained)
        self.assertIn("REDACTED", retained)

    async def test_audit_cursor_is_single_flight_and_cancel_safe(self):
        started = asyncio.Event()
        release = asyncio.Event()
        call_count = 0

        async def slow_audit(**_params):
            nonlocal call_count
            call_count += 1
            started.set()
            await release.wait()
            return {"ok": True, "data": {"call": call_count}}

        with self.runtime_policy(ALLOWED_CHANNEL_IDS={CHANNEL_A, CHANNEL_B}):
            with self.tenant(TOKEN_A, allowed_channels={CHANNEL_A, CHANNEL_B}):
                owner = self.server.current_tenant_fingerprint()
                job = self.audit_job_record(owner)
                job["total_channels"] = 2
                job["remaining_channel_ids"] = [CHANNEL_A, CHANNEL_B]
                self.server.AUDIT_JOB_STORE["audit-a"] = job

                with patch.object(self.server, "channel_daily_audit", slow_audit):
                    first_task = asyncio.create_task(self.server.daily_audit_job_next("audit-a"))
                    await asyncio.wait_for(started.wait(), timeout=1)
                    concurrent = await self.server.daily_audit_job_next("audit-a")
                    self.assertEqual(concurrent["error"]["type"], "conflict")
                    self.assertEqual(call_count, 1)

                    release.set()
                    first = await first_task
                    self.assertTrue(first["ok"])
                    self.assertEqual(job["status"], "queued")
                    self.assertEqual(job["remaining_channel_ids"], [CHANNEL_B])

                    started.clear()
                    release.clear()
                    cancelled_task = asyncio.create_task(
                        self.server.daily_audit_job_next("audit-a")
                    )
                    await asyncio.wait_for(started.wait(), timeout=1)
                    cancelled_task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await cancelled_task

                self.assertEqual(job["status"], "queued")
                self.assertEqual(job["remaining_channel_ids"], [CHANNEL_B])

                with patch.object(
                    self.server,
                    "channel_daily_audit",
                    AsyncMock(return_value={"ok": True, "data": {"call": 3}}),
                ):
                    completed = await self.server.daily_audit_job_next("audit-a")
                    self.assertTrue(completed["ok"])
                    self.assertEqual(job["status"], "completed")
                    finished_at_ts = job["finished_at_ts"]
                    repeated = await self.server.daily_audit_job_next("audit-a")

        self.assertTrue(repeated["ok"])
        self.assertEqual(job["finished_at_ts"], finished_at_ts)

    async def test_audit_cursor_restores_when_cancelled_before_final_commit(self):
        class SecondAcquireGate:
            def __init__(self):
                self.calls = 0
                self.second_waiting = asyncio.Event()
                self.never = asyncio.Event()

            async def __aenter__(self):
                self.calls += 1
                if self.calls == 2:
                    self.second_waiting.set()
                    await self.never.wait()
                return self

            async def __aexit__(self, *_args):
                return False

        gate = SecondAcquireGate()
        with self.runtime_policy(), self.tenant(TOKEN_A):
            owner = self.server.current_tenant_fingerprint()
            job = self.audit_job_record(owner)
            self.server.AUDIT_JOB_STORE["audit-a"] = job
            with (
                patch.object(self.server, "AUDIT_JOB_LOCK", gate),
                patch.object(
                    self.server,
                    "channel_daily_audit",
                    AsyncMock(return_value={"ok": True, "data": {"complete": True}}),
                ),
            ):
                task = asyncio.create_task(self.server.daily_audit_job_next("audit-a"))
                await asyncio.wait_for(gate.second_waiting.wait(), timeout=1)
                self.assertEqual(job["status"], "running")
                self.assertEqual(job["remaining_channel_ids"], [])

                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        self.assertEqual(gate.calls, 3)
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["remaining_channel_ids"], [CHANNEL_A])
        self.assertEqual(job["processed_channel_ids"], [])
        self.assertEqual(job["results"], {})

    async def test_audit_submit_freezes_date_and_bounds_channel_count(self):
        frozen_start = datetime(2031, 4, 5, tzinfo=timezone.utc)
        with self.runtime_policy(), self.tenant(TOKEN_A):
            with patch.object(
                self.server,
                "parse_audit_date",
                return_value=(frozen_start, frozen_start + timedelta(days=1)),
            ):
                submitted = await self.server.daily_audit_job_submit(
                    date="", channel_ids=[str(CHANNEL_A)]
                )
            oversized = await self.server.daily_audit_job_submit(
                channel_ids=[str(CHANNEL_A)] * (self.server.MAX_AUDIT_JOB_CHANNELS + 1)
            )

        self.assertTrue(submitted["ok"])
        job_id = submitted["data"]["task_id"]
        self.assertEqual(self.server.AUDIT_JOB_STORE[job_id]["date"], "2031-04-05")
        self.assertEqual(oversized["error"]["type"], "invalid_payload")

    async def test_generic_job_does_not_retain_params_or_provider_secrets(self):
        started = asyncio.Event()
        release = asyncio.Event()
        webhook_secret = "generic-job-webhook-secret"

        async def fake_action(**_params):
            started.set()
            await release.wait()
            return {
                "ok": True,
                "data": {
                    "api_key": "custom-job-secret",
                    "message": f"provider returned {TOKEN_A}",
                    "webhook": (
                        "https://discord.com/api/v10/webhooks/123456789012345678/" + webhook_secret
                    ),
                },
            }

        with (
            self.runtime_policy(),
            patch.object(self.server, "discord_health_check", fake_action),
            self.tenant(TOKEN_A),
        ):
            submitted = await self.server.discord_job_submit(
                "discord_health_check",
                params={"authorization": f"Bearer {TOKEN_A}", "message": TOKEN_A},
            )
            self.assertTrue(submitted["ok"])
            job_id = submitted["data"]["task_id"]
            await asyncio.wait_for(started.wait(), timeout=1)
            task = self.server.JOB_TASKS[job_id]
            self.assertNotIn("params", self.server.JOB_STORE[job_id])
            self.assertNotIn(TOKEN_A, json.dumps(self.server.JOB_STORE[job_id]))

            release.set()
            await task

        retained = json.dumps(self.server.JOB_STORE[job_id])
        self.assertNotIn(TOKEN_A, retained)
        self.assertNotIn("custom-job-secret", retained)
        self.assertNotIn(webhook_secret, retained)
        self.assertIn("REDACTED", retained)

    async def test_unfinished_jobs_expire_and_both_stores_fail_closed_at_capacity(self):
        now = time.time()
        old = now - self.server.JOB_TTL_SECONDS - 1
        sleeper = asyncio.create_task(asyncio.sleep(30))
        self.server.JOB_STORE["old"] = {
            "created_at_ts": old,
            "_last_used_at_ts": old,
        }
        self.server.JOB_TASKS["old"] = sleeper
        self.server.AUDIT_JOB_STORE["old"] = {
            "created_at_ts": old,
            "_last_used_at_ts": old,
        }

        await self.server.prune_jobs_locked(now)
        await self.server.prune_audit_jobs_locked(now)
        await asyncio.sleep(0)
        self.assertNotIn("old", self.server.JOB_STORE)
        self.assertNotIn("old", self.server.AUDIT_JOB_STORE)
        self.assertTrue(sleeper.cancelled())

        with self.runtime_policy(), self.tenant(TOKEN_A):
            owner = self.server.current_tenant_fingerprint()
            self.server.JOB_STORE["full"] = self.job_record(owner)
            self.server.AUDIT_JOB_STORE["full"] = self.audit_job_record(owner)
            with patch.object(self.server, "JOB_MAX_ENTRIES", 1):
                generic = await self.server.discord_job_submit("discord_health_check", params={})
                audit = await self.server.daily_audit_job_submit(channel_ids=[str(CHANNEL_A)])

        self.assertEqual(generic["error"]["type"], "resource_exhausted")
        self.assertEqual(audit["error"]["type"], "resource_exhausted")
        self.assertEqual(set(self.server.JOB_STORE), {"full"})
        self.assertEqual(set(self.server.AUDIT_JOB_STORE), {"full"})

    async def test_channel_cache_is_scoped_by_provider_credential(self):
        guild = FakeGuild()
        with self.runtime_policy():
            with self.tenant(TOKEN_A):
                channels_a, _, _ = await self.server.get_cached_channels(guild)
            with self.tenant(TOKEN_B):
                channels_b, _, _ = await self.server.get_cached_channels(guild)
            with self.tenant(TOKEN_A):
                cached_a, _, _ = await self.server.get_cached_channels(guild)

        self.assertEqual(guild.fetch_count, 2)
        self.assertEqual(channels_a[0].name, "tenant-1")
        self.assertEqual(channels_b[0].name, "tenant-2")
        self.assertEqual(cached_a[0].name, "tenant-1")
        self.assertEqual(len(self.server.CHANNEL_CACHE), 2)


if __name__ == "__main__":
    unittest.main()
