import asyncio
import base64
import binascii
import hashlib
import hmac
import inspect
import io
import ipaddress
import json
import logging
import mimetypes
import os
import re
import socket
import time
import uuid
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
import discord
import uvicorn
from discord.ext import commands
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__
from .discord_admin_api import execute_operation as execute_admin_operation
from .discord_admin_api import get_operation as get_admin_operation
from .discord_admin_api import validate_payload as validate_admin_payload
from .discord_admin_api import validate_query as validate_admin_query
from .runtime_security import (
    AccessControlMiddleware,
    RuntimeConfigurationError,
    load_runtime_security_config,
    validate_request_header_configuration,
)
from .tool_manifest import (
    CATALOG_VERSION,
    build_tool_manifest,
    enrich_input_schema,
    filter_endpoint_coverage,
    find_manifest_tools,
    find_tool_descriptor,
    get_build_sha,
    get_image_reference,
    get_source_fingerprint,
    get_tool_definition,
    is_navigation_tool,
    manifest_categories,
    runtime_registration,
)

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
MCP_BIND_ADDRESS = os.getenv("MCP_BIND_ADDRESS", "127.0.0.1")
DISCORD_PRIMARY_CHANNEL_ID_RAW = os.getenv("DISCORD_PRIMARY_CHANNEL_ID", "").strip()
DISCORD_ALLOWED_CHANNEL_IDS_RAW = os.getenv("DISCORD_ALLOWED_CHANNEL_IDS", "").strip()
DISCORD_BLOCKED_CHANNEL_IDS_RAW = os.getenv("DISCORD_BLOCKED_CHANNEL_IDS", "").strip()
DISCORD_CREDENTIAL_MODE_RAW = os.getenv("DISCORD_CREDENTIAL_MODE", "").strip().lower()
RUNTIME_SECURITY = load_runtime_security_config(os.environ)
MCP_MODE = RUNTIME_SECURITY.mode
MCP_ACCESS_TOKEN = RUNTIME_SECURITY.standalone_access_token
MCP_PORTAL_GRANT_TOKEN = RUNTIME_SECURITY.portal_grant_token
MCP_PORTAL_GRANT_HEADER = RUNTIME_SECURITY.portal_grant_header
MCP_REQUIRE_CONFIRM_RAW = os.getenv("MCP_REQUIRE_CONFIRM", "").strip()
OPENAI_VISION_ENABLED_RAW = os.getenv("OPENAI_VISION_ENABLED", "").strip()
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
OPENAI_VISION_API_URL_RAW = (
    os.getenv("OPENAI_VISION_API_URL", "https://api.openai.com/v1/chat/completions").strip()
    or "https://api.openai.com/v1/chat/completions"
)
OPENAI_VISION_MAX_MB_RAW = os.getenv("OPENAI_VISION_MAX_MB", "10").strip()
OPENAI_VISION_TIMEOUT_SECONDS_RAW = os.getenv("OPENAI_VISION_TIMEOUT_SECONDS", "30").strip()
OPENAI_RESPONSE_MAX_BYTES = 256 * 1024
OPENAI_RESULT_MAX_CHARS = 32_000
DISCORD_ATTACHMENT_MAX_MB_RAW = os.getenv("DISCORD_ATTACHMENT_MAX_MB", "25").strip()
DISCORD_ATTACHMENT_URL_TIMEOUT_SECONDS_RAW = os.getenv(
    "DISCORD_ATTACHMENT_URL_TIMEOUT_SECONDS", "20"
).strip()
MCP_ATTACHMENT_ALLOWED_DIRS_RAW = os.getenv("MCP_ATTACHMENT_ALLOWED_DIRS", "").strip()
MCP_OPENAI_API_HEADER = os.getenv("MCP_OPENAI_API_HEADER", "x-openai-api").strip() or "x-openai-api"
MCP_DISCORD_TOKEN_HEADER = (
    os.getenv("MCP_DISCORD_TOKEN_HEADER", "x-discord-bot-token").strip() or "x-discord-bot-token"
)
MCP_DISCORD_GUILD_ID_HEADER = (
    os.getenv("MCP_DISCORD_GUILD_ID_HEADER", "x-discord-guild-id").strip() or "x-discord-guild-id"
)
MCP_DISCORD_ALLOWED_CHANNELS_HEADER = (
    os.getenv("MCP_DISCORD_ALLOWED_CHANNELS_HEADER", "x-discord-allowed-channels").strip()
    or "x-discord-allowed-channels"
)
MCP_DISCORD_BLOCKED_CHANNELS_HEADER = (
    os.getenv("MCP_DISCORD_BLOCKED_CHANNELS_HEADER", "x-discord-blocked-channels").strip()
    or "x-discord-blocked-channels"
)
MCP_DISCORD_ALLOW_ALL_READ_HEADER = (
    os.getenv("MCP_DISCORD_ALLOW_ALL_READ_HEADER", "x-discord-allow-all-read").strip()
    or "x-discord-allow-all-read"
)
MCP_DISCORD_DM_ENABLED_HEADER = (
    os.getenv("MCP_DISCORD_DM_ENABLED_HEADER", "x-discord-dm-enabled").strip()
    or "x-discord-dm-enabled"
)
MCP_ADMIN_TOOLS_ENABLED_HEADER = (
    os.getenv("MCP_ADMIN_TOOLS_ENABLED_HEADER", "x-mcp-admin-tools-enabled").strip()
    or "x-mcp-admin-tools-enabled"
)
MCP_REQUIRE_CONFIRM_HEADER = (
    os.getenv("MCP_REQUIRE_CONFIRM_HEADER", "x-mcp-require-confirm").strip()
    or "x-mcp-require-confirm"
)
MCP_BOT_POOL_TTL_SECONDS_RAW = os.getenv("MCP_BOT_POOL_TTL_SECONDS", "900").strip()
MCP_BOT_POOL_MAX_ENTRIES_RAW = os.getenv("MCP_BOT_POOL_MAX_ENTRIES", "32").strip()
MCP_TOOL_OUTPUT_MAX_BYTES_RAW = os.getenv("MCP_TOOL_OUTPUT_MAX_BYTES", "49152").strip()
MCP_FULL_CATALOG_OUTPUT_MAX_BYTES_RAW = os.getenv(
    "MCP_FULL_CATALOG_OUTPUT_MAX_BYTES", "1048576"
).strip()
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
DISCORD_ALLOWED_TARGET_ROLE_IDS_RAW = os.getenv("DISCORD_ALLOWED_TARGET_ROLE_IDS", "").strip()
DISCORD_AUDIT_TIMEZONE_NAME = (
    os.getenv(
        "DISCORD_AUDIT_TIMEZONE",
        "UTC",
    ).strip()
    or "UTC"
)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
if LOG_LEVEL not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
    LOG_LEVEL = "INFO"

logging.basicConfig(level=logging.WARNING)
logging.getLogger().setLevel(logging.WARNING)
logger = logging.getLogger("discord_mcp")
logger.setLevel(getattr(logging, LOG_LEVEL))
for third_party_logger in (
    "aiohttp",
    "asyncio",
    "discord",
    "fastmcp",
    "mcp",
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
):
    logging.getLogger(third_party_logger).setLevel(logging.WARNING)
STATE_FINGERPRINT_KEY = os.urandom(32)


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    return intents


def create_bot() -> commands.Bot:
    return commands.Bot(command_prefix="!", intents=build_intents())


@dataclass
class BotState:
    credential_fingerprint: str
    bot: commands.Bot
    task: asyncio.Task | None
    lock: asyncio.Lock
    last_used: float


@dataclass
class AttachmentRequest:
    source: str
    value: str
    filename: str
    content_type: str | None = None


@dataclass
class PreparedAttachment:
    file: discord.File | None
    metadata: dict


class ClientInputError(ValueError):
    """Deliberate validation failure whose fixed message is safe for MCP clients."""


class ProviderUnavailableError(Exception):
    """Transient provider failure whose original details must remain private."""


class ProviderTimeoutError(ProviderUnavailableError):
    """Transient provider timeout whose original details must remain private."""


class ProviderResponseError(ProviderUnavailableError):
    """Fixed provider-boundary failure safe for classification without details."""


@dataclass(frozen=True)
class ResolvedAttachmentAddress:
    host: str
    family: int


class PinnedAttachmentResolver(aiohttp.abc.AbstractResolver):
    """Resolve one validated attachment hostname to a fixed public address set."""

    def __init__(
        self,
        hostname: str,
        addresses: tuple[ResolvedAttachmentAddress, ...],
    ) -> None:
        self.hostname = hostname
        self.addresses = addresses

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_UNSPEC,
    ) -> list[dict[str, Any]]:
        try:
            requested_hostname = normalize_attachment_hostname(host)
        except ValueError as exc:
            raise OSError("Attachment resolver rejected the connection host.") from exc
        if requested_hostname != self.hostname:
            raise OSError("Attachment resolver rejected the connection host.")
        resolved = [
            {
                "hostname": host,
                "host": address.host,
                "port": port,
                "family": address.family,
                "proto": socket.IPPROTO_TCP,
                "flags": socket.AI_NUMERICHOST,
            }
            for address in self.addresses
            if family in (socket.AF_UNSPEC, address.family)
        ]
        if not resolved:
            raise OSError("Attachment resolver has no address for this network family.")
        return resolved

    async def close(self) -> None:
        return None


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
        parent_tool = super().tool

        def wrap(func):
            tool_name = func.__name__
            registration = runtime_registration(tool_name)
            registration.update(kwargs)
            decorator = parent_tool(*args, **registration)

            @wraps(func)
            async def guarded(*func_args, **func_kwargs):
                boundary_start_time = time.perf_counter()

                async def invoke_tool():
                    policy_error = enforce_runtime_tool_policy(
                        tool_name, func, func_args, func_kwargs
                    )
                    if policy_error is not None:
                        return policy_error
                    return await func(*func_args, **func_kwargs)

                def boundary_failure(exc: Exception) -> dict:
                    error = exception_to_error(exc)
                    if not isinstance(exc, ClientInputError):
                        logger.warning(
                            "tool_boundary_failed tool=%s error_type=%s",
                            tool_name,
                            error["type"],
                        )
                    response = {
                        "ok": False,
                        "error": error,
                        "meta": build_meta(boundary_start_time),
                    }
                    return finalize_tool_result(response, tool_name=tool_name)

                async def finalize_invocation(invocation) -> Any:
                    try:
                        return finalize_tool_result(
                            await invocation(),
                            tool_name=tool_name,
                        )
                    except Exception as exc:
                        return boundary_failure(exc)

                # Catalog navigation remains behind the outer Portal grant, but
                # deliberately avoids provider credentials and Discord work.
                if is_navigation_tool(tool_name):
                    return await finalize_invocation(lambda: func(*func_args, **func_kwargs))
                if not ALLOW_REQUEST_OVERRIDES:
                    return await finalize_invocation(invoke_tool)
                if (
                    REQUEST_OVERRIDE_CONTEXT.get() is not None
                    or REQUEST_OVERRIDE_WARNINGS.get() is not None
                ):
                    return await finalize_invocation(invoke_tool)
                try:
                    overrides, warnings = await build_request_overrides()
                except Exception as exc:
                    return boundary_failure(exc)
                overrides_token = None
                warnings_token = None
                try:
                    if overrides is not None:
                        overrides_token = REQUEST_OVERRIDE_CONTEXT.set(overrides)
                    if warnings:
                        warnings_token = REQUEST_OVERRIDE_WARNINGS.set(warnings)
                    return await finalize_invocation(invoke_tool)
                finally:
                    if overrides_token is not None:
                        REQUEST_OVERRIDE_CONTEXT.reset(overrides_token)
                    if warnings_token is not None:
                        REQUEST_OVERRIDE_WARNINGS.reset(warnings_token)

            registered = decorator(guarded)
            runtime_tool = getattr(self._tool_manager, "_tools", {}).get(tool_name)
            if runtime_tool is not None:
                runtime_tool.parameters = enrich_input_schema(tool_name, runtime_tool.parameters)
            return registered

        return wrap


mcp = DiscordMCP(
    name="discord-mcp",
    stateless_http=True,
    json_response=True,
    host=MCP_BIND_ADDRESS,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(RUNTIME_SECURITY.allowed_hosts),
        allowed_origins=list(RUNTIME_SECURITY.allowed_origins),
    ),
)
if not hasattr(mcp, "_mcp_server"):
    raise RuntimeError("The MCP SDK does not expose a protocol server version boundary.")
mcp._mcp_server.version = __version__
CONFIG_WARNINGS = []
LAST_SUCCESSFUL_API_AT = None
LAST_RATE_LIMIT = {}
LAST_RATE_LIMIT_AT = None


def registered_tool_count() -> int:
    manager = getattr(mcp, "_tool_manager", None)
    for attr in ("_tools", "tools"):
        tools = getattr(manager, attr, None)
        if isinstance(tools, dict):
            return len(tools)
        if isinstance(tools, (list, tuple, set)):
            return len(tools)
    list_tools = getattr(manager, "list_tools", None)
    if callable(list_tools):
        try:
            tools = list_tools()
            if isinstance(tools, (list, tuple, set)):
                return len(tools)
        except Exception:
            pass
    return 55


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


def parse_bounded_int(
    value: str,
    *,
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    parsed = parse_int(value, default)
    if parsed is None or parsed < minimum or parsed > maximum:
        raise RuntimeConfigurationError(f"{name} must be between {minimum} and {maximum}.")
    return parsed


OPENAI_HOST_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def validate_openai_vision_api_url(value: str) -> str:
    raw_url = str(value or "").strip()
    if (
        not raw_url
        or "\\" in raw_url
        or any(ord(character) < 33 or ord(character) == 127 for character in raw_url)
    ):
        raise RuntimeConfigurationError("OPENAI_VISION_API_URL is not a valid HTTPS endpoint.")
    try:
        parsed = urlparse(raw_url)
        parsed.port
    except ValueError as exc:
        raise RuntimeConfigurationError(
            "OPENAI_VISION_API_URL is not a valid HTTPS endpoint."
        ) from exc
    if parsed.scheme.lower() != "https":
        raise RuntimeConfigurationError("OPENAI_VISION_API_URL must use https.")
    if not parsed.netloc or not parsed.hostname:
        raise RuntimeConfigurationError("OPENAI_VISION_API_URL must include a valid hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise RuntimeConfigurationError("OPENAI_VISION_API_URL must not include user information.")
    if parsed.fragment or "#" in raw_url:
        raise RuntimeConfigurationError("OPENAI_VISION_API_URL must not include a fragment.")

    hostname = parsed.hostname.rstrip(".")
    try:
        ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise RuntimeConfigurationError(
                "OPENAI_VISION_API_URL must include a valid hostname."
            ) from exc
        labels = ascii_hostname.split(".")
        if (
            not ascii_hostname
            or len(ascii_hostname) > 253
            or any(not OPENAI_HOST_LABEL_PATTERN.fullmatch(label) for label in labels)
        ):
            raise RuntimeConfigurationError("OPENAI_VISION_API_URL must include a valid hostname.")
    return raw_url


def state_fingerprint(domain: str, *parts: str) -> str:
    payload = b"\x00".join([domain.encode("utf-8"), *(str(part).encode("utf-8") for part in parts)])
    return hmac.new(STATE_FINGERPRINT_KEY, payload, hashlib.sha256).hexdigest()


def credential_fingerprint(token: str) -> str:
    return state_fingerprint("discord-bot", token)


CONFIRM_APPLY_VALUE = "CONFIRM APPLY"
CONFIRM_REQUIRED = parse_bool(MCP_REQUIRE_CONFIRM_RAW) if MCP_REQUIRE_CONFIRM_RAW else True
PUBLIC_MODE = MCP_MODE == "portal"
DISCORD_CREDENTIAL_MODE = DISCORD_CREDENTIAL_MODE_RAW or ("request" if PUBLIC_MODE else "server")
if DISCORD_CREDENTIAL_MODE not in {"request", "server"}:
    raise RuntimeConfigurationError("DISCORD_CREDENTIAL_MODE must be exactly request or server.")
if PUBLIC_MODE and DISCORD_CREDENTIAL_MODE != "request":
    raise RuntimeConfigurationError("Portal mode requires DISCORD_CREDENTIAL_MODE=request.")
ALLOW_REQUEST_OVERRIDES = DISCORD_CREDENTIAL_MODE == "request"
OPENAI_VISION_ENABLED = parse_bool(OPENAI_VISION_ENABLED_RAW)
OPENAI_VISION_API_URL = (
    validate_openai_vision_api_url(OPENAI_VISION_API_URL_RAW)
    if OPENAI_VISION_ENABLED
    else OPENAI_VISION_API_URL_RAW
)
REQUIRE_REQUEST_DISCORD_TOKEN = DISCORD_CREDENTIAL_MODE == "request"
REQUIRE_REQUEST_GUILD_ID = DISCORD_CREDENTIAL_MODE == "request"
REQUIRE_REQUEST_ALLOWED_CHANNELS = DISCORD_CREDENTIAL_MODE == "request"
REQUEST_DISCORD_TOKEN_HEADER = MCP_DISCORD_TOKEN_HEADER.lower()
REQUEST_DISCORD_GUILD_ID_HEADER = MCP_DISCORD_GUILD_ID_HEADER.lower()
REQUEST_DISCORD_ALLOWED_CHANNELS_HEADER = MCP_DISCORD_ALLOWED_CHANNELS_HEADER.lower()
REQUEST_DISCORD_BLOCKED_CHANNELS_HEADER = MCP_DISCORD_BLOCKED_CHANNELS_HEADER.lower()
REQUEST_DISCORD_ALLOW_ALL_READ_HEADER = MCP_DISCORD_ALLOW_ALL_READ_HEADER.lower()
REQUEST_DISCORD_DM_ENABLED_HEADER = MCP_DISCORD_DM_ENABLED_HEADER.lower()
REQUEST_ADMIN_TOOLS_ENABLED_HEADER = MCP_ADMIN_TOOLS_ENABLED_HEADER.lower()
REQUEST_REQUIRE_CONFIRM_HEADER = MCP_REQUIRE_CONFIRM_HEADER.lower()
REQUEST_OPENAI_API_HEADER = MCP_OPENAI_API_HEADER.lower()
REQUEST_SECURITY_HEADERS = validate_request_header_configuration(
    RUNTIME_SECURITY,
    {
        "MCP_DISCORD_TOKEN_HEADER": REQUEST_DISCORD_TOKEN_HEADER,
        "MCP_DISCORD_GUILD_ID_HEADER": REQUEST_DISCORD_GUILD_ID_HEADER,
        "MCP_DISCORD_ALLOWED_CHANNELS_HEADER": REQUEST_DISCORD_ALLOWED_CHANNELS_HEADER,
        "MCP_DISCORD_BLOCKED_CHANNELS_HEADER": REQUEST_DISCORD_BLOCKED_CHANNELS_HEADER,
        "MCP_DISCORD_ALLOW_ALL_READ_HEADER": REQUEST_DISCORD_ALLOW_ALL_READ_HEADER,
        "MCP_DISCORD_DM_ENABLED_HEADER": REQUEST_DISCORD_DM_ENABLED_HEADER,
        "MCP_ADMIN_TOOLS_ENABLED_HEADER": REQUEST_ADMIN_TOOLS_ENABLED_HEADER,
        "MCP_REQUIRE_CONFIRM_HEADER": REQUEST_REQUIRE_CONFIRM_HEADER,
        "MCP_OPENAI_API_HEADER": REQUEST_OPENAI_API_HEADER,
    },
)
DISCORD_ALLOW_ALL_READ = parse_bool(DISCORD_ALLOW_ALL_READ_RAW)
DISCORD_DM_ENABLED = parse_bool(DISCORD_DM_ENABLED_RAW)
LOG_REDACT_MESSAGE_CONTENT = parse_bool(LOG_REDACT_MESSAGE_CONTENT_RAW)
BOT_POOL_TTL_SECONDS = parse_int(MCP_BOT_POOL_TTL_SECONDS_RAW, 900)
if BOT_POOL_TTL_SECONDS is None or BOT_POOL_TTL_SECONDS <= 0:
    BOT_POOL_TTL_SECONDS = 900
BOT_POOL_MAX_ENTRIES = parse_bounded_int(
    MCP_BOT_POOL_MAX_ENTRIES_RAW,
    name="MCP_BOT_POOL_MAX_ENTRIES",
    default=32,
    minimum=1,
    maximum=256,
)
MCP_TOOL_OUTPUT_MAX_BYTES = parse_bounded_int(
    MCP_TOOL_OUTPUT_MAX_BYTES_RAW,
    name="MCP_TOOL_OUTPUT_MAX_BYTES",
    default=49_152,
    minimum=8_192,
    maximum=49_152,
)
MCP_FULL_CATALOG_OUTPUT_MAX_BYTES = parse_bounded_int(
    MCP_FULL_CATALOG_OUTPUT_MAX_BYTES_RAW,
    name="MCP_FULL_CATALOG_OUTPUT_MAX_BYTES",
    default=1_048_576,
    minimum=262_144,
    maximum=1_048_576,
)
OPENAI_VISION_MAX_MB = parse_int(OPENAI_VISION_MAX_MB_RAW, 10)
if OPENAI_VISION_MAX_MB is None or OPENAI_VISION_MAX_MB <= 0:
    OPENAI_VISION_MAX_MB = 10
OPENAI_VISION_TIMEOUT_SECONDS = parse_int(OPENAI_VISION_TIMEOUT_SECONDS_RAW, 30)
if OPENAI_VISION_TIMEOUT_SECONDS is None or OPENAI_VISION_TIMEOUT_SECONDS <= 0:
    OPENAI_VISION_TIMEOUT_SECONDS = 30
DISCORD_ATTACHMENT_MAX_MB = parse_int(DISCORD_ATTACHMENT_MAX_MB_RAW, 25)
if DISCORD_ATTACHMENT_MAX_MB is None or DISCORD_ATTACHMENT_MAX_MB <= 0:
    DISCORD_ATTACHMENT_MAX_MB = 25
DISCORD_ATTACHMENT_MAX_BYTES = DISCORD_ATTACHMENT_MAX_MB * 1024 * 1024
DISCORD_ATTACHMENT_URL_TIMEOUT_SECONDS = parse_int(DISCORD_ATTACHMENT_URL_TIMEOUT_SECONDS_RAW, 20)
if DISCORD_ATTACHMENT_URL_TIMEOUT_SECONDS is None or DISCORD_ATTACHMENT_URL_TIMEOUT_SECONDS <= 0:
    DISCORD_ATTACHMENT_URL_TIMEOUT_SECONDS = 20
MCP_ATTACHMENT_ALLOWED_DIRS = tuple(
    Path(path.strip()).expanduser().resolve()
    for path in MCP_ATTACHMENT_ALLOWED_DIRS_RAW.split(",")
    if path.strip()
)

if not DISCORD_TOKEN and DISCORD_CREDENTIAL_MODE == "server":
    raise RuntimeError("DISCORD_TOKEN is not set")
try:
    AUDIT_TIMEZONE = ZoneInfo(DISCORD_AUDIT_TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    CONFIG_WARNINGS.append(
        f"Invalid DISCORD_AUDIT_TIMEZONE '{DISCORD_AUDIT_TIMEZONE_NAME}', using UTC."
    )
    AUDIT_TIMEZONE = ZoneInfo("UTC")

CHANNEL_CACHE_TTL_SECONDS = parse_int(os.getenv("DISCORD_CHANNEL_CACHE_TTL_SECONDS", "600"), 600)
if CHANNEL_CACHE_TTL_SECONDS is None or CHANNEL_CACHE_TTL_SECONDS <= 0:
    CHANNEL_CACHE_TTL_SECONDS = 600
CHANNEL_CACHE = {}
CHANNEL_CACHE_LOCK = asyncio.Lock()

JOB_TTL_SECONDS = parse_int(os.getenv("DISCORD_JOB_TTL_SECONDS", "3600"), 3600)
if JOB_TTL_SECONDS is None or JOB_TTL_SECONDS <= 0:
    JOB_TTL_SECONDS = 3600
JOB_MAX_ENTRIES = parse_bounded_int(
    os.getenv("DISCORD_JOB_MAX_ENTRIES", "128"),
    name="DISCORD_JOB_MAX_ENTRIES",
    default=128,
    minimum=1,
    maximum=512,
)
JOB_EXECUTION_TIMEOUT_SECONDS = parse_bounded_int(
    os.getenv("DISCORD_JOB_EXECUTION_TIMEOUT_SECONDS", "300"),
    name="DISCORD_JOB_EXECUTION_TIMEOUT_SECONDS",
    default=300,
    minimum=1,
    maximum=3600,
)
MAX_AUDIT_JOB_CHANNELS = 100
JOB_STORE = {}
JOB_TASKS = {}
JOB_LOCK = asyncio.Lock()
AUDIT_JOB_STORE = {}
AUDIT_JOB_LOCK = asyncio.Lock()


def parse_snowflake(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        if not cleaned.isdigit():
            return None
        parsed = int(cleaned)
    else:
        return None
    if parsed <= 0 or parsed > (2**64 - 1):
        return None
    return parsed


def require_snowflake(value, field_name: str) -> int:
    parsed = parse_snowflake(value)
    if parsed is None:
        raise ClientInputError(f"{field_name} must be a Discord snowflake.")
    return parsed


DEFAULT_GUILD_ID = parse_snowflake(DEFAULT_GUILD_ID_RAW)
if DEFAULT_GUILD_ID is None and DISCORD_CREDENTIAL_MODE == "server":
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
    task = state.task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    state.task = None
    async with CHANNEL_CACHE_LOCK:
        for cache_key in list(CHANNEL_CACHE):
            if (
                isinstance(cache_key, tuple)
                and cache_key
                and cache_key[0] == state.credential_fingerprint
            ):
                CHANNEL_CACHE.pop(cache_key, None)


async def prune_bot_pool_locked(now: float) -> list[BotState]:
    expired_states: list[BotState] = []
    if BOT_POOL_TTL_SECONDS <= 0:
        return expired_states
    for fingerprint, state in list(BOT_POOL.items()):
        if now - state.last_used > BOT_POOL_TTL_SECONDS:
            expired_states.append(state)
            BOT_POOL.pop(fingerprint, None)
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
    fingerprint = credential_fingerprint(token)
    pool_full = False
    async with BOT_POOL_LOCK:
        expired_states = await prune_bot_pool_locked(now)
        state = BOT_POOL.get(fingerprint)
        if state is None:
            if len(BOT_POOL) >= BOT_POOL_MAX_ENTRIES:
                pool_full = True
            else:
                state = BotState(
                    credential_fingerprint=fingerprint,
                    bot=create_bot(),
                    task=None,
                    lock=asyncio.Lock(),
                    last_used=now,
                )
                BOT_POOL[fingerprint] = state
        else:
            state.last_used = now
    for expired_state in expired_states:
        await close_bot_state(expired_state)
    if pool_full or state is None:
        raise RuntimeError("Discord bot pool capacity reached.")
    return state


async def reset_bot_state(state: BotState, reason: str):
    await close_bot_state(state)
    state.bot = create_bot()
    state.task = None
    logger.warning("bot_reset reason=%s", reason)


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
    active_token = get_active_request_token()
    if not active_token:
        raise ClientInputError("Discord bot token is not configured.")
    cache_key = (credential_fingerprint(active_token), guild.id)
    cached = CHANNEL_CACHE.get(cache_key)
    if cached and cached["expires_at"] > now and not force_refresh and cached["channels"]:
        return cached["channels"], cached["name_map"], cached["normalized_map"]

    async with CHANNEL_CACHE_LOCK:
        cached = CHANNEL_CACHE.get(cache_key)
        if cached and cached["expires_at"] > now and not force_refresh and cached["channels"]:
            return cached["channels"], cached["name_map"], cached["normalized_map"]
        try:
            channels = await retry_read("fetch_channels", lambda: guild.fetch_channels())
            record_api_success("fetch_channels")
        except Exception:
            logger.warning(
                "channel_cache_refresh_failed guild_id=%s error_type=provider_unavailable",
                guild.id,
            )
            channels = list(guild.channels)
        name_map = {}
        normalized_map = {}
        for channel in channels:
            name_lower = channel.name.lower()
            name_map.setdefault(name_lower, []).append(channel)
            normalized_key = normalize_channel_key(channel.name)
            if normalized_key:
                normalized_map.setdefault(normalized_key, []).append(channel)
        CHANNEL_CACHE[cache_key] = {
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
        activity_at = (
            job.get("finished_at_ts")
            or job.get("_last_used_at_ts")
            or job.get("started_at_ts")
            or job.get("created_at_ts")
        )
        if activity_at and now - activity_at > JOB_TTL_SECONDS:
            expired.append(job_id)
    for job_id in expired:
        JOB_STORE.pop(job_id, None)
        task = JOB_TASKS.pop(job_id, None)
        if task is not None and not task.done():
            task.cancel()


async def prune_audit_jobs_locked(now: float | None = None):
    if now is None:
        now = time.time()
    expired = []
    for job_id, job in AUDIT_JOB_STORE.items():
        activity_at = (
            job.get("finished_at_ts") or job.get("_last_used_at_ts") or job.get("created_at_ts")
        )
        expiry_seconds = JOB_TTL_SECONDS
        if job.get("status") == "running":
            expiry_seconds = max(JOB_TTL_SECONDS, JOB_EXECUTION_TIMEOUT_SECONDS + 5)
        if activity_at and now - activity_at > expiry_seconds:
            expired.append(job_id)
    for job_id in expired:
        AUDIT_JOB_STORE.pop(job_id, None)


async def restore_audit_job_channel(
    task_id: str,
    owner_fingerprint: str,
    channel_id: int,
) -> None:
    """Return an interrupted audit step to its tenant-owned cursor."""

    async with AUDIT_JOB_LOCK:
        job = owned_job_or_none(AUDIT_JOB_STORE, task_id, owner_fingerprint)
        if job is None or job.get("finished_at_ts") is not None:
            return
        remaining = job.setdefault("remaining_channel_ids", [])
        processed = job.setdefault("processed_channel_ids", [])
        if channel_id not in remaining and channel_id not in processed:
            remaining.insert(0, channel_id)
        job["status"] = "queued"
        job["_last_used_at_ts"] = time.time()


async def restore_audit_job_channel_safely(
    task_id: str,
    owner_fingerprint: str,
    channel_id: int,
) -> None:
    """Finish cursor rollback even while the caller is being cancelled."""

    restore_task = asyncio.create_task(
        restore_audit_job_channel(task_id, owner_fingerprint, channel_id)
    )
    try:
        await asyncio.shield(restore_task)
    except asyncio.CancelledError:
        await restore_task


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
        result = job.get("result")
        snapshot["result"] = result
        if isinstance(result, dict) and result.get("error") == snapshot["error"]:
            snapshot["error"] = None
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


def append_bounded_audit_job_result(
    job: dict,
    channel_id: int,
    result: Any,
) -> dict | None:
    candidate_results = dict(job.setdefault("results", {}))
    candidate_results[str(channel_id)] = result
    boundary_error = tool_result_boundary_error(
        candidate_results,
        max_output_bytes=retained_job_output_max_bytes(),
    )
    if boundary_error is None:
        job["results"] = candidate_results
    return boundary_error


async def run_job(job_id: str, action_name: str, action_func, params: dict):
    async with JOB_LOCK:
        job = JOB_STORE.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["started_at"] = job_timestamp()
        job["started_at_ts"] = time.time()
    try:
        result = await asyncio.wait_for(
            action_func(**params), timeout=JOB_EXECUTION_TIMEOUT_SECONDS
        )
        ok = not (isinstance(result, dict) and result.get("ok") is False)
        redacted_result = state_safe_payload(result)
        boundary_error = tool_result_boundary_error(
            redacted_result,
            max_output_bytes=retained_job_output_max_bytes(),
        )
        if boundary_error is not None:
            redacted_result = boundary_error
            status = "failed"
            error = boundary_error["error"]
        else:
            status = "succeeded" if ok else "failed"
            error = None
        if boundary_error is None and not ok and isinstance(redacted_result, dict):
            error = redacted_result.get("error")
    except TimeoutError:
        redacted_result = None
        status = "failed"
        error = build_error(
            "timeout",
            f"Legacy job exceeded {JOB_EXECUTION_TIMEOUT_SECONDS} seconds.",
        )
    except asyncio.CancelledError:
        redacted_result = None
        status = "cancelled"
        error = build_error("cancelled", "Legacy job was cancelled.")
    except Exception as exc:
        redacted_result = None
        status = "failed"
        error = state_safe_payload(exception_to_error(exc))
        retained_error_envelope = {
            "ok": False,
            "error": error,
            "meta": tool_result_error_meta(),
        }
        boundary_error = tool_result_boundary_error(
            retained_error_envelope,
            max_output_bytes=retained_job_output_max_bytes(),
        )
        if boundary_error is not None:
            redacted_result = boundary_error
            error = boundary_error["error"]
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
            warnings.append(f"Invalid channel id in DISCORD_ALLOWED_CHANNEL_IDS: {part}")
        else:
            ids.add(parsed)
    return False, ids, warnings


def parse_request_allowed_channel_ids(raw: str) -> tuple[bool, set[int]]:
    """Parse a required request channel scope without permissive fallbacks."""

    parts = [part.strip() for part in str(raw or "").split(",") if part.strip()]
    if not parts:
        raise ClientInputError("Allowed channel header must contain ALL or channel IDs.")
    all_markers = [part for part in parts if part.lower() in {"all", "*"}]
    if all_markers:
        if len(parts) != 1:
            raise ClientInputError("ALL cannot be combined with individual channel IDs.")
        return True, set()
    channel_ids: set[int] = set()
    for part in parts:
        parsed = parse_snowflake(part)
        if parsed is None:
            raise ClientInputError("Allowed channel header contains an invalid channel ID.")
        channel_ids.add(parsed)
    return False, channel_ids


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


def parse_request_blocked_channel_ids(raw: str | None) -> set[int]:
    blocked_ids: set[int] = set()
    for part in [item.strip() for item in str(raw or "").split(",") if item.strip()]:
        parsed = parse_snowflake(part)
        if parsed is None:
            raise ClientInputError("Blocked channel header contains an invalid channel ID.")
        blocked_ids.add(parsed)
    return blocked_ids


def parse_optional_bool_header(
    raw: str | None, header_name: str, warnings: list[str]
) -> bool | None:
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if not value:
        return None
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ClientInputError(f"{header_name} must be true or false.")


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
    if DISCORD_CREDENTIAL_MODE == "request":
        return None
    return DISCORD_TOKEN or None


def get_active_guild_id() -> int | None:
    overrides = get_active_request_overrides()
    if overrides and overrides.get("guild_id"):
        return overrides["guild_id"]
    if DISCORD_CREDENTIAL_MODE == "request":
        return None
    return DEFAULT_GUILD_ID


def current_tenant_fingerprint() -> str:
    token = get_active_request_token()
    guild_id = get_active_guild_id()
    if not token or guild_id is None:
        raise ClientInputError("Discord tenant credentials are not configured.")
    return state_fingerprint("discord-tenant", token, str(guild_id))


def owned_job_or_none(store: dict[str, dict], task_id: str, owner_fingerprint: str) -> dict | None:
    job = store.get(task_id)
    stored_owner = job.get("_owner_fingerprint") if isinstance(job, dict) else None
    if not isinstance(stored_owner, str) or not hmac.compare_digest(
        stored_owner, owner_fingerprint
    ):
        return None
    return job


def get_active_allowed_channel_ids() -> set[int]:
    overrides = get_active_request_overrides()
    if overrides and overrides.get("allowed_channel_ids"):
        return set(overrides["allowed_channel_ids"])
    return set()


def get_active_allow_all_channels() -> bool:
    overrides = get_active_request_overrides()
    return bool(overrides and overrides.get("allow_all_channels"))


def get_active_blocked_channel_ids() -> set[int]:
    override_ids = set()
    overrides = get_active_request_overrides()
    if overrides and overrides.get("blocked_channel_ids"):
        override_ids.update(overrides["blocked_channel_ids"])
    return set(BLOCKED_CHANNEL_IDS) | override_ids


def get_active_allow_all_read() -> bool:
    overrides = get_active_request_overrides()
    if DISCORD_CREDENTIAL_MODE != "request":
        return DISCORD_ALLOW_ALL_READ
    requested = overrides.get("allow_all_read") if overrides else None
    return DISCORD_ALLOW_ALL_READ and requested is True


def get_active_dm_enabled() -> bool:
    overrides = get_active_request_overrides()
    if DISCORD_CREDENTIAL_MODE != "request":
        return DISCORD_DM_ENABLED
    requested = overrides.get("dm_enabled") if overrides else None
    return DISCORD_DM_ENABLED and requested is True


def get_active_admin_tools_enabled() -> bool:
    overrides = get_active_request_overrides()
    if DISCORD_CREDENTIAL_MODE != "request":
        return MCP_ADMIN_TOOLS_ENABLED
    requested = overrides.get("admin_tools_enabled") if overrides else None
    return MCP_ADMIN_TOOLS_ENABLED and requested is True


def get_active_confirm_required() -> bool:
    overrides = get_active_request_overrides()
    requested = overrides.get("confirm_required") if overrides else None
    return CONFIRM_REQUIRED or requested is True


def enforce_runtime_tool_policy(
    tool_name: str,
    func: Any,
    func_args: tuple[Any, ...],
    func_kwargs: dict[str, Any],
) -> dict | None:
    """Enforce manifest policy before any provider call or side effect."""

    definition = get_tool_definition(tool_name)
    if definition is None:
        return None
    try:
        bound = inspect.signature(func).bind_partial(*func_args, **func_kwargs)
        bound.apply_defaults()
        arguments = bound.arguments
    except TypeError:
        arguments = func_kwargs

    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    access = getattr(definition, "access", {})
    if access.get("adminRequired") and not get_active_admin_tools_enabled():
        error = build_error(
            "permission_denied",
            "This tool is disabled by the server admin policy.",
            required_perms=["MCP_ADMIN_TOOLS_ENABLED=true"],
        )
        return error_with_log(tool_name, start_time, request_id, error)

    confirmation = definition.confirmation
    is_dry_run = parse_bool(arguments.get("dry_run"))
    if (
        confirmation.get("required")
        and not is_dry_run
        and get_active_confirm_required()
        and arguments.get("confirm") != CONFIRM_APPLY_VALUE
    ):
        error = build_error(
            "permission_denied",
            "confirm must be 'CONFIRM APPLY'.",
            required_perms=[f"confirm={CONFIRM_APPLY_VALUE}"],
        )
        return error_with_log(tool_name, start_time, request_id, error)
    return None


async def build_request_overrides() -> tuple[dict | None, list[str]]:
    if not ALLOW_REQUEST_OVERRIDES:
        return None, []
    headers = normalize_headers(get_http_headers())
    warnings: list[str] = []
    token_present = REQUEST_DISCORD_TOKEN_HEADER in headers
    guild_present = REQUEST_DISCORD_GUILD_ID_HEADER in headers
    allowed_channels_present = REQUEST_DISCORD_ALLOWED_CHANNELS_HEADER in headers
    blocked_present = REQUEST_DISCORD_BLOCKED_CHANNELS_HEADER in headers
    allow_all_read_present = REQUEST_DISCORD_ALLOW_ALL_READ_HEADER in headers
    dm_enabled_present = REQUEST_DISCORD_DM_ENABLED_HEADER in headers
    admin_tools_enabled_present = REQUEST_ADMIN_TOOLS_ENABLED_HEADER in headers
    confirm_required_present = REQUEST_REQUIRE_CONFIRM_HEADER in headers

    token_value = headers.get(REQUEST_DISCORD_TOKEN_HEADER, "")
    guild_value = headers.get(REQUEST_DISCORD_GUILD_ID_HEADER, "")
    allowed_channels_value = headers.get(REQUEST_DISCORD_ALLOWED_CHANNELS_HEADER, "")
    blocked_value = headers.get(REQUEST_DISCORD_BLOCKED_CHANNELS_HEADER, None)
    allow_all_read_value = headers.get(REQUEST_DISCORD_ALLOW_ALL_READ_HEADER, None)
    dm_enabled_value = headers.get(REQUEST_DISCORD_DM_ENABLED_HEADER, None)
    admin_tools_enabled_value = headers.get(REQUEST_ADMIN_TOOLS_ENABLED_HEADER, None)
    confirm_required_value = headers.get(REQUEST_REQUIRE_CONFIRM_HEADER, None)

    allow_all_read_override = parse_optional_bool_header(
        allow_all_read_value, REQUEST_DISCORD_ALLOW_ALL_READ_HEADER, warnings
    )
    dm_enabled_override = parse_optional_bool_header(
        dm_enabled_value, REQUEST_DISCORD_DM_ENABLED_HEADER, warnings
    )
    admin_tools_enabled_override = parse_optional_bool_header(
        admin_tools_enabled_value, REQUEST_ADMIN_TOOLS_ENABLED_HEADER, warnings
    )
    confirm_required_override = parse_optional_bool_header(
        confirm_required_value, REQUEST_REQUIRE_CONFIRM_HEADER, warnings
    )

    missing_required = []
    if REQUIRE_REQUEST_DISCORD_TOKEN and (not token_present or not token_value):
        missing_required.append(REQUEST_DISCORD_TOKEN_HEADER)
    if REQUIRE_REQUEST_GUILD_ID and (not guild_present or not guild_value):
        missing_required.append(REQUEST_DISCORD_GUILD_ID_HEADER)
    if REQUIRE_REQUEST_ALLOWED_CHANNELS and (
        not allowed_channels_present or not allowed_channels_value
    ):
        missing_required.append(REQUEST_DISCORD_ALLOWED_CHANNELS_HEADER)
    if missing_required:
        raise HeaderAuthError(
            "Missing required header(s): " + ", ".join(missing_required) + ".",
            missing_required,
        )

    if (
        not token_present
        and not guild_present
        and not allowed_channels_present
        and not blocked_present
        and not allow_all_read_present
        and not dm_enabled_present
        and not admin_tools_enabled_present
        and not confirm_required_present
    ):
        return None, []

    guild_id = None
    if guild_value:
        guild_id = parse_snowflake(guild_value)
        if guild_id is None:
            raise ClientInputError("Invalid guild id header.")

    allow_all_channels = False
    allowed_channel_ids: set[int] = set()
    if allowed_channels_present:
        allow_all_channels, allowed_channel_ids = parse_request_allowed_channel_ids(
            allowed_channels_value
        )

    if allow_all_read_override is True and not DISCORD_ALLOW_ALL_READ:
        warnings.append("Request cannot enable read-all beyond the server policy ceiling.")
    if dm_enabled_override is True and not DISCORD_DM_ENABLED:
        warnings.append("Request cannot enable DMs beyond the server policy ceiling.")
    if admin_tools_enabled_override is True and not MCP_ADMIN_TOOLS_ENABLED:
        warnings.append("Request cannot enable admin tools beyond the server policy ceiling.")
    if confirm_required_override is False and CONFIRM_REQUIRED:
        warnings.append("Request cannot disable the server confirmation requirement.")

    blocked_channel_ids = parse_request_blocked_channel_ids(blocked_value)
    overrides = {
        "token": token_value or None,
        "guild_id": guild_id,
        "allow_all_channels": allow_all_channels,
        "allowed_channel_ids": allowed_channel_ids,
        "blocked_channel_ids": blocked_channel_ids,
        "allow_all_read": allow_all_read_override,
        "dm_enabled": dm_enabled_override,
        "admin_tools_enabled": admin_tools_enabled_override,
        "confirm_required": confirm_required_override,
    }
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
        "width": getattr(attachment, "width", None),
        "height": getattr(attachment, "height", None),
    }


async def read_bounded_openai_response(response) -> bytes:
    content_length_raw = response.headers.get("Content-Length")
    if content_length_raw is not None:
        try:
            content_length = int(str(content_length_raw).strip())
        except ValueError as exc:
            raise ProviderResponseError("OpenAI returned an invalid response size.") from exc
        if content_length < 0 or content_length > OPENAI_RESPONSE_MAX_BYTES:
            raise ProviderResponseError("OpenAI response exceeded the size limit.")

    data = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        if len(data) + len(chunk) > OPENAI_RESPONSE_MAX_BYTES:
            raise ProviderResponseError("OpenAI response exceeded the size limit.")
        data.extend(chunk)
    return bytes(data)


def normalize_openai_usage(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    normalized = {}
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    ):
        count = value.get(key)
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            normalized[key] = count
    return normalized or None


def normalize_attachment_filename(value: str | None, fallback: str) -> str:
    candidate = (value or "").strip() or fallback
    candidate = candidate.replace("\\", "/").split("/")[-1].strip()
    if not candidate:
        candidate = fallback
    return candidate[:240]


def filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = unquote(Path(parsed.path).name or "")
    return normalize_attachment_filename(name, "attachment")


def decode_attachment_object(raw, field_name: str) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return {}
        if value.startswith("{"):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ClientInputError(
                    f"{field_name} must be valid JSON when provided as an object string."
                ) from exc
            if not isinstance(parsed, dict):
                raise ClientInputError(f"{field_name} JSON must be an object.")
            return parsed
        if value.lower().startswith(("http://", "https://")):
            return {"url": value}
        return {"path": value}
    raise ClientInputError(
        f"{field_name} must be an object, JSON object string, URL, or path string."
    )


def build_attachment_request(
    file,
    attachment,
    file_path: str,
    file_url: str,
    file_base64: str,
    file_name: str,
    file_content_type: str,
) -> AttachmentRequest | None:
    merged = {}
    for field_name, raw in (("file", file), ("attachment", attachment)):
        decoded = decode_attachment_object(raw, field_name)
        for key, value in decoded.items():
            if value not in (None, ""):
                merged[key] = value

    if file_path:
        merged["path"] = file_path
    if file_url:
        merged["url"] = file_url
    if file_base64:
        merged["base64"] = file_base64
    if file_name:
        merged["filename"] = file_name
    if file_content_type:
        merged["content_type"] = file_content_type

    source_values = {
        "path": merged.get("path") or merged.get("file_path") or merged.get("local_path"),
        "url": merged.get("url") or merged.get("file_url"),
        "base64": merged.get("base64") or merged.get("data") or merged.get("content_base64"),
    }
    active_sources = [
        (source, str(value).strip())
        for source, value in source_values.items()
        if str(value or "").strip()
    ]
    if not active_sources:
        return None
    if len(active_sources) > 1:
        raise ClientInputError(
            "Provide only one attachment source: file_path, file_url, or file_base64."
        )

    source, value = active_sources[0]
    filename = merged.get("filename") or merged.get("name") or merged.get("file_name")
    if source == "path":
        fallback = Path(value).name or "attachment"
    elif source == "url":
        fallback = filename_from_url(value)
    else:
        fallback = "attachment"
    filename = normalize_attachment_filename(str(filename) if filename else "", fallback)
    content_type = merged.get("content_type") or mimetypes.guess_type(filename)[0]
    return AttachmentRequest(
        source=source,
        value=value,
        filename=filename,
        content_type=str(content_type) if content_type else None,
    )


def check_attachment_size(size_bytes: int) -> None:
    if size_bytes <= 0:
        raise ClientInputError("Attachment cannot be empty.")
    if size_bytes > DISCORD_ATTACHMENT_MAX_BYTES:
        raise ClientInputError(
            f"Attachment exceeds DISCORD_ATTACHMENT_MAX_MB ({DISCORD_ATTACHMENT_MAX_MB} MB)."
        )


SPECIAL_ATTACHMENT_HOST_SUFFIXES = frozenset(
    {
        "alt",
        "example",
        "example.com",
        "example.net",
        "example.org",
        "home",
        "home.arpa",
        "internal",
        "invalid",
        "lan",
        "local",
        "localhost",
        "onion",
        "test",
    }
)
LEGACY_IPV4_LABEL_PATTERN = re.compile(r"(?:0[xX][0-9A-Fa-f]+|[0-9]+)\Z")


def normalize_attachment_hostname(host: str) -> str:
    candidate = str(host or "").strip().rstrip(".")
    if not candidate:
        raise ClientInputError("file_url must include a public hostname.")
    try:
        normalized = candidate.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ClientInputError("file_url hostname is invalid.") from exc
    labels = normalized.split(".")
    if (
        len(normalized) > 253
        or len(labels) < 2
        or any(not label or len(label) > 63 for label in labels)
    ):
        raise ClientInputError("file_url must include a fully qualified public hostname.")
    return normalized


def validate_attachment_url(value: str) -> tuple[str, int]:
    raw_url = str(value or "").strip()
    if (
        not raw_url
        or "\\" in raw_url
        or any(ord(character) < 32 or ord(character) == 127 for character in raw_url)
    ):
        raise ClientInputError("file_url is invalid.")
    try:
        parsed = urlparse(raw_url)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError as exc:
        raise ClientInputError("file_url is invalid.") from exc
    if parsed.scheme.lower() != "https":
        raise ClientInputError("file_url must use https.")
    if parsed.username is not None or parsed.password is not None:
        raise ClientInputError("file_url must not include user information.")
    if "#" in raw_url:
        raise ClientInputError("file_url must not include a fragment.")
    if port not in (None, 443):
        raise ClientInputError("file_url must use the standard HTTPS port.")

    normalized_hostname = normalize_attachment_hostname(hostname or "")
    literal_candidate = normalized_hostname.split("%", 1)[0]
    try:
        literal_address = ipaddress.ip_address(literal_candidate)
    except ValueError:
        literal_address = None
    try:
        socket.inet_aton(normalized_hostname)
        legacy_ipv4_literal = True
    except OSError:
        legacy_ipv4_literal = False
    legacy_numeric_hostname = all(
        LEGACY_IPV4_LABEL_PATTERN.fullmatch(label) for label in normalized_hostname.split(".")
    )
    if literal_address is not None or legacy_ipv4_literal or legacy_numeric_hostname:
        raise ClientInputError("file_url must use a public DNS hostname, not an IP literal.")
    if any(
        normalized_hostname == suffix or normalized_hostname.endswith(f".{suffix}")
        for suffix in SPECIAL_ATTACHMENT_HOST_SUFFIXES
    ):
        raise ClientInputError("file_url hostname is reserved for local or special use.")
    return normalized_hostname, port or 443


async def resolve_public_attachment_addresses(
    hostname: str,
    port: int,
) -> tuple[ResolvedAttachmentAddress, ...]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as exc:
        raise ProviderUnavailableError(
            "Attachment provider hostname could not be resolved."
        ) from exc
    if not infos:
        raise ProviderUnavailableError(
            "Attachment provider hostname did not resolve to an address."
        )

    addresses: list[ResolvedAttachmentAddress] = []
    seen: set[tuple[int, str]] = set()
    for family, _kind, _protocol, _canonical_name, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6) or not sockaddr:
            raise ClientInputError("file_url hostname resolved to an unsupported address.")
        address_text = str(sockaddr[0])
        if "%" in address_text:
            raise ClientInputError("file_url hostname resolved to a scoped address.")
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError as exc:
            raise ClientInputError("file_url hostname resolved to an invalid address.") from exc
        expected_family = socket.AF_INET if address.version == 4 else socket.AF_INET6
        if family != expected_family or not address.is_global:
            raise ClientInputError(
                "file_url hostname must resolve only to globally routable addresses."
            )
        record_key = (family, address.compressed)
        if record_key not in seen:
            seen.add(record_key)
            addresses.append(
                ResolvedAttachmentAddress(
                    host=address.compressed,
                    family=family,
                )
            )
    if not addresses:
        raise ClientInputError("file_url hostname did not resolve to a public address.")
    return tuple(addresses)


def resolve_local_attachment_path(path_value: str) -> Path:
    if PUBLIC_MODE or DISCORD_CREDENTIAL_MODE != "server":
        raise ClientInputError(
            "Local file attachments are available only in standalone server-credential mode."
        )
    if not MCP_ATTACHMENT_ALLOWED_DIRS:
        raise ClientInputError(
            "Local file attachments are disabled; use file_base64/file_url or configure MCP_ATTACHMENT_ALLOWED_DIRS."
        )
    path = Path(path_value).expanduser().resolve()
    if not any(
        path == allowed or allowed in path.parents for allowed in MCP_ATTACHMENT_ALLOWED_DIRS
    ):
        raise ClientInputError("file_path is outside MCP_ATTACHMENT_ALLOWED_DIRS.")
    if not path.is_file():
        raise ClientInputError("file_path does not exist or is not a file.")
    return path


def decode_base64_attachment(value: str) -> bytes:
    encoded = value.strip()
    if "," in encoded and encoded.lower().startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ClientInputError("file_base64 must be valid base64.") from exc
    check_attachment_size(len(data))
    return data


async def fetch_url_attachment(request: AttachmentRequest) -> bytes:
    hostname, port = validate_attachment_url(request.value)
    timeout = aiohttp.ClientTimeout(total=DISCORD_ATTACHMENT_URL_TIMEOUT_SECONDS)
    try:
        async with asyncio.timeout(DISCORD_ATTACHMENT_URL_TIMEOUT_SECONDS):
            addresses = await resolve_public_attachment_addresses(hostname, port)
            resolver = PinnedAttachmentResolver(hostname, addresses)
            connector = aiohttp.TCPConnector(
                resolver=resolver,
                family=socket.AF_UNSPEC,
                use_dns_cache=True,
                ttl_dns_cache=None,
                force_close=True,
                limit=1,
            )
            async with aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                trust_env=False,
            ) as session:
                async with session.get(
                    request.value,
                    allow_redirects=False,
                ) as response:
                    if not 200 <= response.status < 300:
                        raise ProviderUnavailableError(
                            "Attachment provider returned an unsuccessful response."
                        )
                    content_length_raw = response.headers.get("Content-Length")
                    if content_length_raw is not None:
                        try:
                            content_length = int(str(content_length_raw).strip())
                        except ValueError as exc:
                            raise ProviderUnavailableError(
                                "Attachment provider returned an invalid response size."
                            ) from exc
                        check_attachment_size(content_length)

                    data = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        if len(data) + len(chunk) > DISCORD_ATTACHMENT_MAX_BYTES:
                            raise ClientInputError(
                                f"Attachment exceeds DISCORD_ATTACHMENT_MAX_MB ({DISCORD_ATTACHMENT_MAX_MB} MB)."
                            )
                        data.extend(chunk)
    except TimeoutError as exc:
        raise ProviderTimeoutError("Attachment provider request timed out.") from exc
    except aiohttp.ClientError as exc:
        raise ProviderUnavailableError("Attachment provider request failed.") from exc
    check_attachment_size(len(data))
    return bytes(data)


async def prepare_discord_attachment(
    request: AttachmentRequest | None,
    dry_run: bool,
) -> PreparedAttachment | None:
    if request is None:
        return None
    if request.source == "base64":
        data = decode_base64_attachment(request.value)
        metadata = {
            "filename": request.filename,
            "source": "base64",
            "size_bytes": len(data),
        }
        if dry_run:
            return PreparedAttachment(file=None, metadata=metadata)
        return PreparedAttachment(
            file=discord.File(io.BytesIO(data), filename=request.filename),
            metadata=metadata,
        )
    if request.source == "url":
        data = await fetch_url_attachment(request)
        metadata = {
            "filename": request.filename,
            "source": "url",
            "size_bytes": len(data),
        }
        if dry_run:
            return PreparedAttachment(file=None, metadata=metadata)
        return PreparedAttachment(
            file=discord.File(io.BytesIO(data), filename=request.filename),
            metadata=metadata,
        )
    if request.source == "path":
        path = resolve_local_attachment_path(request.value)
        size_bytes = path.stat().st_size
        check_attachment_size(size_bytes)
        metadata = {
            "filename": request.filename,
            "source": "path",
            "size_bytes": size_bytes,
        }
        if dry_run:
            return PreparedAttachment(file=None, metadata=metadata)
        return PreparedAttachment(
            file=discord.File(path.open("rb"), filename=request.filename),
            metadata=metadata,
        )
    raise ClientInputError("Unsupported attachment source.")


def close_prepared_attachment(prepared: PreparedAttachment | None) -> None:
    if prepared is None or prepared.file is None:
        return
    try:
        prepared.file.close()
    except Exception:
        pass


def resolve_timezone(name: str | None) -> ZoneInfo:
    if not name:
        return AUDIT_TIMEZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ClientInputError(f"Invalid timezone: {name}") from exc


def parse_datetime_param(value: str | None, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ClientInputError("Invalid datetime format; use ISO-8601.") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def parse_audit_date(value: str | None, tz: ZoneInfo) -> tuple[datetime, datetime]:
    if value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ClientInputError("Invalid date format; use YYYY-MM-DD.") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        date_value = dt.date()
    else:
        date_value = datetime.now(tz).date()
    start_local = datetime(date_value.year, date_value.month, date_value.day, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local, end_local


async def fetch_messages_in_range(channel, start_utc: datetime, end_utc: datetime, limit: int):
    async def fetch_history():
        return [m async for m in channel.history(limit=limit, after=start_utc, before=end_utc)]

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
    links_top = [{"url": url, "count": count} for url, count in link_counter.most_common(5)]
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
CONFIG_WARNINGS.extend(allowlist_warnings)

BLOCKED_CHANNEL_IDS, blocked_channel_warnings = parse_id_list(
    DISCORD_BLOCKED_CHANNEL_IDS_RAW, "DISCORD_BLOCKED_CHANNEL_IDS"
)
CONFIG_WARNINGS.extend(blocked_channel_warnings)

PROTECTED_USER_IDS, protected_user_warnings = parse_id_list(
    DISCORD_PROTECTED_USER_IDS_RAW, "DISCORD_PROTECTED_USER_IDS"
)
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
    predicate = is_write_allowed if for_write else is_read_allowed
    if guild is not None:
        return [channel.id for channel in guild.text_channels if predicate(channel.id)][
            :HEALTH_CHECK_SAMPLE_LIMIT
        ]

    candidates = set(ALLOWED_CHANNEL_IDS)
    if DISCORD_CREDENTIAL_MODE == "request" and not get_active_allow_all_channels():
        request_ids = get_active_allowed_channel_ids()
        candidates = request_ids if ALLOW_ALL_CHANNELS else candidates & request_ids
    return sorted(channel_id for channel_id in candidates if predicate(channel_id))


def resolve_channel_id(channel_id) -> int:
    parsed = parse_snowflake(channel_id)
    if parsed is not None:
        return parsed
    if channel_id is not None and str(channel_id).strip():
        raise ClientInputError("channelId must be a Discord snowflake")
    request_ids = get_active_allowed_channel_ids()
    if DISCORD_CREDENTIAL_MODE == "request" and len(request_ids) == 1:
        return next(iter(request_ids))
    if PRIMARY_CHANNEL_ID is not None:
        return PRIMARY_CHANNEL_ID
    if len(ALLOWED_CHANNEL_IDS) == 1:
        return next(iter(ALLOWED_CHANNEL_IDS))
    raise ClientInputError("channelId cannot be null and no default channel is configured")


def server_write_allows(channel_id: int) -> bool:
    return ALLOW_ALL_CHANNELS or channel_id in ALLOWED_CHANNEL_IDS


def request_scope_allows(channel_id: int) -> bool:
    if DISCORD_CREDENTIAL_MODE != "request":
        return True
    return get_active_allow_all_channels() or channel_id in get_active_allowed_channel_ids()


def is_write_allowed(channel_id: int) -> bool:
    if channel_id in get_active_blocked_channel_ids():
        return False
    return server_write_allows(channel_id) and request_scope_allows(channel_id)


def is_read_allowed(channel_id: int) -> bool:
    if channel_id in get_active_blocked_channel_ids():
        return False
    server_allows = get_active_allow_all_read() or server_write_allows(channel_id)
    return server_allows and request_scope_allows(channel_id)


def is_channel_allowed(channel_id: int) -> bool:
    return is_write_allowed(channel_id)


def filter_channels_for_read(channels: list) -> list:
    return [channel for channel in channels if is_read_allowed(getattr(channel, "id", -1))]


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
    state = BOT_POOL.get(credential_fingerprint(token)) if token else None
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
STATE_SECRET_KEY_MARKERS = (
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
    "apikey",
    "webhookurl",
)
WEBHOOK_CREDENTIAL_PATTERN = re.compile(
    r"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api(?:/v\d+)?/webhooks/\d+/[^\s/?#]+",
    re.IGNORECASE,
)
DISCORD_CDN_CAPABILITY_URL_PATTERN = re.compile(
    r"https://(?:cdn\.discordapp\.com|media\.discordapp\.net)/attachments/[^\s<>\"']+\?[^\s<>\"']+",
    re.IGNORECASE,
)
HTTP_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
OUTPUT_SECRET_KEY_NAMES = frozenset(
    {
        "accesstoken",
        "authorization",
        "cookie",
        "cookies",
        "credentials",
        "discordbottoken",
        "discordtoken",
        "openaiapikey",
        "password",
        "portalgranttoken",
        "privatekey",
        "refreshtoken",
        "secret",
        "setcookie",
        "token",
        "webhooktoken",
        "webhookurl",
        "xdiscordbottoken",
        "xmadpandaportalgrant",
        "xopenaiapi",
    }
)
OUTPUT_SECRET_KEY_SUFFIXES = (
    "apikey",
    "authorization",
    "credentials",
    "password",
    "secret",
    "token",
)


def active_secret_values() -> tuple[str, ...]:
    candidates = (
        MCP_ACCESS_TOKEN,
        MCP_PORTAL_GRANT_TOKEN,
        DISCORD_TOKEN,
        get_active_request_token(),
        get_openai_api_key(),
    )
    return tuple(
        dict.fromkeys(value for value in candidates if isinstance(value, str) and len(value) >= 8)
    )


def scrub_secret_text(value: str, secrets: tuple[str, ...]) -> str:
    scrubbed = WEBHOOK_CREDENTIAL_PATTERN.sub("[REDACTED_WEBHOOK_URL]", value)
    scrubbed = DISCORD_CDN_CAPABILITY_URL_PATTERN.sub("[REDACTED_DISCORD_CDN_URL]", scrubbed)
    for secret in secrets:
        scrubbed = scrubbed.replace(secret, "[REDACTED]")
    return scrubbed


def scrub_log_text(value: str, secrets: tuple[str, ...]) -> str:
    return HTTP_URL_PATTERN.sub(
        "[REDACTED_URL]",
        scrub_secret_text(value, secrets),
    )


def scrub_secret_mapping_key(
    key: Any,
    *,
    secrets: tuple[str, ...],
    index: int,
) -> Any:
    if isinstance(key, str) and scrub_secret_text(key, secrets) != key:
        return f"[REDACTED_KEY_{index}]"
    return key


def scrub_log_argument(value: Any, secrets: tuple[str, ...]) -> Any:
    """Scrub a logging argument without breaking structured formatter contracts."""

    if isinstance(value, str):
        return scrub_log_text(value, secrets)
    if isinstance(value, bytes):
        return scrub_log_text(value.decode("utf-8", errors="replace"), secrets).encode("utf-8")
    if isinstance(value, tuple):
        return tuple(scrub_log_argument(item, secrets) for item in value)
    if isinstance(value, list):
        return [scrub_log_argument(item, secrets) for item in value]
    if isinstance(value, dict):
        return {
            scrub_secret_mapping_key(key, secrets=secrets, index=index): scrub_log_argument(
                item,
                secrets,
            )
            for index, (key, item) in enumerate(value.items(), start=1)
        }
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return scrub_log_text(str(value), secrets)


class ApplicationSecretLogFilter(logging.Filter):
    """Scrub credentials, URLs, and exception details from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            secrets = active_secret_values()
            record.msg = scrub_log_text(str(record.msg), secrets)
            record.args = scrub_log_argument(record.args, secrets)
        except Exception:
            record.msg = "discord_mcp_log_message_unavailable"
            record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


APPLICATION_SECRET_LOG_FILTER = ApplicationSecretLogFilter()
logger.addFilter(APPLICATION_SECRET_LOG_FILTER)
for configured_logger_name in (
    "aiohttp",
    "asyncio",
    "discord",
    "fastmcp",
    "mcp",
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
):
    configured_logger = logging.getLogger(configured_logger_name)
    configured_logger.addFilter(APPLICATION_SECRET_LOG_FILTER)
    for configured_handler in configured_logger.handlers:
        configured_handler.addFilter(APPLICATION_SECRET_LOG_FILTER)
for root_handler in logging.getLogger().handlers:
    root_handler.addFilter(APPLICATION_SECRET_LOG_FILTER)


def install_secret_scrubbing_record_factory() -> None:
    """Sanitize future third-party records before any handler can emit them."""

    previous_factory = logging.getLogRecordFactory()
    if getattr(previous_factory, "_discord_secret_scrubber", False):
        return

    def secret_scrubbing_record_factory(*args, **kwargs):
        record = previous_factory(*args, **kwargs)
        APPLICATION_SECRET_LOG_FILTER.filter(record)
        return record

    secret_scrubbing_record_factory._discord_secret_scrubber = True
    logging.setLogRecordFactory(secret_scrubbing_record_factory)


install_secret_scrubbing_record_factory()


def scrub_state_secrets(payload: Any, *, secrets: tuple[str, ...] | None = None) -> Any:
    """Remove credential material before retaining provider data in memory."""

    if secrets is None:
        secrets = active_secret_values()
    if isinstance(payload, dict):
        scrubbed = {}
        for index, (key, value) in enumerate(payload.items(), start=1):
            safe_key = scrub_secret_mapping_key(
                key,
                secrets=secrets,
                index=index,
            )
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if any(marker in normalized_key for marker in STATE_SECRET_KEY_MARKERS):
                scrubbed[safe_key] = "[REDACTED]"
            else:
                scrubbed[safe_key] = scrub_state_secrets(value, secrets=secrets)
        return scrubbed
    if isinstance(payload, list):
        return [scrub_state_secrets(item, secrets=secrets) for item in payload]
    if isinstance(payload, tuple):
        return tuple(scrub_state_secrets(item, secrets=secrets) for item in payload)
    if isinstance(payload, str):
        return scrub_secret_text(payload, secrets)
    return payload


def scrub_output_secrets(
    payload: Any,
    *,
    secrets: tuple[str, ...] | None = None,
    preserve_schema_keys: bool = False,
    schema_properties: bool = False,
) -> Any:
    """Remove credential material without redacting requested message content."""

    if secrets is None:
        secrets = active_secret_values()
    if isinstance(payload, dict):
        scrubbed = {}
        is_json_schema = payload.get("type") == "object" and isinstance(
            payload.get("properties"), dict
        )
        for index, (key, value) in enumerate(payload.items(), start=1):
            safe_key = scrub_secret_mapping_key(
                key,
                secrets=secrets,
                index=index,
            )
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if schema_properties:
                scrubbed[safe_key] = scrub_output_secrets(
                    value,
                    secrets=secrets,
                    preserve_schema_keys=preserve_schema_keys,
                )
            elif preserve_schema_keys and is_json_schema and key == "properties":
                scrubbed[safe_key] = scrub_output_secrets(
                    value,
                    secrets=secrets,
                    preserve_schema_keys=True,
                    schema_properties=True,
                )
            elif normalized_key in OUTPUT_SECRET_KEY_NAMES or normalized_key.endswith(
                OUTPUT_SECRET_KEY_SUFFIXES
            ):
                scrubbed[safe_key] = "[REDACTED]"
            else:
                scrubbed[safe_key] = scrub_output_secrets(
                    value,
                    secrets=secrets,
                    preserve_schema_keys=preserve_schema_keys,
                )
        return scrubbed
    if isinstance(payload, list):
        return [
            scrub_output_secrets(
                item,
                secrets=secrets,
                preserve_schema_keys=preserve_schema_keys,
            )
            for item in payload
        ]
    if isinstance(payload, tuple):
        return tuple(
            scrub_output_secrets(
                item,
                secrets=secrets,
                preserve_schema_keys=preserve_schema_keys,
            )
            for item in payload
        )
    if isinstance(payload, str):
        return scrub_secret_text(payload, secrets)
    return payload


def serialized_tool_result_size(payload: Any) -> int:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return len(serialized.encode("utf-8"))


def tool_output_limit_for(payload: Any, tool_name: str = "") -> int:
    if (
        tool_name == "list_capabilities"
        and isinstance(payload, dict)
        and payload.get("ok") is True
        and isinstance(payload.get("data"), dict)
        and payload["data"].get("descriptorsIncluded") is True
    ):
        return MCP_FULL_CATALOG_OUTPUT_MAX_BYTES
    return MCP_TOOL_OUTPUT_MAX_BYTES


def retained_job_output_max_bytes() -> int:
    """Reserve envelope space so retained data remains retrievable through job tools."""

    return max(4_096, MCP_TOOL_OUTPUT_MAX_BYTES - 4_096)


def tool_result_error_meta(max_output_bytes: int | None = None) -> dict:
    return {
        "duration_ms": 0,
        "rate_limit": {"known": False},
        "warnings": [],
        "max_output_bytes": max_output_bytes or MCP_TOOL_OUTPUT_MAX_BYTES,
    }


def nonserializable_tool_result_error(
    max_output_bytes: int | None = None,
) -> dict:
    return {
        "ok": False,
        "error": {
            "type": "invalid_payload",
            "message": "Tool returned a non-serializable result.",
        },
        "meta": tool_result_error_meta(max_output_bytes),
    }


def oversized_tool_result_error(max_output_bytes: int | None = None) -> dict:
    return {
        "ok": False,
        "error": {
            "type": "resource_exhausted",
            "message": (
                "Tool result exceeds its configured output ceiling; narrow count, "
                "limit, filters, or included details."
            ),
        },
        "meta": tool_result_error_meta(max_output_bytes),
    }


def tool_result_boundary_error(
    payload: Any,
    *,
    max_output_bytes: int | None = None,
) -> dict | None:
    effective_max_bytes = max_output_bytes or MCP_TOOL_OUTPUT_MAX_BYTES
    try:
        result_size = serialized_tool_result_size(payload)
    except (OverflowError, RecursionError, TypeError, ValueError):
        return nonserializable_tool_result_error(effective_max_bytes)
    if result_size <= effective_max_bytes:
        return None
    return oversized_tool_result_error(effective_max_bytes)


def finalize_tool_result(payload: Any, *, tool_name: str = "") -> Any:
    try:
        safe_payload = scrub_output_secrets(
            payload,
            preserve_schema_keys=tool_name == "list_capabilities",
        )
    except RecursionError:
        return nonserializable_tool_result_error()
    max_output_bytes = tool_output_limit_for(safe_payload, tool_name)
    return (
        tool_result_boundary_error(
            safe_payload,
            max_output_bytes=max_output_bytes,
        )
        or safe_payload
    )


def state_safe_payload(payload: Any) -> Any:
    scrubbed = scrub_state_secrets(payload)
    return _redact_payload(scrubbed) if LOG_REDACT_MESSAGE_CONTENT else scrubbed


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
                    retry_after = 1.0 * (2**attempt)
                await asyncio.sleep(retry_after)
                attempt += 1
                continue
            raise


def exception_to_error(
    exc: Exception,
    required_perms: list[str] | None = None,
    diagnostics: dict | None = None,
) -> dict:
    if isinstance(exc, HeaderAuthError):
        auth_diagnostics = getattr(exc, "data", {}).get("diagnostics", {})
        required_headers = auth_diagnostics.get("required_headers", [])
        safe_required_headers = [
            str(header)[:128] for header in required_headers if isinstance(header, str) and header
        ]
        return build_error(
            "permission_denied",
            "Required Discord request headers are missing.",
            diagnostics={"required_headers": safe_required_headers},
        )
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
    if isinstance(exc, ClientInputError):
        return build_error("invalid_payload", str(exc), diagnostics=diagnostics)
    if isinstance(exc, ProviderTimeoutError):
        return build_error(
            "timeout",
            "Provider request timed out.",
            diagnostics=diagnostics,
        )
    if isinstance(exc, ProviderUnavailableError):
        return build_error(
            "provider_unavailable",
            "Provider request failed.",
            diagnostics=diagnostics,
        )
    return build_error("internal_error", "Unexpected error.", diagnostics=diagnostics)


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
    if not get_active_confirm_required():
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
    if get_active_dm_enabled():
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
    request_allows_unscoped = (
        DISCORD_CREDENTIAL_MODE != "request" or get_active_allow_all_channels()
    )
    if ALLOW_ALL_CHANNELS and request_allows_unscoped:
        return None
    error = build_error(
        "permission_denied",
        "Unscoped writes require ALL in both server and request channel policies.",
        required_perms=["DISCORD_ALLOWED_CHANNEL_IDS=ALL"],
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
            member = await retry_read("fetch_member", lambda: guild.fetch_member(user_id))
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
            member = await retry_read("fetch_member", lambda: guild.fetch_member(user_id))
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
    protected_roles = [role.id for role in member.roles if role.id in PROTECTED_ROLE_IDS]
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
        has_allowed = any(role.id in ALLOWED_TARGET_ROLE_IDS for role in member.roles)
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
            task_failed = state.task.cancelled()
            if not task_failed:
                try:
                    task_failed = state.task.exception() is not None
                except (asyncio.CancelledError, Exception):
                    task_failed = True
            if task_failed:
                logger.warning("bot_task_failed error_type=bot_start_failed")
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
        raise ClientInputError("Discord bot token is not configured.")
    return await get_client_for_token(token)


async def ensure_client_ready(retry: int = 1) -> commands.Bot:
    try:
        return await get_client()
    except Exception:
        if retry <= 0:
            raise
        logger.warning("ensure_client_retry error_type=client_initialization_failed")
        await reset_bot("ensure_client_retry")
        return await get_client()


def get_client_debug_snapshot() -> dict:
    token = get_active_request_token()
    state = BOT_POOL.get(credential_fingerprint(token)) if token else None
    bot_instance = state.bot if state else None
    task = state.task if state else None
    ready_event = getattr(bot_instance, "_ready", None) if bot_instance is not None else None
    snapshot = {
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
    else:
        snapshot["task_state"] = "done" if task.done() else "running"
        snapshot["task_cancelled"] = task.cancelled()
        if task.done():
            if task.cancelled():
                snapshot["task_error_type"] = "task_cancelled"
            else:
                try:
                    task_failed = task.exception() is not None
                except (asyncio.CancelledError, Exception):
                    task_failed = True
                snapshot["task_error_type"] = "task_failed" if task_failed else None
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
                raise ClientInputError("guildId must be a Discord snowflake")
            if parsed != override_id:
                raise ClientInputError("guildId must match the request header guild id")
        return override_id
    if guild_id and str(guild_id).strip():
        parsed = parse_snowflake(guild_id)
        if parsed is None:
            raise ClientInputError("guildId must be a Discord snowflake")
        if DEFAULT_GUILD_ID is not None and parsed != DEFAULT_GUILD_ID:
            raise ClientInputError("guildId must match configured DISCORD_GUILD_ID")
        return parsed
    if DEFAULT_GUILD_ID is not None:
        return DEFAULT_GUILD_ID
    raise ClientInputError("guildId is required")


async def get_guild_for_client(client: commands.Bot, guild_id: int) -> discord.Guild:
    guild = client.get_guild(guild_id)
    if guild is None:
        guild = await retry_read("fetch_guild", lambda: client.fetch_guild(guild_id))
        record_api_success("fetch_guild")
    if guild is None:
        raise ClientInputError("Discord server not found by guildId")
    return guild


async def get_guild(guild_id: str | None, client: commands.Bot | None = None) -> discord.Guild:
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
        raise ClientInputError("channelId cannot be null")
    channel = client.get_channel(resolved_id)
    if channel is None:
        channel = await retry_read("fetch_channel", lambda: client.fetch_channel(resolved_id))
        record_api_success("fetch_channel")
    if not isinstance(channel, discord.TextChannel):
        raise ClientInputError("Channel not found by channelId")
    active_guild_id = get_active_guild_id()
    if active_guild_id is not None and channel.guild.id != active_guild_id:
        raise ClientInputError("channelId does not belong to configured DISCORD_GUILD_ID")
    return channel


async def get_message_target(channel_id: int | str):
    client = await get_client()
    resolved_id = parse_snowflake(channel_id)
    if resolved_id is None:
        raise ClientInputError("channelId cannot be null")
    channel = client.get_channel(resolved_id)
    if channel is None:
        channel = await retry_read("fetch_channel", lambda: client.fetch_channel(resolved_id))
        record_api_success("fetch_channel")
    if channel is None or not hasattr(channel, "send"):
        raise ClientInputError("Channel is not messageable")
    active_guild_id = get_active_guild_id()
    if getattr(channel, "guild", None) is None or (
        active_guild_id is not None and channel.guild.id != active_guild_id
    ):
        raise ClientInputError("channelId does not belong to configured DISCORD_GUILD_ID")
    return channel


def is_message_target_allowed(channel) -> bool:
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
        raise ClientInputError("userId cannot be null")
    parsed_user_id = require_snowflake(user_id, "userId")
    user = await retry_read("fetch_user", lambda: client.fetch_user(parsed_user_id))
    if user is None:
        raise ClientInputError("User not found by userId")
    return await user.create_dm()


_ADMIN_PERMISSION_ALIASES = {
    "manage_guild_expressions": "manage_emojis_and_stickers",
    "pin_messages": "manage_messages",
    "set_voice_channel_status": "manage_channels",
}


def _admin_identifiers(values: dict[str, Any]) -> dict[str, str]:
    names = (
        "guild_id",
        "channel_id",
        "user_id",
        "role_id",
        "target_id",
        "message_id",
        "emoji",
        "emoji_id",
        "webhook_id",
        "event_id",
        "rule_id",
        "sound_id",
        "sticker_id",
        "template_code",
        "invite_code",
        "integration_id",
    )
    return {name: str(values.get(name, "") or "").strip() for name in names}


def _admin_required_permissions(operation: Any, payload: Any) -> tuple[str, ...]:
    if operation.action != "modify_member":
        return (operation.permission,) if operation.permission else ()
    fields = set(payload) if isinstance(payload, dict) else set()
    required: set[str] = set()
    if fields & {"roles"}:
        required.add("manage_roles")
    if fields & {"nick"}:
        required.add("manage_nicknames")
    if fields & {"communication_disabled_until"}:
        required.add("moderate_members")
    if fields & {"mute", "deaf"}:
        required.add("mute_members")
    if fields & {"channel_id"}:
        required.add("move_members")
    if fields & {"flags"}:
        required.add("manage_guild")
    return tuple(sorted(required))


async def _effective_channel_permissions(
    guild: discord.Guild,
    channel_id: int,
    target_id: str,
    target_type: str,
) -> dict[str, Any]:
    channel = guild.get_channel_or_thread(channel_id)
    if channel is None:
        channel = await retry_read("fetch_channel", lambda: guild.fetch_channel(channel_id))
    normalized_type = str(target_type or "bot").strip().lower()
    target: Any
    if normalized_type == "bot":
        target = await get_bot_member(guild)
        if target is None:
            raise ValueError("Bot member not available.")
    elif normalized_type == "member":
        parsed_target_id = parse_snowflake(target_id)
        if parsed_target_id is None:
            raise ValueError("target_id must be a Discord member snowflake.")
        target = await fetch_member_optional(guild, parsed_target_id)
        if target is None:
            raise ValueError("Target member not found in the guild.")
    elif normalized_type == "role":
        parsed_target_id = parse_snowflake(target_id)
        if parsed_target_id is None:
            raise ValueError("target_id must be a Discord role snowflake.")
        target = guild.get_role(parsed_target_id)
        if target is None:
            raise ValueError("Target role not found in the guild.")
    else:
        raise ValueError("query.target_type must be bot, member, or role.")

    effective = channel.permissions_for(target)
    overwrites = []
    for overwrite_target, overwrite in channel.overwrites.items():
        allow, deny = overwrite.pair()
        overwrites.append(
            {
                "target_id": str(overwrite_target.id),
                "target_type": "role" if isinstance(overwrite_target, discord.Role) else "member",
                "allow": str(allow.value),
                "deny": str(deny.value),
            }
        )
    return {
        "guild_id": str(guild.id),
        "channel_id": str(channel.id),
        "target_id": str(target.id),
        "target_type": normalized_type,
        "effective_permissions": [name for name, allowed in effective if allowed],
        "effective_permissions_value": str(effective.value),
        "permission_overwrites": overwrites,
    }


async def _run_server_management_action(
    expected_risk: str,
    action: str,
    **arguments: Any,
) -> dict:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    warnings: list[str] = []
    audit_trail_id = str(uuid.uuid4())
    identifiers = _admin_identifiers(arguments)
    payload = arguments.get("payload")
    query = arguments.get("query")
    reason = str(arguments.get("reason", "") or "")
    confirm = str(arguments.get("confirm", "") or "")
    parsed_channel_id = parse_snowflake(identifiers.get("channel_id"))
    try:
        operation = get_admin_operation(action, expected_risk)
        query = validate_admin_query(operation, query)
        payload = validate_admin_payload(operation, payload)
        if "guild_id" in operation.required_identifiers:
            identifiers["guild_id"] = str(resolve_guild_id(identifiers["guild_id"]))

        if expected_risk != "read":
            if not get_active_admin_tools_enabled():
                return error_with_log(
                    action,
                    start_time,
                    request_id,
                    build_error(
                        "permission_denied",
                        "Discord server-management writes are disabled.",
                        required_perms=["MCP_ADMIN_TOOLS_ENABLED=true"],
                    ),
                    warnings=warnings,
                    extra={"audit_trail_id": audit_trail_id},
                )
            if expected_risk == "destructive" and confirm != "CONFIRM APPLY":
                return error_with_log(
                    action,
                    start_time,
                    request_id,
                    build_error(
                        "confirmation_required",
                        "Destructive Discord server management requires confirm=CONFIRM APPLY.",
                    ),
                    warnings=warnings,
                    extra={"audit_trail_id": audit_trail_id},
                )
            if expected_risk == "write":
                confirm_error = require_confirm(
                    confirm,
                    action,
                    start_time,
                    request_id,
                    warnings=warnings,
                )
                if confirm_error:
                    return confirm_error

        if parsed_channel_id is not None:
            allowed = (
                is_read_allowed(parsed_channel_id)
                if expected_risk == "read"
                else is_write_allowed(parsed_channel_id)
            )
            if not allowed:
                return error_with_log(
                    action,
                    start_time,
                    request_id,
                    build_error(
                        "permission_denied",
                        "Channel policy blocks this Discord server-management action.",
                    ),
                    warnings=warnings,
                    channel_id=parsed_channel_id,
                    extra={"audit_trail_id": audit_trail_id},
                )

        required_permissions = _admin_required_permissions(operation, payload)
        guild = None
        if (
            required_permissions
            or operation.member_guard
            or operation.role_guard
            or operation.action in {"bulk_ban", "get_effective_channel_permissions"}
        ):
            guild = await get_guild(identifiers.get("guild_id", ""))
        if required_permissions and guild is not None:
            bot_member = await get_bot_member(guild)
            if bot_member is None:
                return error_with_log(
                    action,
                    start_time,
                    request_id,
                    build_error("invalid_payload", "Bot member not available."),
                    warnings=warnings,
                    guild_id=guild.id,
                    extra={"audit_trail_id": audit_trail_id},
                )
            permissions = bot_member.guild_permissions
            channel = guild.get_channel(parsed_channel_id) if parsed_channel_id else None
            if channel is not None:
                permissions = channel.permissions_for(bot_member)
            for permission_name in required_permissions:
                required_perm = _ADMIN_PERMISSION_ALIASES.get(
                    permission_name,
                    permission_name,
                )
                if not getattr(permissions, required_perm, False):
                    return error_with_log(
                        action,
                        start_time,
                        request_id,
                        build_error(
                            "permission_denied",
                            f"Missing permission: {permission_name}.",
                            required_perms=[permission_name],
                        ),
                        warnings=warnings,
                        guild_id=guild.id,
                        extra={"audit_trail_id": audit_trail_id},
                    )

        if operation.member_guard and guild is not None:
            parsed_user_id = parse_snowflake(identifiers.get("user_id"))
            if parsed_user_id is None:
                raise ValueError("user_id must be a Discord snowflake for this action.")
            member, member_error = await get_member_or_error(
                guild,
                parsed_user_id,
                action,
                start_time,
                request_id,
                warnings,
                audit_trail_id,
            )
            if member_error:
                return member_error
            guard_error = ensure_member_guardrails(
                member,
                action,
                start_time,
                request_id,
                warnings,
                audit_trail_id,
                role_id=parse_snowflake(identifiers.get("role_id")),
            )
            if guard_error:
                return guard_error
            _, hierarchy_error = await ensure_bot_can_moderate(
                guild,
                member,
                action,
                start_time,
                request_id,
                warnings,
                audit_trail_id,
                required_perm=(
                    _ADMIN_PERMISSION_ALIASES.get(operation.permission, operation.permission)
                    if operation.permission
                    else None
                ),
            )
            if hierarchy_error:
                return hierarchy_error

        if operation.action == "bulk_ban" and guild is not None:
            raw_user_ids = (payload or {}).get("user_ids") if isinstance(payload, dict) else None
            if not isinstance(raw_user_ids, list) or not raw_user_ids:
                raise ValueError("bulk_ban payload.user_ids must contain Discord snowflakes.")
            for raw_user_id in raw_user_ids:
                parsed_user_id = parse_snowflake(str(raw_user_id))
                if parsed_user_id is None:
                    raise ValueError("bulk_ban payload.user_ids must contain Discord snowflakes.")
                if parsed_user_id in PROTECTED_USER_IDS or parsed_user_id == guild.owner_id:
                    return error_with_log(
                        action,
                        start_time,
                        request_id,
                        build_error(
                            "permission_denied",
                            "Bulk ban contains a protected user or the guild owner.",
                            required_perms=["DISCORD_PROTECTED_USER_IDS"],
                        ),
                        warnings=warnings,
                        guild_id=guild.id,
                        extra={"audit_trail_id": audit_trail_id},
                    )
                member = await fetch_member_optional(guild, parsed_user_id)
                if member is None:
                    continue
                guard_error = ensure_member_guardrails(
                    member,
                    action,
                    start_time,
                    request_id,
                    warnings,
                    audit_trail_id,
                )
                if guard_error:
                    return guard_error
                _, hierarchy_error = await ensure_bot_can_moderate(
                    guild,
                    member,
                    action,
                    start_time,
                    request_id,
                    warnings,
                    audit_trail_id,
                    required_perm="ban_members",
                )
                if hierarchy_error:
                    return hierarchy_error

        if operation.role_guard and guild is not None:
            guarded_role_id = parse_snowflake(identifiers.get("role_id"))
            if guarded_role_id is None and str((payload or {}).get("type", "")) == "0":
                guarded_role_id = parse_snowflake(identifiers.get("target_id"))
            if guarded_role_id is not None:
                role = guild.get_role(guarded_role_id)
                bot_member = await get_bot_member(guild)
                if guarded_role_id in PROTECTED_ROLE_IDS:
                    return error_with_log(
                        action,
                        start_time,
                        request_id,
                        build_error("permission_denied", "Target role is protected."),
                        warnings=warnings,
                        guild_id=guild.id,
                        extra={"audit_trail_id": audit_trail_id},
                    )
                if role is not None and bot_member is not None and role >= bot_member.top_role:
                    return error_with_log(
                        action,
                        start_time,
                        request_id,
                        build_error(
                            "permission_denied",
                            "Bot role hierarchy prevents managing this role.",
                            required_perms=["role_hierarchy"],
                        ),
                        warnings=warnings,
                        guild_id=guild.id,
                        extra={"audit_trail_id": audit_trail_id},
                    )

        if operation.action == "get_effective_channel_permissions":
            if guild is None or parsed_channel_id is None:
                raise ValueError("channel_id is required for effective permission lookup.")
            effective_result = await _effective_channel_permissions(
                guild,
                parsed_channel_id,
                identifiers.get("target_id", ""),
                str(query.get("target_type", "bot")),
            )
            result = {
                "ok": True,
                "status": 200,
                "resource": operation.resource,
                "data": effective_result,
                "rate_limit": None,
            }
        else:
            result = await execute_admin_operation(
                operation,
                token=get_active_request_token() or DISCORD_TOKEN,
                identifiers=identifiers,
                query=query,
                payload=payload,
                reason=reason,
            )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=(
                guild.id if guild is not None else parse_snowflake(identifiers.get("guild_id"))
            ),
            channel_id=parsed_channel_id,
            extra={"audit_trail_id": audit_trail_id},
        )
        meta["rate_limit"] = result.get("rate_limit", meta.get("rate_limit"))
        if not result.get("ok"):
            upstream_error = result.get("error") or {}
            return error_response(
                upstream_error.get("type") or "discord_api_error",
                upstream_error.get("message") or "Discord rejected the server-management request.",
                meta,
                discord_code=upstream_error.get("discord_error_code"),
            )
        record_api_success(action, result.get("rate_limit"))
        log_action(
            action,
            start_time,
            "ok",
            guild_id=(guild.id if guild is not None else None),
            channel_id=parsed_channel_id,
            extra={"audit_trail_id": audit_trail_id},
        )
        return success_response(
            {
                "action": action,
                "resource": result.get("resource"),
                "status": result.get("status"),
                "result": result.get("data"),
            },
            meta,
        )
    except (TypeError, ValueError) as exc:
        return error_with_log(
            action or "discord_server_management",
            start_time,
            request_id,
            build_error("invalid_payload", str(exc)[:512]),
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )
    except Exception as exc:
        return error_with_log(
            action or "discord_server_management",
            start_time,
            request_id,
            exception_to_error(exc),
            warnings=warnings,
            extra={"audit_trail_id": audit_trail_id},
        )


@mcp.tool()
async def discord_server_read(
    action: str,
    guild_id: str = "",
    channel_id: str = "",
    user_id: str = "",
    role_id: str = "",
    target_id: str = "",
    message_id: str = "",
    emoji: str = "",
    emoji_id: str = "",
    webhook_id: str = "",
    event_id: str = "",
    rule_id: str = "",
    sound_id: str = "",
    sticker_id: str = "",
    template_code: str = "",
    invite_code: str = "",
    integration_id: str = "",
    query: dict | None = None,
) -> dict:
    """Execute one reviewed read-only Discord server-management action."""
    values = dict(locals())
    values.pop("action")
    return await _run_server_management_action("read", action, **values)


@mcp.tool()
async def discord_server_write(
    action: str,
    guild_id: str = "",
    channel_id: str = "",
    user_id: str = "",
    role_id: str = "",
    target_id: str = "",
    message_id: str = "",
    emoji: str = "",
    emoji_id: str = "",
    webhook_id: str = "",
    event_id: str = "",
    rule_id: str = "",
    sound_id: str = "",
    sticker_id: str = "",
    template_code: str = "",
    invite_code: str = "",
    integration_id: str = "",
    payload: dict | None = None,
    reason: str = "",
    confirm: str = "",
) -> dict:
    """Execute one reviewed additive or reversible Discord administration action."""
    values = dict(locals())
    values.pop("action")
    return await _run_server_management_action("write", action, **values)


@mcp.tool()
async def discord_server_destructive(
    action: str,
    guild_id: str = "",
    channel_id: str = "",
    user_id: str = "",
    role_id: str = "",
    target_id: str = "",
    message_id: str = "",
    emoji: str = "",
    emoji_id: str = "",
    webhook_id: str = "",
    event_id: str = "",
    rule_id: str = "",
    sound_id: str = "",
    sticker_id: str = "",
    template_code: str = "",
    invite_code: str = "",
    integration_id: str = "",
    payload: dict | None = None,
    reason: str = "",
    confirm: str = "",
) -> dict:
    """Execute one reviewed overwrite, moderation, removal, or delete action."""
    values = dict(locals())
    values.pop("action")
    return await _run_server_management_action("destructive", action, **values)


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
        "primary_channel_id": (str(PRIMARY_CHANNEL_ID) if PRIMARY_CHANNEL_ID is not None else None),
        "write_allowed_channel_ids": (
            ["ALL"] if ALLOW_ALL_CHANNELS else [str(cid) for cid in sorted(ALLOWED_CHANNEL_IDS)]
        ),
        "blocked_channel_ids": [str(cid) for cid in sorted(get_active_blocked_channel_ids())],
        "allow_all_read": get_active_allow_all_read(),
        "admin_tools_enabled": get_active_admin_tools_enabled(),
        "dm_enabled": get_active_dm_enabled(),
        "log_redact_message_content": LOG_REDACT_MESSAGE_CONTENT,
        "audit_timezone": DISCORD_AUDIT_TIMEZONE_NAME,
        "protected_user_ids_count": len(PROTECTED_USER_IDS),
        "protected_role_ids_count": len(PROTECTED_ROLE_IDS),
        "allowed_target_role_ids_count": len(ALLOWED_TARGET_ROLE_IDS),
        "public_mode": PUBLIC_MODE,
        "credential_mode": DISCORD_CREDENTIAL_MODE,
        "portal_grant_configured": bool(MCP_PORTAL_GRANT_TOKEN),
        "portal_grant_header": MCP_PORTAL_GRANT_HEADER,
        "allow_request_overrides": ALLOW_REQUEST_OVERRIDES,
        "confirm_required": get_active_confirm_required(),
        "openai_vision_enabled": OPENAI_VISION_ENABLED,
        "openai_vision_model": OPENAI_VISION_MODEL,
        "openai_vision_api_configured": bool(OPENAI_VISION_API_URL),
        "openai_vision_max_mb": OPENAI_VISION_MAX_MB,
        "openai_vision_timeout_seconds": OPENAI_VISION_TIMEOUT_SECONDS,
        "openai_api_header": MCP_OPENAI_API_HEADER,
        "require_request_discord_token": REQUIRE_REQUEST_DISCORD_TOKEN,
        "require_request_guild_id": REQUIRE_REQUEST_GUILD_ID,
        "require_request_allowed_channels": REQUIRE_REQUEST_ALLOWED_CHANNELS,
        "request_header_names": {
            "discord_token": REQUEST_DISCORD_TOKEN_HEADER,
            "guild_id": REQUEST_DISCORD_GUILD_ID_HEADER,
            "allowed_channels": REQUEST_DISCORD_ALLOWED_CHANNELS_HEADER,
            "blocked_channels": REQUEST_DISCORD_BLOCKED_CHANNELS_HEADER,
            "allow_all_read": REQUEST_DISCORD_ALLOW_ALL_READ_HEADER,
            "dm_enabled": REQUEST_DISCORD_DM_ENABLED_HEADER,
            "admin_tools_enabled": REQUEST_ADMIN_TOOLS_ENABLED_HEADER,
            "confirm_required": REQUEST_REQUIRE_CONFIRM_HEADER,
            "openai_api_key": REQUEST_OPENAI_API_HEADER,
        },
        "bot_pool_ttl_seconds": BOT_POOL_TTL_SECONDS,
        "bot_pool_max_entries": BOT_POOL_MAX_ENTRIES,
        "max_embed_chars": 4096,
        "max_message_chars": 2000,
        "thread_ops_enabled": True,
        "health_check_sample_limit": HEALTH_CHECK_SAMPLE_LIMIT,
        "channel_cache_ttl_seconds": CHANNEL_CACHE_TTL_SECONDS,
        "job_ttl_seconds": JOB_TTL_SECONDS,
        "job_max_entries": JOB_MAX_ENTRIES,
        "job_execution_timeout_seconds": JOB_EXECUTION_TIMEOUT_SECONDS,
        "max_audit_job_channels": MAX_AUDIT_JOB_CHANNELS,
        "tool_output_max_bytes": MCP_TOOL_OUTPUT_MAX_BYTES,
        "full_catalog_output_max_bytes": MCP_FULL_CATALOG_OUTPUT_MAX_BYTES,
        "retained_job_output_max_bytes": retained_job_output_max_bytes(),
    }

    if (
        PRIMARY_CHANNEL_ID is not None
        and ALLOWED_CHANNEL_IDS
        and (PRIMARY_CHANNEL_ID not in ALLOWED_CHANNEL_IDS)
    ):
        warnings.append(
            "DISCORD_PRIMARY_CHANNEL_ID is not in DISCORD_ALLOWED_CHANNEL_IDS; "
            "it remains a default selector but is not authorized by itself."
        )
    if not ALLOW_ALL_CHANNELS and not ALLOWED_CHANNEL_IDS:
        warnings.append("DISCORD_ALLOWED_CHANNEL_IDS is configured but empty; writes restricted.")
    if not get_active_allow_all_read() and not ALLOWED_CHANNEL_IDS and not ALLOW_ALL_CHANNELS:
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
                if channel_id in write_sample and (not caps["read_history"] or not caps["send"]):
                    warnings.append(
                        f"Write channel {channel_id} lacks read_history/send permission."
                    )
                if channel_id in read_sample and not caps["read_history"]:
                    warnings.append(f"Read channel {channel_id} lacks read_history permission.")
            except Exception:
                capabilities[str(channel_id)] = {
                    "found": False,
                    "error": "channel_unavailable",
                }
                warnings.append(f"Channel {channel_id} is unavailable.")

        write_required = bool(ALLOW_ALL_CHANNELS or ALLOWED_CHANNEL_IDS)
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
    file: dict | str | None = None,
    attachment: dict | str | None = None,
    file_path: str = "",
    file_url: str = "",
    file_base64: str = "",
    file_name: str = "",
    file_content_type: str = "",
    dry_run: bool | str = False,
    thread_if_split: bool | str = False,
    thread_name: str = "",
    confirm: str = "",
) -> dict:
    """Send a Discord message with optional embed content and one optional attachment."""
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    resolved_channel_id = None
    warnings = []
    diagnostics = {}
    try:
        dry_run = parse_bool(dry_run)
        resolved_channel_id = resolve_channel_id(channel_id)
        attachment_request = build_attachment_request(
            file,
            attachment,
            file_path,
            file_url,
            file_base64,
            file_name,
            file_content_type,
        )
        has_attachment = attachment_request is not None

        if not message and not embed_title and not embed_description and not has_attachment:
            error = build_error(
                "invalid_payload",
                "message, embed content, or attachment must be provided.",
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
            "attachments_count": 1 if has_attachment else 0,
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
        except Exception:
            if dry_run:
                warnings.append("Dry-run skipped unavailable channel lookup.")
                diagnostics["channel_lookup_error"] = "channel_unavailable"
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
        if has_attachment and perms is not None and not perms.attach_files:
            error = build_error(
                "permission_denied",
                "Missing permission to attach files.",
                required_perms=["attach_files"],
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

        prepared_attachment = await prepare_discord_attachment(attachment_request, dry_run)
        if prepared_attachment is not None:
            diagnostics["attachment"] = prepared_attachment.metadata

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
            warnings.append(
                "create_threads permission missing or unavailable; falling back to channel."
            )
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
                "attachments": [prepared_attachment.metadata]
                if prepared_attachment is not None
                else [],
                "diagnostics": diagnostics,
            }
            return success_response(data, meta)

        sent_message_ids = []
        sent_message = None
        thread_id = None
        target_channel = channel
        try:
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
                    send_kwargs = {
                        "content": part["content"] or None,
                        "embed": embed,
                    }
                    if prepared_attachment is not None and prepared_attachment.file is not None:
                        send_kwargs["file"] = prepared_attachment.file
                    sent_message = await channel.send(**send_kwargs)
                    sent_message_ids.append(str(sent_message.id))
                    if thread_planned and sent_message is not None:
                        try:
                            thread = await sent_message.create_thread(
                                name=thread_name or "MCP continuation"
                            )
                            record_api_success("create_thread")
                            thread_id = str(thread.id)
                            target_channel = thread
                        except Exception:
                            warnings.append("Thread creation failed; continuing in the channel.")
                            thread_planned = False
                            target_channel = channel
                else:
                    sent = await target_channel.send(
                        content=part["content"] or None,
                        embed=embed,
                    )
                    sent_message_ids.append(str(sent.id))
        finally:
            close_prepared_attachment(prepared_attachment)

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
            "attachments": [prepared_attachment.metadata]
            if prepared_attachment is not None
            else [],
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
        except Exception:
            init_errors.append("client_initialization_failed")
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
    if include_admin and get_active_admin_tools_enabled() and message_id:
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
        read = await read_messages(channel_id=report["channel_id"] or channel_id, count="5")
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

    try:
        owner_fingerprint = current_tenant_fingerprint()
    except ValueError:
        meta = build_meta(start_time, request_id=request_id, warnings=warnings)
        return error_response("permission_denied", "Discord tenant credentials are required.", meta)

    job_id = str(uuid.uuid4())
    now = time.time()
    job = {
        "task_id": job_id,
        "action": action,
        "status": "queued",
        "created_at": job_timestamp(),
        "created_at_ts": now,
        "_last_used_at_ts": now,
        "_owner_fingerprint": owner_fingerprint,
        "result": None,
        "error": None,
        "started_at": None,
        "finished_at": None,
        "finished_at_ts": None,
    }
    async with JOB_LOCK:
        await prune_jobs_locked(now)
        if len(JOB_STORE) >= JOB_MAX_ENTRIES:
            meta = build_meta(start_time, request_id=request_id, warnings=warnings)
            return error_response(
                "resource_exhausted",
                "Discord job capacity is currently full.",
                meta,
            )
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
    try:
        owner_fingerprint = current_tenant_fingerprint()
    except ValueError:
        meta = build_meta(start_time, request_id=request_id, warnings=warnings)
        return error_response("not_found", "Job not found.", meta)
    async with JOB_LOCK:
        await prune_jobs_locked()
        job = owned_job_or_none(JOB_STORE, task_id, owner_fingerprint)
        if job is None:
            meta = build_meta(start_time, request_id=request_id, warnings=warnings)
            return error_response("not_found", "Job not found.", meta)
        job["_last_used_at_ts"] = time.time()
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
        if not get_active_admin_tools_enabled():
            error = build_error(
                "permission_denied",
                "MCP_ADMIN_TOOLS_ENABLED must be true to edit messages.",
                required_perms=[
                    "MCP_ADMIN_TOOLS_ENABLED",
                    f"confirm={CONFIRM_APPLY_VALUE}",
                ],
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
            channel.permissions_for(member) if member is not None else discord.Permissions.none()
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

        parsed_message_id = require_snowflake(message_id, "messageId")
        msg = await retry_read("fetch_message", lambda: channel.fetch_message(parsed_message_id))
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
        if not get_active_admin_tools_enabled():
            error = build_error(
                "permission_denied",
                "MCP_ADMIN_TOOLS_ENABLED must be true to delete messages.",
                required_perms=[
                    "MCP_ADMIN_TOOLS_ENABLED",
                    f"confirm={CONFIRM_APPLY_VALUE}",
                ],
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
            channel.permissions_for(member) if member is not None else discord.Permissions.none()
        )
        diagnostics["permissions"] = channel_capabilities(perms)

        parsed_message_id = require_snowflake(message_id, "messageId")
        msg = await retry_read("fetch_message", lambda: channel.fetch_message(parsed_message_id))
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
                m async for m in channel.history(limit=limit, before=before_obj, after=after_obj)
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
            error = build_error("invalid_payload", "before_message_id must be a Discord snowflake.")
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
            error = build_error("invalid_payload", "after_message_id must be a Discord snowflake.")
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
            error = build_error("invalid_payload", "date_from must be earlier than date_to.")
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
            error = build_error("invalid_payload", "author_id must be a Discord snowflake.")
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

        messages = await retry_read("search_messages", lambda: fetch_history(channel, limit_value))

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
            error = build_error("invalid_payload", "message_id must be a Discord snowflake.")
            return error_with_log(
                "analyze_attachment",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )

        msg = await retry_read("fetch_message", lambda: channel.fetch_message(parsed_message_id))
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
                prompt_text = "Extract all readable text from this image. Preserve line breaks."

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
        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                trust_env=False,
            ) as session:
                async with session.post(
                    OPENAI_VISION_API_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_payload,
                    allow_redirects=False,
                ) as response:
                    if not 200 <= response.status < 300:
                        error = build_error(
                            "provider_unavailable",
                            "OpenAI request failed.",
                            diagnostics={"status": response.status},
                        )
                        return error_with_log(
                            "analyze_attachment",
                            start_time,
                            request_id,
                            error,
                            warnings=warnings,
                            channel_id=resolved_channel_id,
                        )
                    response_bytes = await read_bounded_openai_response(response)
        except TimeoutError:
            error = build_error(
                "provider_unavailable",
                "OpenAI request timed out.",
            )
            return error_with_log(
                "analyze_attachment",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )
        except (aiohttp.ClientError, ProviderResponseError):
            error = build_error(
                "provider_unavailable",
                "OpenAI response could not be accepted.",
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
            response_json = json.loads(response_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            error = build_error(
                "provider_unavailable",
                "OpenAI returned an invalid response payload.",
            )
            return error_with_log(
                "analyze_attachment",
                start_time,
                request_id,
                error,
                warnings=warnings,
                channel_id=resolved_channel_id,
            )
        if not isinstance(response_json, dict):
            error = build_error(
                "provider_unavailable",
                "OpenAI returned an invalid response payload.",
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
        usage = normalize_openai_usage(response_json.get("usage"))
        choices = response_json.get("choices") or []
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if message and isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    result_text = content
        output_text = response_json.get("output_text")
        if not result_text and isinstance(output_text, str):
            result_text = output_text

        if len(result_text) > OPENAI_RESULT_MAX_CHARS:
            result_text = result_text[:OPENAI_RESULT_MAX_CHARS]
            warnings.append(f"OpenAI text was truncated to {OPENAI_RESULT_MAX_CHARS} characters.")

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
                    "created_at": thread.created_at.isoformat() if thread.created_at else None,
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
            channel.permissions_for(member) if member is not None else discord.Permissions.none()
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
            error = build_error("invalid_payload", "auto_archive_duration must be an integer.")
            return error_with_log("create_thread", start_time, request_id, error, warnings=warnings)

        parsed_message_id = require_snowflake(message_id, "messageId")
        msg = await retry_read("fetch_message", lambda: channel.fetch_message(parsed_message_id))
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

        thread = await msg.create_thread(name=name, auto_archive_duration=duration or None)
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
        data = {
            "thread_id": str(thread.id),
            "name": thread.name,
            "message_id": str(msg.id),
        }
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
        messages = await fetch_messages_in_range(channel, start_utc, end_utc, limit_value)

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
            error = build_error("invalid_payload", "channel_ids must be a non-empty list.")
            return error_with_log("daily_audit_job_submit", start_time, request_id, error)
        if len(channel_ids) > MAX_AUDIT_JOB_CHANNELS:
            error = build_error(
                "invalid_payload",
                f"channel_ids cannot contain more than {MAX_AUDIT_JOB_CHANNELS} entries.",
            )
            return error_with_log("daily_audit_job_submit", start_time, request_id, error)

        tz = resolve_timezone(timezone_name or None)
        timezone_label = getattr(tz, "key", None) or tz.tzname(datetime.now()) or "UTC"
        start_local, _ = parse_audit_date(date, tz)
        audit_date = start_local.date().isoformat()

        parsed_ids = []
        seen_ids: set[int] = set()
        for raw_id in channel_ids:
            parsed = parse_snowflake(raw_id)
            if parsed is None:
                error = build_error("invalid_payload", f"Invalid channel id: {raw_id}.")
                return error_with_log(
                    "daily_audit_job_submit",
                    start_time,
                    request_id,
                    error,
                    warnings=warnings,
                )
            if not is_read_allowed(parsed):
                error = build_error(
                    "permission_denied",
                    "Channel is outside the effective read policy.",
                    required_perms=["read_allowed_channel"],
                )
                return error_with_log(
                    "daily_audit_job_submit",
                    start_time,
                    request_id,
                    error,
                    warnings=warnings,
                    channel_id=parsed,
                )
            if parsed not in seen_ids:
                seen_ids.add(parsed)
                parsed_ids.append(parsed)

        try:
            owner_fingerprint = current_tenant_fingerprint()
        except ValueError:
            error = build_error("permission_denied", "Discord tenant credentials are required.")
            return error_with_log("daily_audit_job_submit", start_time, request_id, error)
        job_id = str(uuid.uuid4())
        now = time.time()
        job = {
            "task_id": job_id,
            "status": "queued",
            "created_at": job_timestamp(),
            "created_at_ts": now,
            "_last_used_at_ts": now,
            "_owner_fingerprint": owner_fingerprint,
            "finished_at": None,
            "finished_at_ts": None,
            "date": audit_date,
            "timezone": timezone_label,
            "total_channels": len(parsed_ids),
            "remaining_channel_ids": parsed_ids,
            "processed_channel_ids": [],
            "results": {},
            "error": None,
        }
        async with AUDIT_JOB_LOCK:
            await prune_audit_jobs_locked(now)
            if len(AUDIT_JOB_STORE) >= JOB_MAX_ENTRIES:
                error = build_error(
                    "resource_exhausted",
                    "Discord audit job capacity is currently full.",
                )
                return error_with_log(
                    "daily_audit_job_submit",
                    start_time,
                    request_id,
                    error,
                    warnings=warnings,
                )
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
            return error_with_log("daily_audit_job_status", start_time, request_id, error)
        try:
            owner_fingerprint = current_tenant_fingerprint()
        except ValueError:
            error = build_error("not_found", "Job not found.")
            return error_with_log("daily_audit_job_status", start_time, request_id, error)
        async with AUDIT_JOB_LOCK:
            await prune_audit_jobs_locked()
            job = owned_job_or_none(AUDIT_JOB_STORE, task_id, owner_fingerprint)
            if job is None:
                error = build_error("not_found", "Job not found.")
                return error_with_log("daily_audit_job_status", start_time, request_id, error)
            if job.get("finished_at_ts") is None:
                job["_last_used_at_ts"] = time.time()
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
            return error_with_log("daily_audit_job_next", start_time, request_id, error)
        try:
            owner_fingerprint = current_tenant_fingerprint()
        except ValueError:
            error = build_error("not_found", "Job not found.")
            return error_with_log("daily_audit_job_next", start_time, request_id, error)

        channel_id: int | None = None
        finalized = False
        try:
            async with AUDIT_JOB_LOCK:
                await prune_audit_jobs_locked()
                job = owned_job_or_none(AUDIT_JOB_STORE, task_id, owner_fingerprint)
                if job is None:
                    error = build_error("not_found", "Job not found.")
                    return error_with_log("daily_audit_job_next", start_time, request_id, error)
                if job.get("finished_at_ts") is not None or job.get("status") == "completed":
                    snapshot = build_audit_job_snapshot(job, include_results=True)
                    meta = build_meta(start_time, request_id=request_id, warnings=warnings)
                    return success_response(snapshot, meta)
                if job.get("status") == "running":
                    error = build_error(
                        "conflict", "An audit step is already in progress for this job."
                    )
                    return error_with_log("daily_audit_job_next", start_time, request_id, error)
                if not job.get("remaining_channel_ids"):
                    job["status"] = "completed"
                    job["finished_at"] = job_timestamp()
                    job["finished_at_ts"] = time.time()
                    snapshot = build_audit_job_snapshot(job, include_results=True)
                    meta = build_meta(start_time, request_id=request_id, warnings=warnings)
                    return success_response(snapshot, meta)

                channel_id = job["remaining_channel_ids"].pop(0)
                job["status"] = "running"
                job["_last_used_at_ts"] = time.time()
                audit_date = job.get("date", "")
                audit_timezone = job.get("timezone", "")

            try:
                channel_result = await asyncio.wait_for(
                    channel_daily_audit(
                        channel_id=str(channel_id),
                        date=audit_date,
                        limit=limit,
                        timezone_name=audit_timezone,
                        include_threads=include_threads,
                    ),
                    timeout=JOB_EXECUTION_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                error = build_error(
                    "timeout",
                    f"Audit step exceeded {JOB_EXECUTION_TIMEOUT_SECONDS} seconds.",
                )
                return error_with_log("daily_audit_job_next", start_time, request_id, error)

            retained_channel_result = state_safe_payload(channel_result)
            aggregate_boundary_error = None
            async with AUDIT_JOB_LOCK:
                job = owned_job_or_none(AUDIT_JOB_STORE, task_id, owner_fingerprint)
                if job is None:
                    error = build_error("not_found", "Job not found.")
                    return error_with_log("daily_audit_job_next", start_time, request_id, error)
                processed_channel_ids = job.setdefault("processed_channel_ids", [])
                if channel_id not in processed_channel_ids:
                    processed_channel_ids.append(channel_id)
                aggregate_boundary_error = append_bounded_audit_job_result(
                    job,
                    channel_id,
                    retained_channel_result,
                )
                job["_last_used_at_ts"] = time.time()
                if aggregate_boundary_error is not None:
                    job["status"] = "failed"
                    job["error"] = aggregate_boundary_error["error"]
                    job["finished_at"] = job_timestamp()
                    job["finished_at_ts"] = time.time()
                else:
                    if job.get("remaining_channel_ids"):
                        job["status"] = "queued"
                    else:
                        job["status"] = "completed"
                        if job.get("finished_at_ts") is None:
                            job["finished_at"] = job_timestamp()
                            job["finished_at_ts"] = time.time()

                snapshot = build_audit_job_snapshot(job, include_results=False)
                finalized = True

            if aggregate_boundary_error is not None:
                return error_with_log(
                    "daily_audit_job_next",
                    start_time,
                    request_id,
                    aggregate_boundary_error["error"],
                    warnings=warnings,
                    channel_id=channel_id,
                )

            data = {
                "channel_id": str(channel_id),
                "channel_result": channel_result,
                "job": snapshot,
            }
            meta = build_meta(start_time, request_id=request_id, warnings=warnings)
            log_action("daily_audit_job_next", start_time, "ok")
            return success_response(data, meta)
        finally:
            if channel_id is not None and not finalized:
                await restore_audit_job_channel_safely(task_id, owner_fingerprint, channel_id)
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
        parsed_message_id = require_snowflake(message_id, "messageId")
        msg = await retry_read("fetch_message", lambda: channel.fetch_message(parsed_message_id))
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
        parsed_message_id = require_snowflake(message_id, "messageId")
        msg = await retry_read("fetch_message", lambda: channel.fetch_message(parsed_message_id))
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
            error = build_error("invalid_payload", "duration_minutes must be a positive integer.")
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
            error = build_error("invalid_payload", "User is currently in the guild; cannot unban.")
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
            return error_with_log("get_user_id_by_name", start_time, request_id, error)
        guild = await get_guild(guild_id)
        name = username
        discriminator = None
        if "#" in username:
            idx = username.rfind("#")
            name = username[:idx]
            discriminator = username[idx + 1 :]

        async def fetch_members():
            return [
                m async for m in guild.fetch_members(limit=None) if m.name.lower() == name.lower()
            ]

        members = await retry_read("fetch_members", fetch_members)
        if discriminator:
            members = [m for m in members if m.discriminator == discriminator]

        if not members:
            error = build_error("not_found", f"No user found with username {username}.")
            return error_with_log(
                "get_user_id_by_name",
                start_time,
                request_id,
                error,
                warnings=warnings,
                guild_id=guild.id,
            )
        if len(members) > 1:
            user_list = [f"{m.name}#{m.discriminator} (ID: {m.id})" for m in members]
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
        parsed_message_id = require_snowflake(message_id, "messageId")
        msg = await retry_read("fetch_dm_message", lambda: dm.fetch_message(parsed_message_id))
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
        parsed_message_id = require_snowflake(message_id, "messageId")
        msg = await retry_read("fetch_dm_message", lambda: dm.fetch_message(parsed_message_id))
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
            return error_with_log("create_text_channel", start_time, request_id, error)
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
            parsed_category_id = require_snowflake(category_id, "categoryId")
            category = discord.utils.get(guild.categories, id=parsed_category_id)
            if category is None:
                category = await retry_read(
                    "fetch_category", lambda: guild.fetch_channel(parsed_category_id)
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
        meta = build_meta(start_time, request_id=request_id, warnings=warnings, guild_id=guild.id)
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
        channel = guild.get_channel(parsed_channel_id)
        if channel is None:
            channel = await retry_read(
                "fetch_channel", lambda: guild.fetch_channel(parsed_channel_id)
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
            not get_active_allow_all_read()
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
            channels = [c for c in channels_list if query_key in normalize_channel_key(c.name)]
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
            not get_active_allow_all_read()
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
        payload = [{"id": str(c.id), "name": c.name, "type": c.type.name} for c in channels]
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
        meta = build_meta(start_time, request_id=request_id, warnings=warnings, guild_id=guild.id)
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
            error = build_error("invalid_payload", "categoryId must be a Discord snowflake.")
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
        category = discord.utils.get(guild.categories, id=parsed_category_id)
        if category is None:
            category = await retry_read(
                "fetch_category", lambda: guild.fetch_channel(parsed_category_id)
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
        meta = build_meta(start_time, request_id=request_id, warnings=warnings, guild_id=guild.id)
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
            not get_active_allow_all_read()
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
        categories = [c for c in guild.categories if c.name.lower() == category_name.lower()]
        if not get_active_allow_all_read() and not ALLOW_ALL_CHANNELS:
            allowed_ids = set(ALLOWED_CHANNEL_IDS)
            if PRIMARY_CHANNEL_ID is not None:
                allowed_ids.add(PRIMARY_CHANNEL_ID)
            categories = [c for c in categories if any(ch.id in allowed_ids for ch in c.channels)]
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
            {"id": str(c.id), "name": c.name, "channel_count": len(c.channels)} for c in categories
        ]
        meta = build_meta(start_time, request_id=request_id, warnings=warnings, guild_id=guild.id)
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
            error = build_error("invalid_payload", "categoryId must be a Discord snowflake.")
            return error_with_log("list_channels_in_category", start_time, request_id, error)
        if (
            not get_active_allow_all_read()
            and not ALLOWED_CHANNEL_IDS
            and PRIMARY_CHANNEL_ID is None
            and not ALLOW_ALL_CHANNELS
        ):
            error = build_error(
                "permission_denied",
                "Channel reads are restricted.",
                required_perms=["DISCORD_ALLOW_ALL_READ=true or DISCORD_ALLOWED_CHANNEL_IDS"],
            )
            return error_with_log("list_channels_in_category", start_time, request_id, error)

        guild = await get_guild(guild_id)
        category = discord.utils.get(guild.categories, id=parsed_category_id)
        if category is None:
            category = await retry_read(
                "fetch_category", lambda: guild.fetch_channel(parsed_category_id)
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
        payload = [{"id": str(c.id), "name": c.name, "type": c.type.name} for c in channels]
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
        normalized_name = (name or "").strip()
        if not normalized_name:
            error = build_error("invalid_payload", "webhook name cannot be null.")
            return error_with_log("create_webhook", start_time, request_id, error)
        if len(normalized_name) > 80:
            error = build_error("invalid_payload", "webhook name exceeds 80 characters.")
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
        webhook = await channel.create_webhook(name=normalized_name)
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
        data = {
            "webhook_id": str(webhook.id),
            "name": webhook.name,
            "url": webhook.url,
        }
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
        parsed_webhook_id = require_snowflake(webhook_id, "webhookId")
        client = await get_client()
        webhook = await retry_read("fetch_webhook", lambda: client.fetch_webhook(parsed_webhook_id))
        if webhook is None:
            error = build_error("not_found", "Webhook not found by webhookId.")
            return error_with_log(
                "delete_webhook", start_time, request_id, error, warnings=warnings
            )
        active_guild_id = get_active_guild_id()
        webhook_guild_id = parse_snowflake(getattr(webhook, "guild_id", None))
        webhook_channel_id = parse_snowflake(getattr(webhook, "channel_id", None))
        if webhook_channel_id is None or (
            active_guild_id is not None and webhook_guild_id != active_guild_id
        ):
            error = build_error("not_found", "Webhook not found by webhookId.")
            return error_with_log(
                "delete_webhook", start_time, request_id, error, warnings=warnings
            )
        allow_error = require_write_allowed(
            webhook_channel_id,
            "delete_webhook",
            start_time,
            request_id,
            warnings=warnings,
            guild_id=webhook_guild_id,
        )
        if allow_error:
            return allow_error
        name = webhook.name
        await webhook.delete()
        record_api_success("delete_webhook")
        log_action(
            "delete_webhook",
            start_time,
            "ok",
            guild_id=webhook_guild_id,
            channel_id=webhook_channel_id,
        )
        meta = build_meta(
            start_time,
            request_id=request_id,
            warnings=warnings,
            guild_id=webhook_guild_id,
            channel_id=webhook_channel_id,
        )
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
        payload = [{"id": str(w.id), "name": w.name} for w in webhooks]
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
        if WEBHOOK_CREDENTIAL_PATTERN.fullmatch(webhook_url) is None:
            error = build_error(
                "invalid_payload",
                "webhookUrl must be a complete HTTPS Discord webhook URL.",
            )
            return error_with_log("send_webhook_message", start_time, request_id, error)
        if not message:
            error = build_error("invalid_payload", "message cannot be null.")
            return error_with_log("send_webhook_message", start_time, request_id, error)
        if len(message) > 2000:
            error = build_error("invalid_payload", "message exceeds 2000 characters.")
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
            "send_webhook_message",
            start_time,
            request_id,
            warnings=warnings,
        )
        if writes_error:
            return writes_error

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
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


def current_tool_manifest() -> dict[str, Any]:
    """Build the complete catalog only after every native tool is registered."""
    return build_tool_manifest(mcp._tool_manager, build_sha=get_build_sha())


def configuration_snapshot() -> dict[str, Any]:
    """Inspect configuration presence without contacting Discord or returning values."""
    headers = normalize_headers(get_http_headers()) if ALLOW_REQUEST_OVERRIDES else {}
    warnings: list[str] = []

    request_token_present = bool(headers.get(REQUEST_DISCORD_TOKEN_HEADER, ""))
    request_guild_raw = headers.get(REQUEST_DISCORD_GUILD_ID_HEADER, "")
    request_guild_valid = bool(request_guild_raw and parse_snowflake(request_guild_raw))
    allowed_channels_present = REQUEST_DISCORD_ALLOWED_CHANNELS_HEADER in headers
    allowed_channels_valid = False
    if allowed_channels_present:
        try:
            parse_request_allowed_channel_ids(
                headers.get(REQUEST_DISCORD_ALLOWED_CHANNELS_HEADER, "")
            )
            allowed_channels_valid = True
        except ValueError:
            warnings.append("The request allowed-channel policy is invalid.")
    blocked_header_present = REQUEST_DISCORD_BLOCKED_CHANNELS_HEADER in headers
    blocked_header_valid = True
    if blocked_header_present:
        try:
            parse_request_blocked_channel_ids(
                headers.get(REQUEST_DISCORD_BLOCKED_CHANNELS_HEADER, "")
            )
        except ValueError:
            blocked_header_valid = False
            warnings.append("The request blocked-channel policy is invalid.")

    if request_guild_raw and not request_guild_valid:
        warnings.append("The request-scoped Discord guild ID is not a valid snowflake.")

    token_configured = (
        request_token_present if DISCORD_CREDENTIAL_MODE == "request" else bool(DISCORD_TOKEN)
    )
    guild_configured = (
        request_guild_valid
        if DISCORD_CREDENTIAL_MODE == "request"
        else DEFAULT_GUILD_ID is not None
    )

    def request_policy_value(header_name: str) -> bool | None:
        try:
            return parse_optional_bool_header(headers.get(header_name), header_name, warnings)
        except ValueError:
            warnings.append("A request policy header must be true or false.")
            return None

    requested_read_all = request_policy_value(REQUEST_DISCORD_ALLOW_ALL_READ_HEADER)
    requested_dm = request_policy_value(REQUEST_DISCORD_DM_ENABLED_HEADER)
    requested_admin = request_policy_value(REQUEST_ADMIN_TOOLS_ENABLED_HEADER)
    requested_confirm = request_policy_value(REQUEST_REQUIRE_CONFIRM_HEADER)

    missing = []
    if REQUIRE_REQUEST_DISCORD_TOKEN and not request_token_present:
        missing.append("discord_bot_token")
    elif not token_configured:
        missing.append("discord_bot_token")
    if REQUIRE_REQUEST_GUILD_ID and not request_guild_valid:
        missing.append("discord_guild_id")
    elif not guild_configured:
        missing.append("discord_guild_id")
    if REQUIRE_REQUEST_ALLOWED_CHANNELS and not allowed_channels_valid:
        missing.append("allowed_channel_policy")
    if not ALLOW_ALL_CHANNELS and not ALLOWED_CHANNEL_IDS:
        missing.append("server_channel_policy")
    if not blocked_header_valid:
        missing.append("valid_blocked_channel_policy")
    if PUBLIC_MODE and not MCP_PORTAL_GRANT_TOKEN:
        missing.append("portal_service_grant")

    return {
        "ready": not missing,
        "missing": missing,
        "configuration": {
            "publicMode": PUBLIC_MODE,
            "credentialMode": DISCORD_CREDENTIAL_MODE,
            "portalGrantConfigured": bool(MCP_PORTAL_GRANT_TOKEN),
            "discordBotTokenConfigured": token_configured,
            "discordGuildConfigured": guild_configured,
            "blockedChannelPolicyProvided": blocked_header_present,
            "allowedChannelPolicyProvided": allowed_channels_present,
            "allowedChannelPolicyValid": allowed_channels_valid,
            "serverChannelPolicyConfigured": bool(ALLOW_ALL_CHANNELS or ALLOWED_CHANNEL_IDS),
            "requestOverridesEnabled": ALLOW_REQUEST_OVERRIDES,
        },
        "capabilities": {
            "readAllAllowedChannels": (
                DISCORD_ALLOW_ALL_READ and requested_read_all is True
                if DISCORD_CREDENTIAL_MODE == "request"
                else DISCORD_ALLOW_ALL_READ
            ),
            "directMessagesEnabled": (
                DISCORD_DM_ENABLED and requested_dm is True
                if DISCORD_CREDENTIAL_MODE == "request"
                else DISCORD_DM_ENABLED
            ),
            "adminToolsEnabled": (
                MCP_ADMIN_TOOLS_ENABLED and requested_admin is True
                if DISCORD_CREDENTIAL_MODE == "request"
                else MCP_ADMIN_TOOLS_ENABLED
            ),
            "confirmationRequired": (
                CONFIRM_REQUIRED or requested_confirm is True
                if DISCORD_CREDENTIAL_MODE == "request"
                else CONFIRM_REQUIRED
            ),
            "visionEnabled": OPENAI_VISION_ENABLED,
        },
        "warnings": warnings,
    }


@mcp.tool()
async def check_configuration() -> dict[str, Any]:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    data = configuration_snapshot()
    meta = build_meta(
        start_time,
        request_id=request_id,
        warnings=data.pop("warnings", []),
    )
    return success_response(data, meta)


@mcp.tool()
async def list_capabilities(include_descriptors: bool | str = False) -> dict[str, Any]:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    manifest = current_tool_manifest()
    include_descriptors = parse_bool(include_descriptors)
    data = {
        "schemaVersion": manifest["schemaVersion"],
        "serviceId": manifest["serviceId"],
        "serviceAliases": manifest["serviceAliases"],
        "catalogVersion": manifest["catalogVersion"],
        "buildSha": manifest["buildSha"],
        "descriptorHash": manifest["descriptorHash"],
        "counts": manifest["counts"],
        "categories": manifest_categories(manifest),
        "tools": manifest["tools"] if include_descriptors else [],
        "descriptorsIncluded": include_descriptors,
        "nextAction": (
            None
            if include_descriptors
            else {
                "toolName": "list_capabilities",
                "arguments": {"include_descriptors": True},
            }
        ),
    }
    return success_response(
        data,
        build_meta(start_time, request_id=request_id),
    )


@mcp.tool()
async def get_endpoint_coverage(feature: str = "") -> dict[str, Any]:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    coverage = filter_endpoint_coverage(feature)
    data = {
        "serviceId": "discord",
        "catalogVersion": CATALOG_VERSION,
        "retrievedAt": "2026-07-18",
        "filter": feature or None,
        "count": len(coverage),
        "coverage": coverage,
    }
    return success_response(data, build_meta(start_time, request_id=request_id))


@mcp.tool()
async def get_tool_usage(tool_name: str) -> dict[str, Any]:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    manifest = current_tool_manifest()
    descriptor = find_tool_descriptor(manifest, tool_name)
    meta = build_meta(start_time, request_id=request_id)
    if descriptor is None:
        return error_response(
            "not_found",
            "Tool is not present in the Discord ToolManifest.",
            meta,
            diagnostics={"requested_type": "tool_name_or_alias"},
        )
    return success_response(
        {
            "descriptor": descriptor,
            "nextAction": {
                "type": "mcp_tool_call",
                "toolName": descriptor["nativeToolName"],
                "reviewInputSchema": True,
                "confirmationRequired": descriptor["confirmation"]["required"],
            },
        },
        meta,
    )


@mcp.tool()
async def find_tools(
    query: str,
    category: str = "",
    risk: str = "",
    limit: int = 8,
    include_legacy: bool | str = False,
) -> dict[str, Any]:
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    meta = build_meta(start_time, request_id=request_id)
    try:
        matches = find_manifest_tools(
            current_tool_manifest(),
            query,
            category=category,
            risk=risk,
            limit=limit,
            include_legacy=parse_bool(include_legacy),
        )
    except (TypeError, ValueError):
        return error_response(
            "invalid_payload",
            "Tool discovery filters are invalid.",
            meta,
        )
    return success_response(
        {
            "query": query,
            "filters": {
                "category": category or None,
                "risk": risk or None,
                "includeLegacy": parse_bool(include_legacy),
            },
            "count": len(matches),
            "matches": matches,
        },
        meta,
    )


def build_app() -> Any:
    app_factory = mcp.streamable_http_app
    app = app_factory() if callable(app_factory) else app_factory
    protected_app = AccessControlMiddleware(
        app,
        RUNTIME_SECURITY,
        singleton_headers=REQUEST_SECURITY_HEADERS,
    )

    async def host_override(scope, receive, send):
        if scope["type"] == "http":
            if scope.get("path") == "/health":
                manifest = current_tool_manifest()
                tool_count = manifest["counts"]["raw"]
                server_channel_policy_ready = bool(ALLOW_ALL_CHANNELS or ALLOWED_CHANNEL_IDS)
                if DISCORD_CREDENTIAL_MODE == "request":
                    service_auth_ready = bool(
                        MCP_PORTAL_GRANT_TOKEN if PUBLIC_MODE else MCP_ACCESS_TOKEN
                    )
                    configuration_ready = service_auth_ready and server_channel_policy_ready
                else:
                    configuration_ready = bool(
                        DISCORD_TOKEN
                        and DEFAULT_GUILD_ID is not None
                        and server_channel_policy_ready
                    )
                body = json.dumps(
                    {
                        "ok": True,
                        "status": "healthy" if configuration_ready else "degraded",
                        "service": "discord-mcp",
                        "service_id": "discord",
                        "version": __version__,
                        "build_sha": manifest["buildSha"],
                        "source_fingerprint": get_source_fingerprint(),
                        "image_reference": get_image_reference(),
                        "catalog_version": manifest["catalogVersion"],
                        "descriptor_hash": manifest["descriptorHash"],
                        "tool_count": tool_count,
                        "raw_tool_count": manifest["counts"]["raw"],
                        "exposed_tool_count": tool_count,
                        "agent_ready_tool_count": manifest["counts"]["agentReady"],
                        "documented_tool_count": manifest["counts"]["documented"],
                        "tools": {
                            "total": tool_count,
                            "raw": manifest["counts"]["raw"],
                            "agent_ready": manifest["counts"]["agentReady"],
                            "legacy": manifest["counts"]["legacy"],
                            "hidden": manifest["counts"]["hidden"],
                            "documented": manifest["counts"]["documented"],
                        },
                        "configuration_ready": configuration_ready,
                        "configuration": {
                            "provider_credentials": (
                                "request_scoped"
                                if DISCORD_CREDENTIAL_MODE == "request"
                                else "server_scoped"
                            ),
                            "portal_grant_ready": bool(MCP_PORTAL_GRANT_TOKEN),
                            "server_channel_policy_ready": server_channel_policy_ready,
                        },
                        "public_mode": PUBLIC_MODE,
                        "portal_grant_configured": bool(MCP_PORTAL_GRANT_TOKEN),
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
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
        await protected_app(scope, receive, send)

    return host_override


def main() -> None:
    """Run the authenticated Streamable HTTP Discord MCP server."""

    os.environ.setdefault("HOST", MCP_BIND_ADDRESS)
    os.environ.setdefault("PORT", str(MCP_HTTP_PORT))
    uvicorn.run(build_app, host=MCP_BIND_ADDRESS, port=MCP_HTTP_PORT, factory=True)


if __name__ == "__main__":
    main()
