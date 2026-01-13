<div align="center">
  <img src="assets/img/Discord_MCP_full_logo.svg" width="60%" alt="DeepSeek-V3" />
</div>
<hr>
<div align="center" style="line-height: 1;">
    <a href="https://github.com/modelcontextprotocol/servers" target="_blank" style="margin: 2px;">
        <img alt="MCP Server" src="https://badge.mcpx.dev?type=server" style="display: inline-block; vertical-align: middle;"/>
    </a>
    <a href="https://smithery.ai/server/@SaseQ/discord-mcp" target="_blank" style="margin: 2px;">
        <img alt="Smithery Badge" src="https://camo.githubusercontent.com/ee5c6c6dc502821f4d57313b2885f7878af52be14142dd98526ea12aedf9b260/68747470733a2f2f736d6974686572792e61692f62616467652f40646d6f6e74676f6d65727934302f646565707365656b2d6d63702d736572766572" data-canonical-src="https://smithery.ai/server/@SaseQ/discord-mcp" style="display: inline-block; vertical-align: middle;"/>
    </a>
    <a href="https://discord.gg/5Uvxe5jteM" target="_blank" style="margin: 2px;">
        <img alt="Discord" src="https://img.shields.io/discord/936242526120194108?color=7389D8&label&logo=discord&logoColor=ffffff" style="display: inline-block; vertical-align: middle;"/>
    </a>
    <a href="https://github.com/SaseQ/discord-mcp/blob/main/LICENSE" target="_blank" style="margin: 2px;">
        <img alt="MIT License" src="https://img.shields.io/github/license/SaseQ/discord-mcp" style="display: inline-block; vertical-align: middle;"/>
    </a>
</div>


## 📖 Description

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/introduction) server for the Discord API [(JDA)](https://jda.wiki/), 
allowing seamless integration of Discord Bot with MCP-compatible applications like Claude Desktop.

Enable your AI assistants to seamlessly interact with Discord. Manage channels, send messages, and retrieve server information effortlessly. Enhance your Discord experience with powerful automation capabilities.


## 🔬 Installation

### ► 🐳 Docker Installation (Recommended)
> NOTE: Docker installation is required. Full instructions can be found on [docker.com](https://www.docker.com/products/docker-desktop/).
```json
{
  "mcpServers": {
    "mcp-server": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "DISCORD_TOKEN=<YOUR_DISCORD_BOT_TOKEN>",
        "-e", "DISCORD_GUILD_ID=<OPTIONAL_DEFAULT_SERVER_ID>",
        "saseq/discord-mcp:latest"
      ]
    }
  }
}
```

## Streamable HTTP Transport (n8n)

The recommended HTTP transport is the FastMCP server in `fastmcp/`. It exposes a single `/mcp` endpoint for GET and POST.

Environment variables:
- `DISCORD_TOKEN`: Discord bot token (not a user token; required unless using request headers)
- `DISCORD_GUILD_ID`: Default guild/server ID (required unless using request headers)
- `DISCORD_PRIMARY_CHANNEL_ID`: Default channel ID for send/read tools (optional)
- `DISCORD_ALLOWED_CHANNEL_IDS`: Comma-separated allowlist for send/edit/delete (optional, use `ALL` or `*` to allow all channels; defaults to allowing all)
- `DISCORD_BLOCKED_CHANNEL_IDS`: Comma-separated blocklist to disallow reads/writes (optional)
- `DISCORD_ALLOW_ALL_READ`: Allow reads across all channels (optional)
- `MCP_ADMIN_TOOLS_ENABLED`: Enable admin-gated edit/delete (requires `confirm="CONFIRM APPLY"`)
- `DISCORD_DM_ENABLED`: Enable DM tools (optional, default false)
- `LOG_REDACT_MESSAGE_CONTENT`: Redact message content in logs and job results (optional, default true)
- `DISCORD_AUDIT_TIMEZONE`: Timezone for audit tools (optional, default `America/Los_Angeles`)
- `DISCORD_PROTECTED_USER_IDS`: Comma-separated user IDs protected from moderation (optional)
- `DISCORD_PROTECTED_ROLE_IDS`: Comma-separated role IDs protected from moderation (optional)
- `DISCORD_ALLOWED_TARGET_ROLE_IDS`: Restrict moderation to members with these roles (optional)
- `DISCORD_CHANNEL_CACHE_TTL_SECONDS`: Channel name cache TTL in seconds (optional, default 600)
- `DISCORD_JOB_TTL_SECONDS`: Async job retention TTL in seconds (optional, default 3600)
- `MCP_ALLOW_REQUEST_OVERRIDES`: Enable per-request headers for public endpoints (optional)
- `MCP_REQUIRE_REQUEST_DISCORD_TOKEN`: Require bot token header (optional, default follows `MCP_ALLOW_REQUEST_OVERRIDES`)
- `MCP_REQUIRE_REQUEST_GUILD_ID`: Require guild id header (optional, default follows `MCP_ALLOW_REQUEST_OVERRIDES`)
- `MCP_REQUIRE_REQUEST_BLOCKED_CHANNELS`: Require blocked channels header (optional, default follows `MCP_ALLOW_REQUEST_OVERRIDES`)
- `MCP_DISCORD_TOKEN_HEADER`: Header name for bot token (default `x-discord-bot-token`)
- `MCP_DISCORD_GUILD_ID_HEADER`: Header name for guild id (default `x-discord-guild-id`)
- `MCP_DISCORD_BLOCKED_CHANNELS_HEADER`: Header name for blocked channels (default `x-discord-blocked-channels`)
- `MCP_BOT_POOL_TTL_SECONDS`: Idle TTL for bot clients in the pool (optional, default 900)

Docker Compose (recommended):
```bash
cd fastmcp
cp .env.example .env
# Edit .env with your DISCORD_TOKEN and DISCORD_GUILD_ID (or enable request headers)
docker-compose up -d --build
```

Endpoints:
- `GET /mcp` -> SSE stream for server-initiated notifications
- `POST /mcp` -> JSON-RPC requests (returns JSON or SSE per request)

Example curl flow:
```bash
# 1) Initialize session
curl -i -X POST http://localhost:8085/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# 2) List tools
curl -i -X POST http://localhost:8085/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

### Hosted MCP (Bring Your Own Discord Bot)

If you expose FastMCP publicly, you can require clients to supply **their own bot
token and guild id** via headers. This lets anyone use the endpoint with their
own Discord credentials.

Server env (recommended for public endpoints):
```bash
MCP_ALLOW_REQUEST_OVERRIDES=true
MCP_REQUIRE_REQUEST_DISCORD_TOKEN=true
MCP_REQUIRE_REQUEST_GUILD_ID=true
MCP_REQUIRE_REQUEST_BLOCKED_CHANNELS=true
```

Client headers:
- `X-Discord-Bot-Token`: **Discord bot token** (required, not a user token)
- `X-Discord-Guild-Id`: guild id (required)
- `X-Discord-Blocked-Channels`: blocked channel names (required, may be empty)

Blocked channel format: `#channel, #channel` (spaces optional). If a channel
name does not match, the request still succeeds and a warning is returned.

If required headers are missing, the server returns a JSON-RPC error with
`type=permission_denied` and `diagnostics.required_headers` listing the
missing header names.

Example (curl):
```bash
curl -i -X POST http://localhost:8085/mcp \
  -H "Content-Type: application/json" \
  -H "X-Discord-Bot-Token: <BOT_TOKEN>" \
  -H "X-Discord-Guild-Id: <GUILD_ID>" \
  -H "X-Discord-Blocked-Channels: #announcements, #general-conversation" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"send_message","arguments":{"channel_id":"123","message":"Hello","confirm":"CONFIRM APPLY"}}}'
```

Note: header auth only applies to HTTP transport; STDIO cannot pass headers.

### VPS Deployment (Nginx Proxy Manager)

Attach the FastMCP container to the same Docker network as Nginx Proxy Manager (usually `npm_default`).

NPM host settings:
- Forward Hostname/IP: `discord-mcp`
- Forward Port: `8085`
- Websockets: ON
- HTTP/2: OFF
- Advanced: empty

Then point n8n to:
- Endpoint: `https://discord-mcp.yourdomain.com/mcp`
- Transport: HTTP Streamable

Hostinger VPS (affiliate links):
- KVM 1: https://www.hostinger.com/cart?product=vps%3Avps_kvm_1&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=019b3c9f-6443-7070-b054-57dd34af9aba
- KVM 2: https://www.hostinger.com/cart?product=vps%3Avps_kvm_2&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=019b3c9f-b07b-71be-bd64-00f81c1bdb82
- KVM 4: https://www.hostinger.com/cart?product=vps%3Avps_kvm_4&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=019b3c9f-d6e0-7180-b425-9c286e86986e
- KVM 8: https://www.hostinger.com/cart?product=vps%3Avps_kvm_8&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=019b3ca0-15e3-710a-a3c3-51c7d2921f86

<details>
    <summary style="font-size: 1.35em; font-weight: bold;">
        🔧 Manual Installation
    </summary>

#### Clone the repository
```bash
git clone https://github.com/SaseQ/discord-mcp
```

#### Build the project
> NOTE: Maven installation is required to use the mvn command. Full instructions can be found [here](https://www.baeldung.com/install-maven-on-windows-linux-mac).
```bash
cd discord-mcp
mvn clean package # The jar file will be available in the /target directory
```

#### Configure AI client
Many code editors and other AI clients use a configuration file to manage MCP servers.

The Discord MPC server can be configured by adding the following to your configuration file.

> NOTE: You will need to create a Discord Bot token to use this server. Instructions on how to create a Discord Bot token can be found [here](https://discordjs.guide/preparations/setting-up-a-bot-application.html#creating-your-bot).
```json
{
  "mcpServers": {
    "discord-mcp": {
      "command": "java",
      "args": [
        "-jar",
        "/absolute/path/to/discord-mcp-0.0.1-SNAPSHOT.jar"
      ],
      "env": {
        "DISCORD_TOKEN": "YOUR_DISCORD_BOT_TOKEN",
        "DISCORD_GUILD_ID": "OPTIONAL_DEFAULT_SERVER_ID"
      }
    }
  }
}
```
The `DISCORD_GUILD_ID` environment variable is optional. When provided, it sets a default Discord server ID so any tool that accepts a `guildId` parameter can omit it.

</details>

<details>
    <summary style="font-size: 1.35em; font-weight: bold;">
        ⚓ Smithery Installation
    </summary>

Install Discord MCP Server automatically via [Smithery](https://smithery.ai/):
```bash
npx -y @smithery/cli@latest install @SaseQ/discord-mcp --client <CLIENT_NAME> --key <YOUR_SMITHERY_KEY>
```

</details>

<details>
    <summary style="font-size: 1.35em; font-weight: bold;">
        🖲 Cursor Installation
    </summary>

Go to: `Settings` -> `Cursor Settings` -> `MCP` -> `Add new global MCP server`

Pasting the following configuration into your Cursor `~/.cursor/mcp.json` file is the recommended approach. You may also install in a specific project by creating `.cursor/mcp.json` in your project folder. See [Cursor MCP docs](https://docs.cursor.com/context/model-context-protocol) for more info.
```json
{
  "mcpServers": {
    "mcp-server": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "DISCORD_TOKEN=<YOUR_DISCORD_BOT_TOKEN>",
        "-e", "DISCORD_GUILD_ID=<OPTIONAL_DEFAULT_SERVER_ID>",
        "saseq/discord-mcp:latest"
      ]
    }
  }
}
```

</details>

<details>
    <summary style="font-size: 1.35em; font-weight: bold;">
        ⌨️ Claude Code Installation
    </summary>

Run this command. See [Claude Code MCP docs](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/tutorials#set-up-model-context-protocol-mcp) for more info.
```bash
claude mcp add mcp-server -- docker run --rm -i -e DISCORD_TOKEN=<YOUR_DISCORD_BOT_TOKEN> -e DISCORD_GUILD_ID=<OPTIONAL_DEFAULT_SERVER_ID> saseq/discord-mcp:latest
```

</details>

## 🛠️ Available Tools

#### Server Information
 - [`get_server_info`](): Get detailed discord server information
 - [`discord_health_check`](): High-signal health report with status, warnings, permissions, and rate-limit snapshot
 - [`discord_smoke_test`](): Run health check + dry-run + send + optional edit/delete + read-back

#### Operations
 - [`discord_ack`](): Post a standardized acknowledgement message to a channel/thread
 - [`discord_job_submit`](): Submit an async job (returns `task_id`)
 - [`discord_job_status`](): Check async job status and optional result

#### User Management
- [`get_user_id_by_name`](): Get a Discord user's ID by username in a guild for ping usage `<@id>`
- [`send_private_message`](): Send a private message to a specific user
- [`edit_private_message`](): Edit a private message from a specific user
- [`delete_private_message`](): Delete a private message from a specific user
- [`read_private_messages`](): Read recent message history from a specific user

#### Moderation (confirm-gated)
- [`timeout_member`](): Timeout a member for a duration (minutes)
- [`remove_timeout`](): Remove a timeout from a member
- [`kick_member`](): Kick a member from the guild
- [`ban_member`](): Ban a member (optional delete message days)
- [`unban_member`](): Unban a user
- [`add_role`](): Add a role to a member
- [`remove_role`](): Remove a role from a member
- [`edit_nickname`](): Set or clear a member nickname

#### Message Management
 - [`send_message`](): Send a message to a specific channel (supports `dry_run`, optional embeds, auto-splitting, and `thread_if_split`)
 - [`edit_message`](): Edit a message (requires `MCP_ADMIN_TOOLS_ENABLED=true` and `confirm="CONFIRM APPLY"`)
 - [`delete_message`](): Delete a message (requires `MCP_ADMIN_TOOLS_ENABLED=true` and `confirm="CONFIRM APPLY"`)
 - [`read_messages`](): Read recent message history (supports `before_message_id`)
 - [`search_messages`](): Search messages with filters (limit, author, date range, threads, links/files)
 - [`add_reaction`](): Add a reaction (emoji) to a specific message
 - [`remove_reaction`](): Remove a specified reaction (emoji) from a message

#### Thread Management
 - [`list_threads`](): List active (and optionally archived) threads for a channel
 - [`create_thread`](): Create a thread from a message
 - [`archive_thread`](): Archive a thread
 - [`unarchive_thread`](): Unarchive a thread

#### Audits
 - [`channel_daily_audit`](): Summarize a single channel for a day
 - [`daily_audit_job_submit`](): Create a sequential audit job over channels
 - [`daily_audit_job_status`](): Check audit job status
 - [`daily_audit_job_next`](): Process the next channel in the audit job

#### Channel Management
 - [`create_text_channel`](): Create text a channel
 - [`delete_channel`](): Delete a channel
 - [`find_channel`](): Find a channel type and ID using name (supports emoji/prefix-insensitive matches)
 - [`list_channels`](): List of all channels

#### Category Management
 - [`create_category`](): Create a new category for channels
 - [`delete_category`](): Delete a category
 - [`find_category`](): Find a category ID using name and server ID
 - [`list_channels_in_category`](): List of channels in a specific category

#### Webhook Management
 - [`create_webhook`](): Create a new webhook on a specific channel
 - [`delete_webhook`](): Delete a webhook
 - [`list_webhooks`](): List of webhooks on a specific channel
 - [`send_webhook_message`](): Send a message via webhook

>If `DISCORD_GUILD_ID` is set (or `X-Discord-Guild-Id` is provided), the `guildId` parameter becomes optional for all tools above.
>If `DISCORD_PRIMARY_CHANNEL_ID` is set, `channel_id` becomes optional for send/read tools. By default all channels are writable; use `DISCORD_BLOCKED_CHANNEL_IDS` or `X-Discord-Blocked-Channels` to block specific channels, or set `DISCORD_ALLOWED_CHANNEL_IDS` to enforce an allowlist.
>All write/noisy tools (including moderation actions) require `confirm="CONFIRM APPLY"`.

#### Examples (FastMCP JSON-RPC)

Timeout a member:
```json
{"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":"timeout_member","arguments":{"user_id":"123456789012345678","duration_minutes":"30","reason":"Spam","confirm":"CONFIRM APPLY"}}}
```

Ban a member (delete last 1 day of messages):
```json
{"jsonrpc":"2.0","id":11,"method":"tools/call","params":{"name":"ban_member","arguments":{"user_id":"123456789012345678","delete_message_days":"1","reason":"Raid","confirm":"CONFIRM APPLY"}}}
```

Add a role:
```json
{"jsonrpc":"2.0","id":12,"method":"tools/call","params":{"name":"add_role","arguments":{"user_id":"123456789012345678","role_id":"987654321098765432","reason":"Verified","confirm":"CONFIRM APPLY"}}}
```

Edit nickname (clear by sending empty string):
```json
{"jsonrpc":"2.0","id":13,"method":"tools/call","params":{"name":"edit_nickname","arguments":{"user_id":"123456789012345678","nickname":"","reason":"Reset","confirm":"CONFIRM APPLY"}}}
```

Daily audit job flow (one channel per step):
```json
{"jsonrpc":"2.0","id":20,"method":"tools/call","params":{"name":"daily_audit_job_submit","arguments":{"date":"2026-01-12","channel_ids":["1455591724532629627","1411052130709667850"]}}}
{"jsonrpc":"2.0","id":21,"method":"tools/call","params":{"name":"daily_audit_job_next","arguments":{"task_id":"<task_id_from_submit>","limit":"50"}}}
{"jsonrpc":"2.0","id":22,"method":"tools/call","params":{"name":"daily_audit_job_status","arguments":{"task_id":"<task_id_from_submit>","include_results":true}}}
```

<hr>

A more detailed examples can be found in the [Wiki](https://github.com/SaseQ/discord-mcp/wiki).
