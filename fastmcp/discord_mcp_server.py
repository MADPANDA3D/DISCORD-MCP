import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import commands
from mcp.server.fastmcp import FastMCP
import uvicorn
from starlette.middleware.trustedhost import TrustedHostMiddleware


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DEFAULT_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()
MCP_HTTP_PORT = int(os.getenv("MCP_HTTP_PORT", "8085"))
MCP_BIND_ADDRESS = os.getenv("MCP_BIND_ADDRESS", "0.0.0.0")
DISCORD_PRIMARY_CHANNEL_ID_RAW = os.getenv("DISCORD_PRIMARY_CHANNEL_ID", "").strip()
DISCORD_ALLOWED_CHANNEL_IDS_RAW = os.getenv("DISCORD_ALLOWED_CHANNEL_IDS", "").strip()
MCP_ADMIN_TOOLS_ENABLED = os.getenv("MCP_ADMIN_TOOLS_ENABLED", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("discord_mcp")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set")


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    return intents


def create_bot() -> commands.Bot:
    return commands.Bot(command_prefix="!", intents=build_intents())


bot = create_bot()
mcp = FastMCP(
    name="discord-mcp",
    stateless_http=True,
    json_response=True,
    host="0.0.0.0",
)
bot_task = None
bot_lock = asyncio.Lock()
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


def normalize_channel_key(value: str) -> str:
    return "".join(ch for ch in value.lower().strip() if ch.isalnum())


async def reset_bot(reason: str):
    global bot, bot_task
    old_bot = bot
    if old_bot is not None and not old_bot.is_closed():
        try:
            await old_bot.close()
        except Exception:
            pass
    bot = create_bot()
    bot_task = None
    logger.warning("bot_reset reason=%s", reason)


async def wait_until_ready_safe(
    client: commands.Bot,
    timeout_seconds: float = 8.0,
    poll_interval: float = 0.2,
):
    deadline = time.monotonic() + timeout_seconds
    last_exc = None
    while time.monotonic() < deadline:
        if client.is_ready():
            return
        try:
            await asyncio.wait_for(client.wait_until_ready(), timeout=poll_interval)
            return
        except asyncio.TimeoutError:
            continue
        except discord.ClientException as exc:
            last_exc = exc
            if "properly initialised" in str(exc):
                await asyncio.sleep(poll_interval)
                continue
            raise
    if last_exc:
        raise last_exc
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
            channels = await guild.fetch_channels()
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
        error = None if ok else result
    except Exception as exc:
        result = None
        status = "failed"
        error = exception_to_error(exc)
    async with JOB_LOCK:
        job = JOB_STORE.get(job_id)
        if job:
            job["status"] = status
            job["finished_at"] = job_timestamp()
            job["finished_at_ts"] = time.time()
            job["result"] = result
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


PRIMARY_CHANNEL_ID = parse_snowflake(DISCORD_PRIMARY_CHANNEL_ID_RAW)
if DISCORD_PRIMARY_CHANNEL_ID_RAW and PRIMARY_CHANNEL_ID is None:
    CONFIG_WARNINGS.append("Invalid DISCORD_PRIMARY_CHANNEL_ID configured.")
ALLOW_ALL_CHANNELS, ALLOWED_CHANNEL_IDS, allowlist_warnings = parse_allowed_channel_ids(
    DISCORD_ALLOWED_CHANNEL_IDS_RAW
)
CONFIG_WARNINGS.extend(allowlist_warnings)


HEALTH_CHECK_SAMPLE_LIMIT = 5


def effective_allowed_channel_ids(guild: discord.Guild | None = None) -> list[int]:
    ids = []
    if PRIMARY_CHANNEL_ID is not None:
        ids.append(PRIMARY_CHANNEL_ID)
    if ALLOW_ALL_CHANNELS:
        if guild is not None:
            sample_count = 0
            for channel in guild.text_channels:
                if channel.id in ids:
                    continue
                ids.append(channel.id)
                sample_count += 1
                if sample_count >= HEALTH_CHECK_SAMPLE_LIMIT:
                    break
        return ids
    ids.extend(sorted(ALLOWED_CHANNEL_IDS))
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


def is_channel_allowed(channel_id: int) -> bool:
    if ALLOW_ALL_CHANNELS:
        return True
    if not ALLOWED_CHANNEL_IDS:
        return True
    if PRIMARY_CHANNEL_ID is not None and channel_id == PRIMARY_CHANNEL_ID:
        return True
    return channel_id in ALLOWED_CHANNEL_IDS


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
    http = getattr(bot, "http", None)
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


def success_response(**payload) -> dict:
    response = {"ok": True}
    response.update(payload)
    return response


def error_response(
    error_type: str,
    message: str,
    guild_id: int | None = None,
    channel_id: int | None = None,
    required_perms: list[str] | None = None,
    discord_code: int | None = None,
    diagnostics: dict | None = None,
) -> dict:
    error = {"type": error_type, "message": message}
    if guild_id is not None:
        error["guild_id"] = str(guild_id)
    if channel_id is not None:
        error["channel_id"] = str(channel_id)
    if required_perms:
        error["required_perms"] = list(required_perms)
    if discord_code is not None:
        error["discord_error_code"] = discord_code
    response = {"ok": False, "error": error}
    if diagnostics:
        response["diagnostics"] = diagnostics
    return response


def error_with_log(
    action: str,
    start_time: float,
    error: dict,
    guild_id: int | None = None,
    channel_id: int | None = None,
) -> dict:
    log_action(
        action,
        start_time,
        "error",
        guild_id=guild_id,
        channel_id=channel_id,
        error_type=error.get("error", {}).get("type"),
    )
    return error


def exception_to_error(
    exc: Exception,
    guild_id: int | None = None,
    channel_id: int | None = None,
    required_perms: list[str] | None = None,
    diagnostics: dict | None = None,
) -> dict:
    if isinstance(exc, discord.Forbidden):
        return error_response(
            "permission_denied",
            "Permission denied by Discord API.",
            guild_id=guild_id,
            channel_id=channel_id,
            required_perms=required_perms,
            discord_code=getattr(exc, "code", None),
            diagnostics=diagnostics,
        )
    if isinstance(exc, discord.NotFound):
        return error_response(
            "not_found",
            "Discord resource not found.",
            guild_id=guild_id,
            channel_id=channel_id,
            discord_code=getattr(exc, "code", None),
            diagnostics=diagnostics,
        )
    if isinstance(exc, discord.HTTPException):
        if getattr(exc, "status", None) == 429:
            update_rate_limit_from_exception(exc)
            return error_response(
                "rate_limited",
                "Discord rate limit exceeded.",
                guild_id=guild_id,
                channel_id=channel_id,
                discord_code=getattr(exc, "code", None),
                diagnostics=diagnostics,
            )
        return error_response(
            "invalid_payload",
            f"Discord API error (status {getattr(exc, 'status', 'unknown')}).",
            guild_id=guild_id,
            channel_id=channel_id,
            discord_code=getattr(exc, "code", None),
            diagnostics=diagnostics,
        )
    if isinstance(exc, ValueError):
        return error_response(
            "invalid_payload",
            str(exc),
            guild_id=guild_id,
            channel_id=channel_id,
            diagnostics=diagnostics,
        )
    return error_response(
        "invalid_payload",
        "Unexpected error.",
        guild_id=guild_id,
        channel_id=channel_id,
        diagnostics=diagnostics,
    )


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


def is_http_session_closed(client: commands.Bot) -> bool:
    http = getattr(client, "http", None)
    session = getattr(http, "_session", None)
    return bool(session is not None and getattr(session, "closed", False))


async def get_client() -> commands.Bot:
    global bot, bot_task
    if bot is not None and bot.is_ready() and not is_http_session_closed(bot):
        return bot
    async with bot_lock:
        if bot_task is not None and bot_task.done():
            try:
                exc = bot_task.exception()
            except Exception as exc:
                exc = exc
            if exc:
                logger.warning("bot_task_failed err=%s", exc)
                await reset_bot("bot_task_failed")
        if bot is None or bot.is_closed() or is_http_session_closed(bot):
            await reset_bot("bot_closed_or_session")
        if bot_task is None or bot_task.done():
            bot_task = asyncio.create_task(bot.start(DISCORD_TOKEN))
    await wait_until_ready_safe(bot)
    return bot


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
    snapshot = {
        "client_id": id(bot) if bot is not None else None,
        "is_ready": bot.is_ready() if bot is not None else False,
        "is_closed": bot.is_closed() if bot is not None else True,
        "session_closed": is_http_session_closed(bot) if bot is not None else None,
        "lock_locked": bot_lock.locked(),
    }
    if bot_task is None:
        snapshot["task_state"] = "none"
    else:
        snapshot["task_state"] = "done" if bot_task.done() else "running"
        snapshot["task_cancelled"] = bot_task.cancelled()
        if bot_task.done():
            try:
                exc = bot_task.exception()
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
    if (not guild_id or not str(guild_id).strip()) and DEFAULT_GUILD_ID:
        guild_id = DEFAULT_GUILD_ID
    parsed = parse_snowflake(guild_id)
    if parsed is None:
        if guild_id and str(guild_id).strip():
            raise ValueError("guildId must be a Discord snowflake")
        raise ValueError("guildId cannot be null")
    return parsed


async def get_guild(
    guild_id: str | None, client: commands.Bot | None = None
) -> discord.Guild:
    if client is None:
        client = await get_client()
    resolved_id = resolve_guild_id(guild_id)
    guild = client.get_guild(resolved_id)
    if guild is None:
        guild = await client.fetch_guild(resolved_id)
        record_api_success("fetch_guild")
    if guild is None:
        raise ValueError("Discord server not found by guildId")
    return guild


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
        channel = await client.fetch_channel(resolved_id)
        record_api_success("fetch_channel")
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("Channel not found by channelId")
    return channel


async def get_message_target(channel_id: int | str):
    client = await get_client()
    resolved_id = parse_snowflake(channel_id)
    if resolved_id is None:
        raise ValueError("channelId cannot be null")
    channel = client.get_channel(resolved_id)
    if channel is None:
        channel = await client.fetch_channel(resolved_id)
        record_api_success("fetch_channel")
    if channel is None or not hasattr(channel, "send"):
        raise ValueError("Channel is not messageable")
    return channel


def is_message_target_allowed(channel) -> bool:
    if ALLOW_ALL_CHANNELS or not ALLOWED_CHANNEL_IDS:
        return True
    if isinstance(channel, discord.Thread):
        parent_id = channel.parent_id
        if parent_id:
            return is_channel_allowed(parent_id)
        return False
    return is_channel_allowed(channel.id)


async def get_dm_channel(user_id: str, client: commands.Bot | None = None) -> discord.DMChannel:
    if client is None:
        client = await get_client()
    if not user_id:
        raise ValueError("userId cannot be null")
    user = await client.fetch_user(int(user_id))
    if user is None:
        raise ValueError("User not found by userId")
    return await user.create_dm()


@mcp.tool()
async def get_server_info(guild_id: str = "") -> str:
    guild = await get_guild(guild_id)
    owner_name = "unknown"
    if guild.owner is not None:
        owner_name = guild.owner.name
    elif guild.owner_id:
        try:
            owner_member = await guild.fetch_member(guild.owner_id)
            owner_name = owner_member.name
        except Exception:
            owner_name = f"ID {guild.owner_id}"
    creation_date = guild.created_at.astimezone(timezone.utc).date().isoformat()
    boost_count = guild.premium_subscription_count or 0
    boost_tier = str(guild.premium_tier)

    return "\n".join([
        f"Server Name: {guild.name}",
        f"Server ID: {guild.id}",
        f"Owner: {owner_name}",
        f"Created On: {creation_date}",
        f"Members: {guild.member_count}",
        "Channels: ",
        f" - Text: {len(guild.text_channels)}",
        f" - Voice: {len(guild.voice_channels)}",
        f"  - Categories: {len(guild.categories)}",
        "Boosts: ",
        f" - Count: {boost_count}",
        f" - Tier: {boost_tier}",
    ])


@mcp.tool()
async def discord_health_check(guild_id: str = "") -> dict:
    start_time = time.perf_counter()
    warnings = list(CONFIG_WARNINGS)
    capabilities = {}
    guild_info = {"id": None, "name": None, "found": False}
    bot_info = {"user": None, "application": None}
    primary_ok = False
    any_allowed_ok = False
    effective_allowed = []
    discord_config = {
        "primary_channel_id": (
            str(PRIMARY_CHANNEL_ID) if PRIMARY_CHANNEL_ID is not None else None
        ),
        "allowed_channel_ids": (
            ["ALL"] if ALLOW_ALL_CHANNELS else [str(cid) for cid in sorted(ALLOWED_CHANNEL_IDS)]
        ),
        "allow_all_channels": ALLOW_ALL_CHANNELS,
        "admin_tools_enabled": MCP_ADMIN_TOOLS_ENABLED,
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
        warnings.append("DISCORD_ALLOWED_CHANNEL_IDS is not configured.")

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

        guild = None
        if guild_id or DEFAULT_GUILD_ID:
            try:
                guild = await get_guild(guild_id)
                guild_info = {
                    "id": str(guild.id),
                    "name": guild.name,
                    "found": True,
                }
            except Exception as exc:
                warnings.append(f"Guild lookup failed: {exc}")
        else:
            warnings.append("DISCORD_GUILD_ID not set; guild checks skipped.")

        if ALLOW_ALL_CHANNELS and guild is None:
            warnings.append("Allow-all enabled but guild unavailable for sampling.")

        effective_allowed = effective_allowed_channel_ids(
            guild if guild_info["found"] else None
        )
        if not effective_allowed:
            warnings.append("No channels available for capability checks.")
        for channel_id in sorted(effective_allowed):
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
                if channel_id == PRIMARY_CHANNEL_ID and (
                    not caps["read_history"] or not caps["send"]
                ):
                    warnings.append(
                        f"Primary channel {channel_id} lacks read_history/send permission."
                    )
                if (not caps["read_history"] or not caps["send"]) and (
                    ALLOW_ALL_CHANNELS or channel_id in ALLOWED_CHANNEL_IDS
                ):
                    label = "Sample channel" if ALLOW_ALL_CHANNELS else "Allowed channel"
                    warnings.append(
                        f"{label} {channel_id} lacks read_history/send permission."
                    )
                if caps["read_history"] and caps["send"]:
                    if PRIMARY_CHANNEL_ID is not None and channel_id == PRIMARY_CHANNEL_ID:
                        primary_ok = True
                    if ALLOW_ALL_CHANNELS or channel_id in ALLOWED_CHANNEL_IDS:
                        any_allowed_ok = True
            except Exception as exc:
                capabilities[str(channel_id)] = {"found": False, "error": str(exc)}
                warnings.append(f"Channel {channel_id} unavailable: {exc}")

        if PRIMARY_CHANNEL_ID is not None:
            ok = primary_ok
        elif ALLOW_ALL_CHANNELS or ALLOWED_CHANNEL_IDS:
            ok = any_allowed_ok
        else:
            ok = False

        if ok and not warnings:
            status = "green"
        elif ok:
            status = "yellow"
        else:
            status = "red"

        response = {
            "ok": ok,
            "status": status,
            "warnings": warnings,
            "bot": bot_info,
            "guild": guild_info,
            "primary_channel_id": (
                str(PRIMARY_CHANNEL_ID) if PRIMARY_CHANNEL_ID is not None else None
            ),
            "allowed_channel_ids": (
                ["ALL"]
                if ALLOW_ALL_CHANNELS
                else [str(cid) for cid in sorted(ALLOWED_CHANNEL_IDS)]
            ),
            "admin_tools_enabled": MCP_ADMIN_TOOLS_ENABLED,
            "discord_config": discord_config,
            "capabilities": capabilities,
            "rate_limit": get_rate_limit_snapshot(),
            "last_successful_api_at": LAST_SUCCESSFUL_API_AT,
        }
        log_action("discord_health_check", start_time, "ok", guild_id=None)
        return response
    except Exception as exc:
        warnings.append(str(exc))
        response = {
            "ok": False,
            "status": "red",
            "warnings": warnings,
            "bot": bot_info,
            "guild": guild_info,
            "capabilities": capabilities,
            "discord_config": discord_config,
            "rate_limit": get_rate_limit_snapshot(),
            "last_successful_api_at": LAST_SUCCESSFUL_API_AT,
        }
        log_action("discord_health_check", start_time, "error", error_type="invalid_payload")
        return response


@mcp.tool()
async def discord_ack(
    channel_id: str = "",
    message: str = "",
    include_timestamp: bool | str = True,
) -> dict:
    start_time = time.perf_counter()
    resolved_channel_id = None
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
        if not allowed and not MCP_ADMIN_TOOLS_ENABLED:
            error = error_response(
                "permission_denied",
                "Channel is not in allowlist.",
                channel_id=resolved_channel_id,
                required_perms=["allowlisted_channel"],
                diagnostics=diagnostics,
            )
            return error_with_log(
                "discord_ack", start_time, error, channel_id=resolved_channel_id
            )

        sent = await target.send(ack_message)
        record_api_success("discord_ack")
        log_action(
            "discord_ack",
            start_time,
            "ok",
            channel_id=resolved_channel_id,
        )
        return success_response(
            channel_id=str(target.id),
            message_id=str(sent.id),
            jump_url=sent.jump_url,
        )
    except Exception as exc:
        error = exception_to_error(exc, channel_id=resolved_channel_id)
        return error_with_log(
            "discord_ack", start_time, error, channel_id=resolved_channel_id
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
) -> dict:
    start_time = time.perf_counter()
    resolved_channel_id = None
    try:
        dry_run = parse_bool(dry_run)
        resolved_channel_id = resolve_channel_id(channel_id)

        if not message and not embed_title and not embed_description:
            error = error_response(
                "invalid_payload",
                "message or embed content must be provided.",
                channel_id=resolved_channel_id,
            )
            return error_with_log(
                "send_message", start_time, error, channel_id=resolved_channel_id
            )

        allowed = is_channel_allowed(resolved_channel_id)
        diagnostics = {
            "resolved_channel_id": str(resolved_channel_id),
            "allowed_channel": allowed,
        }
        if not allowed and not MCP_ADMIN_TOOLS_ENABLED:
            error = error_response(
                "permission_denied",
                "Channel is not in allowlist.",
                channel_id=resolved_channel_id,
                required_perms=["allowlisted_channel"],
                diagnostics=diagnostics,
            )
            return error_with_log(
                "send_message", start_time, error, channel_id=resolved_channel_id
            )

        channel = await get_text_channel(resolved_channel_id)
        client = await get_client()
        member = await get_bot_member(channel.guild)
        perms = (
            channel.permissions_for(member)
            if member is not None
            else discord.Permissions.none()
        )
        caps = channel_capabilities(perms)
        diagnostics["permissions"] = caps

        if not perms.send_messages:
            error = error_response(
                "permission_denied",
                "Missing permission to send messages.",
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
                required_perms=["send_messages"],
                diagnostics=diagnostics,
            )
            return error_with_log(
                "send_message",
                start_time,
                error,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )

        thread_if_split = parse_bool(thread_if_split)
        thread_name = (thread_name or "").strip()

        has_embed = bool(embed_title or embed_description or embed_color)
        embed_title_length = len(embed_title) if embed_title else 0
        embed_description_length = len(embed_description) if embed_description else 0
        content_length = len(message) if message else 0
        content_parts = split_text(message, 2000) if message else []
        embed_parts = split_text(embed_description, 4096) if embed_description else []
        planned_parts = max(len(content_parts), len(embed_parts), 1)
        will_split = planned_parts > 1
        warnings = []

        diagnostics.update(
            {
                "content_length": content_length,
                "content_limit": 2000,
                "embed_title_length": embed_title_length,
                "embed_title_limit": 256,
                "embed_description_length": embed_description_length,
                "embed_description_limit": 4096,
                "embeds_allowed": perms.embed_links,
                "planned_parts": planned_parts,
                "will_split": will_split,
                "thread_if_split": thread_if_split,
            }
        )

        if embed_title_length > 256:
            error = error_response(
                "invalid_payload",
                "embed_title exceeds 256 characters.",
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
                diagnostics=diagnostics,
            )
            return error_with_log(
                "send_message",
                start_time,
                error,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )
        if has_embed and not perms.embed_links:
            error = error_response(
                "permission_denied",
                "Missing permission to embed links.",
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
                required_perms=["embed_links"],
                diagnostics=diagnostics,
            )
            return error_with_log(
                "send_message",
                start_time,
                error,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )

        color_value = None
        if embed_color:
            color_value = parse_int(embed_color)
            if color_value is None:
                error = error_response(
                    "invalid_payload",
                    "embed_color must be an integer.",
                    guild_id=channel.guild.id,
                    channel_id=resolved_channel_id,
                    diagnostics=diagnostics,
                )
                return error_with_log(
                    "send_message",
                    start_time,
                    error,
                    guild_id=channel.guild.id,
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

        thread_planned = thread_if_split and will_split and caps["create_threads"]
        if thread_if_split and will_split and not caps["create_threads"]:
            warnings.append("create_threads permission missing; falling back to channel.")
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
            return success_response(dry_run=True, warnings=warnings, **diagnostics)

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
        return success_response(
            channel_id=str(channel.id),
            message_id=str(sent_message.id) if sent_message else None,
            sent_message_ids=sent_message_ids,
            thread_id=thread_id,
            jump_url=sent_message.jump_url if sent_message else None,
            warnings=warnings,
            planned_parts=planned_parts,
        )
    except Exception as exc:
        error = exception_to_error(
            exc,
            channel_id=resolved_channel_id,
        )
        return error_with_log(
            "send_message", start_time, error, channel_id=resolved_channel_id
        )


@mcp.tool()
async def discord_smoke_test(
    channel_id: str = "",
    include_admin: bool | str = True,
    debug: bool | str = False,
) -> dict:
    start_time = time.perf_counter()
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
        report["duration_ms"] = int((time.perf_counter() - start_time) * 1000)
        return report

    health = await discord_health_check()
    if not health.get("ok") and any(
        "properly initialised" in warning for warning in health.get("warnings", [])
    ):
        await reset_bot("smoke_test_health_retry")
        await ensure_client_ready()
        health = await discord_health_check()
    report["steps"].append(
        {
            "name": "health_check",
            "ok": bool(health.get("ok")),
            "status": health.get("status"),
            "warnings": health.get("warnings", []),
        }
    )
    if not health.get("ok"):
        report["ok"] = False

    test_message = f"MCP smoke test {datetime.now(timezone.utc).isoformat()}"
    dry_run = await send_message(
        channel_id=channel_id,
        message=test_message,
        dry_run=True,
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

    report["channel_id"] = send.get("channel_id")
    message_id = send.get("message_id")
    if not message_id and send.get("sent_message_ids"):
        message_id = send.get("sent_message_ids")[0]
    report["message_id"] = message_id

    should_delete = False
    if include_admin and MCP_ADMIN_TOOLS_ENABLED and message_id:
        edit = await edit_message(
            channel_id=report["channel_id"] or channel_id,
            message_id=message_id,
            new_message=f"{test_message} (edited)",
            confirm=True,
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
            for msg in read.get("messages", []):
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
            confirm=True,
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

    report["duration_ms"] = int((time.perf_counter() - start_time) * 1000)
    return report


@mcp.tool()
async def discord_job_submit(action: str, params: dict | None = None) -> dict:
    start_time = time.perf_counter()
    if not action:
        error = error_response("invalid_payload", "action cannot be null.")
        return error_with_log("discord_job_submit", start_time, error)
    if params is None:
        params = {}
    if not isinstance(params, dict):
        error = error_response("invalid_payload", "params must be an object.")
        return error_with_log("discord_job_submit", start_time, error)

    action_map = {
        "discord_health_check": discord_health_check,
        "discord_smoke_test": discord_smoke_test,
        "discord_ack": discord_ack,
        "get_server_info": get_server_info,
        "list_channels": list_channels,
        "find_channel": find_channel,
        "read_messages": read_messages,
        "list_threads": list_threads,
        "send_message": send_message,
    }
    action_func = action_map.get(action)
    if action_func is None:
        error = error_response(
            "invalid_payload",
            "Unsupported action.",
            diagnostics={"supported_actions": sorted(action_map.keys())},
        )
        return error_with_log("discord_job_submit", start_time, error)

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
    return success_response(
        task_id=job_id,
        status="queued",
        action=action,
    )


@mcp.tool()
async def discord_job_status(
    task_id: str,
    include_result: bool | str = False,
) -> dict:
    start_time = time.perf_counter()
    include_result = parse_bool(include_result)
    if not task_id:
        error = error_response("invalid_payload", "task_id cannot be null.")
        return error_with_log("discord_job_status", start_time, error)
    async with JOB_LOCK:
        await prune_jobs_locked()
        job = JOB_STORE.get(task_id)
        if job is None:
            error = error_response("not_found", "Job not found.")
            return error_with_log("discord_job_status", start_time, error)
        snapshot = build_job_snapshot(job, include_result)

    log_action("discord_job_status", start_time, "ok")
    return success_response(**snapshot)


@mcp.tool()
async def edit_message(
    channel_id: str = "",
    message_id: str = "",
    new_message: str = "",
    confirm: bool | str = False,
    dry_run: bool | str = False,
) -> dict:
    start_time = time.perf_counter()
    resolved_channel_id = None
    try:
        confirm = parse_bool(confirm)
        dry_run = parse_bool(dry_run)
        if not MCP_ADMIN_TOOLS_ENABLED:
            error = error_response(
                "permission_denied",
                "MCP_ADMIN_TOOLS_ENABLED must be true to edit messages.",
                required_perms=["MCP_ADMIN_TOOLS_ENABLED", "confirm=true"],
            )
            return error_with_log("edit_message", start_time, error)
        if not confirm:
            error = error_response(
                "permission_denied",
                "confirm=true is required to edit messages.",
                required_perms=["confirm=true"],
            )
            return error_with_log("edit_message", start_time, error)
        if not message_id:
            error = error_response("invalid_payload", "messageId cannot be null.")
            return error_with_log("edit_message", start_time, error)
        if not new_message:
            error = error_response("invalid_payload", "newMessage cannot be null.")
            return error_with_log("edit_message", start_time, error)

        resolved_channel_id = resolve_channel_id(channel_id)
        allowed = is_channel_allowed(resolved_channel_id)
        diagnostics = {
            "resolved_channel_id": str(resolved_channel_id),
            "allowed_channel": allowed,
            "content_length": len(new_message),
            "content_limit": 2000,
        }

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
            error = error_response(
                "invalid_payload",
                "newMessage exceeds 2000 characters.",
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
                diagnostics=diagnostics,
            )
            return error_with_log(
                "edit_message",
                start_time,
                error,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )

        msg = await channel.fetch_message(int(message_id))
        if msg is None:
            error = error_response(
                "not_found",
                "Message not found by messageId.",
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
                diagnostics=diagnostics,
            )
            return error_with_log(
                "edit_message",
                start_time,
                error,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )

        if client.user and msg.author.id != client.user.id and not perms.manage_messages:
            error = error_response(
                "permission_denied",
                "Missing permission to manage messages.",
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
                required_perms=["manage_messages"],
                diagnostics=diagnostics,
            )
            return error_with_log(
                "edit_message",
                start_time,
                error,
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
            return success_response(dry_run=True, **diagnostics)

        edited = await msg.edit(content=new_message)
        record_api_success("edit_message")
        log_action(
            "edit_message",
            start_time,
            "ok",
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        return success_response(
            channel_id=str(channel.id),
            message_id=str(edited.id),
            jump_url=edited.jump_url,
        )
    except Exception as exc:
        error = exception_to_error(
            exc,
            channel_id=resolved_channel_id,
            diagnostics=diagnostics if diagnostics else None,
        )
        return error_with_log(
            "edit_message", start_time, error, channel_id=resolved_channel_id
        )


@mcp.tool()
async def delete_message(
    channel_id: str = "",
    message_id: str = "",
    confirm: bool | str = False,
    dry_run: bool | str = False,
) -> dict:
    start_time = time.perf_counter()
    resolved_channel_id = None
    try:
        confirm = parse_bool(confirm)
        dry_run = parse_bool(dry_run)
        if not MCP_ADMIN_TOOLS_ENABLED:
            error = error_response(
                "permission_denied",
                "MCP_ADMIN_TOOLS_ENABLED must be true to delete messages.",
                required_perms=["MCP_ADMIN_TOOLS_ENABLED", "confirm=true"],
            )
            return error_with_log("delete_message", start_time, error)
        if not confirm:
            error = error_response(
                "permission_denied",
                "confirm=true is required to delete messages.",
                required_perms=["confirm=true"],
            )
            return error_with_log("delete_message", start_time, error)
        if not message_id:
            error = error_response("invalid_payload", "messageId cannot be null.")
            return error_with_log("delete_message", start_time, error)

        resolved_channel_id = resolve_channel_id(channel_id)
        allowed = is_channel_allowed(resolved_channel_id)
        diagnostics = {
            "resolved_channel_id": str(resolved_channel_id),
            "allowed_channel": allowed,
        }

        channel = await get_text_channel(resolved_channel_id)
        client = await get_client()
        member = await get_bot_member(channel.guild)
        perms = (
            channel.permissions_for(member)
            if member is not None
            else discord.Permissions.none()
        )
        diagnostics["permissions"] = channel_capabilities(perms)

        msg = await channel.fetch_message(int(message_id))
        if msg is None:
            error = error_response(
                "not_found",
                "Message not found by messageId.",
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
                diagnostics=diagnostics,
            )
            return error_with_log(
                "delete_message",
                start_time,
                error,
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
            )

        if client.user and msg.author.id != client.user.id and not perms.manage_messages:
            error = error_response(
                "permission_denied",
                "Missing permission to manage messages.",
                guild_id=channel.guild.id,
                channel_id=resolved_channel_id,
                required_perms=["manage_messages"],
                diagnostics=diagnostics,
            )
            return error_with_log(
                "delete_message",
                start_time,
                error,
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
            return success_response(dry_run=True, **diagnostics)

        await msg.delete()
        record_api_success("delete_message")
        log_action(
            "delete_message",
            start_time,
            "ok",
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        return success_response(
            channel_id=str(channel.id),
            message_id=str(msg.id),
        )
    except Exception as exc:
        error = exception_to_error(
            exc,
            channel_id=resolved_channel_id,
        )
        return error_with_log(
            "delete_message", start_time, error, channel_id=resolved_channel_id
        )


@mcp.tool()
async def read_messages(
    channel_id: str = "",
    count: str = "",
    before_message_id: str = "",
) -> dict:
    start_time = time.perf_counter()
    resolved_channel_id = None
    try:
        resolved_channel_id = resolve_channel_id(channel_id)
        channel = await get_text_channel(resolved_channel_id)
        limit = parse_int(count, 100)
        if limit is None or limit <= 0:
            error = error_response(
                "invalid_payload",
                "count must be a positive integer.",
                channel_id=resolved_channel_id,
            )
            return error_with_log(
                "read_messages", start_time, error, channel_id=resolved_channel_id
            )
        before_id = None
        if before_message_id:
            before_id = parse_snowflake(before_message_id)
            if before_id is None:
                error = error_response(
                    "invalid_payload",
                    "before_message_id must be a Discord snowflake.",
                    channel_id=resolved_channel_id,
                )
                return error_with_log(
                    "read_messages", start_time, error, channel_id=resolved_channel_id
                )
        before_obj = discord.Object(id=before_id) if before_id else None
        messages = [m async for m in channel.history(limit=limit, before=before_obj)]
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
            }
            for msg in messages
        ]
        log_action(
            "read_messages",
            start_time,
            "ok",
            guild_id=channel.guild.id,
            channel_id=resolved_channel_id,
        )
        return success_response(
            channel_id=str(channel.id),
            count=len(messages),
            before_message_id=str(before_id) if before_id else None,
            messages=payload,
        )
    except Exception as exc:
        error = exception_to_error(exc, channel_id=resolved_channel_id)
        return error_with_log(
            "read_messages", start_time, error, channel_id=resolved_channel_id
        )


@mcp.tool()
async def list_threads(
    channel_id: str = "",
    include_archived: bool | str = False,
    limit: str = "",
) -> dict:
    start_time = time.perf_counter()
    resolved_channel_id = None
    try:
        include_archived = parse_bool(include_archived)
        resolved_channel_id = resolve_channel_id(channel_id)
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
            error = error_response(
                "invalid_payload",
                "Threads are not supported for this channel.",
                channel_id=resolved_channel_id,
            )
            return error_with_log(
                "list_threads", start_time, error, channel_id=resolved_channel_id
            )

        if include_archived and hasattr(channel, "archived_threads"):
            archived_limit = parse_int(limit, 50)
            async for thread in channel.archived_threads(limit=archived_limit):
                threads.append(thread)

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
        return success_response(
            channel_id=str(channel.id),
            count=len(payload),
            threads=payload,
        )
    except Exception as exc:
        error = exception_to_error(exc, channel_id=resolved_channel_id)
        return error_with_log(
            "list_threads", start_time, error, channel_id=resolved_channel_id
        )


@mcp.tool()
async def add_reaction(channel_id: str, message_id: str, emoji: str) -> str:
    if not emoji:
        raise ValueError("emoji cannot be null")
    channel = await get_text_channel(channel_id)
    msg = await channel.fetch_message(int(message_id))
    if msg is None:
        raise ValueError("Message not found by messageId")
    await msg.add_reaction(emoji)
    return f"Added reaction successfully. Message link: {msg.jump_url}"


@mcp.tool()
async def remove_reaction(channel_id: str, message_id: str, emoji: str) -> str:
    if not emoji:
        raise ValueError("emoji cannot be null")
    channel = await get_text_channel(channel_id)
    client = await get_client()
    msg = await channel.fetch_message(int(message_id))
    if msg is None:
        raise ValueError("Message not found by messageId")
    await msg.remove_reaction(emoji, client.user)
    return f"Removed reaction successfully. Message link: {msg.jump_url}"


@mcp.tool()
async def get_user_id_by_name(username: str, guild_id: str = "") -> str:
    if not username:
        raise ValueError("username cannot be null")
    guild = await get_guild(guild_id)
    name = username
    discriminator = None
    if "#" in username:
        idx = username.rfind("#")
        name = username[:idx]
        discriminator = username[idx + 1 :]

    members = [
        m async for m in guild.fetch_members(limit=None)
        if m.name.lower() == name.lower()
    ]
    if discriminator:
        members = [m for m in members if m.discriminator == discriminator]

    if not members:
        raise ValueError(f"No user found with username {username}")
    if len(members) > 1:
        user_list = ", ".join(
            f"{m.name}#{m.discriminator} (ID: {m.id})" for m in members
        )
        raise ValueError(
            f"Multiple users found with username '{username}'. List: {user_list}. "
            "Please specify the full username#discriminator."
        )
    return str(members[0].id)


@mcp.tool()
async def send_private_message(user_id: str, message: str) -> str:
    if not message:
        raise ValueError("message cannot be null")
    dm = await get_dm_channel(user_id)
    sent = await dm.send(message)
    return f"Message sent successfully. Message link: {sent.jump_url}"


@mcp.tool()
async def edit_private_message(user_id: str, message_id: str, new_message: str) -> str:
    if not new_message:
        raise ValueError("newMessage cannot be null")
    dm = await get_dm_channel(user_id)
    msg = await dm.fetch_message(int(message_id))
    if msg is None:
        raise ValueError("Message not found by messageId")
    edited = await msg.edit(content=new_message)
    return f"Message edited successfully. Message link: {edited.jump_url}"


@mcp.tool()
async def delete_private_message(user_id: str, message_id: str) -> str:
    dm = await get_dm_channel(user_id)
    msg = await dm.fetch_message(int(message_id))
    if msg is None:
        raise ValueError("Message not found by messageId")
    await msg.delete()
    return "Message deleted successfully"


@mcp.tool()
async def read_private_messages(user_id: str, count: str = "") -> str:
    dm = await get_dm_channel(user_id)
    limit = int(count) if count else 100
    messages = [m async for m in dm.history(limit=limit)]
    formatted = []
    for msg in messages:
        formatted.append(
            f"- (ID: {msg.id}) **[{msg.author.name}]** `{msg.created_at}`: ```{msg.content}```"
        )
    return f"**Retrieved {len(messages)} messages:** \n" + "\n".join(formatted)


@mcp.tool()
async def create_text_channel(name: str, guild_id: str = "", category_id: str = "") -> str:
    if not name:
        raise ValueError("name cannot be null")
    guild = await get_guild(guild_id)
    category = None
    if category_id:
        category = discord.utils.get(guild.categories, id=int(category_id))
        if category is None:
            category = await guild.fetch_channel(int(category_id))
        if not isinstance(category, discord.CategoryChannel):
            raise ValueError("Category not found by categoryId")
    channel = await guild.create_text_channel(name, category=category)
    if category:
        return f"Created new text channel: {channel.name} (ID: {channel.id}) in category: {category.name}"
    return f"Created new text channel: {channel.name} (ID: {channel.id})"


@mcp.tool()
async def delete_channel(channel_id: str, guild_id: str = "") -> str:
    guild = await get_guild(guild_id)
    channel = guild.get_channel(int(channel_id))
    if channel is None:
        channel = await guild.fetch_channel(int(channel_id))
    if channel is None:
        raise ValueError("Channel not found by channelId")
    channel_type = channel.type.name
    channel_name = channel.name
    await channel.delete()
    return f"Deleted {channel_type} channel: {channel_name}"


@mcp.tool()
async def find_channel(channel_name: str, guild_id: str = "") -> str:
    if not channel_name:
        raise ValueError("channelName cannot be null")
    client = await get_client()
    guild = await get_guild(guild_id, client)
    query_key = normalize_channel_key(channel_name)
    if not query_key:
        raise ValueError("channelName cannot be null")
    channels_list, name_map, normalized_map = await get_cached_channels(guild)
    channels = name_map.get(channel_name.lower(), [])
    if not channels:
        channels = normalized_map.get(query_key, [])
    if not channels:
        channels = [
            c for c in channels_list if query_key in normalize_channel_key(c.name)
        ]
    if not channels:
        raise ValueError(f"No channels found with name {channel_name}")
    if len(channels) > 1:
        channel_list = "\n".join(
            f"- {c.type.name} channel: {c.name} (ID: {c.id})" for c in channels
        )
        return f"Retrieved {len(channels)} channels:\n{channel_list}"
    channel = channels[0]
    return f"Retrieved {channel.type.name} channel: {channel.name} (ID: {channel.id})"


@mcp.tool()
async def list_channels(guild_id: str = "") -> str:
    client = await get_client()
    guild = await get_guild(guild_id, client)
    channels, _, _ = await get_cached_channels(guild)
    if not channels:
        raise ValueError("No channels found by guildId")
    channel_list = "\n".join(
        f"- {c.type.name} channel: {c.name} (ID: {c.id})" for c in channels
    )
    return f"Retrieved {len(channels)} channels:\n{channel_list}"


@mcp.tool()
async def create_category(name: str, guild_id: str = "") -> str:
    if not name:
        raise ValueError("name cannot be null")
    guild = await get_guild(guild_id)
    category = await guild.create_category(name)
    return f"Created new category: {category.name}"


@mcp.tool()
async def delete_category(category_id: str, guild_id: str = "") -> str:
    guild = await get_guild(guild_id)
    category = discord.utils.get(guild.categories, id=int(category_id))
    if category is None:
        category = await guild.fetch_channel(int(category_id))
    if not isinstance(category, discord.CategoryChannel):
        raise ValueError("Category not found by categoryId")
    name = category.name
    await category.delete()
    return f"Deleted category: {name}"


@mcp.tool()
async def find_category(category_name: str, guild_id: str = "") -> str:
    if not category_name:
        raise ValueError("categoryName cannot be null")
    guild = await get_guild(guild_id)
    categories = [c for c in guild.categories if c.name.lower() == category_name.lower()]
    if not categories:
        raise ValueError(f"Category {category_name} not found")
    if len(categories) > 1:
        channel_list = ", ".join(f"**{c.name}** - `{c.id}`" for c in categories)
        raise ValueError(
            f"Multiple channels found with name {category_name}.\n"
            f"List: {channel_list}.\nPlease specify the channel ID."
        )
    category = categories[0]
    return f"Retrieved category: {category.name}, with ID: {category.id}"


@mcp.tool()
async def list_channels_in_category(category_id: str, guild_id: str = "") -> str:
    guild = await get_guild(guild_id)
    category = discord.utils.get(guild.categories, id=int(category_id))
    if category is None:
        category = await guild.fetch_channel(int(category_id))
    if not isinstance(category, discord.CategoryChannel):
        raise ValueError("Category not found by categoryId")
    channels = category.channels
    if not channels:
        raise ValueError("Category not contains any channels")
    channel_list = "\n".join(
        f"- {c.type.name} channel: {c.name} (ID: {c.id})" for c in channels
    )
    return f"Retrieved {len(channels)} channels:\n{channel_list}"


@mcp.tool()
async def create_webhook(channel_id: str, name: str) -> str:
    if not name:
        raise ValueError("webhook name cannot be null")
    channel = await get_text_channel(channel_id)
    webhook = await channel.create_webhook(name)
    return f"Created {name} webhook: {webhook.url}"


@mcp.tool()
async def delete_webhook(webhook_id: str) -> str:
    client = await get_client()
    webhook = await client.fetch_webhook(int(webhook_id))
    if webhook is None:
        raise ValueError("Webhook not found by webhookId")
    name = webhook.name
    await webhook.delete()
    return f"Deleted {name} webhook"


@mcp.tool()
async def list_webhooks(channel_id: str) -> str:
    channel = await get_text_channel(channel_id)
    webhooks = await channel.webhooks()
    if not webhooks:
        raise ValueError("No webhooks found")
    formatted = [
        f"- (ID: {w.id}) **[{w.name}]** ```{w.url}```" for w in webhooks
    ]
    return f"**Retrieved {len(formatted)} messages:** \n" + "\n".join(formatted)


@mcp.tool()
async def send_webhook_message(webhook_url: str, message: str) -> str:
    if not webhook_url:
        raise ValueError("webhookUrl cannot be null")
    if not message:
        raise ValueError("message cannot be null")
    async with aiohttp.ClientSession() as session:
        webhook = discord.Webhook.from_url(webhook_url, session=session)
        sent = await webhook.send(message, wait=True)
        if sent is None:
            return "Message sent successfully."
        return f"Message sent successfully. Message link: {sent.jump_url}"


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
