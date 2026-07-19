#!/usr/bin/env python3
"""Create a private runtime scaffold without printing generated secrets."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path


def build_environment(mode: str) -> str:
    if mode not in {"standalone", "portal"}:
        raise ValueError("mode must be exactly standalone or portal")
    access_token = secrets.token_urlsafe(48) if mode == "standalone" else ""
    portal_grant = secrets.token_urlsafe(48) if mode == "portal" else ""
    credential_mode = "request" if mode == "portal" else "server"
    server_channel_ceiling = "ALL" if mode == "portal" else ""
    return "\n".join(
        (
            f"MCP_MODE={mode}",
            f"MCP_ACCESS_TOKEN={access_token}",
            f"MCP_PORTAL_GRANT_TOKEN={portal_grant}",
            "MCP_PORTAL_GRANT_HEADER=X-MADPANDA-PORTAL-GRANT",
            "MCP_HOST_PORT=8085",
            "MCP_RUNTIME_IMAGE=discord-mcp:local-source",
            "MCP_ALLOWED_HOSTS=localhost:*,127.0.0.1:*,[::1]:*,discord-mcp:*",
            "MCP_ALLOWED_ORIGINS=",
            "MCP_REQUEST_BODY_MAX_BYTES=1048576",
            "MCP_REQUEST_BODY_TIMEOUT_SECONDS=10",
            "MCP_TOOL_OUTPUT_MAX_BYTES=49152",
            "MCP_FULL_CATALOG_OUTPUT_MAX_BYTES=1048576",
            "MCP_BIND_ADDRESS=127.0.0.1",
            "MCP_HTTP_PORT=8085",
            "MCP_BUILD_SHA=development",
            "MCP_SOURCE_FINGERPRINT=development",
            "MCP_IMAGE_REFERENCE=development",
            f"DISCORD_CREDENTIAL_MODE={credential_mode}",
            "DISCORD_TOKEN=",
            "DISCORD_GUILD_ID=",
            "DISCORD_PRIMARY_CHANNEL_ID=",
            f"DISCORD_ALLOWED_CHANNEL_IDS={server_channel_ceiling}",
            "DISCORD_BLOCKED_CHANNEL_IDS=",
            "DISCORD_ALLOW_ALL_READ=false",
            "DISCORD_DM_ENABLED=false",
            "DISCORD_PROTECTED_USER_IDS=",
            "DISCORD_PROTECTED_ROLE_IDS=",
            "DISCORD_ALLOWED_TARGET_ROLE_IDS=",
            "DISCORD_AUDIT_TIMEZONE=UTC",
            "MCP_ADMIN_TOOLS_ENABLED=false",
            "MCP_REQUIRE_CONFIRM=true",
            "MCP_ATTACHMENT_ALLOWED_DIRS=",
            "MCP_DISCORD_TOKEN_HEADER=x-discord-bot-token",
            "MCP_DISCORD_GUILD_ID_HEADER=x-discord-guild-id",
            "MCP_DISCORD_ALLOWED_CHANNELS_HEADER=x-discord-allowed-channels",
            "MCP_DISCORD_BLOCKED_CHANNELS_HEADER=x-discord-blocked-channels",
            "MCP_DISCORD_ALLOW_ALL_READ_HEADER=x-discord-allow-all-read",
            "MCP_DISCORD_DM_ENABLED_HEADER=x-discord-dm-enabled",
            "MCP_ADMIN_TOOLS_ENABLED_HEADER=x-mcp-admin-tools-enabled",
            "MCP_REQUIRE_CONFIRM_HEADER=x-mcp-require-confirm",
            "MCP_OPENAI_API_HEADER=x-openai-api",
            "MCP_BOT_POOL_TTL_SECONDS=900",
            "MCP_BOT_POOL_MAX_ENTRIES=32",
            "DISCORD_CHANNEL_CACHE_TTL_SECONDS=600",
            "DISCORD_JOB_TTL_SECONDS=3600",
            "DISCORD_JOB_MAX_ENTRIES=128",
            "DISCORD_JOB_EXECUTION_TIMEOUT_SECONDS=300",
            "DISCORD_ATTACHMENT_MAX_MB=25",
            "DISCORD_ATTACHMENT_URL_TIMEOUT_SECONDS=20",
            "OPENAI_VISION_ENABLED=false",
            "OPENAI_VISION_MODEL=gpt-4o-mini",
            "OPENAI_VISION_API_URL=https://api.openai.com/v1/chat/completions",
            "OPENAI_VISION_MAX_MB=10",
            "OPENAI_VISION_TIMEOUT_SECONDS=30",
            "LOG_LEVEL=INFO",
            "LOG_REDACT_MESSAGE_CONTENT=true",
            "",
        )
    )


def create_environment(env_path: Path, mode: str) -> bool:
    """Atomically create one mode-0600 environment; never overwrite."""

    try:
        descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(build_environment(mode))
        env_path.chmod(0o600)
    except BaseException:
        env_path.unlink(missing_ok=True)
        raise
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an ignored mode-0600 Discord MCP runtime environment."
    )
    parser.add_argument("--mode", choices=("standalone", "portal"), default="standalone")
    args = parser.parse_args()
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not create_environment(env_path, args.mode):
        print("Runtime environment already exists; no values changed.")
        return
    follow_up = (
        "Add DISCORD_TOKEN, DISCORD_GUILD_ID, and a narrow DISCORD_ALLOWED_CHANNEL_IDS policy."
        if args.mode == "standalone"
        else "Keep provider credentials in the broker and inject the documented request headers."
    )
    print(
        f"Created ignored mode-0600 {args.mode} environment with a fresh service credential; "
        f"no value was printed. {follow_up}"
    )


if __name__ == "__main__":
    main()
