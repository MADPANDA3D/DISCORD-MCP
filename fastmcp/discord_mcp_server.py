import asyncio
import logging
import os
import time
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

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
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


def parse_snowflake_list(raw: str) -> tuple[set[int], list[str]]:
    ids = set()
    warnings = []
    if not raw:
        return ids, warnings
    for part in raw.split(","):
        cleaned = part.strip()
        if not cleaned:
            continue
        parsed = parse_snowflake(cleaned)
        if parsed is None:
            warnings.append(
                f"Invalid channel id in DISCORD_ALLOWED_CHANNEL_IDS: {cleaned}"
            )
        else:
            ids.add(parsed)
    return ids, warnings


PRIMARY_CHANNEL_ID = parse_snowflake(DISCORD_PRIMARY_CHANNEL_ID_RAW)
if DISCORD_PRIMARY_CHANNEL_ID_RAW and PRIMARY_CHANNEL_ID is None:
    CONFIG_WARNINGS.append("Invalid DISCORD_PRIMARY_CHANNEL_ID configured.")
ALLOWED_CHANNEL_IDS, allowlist_warnings = parse_snowflake_list(
    DISCORD_ALLOWED_CHANNEL_IDS_RAW
)
CONFIG_WARNINGS.extend(allowlist_warnings)


def effective_allowed_channel_ids() -> set[int]:
    ids = set(ALLOWED_CHANNEL_IDS)
    if PRIMARY_CHANNEL_ID is not None:
        ids.add(PRIMARY_CHANNEL_ID)
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


async def get_bot_member(guild: discord.Guild) -> discord.Member | None:
    if bot.user is None:
        return None
    member = guild.get_member(bot.user.id)
    if member is None:
        try:
            member = await guild.fetch_member(bot.user.id)
            record_api_success("fetch_member")
        except discord.NotFound:
            return None
    return member


async def ensure_ready():
    global bot_task
    if bot.is_ready():
        return
    async with bot_lock:
        if bot_task is None or bot_task.done():
            bot_task = asyncio.create_task(bot.start(DISCORD_TOKEN))
    await bot.wait_until_ready()


def resolve_guild_id(guild_id: str | None) -> int:
    if (not guild_id or not str(guild_id).strip()) and DEFAULT_GUILD_ID:
        guild_id = DEFAULT_GUILD_ID
    parsed = parse_snowflake(guild_id)
    if parsed is None:
        if guild_id and str(guild_id).strip():
            raise ValueError("guildId must be a Discord snowflake")
        raise ValueError("guildId cannot be null")
    return parsed


async def get_guild(guild_id: str | None) -> discord.Guild:
    await ensure_ready()
    resolved_id = resolve_guild_id(guild_id)
    guild = bot.get_guild(resolved_id)
    if guild is None:
        guild = await bot.fetch_guild(resolved_id)
        record_api_success("fetch_guild")
    if guild is None:
        raise ValueError("Discord server not found by guildId")
    return guild


async def get_text_channel(channel_id: int | str) -> discord.TextChannel:
    await ensure_ready()
    resolved_id = parse_snowflake(channel_id)
    if resolved_id is None:
        raise ValueError("channelId cannot be null")
    channel = bot.get_channel(resolved_id)
    if channel is None:
        channel = await bot.fetch_channel(resolved_id)
        record_api_success("fetch_channel")
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("Channel not found by channelId")
    return channel


async def get_dm_channel(user_id: str) -> discord.DMChannel:
    await ensure_ready()
    if not user_id:
        raise ValueError("userId cannot be null")
    user = await bot.fetch_user(int(user_id))
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
    effective_allowed = effective_allowed_channel_ids()

    if PRIMARY_CHANNEL_ID is not None and ALLOWED_CHANNEL_IDS and (
        PRIMARY_CHANNEL_ID not in ALLOWED_CHANNEL_IDS
    ):
        warnings.append(
            "DISCORD_PRIMARY_CHANNEL_ID is not in DISCORD_ALLOWED_CHANNEL_IDS; "
            "primary channel will still be used for defaults."
        )
    if not ALLOWED_CHANNEL_IDS:
        warnings.append("DISCORD_ALLOWED_CHANNEL_IDS is not configured.")

    try:
        await ensure_ready()
        if bot.user:
            bot_info["user"] = {
                "id": str(bot.user.id),
                "name": bot.user.name,
                "discriminator": bot.user.discriminator,
                "global_name": bot.user.global_name,
            }
        else:
            warnings.append("Bot user is not ready yet.")
        try:
            app_info = await bot.application_info()
            record_api_success("application_info")
            bot_info["application"] = {
                "id": str(app_info.id),
                "name": app_info.name,
            }
        except Exception:
            warnings.append("Unable to fetch application info.")

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

        if not effective_allowed:
            warnings.append("No primary or allowed channels configured for checks.")
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
                if channel_id in ALLOWED_CHANNEL_IDS and (
                    not caps["read_history"] or not caps["send"]
                ):
                    warnings.append(
                        f"Allowed channel {channel_id} lacks read_history/send permission."
                    )
                if caps["read_history"] and caps["send"]:
                    if PRIMARY_CHANNEL_ID is not None and channel_id == PRIMARY_CHANNEL_ID:
                        primary_ok = True
                    if channel_id in ALLOWED_CHANNEL_IDS:
                        any_allowed_ok = True
            except Exception as exc:
                capabilities[str(channel_id)] = {"found": False, "error": str(exc)}
                warnings.append(f"Channel {channel_id} unavailable: {exc}")

        if PRIMARY_CHANNEL_ID is not None:
            ok = primary_ok
        elif ALLOWED_CHANNEL_IDS:
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
            "allowed_channel_ids": [str(cid) for cid in sorted(ALLOWED_CHANNEL_IDS)],
            "admin_tools_enabled": MCP_ADMIN_TOOLS_ENABLED,
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
            "rate_limit": get_rate_limit_snapshot(),
            "last_successful_api_at": LAST_SUCCESSFUL_API_AT,
        }
        log_action("discord_health_check", start_time, "error", error_type="invalid_payload")
        return response


@mcp.tool()
async def send_message(
    channel_id: str = "",
    message: str = "",
    embed_title: str = "",
    embed_description: str = "",
    embed_color: str = "",
    dry_run: bool | str = False,
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

        embed = None
        has_embed = bool(embed_title or embed_description or embed_color)
        embed_title_length = len(embed_title) if embed_title else 0
        embed_description_length = len(embed_description) if embed_description else 0
        content_length = len(message) if message else 0
        diagnostics.update(
            {
                "content_length": content_length,
                "content_limit": 2000,
                "embed_title_length": embed_title_length,
                "embed_title_limit": 256,
                "embed_description_length": embed_description_length,
                "embed_description_limit": 4096,
                "embeds_allowed": perms.embed_links,
            }
        )

        if content_length > 2000:
            error = error_response(
                "invalid_payload",
                "message exceeds 2000 characters.",
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
        if embed_description_length > 4096:
            error = error_response(
                "invalid_payload",
                "embed_description exceeds 4096 characters.",
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

        if dry_run:
            log_action(
                "send_message",
                start_time,
                "ok",
                channel_id=resolved_channel_id,
            )
            return success_response(dry_run=True, **diagnostics)

        if has_embed:
            embed = discord.Embed(
                title=embed_title or None,
                description=embed_description or None,
            )
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
                embed.color = discord.Color(color_value)

        sent_message = await channel.send(
            content=message or None,
            embed=embed,
        )
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
            message_id=str(sent_message.id),
            jump_url=sent_message.jump_url,
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

        channel = await get_text_channel(resolved_channel_id)
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

        if msg.author.id != bot.user.id and not perms.manage_messages:
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

        if msg.author.id != bot.user.id and not perms.manage_messages:
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
    msg = await channel.fetch_message(int(message_id))
    if msg is None:
        raise ValueError("Message not found by messageId")
    await msg.remove_reaction(emoji, bot.user)
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
    guild = await get_guild(guild_id)
    channels = [c for c in guild.channels if c.name.lower() == channel_name.lower()]
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
    guild = await get_guild(guild_id)
    channels = guild.channels
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
    await ensure_ready()
    webhook = await bot.fetch_webhook(int(webhook_id))
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
