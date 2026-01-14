import asyncio
import json
import hashlib
import logging
import os
import re
import time
import uuid
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
import discord
from discord.ext import commands
from mcp.server.fastmcp import FastMCP
import uvicorn
from starlette.middleware.trustedhost import TrustedHostMiddleware

try:
    from fastmcp.server.dependencies import get_http_headers
except Exception:  # pragma: no cover - optional runtime dependency
    try:
        from mcp.server.dependencies import get_http_headers
    except Exception:  # pragma: no cover - optional runtime dependency
        try:
            from mcp.server.lowlevel.server import request_ctx
        except Exception:  # pragma: no cover - optional runtime dependency
            request_ctx = None

        def get_http_headers() -> dict:
            if request_ctx is None:
                return {}
            context = request_ctx.get(None)
            if context is None:
                return {}
            request = getattr(context, "request", None)
            if request is None:
                return {}
            headers = getattr(request, "headers", None)
            if headers is None:
                return {}
            try:
                return dict(headers)
            except Exception:
                return {}


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DEFAULT_GUILD_ID_RAW = os.getenv("DISCORD_GUILD_ID", "").strip()
MCP_HTTP_PORT = int(os.getenv("MCP_HTTP_PORT", "8085"))
MCP_BIND_ADDRESS = os.getenv("MCP_BIND_ADDRESS", "0.0.0.0")
DISCORD_PRIMARY_CHANNEL_ID_RAW = os.getenv("DISCORD_PRIMARY_CHANNEL_ID", "").strip()
DISCORD_ALLOWED_CHANNEL_IDS_RAW = os.getenv("DISCORD_ALLOWED_CHANNEL_IDS", "").strip()
DISCORD_BLOCKED_CHANNEL_IDS_RAW = os.getenv("DISCORD_BLOCKED_CHANNEL_IDS", "").strip()
MCP_ALLOW_REQUEST_OVERRIDES_RAW = os.getenv("MCP_ALLOW_REQUEST_OVERRIDES", "").strip()
MCP_REQUIRE_CONFIRM_RAW = os.getenv("MCP_REQUIRE_CONFIRM", "").strip()
OPENAI_VISION_ENABLED_RAW = os.getenv("OPENAI_VISION_ENABLED", "").strip()
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
OPENAI_VISION_API_URL = (
    os.getenv("OPENAI_VISION_API_URL", "https://api.openai.com/v1/chat/completions")
    .strip()
    or "https://api.openai.com/v1/chat/completions"
)
OPENAI_VISION_MAX_MB_RAW = os.getenv("OPENAI_VISION_MAX_MB", "10").strip()
OPENAI_VISION_TIMEOUT_SECONDS_RAW = os.getenv("OPENAI_VISION_TIMEOUT_SECONDS", "30").strip()
MCP_OPENAI_API_HEADER = os.getenv("MCP_OPENAI_API_HEADER", "x-openai-api").strip() or "x-openai-api"
MCP_REQUIRE_REQUEST_DISCORD_TOKEN_RAW = os.getenv(
    "MCP_REQUIRE_REQUEST_DISCORD_TOKEN", ""
).strip()
MCP_REQUIRE_REQUEST_GUILD_ID_RAW = os.getenv("MCP_REQUIRE_REQUEST_GUILD_ID", "").strip()
MCP_REQUIRE_REQUEST_BLOCKED_CHANNELS_RAW = os.getenv(
    "MCP_REQUIRE_REQUEST_BLOCKED_CHANNELS", ""
).strip()
MCP_DISCORD_TOKEN_HEADER = os.getenv(
    "MCP_DISCORD_TOKEN_HEADER", "x-discord-bot-token"
).strip() or "x-discord-bot-token"
MCP_DISCORD_GUILD_ID_HEADER = os.getenv(
    "MCP_DISCORD_GUILD_ID_HEADER", "x-discord-guild-id"
).strip() or "x-discord-guild-id"
MCP_DISCORD_BLOCKED_CHANNELS_HEADER = os.getenv(
    "MCP_DISCORD_BLOCKED_CHANNELS_HEADER", "x-discord-blocked-channels"
).strip() or "x-discord-blocked-channels"
MCP_BOT_POOL_TTL_SECONDS_RAW = os.getenv("MCP_BOT_POOL_TTL_SECONDS", "900").strip()
MCP_ADMIN_TOOLS_ENABLED = os.getenv("MCP_ADMIN_TOOLS_ENABLED", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
DISCORD_ALLOW_ALL_READ_RAW = os.getenv("DISCORD_ALLOW_ALL_READ", "").strip()
DISCORD_DM_ENABLED_RAW = os.getenv("DISCORD_DM_ENABLED", "").strip()
LOG_REDACT_MESSAGE_CONTENT_RAW = os.getenv("LOG_REDACT_MESSAGE_CONTENT", "true").strip()
DISCORD_PROTECTED_USER_IDS_RAW = os.getenv("DISCORD_PROTECTED_USER_IDS", "").strip()
DISCORD_PROTECTED_ROLE_IDS_RAW = os.getenv("DISCORD_PROTECTED_ROLE_IDS", "").strip()
DISCORD_ALLOWED_TARGET_ROLE_IDS_RAW = os.getenv(
    "DISCORD_ALLOWED_TARGET_ROLE_IDS", ""
).strip()
DISCORD_AUDIT_TIMEZONE_NAME = os.getenv(
    "DISCORD_AUDIT_TIMEZONE",
    "America/Los_Angeles",
).strip() or "America/Los_Angeles"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("discord_mcp")


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    return intents


def create_bot() -> commands.Bot:
    return commands.Bot(command_prefix="!", intents=build_intents())


@dataclass
class BotState:
    token: str
    bot: commands.Bot
    task: asyncio.Task | None
    lock: asyncio.Lock
    last_used: float


REQUEST_OVERRIDE_CONTEXT: ContextVar[dict | None] = ContextVar(
    "discord_request_overrides", default=None
)
REQUEST_OVERRIDE_WARNINGS: ContextVar[list[str] | None] = ContextVar(
    "discord_request_warnings", default=None
)

BOT_POOL: dict[str, BotState] = {}
BOT_POOL_LOCK = asyncio.Lock()


class DiscordMCP(FastMCP):
    def tool(self, *args, **kwargs):  # type: ignore[override]
        decorator = super().tool(*args, **kwargs)

        def wrap(func):
            @wraps(func)
            async def guarded(*func_args, **func_kwargs):
                if not ALLOW_REQUEST_OVERRIDES:
                    return await func(*func_args, **func_kwargs)
                if (
                    REQUEST_OVERRIDE_CONTEXT.get() is not None
                    or REQUEST_OVERRIDE_WARNINGS.get() is not None
                ):
                    return await func(*func_args, **func_kwargs)
                overrides, warnings = await build_request_overrides()
                overrides_token = None
                warnings_token = None
                try:
                    if overrides is not None:
                        overrides_token = REQUEST_OVERRIDE_CONTEXT.set(overrides)
                    if warnings:
                        warnings_token = REQUEST_OVERRIDE_WARNINGS.set(warnings)
                    return await func(*func_args, **func_kwargs)
                finally:
                    if overrides_token is not None:
                        REQUEST_OVERRIDE_CONTEXT.reset(overrides_token)
                    if warnings_token is not None:
                        REQUEST_OVERRIDE_WARNINGS.reset(warnings_token)

            return decorator(guarded)

        return wrap


mcp = DiscordMCP(
    name="discord-mcp",
    stateless_http=True,
    json_response=True,
    host="0.0.0.0",
)
CONFIG_WARNINGS = []
LAST_SUCCESSFUL_API_AT = None
LAST_RATE_LIMIT = {}
LAST_RATE_LIMIT_AT = None


def parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def parse_int(value, default=None):
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


CONFIRM_APPLY_VALUE = "CONFIRM APPLY"
CONFIRM_REQUIRED = (
    parse_bool(MCP_REQUIRE_CONFIRM_RAW) if MCP_REQUIRE_CONFIRM_RAW else True
)
ALLOW_REQUEST_OVERRIDES = parse_bool(MCP_ALLOW_REQUEST_OVERRIDES_RAW)
OPENAI_VISION_ENABLED = parse_bool(OPENAI_VISION_ENABLED_RAW)
REQUIRE_REQUEST_DISCORD_TOKEN = (
    parse_bool(MCP_REQUIRE_REQUEST_DISCORD_TOKEN_RAW)
    if MCP_REQUIRE_REQUEST_DISCORD_TOKEN_RAW
    else ALLOW_REQUEST_OVERRIDES
)
REQUIRE_REQUEST_GUILD_ID = (
    parse_bool(MCP_REQUIRE_REQUEST_GUILD_ID_RAW)
    if MCP_REQUIRE_REQUEST_GUILD_ID_RAW
    else ALLOW_REQUEST_OVERRIDES
)
REQUIRE_REQUEST_BLOCKED_CHANNELS = (
    parse_bool(MCP_REQUIRE_REQUEST_BLOCKED_CHANNELS_RAW)
    if MCP_REQUIRE_REQUEST_BLOCKED_CHANNELS_RAW
    else ALLOW_REQUEST_OVERRIDES
)
REQUEST_DISCORD_TOKEN_HEADER = MCP_DISCORD_TOKEN_HEADER.lower()
REQUEST_DISCORD_GUILD_ID_HEADER = MCP_DISCORD_GUILD_ID_HEADER.lower()
REQUEST_DISCORD_BLOCKED_CHANNELS_HEADER = MCP_DISCORD_BLOCKED_CHANNELS_HEADER.lower()
REQUEST_OPENAI_API_HEADER = MCP_OPENAI_API_HEADER.lower()
DISCORD_ALLOW_ALL_READ = parse_bool(DISCORD_ALLOW_ALL_READ_RAW)
DISCORD_DM_ENABLED = parse_bool(DISCORD_DM_ENABLED_RAW)
LOG_REDACT_MESSAGE_CONTENT = parse_bool(LOG_REDACT_MESSAGE_CONTENT_RAW)
BOT_POOL_TTL_SECONDS = parse_int(MCP_BOT_POOL_TTL_SECONDS_RAW, 900)
if BOT_POOL_TTL_SECONDS is None or BOT_POOL_TTL_SECONDS <= 0:
    BOT_POOL_TTL_SECONDS = 900
OPENAI_VISION_MAX_MB = parse_int(OPENAI_VISION_MAX_MB_RAW, 10)
if OPENAI_VISION_MAX_MB is None or OPENAI_VISION_MAX_MB <= 0:
    OPENAI_VISION_MAX_MB = 10
OPENAI_VISION_TIMEOUT_SECONDS = parse_int(OPENAI_VISION_TIMEOUT_SECONDS_RAW, 30)
if OPENAI_VISION_TIMEOUT_SECONDS is None or OPENAI_VISION_TIMEOUT_SECONDS <= 0:
    OPENAI_VISION_TIMEOUT_SECONDS = 30

if not DISCORD_TOKEN and not ALLOW_REQUEST_OVERRIDES:
    raise RuntimeError("DISCORD_TOKEN is not set")
try:
    AUDIT_TIMEZONE = ZoneInfo(DISCORD_AUDIT_TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    CONFIG_WARNINGS.append(
        f"Invalid DISCORD_AUDIT_TIMEZONE '{DISCORD_AUDIT_TIMEZONE_NAME}', using UTC."
    )
    AUDIT_TIMEZONE = ZoneInfo("UTC")

CHANNEL_CACHE_TTL_SECONDS = parse_int(
    os.getenv("DISCORD_CHANNEL_CACHE_TTL_SECONDS", "600"), 600
)
if CHANNEL_CACHE_TTL_SECONDS is None or CHANNEL_CACHE_TTL_SECONDS <= 0:
    CHANNEL_CACHE_TTL_SECONDS = 600
CHANNEL_CACHE = {}
CHANNEL_CACHE_LOCK = asyncio.Lock()

JOB_TTL_SECONDS = parse_int(os.getenv("DISCORD_JOB_TTL_SECONDS", "3600"), 3600)
if JOB_TTL_SECONDS is None or JOB_TTL_SECONDS <= 0:
    JOB_TTL_SECONDS = 3600
JOB_STORE = {}
JOB_TASKS = {}
JOB_LOCK = asyncio.Lock()
AUDIT_JOB_STORE = {}
AUDIT_JOB_LOCK = asyncio.Lock()


def parse_snowflake(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        if not cleaned.isdigit():
            return None
        return int(cleaned)
    return None


DEFAULT_GUILD_ID = parse_snowflake(DEFAULT_GUILD_ID_RAW)
if DEFAULT_GUILD_ID is None and not ALLOW_REQUEST_OVERRIDES:
    raise RuntimeError("DISCORD_GUILD_ID is not set or invalid")
DEFAULT_GUILD_ID_STR = str(DEFAULT_GUILD_ID) if DEFAULT_GUILD_ID is not None else ""


def normalize_channel_key(value: str) -> str:
    return "".join(ch for ch in value.lower().strip() if ch.isalnum())


async def close_bot_state(state: BotState):
    if state.bot is not None and not state.bot.is_closed():
        try:
            await state.bot.close()
        except Exception:
            pass


async def prune_bot_pool_locked(now: float) -> list[BotState]:
    expired_states: list[BotState] = []
    if BOT_POOL_TTL_SECONDS <= 0:
        return expired_states
    for token, state in list(BOT_POOL.items()):
        if now - state.last_used > BOT_POOL_TTL_SECONDS:
            expired_states.append(state)
            BOT_POOL.pop(token, None)
    return expired_states


async def prune_bot_pool(now: float | None = None):
    now = now or time.time()
    expired_states: list[BotState]
    async with BOT_POOL_LOCK:
        expired_states = await prune_bot_pool_locked(now)
    for state in expired_states:
        await close_bot_state(state)


async def get_bot_state(token: str) -> BotState:
    now = time.time()
    async with BOT_POOL_LOCK:
        expired_states = await prune_bot_pool_locked(now)
        state = BOT_POOL.get(token)
        if state is None:
            state = BotState(
                token=token,
                bot=create_bot(),
                task=None,
                lock=asyncio.Lock(),
                last_used=now,
            )
            BOT_POOL[token] = state
    for expired_state in expired_states:
        await close_bot_state(expired_state)
    return state


async def reset_bot_state(state: BotState, reason: str):
    await close_bot_state(state)
    state.bot = create_bot()
    state.task = None
    logger.warning("bot_reset token=%s reason=%s", state.token, reason)


async def reset_bot(reason: str, token: str | None = None):
    token = token or get_active_request_token()
    if not token:
        return
    state = await get_bot_state(token)
    async with state.lock:
        await reset_bot_state(state, reason)


async def wait_until_ready_safe(
    client: commands.Bot,
    timeout_seconds: float = 8.0,
    poll_interval: float = 0.2,
):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if client.is_ready():
            return
        ready_event = getattr(client, "_ready", None)
        if ready_event is not None and hasattr(ready_event, "wait"):
            try:
                await asyncio.wait_for(ready_event.wait(), timeout=poll_interval)
                return
            except asyncio.TimeoutError:
                continue
        await asyncio.sleep(poll_interval)
    raise discord.ClientException("Client did not become ready before timeout")


async def get_cached_channels(
    guild: discord.Guild, force_refresh: bool = False
) -> tuple[list, dict, dict]:
    now = time.time()
    cached = CHANNEL_CACHE.get(guild.id)
    if (
        cached
        and cached["expires_at"] > now
        and not force_refresh
        and cached["channels"]
    ):
        return cached["channels"], cached["name_map"], cached["normalized_map"]

    async with CHANNEL_CACHE_LOCK:
        cached = CHANNEL_CACHE.get(guild.id)
        if (
            cached
            and cached["expires_at"] > now
            and not force_refresh
            and cached["channels"]
        ):
            return cached["channels"], cached["name_map"], cached["normalized_map"]
        try:
            channels = await retry_read(
                "fetch_channels", lambda: guild.fetch_channels()
            )
            record_api_success("fetch_channels")
        except Exception as exc:
            logger.warning("channel_cache_refresh_failed guild_id=%s err=%s", guild.id, exc)
            channels = list(guild.channels)
        name_map = {}
        normalized_map = {}
        for channel in channels:
            name_lower = channel.name.lower()
            name_map.setdefault(name_lower, []).append(channel)
            normalized_key = normalize_channel_key(channel.name)
            if normalized_key:
                normalized_map.setdefault(normalized_key, []).append(channel)
        CHANNEL_CACHE[guild.id] = {
            "channels": channels,
            "name_map": name_map,
            "normalized_map": normalized_map,
            "expires_at": now + CHANNEL_CACHE_TTL_SECONDS,
        }
        return channels, name_map, normalized_map


async def resolve_blocked_channel_ids(
    guild: discord.Guild,
    blocked_names: list[str],
    warnings: list[str],
) -> set[int]:
    blocked_ids: set[int] = set()
    if not blocked_names:
        return blocked_ids
    _, name_map, _ = await get_cached_channels(guild)
    for raw_name in blocked_names:
        name = raw_name.strip()
        if not name:
            continue
        key = name.lower()
        matches = [
            channel
            for channel in name_map.get(key, [])
            if hasattr(channel, "send")
        ]
        if not matches:
            warnings.append(f"Blocked channel '#{name}' not found; ignoring.")
            continue
        if len(matches) > 1:
            warnings.append(
                f"Blocked channel '#{name}' matched multiple channels; using first."
            )
        blocked_ids.add(matches[0].id)
    return blocked_ids


def job_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


async def prune_jobs_locked(now: float | None = None):
    if now is None:
        now = time.time()
    expired = []
    for job_id, job in JOB_STORE.items():
        finished_at_ts = job.get("finished_at_ts")
        if finished_at_ts and now - finished_at_ts > JOB_TTL_SECONDS:
            expired.append(job_id)
    for job_id in expired:
        JOB_STORE.pop(job_id, None)
        JOB_TASKS.pop(job_id, None)


async def prune_audit_jobs_locked(now: float | None = None):
    if now is None:
        now = time.time()
    expired = []
    for job_id, job in AUDIT_JOB_STORE.items():
        finished_at_ts = job.get("finished_at_ts")
        if finished_at_ts and now - finished_at_ts > JOB_TTL_SECONDS:
            expired.append(job_id)
    for job_id in expired:
        AUDIT_JOB_STORE.pop(job_id, None)


def build_job_snapshot(job: dict, include_result: bool = False) -> dict:
    snapshot = {
        "task_id": job.get("task_id"),
        "action": job.get("action"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "error": job.get("error"),
    }
    if include_result:
        snapshot["result"] = job.get("result")
    return snapshot


def build_audit_job_snapshot(job: dict, include_results: bool = False) -> dict:
    remaining = job.get("remaining_channel_ids", [])
    processed = job.get("processed_channel_ids", [])
    snapshot = {
        "task_id": job.get("task_id"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "finished_at": job.get("finished_at"),
        "date": job.get("date"),
        "timezone": job.get("timezone"),
        "total_channels": job.get("total_channels", 0),
        "completed_count": len(processed),
        "remaining_count": len(remaining),
        "next_channel_id": str(remaining[0]) if remaining else None,
        "error": job.get("error"),
    }
    if include_results:
        snapshot["results"] = job.get("results", {})
    return snapshot


async def run_job(job_id: str, action_name: str, action_func, params: dict):
    async with JOB_LOCK:
        job = JOB_STORE.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["started_at"] = job_timestamp()
        job["started_at_ts"] = time.time()
    try:
        result = await action_func(**params)
        ok = not (isinstance(result, dict) and result.get("ok") is False)
        status = "succeeded" if ok else "failed"
        redacted_result = redact_payload(result)
        error = None
        if not ok and isinstance(redacted_result, dict):
            error = redacted_result.get("error")
    except Exception as exc:
        redacted_result = None
        status = "failed"
        error = exception_to_error(exc)
    async with JOB_LOCK:
        job = JOB_STORE.get(job_id)
        if job:
            job["status"] = status
            job["finished_at"] = job_timestamp()
            job["finished_at_ts"] = time.time()
            job["result"] = redacted_result
            job["error"] = error
        JOB_TASKS.pop(job_id, None)


def parse_allowed_channel_ids(raw: str) -> tuple[bool, set[int], list[str]]:
    ids = set()
    warnings = []
    if not raw:
        return False, ids, warnings
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    for part in parts:
        if part.lower() in ("all", "*"):
            if len(parts) > 1:
                warnings.append(
                    "DISCORD_ALLOWED_CHANNEL_IDS includes ALL with specific IDs; using ALL."
                )
            return True, set(), warnings
    for part in parts:
        parsed = parse_snowflake(part)
        if parsed is None:
            warnings.append(
                f"Invalid channel id in DISCORD_ALLOWED_CHANNEL_IDS: {part}"
            )
        else:
            ids.add(parsed)
    return False, ids, warnings


def parse_id_list(raw: str, name: str) -> tuple[set[int], list[str]]:
    ids = set()
    warnings = []
    if not raw:
        return ids, warnings
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    for part in parts:
        parsed = parse_snowflake(part)
        if parsed is None:
            warnings.append(f"Invalid ID in {name}: {part}")
        else:
            ids.add(parsed)
    return ids, warnings


def normalize_headers(headers: dict | None) -> dict[str, str]:
    if not headers:
        return {}
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            if not value:
                continue
            value = value[0]
        if isinstance(value, bytes):
            value = value.decode("utf-8", "ignore")
        normalized[str(key).lower()] = str(value).strip()
    return normalized


def parse_blocked_channel_names(raw: str | None) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, str):
        raw = str(raw)
    parts = [part.strip() for part in raw.split(",")]
    cleaned = []
    for part in parts:
        if not part:
            continue
        name = part.lstrip("#").strip()
        if name:
            cleaned.append(name)
    return cleaned


class HeaderAuthError(Exception):
    def __init__(self, message: str, required_headers: list[str]):
        super().__init__(message)
        self.code = -32001
        self.data = {
            "type": "permission_denied",
            "diagnostics": {"required_headers": required_headers},
        }


def get_active_request_overrides() -> dict | None:
    return REQUEST_OVERRIDE_CONTEXT.get()


def get_request_override_warnings() -> list[str]:
    return list(REQUEST_OVERRIDE_WARNINGS.get() or [])


def get_active_request_token() -> str | None:
    overrides = get_active_request_overrides()
    if overrides and overrides.get("token"):
        return overrides["token"]
    return DISCORD_TOKEN or None


def get_active_guild_id() -> int | None:
    overrides = get_active_request_overrides()
    if overrides and overrides.get("guild_id"):
        return overrides["guild_id"]
    return DEFAULT_GUILD_ID


def get_active_blocked_channel_ids() -> set[int]:
    override_ids = set()
    overrides = get_active_request_overrides()
    if overrides and overrides.get("blocked_channel_ids"):
        override_ids.update(overrides["blocked_channel_ids"])
    return set(BLOCKED_CHANNEL_IDS) | override_ids


async def build_request_overrides() -> tuple[dict | None, list[str]]:
    if not ALLOW_REQUEST_OVERRIDES:
        return None, []
    headers = normalize_headers(get_http_headers())
    token_present = REQUEST_DISCORD_TOKEN_HEADER in headers
    guild_present = REQUEST_DISCORD_GUILD_ID_HEADER in headers
    blocked_present = REQUEST_DISCORD_BLOCKED_CHANNELS_HEADER in headers

    token_value = headers.get(REQUEST_DISCORD_TOKEN_HEADER, "")
    guild_value = headers.get(REQUEST_DISCORD_GUILD_ID_HEADER, "")
    blocked_value = headers.get(REQUEST_DISCORD_BLOCKED_CHANNELS_HEADER, None)

    missing_required = []
    if REQUIRE_REQUEST_DISCORD_TOKEN and (not token_present or not token_value):
        missing_required.append(REQUEST_DISCORD_TOKEN_HEADER)
    if REQUIRE_REQUEST_GUILD_ID and (not guild_present or not guild_value):
        missing_required.append(REQUEST_DISCORD_GUILD_ID_HEADER)
    if REQUIRE_REQUEST_BLOCKED_CHANNELS and not blocked_present:
        missing_required.append(REQUEST_DISCORD_BLOCKED_CHANNELS_HEADER)
    if missing_required:
        raise HeaderAuthError(
            "Missing required header(s): " + ", ".join(missing_required) + ".",
            missing_required,
        )

    if not token_present and not guild_present and not blocked_present:
        return None, []

    guild_id = None
    if guild_value:
        guild_id = parse_snowflake(guild_value)
        if guild_id is None:
            raise ValueError("Invalid guild id header.")

    blocked_names = parse_blocked_channel_names(blocked_value)
    overrides = {
        "token": token_value or None,
        "guild_id": guild_id,
        "blocked_channel_names": blocked_names,
        "blocked_channel_ids": set(),
    }
    warnings: list[str] = []
    if blocked_names:
        if guild_id is None:
            warnings.append("Blocked channels provided without a guild id; ignoring.")
            return overrides, warnings
        if not token_value:
            warnings.append("Blocked channels provided without a bot token; ignoring.")
            return overrides, warnings
        client = await get_client_for_token(token_value)
        guild = await get_guild_for_client(client, guild_id)
        blocked_ids = await resolve_blocked_channel_ids(guild, blocked_names, warnings)
        overrides["blocked_channel_ids"] = blocked_ids

    return overrides, warnings


def split_text(text: str, limit: int) -> list[str]:
    if text is None:
        return []
    remaining = str(text)
    if not remaining:
        return []
    parts = []
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at > 0:
            split_at += 1
        else:
            split_at = limit
        parts.append(remaining[:split_at])
        remaining = remaining[split_at:]
    return parts


LINK_RE = re.compile(r"https?://\\S+", re.IGNORECASE)
MAX_AUDIT_ITEMS = 10


def serialize_embeds(embeds: list[discord.Embed] | None) -> list[dict]:
    if not embeds:
        return []
    payload = []
    for embed in embeds:
        fields = []
        for field in embed.fields or []:
            fields.append(
                {
                    "name": field.name if getattr(field, "name", None) else None,
                    "value": field.value if getattr(field, "value", None) else None,
                    "inline": bool(getattr(field, "inline", False)),
                }
            )
        payload.append(
            {
                "title": embed.title,
                "description": embed.description,
                "url": embed.url,
                "color": getattr(embed.color, "value", None)
                if getattr(embed, "color", None) is not None
                else None,
                "fields": fields,
                "footer": getattr(embed.footer, "text", None)
                if getattr(embed, "footer", None) is not None
                else None,
                "author": getattr(embed.author, "name", None)
                if getattr(embed, "author", None) is not None
                else None,
            }
        )
    return payload


def extract_embed_text(embeds: list[discord.Embed] | None) -> str:
    if not embeds:
        return ""
    parts = []
    for embed in embeds:
        if embed.title:
            parts.append(embed.title)
        if embed.description:
            parts.append(embed.description)
        if getattr(embed, "author", None) is not None and getattr(embed.author, "name", None):
            parts.append(embed.author.name)
        for field in embed.fields or []:
            if getattr(field, "name", None):
                parts.append(field.name)
            if getattr(field, "value", None):
                parts.append(field.value)
        if getattr(embed, "footer", None) is not None and getattr(embed.footer, "text", None):
            parts.append(embed.footer.text)
        if embed.url:
            parts.append(embed.url)
    return "\n".join(part for part in parts if part)


def merge_message_text(content: str | None, embed_text: str) -> str:
    content_value = content or ""
    embed_value = embed_text or ""
    if content_value and embed_value:
        return f"{content_value}\n\n{embed_value}"
    return content_value or embed_value


def get_message_text(message) -> str:
    embeds = getattr(message, "embeds", None)
    return merge_message_text(getattr(message, "content", None), extract_embed_text(embeds))


def get_openai_api_key(headers: dict | None = None) -> str | None:
    normalized = normalize_headers(headers or get_http_headers())
    value = normalized.get(REQUEST_OPENAI_API_HEADER, "").strip()
    return value or None


def is_image_attachment(attachment) -> bool:
    content_type = (getattr(attachment, "content_type", None) or "").lower()
    if content_type.startswith("image/"):
        return True
    filename = (getattr(attachment, "filename", None) or "").lower()
    return filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"))


def attachment_metadata(attachment) -> dict:
    return {
        "filename": getattr(attachment, "filename", None),
        "content_type": getattr(attachment, "content_type", None),
        "size_bytes": getattr(attachment, "size", None),
        "url": getattr(attachment, "url", None),
        "proxy_url": getattr(attachment, "proxy_url", None),
        "width": getattr(attachment, "width", None),
        "height": getattr(attachment, "height", None),
    }


def resolve_timezone(name: str | None) -> ZoneInfo:
    if not name:
        return AUDIT_TIMEZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid timezone: {name}") from exc


def parse_datetime_param(value: str | None, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid datetime format; use ISO-8601.") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def parse_audit_date(value: str | None, tz: ZoneInfo) -> tuple[datetime, datetime]:
    if value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Invalid date format; use YYYY-MM-DD.") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        date_value = dt.date()
    else:
        date_value = datetime.now(tz).date()
    start_local = datetime(
        date_value.year, date_value.month, date_value.day, tzinfo=tz
    )
    end_local = start_local + timedelta(days=1)
    return start_local, end_local


async def fetch_messages_in_range(channel, start_utc: datetime, end_utc: datetime, limit: int):
    async def fetch_history():
        return [
            m
            async for m in channel.history(limit=limit, after=start_utc, before=end_utc)
        ]

    return await retry_read("fetch_history", fetch_history)


def build_audit_item(msg) -> dict:
    content = get_message_text(msg)
    snippet = content.strip()
    if len(snippet) > 200:
        snippet = snippet[:200] + "..."
    return {
        "message_id": str(msg.id),
        "author_id": str(msg.author.id),
        "author_name": msg.author.name,
        "created_at": msg.created_at.isoformat(),
        "jump_url": msg.jump_url,
        "snippet": snippet,
    }


def classify_audit_message(msg_content: str) -> str | None:
    content = (msg_content or "").lower()
    if not content:
        return None
    if "blocker" in content or "blocked" in content or "stuck" in content:
        return "blockers"
    if "decision" in content or "decided" in content or "approved" in content:
        return "decisions"
    if "?" in content or content.startswith("q:") or "question" in content:
        return "questions"
    if "shipped" in content or "merged" in content or "completed" in content:
        return "highlights"
    return None


def summarize_daily_audit(messages: list) -> dict:
    message_count = len(messages)
    author_counter = Counter()
    author_names = {}
    link_counter = Counter()
    attachments_count = 0
    buckets = {"highlights": [], "blockers": [], "decisions": [], "questions": []}

    for msg in messages:
        text = get_message_text(msg)
        author_counter[msg.author.id] += 1
        author_names.setdefault(msg.author.id, msg.author.name)
        if msg.attachments:
            attachments_count += len(msg.attachments)
        links = LINK_RE.findall(text)
        link_counter.update(links)
        bucket = classify_audit_message(text)
        if bucket and len(buckets[bucket]) < MAX_AUDIT_ITEMS:
            buckets[bucket].append(build_audit_item(msg))

    top_authors = [
        {
            "id": str(author_id),
            "name": author_names.get(author_id),
            "count": count,
        }
        for author_id, count in author_counter.most_common(5)
    ]
    links_top = [
        {"url": url, "count": count} for url, count in link_counter.most_common(5)
    ]
    return {
        "message_count": message_count,
        "unique_authors": len(author_counter),
        "top_authors": top_authors,
        "links_topN": links_top,
        "attachments_count": attachments_count,
        "highlights": buckets["highlights"],
        "blockers": buckets["blockers"],
        "decisions": buckets["decisions"],
        "questions": buckets["questions"],
    }


PRIMARY_CHANNEL_ID = parse_snowflake(DISCORD_PRIMARY_CHANNEL_ID_RAW)
if DISCORD_PRIMARY_CHANNEL_ID_RAW and PRIMARY_CHANNEL_ID is None:
    CONFIG_WARNINGS.append("Invalid DISCORD_PRIMARY_CHANNEL_ID configured.")
ALLOW_ALL_CHANNELS, ALLOWED_CHANNEL_IDS, allowlist_warnings = parse_allowed_channel_ids(
    DISCORD_ALLOWED_CHANNEL_IDS_RAW
)
if not DISCORD_ALLOWED_CHANNEL_IDS_RAW:
    ALLOW_ALL_CHANNELS = True
CONFIG_WARNINGS.extend(allowlist_warnings)

BLOCKED_CHANNEL_IDS, blocked_channel_warnings = parse_id_list(
    DISCORD_BLOCKED_CHANNEL_IDS_RAW, "DISCORD_BLOCKED_CHANNEL_IDS"
)
CONFIG_WARNINGS.extend(blocked_channel_warnings)

PROTECTED_USER_ID_DEFAULT = 1229101510546423960
PROTECTED_USER_IDS, protected_user_warnings = parse_id_list(
    DISCORD_PROTECTED_USER_IDS_RAW, "DISCORD_PROTECTED_USER_IDS"
)
PROTECTED_USER_IDS.add(PROTECTED_USER_ID_DEFAULT)
CONFIG_WARNINGS.extend(protected_user_warnings)

PROTECTED_ROLE_IDS, protected_role_warnings = parse_id_list(
    DISCORD_PROTECTED_ROLE_IDS_RAW, "DISCORD_PROTECTED_ROLE_IDS"
)
CONFIG_WARNINGS.extend(protected_role_warnings)

ALLOWED_TARGET_ROLE_IDS, allowed_target_role_warnings = parse_id_list(
    DISCORD_ALLOWED_TARGET_ROLE_IDS_RAW, "DISCORD_ALLOWED_TARGET_ROLE_IDS"
)
CONFIG_WARNINGS.extend(allowed_target_role_warnings)


HEALTH_CHECK_SAMPLE_LIMIT = 5
DEFAULT_READ_LIMIT = 50
MAX_READ_LIMIT = 100
MAX_TIMEOUT_MINUTES = 28 * 24 * 60
MAX_NICKNAME_LENGTH = 32


def effective_allowed_channel_ids(
    guild: discord.Guild | None = None, for_write: bool = False
) -> list[int]:
    blocked_ids = get_active_blocked_channel_ids()
    ids = []
    if PRIMARY_CHANNEL_ID is not None:
        ids.append(PRIMARY_CHANNEL_ID)
    allow_all = ALLOW_ALL_CHANNELS if for_write else (DISCORD_ALLOW_ALL_READ or ALLOW_ALL_CHANNELS)
    if allow_all:
        if guild is not None:
            sample_count = 0
            for channel in guild.text_channels:
                if channel.id in blocked_ids:
                    continue
                if channel.id in ids:
                    continue
                ids.append(channel.id)
                sample_count += 1
                if sample_count >= HEALTH_CHECK_SAMPLE_LIMIT:
                    break
        return [cid for cid in ids if cid not in blocked_ids]
    ids.extend(sorted(ALLOWED_CHANNEL_IDS))
    ids = [cid for cid in ids if cid not in blocked_ids]
    return ids


def resolve_channel_id(channel_id) -> int:
    parsed = parse_snowflake(channel_id)
    if parsed is not None:
        return parsed
    if channel_id is not None and str(channel_id).strip():
        raise ValueError("channelId must be a Discord snowflake")
    if PRIMARY_CHANNEL_ID is not None:
        return PRIMARY_CHANNEL_ID
    if len(ALLOWED_CHANNEL_IDS) == 1:
        return next(iter(ALLOWED_CHANNEL_IDS))
    raise ValueError("channelId cannot be null and no default channel is configured")


def is_write_allowed(channel_id: int) -> bool:
    if channel_id in get_active_blocked_channel_ids():
        return False
    if ALLOW_ALL_CHANNELS:
        return True
    if not ALLOWED_CHANNEL_IDS:
        return False
    if PRIMARY_CHANNEL_ID is not None and channel_id == PRIMARY_CHANNEL_ID:
        return True
    return channel_id in ALLOWED_CHANNEL_IDS


def is_read_allowed(channel_id: int) -> bool:
    if channel_id in get_active_blocked_channel_ids():
        return False
    if DISCORD_ALLOW_ALL_READ:
        return True
    return is_write_allowed(channel_id)


def is_channel_allowed(channel_id: int) -> bool:
    return is_write_allowed(channel_id)


def filter_channels_for_read(channels: list) -> list:
    blocked_ids = get_active_blocked_channel_ids()
    channels = [
        channel
        for channel in channels
        if getattr(channel, "id", None) not in blocked_ids
    ]
    if DISCORD_ALLOW_ALL_READ or ALLOW_ALL_CHANNELS:
        return channels
    allowed_ids = set(ALLOWED_CHANNEL_IDS)
    if PRIMARY_CHANNEL_ID is not None:
        allowed_ids.add(PRIMARY_CHANNEL_ID)
    return [channel for channel in channels if getattr(channel, "id", None) in allowed_ids]


def record_api_success(action: str, rate_limit_snapshot: dict | None = None):
    global LAST_SUCCESSFUL_API_AT, LAST_RATE_LIMIT, LAST_RATE_LIMIT_AT
    LAST_SUCCESSFUL_API_AT = datetime.now(timezone.utc).isoformat()
    if rate_limit_snapshot:
        LAST_RATE_LIMIT = dict(rate_limit_snapshot)
        LAST_RATE_LIMIT_AT = LAST_SUCCESSFUL_API_AT


def update_rate_limit_from_exception(exc):
    global LAST_RATE_LIMIT, LAST_RATE_LIMIT_AT
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        LAST_RATE_LIMIT = {
            "rate_limited": True,
            "retry_after_seconds": retry_after,
        }
        LAST_RATE_LIMIT_AT = datetime.now(timezone.utc).isoformat()


def get_rate_limit_snapshot() -> dict:
    snapshot = {}
    if LAST_RATE_LIMIT:
        snapshot.update(LAST_RATE_LIMIT)
        if LAST_RATE_LIMIT_AT:
            snapshot["last_rate_limit_at"] = LAST_RATE_LIMIT_AT
    bot_instance = None
    token = get_active_request_token()
    state = BOT_POOL.get(token) if token else None
    if state is not None:
        bot_instance = state.bot
    http = getattr(bot_instance, "http", None)
    global_over = getattr(http, "_global_over", None)
    if isinstance(global_over, asyncio.Event):
        snapshot["global_rate_limited"] = not global_over.is_set()
    buckets = getattr(http, "_buckets", None)
    if isinstance(buckets, dict):
        snapshot["bucket_count"] = len(buckets)
    known = bool(snapshot)
    snapshot["known"] = known
    return snapshot


def log_action(
    action: str,
    start_time: float,
    status: str,
    guild_id: int | None = None,
    channel_id: int | None = None,
    error_type: str | None = None,
):
    duration_ms = int((time.perf_counter() - start_time) * 1000)
    rate_limit = get_rate_limit_snapshot()
    logger.info(
        "action=%s status=%s duration_ms=%s guild_id=%s channel_id=%s error_type=%s rate_limit=%s",
        action,
        status,
        duration_ms,
        guild_id,
        channel_id,
        error_type,
        rate_limit,
    )


def build_meta(
    start_time: float,
    request_id: str | None = None,
    warnings: list[str] | None = None,
    guild_id: int | None = None,
    channel_id: int | None = None,
    thread_id: int | None = None,
    extra: dict | None = None,
) -> dict:
    merged_warnings = list(warnings or [])
    override_warnings = get_request_override_warnings()
    for warning in override_warnings:
        if warning not in merged_warnings:
            merged_warnings.append(warning)
    meta = {
        "duration_ms": int((time.perf_counter() - start_time) * 1000),
        "rate_limit": get_rate_limit_snapshot(),
        "warnings": merged_warnings,
    }
    if request_id:
        meta["request_id"] = request_id
    if guild_id is not None:
        meta["guild_id"] = str(guild_id)
    if channel_id is not None:
        meta["channel_id"] = str(channel_id)
    if thread_id is not None:
        meta["thread_id"] = str(thread_id)
    if extra:
        meta.update(extra)
    return meta


def success_response(data: dict, meta: dict) -> dict:
    return {"ok": True, "data": data, "meta": meta}


def build_error(
    error_type: str,
    message: str,
    required_perms: list[str] | None = None,
    discord_code: int | None = None,
    diagnostics: dict | None = None,
) -> dict:
    error = {"type": error_type, "message": message}
    if required_perms:
        error["required_perms"] = list(required_perms)
    if discord_code is not None:
        error["discord_error_code"] = discord_code
    if diagnostics:
        error["diagnostics"] = diagnostics
    return error


def error_response(
    error_type: str,
    message: str,
    meta: dict,
    required_perms: list[str] | None = None,
    discord_code: int | None = None,
    diagnostics: dict | None = None,
) -> dict:
    return {
        "ok": False,
        "error": build_error(
            error_type,
            message,
            required_perms=required_perms,
            discord_code=discord_code,
            diagnostics=diagnostics,
        ),
        "meta": meta,
    }


def error_with_log(
    action: str,
    start_time: float,
    request_id: str | None,
    error: dict,
    warnings: list[str] | None = None,
    guild_id: int | None = None,
    channel_id: int | None = None,
    thread_id: int | None = None,
    extra: dict | None = None,
) -> dict:
    meta = build_meta(
        start_time,
        request_id=request_id,
        warnings=warnings,
        guild_id=guild_id,
        channel_id=channel_id,
        thread_id=thread_id,
        extra=extra,
    )
    log_action(
        action,
        start_time,
        "error",
        guild_id=guild_id,
        channel_id=channel_id,
        error_type=error.get("type"),
    )
    return {"ok": False, "error": error, "meta": meta}


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


REDACT_KEYS = {"content", "embed_description", "embed_title", "new_message", "snippet"}


def redact_payload(payload):
    if not LOG_REDACT_MESSAGE_CONTENT:
        return payload
    return _redact_payload(payload)


def _redact_payload(payload):
    if isinstance(payload, dict):
        redacted = {}
        for key, value in payload.items():
            if key in REDACT_KEYS and value is not None:
                text = str(value)
                redacted[key] = None
                redacted[f"{key}_hash"] = hash_text(text)
                redacted[f"{key}_length"] = len(text)
                continue
            redacted[key] = _redact_payload(value)
        return redacted
    if isinstance(payload, list):
        return [_redact_payload(item) for item in payload]
    return payload


async def retry_read(action: str, coro_factory, max_retries: int = 3):
    attempt = 0
    while True:
        try:
            return await coro_factory()
        except discord.HTTPException as exc:
            if getattr(exc, "status", None) == 429 and attempt < max_retries:
                update_rate_limit_from_exception(exc)
                retry_after = getattr(exc, "retry_after", None)
                if retry_after is None:
                    retry_after = 1.0 * (2 ** attempt)
                await asyncio.sleep(retry_after)
                attempt += 1
                continue
            raise


def exception_to_error(
    exc: Exception,
    required_perms: list[str] | None = None,
    diagnostics: dict | None = None,
) -> dict:
    if isinstance(exc, discord.Forbidden):
        return build_error(
            "permission_denied",
            "Permission denied by Discord API.",
            required_perms=required_perms,
            discord_code=getattr(exc, "code", None),
            diagnostics=diagnostics,
        )
    if isinstance(exc, discord.NotFound):
        return build_error(
            "not_found",
            "Discord resource not found.",
            discord_code=getattr(exc, "code", None),
            diagnostics=diagnostics,
        )
    if isinstance(exc, discord.HTTPException):
        if getattr(exc, "status", None) == 429:
            update_rate_limit_from_exception(exc)
            return build_error(
                "rate_limited",
                "Discord rate limit exceeded.",
                discord_code=getattr(exc, "code", None),
                diagnostics=diagnostics,
            )
        return build_error(
            "invalid_payload",
            f"Discord API error (status {getattr(exc, 'status', 'unknown')}).",
            discord_code=getattr(exc, "code", None),
            diagnostics=diagnostics,
        )
    if isinstance(exc, ValueError):
        return build_error("invalid_payload", str(exc), diagnostics=diagnostics)
    return build_error("invalid_payload", "Unexpected error.", diagnostics=diagnostics)


def require_confirm(
    confirm: str | None,
    action: str,
    start_time: float,
    request_id: str | None,
    warnings: list[str] | None = None,
    guild_id: int | None = None,
    channel_id: int | None = None,
    diagnostics: dict | None = None,
    extra: dict | None = None,
) -> dict | None:
    if not CONFIRM_REQUIRED:
        return None
    if confirm != CONFIRM_APPLY_VALUE:
        error = build_error(
            "permission_denied",
            "confirm must be 'CONFIRM APPLY'.",
            required_perms=[f"confirm={CONFIRM_APPLY_VALUE}"],
            diagnostics=diagnostics,
        )
        return error_with_log(
            action,
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=guild_id,
            channel_id=channel_id,
        )
    return None


def require_write_allowed(
    channel_id: int,
    action: str,
    start_time: float,
    request_id: str | None,
    warnings: list[str] | None = None,
    guild_id: int | None = None,
    diagnostics: dict | None = None,
) -> dict | None:
    if channel_id in get_active_blocked_channel_ids():
        error = build_error(
            "permission_denied",
            "Channel is blocked from writes.",
            required_perms=["DISCORD_BLOCKED_CHANNEL_IDS"],
            diagnostics=diagnostics,
        )
        return error_with_log(
            action,
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=guild_id,
            channel_id=channel_id,
        )
    if is_write_allowed(channel_id):
        return None
    error = build_error(
        "permission_denied",
        "Channel is not in the write allowlist.",
        required_perms=["allowlisted_channel"],
        diagnostics=diagnostics,
    )
    return error_with_log(
        action,
        start_time,
        request_id,
        error,
        warnings=warnings,
        guild_id=guild_id,
        channel_id=channel_id,
        extra=extra,
    )


def require_read_allowed(
    channel_id: int,
    action: str,
    start_time: float,
    request_id: str | None,
    warnings: list[str] | None = None,
    guild_id: int | None = None,
    diagnostics: dict | None = None,
) -> dict | None:
    if channel_id in get_active_blocked_channel_ids():
        error = build_error(
            "permission_denied",
            "Channel is blocked from reads.",
            required_perms=["DISCORD_BLOCKED_CHANNEL_IDS"],
            diagnostics=diagnostics,
        )
        return error_with_log(
            action,
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=guild_id,
            channel_id=channel_id,
        )
    if is_read_allowed(channel_id):
        return None
    error = build_error(
        "permission_denied",
        "Channel is not readable with current allowlist settings.",
        required_perms=["read_allowed_channel"],
        diagnostics=diagnostics,
    )
    return error_with_log(
        action,
        start_time,
        request_id,
        error,
        warnings=warnings,
        guild_id=guild_id,
        channel_id=channel_id,
    )


def require_dm_enabled(
    action: str,
    start_time: float,
    request_id: str | None,
    warnings: list[str] | None = None,
) -> dict | None:
    if DISCORD_DM_ENABLED:
        return None
    error = build_error(
        "permission_denied",
        "DM tools are disabled.",
        required_perms=["DISCORD_DM_ENABLED=true"],
    )
    return error_with_log(action, start_time, request_id, error, warnings=warnings)


def require_writes_enabled(
    action: str,
    start_time: float,
    request_id: str | None,
    warnings: list[str] | None = None,
) -> dict | None:
    if ALLOW_ALL_CHANNELS or ALLOWED_CHANNEL_IDS or PRIMARY_CHANNEL_ID is not None:
        return None
    error = build_error(
        "permission_denied",
        "Writes are disabled; configure DISCORD_ALLOWED_CHANNEL_IDS.",
        required_perms=["DISCORD_ALLOWED_CHANNEL_IDS"],
    )
    return error_with_log(action, start_time, request_id, error, warnings=warnings)


def channel_capabilities(perms: discord.Permissions) -> dict:
    return {
        "view": perms.view_channel,
        "read_history": perms.read_message_history,
        "send": perms.send_messages,
        "embed_links": perms.embed_links,
        "attach_files": perms.attach_files,
        "add_reactions": perms.add_reactions,
        "manage_messages": perms.manage_messages,
        "create_threads": perms.create_public_threads or perms.create_private_threads,
    }


async def get_member_or_error(
    guild: discord.Guild,
    user_id: int,
    action: str,
    start_time: float,
    request_id: str,
    warnings: list[str] | None,
    audit_trail_id: str,
) -> tuple[discord.Member | None, dict | None]:
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await retry_read(
                "fetch_member", lambda: guild.fetch_member(user_id)
            )
            record_api_success("fetch_member")
        except discord.NotFound:
            member = None
    if member is None:
        error = build_error("not_found", "User not found in guild.")
        return None, error_with_log(
            action,
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
    return member, None


async def fetch_member_optional(
    guild: discord.Guild,
    user_id: int,
) -> discord.Member | None:
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await retry_read(
                "fetch_member", lambda: guild.fetch_member(user_id)
            )
            record_api_success("fetch_member")
        except discord.NotFound:
            return None
    return member


def ensure_member_guardrails(
    member: discord.Member,
    action: str,
    start_time: float,
    request_id: str,
    warnings: list[str] | None,
    audit_trail_id: str,
    role_id: int | None = None,
) -> dict | None:
    if member.id in PROTECTED_USER_IDS:
        error = build_error(
            "permission_denied",
            "Target user is protected.",
            required_perms=["DISCORD_PROTECTED_USER_IDS"],
        )
        return error_with_log(
            action,
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=member.guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
    if member.guild.owner_id == member.id:
        error = build_error("permission_denied", "Cannot moderate the guild owner.")
        return error_with_log(
            action,
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=member.guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
    protected_roles = [
        role.id for role in member.roles if role.id in PROTECTED_ROLE_IDS
    ]
    if protected_roles:
        error = build_error(
            "permission_denied",
            "Target user has protected roles.",
            required_perms=["DISCORD_PROTECTED_ROLE_IDS"],
            diagnostics={"protected_role_ids": [str(rid) for rid in protected_roles]},
        )
        return error_with_log(
            action,
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=member.guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
    if ALLOWED_TARGET_ROLE_IDS:
        has_allowed = any(
            role.id in ALLOWED_TARGET_ROLE_IDS for role in member.roles
        )
        role_allowed = role_id in ALLOWED_TARGET_ROLE_IDS if role_id else False
        if not has_allowed and not role_allowed:
            error = build_error(
                "permission_denied",
                "Target user does not have an allowed role.",
                required_perms=["DISCORD_ALLOWED_TARGET_ROLE_IDS"],
            )
            return error_with_log(
                action,
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=member.guild.id,
                extra={"audit_trail_id": audit_trail_id},
            )
    return None


async def ensure_bot_can_moderate(
    guild: discord.Guild,
    target: discord.Member,
    action: str,
    start_time: float,
    request_id: str,
    warnings: list[str] | None,
    audit_trail_id: str,
    required_perm: str | None = None,
) -> tuple[discord.Member | None, dict | None]:
    bot_member = await get_bot_member(guild)
    if bot_member is None:
        return None, error_with_log(
            action,
            start_time,
            request_id,
            build_error("invalid_payload", "Bot member not available."),
            warnings=warnings,
            guild_id=guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
    perms = bot_member.guild_permissions
    if required_perm and not getattr(perms, required_perm, False):
        error = build_error(
            "permission_denied",
            f"Missing permission: {required_perm}.",
            required_perms=[required_perm],
        )
        return None, error_with_log(
            action,
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
    if target.top_role >= bot_member.top_role and guild.owner_id != bot_member.id:
        error = build_error(
            "permission_denied",
            "Bot role hierarchy prevents moderating this member.",
            required_perms=["role_hierarchy"],
        )
        return None, error_with_log(
            action,
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
    return bot_member, None


async def ensure_bot_has_permission(
    guild: discord.Guild,
    action: str,
    start_time: float,
    request_id: str,
    warnings: list[str] | None,
    audit_trail_id: str,
    required_perm: str | None = None,
) -> tuple[discord.Member | None, dict | None]:
    bot_member = await get_bot_member(guild)
    if bot_member is None:
        return None, error_with_log(
            action,
            start_time,
            request_id,
            build_error("invalid_payload", "Bot member not available."),
            warnings=warnings,
            guild_id=guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
    perms = bot_member.guild_permissions
    if required_perm and not getattr(perms, required_perm, False):
        error = build_error(
            "permission_denied",
            f"Missing permission: {required_perm}.",
            required_perms=[required_perm],
        )
        return None, error_with_log(
            action,
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
    return bot_member, None


def is_http_session_closed(client: commands.Bot) -> bool:
    http = getattr(client, "http", None)
    session = getattr(http, "_session", None)
    return bool(session is not None and getattr(session, "closed", False))


async def get_client_for_token(token: str) -> commands.Bot:
    state = await get_bot_state(token)
    async with state.lock:
        if state.task is not None and state.task.done():
            try:
                exc = state.task.exception()
            except Exception as exc:
                exc = exc
            if exc:
                logger.warning("bot_task_failed token=%s err=%s", token, exc)
                await reset_bot_state(state, "bot_task_failed")
        if state.bot is None or state.bot.is_closed() or is_http_session_closed(state.bot):
            await reset_bot_state(state, "bot_closed_or_session")
        if state.task is None or state.task.done():
            state.task = asyncio.create_task(state.bot.start(token))
    await wait_until_ready_safe(state.bot)
    state.last_used = time.time()
    return state.bot


async def get_client() -> commands.Bot:
    token = get_active_request_token()
    if not token:
        raise ValueError("Discord bot token is not configured.")
    return await get_client_for_token(token)


async def ensure_client_ready(retry: int = 1) -> commands.Bot:
    try:
        return await get_client()
    except Exception as exc:
        if retry <= 0:
            raise
        logger.warning("ensure_client_retry err=%s", exc)
        await reset_bot("ensure_client_retry")
        return await get_client()


def get_client_debug_snapshot() -> dict:
    token = get_active_request_token()
    state = BOT_POOL.get(token) if token else None
    bot_instance = state.bot if state else None
    task = state.task if state else None
    ready_event = getattr(bot_instance, "_ready", None) if bot_instance is not None else None
    snapshot = {
        "client_id": id(bot_instance) if bot_instance is not None else None,
        "client_module": bot_instance.__class__.__module__ if bot_instance is not None else None,
        "is_ready": bot_instance.is_ready() if bot_instance is not None else False,
        "is_closed": bot_instance.is_closed() if bot_instance is not None else True,
        "session_closed": is_http_session_closed(bot_instance)
        if bot_instance is not None
        else None,
        "lock_locked": state.lock.locked() if state else False,
        "ready_event_exists": ready_event is not None,
        "ready_event_set": ready_event.is_set() if ready_event is not None else None,
    }
    if task is None:
        snapshot["task_state"] = "none"
        snapshot["task_id"] = None
    else:
        snapshot["task_state"] = "done" if task.done() else "running"
        snapshot["task_cancelled"] = task.cancelled()
        snapshot["task_id"] = id(task)
        if task.done():
            try:
                exc = task.exception()
            except Exception as exc:
                exc = exc
            snapshot["task_exception"] = str(exc) if exc else None
    return snapshot


async def get_bot_member(guild: discord.Guild) -> discord.Member | None:
    client = await get_client()
    if client.user is None:
        return None
    member = guild.get_member(client.user.id)
    if member is None:
        try:
            member = await guild.fetch_member(client.user.id)
            record_api_success("fetch_member")
        except discord.NotFound:
            return None
    return member


def resolve_guild_id(guild_id: str | None) -> int:
    overrides = get_active_request_overrides()
    if overrides and overrides.get("guild_id"):
        override_id = overrides["guild_id"]
        if guild_id and str(guild_id).strip():
            parsed = parse_snowflake(guild_id)
            if parsed is None:
                raise ValueError("guildId must be a Discord snowflake")
            if parsed != override_id:
                raise ValueError("guildId must match the request header guild id")
        return override_id
    if guild_id and str(guild_id).strip():
        parsed = parse_snowflake(guild_id)
        if parsed is None:
            raise ValueError("guildId must be a Discord snowflake")
        if DEFAULT_GUILD_ID is not None and parsed != DEFAULT_GUILD_ID:
            raise ValueError("guildId must match configured DISCORD_GUILD_ID")
        return parsed
    if DEFAULT_GUILD_ID is not None:
        return DEFAULT_GUILD_ID
    raise ValueError("guildId is required")


async def get_guild_for_client(client: commands.Bot, guild_id: int) -> discord.Guild:
    guild = client.get_guild(guild_id)
    if guild is None:
        guild = await retry_read("fetch_guild", lambda: client.fetch_guild(guild_id))
        record_api_success("fetch_guild")
    if guild is None:
        raise ValueError("Discord server not found by guildId")
    return guild


async def get_guild(
    guild_id: str | None, client: commands.Bot | None = None
) -> discord.Guild:
    if client is None:
        client = await get_client()
    resolved_id = resolve_guild_id(guild_id)
    return await get_guild_for_client(client, resolved_id)


async def get_text_channel(
    channel_id: int | str, client: commands.Bot | None = None
) -> discord.TextChannel:
    if client is None:
        client = await get_client()
    resolved_id = parse_snowflake(channel_id)
    if resolved_id is None:
        raise ValueError("channelId cannot be null")
    channel = client.get_channel(resolved_id)
    if channel is None:
        channel = await retry_read(
            "fetch_channel", lambda: client.fetch_channel(resolved_id)
        )
        record_api_success("fetch_channel")
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("Channel not found by channelId")
    active_guild_id = get_active_guild_id()
    if active_guild_id is not None and channel.guild.id != active_guild_id:
        raise ValueError("channelId does not belong to configured DISCORD_GUILD_ID")
    return channel


async def get_message_target(channel_id: int | str):
    client = await get_client()
    resolved_id = parse_snowflake(channel_id)
    if resolved_id is None:
        raise ValueError("channelId cannot be null")
    channel = client.get_channel(resolved_id)
    if channel is None:
        channel = await retry_read(
            "fetch_channel", lambda: client.fetch_channel(resolved_id)
        )
        record_api_success("fetch_channel")
    if channel is None or not hasattr(channel, "send"):
        raise ValueError("Channel is not messageable")
    active_guild_id = get_active_guild_id()
    if getattr(channel, "guild", None) is None or (
        active_guild_id is not None and channel.guild.id != active_guild_id
    ):
        raise ValueError("channelId does not belong to configured DISCORD_GUILD_ID")
    return channel


def is_message_target_allowed(channel) -> bool:
    if ALLOW_ALL_CHANNELS:
        return True
    if not ALLOWED_CHANNEL_IDS:
        return False
    if isinstance(channel, discord.Thread):
        parent_id = channel.parent_id
        if parent_id:
            return is_write_allowed(parent_id)
        return False
    return is_write_allowed(channel.id)


async def get_dm_channel(user_id: str, client: commands.Bot | None = None) -> discord.DMChannel:
    if client is None:
        client = await get_client()
    if not user_id:
        raise ValueError("userId cannot be null")
    user = await retry_read("fetch_user", lambda: client.fetch_user(int(user_id)))
    if user is None:
        raise ValueError("User not found by userId")
    return await user.create_dm()


@mcp.tool()
async def get_server_info(guild_id: str = "") -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    try:
        guild = await get_guild(guild_id)
        owner_name = "unknown"
        if guild.owner is not None:
            owner_name = guild.owner.name
        elif guild.owner_id:
            try:
                owner_member = await retry_read(
                    "fetch_member", lambda: guild.fetch_member(guild.owner_id)
                )
                record_api_success("fetch_member")
                owner_name = owner_member.name
            except Exception:
                owner_name = f"ID {guild.owner_id}"
        creation_date = guild.created_at.astimezone(timezone.utc).date().isoformat()
        boost_count = guild.premium_subscription_count or 0
        boost_tier = str(guild.premium_tier)

        data = {
            "name": guild.name,
            "id": str(guild.id),
            "owner": owner_name,
            "created_on": creation_date,
            "member_count": guild.member_count,
            "channels": {
                "text": len(guild.text_channels),
                "voice": len(guild.voice_channels),
                "categories": len(guild.categories),
            },
            "boosts": {"count": boost_count, "tier": boost_tier},
        }
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=guild.id,
        )
        log_action("get_server_info", start_time, "ok", guild_id=guild.id)
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "get_server_info",
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=DEFAULT_GUILD_ID,
        )


@mcp.tool()
async def discord_health_check(guild_id: str = "") -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = list(CONFIG_WARNINGS)
    capabilities = {}
    guild_info = {"id": None, "name": None, "found": False}
    bot_info = {"user": None, "application": None}
    read_ok = False
    write_ok = False
    discord_config = {
        "primary_channel_id": (
            str(PRIMARY_CHANNEL_ID) if PRIMARY_CHANNEL_ID is not None else None
        ),
        "write_allowed_channel_ids": (
            ["ALL"]
            if ALLOW_ALL_CHANNELS
            else [str(cid) for cid in sorted(ALLOWED_CHANNEL_IDS)]
        ),
        "blocked_channel_ids": [
            str(cid) for cid in sorted(get_active_blocked_channel_ids())
        ],
        "allow_all_read": DISCORD_ALLOW_ALL_READ,
        "admin_tools_enabled": MCP_ADMIN_TOOLS_ENABLED,
        "dm_enabled": DISCORD_DM_ENABLED,
        "log_redact_message_content": LOG_REDACT_MESSAGE_CONTENT,
        "audit_timezone": DISCORD_AUDIT_TIMEZONE_NAME,
        "protected_user_ids_count": len(PROTECTED_USER_IDS),
        "protected_role_ids_count": len(PROTECTED_ROLE_IDS),
        "allowed_target_role_ids_count": len(ALLOWED_TARGET_ROLE_IDS),
        "allow_request_overrides": ALLOW_REQUEST_OVERRIDES,
        "confirm_required": CONFIRM_REQUIRED,
        "openai_vision_enabled": OPENAI_VISION_ENABLED,
        "openai_vision_model": OPENAI_VISION_MODEL,
        "openai_vision_api_url": OPENAI_VISION_API_URL,
        "openai_vision_max_mb": OPENAI_VISION_MAX_MB,
        "openai_vision_timeout_seconds": OPENAI_VISION_TIMEOUT_SECONDS,
        "openai_api_header": MCP_OPENAI_API_HEADER,
        "require_request_discord_token": REQUIRE_REQUEST_DISCORD_TOKEN,
        "require_request_guild_id": REQUIRE_REQUEST_GUILD_ID,
        "require_request_blocked_channels": REQUIRE_REQUEST_BLOCKED_CHANNELS,
        "request_header_names": {
            "discord_token": REQUEST_DISCORD_TOKEN_HEADER,
            "guild_id": REQUEST_DISCORD_GUILD_ID_HEADER,
            "blocked_channels": REQUEST_DISCORD_BLOCKED_CHANNELS_HEADER,
            "openai_api_key": REQUEST_OPENAI_API_HEADER,
        },
        "bot_pool_ttl_seconds": BOT_POOL_TTL_SECONDS,
        "max_embed_chars": 4096,
        "max_message_chars": 2000,
        "thread_ops_enabled": True,
        "health_check_sample_limit": HEALTH_CHECK_SAMPLE_LIMIT,
        "channel_cache_ttl_seconds": CHANNEL_CACHE_TTL_SECONDS,
        "job_ttl_seconds": JOB_TTL_SECONDS,
    }

    if PRIMARY_CHANNEL_ID is not None and ALLOWED_CHANNEL_IDS and (
        PRIMARY_CHANNEL_ID not in ALLOWED_CHANNEL_IDS
    ):
        warnings.append(
            "DISCORD_PRIMARY_CHANNEL_ID is not in DISCORD_ALLOWED_CHANNEL_IDS; "
            "primary channel will still be used for defaults."
        )
    if not ALLOW_ALL_CHANNELS and not ALLOWED_CHANNEL_IDS:
        warnings.append("DISCORD_ALLOWED_CHANNEL_IDS is configured but empty; writes restricted.")
    if not DISCORD_ALLOW_ALL_READ and not ALLOWED_CHANNEL_IDS and not ALLOW_ALL_CHANNELS:
        warnings.append(
            "DISCORD_ALLOW_ALL_READ is false and no allowlist is configured; reads are restricted."
        )
    if PRIMARY_CHANNEL_ID in get_active_blocked_channel_ids():
        warnings.append("DISCORD_PRIMARY_CHANNEL_ID is blocked by DISCORD_BLOCKED_CHANNEL_IDS.")

    try:
        client = await get_client()
        if client.user:
            bot_info["user"] = {
                "id": str(client.user.id),
                "name": client.user.name,
                "discriminator": client.user.discriminator,
                "global_name": client.user.global_name,
            }
        else:
            warnings.append("Bot user is not ready yet.")
        try:
            app_info = await client.application_info()
            record_api_success("application_info")
            bot_info["application"] = {
                "id": str(app_info.id),
                "name": app_info.name,
            }
        except Exception:
            warnings.append("Unable to fetch application info.")

        guild = await get_guild(guild_id)
        guild_info = {
            "id": str(guild.id),
            "name": guild.name,
            "found": True,
        }

        read_sample = effective_allowed_channel_ids(guild, for_write=False)
        write_sample = effective_allowed_channel_ids(guild, for_write=True)
        sample_ids = sorted(set(read_sample) | set(write_sample))
        if not sample_ids:
            warnings.append("No channels available for capability checks.")

        for channel_id in sample_ids:
            try:
                channel = await get_text_channel(channel_id)
                member = await get_bot_member(channel.guild)
                perms = (
                    channel.permissions_for(member)
                    if member is not None
                    else discord.Permissions.none()
                )
                caps = channel_capabilities(perms)
                capabilities[str(channel_id)] = {"found": True, **caps}

                if channel_id in read_sample and caps["read_history"]:
                    read_ok = True
                if channel_id in write_sample and caps["read_history"] and caps["send"]:
                    write_ok = True

                if channel_id == PRIMARY_CHANNEL_ID and (
                    not caps["read_history"] or not caps["send"]
                ):
                    warnings.append(
                        f"Primary channel {channel_id} lacks read_history/send permission."
                    )
                if channel_id in write_sample and (
                    not caps["read_history"] or not caps["send"]
                ):
                    warnings.append(
                        f"Write channel {channel_id} lacks read_history/send permission."
                    )
                if channel_id in read_sample and not caps["read_history"]:
                    warnings.append(
                        f"Read channel {channel_id} lacks read_history permission."
                    )
            except Exception as exc:
                capabilities[str(channel_id)] = {"found": False, "error": str(exc)}
                warnings.append(f"Channel {channel_id} unavailable: {exc}")

        write_required = bool(ALLOW_ALL_CHANNELS or ALLOWED_CHANNEL_IDS or PRIMARY_CHANNEL_ID)
        ok = read_ok and (not write_required or write_ok)
        if ok and not warnings:
            status = "green"
        elif ok:
            status = "yellow"
        else:
            status = "red"

        data = {
            "status": status,
            "healthy": ok,
            "warnings": warnings,
            "bot": bot_info,
            "guild": guild_info,
            "discord_config": discord_config,
            "capabilities": capabilities,
            "last_successful_api_at": LAST_SUCCESSFUL_API_AT,
        }
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=guild.id,
        )
        log_action("discord_health_check", start_time, "ok", guild_id=guild.id)
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "discord_health_check",
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=DEFAULT_GUILD_ID,
        )


@mcp.tool()
async def discord_ack(
    channel_id: str = "",
    message: str = "",
    include_timestamp: bool | str = True,
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    resolved_channel_id = None
    warnings = []
    diagnostics = {}
    try:
        include_timestamp = parse_bool(include_timestamp)
        resolved_channel_id = resolve_channel_id(channel_id)
        ack_message = message.strip() if message else "On it - running checks now."
        if include_timestamp:
            ack_message = f"{ack_message} ({datetime.now(timezone.utc).isoformat()})"

        target = await get_message_target(resolved_channel_id)
        allowed = is_message_target_allowed(target)
        diagnostics = {
            "resolved_channel_id": str(resolved_channel_id),
            "allowed_channel": allowed,
        }
        if not allowed:
            error = build_error(
                "permission_denied",
                "Channel is not in the write allowlist.",
                required_perms=["allowlisted_channel"],
                diagnostics=diagnostics,
            )
            return error_with_log(
                "discord_ack",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )

        sent = await target.send(ack_message)
        record_api_success("discord_ack")
        log_action(
            "discord_ack",
            start_time,
            "ok",
            channel_id=resolved_channel_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=target.guild.id,
            channel_id=target.id,
            thread_id=target.id if isinstance(target, discord.Thread) else None,
        )
        data = {
            "channel_id": str(target.id),
            "message_id": str(sent.id),
            "jump_url": sent.jump_url,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "discord_ack",
            start_time,
            request_id,
            error,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )


@mcp.tool()
async def send_message(
    channel_id: str = "",
    message: str = "",
    embed_title: str = "",
    embed_description: str = "",
    embed_color: str = "",
    dry_run: bool | str = False,
    thread_if_split: bool | str = False,
    thread_name: str = "",
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    resolved_channel_id = None
    warnings = []
    diagnostics = {}
    try:
        dry_run = parse_bool(dry_run)
        resolved_channel_id = resolve_channel_id(channel_id)

        if not message and not embed_title and not embed_description:
            error = build_error(
                "invalid_payload",
                "message or embed content must be provided.",
            )
            return error_with_log(
                "send_message",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )

        diagnostics = {
            "resolved_channel_id": str(resolved_channel_id),
            "allowed_channel": is_write_allowed(resolved_channel_id),
        }
        allow_error = require_write_allowed(
            resolved_channel_id,
            "send_message",
            start_time,
            request_id,
            warnings=warnings,
            diagnostics=diagnostics,
        )
        if allow_error:
            return allow_error

        thread_if_split = parse_bool(thread_if_split)
        thread_name = (thread_name or "").strip()
        has_embed = bool(embed_title or embed_description or embed_color)
        channel = None
        perms = None
        caps = None
        try:
            channel = await get_text_channel(resolved_channel_id)
            member = await get_bot_member(channel.guild)
            perms = (
                channel.permissions_for(member)
                if member is not None
                else discord.Permissions.none()
            )
            caps = channel_capabilities(perms)
            diagnostics["permissions"] = caps
        except Exception as exc:
            if dry_run:
                warnings.append(f"Dry-run skipped channel lookup: {exc}")
                diagnostics["channel_lookup_error"] = str(exc)
                caps = {"create_threads": False}
            else:
                raise

        if perms is not None and not perms.send_messages:
            error = build_error(
                "permission_denied",
                "Missing permission to send messages.",
                required_perms=["send_messages"],
                diagnostics=diagnostics,
            )
            return error_with_log(
                "send_message",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=channel.guild.id if channel else None,
                channel_id=resolved_channel_id,
            )
        embed_title_length = len(embed_title) if embed_title else 0
        embed_description_length = len(embed_description) if embed_description else 0
        content_length = len(message) if message else 0
        content_parts = split_text(message, 2000) if message else []
        embed_parts = split_text(embed_description, 4096) if embed_description else []
        planned_parts = max(len(content_parts), len(embed_parts), 1)
        will_split = planned_parts > 1
        diagnostics.update(
            {
                "content_length": content_length,
                "content_limit": 2000,
                "embed_title_length": embed_title_length,
                "embed_title_limit": 256,
                "embed_description_length": embed_description_length,
                "embed_description_limit": 4096,
                "embeds_allowed": perms.embed_links if perms is not None else None,
                "planned_parts": planned_parts,
                "will_split": will_split,
                "thread_if_split": thread_if_split,
            }
        )

        if embed_title_length > 256:
            error = build_error(
                "invalid_payload",
                "embed_title exceeds 256 characters.",
                diagnostics=diagnostics,
            )
            return error_with_log(
                "send_message",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=channel.guild.id if channel else get_active_guild_id(),
                channel_id=resolved_channel_id,
            )
        if has_embed and perms is not None and not perms.embed_links:
            error = build_error(
                "permission_denied",
                "Missing permission to embed links.",
                required_perms=["embed_links"],
                diagnostics=diagnostics,
            )
            return error_with_log(
                "send_message",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=channel.guild.id if channel else get_active_guild_id(),
                channel_id=resolved_channel_id,
            )

        color_value = None
        if embed_color:
            color_value = parse_int(embed_color)
            if color_value is None:
                error = build_error(
                    "invalid_payload",
                    "embed_color must be an integer.",
                    diagnostics=diagnostics,
                )
                return error_with_log(
                    "send_message",
                    start_time,
                    request_id,
                    error,
                    warnings=warnings,
                    guild_id=channel.guild.id if channel else get_active_guild_id(),
                    channel_id=resolved_channel_id,
                )

        parts = []
        for idx in range(planned_parts):
            content_part = content_parts[idx] if idx < len(content_parts) else ""
            embed_title_part = ""
            embed_desc_part = ""
            include_embed = False
            if embed_parts:
                if idx < len(embed_parts):
                    embed_desc_part = embed_parts[idx]
                    embed_title_part = embed_title
                    include_embed = bool(embed_title_part or embed_desc_part or embed_color)
            else:
                if idx == 0 and has_embed:
                    embed_title_part = embed_title
                    embed_desc_part = embed_description
                    include_embed = True
            parts.append(
                {
                    "content": content_part,
                    "embed_title": embed_title_part,
                    "embed_description": embed_desc_part,
                    "include_embed": include_embed,
                }
            )

        can_create_threads = bool(caps and caps.get("create_threads"))
        thread_planned = thread_if_split and will_split and can_create_threads
        if thread_if_split and will_split and not can_create_threads:
            warnings.append("create_threads permission missing or unavailable; falling back to channel.")
        if thread_planned and not thread_name:
            if embed_title:
                thread_name = embed_title.strip()
            elif message:
                thread_name = "MCP continuation"
            else:
                thread_name = "MCP continuation"
        if thread_name and len(thread_name) > 90:
            thread_name = thread_name[:90]

        split_strategy = "thread" if thread_planned else "channel"
        parts_plan = [
            {
                "index": idx + 1,
                "content_length": len(part["content"]) if part["content"] else 0,
                "embed_description_length": len(part["embed_description"])
                if part["embed_description"]
                else 0,
                "has_embed": part["include_embed"],
            }
            for idx, part in enumerate(parts)
        ]
        diagnostics.update(
            {
                "split_strategy": split_strategy,
                "thread_planned": thread_planned,
                "thread_name": thread_name or None,
                "parts": parts_plan,
            }
        )

        if dry_run:
            log_action(
                "send_message",
                start_time,
                "ok",
                channel_id=resolved_channel_id,
            )
            meta = build_meta(
                start_time,
                request_id=request_id,
                warnings=warnings,
                guild_id=channel.guild.id if channel else get_active_guild_id(),
                channel_id=resolved_channel_id,
            )
            data = {
                "dry_run": True,
                "channel_id": str(channel.id) if channel else str(resolved_channel_id),
                "diagnostics": diagnostics,
            }
            return success_response(data, meta)

        sent_message_ids = []
        sent_message = None
        thread_id = None
        target_channel = channel
        for idx, part in enumerate(parts):
            embed = None
            if part["include_embed"]:
                embed = discord.Embed(
                    title=part["embed_title"] or None,
                    description=part["embed_description"] or None,
                )
                if color_value is not None:
                    embed.color = discord.Color(color_value)
            if idx == 0:
                sent_message = await channel.send(
                    content=part["content"] or None,
                    embed=embed,
                )
                sent_message_ids.append(str(sent_message.id))
                if thread_planned and sent_message is not None:
                    try:
                        thread = await sent_message.create_thread(
                            name=thread_name or "MCP continuation"
                        )
                        record_api_success("create_thread")
                        thread_id = str(thread.id)
                        target_channel = thread
                    except Exception as exc:
                        warnings.append(f"Thread creation failed: {exc}")
                        thread_planned = False
                        target_channel = channel
            else:
                sent = await target_channel.send(
                    content=part["content"] or None,
                    embed=embed,
                )
                sent_message_ids.append(str(sent.id))

        record_api_success("send_message")
        log_action(
            "send_message",
            start_time,
            "ok",
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
            thread_id=thread_id,
        )
        data = {
            "channel_id": str(channel.id),
            "message_id": str(sent_message.id) if sent_message else None,
            "sent_message_ids": sent_message_ids,
            "thread_id": thread_id,
            "jump_url": sent_message.jump_url if sent_message else None,
            "planned_parts": planned_parts,
            "diagnostics": diagnostics,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc, diagnostics=diagnostics)
        return error_with_log(
            "send_message",
            start_time,
            request_id,
            error,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )


@mcp.tool()
async def discord_smoke_test(
    channel_id: str = "",
    include_admin: bool | str = True,
    debug: bool | str = False,
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    confirm_error = require_confirm(
        confirm,
        "discord_smoke_test",
        start_time,
        request_id,
        warnings=warnings,
        channel_id=parse_snowflake(channel_id) if channel_id else None,
    )
    if confirm_error:
        return confirm_error
    include_admin = parse_bool(include_admin)
    debug = parse_bool(debug)
    report = {
        "ok": True,
        "steps": [],
        "message_id": None,
        "channel_id": None,
    }

    init_errors = []
    client = None
    for attempt in range(2):
        try:
            client = await ensure_client_ready()
            break
        except Exception as exc:
            init_errors.append(str(exc))
            if attempt == 0:
                await reset_bot("smoke_test_init_retry")
    client_snapshot = get_client_debug_snapshot() if debug else None
    report["steps"].append(
        {
            "name": "client_init",
            "ok": client is not None,
            "errors": init_errors,
            "debug": client_snapshot,
        }
    )
    if client is None:
        report["ok"] = False
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=DEFAULT_GUILD_ID,
        )
        log_action("discord_smoke_test", start_time, "ok", guild_id=DEFAULT_GUILD_ID)
        return success_response(report, meta)

    health = await discord_health_check()
    health_data = health.get("data", {}) if isinstance(health, dict) else {}
    health_ok = health_data.get("healthy")
    if health_ok is False and any(
        "properly initialised" in warning for warning in health_data.get("warnings", [])
    ):
        await reset_bot("smoke_test_health_retry")
        await ensure_client_ready()
        health = await discord_health_check()
        health_data = health.get("data", {}) if isinstance(health, dict) else {}
        health_ok = health_data.get("healthy")
    report["steps"].append(
        {
            "name": "health_check",
            "ok": bool(health_ok),
            "status": health_data.get("status"),
            "warnings": health_data.get("warnings", []),
        }
    )
    if not health_ok:
        report["ok"] = False

    test_message = f"MCP smoke test {datetime.now(timezone.utc).isoformat()}"
    dry_run = await send_message(
        channel_id=channel_id,
        message=test_message,
        dry_run=True,
        confirm=CONFIRM_APPLY_VALUE,
    )
    report["steps"].append(
        {
            "name": "dry_run_send",
            "ok": bool(dry_run.get("ok")),
            "details": dry_run,
        }
    )
    if not dry_run.get("ok"):
        report["ok"] = False
        report["duration_ms"] = int((time.perf_counter() - start_time) * 1000)
        return report

    send = await send_message(
        channel_id=channel_id,
        message=test_message,
        dry_run=False,
        confirm=CONFIRM_APPLY_VALUE,
    )
    report["steps"].append(
        {
            "name": "real_send",
            "ok": bool(send.get("ok")),
            "details": send,
        }
    )
    if not send.get("ok"):
        report["ok"] = False
        report["duration_ms"] = int((time.perf_counter() - start_time) * 1000)
        return report

    send_data = send.get("data", {}) if isinstance(send, dict) else {}
    report["channel_id"] = send_data.get("channel_id")
    message_id = send_data.get("message_id")
    if not message_id and send_data.get("sent_message_ids"):
        message_id = send_data.get("sent_message_ids")[0]
    report["message_id"] = message_id

    should_delete = False
    if include_admin and MCP_ADMIN_TOOLS_ENABLED and message_id:
        edit = await edit_message(
            channel_id=report["channel_id"] or channel_id,
            message_id=message_id,
            new_message=f"{test_message} (edited)",
            confirm=CONFIRM_APPLY_VALUE,
        )
        report["steps"].append(
            {
                "name": "edit_message",
                "ok": bool(edit.get("ok")),
                "details": edit,
            }
        )
        if not edit.get("ok"):
            report["ok"] = False
        should_delete = True
    else:
        report["steps"].append(
            {
                "name": "admin_steps",
                "ok": True,
                "details": {
                    "skipped": True,
                    "reason": "Admin tools disabled or message_id missing.",
                },
            }
        )

    if message_id:
        read = await read_messages(
            channel_id=report["channel_id"] or channel_id, count="5"
        )
        found = False
        if read.get("ok"):
            messages = read.get("data", {}).get("messages", [])
            for msg in messages:
                if msg.get("id") == message_id:
                    found = True
                    break
        report["steps"].append(
            {
                "name": "read_recent",
                "ok": bool(read.get("ok")),
                "found_message": found,
                "details": read,
            }
        )
        if not read.get("ok") or not found:
            report["ok"] = False
    if should_delete:
        delete = await delete_message(
            channel_id=report["channel_id"] or channel_id,
            message_id=message_id,
            confirm=CONFIRM_APPLY_VALUE,
        )
        report["steps"].append(
            {
                "name": "delete_message",
                "ok": bool(delete.get("ok")),
                "details": delete,
            }
        )
        if not delete.get("ok"):
            report["ok"] = False

    meta = build_meta(
        start_time,
        request_id=request_id,
        warnings=warnings,
        guild_id=DEFAULT_GUILD_ID,
    )
    log_action("discord_smoke_test", start_time, "ok", guild_id=DEFAULT_GUILD_ID)
    return success_response(report, meta)


@mcp.tool()
async def discord_job_submit(action: str, params: dict | None = None) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    if not action:
        meta = build_meta(start_time, request_id=request_id, warnings=warnings)
        return error_response("invalid_payload", "action cannot be null.", meta)
    if params is None:
        params = {}
    if not isinstance(params, dict):
        meta = build_meta(start_time, request_id=request_id, warnings=warnings)
        return error_response("invalid_payload", "params must be an object.", meta)

    action_map = {
        "discord_health_check": discord_health_check,
        "discord_smoke_test": discord_smoke_test,
        "discord_ack": discord_ack,
        "get_server_info": get_server_info,
        "list_channels": list_channels,
        "find_channel": find_channel,
        "read_messages": read_messages,
        "search_messages": search_messages,
        "list_threads": list_threads,
        "send_message": send_message,
        "channel_daily_audit": channel_daily_audit,
    }
    action_func = action_map.get(action)
    if action_func is None:
        meta = build_meta(start_time, request_id=request_id, warnings=warnings)
        return error_response(
            "invalid_payload",
            "Unsupported action.",
            meta,
            diagnostics={"supported_actions": sorted(action_map.keys())},
        )

    job_id = str(uuid.uuid4())
    now = time.time()
    job = {
        "task_id": job_id,
        "action": action,
        "status": "queued",
        "created_at": job_timestamp(),
        "created_at_ts": now,
        "params": params,
        "result": None,
        "error": None,
        "started_at": None,
        "finished_at": None,
        "finished_at_ts": None,
    }
    async with JOB_LOCK:
        await prune_jobs_locked(now)
        JOB_STORE[job_id] = job
        task = asyncio.create_task(run_job(job_id, action, action_func, params))
        JOB_TASKS[job_id] = task

    log_action("discord_job_submit", start_time, "ok")
    meta = build_meta(start_time, request_id=request_id, warnings=warnings)
    data = {"task_id": job_id, "status": "queued", "action": action}
    return success_response(data, meta)


@mcp.tool()
async def discord_job_status(
    task_id: str,
    include_result: bool | str = False,
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    include_result = parse_bool(include_result)
    if not task_id:
        meta = build_meta(start_time, request_id=request_id, warnings=warnings)
        return error_response("invalid_payload", "task_id cannot be null.", meta)
    async with JOB_LOCK:
        await prune_jobs_locked()
        job = JOB_STORE.get(task_id)
        if job is None:
            meta = build_meta(start_time, request_id=request_id, warnings=warnings)
            return error_response("not_found", "Job not found.", meta)
        snapshot = build_job_snapshot(job, include_result)

    log_action("discord_job_status", start_time, "ok")
    meta = build_meta(start_time, request_id=request_id, warnings=warnings)
    return success_response(snapshot, meta)


@mcp.tool()
async def edit_message(
    channel_id: str = "",
    message_id: str = "",
    new_message: str = "",
    confirm: str = "",
    dry_run: bool | str = False,
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    resolved_channel_id = None
    warnings = []
    diagnostics = {}
    try:
        dry_run = parse_bool(dry_run)
        if not MCP_ADMIN_TOOLS_ENABLED:
            error = build_error(
                "permission_denied",
                "MCP_ADMIN_TOOLS_ENABLED must be true to edit messages.",
                required_perms=["MCP_ADMIN_TOOLS_ENABLED", f"confirm={CONFIRM_APPLY_VALUE}"],
            )
            return error_with_log("edit_message", start_time, request_id, error)
        confirm_error = require_confirm(
            confirm,
            "edit_message",
            start_time,
            request_id,
            warnings=warnings,
        )
        if confirm_error:
            return confirm_error
        if not message_id:
            error = build_error("invalid_payload", "messageId cannot be null.")
            return error_with_log("edit_message", start_time, request_id, error)
        if not new_message:
            error = build_error("invalid_payload", "newMessage cannot be null.")
            return error_with_log("edit_message", start_time, request_id, error)

        resolved_channel_id = resolve_channel_id(channel_id)
        diagnostics = {
            "resolved_channel_id": str(resolved_channel_id),
            "allowed_channel": is_write_allowed(resolved_channel_id),
            "content_length": len(new_message),
            "content_limit": 2000,
        }

        allow_error = require_write_allowed(
            resolved_channel_id,
            "edit_message",
            start_time,
            request_id,
            warnings=warnings,
            diagnostics=diagnostics,
        )
        if allow_error:
            return allow_error

        client = await ensure_client_ready()
        channel = await get_text_channel(resolved_channel_id, client)
        member = await get_bot_member(channel.guild)
        perms = (
            channel.permissions_for(member)
            if member is not None
            else discord.Permissions.none()
        )
        diagnostics["permissions"] = channel_capabilities(perms)

        if len(new_message) > 2000:
            error = build_error(
                "invalid_payload",
                "newMessage exceeds 2000 characters.",
                diagnostics=diagnostics,
            )
            return error_with_log(
                "edit_message",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )

        msg = await retry_read(
            "fetch_message", lambda: channel.fetch_message(int(message_id))
        )
        if msg is None:
            error = build_error(
                "not_found",
                "Message not found by messageId.",
                diagnostics=diagnostics,
            )
            return error_with_log(
                "edit_message",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )

        if client.user and msg.author.id != client.user.id and not perms.manage_messages:
            error = build_error(
                "permission_denied",
                "Missing permission to manage messages.",
                required_perms=["manage_messages"],
                diagnostics=diagnostics,
            )
            return error_with_log(
                "edit_message",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )

        if dry_run:
            log_action(
                "edit_message",
                start_time,
                "ok",
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )
            meta = build_meta(
                start_time,
                request_id=request_id,
                warnings=warnings,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )
            data = {"dry_run": True, "diagnostics": diagnostics}
            return success_response(data, meta)

        edited = await msg.edit(content=new_message)
        record_api_success("edit_message")
        log_action(
            "edit_message",
            start_time,
            "ok",
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        data = {
            "channel_id": str(channel.id),
            "message_id": str(edited.id),
            "jump_url": edited.jump_url,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc, diagnostics=diagnostics if diagnostics else None)
        return error_with_log(
            "edit_message",
            start_time,
            request_id,
            error,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )


@mcp.tool()
async def delete_message(
    channel_id: str = "",
    message_id: str = "",
    confirm: str = "",
    dry_run: bool | str = False,
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    resolved_channel_id = None
    warnings = []
    diagnostics = {}
    try:
        dry_run = parse_bool(dry_run)
        if not MCP_ADMIN_TOOLS_ENABLED:
            error = build_error(
                "permission_denied",
                "MCP_ADMIN_TOOLS_ENABLED must be true to delete messages.",
                required_perms=["MCP_ADMIN_TOOLS_ENABLED", f"confirm={CONFIRM_APPLY_VALUE}"],
            )
            return error_with_log("delete_message", start_time, request_id, error)
        confirm_error = require_confirm(
            confirm,
            "delete_message",
            start_time,
            request_id,
            warnings=warnings,
        )
        if confirm_error:
            return confirm_error
        if not message_id:
            error = build_error("invalid_payload", "messageId cannot be null.")
            return error_with_log("delete_message", start_time, request_id, error)

        resolved_channel_id = resolve_channel_id(channel_id)
        diagnostics = {
            "resolved_channel_id": str(resolved_channel_id),
            "allowed_channel": is_write_allowed(resolved_channel_id),
        }

        allow_error = require_write_allowed(
            resolved_channel_id,
            "delete_message",
            start_time,
            request_id,
            warnings=warnings,
            diagnostics=diagnostics,
        )
        if allow_error:
            return allow_error

        channel = await get_text_channel(resolved_channel_id)
        client = await get_client()
        member = await get_bot_member(channel.guild)
        perms = (
            channel.permissions_for(member)
            if member is not None
            else discord.Permissions.none()
        )
        diagnostics["permissions"] = channel_capabilities(perms)

        msg = await retry_read(
            "fetch_message", lambda: channel.fetch_message(int(message_id))
        )
        if msg is None:
            error = build_error(
                "not_found",
                "Message not found by messageId.",
                diagnostics=diagnostics,
            )
            return error_with_log(
                "delete_message",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )

        if client.user and msg.author.id != client.user.id and not perms.manage_messages:
            error = build_error(
                "permission_denied",
                "Missing permission to manage messages.",
                required_perms=["manage_messages"],
                diagnostics=diagnostics,
            )
            return error_with_log(
                "delete_message",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )

        if dry_run:
            log_action(
                "delete_message",
                start_time,
                "ok",
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )
            meta = build_meta(
                start_time,
                request_id=request_id,
                warnings=warnings,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )
            data = {"dry_run": True, "diagnostics": diagnostics}
            return success_response(data, meta)

        await msg.delete()
        record_api_success("delete_message")
        log_action(
            "delete_message",
            start_time,
            "ok",
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        data = {"channel_id": str(channel.id), "message_id": str(msg.id)}
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc, diagnostics=diagnostics)
        return error_with_log(
            "delete_message",
            start_time,
            request_id,
            error,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )


@mcp.tool()
async def read_messages(
    channel_id: str = "",
    count: str = "",
    before_message_id: str = "",
    after_message_id: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    resolved_channel_id = None
    warnings = []
    try:
        resolved_channel_id = resolve_channel_id(channel_id)
        channel = await get_message_target(resolved_channel_id)
        read_channel_id = (
            channel.parent_id
            if isinstance(channel, discord.Thread) and channel.parent_id
            else channel.id
        )
        allow_error = require_read_allowed(
            read_channel_id,
            "read_messages",
            start_time,
            request_id,
            warnings=warnings,
        )
        if allow_error:
            return allow_error
        limit = parse_int(count, DEFAULT_READ_LIMIT)
        if limit is None or limit <= 0:
            error = build_error("invalid_payload", "count must be a positive integer.")
            return error_with_log(
                "read_messages",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )
        if limit > MAX_READ_LIMIT:
            limit = MAX_READ_LIMIT
        before_id = None
        if before_message_id:
            before_id = parse_snowflake(before_message_id)
            if before_id is None:
                error = build_error(
                    "invalid_payload", "before_message_id must be a Discord snowflake."
                )
                return error_with_log(
                    "read_messages",
                    start_time,
                    request_id,
                    error,
                    warnings=warnings,
                    channel_id=resolved_channel_id,
                )
        after_id = None
        if after_message_id:
            after_id = parse_snowflake(after_message_id)
            if after_id is None:
                error = build_error(
                    "invalid_payload", "after_message_id must be a Discord snowflake."
                )
                return error_with_log(
                    "read_messages",
                    start_time,
                    request_id,
                    error,
                    warnings=warnings,
                    channel_id=resolved_channel_id,
                )
        before_obj = discord.Object(id=before_id) if before_id else None
        after_obj = discord.Object(id=after_id) if after_id else None

        async def fetch_history():
            return [
                m
                async for m in channel.history(
                    limit=limit, before=before_obj, after=after_obj
                )
            ]

        messages = await retry_read("read_messages", fetch_history)
        record_api_success("read_messages")
        payload = [
            {
                "id": str(msg.id),
                "author": {
                    "id": str(msg.author.id),
                    "name": msg.author.name,
                },
                "created_at": msg.created_at.isoformat(),
                "content": msg.content,
                "embed_text": extract_embed_text(msg.embeds),
                "content_with_embeds": get_message_text(msg),
                "embeds": serialize_embeds(msg.embeds),
                "jump_url": msg.jump_url,
                "attachments_count": len(msg.attachments),
                "has_attachments": bool(msg.attachments),
                "has_links": bool(LINK_RE.search(get_message_text(msg))),
                "has_embeds": bool(msg.embeds),
            }
            for msg in messages
        ]
        log_action(
            "read_messages",
            start_time,
            "ok",
            guild_id=channel.guild.id if getattr(channel, "guild", None) else DEFAULT_GUILD_ID,
            channel_id=resolved_channel_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=channel.guild.id if getattr(channel, "guild", None) else DEFAULT_GUILD_ID,
            channel_id=resolved_channel_id,
        )
        data = {
            "channel_id": str(channel.id),
            "count": len(messages),
            "before_message_id": str(before_id) if before_id else None,
            "after_message_id": str(after_id) if after_id else None,
            "messages": payload,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "read_messages",
            start_time,
            request_id,
            error,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )


@mcp.tool()
async def search_messages(
    channel_id: str = "",
    limit: str = "",
    before_message_id: str = "",
    after_message_id: str = "",
    date_from: str = "",
    date_to: str = "",
    query: str = "",
    author_id: str = "",
    has_link: bool | str = False,
    has_file: bool | str = False,
    include_threads: bool | str = False,
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    resolved_channel_id = None
    try:
        resolved_channel_id = resolve_channel_id(channel_id)
        channel = await get_message_target(resolved_channel_id)
        read_channel_id = (
            channel.parent_id
            if isinstance(channel, discord.Thread) and channel.parent_id
            else channel.id
        )
        allow_error = require_read_allowed(
            read_channel_id,
            "search_messages",
            start_time,
            request_id,
            warnings=warnings,
        )
        if allow_error:
            return allow_error

        limit_value = parse_int(limit, DEFAULT_READ_LIMIT)
        if limit_value is None or limit_value <= 0:
            error = build_error("invalid_payload", "limit must be a positive integer.")
            return error_with_log(
                "search_messages",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )
        if limit_value > MAX_READ_LIMIT:
            limit_value = MAX_READ_LIMIT

        before_id = parse_snowflake(before_message_id) if before_message_id else None
        if before_message_id and before_id is None:
            error = build_error(
                "invalid_payload", "before_message_id must be a Discord snowflake."
            )
            return error_with_log(
                "search_messages",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )
        after_id = parse_snowflake(after_message_id) if after_message_id else None
        if after_message_id and after_id is None:
            error = build_error(
                "invalid_payload", "after_message_id must be a Discord snowflake."
            )
            return error_with_log(
                "search_messages",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )

        tz = AUDIT_TIMEZONE
        after_dt = parse_datetime_param(date_from, tz)
        before_dt = parse_datetime_param(date_to, tz)
        if after_dt and before_dt and after_dt > before_dt:
            error = build_error(
                "invalid_payload", "date_from must be earlier than date_to."
            )
            return error_with_log(
                "search_messages",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )

        author_parsed = parse_snowflake(author_id) if author_id else None
        if author_id and author_parsed is None:
            error = build_error(
                "invalid_payload", "author_id must be a Discord snowflake."
            )
            return error_with_log(
                "search_messages",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )

        has_link = parse_bool(has_link)
        has_file = parse_bool(has_file)
        include_threads = parse_bool(include_threads)

        before_obj = discord.Object(id=before_id) if before_id else None
        after_obj = discord.Object(id=after_id) if after_id else None
        history_before = before_obj or before_dt
        history_after = after_obj or after_dt

        async def fetch_history(target_channel, limit_count):
            return [
                m
                async for m in target_channel.history(
                    limit=limit_count,
                    before=history_before,
                    after=history_after,
                )
            ]

        messages = await retry_read(
            "search_messages", lambda: fetch_history(channel, limit_value)
        )

        if include_threads and isinstance(channel, discord.TextChannel):
            remaining = max(limit_value - len(messages), 0)
            if remaining > 0:
                threads = []
                if hasattr(channel, "active_threads"):
                    active_threads = await channel.active_threads()
                    if isinstance(active_threads, tuple):
                        active_threads = active_threads[0]
                    threads.extend(active_threads)
                elif hasattr(channel, "threads"):
                    threads.extend(list(channel.threads))
                for thread in threads:
                    if remaining <= 0:
                        break
                    thread_messages = await retry_read(
                        "search_thread_messages",
                        lambda: fetch_history(thread, remaining),
                    )
                    messages.extend(thread_messages)
                    remaining = max(limit_value - len(messages), 0)

        def message_matches(msg) -> bool:
            if author_parsed and msg.author.id != author_parsed:
                return False
            if after_dt and msg.created_at < after_dt:
                return False
            if before_dt and msg.created_at > before_dt:
                return False
            content = get_message_text(msg)
            if query and query.lower() not in content.lower():
                return False
            if has_link and not LINK_RE.search(content):
                return False
            if has_file and not msg.attachments:
                return False
            return True

        filtered = [msg for msg in messages if message_matches(msg)]
        payload = [
            {
                "id": str(msg.id),
                "author": {"id": str(msg.author.id), "name": msg.author.name},
                "created_at": msg.created_at.isoformat(),
                "content": msg.content,
                "embed_text": extract_embed_text(msg.embeds),
                "content_with_embeds": get_message_text(msg),
                "embeds": serialize_embeds(msg.embeds),
                "jump_url": msg.jump_url,
                "channel_id": str(msg.channel.id),
                "thread_id": str(msg.channel.id)
                if isinstance(msg.channel, discord.Thread)
                else None,
                "has_links": bool(LINK_RE.search(get_message_text(msg))),
                "attachments_count": len(msg.attachments),
                "has_embeds": bool(msg.embeds),
            }
            for msg in filtered
        ]

        record_api_success("search_messages")
        log_action(
            "search_messages",
            start_time,
            "ok",
            guild_id=channel.guild.id if getattr(channel, "guild", None) else DEFAULT_GUILD_ID,
            channel_id=resolved_channel_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=channel.guild.id if getattr(channel, "guild", None) else DEFAULT_GUILD_ID,
            channel_id=resolved_channel_id,
        )
        data = {
            "channel_id": str(channel.id),
            "count": len(payload),
            "limit": limit_value,
            "messages": payload,
            "filters": {
                "before_message_id": str(before_id) if before_id else None,
                "after_message_id": str(after_id) if after_id else None,
                "date_from": after_dt.isoformat() if after_dt else None,
                "date_to": before_dt.isoformat() if before_dt else None,
                "query": query or None,
                "author_id": str(author_parsed) if author_parsed else None,
                "has_link": has_link,
                "has_file": has_file,
                "include_threads": include_threads,
            },
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "search_messages",
            start_time,
            request_id,
            error,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )


@mcp.tool()
async def analyze_attachment(
    channel_id: str = "",
    message_id: str = "",
    attachment_index: str = "0",
    mode: str = "ocr",
    prompt: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    resolved_channel_id = None
    try:
        if not OPENAI_VISION_ENABLED:
            error = build_error(
                "permission_denied",
                "OpenAI vision is disabled; set OPENAI_VISION_ENABLED=true.",
                required_perms=["OPENAI_VISION_ENABLED=true"],
            )
            return error_with_log("analyze_attachment", start_time, request_id, error)

        header_name = MCP_OPENAI_API_HEADER
        api_key = get_openai_api_key()
        if not api_key:
            error = build_error(
                "permission_denied",
                f"OpenAI API key missing; provide {header_name} header.",
                required_perms=[header_name],
                diagnostics={"required_headers": [header_name]},
            )
            return error_with_log("analyze_attachment", start_time, request_id, error)

        resolved_channel_id = resolve_channel_id(channel_id)
        channel = await get_message_target(resolved_channel_id)
        read_channel_id = (
            channel.parent_id
            if isinstance(channel, discord.Thread) and channel.parent_id
            else channel.id
        )
        allow_error = require_read_allowed(
            read_channel_id,
            "analyze_attachment",
            start_time,
            request_id,
            warnings=warnings,
        )
        if allow_error:
            return allow_error

        parsed_message_id = parse_snowflake(message_id)
        if parsed_message_id is None:
            error = build_error(
                "invalid_payload", "message_id must be a Discord snowflake."
            )
            return error_with_log(
                "analyze_attachment",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )

        msg = await retry_read(
            "fetch_message", lambda: channel.fetch_message(int(parsed_message_id))
        )
        if msg is None:
            error = build_error("not_found", "Message not found by message_id.")
            return error_with_log(
                "analyze_attachment",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )

        attachments = list(msg.attachments or [])
        if not attachments:
            error = build_error("invalid_payload", "Message has no attachments.")
            return error_with_log(
                "analyze_attachment",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )

        index_value = parse_int(attachment_index, 0)
        if index_value is None or index_value < 0 or index_value >= len(attachments):
            error = build_error(
                "invalid_payload",
                "attachment_index is out of range.",
                diagnostics={"attachments_count": len(attachments)},
            )
            return error_with_log(
                "analyze_attachment",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )

        attachment = attachments[index_value]
        size_bytes = getattr(attachment, "size", None)
        if size_bytes and size_bytes > OPENAI_VISION_MAX_MB * 1024 * 1024:
            error = build_error(
                "invalid_payload",
                f"Attachment exceeds {OPENAI_VISION_MAX_MB} MB limit.",
                diagnostics={"size_bytes": size_bytes},
            )
            return error_with_log(
                "analyze_attachment",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )

        if not is_image_attachment(attachment):
            error = build_error("invalid_payload", "Attachment is not an image.")
            return error_with_log(
                "analyze_attachment",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )

        mode_value = (mode or "ocr").strip().lower()
        if mode_value not in ("ocr", "describe"):
            error = build_error("invalid_payload", "mode must be 'ocr' or 'describe'.")
            return error_with_log(
                "analyze_attachment",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )

        prompt_text = (prompt or "").strip()
        if not prompt_text:
            if mode_value == "describe":
                prompt_text = "Describe the image clearly and mention any visible text."
            else:
                prompt_text = (
                    "Extract all readable text from this image. Preserve line breaks."
                )

        request_payload = {
            "model": OPENAI_VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": attachment.url},
                        },
                    ],
                }
            ],
            "max_tokens": 800,
        }

        timeout = aiohttp.ClientTimeout(total=OPENAI_VISION_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                OPENAI_VISION_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=request_payload,
            ) as response:
                response_text = await response.text()
                if response.status >= 400:
                    error = build_error(
                        "invalid_payload",
                        "OpenAI API error.",
                        diagnostics={
                            "status": response.status,
                            "response": response_text[:500],
                        },
                    )
                    return error_with_log(
                        "analyze_attachment",
                        start_time,
                        request_id,
                        error,
                        warnings=warnings,
                        channel_id=resolved_channel_id,
                    )
                try:
                    response_json = json.loads(response_text)
                except json.JSONDecodeError:
                    error = build_error(
                        "invalid_payload",
                        "Invalid OpenAI response payload.",
                        diagnostics={"response": response_text[:500]},
                    )
                    return error_with_log(
                        "analyze_attachment",
                        start_time,
                        request_id,
                        error,
                        warnings=warnings,
                        channel_id=resolved_channel_id,
                    )

        result_text = ""
        usage = None
        if isinstance(response_json, dict):
            usage = response_json.get("usage")
            choices = response_json.get("choices") or []
            if choices:
                message = choices[0].get("message") if isinstance(choices[0], dict) else None
                if message and isinstance(message, dict):
                    result_text = message.get("content") or ""
            if not result_text and response_json.get("output_text"):
                result_text = response_json.get("output_text") or ""

        if not result_text:
            warnings.append("No text extracted from OpenAI response.")

        log_action(
            "analyze_attachment",
            start_time,
            "ok",
            guild_id=channel.guild.id if getattr(channel, "guild", None) else DEFAULT_GUILD_ID,
            channel_id=resolved_channel_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=channel.guild.id if getattr(channel, "guild", None) else DEFAULT_GUILD_ID,
            channel_id=resolved_channel_id,
        )
        data = {
            "mode": mode_value,
            "text": result_text,
            "model": OPENAI_VISION_MODEL,
            "attachment": attachment_metadata(attachment),
            "message_id": str(msg.id),
            "channel_id": str(channel.id),
            "usage": usage,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "analyze_attachment",
            start_time,
            request_id,
            error,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )


@mcp.tool()
async def list_threads(
    channel_id: str = "",
    include_archived: bool | str = False,
    limit: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    resolved_channel_id = None
    warnings = []
    try:
        include_archived = parse_bool(include_archived)
        resolved_channel_id = resolve_channel_id(channel_id)
        allow_error = require_read_allowed(
            resolved_channel_id,
            "list_threads",
            start_time,
            request_id,
            warnings=warnings,
        )
        if allow_error:
            return allow_error
        channel = await get_text_channel(resolved_channel_id)
        threads = []

        if hasattr(channel, "active_threads"):
            active_threads = await channel.active_threads()
            if isinstance(active_threads, tuple):
                active_threads = active_threads[0]
            threads.extend(active_threads)
        elif hasattr(channel, "threads"):
            threads.extend(list(channel.threads))
        else:
            error = build_error(
                "invalid_payload",
                "Threads are not supported for this channel.",
            )
            return error_with_log(
                "list_threads",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )

        if include_archived and hasattr(channel, "archived_threads"):
            archived_limit = parse_int(limit, DEFAULT_READ_LIMIT)
            if archived_limit is None or archived_limit <= 0:
                archived_limit = DEFAULT_READ_LIMIT
            if archived_limit > MAX_READ_LIMIT:
                archived_limit = MAX_READ_LIMIT

            async def fetch_archived():
                archived = []
                async for thread in channel.archived_threads(limit=archived_limit):
                    archived.append(thread)
                return archived

            archived_threads = await retry_read("archived_threads", fetch_archived)
            threads.extend(archived_threads)

        payload = []
        for thread in threads:
            payload.append(
                {
                    "id": str(thread.id),
                    "name": thread.name,
                    "archived": thread.archived,
                    "locked": thread.locked,
                    "created_at": thread.created_at.isoformat()
                    if thread.created_at
                    else None,
                    "owner_id": str(thread.owner_id) if thread.owner_id else None,
                }
            )

        record_api_success("list_threads")
        log_action(
            "list_threads",
            start_time,
            "ok",
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        data = {
            "channel_id": str(channel.id),
            "count": len(payload),
            "threads": payload,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "list_threads",
            start_time,
            request_id,
            error,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )


@mcp.tool()
async def create_thread(
    channel_id: str,
    message_id: str,
    name: str,
    auto_archive_duration: str = "",
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    resolved_channel_id = None
    try:
        if not name:
            error = build_error("invalid_payload", "name cannot be null.")
            return error_with_log("create_thread", start_time, request_id, error)
        if not message_id:
            error = build_error("invalid_payload", "messageId cannot be null.")
            return error_with_log("create_thread", start_time, request_id, error)
        resolved_channel_id = resolve_channel_id(channel_id)
        confirm_error = require_confirm(
            confirm,
            "create_thread",
            start_time,
            request_id,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )
        if confirm_error:
            return confirm_error
        allow_error = require_write_allowed(
            resolved_channel_id,
            "create_thread",
            start_time,
            request_id,
            warnings=warnings,
        )
        if allow_error:
            return allow_error

        channel = await get_text_channel(resolved_channel_id)
        member = await get_bot_member(channel.guild)
        perms = (
            channel.permissions_for(member)
            if member is not None
            else discord.Permissions.none()
        )
        caps = channel_capabilities(perms)
        if not caps.get("create_threads"):
            error = build_error(
                "permission_denied",
                "Missing permission to create threads.",
                required_perms=["create_threads"],
            )
            return error_with_log(
                "create_thread",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )

        duration = parse_int(auto_archive_duration, None)
        if auto_archive_duration and duration is None:
            error = build_error(
                "invalid_payload", "auto_archive_duration must be an integer."
            )
            return error_with_log(
                "create_thread", start_time, request_id, error, warnings=warnings
            )

        msg = await retry_read(
            "fetch_message", lambda: channel.fetch_message(int(message_id))
        )
        if msg is None:
            error = build_error("not_found", "Message not found by messageId.")
            return error_with_log(
                "create_thread",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )

        thread = await msg.create_thread(
            name=name, auto_archive_duration=duration or None
        )
        record_api_success("create_thread")
        log_action(
            "create_thread",
            start_time,
            "ok",
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
            thread_id=thread.id,
        )
        data = {"thread_id": str(thread.id), "name": thread.name, "message_id": str(msg.id)}
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "create_thread",
            start_time,
            request_id,
            error,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )


@mcp.tool()
async def archive_thread(thread_id: str, confirm: str = "") -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    parsed_thread_id = None
    try:
        parsed_thread_id = parse_snowflake(thread_id)
        if parsed_thread_id is None:
            error = build_error("invalid_payload", "thread_id must be a Discord snowflake.")
            return error_with_log("archive_thread", start_time, request_id, error)
        confirm_error = require_confirm(
            confirm,
            "archive_thread",
            start_time,
            request_id,
            warnings=warnings,
            channel_id=parsed_thread_id,
        )
        if confirm_error:
            return confirm_error

        thread = await get_message_target(parsed_thread_id)
        if not isinstance(thread, discord.Thread):
            error = build_error("invalid_payload", "Channel is not a thread.")
            return error_with_log(
                "archive_thread", start_time, request_id, error, warnings=warnings
            )
        parent_id = thread.parent_id or thread.id
        allow_error = require_write_allowed(
            parent_id,
            "archive_thread",
            start_time,
            request_id,
            warnings=warnings,
        )
        if allow_error:
            return allow_error

        await thread.edit(archived=True)
        record_api_success("archive_thread")
        log_action(
            "archive_thread",
            start_time,
            "ok",
            guild_id=thread.guild.id,
            channel_id=parent_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=thread.guild.id,
            channel_id=parent_id,
            thread_id=thread.id,
        )
        data = {"thread_id": str(thread.id), "archived": True}
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "archive_thread",
            start_time,
            request_id,
            error,
            warnings=warnings,
            channel_id=parsed_thread_id,
        )


@mcp.tool()
async def unarchive_thread(thread_id: str, confirm: str = "") -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    parsed_thread_id = None
    try:
        parsed_thread_id = parse_snowflake(thread_id)
        if parsed_thread_id is None:
            error = build_error("invalid_payload", "thread_id must be a Discord snowflake.")
            return error_with_log("unarchive_thread", start_time, request_id, error)
        confirm_error = require_confirm(
            confirm,
            "unarchive_thread",
            start_time,
            request_id,
            warnings=warnings,
            channel_id=parsed_thread_id,
        )
        if confirm_error:
            return confirm_error

        thread = await get_message_target(parsed_thread_id)
        if not isinstance(thread, discord.Thread):
            error = build_error("invalid_payload", "Channel is not a thread.")
            return error_with_log(
                "unarchive_thread", start_time, request_id, error, warnings=warnings
            )
        parent_id = thread.parent_id or thread.id
        allow_error = require_write_allowed(
            parent_id,
            "unarchive_thread",
            start_time,
            request_id,
            warnings=warnings,
        )
        if allow_error:
            return allow_error

        await thread.edit(archived=False)
        record_api_success("unarchive_thread")
        log_action(
            "unarchive_thread",
            start_time,
            "ok",
            guild_id=thread.guild.id,
            channel_id=parent_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=thread.guild.id,
            channel_id=parent_id,
            thread_id=thread.id,
        )
        data = {"thread_id": str(thread.id), "archived": False}
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "unarchive_thread",
            start_time,
            request_id,
            error,
            warnings=warnings,
            channel_id=parsed_thread_id,
        )


@mcp.tool()
async def channel_daily_audit(
    channel_id: str,
    date: str = "",
    limit: str = "",
    timezone_name: str = "",
    include_threads: bool | str = False,
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    resolved_channel_id = None
    try:
        resolved_channel_id = resolve_channel_id(channel_id)
        channel = await get_message_target(resolved_channel_id)
        read_channel_id = (
            channel.parent_id
            if isinstance(channel, discord.Thread) and channel.parent_id
            else channel.id
        )
        allow_error = require_read_allowed(
            read_channel_id,
            "channel_daily_audit",
            start_time,
            request_id,
            warnings=warnings,
        )
        if allow_error:
            return allow_error

        tz = resolve_timezone(timezone_name or None)
        start_local, end_local = parse_audit_date(date, tz)
        start_utc = start_local.astimezone(timezone.utc)
        end_utc = end_local.astimezone(timezone.utc)

        limit_value = parse_int(limit, DEFAULT_READ_LIMIT)
        if limit_value is None or limit_value <= 0:
            error = build_error("invalid_payload", "limit must be a positive integer.")
            return error_with_log(
                "channel_daily_audit",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )
        if limit_value > MAX_READ_LIMIT:
            limit_value = MAX_READ_LIMIT

        include_threads = parse_bool(include_threads)
        messages = await fetch_messages_in_range(
            channel, start_utc, end_utc, limit_value
        )

        if include_threads and isinstance(channel, discord.TextChannel):
            remaining = max(limit_value - len(messages), 0)
            if remaining > 0:
                threads = []
                if hasattr(channel, "active_threads"):
                    active_threads = await channel.active_threads()
                    if isinstance(active_threads, tuple):
                        active_threads = active_threads[0]
                    threads.extend(active_threads)
                elif hasattr(channel, "threads"):
                    threads.extend(list(channel.threads))
                for thread in threads:
                    if remaining <= 0:
                        break
                    thread_messages = await fetch_messages_in_range(
                        thread, start_utc, end_utc, remaining
                    )
                    messages.extend(thread_messages)
                    remaining = max(limit_value - len(messages), 0)

        if len(messages) >= limit_value:
            warnings.append("Message limit reached; results may be truncated.")

        summary = summarize_daily_audit(messages)
        timezone_label = getattr(tz, "key", None) or tz.tzname(datetime.now()) or "UTC"
        summary.update(
            {
                "channel_id": str(channel.id),
                "channel_name": getattr(channel, "name", None),
                "date": start_local.date().isoformat(),
                "timezone": timezone_label,
                "range_utc": {
                    "start": start_utc.isoformat(),
                    "end": end_utc.isoformat(),
                },
                "include_threads": include_threads,
            }
        )

        record_api_success("channel_daily_audit")
        log_action(
            "channel_daily_audit",
            start_time,
            "ok",
            guild_id=channel.guild.id if getattr(channel, "guild", None) else DEFAULT_GUILD_ID,
            channel_id=resolved_channel_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=channel.guild.id if getattr(channel, "guild", None) else DEFAULT_GUILD_ID,
            channel_id=resolved_channel_id,
        )
        return success_response(summary, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "channel_daily_audit",
            start_time,
            request_id,
            error,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )


@mcp.tool()
async def daily_audit_job_submit(
    date: str = "",
    channel_ids: list[str | int] | None = None,
    timezone_name: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    try:
        if not channel_ids or not isinstance(channel_ids, list):
            error = build_error(
                "invalid_payload", "channel_ids must be a non-empty list."
            )
            return error_with_log(
                "daily_audit_job_submit", start_time, request_id, error
            )

        tz = resolve_timezone(timezone_name or None)
        timezone_label = getattr(tz, "key", None) or tz.tzname(datetime.now()) or "UTC"
        parse_audit_date(date, tz)

        parsed_ids = []
        for raw_id in channel_ids:
            parsed = parse_snowflake(raw_id)
            if parsed is None:
                error = build_error(
                    "invalid_payload", f"Invalid channel id: {raw_id}."
                )
                return error_with_log(
                    "daily_audit_job_submit",
                    start_time,
                    request_id,
                    error,
                    warnings=warnings,
                )
            if parsed not in parsed_ids:
                parsed_ids.append(parsed)

        job_id = str(uuid.uuid4())
        now = time.time()
        job = {
            "task_id": job_id,
            "status": "queued",
            "created_at": job_timestamp(),
            "created_at_ts": now,
            "finished_at": None,
            "finished_at_ts": None,
            "date": date or "",
            "timezone": timezone_label,
            "total_channels": len(parsed_ids),
            "remaining_channel_ids": parsed_ids,
            "processed_channel_ids": [],
            "results": {},
            "error": None,
        }
        async with AUDIT_JOB_LOCK:
            await prune_audit_jobs_locked(now)
            AUDIT_JOB_STORE[job_id] = job

        log_action("daily_audit_job_submit", start_time, "ok")
        meta = build_meta(start_time, request_id=request_id, warnings=warnings)
        data = {
            "task_id": job_id,
            "status": "queued",
            "total_channels": len(parsed_ids),
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "daily_audit_job_submit",
            start_time,
            request_id,
            error,
            warnings=warnings,
        )


@mcp.tool()
async def daily_audit_job_status(
    task_id: str,
    include_results: bool | str = False,
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    include_results = parse_bool(include_results)
    try:
        if not task_id:
            error = build_error("invalid_payload", "task_id cannot be null.")
            return error_with_log(
                "daily_audit_job_status", start_time, request_id, error
            )
        async with AUDIT_JOB_LOCK:
            await prune_audit_jobs_locked()
            job = AUDIT_JOB_STORE.get(task_id)
            if job is None:
                error = build_error("not_found", "Job not found.")
                return error_with_log(
                    "daily_audit_job_status", start_time, request_id, error
                )
            snapshot = build_audit_job_snapshot(job, include_results)

        log_action("daily_audit_job_status", start_time, "ok")
        meta = build_meta(start_time, request_id=request_id, warnings=warnings)
        return success_response(snapshot, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "daily_audit_job_status",
            start_time,
            request_id,
            error,
            warnings=warnings,
        )


@mcp.tool()
async def daily_audit_job_next(
    task_id: str,
    limit: str = "",
    include_threads: bool | str = False,
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    try:
        if not task_id:
            error = build_error("invalid_payload", "task_id cannot be null.")
            return error_with_log(
                "daily_audit_job_next", start_time, request_id, error
            )

        async with AUDIT_JOB_LOCK:
            await prune_audit_jobs_locked()
            job = AUDIT_JOB_STORE.get(task_id)
            if job is None:
                error = build_error("not_found", "Job not found.")
                return error_with_log(
                    "daily_audit_job_next", start_time, request_id, error
                )
            if not job.get("remaining_channel_ids"):
                job["status"] = "completed"
                job["finished_at"] = job_timestamp()
                job["finished_at_ts"] = time.time()
                snapshot = build_audit_job_snapshot(job, include_results=True)
                meta = build_meta(start_time, request_id=request_id, warnings=warnings)
                return success_response(snapshot, meta)

            channel_id = job["remaining_channel_ids"].pop(0)
            job["status"] = "running"

        channel_result = await channel_daily_audit(
            channel_id=str(channel_id),
            date=job.get("date", ""),
            limit=limit,
            timezone_name=job.get("timezone", ""),
            include_threads=include_threads,
        )

        async with AUDIT_JOB_LOCK:
            job = AUDIT_JOB_STORE.get(task_id)
            if job is None:
                error = build_error("not_found", "Job not found.")
                return error_with_log(
                    "daily_audit_job_next", start_time, request_id, error
                )
            job.setdefault("processed_channel_ids", []).append(channel_id)
            job.setdefault("results", {})[str(channel_id)] = redact_payload(
                channel_result
            )
            if not job.get("remaining_channel_ids"):
                job["status"] = "completed"
                job["finished_at"] = job_timestamp()
                job["finished_at_ts"] = time.time()

            snapshot = build_audit_job_snapshot(job, include_results=False)

        data = {
            "channel_id": str(channel_id),
            "channel_result": channel_result,
            "job": snapshot,
        }
        meta = build_meta(start_time, request_id=request_id, warnings=warnings)
        log_action("daily_audit_job_next", start_time, "ok")
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "daily_audit_job_next",
            start_time,
            request_id,
            error,
            warnings=warnings,
        )


@mcp.tool()
async def add_reaction(
    channel_id: str,
    message_id: str,
    emoji: str,
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    diagnostics = {}
    resolved_channel_id = None
    try:
        if not emoji:
            error = build_error("invalid_payload", "emoji cannot be null.")
            return error_with_log("add_reaction", start_time, request_id, error)
        if not message_id:
            error = build_error("invalid_payload", "messageId cannot be null.")
            return error_with_log("add_reaction", start_time, request_id, error)

        resolved_channel_id = resolve_channel_id(channel_id)
        confirm_error = require_confirm(
            confirm,
            "add_reaction",
            start_time,
            request_id,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )
        if confirm_error:
            return confirm_error
        allow_error = require_write_allowed(
            resolved_channel_id,
            "add_reaction",
            start_time,
            request_id,
            warnings=warnings,
        )
        if allow_error:
            return allow_error

        channel = await get_text_channel(resolved_channel_id)
        msg = await retry_read(
            "fetch_message", lambda: channel.fetch_message(int(message_id))
        )
        if msg is None:
            error = build_error("not_found", "Message not found by messageId.")
            return error_with_log(
                "add_reaction",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )
        await msg.add_reaction(emoji)
        record_api_success("add_reaction")
        log_action(
            "add_reaction",
            start_time,
            "ok",
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        data = {
            "channel_id": str(channel.id),
            "message_id": str(msg.id),
            "jump_url": msg.jump_url,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc, diagnostics=diagnostics)
        return error_with_log(
            "add_reaction",
            start_time,
            request_id,
            error,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )


@mcp.tool()
async def remove_reaction(
    channel_id: str,
    message_id: str,
    emoji: str,
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    diagnostics = {}
    resolved_channel_id = None
    try:
        if not emoji:
            error = build_error("invalid_payload", "emoji cannot be null.")
            return error_with_log("remove_reaction", start_time, request_id, error)
        if not message_id:
            error = build_error("invalid_payload", "messageId cannot be null.")
            return error_with_log("remove_reaction", start_time, request_id, error)

        resolved_channel_id = resolve_channel_id(channel_id)
        confirm_error = require_confirm(
            confirm,
            "remove_reaction",
            start_time,
            request_id,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )
        if confirm_error:
            return confirm_error
        allow_error = require_write_allowed(
            resolved_channel_id,
            "remove_reaction",
            start_time,
            request_id,
            warnings=warnings,
        )
        if allow_error:
            return allow_error

        channel = await get_text_channel(resolved_channel_id)
        client = await get_client()
        msg = await retry_read(
            "fetch_message", lambda: channel.fetch_message(int(message_id))
        )
        if msg is None:
            error = build_error("not_found", "Message not found by messageId.")
            return error_with_log(
                "remove_reaction",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )
        await msg.remove_reaction(emoji, client.user)
        record_api_success("remove_reaction")
        log_action(
            "remove_reaction",
            start_time,
            "ok",
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        data = {
            "channel_id": str(channel.id),
            "message_id": str(msg.id),
            "jump_url": msg.jump_url,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc, diagnostics=diagnostics)
        return error_with_log(
            "remove_reaction",
            start_time,
            request_id,
            error,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )


@mcp.tool()
async def timeout_member(
    user_id: str,
    duration_minutes: str,
    reason: str = "",
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    audit_trail_id = str(uuid.uuid4())
    warnings = []
    parsed_user_id = None
    try:
        confirm_error = require_confirm(
            confirm,
            "timeout_member",
            start_time,
            request_id,
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )
        if confirm_error:
            return confirm_error
        parsed_user_id = parse_snowflake(user_id)
        if parsed_user_id is None:
            error = build_error("invalid_payload", "userId must be a Discord snowflake.")
            return error_with_log(
                "timeout_member",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )
        duration_value = parse_int(duration_minutes, None)
        if duration_value is None or duration_value <= 0:
            error = build_error(
                "invalid_payload", "duration_minutes must be a positive integer."
            )
            return error_with_log(
                "timeout_member",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )
        if duration_value > MAX_TIMEOUT_MINUTES:
            error = build_error(
                "invalid_payload",
                f"duration_minutes exceeds max {MAX_TIMEOUT_MINUTES}.",
            )
            return error_with_log(
                "timeout_member",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )

        guild = await get_guild("")
        member, error_response_obj = await get_member_or_error(
            guild,
            parsed_user_id,
            "timeout_member",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
        )
        if error_response_obj:
            return error_response_obj
        guard_error = ensure_member_guardrails(
            member,
            "timeout_member",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
        )
        if guard_error:
            return guard_error
        bot_member, perm_error = await ensure_bot_can_moderate(
            guild,
            member,
            "timeout_member",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
            required_perm="moderate_members",
        )
        if perm_error:
            return perm_error

        timeout_until = datetime.now(timezone.utc) + timedelta(minutes=duration_value)
        reason_text = reason.strip() if reason else ""
        await member.edit(timed_out_until=timeout_until, reason=reason_text or None)
        record_api_success("timeout_member")
        log_action("timeout_member", start_time, "ok", guild_id=guild.id)
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
        data = {
            "user_id": str(member.id),
            "timeout_until": timeout_until.isoformat(),
            "duration_minutes": duration_value,
            "reason": reason_text or None,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "timeout_member",
            start_time,
            request_id,
            error,
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )


@mcp.tool()
async def remove_timeout(
    user_id: str,
    reason: str = "",
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    audit_trail_id = str(uuid.uuid4())
    warnings = []
    parsed_user_id = None
    try:
        confirm_error = require_confirm(
            confirm,
            "remove_timeout",
            start_time,
            request_id,
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )
        if confirm_error:
            return confirm_error
        parsed_user_id = parse_snowflake(user_id)
        if parsed_user_id is None:
            error = build_error("invalid_payload", "userId must be a Discord snowflake.")
            return error_with_log(
                "remove_timeout",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )

        guild = await get_guild("")
        member, error_response_obj = await get_member_or_error(
            guild,
            parsed_user_id,
            "remove_timeout",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
        )
        if error_response_obj:
            return error_response_obj
        guard_error = ensure_member_guardrails(
            member,
            "remove_timeout",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
        )
        if guard_error:
            return guard_error
        bot_member, perm_error = await ensure_bot_can_moderate(
            guild,
            member,
            "remove_timeout",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
            required_perm="moderate_members",
        )
        if perm_error:
            return perm_error

        reason_text = reason.strip() if reason else ""
        await member.edit(timed_out_until=None, reason=reason_text or None)
        record_api_success("remove_timeout")
        log_action("remove_timeout", start_time, "ok", guild_id=guild.id)
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
        data = {
            "user_id": str(member.id),
            "timeout_removed": True,
            "reason": reason_text or None,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "remove_timeout",
            start_time,
            request_id,
            error,
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )


@mcp.tool()
async def kick_member(
    user_id: str,
    reason: str = "",
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    audit_trail_id = str(uuid.uuid4())
    warnings = []
    parsed_user_id = None
    try:
        confirm_error = require_confirm(
            confirm,
            "kick_member",
            start_time,
            request_id,
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )
        if confirm_error:
            return confirm_error
        parsed_user_id = parse_snowflake(user_id)
        if parsed_user_id is None:
            error = build_error("invalid_payload", "userId must be a Discord snowflake.")
            return error_with_log(
                "kick_member",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )

        guild = await get_guild("")
        member, error_response_obj = await get_member_or_error(
            guild,
            parsed_user_id,
            "kick_member",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
        )
        if error_response_obj:
            return error_response_obj
        guard_error = ensure_member_guardrails(
            member,
            "kick_member",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
        )
        if guard_error:
            return guard_error
        bot_member, perm_error = await ensure_bot_can_moderate(
            guild,
            member,
            "kick_member",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
            required_perm="kick_members",
        )
        if perm_error:
            return perm_error

        reason_text = reason.strip() if reason else ""
        await member.kick(reason=reason_text or None)
        record_api_success("kick_member")
        log_action("kick_member", start_time, "ok", guild_id=guild.id)
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
        data = {
            "user_id": str(member.id),
            "kicked": True,
            "reason": reason_text or None,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "kick_member",
            start_time,
            request_id,
            error,
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )


@mcp.tool()
async def ban_member(
    user_id: str,
    delete_message_days: str = "",
    reason: str = "",
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    audit_trail_id = str(uuid.uuid4())
    warnings = []
    parsed_user_id = None
    try:
        confirm_error = require_confirm(
            confirm,
            "ban_member",
            start_time,
            request_id,
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )
        if confirm_error:
            return confirm_error
        parsed_user_id = parse_snowflake(user_id)
        if parsed_user_id is None:
            error = build_error("invalid_payload", "userId must be a Discord snowflake.")
            return error_with_log(
                "ban_member",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )
        if parsed_user_id in PROTECTED_USER_IDS:
            error = build_error(
                "permission_denied",
                "Target user is protected.",
                required_perms=["DISCORD_PROTECTED_USER_IDS"],
            )
            return error_with_log(
                "ban_member",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )

        delete_days = None
        if delete_message_days:
            delete_days = parse_int(delete_message_days, None)
            if delete_days is None or delete_days < 0 or delete_days > 7:
                error = build_error(
                    "invalid_payload",
                    "delete_message_days must be between 0 and 7.",
                )
                return error_with_log(
                    "ban_member",
                    start_time,
                    request_id,
                    error,
                    warnings=warnings,
                    extra={"audit_trail_id": audit_trail_id},
                )

        guild = await get_guild("")
        member = await fetch_member_optional(guild, parsed_user_id)
        if member is None and ALLOWED_TARGET_ROLE_IDS:
            error = build_error(
                "permission_denied",
                "Target user is not in the guild; allowed role checks require membership.",
                required_perms=["DISCORD_ALLOWED_TARGET_ROLE_IDS"],
            )
            return error_with_log(
                "ban_member",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
                extra={"audit_trail_id": audit_trail_id},
            )

        reason_text = reason.strip() if reason else ""
        if member is not None:
            guard_error = ensure_member_guardrails(
                member,
                "ban_member",
                start_time,
                request_id,
                warnings,
                audit_trail_id,
            )
            if guard_error:
                return guard_error
            bot_member, perm_error = await ensure_bot_can_moderate(
                guild,
                member,
                "ban_member",
                start_time,
                request_id,
                warnings,
                audit_trail_id,
                required_perm="ban_members",
            )
            if perm_error:
                return perm_error
            ban_kwargs = {"reason": reason_text or None}
            if delete_days is not None:
                ban_kwargs["delete_message_days"] = delete_days
            await guild.ban(member, **ban_kwargs)
        else:
            bot_member, perm_error = await ensure_bot_has_permission(
                guild,
                "ban_member",
                start_time,
                request_id,
                warnings,
                audit_trail_id,
                required_perm="ban_members",
            )
            if perm_error:
                return perm_error
            target = discord.Object(id=parsed_user_id)
            ban_kwargs = {"reason": reason_text or None}
            if delete_days is not None:
                ban_kwargs["delete_message_days"] = delete_days
            await guild.ban(target, **ban_kwargs)

        record_api_success("ban_member")
        log_action("ban_member", start_time, "ok", guild_id=guild.id)
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
        data = {
            "user_id": str(parsed_user_id),
            "banned": True,
            "delete_message_days": delete_days,
            "reason": reason_text or None,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "ban_member",
            start_time,
            request_id,
            error,
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )


@mcp.tool()
async def unban_member(
    user_id: str,
    reason: str = "",
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    audit_trail_id = str(uuid.uuid4())
    warnings = []
    parsed_user_id = None
    try:
        confirm_error = require_confirm(
            confirm,
            "unban_member",
            start_time,
            request_id,
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )
        if confirm_error:
            return confirm_error
        parsed_user_id = parse_snowflake(user_id)
        if parsed_user_id is None:
            error = build_error("invalid_payload", "userId must be a Discord snowflake.")
            return error_with_log(
                "unban_member",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )
        if parsed_user_id in PROTECTED_USER_IDS:
            error = build_error(
                "permission_denied",
                "Target user is protected.",
                required_perms=["DISCORD_PROTECTED_USER_IDS"],
            )
            return error_with_log(
                "unban_member",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )

        guild = await get_guild("")
        member = await fetch_member_optional(guild, parsed_user_id)
        if member is not None:
            error = build_error(
                "invalid_payload", "User is currently in the guild; cannot unban."
            )
            return error_with_log(
                "unban_member",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
                extra={"audit_trail_id": audit_trail_id},
            )
        if ALLOWED_TARGET_ROLE_IDS:
            error = build_error(
                "permission_denied",
                "Target user is not in the guild; allowed role checks require membership.",
                required_perms=["DISCORD_ALLOWED_TARGET_ROLE_IDS"],
            )
            return error_with_log(
                "unban_member",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
                extra={"audit_trail_id": audit_trail_id},
            )

        reason_text = reason.strip() if reason else ""
        bot_member, perm_error = await ensure_bot_has_permission(
            guild,
            "unban_member",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
            required_perm="ban_members",
        )
        if perm_error:
            return perm_error
        await guild.unban(
            discord.Object(id=parsed_user_id),
            reason=reason_text or None,
        )
        record_api_success("unban_member")
        log_action("unban_member", start_time, "ok", guild_id=guild.id)
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
        data = {
            "user_id": str(parsed_user_id),
            "unbanned": True,
            "reason": reason_text or None,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "unban_member",
            start_time,
            request_id,
            error,
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )


@mcp.tool()
async def add_role(
    user_id: str,
    role_id: str,
    reason: str = "",
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    audit_trail_id = str(uuid.uuid4())
    warnings = []
    parsed_user_id = None
    parsed_role_id = None
    try:
        confirm_error = require_confirm(
            confirm,
            "add_role",
            start_time,
            request_id,
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )
        if confirm_error:
            return confirm_error
        parsed_user_id = parse_snowflake(user_id)
        if parsed_user_id is None:
            error = build_error("invalid_payload", "userId must be a Discord snowflake.")
            return error_with_log(
                "add_role",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )
        parsed_role_id = parse_snowflake(role_id)
        if parsed_role_id is None:
            error = build_error("invalid_payload", "roleId must be a Discord snowflake.")
            return error_with_log(
                "add_role",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )
        if parsed_role_id in PROTECTED_ROLE_IDS:
            error = build_error(
                "permission_denied",
                "Role is protected.",
                required_perms=["DISCORD_PROTECTED_ROLE_IDS"],
            )
            return error_with_log(
                "add_role",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )

        guild = await get_guild("")
        role = guild.get_role(parsed_role_id)
        if role is None:
            roles = await retry_read("fetch_roles", guild.fetch_roles)
            role = discord.utils.get(roles, id=parsed_role_id)
        if role is None:
            error = build_error("not_found", "Role not found by roleId.")
            return error_with_log(
                "add_role",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
                extra={"audit_trail_id": audit_trail_id},
            )

        member, error_response_obj = await get_member_or_error(
            guild,
            parsed_user_id,
            "add_role",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
        )
        if error_response_obj:
            return error_response_obj
        guard_error = ensure_member_guardrails(
            member,
            "add_role",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
            role_id=role.id,
        )
        if guard_error:
            return guard_error
        bot_member, perm_error = await ensure_bot_can_moderate(
            guild,
            member,
            "add_role",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
            required_perm="manage_roles",
        )
        if perm_error:
            return perm_error
        if role >= bot_member.top_role and guild.owner_id != bot_member.id:
            error = build_error(
                "permission_denied",
                "Bot role hierarchy prevents modifying this role.",
                required_perms=["role_hierarchy"],
            )
            return error_with_log(
                "add_role",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
                extra={"audit_trail_id": audit_trail_id},
            )

        if role in member.roles:
            warnings.append("Member already has role.")
            log_action("add_role", start_time, "ok", guild_id=guild.id)
            meta = build_meta(
                start_time,
                request_id=request_id,
                warnings=warnings,
                guild_id=guild.id,
                extra={"audit_trail_id": audit_trail_id},
            )
            data = {
                "user_id": str(member.id),
                "role_id": str(role.id),
                "role_name": role.name,
                "added": False,
                "reason": reason.strip() if reason else None,
            }
            return success_response(data, meta)

        reason_text = reason.strip() if reason else ""
        await member.add_roles(role, reason=reason_text or None)
        record_api_success("add_role")
        log_action("add_role", start_time, "ok", guild_id=guild.id)
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
        data = {
            "user_id": str(member.id),
            "role_id": str(role.id),
            "role_name": role.name,
            "added": True,
            "reason": reason_text or None,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "add_role",
            start_time,
            request_id,
            error,
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )


@mcp.tool()
async def remove_role(
    user_id: str,
    role_id: str,
    reason: str = "",
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    audit_trail_id = str(uuid.uuid4())
    warnings = []
    parsed_user_id = None
    parsed_role_id = None
    try:
        confirm_error = require_confirm(
            confirm,
            "remove_role",
            start_time,
            request_id,
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )
        if confirm_error:
            return confirm_error
        parsed_user_id = parse_snowflake(user_id)
        if parsed_user_id is None:
            error = build_error("invalid_payload", "userId must be a Discord snowflake.")
            return error_with_log(
                "remove_role",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )
        parsed_role_id = parse_snowflake(role_id)
        if parsed_role_id is None:
            error = build_error("invalid_payload", "roleId must be a Discord snowflake.")
            return error_with_log(
                "remove_role",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )
        if parsed_role_id in PROTECTED_ROLE_IDS:
            error = build_error(
                "permission_denied",
                "Role is protected.",
                required_perms=["DISCORD_PROTECTED_ROLE_IDS"],
            )
            return error_with_log(
                "remove_role",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )

        guild = await get_guild("")
        role = guild.get_role(parsed_role_id)
        if role is None:
            roles = await retry_read("fetch_roles", guild.fetch_roles)
            role = discord.utils.get(roles, id=parsed_role_id)
        if role is None:
            error = build_error("not_found", "Role not found by roleId.")
            return error_with_log(
                "remove_role",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
                extra={"audit_trail_id": audit_trail_id},
            )

        member, error_response_obj = await get_member_or_error(
            guild,
            parsed_user_id,
            "remove_role",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
        )
        if error_response_obj:
            return error_response_obj
        guard_error = ensure_member_guardrails(
            member,
            "remove_role",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
            role_id=role.id,
        )
        if guard_error:
            return guard_error
        bot_member, perm_error = await ensure_bot_can_moderate(
            guild,
            member,
            "remove_role",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
            required_perm="manage_roles",
        )
        if perm_error:
            return perm_error
        if role >= bot_member.top_role and guild.owner_id != bot_member.id:
            error = build_error(
                "permission_denied",
                "Bot role hierarchy prevents modifying this role.",
                required_perms=["role_hierarchy"],
            )
            return error_with_log(
                "remove_role",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
                extra={"audit_trail_id": audit_trail_id},
            )

        if role not in member.roles:
            warnings.append("Member does not have role.")
            log_action("remove_role", start_time, "ok", guild_id=guild.id)
            meta = build_meta(
                start_time,
                request_id=request_id,
                warnings=warnings,
                guild_id=guild.id,
                extra={"audit_trail_id": audit_trail_id},
            )
            data = {
                "user_id": str(member.id),
                "role_id": str(role.id),
                "role_name": role.name,
                "removed": False,
                "reason": reason.strip() if reason else None,
            }
            return success_response(data, meta)

        reason_text = reason.strip() if reason else ""
        await member.remove_roles(role, reason=reason_text or None)
        record_api_success("remove_role")
        log_action("remove_role", start_time, "ok", guild_id=guild.id)
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
        data = {
            "user_id": str(member.id),
            "role_id": str(role.id),
            "role_name": role.name,
            "removed": True,
            "reason": reason_text or None,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "remove_role",
            start_time,
            request_id,
            error,
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )


@mcp.tool()
async def edit_nickname(
    user_id: str,
    nickname: str,
    reason: str = "",
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    audit_trail_id = str(uuid.uuid4())
    warnings = []
    parsed_user_id = None
    try:
        confirm_error = require_confirm(
            confirm,
            "edit_nickname",
            start_time,
            request_id,
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )
        if confirm_error:
            return confirm_error
        parsed_user_id = parse_snowflake(user_id)
        if parsed_user_id is None:
            error = build_error("invalid_payload", "userId must be a Discord snowflake.")
            return error_with_log(
                "edit_nickname",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )
        if nickname is None:
            error = build_error("invalid_payload", "nickname cannot be null.")
            return error_with_log(
                "edit_nickname",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )

        new_nick = nickname.strip()
        if not new_nick:
            new_nick = None
        if new_nick and len(new_nick) > MAX_NICKNAME_LENGTH:
            error = build_error(
                "invalid_payload",
                f"nickname exceeds {MAX_NICKNAME_LENGTH} characters.",
            )
            return error_with_log(
                "edit_nickname",
                start_time,
                request_id,
                error,
                warnings=warnings,
                extra={"audit_trail_id": audit_trail_id},
            )

        guild = await get_guild("")
        member, error_response_obj = await get_member_or_error(
            guild,
            parsed_user_id,
            "edit_nickname",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
        )
        if error_response_obj:
            return error_response_obj
        guard_error = ensure_member_guardrails(
            member,
            "edit_nickname",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
        )
        if guard_error:
            return guard_error
        bot_member, perm_error = await ensure_bot_can_moderate(
            guild,
            member,
            "edit_nickname",
            start_time,
            request_id,
            warnings,
            audit_trail_id,
            required_perm="manage_nicknames",
        )
        if perm_error:
            return perm_error

        reason_text = reason.strip() if reason else ""
        await member.edit(nick=new_nick, reason=reason_text or None)
        record_api_success("edit_nickname")
        log_action("edit_nickname", start_time, "ok", guild_id=guild.id)
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=guild.id,
            extra={"audit_trail_id": audit_trail_id},
        )
        data = {
            "user_id": str(member.id),
            "nickname": new_nick,
            "cleared": new_nick is None,
            "reason": reason_text or None,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "edit_nickname",
            start_time,
            request_id,
            error,
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )


@mcp.tool()
async def get_user_id_by_name(username: str, guild_id: str = "") -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    try:
        if not username:
            error = build_error("invalid_payload", "username cannot be null.")
            return error_with_log(
                "get_user_id_by_name", start_time, request_id, error
            )
        guild = await get_guild(guild_id)
        name = username
        discriminator = None
        if "#" in username:
            idx = username.rfind("#")
            name = username[:idx]
            discriminator = username[idx + 1 :]

        async def fetch_members():
            return [
                m async for m in guild.fetch_members(limit=None)
                if m.name.lower() == name.lower()
            ]

        members = await retry_read("fetch_members", fetch_members)
        if discriminator:
            members = [m for m in members if m.discriminator == discriminator]

        if not members:
            error = build_error(
                "not_found", f"No user found with username {username}."
            )
            return error_with_log(
                "get_user_id_by_name",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
            )
        if len(members) > 1:
            user_list = [
                f"{m.name}#{m.discriminator} (ID: {m.id})" for m in members
            ]
            error = build_error(
                "invalid_payload",
                f"Multiple users found with username {username}.",
                diagnostics={"matches": user_list},
            )
            return error_with_log(
                "get_user_id_by_name",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
            )

        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=guild.id,
        )
        data = {
            "user_id": str(members[0].id),
            "username": f"{members[0].name}#{members[0].discriminator}",
        }
        log_action("get_user_id_by_name", start_time, "ok", guild_id=guild.id)
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "get_user_id_by_name",
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=DEFAULT_GUILD_ID,
        )


@mcp.tool()
async def send_private_message(
    user_id: str,
    message: str,
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    try:
        dm_error = require_dm_enabled(
            "send_private_message", start_time, request_id, warnings=warnings
        )
        if dm_error:
            return dm_error
        confirm_error = require_confirm(
            confirm,
            "send_private_message",
            start_time,
            request_id,
            warnings=warnings,
        )
        if confirm_error:
            return confirm_error
        if not user_id:
            error = build_error("invalid_payload", "userId cannot be null.")
            return error_with_log("send_private_message", start_time, request_id, error)
        if not message:
            error = build_error("invalid_payload", "message cannot be null.")
            return error_with_log("send_private_message", start_time, request_id, error)
        dm = await get_dm_channel(user_id)
        sent = await dm.send(message)
        record_api_success("send_private_message")
        log_action("send_private_message", start_time, "ok")
        meta = build_meta(start_time, request_id=request_id, warnings=warnings)
        data = {
            "user_id": str(user_id),
            "message_id": str(sent.id),
            "jump_url": sent.jump_url,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "send_private_message",
            start_time,
            request_id,
            error,
            warnings=warnings,
        )


@mcp.tool()
async def edit_private_message(
    user_id: str,
    message_id: str,
    new_message: str,
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    try:
        dm_error = require_dm_enabled(
            "edit_private_message", start_time, request_id, warnings=warnings
        )
        if dm_error:
            return dm_error
        confirm_error = require_confirm(
            confirm,
            "edit_private_message",
            start_time,
            request_id,
            warnings=warnings,
        )
        if confirm_error:
            return confirm_error
        if not new_message:
            error = build_error("invalid_payload", "newMessage cannot be null.")
            return error_with_log("edit_private_message", start_time, request_id, error)
        dm = await get_dm_channel(user_id)
        msg = await retry_read(
            "fetch_dm_message", lambda: dm.fetch_message(int(message_id))
        )
        if msg is None:
            error = build_error("not_found", "Message not found by messageId.")
            return error_with_log("edit_private_message", start_time, request_id, error)
        edited = await msg.edit(content=new_message)
        record_api_success("edit_private_message")
        log_action("edit_private_message", start_time, "ok")
        meta = build_meta(start_time, request_id=request_id, warnings=warnings)
        data = {
            "user_id": str(user_id),
            "message_id": str(edited.id),
            "jump_url": edited.jump_url,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "edit_private_message",
            start_time,
            request_id,
            error,
            warnings=warnings,
        )


@mcp.tool()
async def delete_private_message(
    user_id: str,
    message_id: str,
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    try:
        dm_error = require_dm_enabled(
            "delete_private_message", start_time, request_id, warnings=warnings
        )
        if dm_error:
            return dm_error
        confirm_error = require_confirm(
            confirm,
            "delete_private_message",
            start_time,
            request_id,
            warnings=warnings,
        )
        if confirm_error:
            return confirm_error
        dm = await get_dm_channel(user_id)
        msg = await retry_read(
            "fetch_dm_message", lambda: dm.fetch_message(int(message_id))
        )
        if msg is None:
            error = build_error("not_found", "Message not found by messageId.")
            return error_with_log("delete_private_message", start_time, request_id, error)
        await msg.delete()
        record_api_success("delete_private_message")
        log_action("delete_private_message", start_time, "ok")
        meta = build_meta(start_time, request_id=request_id, warnings=warnings)
        data = {"user_id": str(user_id), "message_id": str(message_id)}
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "delete_private_message",
            start_time,
            request_id,
            error,
            warnings=warnings,
        )


@mcp.tool()
async def read_private_messages(user_id: str, count: str = "") -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    try:
        dm_error = require_dm_enabled(
            "read_private_messages", start_time, request_id, warnings=warnings
        )
        if dm_error:
            return dm_error
        dm = await get_dm_channel(user_id)
        limit = parse_int(count, DEFAULT_READ_LIMIT)
        if limit is None or limit <= 0:
            error = build_error("invalid_payload", "count must be a positive integer.")
            return error_with_log("read_private_messages", start_time, request_id, error)
        if limit > MAX_READ_LIMIT:
            limit = MAX_READ_LIMIT

        async def fetch_history():
            return [m async for m in dm.history(limit=limit)]

        messages = await retry_read("read_private_messages", fetch_history)
        record_api_success("read_private_messages")
        payload = [
            {
                "id": str(msg.id),
                "author": {
                    "id": str(msg.author.id),
                    "name": msg.author.name,
                },
                "created_at": msg.created_at.isoformat(),
                "content": msg.content,
                "embed_text": extract_embed_text(msg.embeds),
                "content_with_embeds": get_message_text(msg),
                "embeds": serialize_embeds(msg.embeds),
                "jump_url": msg.jump_url,
                "has_embeds": bool(msg.embeds),
            }
            for msg in messages
        ]
        log_action("read_private_messages", start_time, "ok")
        meta = build_meta(start_time, request_id=request_id, warnings=warnings)
        data = {"count": len(messages), "messages": payload}
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "read_private_messages",
            start_time,
            request_id,
            error,
            warnings=warnings,
        )


@mcp.tool()
async def create_text_channel(
    name: str,
    guild_id: str = "",
    category_id: str = "",
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    try:
        if not name:
            error = build_error("invalid_payload", "name cannot be null.")
            return error_with_log(
                "create_text_channel", start_time, request_id, error
            )
        confirm_error = require_confirm(
            confirm,
            "create_text_channel",
            start_time,
            request_id,
            warnings=warnings,
        )
        if confirm_error:
            return confirm_error
        writes_error = require_writes_enabled(
            "create_text_channel", start_time, request_id, warnings=warnings
        )
        if writes_error:
            return writes_error

        guild = await get_guild(guild_id)
        category = None
        if category_id:
            category = discord.utils.get(guild.categories, id=int(category_id))
            if category is None:
                category = await retry_read(
                    "fetch_category", lambda: guild.fetch_channel(int(category_id))
                )
            if not isinstance(category, discord.CategoryChannel):
                error = build_error("not_found", "Category not found by categoryId.")
                return error_with_log(
                    "create_text_channel",
                    start_time,
                    request_id,
                    error,
                    warnings=warnings,
                    guild_id=guild.id,
                )

        channel = await guild.create_text_channel(name, category=category)
        record_api_success("create_text_channel")
        log_action("create_text_channel", start_time, "ok", guild_id=guild.id)
        meta = build_meta(
            start_time, request_id=request_id, warnings=warnings, guild_id=guild.id
        )
        data = {
            "channel_id": str(channel.id),
            "name": channel.name,
            "category_id": str(category.id) if category else None,
            "category_name": category.name if category else None,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "create_text_channel",
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=DEFAULT_GUILD_ID,
        )


@mcp.tool()
async def delete_channel(
    channel_id: str,
    guild_id: str = "",
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    parsed_channel_id = None
    try:
        if not channel_id:
            error = build_error("invalid_payload", "channelId cannot be null.")
            return error_with_log("delete_channel", start_time, request_id, error)
        parsed_channel_id = parse_snowflake(channel_id)
        if parsed_channel_id is None:
            error = build_error("invalid_payload", "channelId must be a Discord snowflake.")
            return error_with_log("delete_channel", start_time, request_id, error)

        confirm_error = require_confirm(
            confirm,
            "delete_channel",
            start_time,
            request_id,
            warnings=warnings,
            channel_id=parsed_channel_id,
        )
        if confirm_error:
            return confirm_error
        allow_error = require_write_allowed(
            parsed_channel_id,
            "delete_channel",
            start_time,
            request_id,
            warnings=warnings,
        )
        if allow_error:
            return allow_error

        guild = await get_guild(guild_id)
        channel = guild.get_channel(int(parsed_channel_id))
        if channel is None:
            channel = await retry_read(
                "fetch_channel", lambda: guild.fetch_channel(int(parsed_channel_id))
            )
        if channel is None:
            error = build_error("not_found", "Channel not found by channelId.")
            return error_with_log(
                "delete_channel",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
            )
        channel_type = channel.type.name
        channel_name = channel.name
        await channel.delete()
        record_api_success("delete_channel")
        log_action("delete_channel", start_time, "ok", guild_id=guild.id)
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=guild.id,
            channel_id=parsed_channel_id,
        )
        data = {
            "channel_id": str(parsed_channel_id),
            "name": channel_name,
            "type": channel_type,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "delete_channel",
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=DEFAULT_GUILD_ID,
            channel_id=parsed_channel_id,
        )


@mcp.tool()
async def find_channel(channel_name: str, guild_id: str = "") -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    try:
        if not channel_name:
            error = build_error("invalid_payload", "channelName cannot be null.")
            return error_with_log("find_channel", start_time, request_id, error)
        if (
            not DISCORD_ALLOW_ALL_READ
            and not ALLOWED_CHANNEL_IDS
            and PRIMARY_CHANNEL_ID is None
            and not ALLOW_ALL_CHANNELS
        ):
            error = build_error(
                "permission_denied",
                "Channel reads are restricted.",
                required_perms=["DISCORD_ALLOW_ALL_READ=true or DISCORD_ALLOWED_CHANNEL_IDS"],
            )
            return error_with_log("find_channel", start_time, request_id, error)

        client = await get_client()
        guild = await get_guild(guild_id, client)
        query_key = normalize_channel_key(channel_name)
        if not query_key:
            error = build_error("invalid_payload", "channelName cannot be null.")
            return error_with_log("find_channel", start_time, request_id, error)

        channels_list, name_map, normalized_map = await get_cached_channels(guild)
        channels = name_map.get(channel_name.lower(), [])
        if not channels:
            channels = normalized_map.get(query_key, [])
        if not channels:
            channels = [
                c for c in channels_list if query_key in normalize_channel_key(c.name)
            ]
        channels = filter_channels_for_read(channels)
        if not channels:
            error = build_error("not_found", f"No channels found with name {channel_name}.")
            return error_with_log(
                "find_channel",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
            )

        payload = [
            {
                "id": str(c.id),
                "name": c.name,
                "type": c.type.name,
            }
            for c in channels
        ]
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=guild.id,
        )
        data = {"count": len(payload), "channels": payload}
        log_action("find_channel", start_time, "ok", guild_id=guild.id)
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "find_channel",
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=DEFAULT_GUILD_ID,
        )


@mcp.tool()
async def list_channels(guild_id: str = "") -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    try:
        if (
            not DISCORD_ALLOW_ALL_READ
            and not ALLOWED_CHANNEL_IDS
            and PRIMARY_CHANNEL_ID is None
            and not ALLOW_ALL_CHANNELS
        ):
            error = build_error(
                "permission_denied",
                "Channel reads are restricted.",
                required_perms=["DISCORD_ALLOW_ALL_READ=true or DISCORD_ALLOWED_CHANNEL_IDS"],
            )
            return error_with_log("list_channels", start_time, request_id, error)
        client = await get_client()
        guild = await get_guild(guild_id, client)
        channels, _, _ = await get_cached_channels(guild)
        channels = filter_channels_for_read(channels)
        if not channels:
            error = build_error("not_found", "No channels found by guildId.")
            return error_with_log(
                "list_channels",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
            )
        payload = [
            {"id": str(c.id), "name": c.name, "type": c.type.name}
            for c in channels
        ]
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=guild.id,
        )
        data = {"count": len(payload), "channels": payload}
        log_action("list_channels", start_time, "ok", guild_id=guild.id)
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "list_channels",
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=DEFAULT_GUILD_ID,
        )


@mcp.tool()
async def create_category(
    name: str,
    guild_id: str = "",
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    try:
        if not name:
            error = build_error("invalid_payload", "name cannot be null.")
            return error_with_log("create_category", start_time, request_id, error)
        confirm_error = require_confirm(
            confirm,
            "create_category",
            start_time,
            request_id,
            warnings=warnings,
        )
        if confirm_error:
            return confirm_error
        writes_error = require_writes_enabled(
            "create_category", start_time, request_id, warnings=warnings
        )
        if writes_error:
            return writes_error

        guild = await get_guild(guild_id)
        category = await guild.create_category(name)
        record_api_success("create_category")
        log_action("create_category", start_time, "ok", guild_id=guild.id)
        meta = build_meta(
            start_time, request_id=request_id, warnings=warnings, guild_id=guild.id
        )
        data = {"category_id": str(category.id), "name": category.name}
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "create_category",
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=DEFAULT_GUILD_ID,
        )


@mcp.tool()
async def delete_category(
    category_id: str,
    guild_id: str = "",
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    parsed_category_id = None
    try:
        parsed_category_id = parse_snowflake(category_id)
        if parsed_category_id is None:
            error = build_error(
                "invalid_payload", "categoryId must be a Discord snowflake."
            )
            return error_with_log("delete_category", start_time, request_id, error)
        confirm_error = require_confirm(
            confirm,
            "delete_category",
            start_time,
            request_id,
            warnings=warnings,
        )
        if confirm_error:
            return confirm_error
        writes_error = require_writes_enabled(
            "delete_category", start_time, request_id, warnings=warnings
        )
        if writes_error:
            return writes_error

        guild = await get_guild(guild_id)
        category = discord.utils.get(guild.categories, id=int(parsed_category_id))
        if category is None:
            category = await retry_read(
                "fetch_category", lambda: guild.fetch_channel(int(parsed_category_id))
            )
        if not isinstance(category, discord.CategoryChannel):
            error = build_error("not_found", "Category not found by categoryId.")
            return error_with_log(
                "delete_category",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
            )
        name = category.name
        await category.delete()
        record_api_success("delete_category")
        log_action("delete_category", start_time, "ok", guild_id=guild.id)
        meta = build_meta(
            start_time, request_id=request_id, warnings=warnings, guild_id=guild.id
        )
        data = {"category_id": str(parsed_category_id), "name": name}
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "delete_category",
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=DEFAULT_GUILD_ID,
        )


@mcp.tool()
async def find_category(category_name: str, guild_id: str = "") -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    try:
        if not category_name:
            error = build_error("invalid_payload", "categoryName cannot be null.")
            return error_with_log("find_category", start_time, request_id, error)
        if (
            not DISCORD_ALLOW_ALL_READ
            and not ALLOWED_CHANNEL_IDS
            and PRIMARY_CHANNEL_ID is None
            and not ALLOW_ALL_CHANNELS
        ):
            error = build_error(
                "permission_denied",
                "Category reads are restricted.",
                required_perms=["DISCORD_ALLOW_ALL_READ=true or DISCORD_ALLOWED_CHANNEL_IDS"],
            )
            return error_with_log("find_category", start_time, request_id, error)

        guild = await get_guild(guild_id)
        categories = [
            c for c in guild.categories if c.name.lower() == category_name.lower()
        ]
        if not DISCORD_ALLOW_ALL_READ and not ALLOW_ALL_CHANNELS:
            allowed_ids = set(ALLOWED_CHANNEL_IDS)
            if PRIMARY_CHANNEL_ID is not None:
                allowed_ids.add(PRIMARY_CHANNEL_ID)
            categories = [
                c for c in categories if any(ch.id in allowed_ids for ch in c.channels)
            ]
        if not categories:
            error = build_error("not_found", f"Category {category_name} not found.")
            return error_with_log(
                "find_category",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
            )

        payload = [
            {"id": str(c.id), "name": c.name, "channel_count": len(c.channels)}
            for c in categories
        ]
        meta = build_meta(
            start_time, request_id=request_id, warnings=warnings, guild_id=guild.id
        )
        data = {"count": len(payload), "categories": payload}
        log_action("find_category", start_time, "ok", guild_id=guild.id)
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "find_category",
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=DEFAULT_GUILD_ID,
        )


@mcp.tool()
async def list_channels_in_category(category_id: str, guild_id: str = "") -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    parsed_category_id = None
    try:
        parsed_category_id = parse_snowflake(category_id)
        if parsed_category_id is None:
            error = build_error(
                "invalid_payload", "categoryId must be a Discord snowflake."
            )
            return error_with_log(
                "list_channels_in_category", start_time, request_id, error
            )
        if (
            not DISCORD_ALLOW_ALL_READ
            and not ALLOWED_CHANNEL_IDS
            and PRIMARY_CHANNEL_ID is None
            and not ALLOW_ALL_CHANNELS
        ):
            error = build_error(
                "permission_denied",
                "Channel reads are restricted.",
                required_perms=["DISCORD_ALLOW_ALL_READ=true or DISCORD_ALLOWED_CHANNEL_IDS"],
            )
            return error_with_log(
                "list_channels_in_category", start_time, request_id, error
            )

        guild = await get_guild(guild_id)
        category = discord.utils.get(guild.categories, id=int(parsed_category_id))
        if category is None:
            category = await retry_read(
                "fetch_category", lambda: guild.fetch_channel(int(parsed_category_id))
            )
        if not isinstance(category, discord.CategoryChannel):
            error = build_error("not_found", "Category not found by categoryId.")
            return error_with_log(
                "list_channels_in_category",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
            )
        channels = category.channels
        channels = filter_channels_for_read(channels)
        if not channels:
            error = build_error("not_found", "Category does not contain readable channels.")
            return error_with_log(
                "list_channels_in_category",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
            )
        payload = [
            {"id": str(c.id), "name": c.name, "type": c.type.name} for c in channels
        ]
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=guild.id,
        )
        data = {"count": len(payload), "channels": payload}
        log_action("list_channels_in_category", start_time, "ok", guild_id=guild.id)
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "list_channels_in_category",
            start_time,
            request_id,
            error,
            warnings=warnings,
            guild_id=DEFAULT_GUILD_ID,
        )


@mcp.tool()
async def create_webhook(
    channel_id: str,
    name: str,
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    resolved_channel_id = None
    try:
        if not name:
            error = build_error("invalid_payload", "webhook name cannot be null.")
            return error_with_log("create_webhook", start_time, request_id, error)
        resolved_channel_id = resolve_channel_id(channel_id)
        confirm_error = require_confirm(
            confirm,
            "create_webhook",
            start_time,
            request_id,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )
        if confirm_error:
            return confirm_error
        allow_error = require_write_allowed(
            resolved_channel_id,
            "create_webhook",
            start_time,
            request_id,
            warnings=warnings,
        )
        if allow_error:
            return allow_error

        channel = await get_text_channel(resolved_channel_id)
        webhook = await channel.create_webhook(name=name)
        record_api_success("create_webhook")
        log_action(
            "create_webhook",
            start_time,
            "ok",
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        data = {"webhook_id": str(webhook.id), "name": webhook.name, "url": webhook.url}
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "create_webhook",
            start_time,
            request_id,
            error,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )


@mcp.tool()
async def delete_webhook(webhook_id: str, confirm: str = "") -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    try:
        if not webhook_id:
            error = build_error("invalid_payload", "webhookId cannot be null.")
            return error_with_log("delete_webhook", start_time, request_id, error)
        confirm_error = require_confirm(
            confirm,
            "delete_webhook",
            start_time,
            request_id,
            warnings=warnings,
        )
        if confirm_error:
            return confirm_error
        writes_error = require_writes_enabled(
            "delete_webhook", start_time, request_id, warnings=warnings
        )
        if writes_error:
            return writes_error

        client = await get_client()
        webhook = await retry_read(
            "fetch_webhook", lambda: client.fetch_webhook(int(webhook_id))
        )
        if webhook is None:
            error = build_error("not_found", "Webhook not found by webhookId.")
            return error_with_log(
                "delete_webhook", start_time, request_id, error, warnings=warnings
            )
        name = webhook.name
        await webhook.delete()
        record_api_success("delete_webhook")
        log_action("delete_webhook", start_time, "ok")
        meta = build_meta(start_time, request_id=request_id, warnings=warnings)
        data = {"webhook_id": str(webhook.id), "name": name}
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "delete_webhook",
            start_time,
            request_id,
            error,
            warnings=warnings,
        )


@mcp.tool()
async def list_webhooks(channel_id: str, confirm: str = "") -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    resolved_channel_id = None
    try:
        resolved_channel_id = resolve_channel_id(channel_id)
        confirm_error = require_confirm(
            confirm,
            "list_webhooks",
            start_time,
            request_id,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )
        if confirm_error:
            return confirm_error
        allow_error = require_write_allowed(
            resolved_channel_id,
            "list_webhooks",
            start_time,
            request_id,
            warnings=warnings,
        )
        if allow_error:
            return allow_error

        channel = await get_text_channel(resolved_channel_id)
        webhooks = await retry_read("list_webhooks", lambda: channel.webhooks())
        if not webhooks:
            error = build_error("not_found", "No webhooks found.")
            return error_with_log(
                "list_webhooks",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )
        payload = [
            {"id": str(w.id), "name": w.name, "url": w.url} for w in webhooks
        ]
        log_action(
            "list_webhooks",
            start_time,
            "ok",
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        data = {"count": len(payload), "webhooks": payload}
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "list_webhooks",
            start_time,
            request_id,
            error,
            warnings=warnings,
            channel_id=resolved_channel_id,
        )


@mcp.tool()
async def send_webhook_message(
    webhook_url: str,
    message: str,
    confirm: str = "",
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings = []
    try:
        if not webhook_url:
            error = build_error("invalid_payload", "webhookUrl cannot be null.")
            return error_with_log("send_webhook_message", start_time, request_id, error)
        if not message:
            error = build_error("invalid_payload", "message cannot be null.")
            return error_with_log("send_webhook_message", start_time, request_id, error)

        confirm_error = require_confirm(
            confirm,
            "send_webhook_message",
            start_time,
            request_id,
            warnings=warnings,
        )
        if confirm_error:
            return confirm_error
        writes_error = require_writes_enabled(
            "send_webhook_message", start_time, request_id, warnings=warnings
        )
        if writes_error:
            return writes_error

        async with aiohttp.ClientSession() as session:
            webhook = discord.Webhook.from_url(webhook_url, session=session)
            sent = await webhook.send(message, wait=True)
        record_api_success("send_webhook_message")
        log_action("send_webhook_message", start_time, "ok")
        meta = build_meta(start_time, request_id=request_id, warnings=warnings)
        data = {
            "message_id": str(sent.id) if sent else None,
            "jump_url": sent.jump_url if sent else None,
        }
        return success_response(data, meta)
    except Exception as exc:
        error = exception_to_error(exc)
        return error_with_log(
            "send_webhook_message",
            start_time,
            request_id,
            error,
            warnings=warnings,
        )


if __name__ == "__main__":
    os.environ.setdefault("HOST", MCP_BIND_ADDRESS)
    os.environ.setdefault("PORT", str(MCP_HTTP_PORT))
    app_factory = mcp.streamable_http_app

    def build_app():
        app = app_factory() if callable(app_factory) else app_factory
        try:
            app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])
        except Exception:
            pass

        async def host_override(scope, receive, send):
            if scope["type"] == "http":
                if scope.get("path") == "/health":
                    body = b'{"status":"ok"}'
                    headers = [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ]
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 200,
                            "headers": headers,
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    return
                headers = []
                for key, value in scope.get("headers", []):
                    if key.lower() == b"host":
                        continue
                    headers.append((key, value))
                headers.append((b"host", b"localhost"))
                scope = {**scope, "headers": headers}
            await app(scope, receive, send)

        return host_override

    uvicorn.run(build_app, host=MCP_BIND_ADDRESS, port=MCP_HTTP_PORT, factory=True)
