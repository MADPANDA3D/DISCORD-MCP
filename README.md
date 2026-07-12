<p align="center">
  <img src="./assets/brand/header.jpg" alt="MADPANDA3D Discord MCP header" />
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="MIT License" /></a>
  <a href="https://www.java.com/"><img src="https://img.shields.io/badge/Java-17-007396?logo=java&logoColor=white" alt="Java 17" /></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+" /></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/MCP-Server-000000" alt="MCP Server" /></a>
  <a href="https://discord.com/developers/docs/intro"><img src="https://img.shields.io/badge/Discord-API-5865F2?logo=discord&logoColor=white" alt="Discord API" /></a>
  <a href="https://github.com/MADPANDA3D/DISCORD-MCP/releases"><img src="https://img.shields.io/github/v/release/MADPANDA3D/DISCORD-MCP?display_name=tag&color=0e8a16" alt="release" /></a>
  <a href="https://github.com/MADPANDA3D/DISCORD-MCP/issues"><img src="https://img.shields.io/github/issues/MADPANDA3D/DISCORD-MCP?color=ff8c00" alt="open issues" /></a>
  <a href="https://github.com/MADPANDA3D/DISCORD-MCP"><img src="https://img.shields.io/github/stars/MADPANDA3D/DISCORD-MCP?color=f1c40f" alt="stars" /></a>
</p>

<h1 align="center"><strong>MADPANDA3D DISCORD MCP</strong></h1>
<p align="center"><strong>Operate Discord with MCP-native tools</strong></p>
<p align="center">
  MADPANDA3D Discord MCP is a production-ready Model Context Protocol server for the Discord API.
  It blends a Spring Boot JDA core with a FastMCP HTTP transport so agents can automate Discord
  safely and at scale.
</p>
<p align="center">
  Manage channels, moderate users, schedule audits, and run async jobs without leaving your
  MCP-compatible client. Built for clean ops, consistent controls, and predictable automation.
</p>

## Overview

This server supports both STDIO and HTTP streamable transports. The hosted provider endpoint is
Portal-only: MAD MCP Portal authenticates the client, injects its private service grant, and
forwards the client's Discord bot credentials. Self-hosted/private mode remains available for
local and STDIO clients.

## Hosted MCP (Header Auth)

Connect clients to MAD MCP Portal:

```
https://madpanda3d.com/lab/mad-mcps/portal/api/mcp
```

The Discord provider endpoint is not a public client endpoint. Its
`X-MADPANDA-PORTAL-GRANT` value is an internal service credential and must never be copied into
client configuration.

n8n setup through Portal:

1. Add **MCP tool node** to your agent.
2. Add the MAD MCP Portal endpoint URL.
3. Set **Server transport** to **HTTP streamable**.
4. Configure the Portal-issued client authentication.
5. Store the Discord bot token and guild ID in the client's Discord service profile.
6. Save the Portal credentials.
7. Set **Tools to include** -> **All**.

## n8n Setup

Screenshots below show the MCP node configuration in n8n (steps 1-3).

<p align="center">
  <img src="./assets/n8n/n8n-qdrant-mcp-setup-step1.jpg" alt="n8n MCP setup step 1" width="900" />
</p>
<p align="center">
  <img src="./assets/n8n/n8n-qdrant-mcp-setup-step2.jpg" alt="n8n MCP setup step 2" width="900" />
</p>
<p align="center">
  <img src="./assets/n8n/n8n-discord-mcp-setup-step3.jpg" alt="n8n MCP setup step 3" width="900" />
</p>

## Deploy

- [![Deploy to VPS](https://img.shields.io/badge/Deploy_to_VPS-Hostinger-blue?style=for-the-badge&logo=linux&logoColor=white)](https://www.hostinger.com/cart?product=vps%3Avps_kvm_4&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a492-531e-70d3-83f5-e28eb919466d)

<details>
<summary>VPS Deployment (Nginx Proxy Manager)</summary>

Attach the FastMCP container to the same Docker network as Nginx Proxy Manager (usually `npm_default`).

NPM host settings:
- Forward Hostname/IP: `discord-mcp`
- Forward Port: `8085`
- Websockets: ON
- HTTP/2: OFF
- Advanced: empty

Keep the provider hostname private to MAD MCP Portal. Point n8n and other agents to the Portal
endpoint instead; Portal supplies the service grant after client authentication.

</details>

## Quickstart

<details>
<summary>FastMCP (Docker Compose)</summary>

```bash
cd fastmcp
cp .env.example .env
# Hosted: set MCP_PORTAL_GRANT_TOKEN; private: set MCP_PUBLIC_MODE=false plus Discord defaults.
docker compose -f fastmcp/docker-compose.yaml up -d --build
```

</details>

<details>
<summary>FastMCP (Python)</summary>

```bash
pip install -r fastmcp/requirements.txt
python fastmcp/discord_mcp_server.py
```

</details>

<details>
<summary>Java (Spring Boot)</summary>

```bash
mvn clean package
java -jar target/discord-mcp-*.jar
```

</details>

## HTTP Transport

The FastMCP server in `fastmcp/` exposes a single `/mcp` endpoint for GET and POST.

Endpoints:
- `GET /mcp` -> SSE stream for server-initiated notifications
- `POST /mcp` -> JSON-RPC requests (returns JSON or SSE per request)

<details>
<summary>Example private-mode curl flow</summary>

```bash
# Run with MCP_PUBLIC_MODE=false for direct self-hosted access.
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

</details>

## Tools

The FastMCP runtime publishes 52 native tools: 46 `agent_ready`, 3 `legacy`, and
3 `hidden` from default discovery. Every tool is described by the provider-owned,
deterministically hashed ToolManifest exposed through
`list_capabilities(include_descriptors=true)`. The manifest includes complete input
and output schemas, risk annotations, confirmation rules, aliases, tier, catalog
version, build SHA, and descriptor hashes without runtime credential values.
The current immutable catalog version is `discord-2026.07.12.2` using shared manifest
schema `1.0.0`; every descriptor identifies itself as
`discord.<nativeToolName>` through `canonicalName`.

See [Discord API endpoint coverage](docs/endpoint-coverage.md) for implemented and
intentionally excluded resource areas.

<details>
<summary>Agent navigation</summary>

- `check_configuration`: report safe setup presence and policy flags without contacting Discord
- `list_capabilities`: list counts/categories or return the lossless provider manifest
- `get_endpoint_coverage`: inspect official Discord resource coverage and explicit gaps
- `get_tool_usage`: return one lossless tool descriptor by native name or alias
- `find_tools`: punctuation-normalized, multi-token ranked discovery with category/risk filters

</details>

<details>
<summary>Server Information</summary>

- `get_server_info`: detailed server metadata and permissions
- `discord_health_check`: status, warnings, permissions, and rate-limit snapshot
- `discord_smoke_test`: health check + dry-run + send + optional edit/delete + read-back

</details>

<details>
<summary>Operations</summary>

- `discord_ack`: post standardized acknowledgement messages
- `discord_job_submit`: submit an async job (returns `task_id`)
- `discord_job_status`: check async job status and optional result

</details>

<details>
<summary>User Management</summary>

- `get_user_id_by_name`: resolve a Discord user ID for pings
- `send_private_message`: send a DM
- `edit_private_message`: edit a DM
- `delete_private_message`: delete a DM
- `read_private_messages`: read recent DM history

</details>

<details>
<summary>Moderation (confirm-gated)</summary>

- `timeout_member`
- `remove_timeout`
- `kick_member`
- `ban_member`
- `unban_member`
- `add_role`
- `remove_role`
- `edit_nickname`

</details>

<details>
<summary>Message Management</summary>

- `send_message`: send messages (supports `dry_run`, embeds, one attachment, auto-splitting)
- `edit_message`: edit messages (admin + confirm)
- `delete_message`: delete messages (admin + confirm)
- `read_messages`: read recent history
- `search_messages`: search with filters
- `analyze_attachment`: OCR or describe image attachments (OpenAI vision)
- `add_reaction`: add a reaction
- `remove_reaction`: remove a reaction

</details>

### `send_message` Attachments

`send_message` accepts one optional attachment through `file_base64`, `file_url`, `file_path`, or a `file`/`attachment` object with `base64`, `url`, or `path`.

Local `file_path` reads are disabled unless `MCP_ATTACHMENT_ALLOWED_DIRS` is set to a comma-separated allowlist of directories available inside the FastMCP container. Use `file_base64` or `file_url` for hosted clients that cannot mount local files into the service.

<details>
<summary>Thread Management</summary>

- `list_threads`
- `create_thread`
- `archive_thread`
- `unarchive_thread`

</details>

<details>
<summary>Audits</summary>

- `channel_daily_audit`
- `daily_audit_job_submit`
- `daily_audit_job_status`
- `daily_audit_job_next`

</details>

<details>
<summary>Channel Management</summary>

- `create_text_channel`
- `delete_channel`
- `find_channel`
- `list_channels`

</details>

<details>
<summary>Category Management</summary>

- `create_category`
- `delete_category`
- `find_category`
- `list_channels_in_category`

</details>

<details>
<summary>Webhook Management</summary>

- `create_webhook`
- `delete_webhook`
- `list_webhooks`
- `send_webhook_message`

</details>

## Configuration

<details>
<summary>Environment Variables</summary>

| Name                            | Description                                                                 | Default Value                     |
|---------------------------------|-----------------------------------------------------------------------------|-----------------------------------|
| `DISCORD_TOKEN`                 | Discord bot token (required unless using request headers)                  | None                              |
| `DISCORD_GUILD_ID`              | Default guild/server ID (required unless using request headers)            | None                              |
| `DISCORD_PRIMARY_CHANNEL_ID`    | Default channel ID for send/read tools                                     | unset                             |
| `DISCORD_ALLOWED_CHANNEL_IDS`   | Comma-separated allowlist for send/edit/delete (`ALL` or `*` to allow all) | unset                             |
| `DISCORD_BLOCKED_CHANNEL_IDS`   | Comma-separated blocklist for reads/writes                                 | unset                             |
| `DISCORD_ALLOW_ALL_READ`        | Allow reads across all channels                                            | `false`                           |
| `DISCORD_DM_ENABLED`            | Enable DM tools                                                            | `false`                           |
| `DISCORD_AUDIT_TIMEZONE`        | Timezone for audit tools                                                   | `America/Los_Angeles`             |
| `DISCORD_PROTECTED_USER_IDS`    | Comma-separated user IDs protected from moderation                         | unset                             |
| `DISCORD_PROTECTED_ROLE_IDS`    | Comma-separated role IDs protected from moderation                         | unset                             |
| `DISCORD_ALLOWED_TARGET_ROLE_IDS` | Restrict moderation to members with these roles                         | unset                             |
| `DISCORD_CHANNEL_CACHE_TTL_SECONDS` | Channel name cache TTL in seconds                                     | `600`                             |
| `DISCORD_JOB_TTL_SECONDS`       | Async job retention TTL in seconds                                         | `3600`                            |
| `MCP_BOT_POOL_TTL_SECONDS`      | Idle TTL for bot clients in the pool                                       | `900`                             |
| `LOG_LEVEL`                     | Log level                                                                  | `INFO`                            |
| `LOG_REDACT_MESSAGE_CONTENT`    | Redact message content in logs/results                                     | `true`                            |
| `MCP_ADMIN_TOOLS_ENABLED`       | Enable admin-gated edit/delete tools                                       | `false`                           |
| `MCP_REQUIRE_CONFIRM`           | Require `confirm="CONFIRM APPLY"` for confirm-gated tools                  | `false`                           |
| `MCP_HTTP_PORT`                 | HTTP server port                                                          | `8085`                            |
| `MCP_BIND_ADDRESS`              | HTTP bind address                                                         | `0.0.0.0`                         |
| `MCP_TRANSPORT`                 | Transport type                                                             | `streamable-http`                 |
| `MCP_STDIO`                     | Enable STDIO transport                                                     | `false`                           |
| `MCP_PUBLIC_MODE`               | Require the Portal grant and per-request Discord credentials on HTTP       | `false`                           |
| `MCP_BUILD_SHA`                 | Deployed Git commit SHA reported by health and the ToolManifest             | `unknown`                         |
| `MCP_PORTAL_GRANT_TOKEN`        | Private shared grant required from MAD MCP Portal in public mode            | unset                             |
| `MCP_PORTAL_GRANT_HEADER`       | Header carrying the Portal service grant                                   | `X-MADPANDA-PORTAL-GRANT`         |
| `MCP_ALLOW_REQUEST_OVERRIDES`   | Enable per-request headers; always enabled in public mode                   | `false`                           |
| `OPENAI_VISION_ENABLED`         | Enable OpenAI vision (attachment OCR/describe)                             | `false`                           |
| `OPENAI_VISION_MODEL`           | OpenAI model for vision                                                    | `gpt-4o-mini`                     |
| `OPENAI_VISION_API_URL`         | OpenAI API URL for vision                                                  | `https://api.openai.com/v1/chat/completions` |
| `OPENAI_VISION_MAX_MB`          | Max attachment size for vision (MB)                                        | `10`                              |
| `OPENAI_VISION_TIMEOUT_SECONDS` | OpenAI request timeout (seconds)                                           | `30`                              |
| `MCP_OPENAI_API_HEADER`         | Header name for OpenAI API key                                             | `x-openai-api`                    |
| `MCP_REQUIRE_REQUEST_DISCORD_TOKEN` | Require bot token header; always required in public mode               | `false`                           |
| `MCP_REQUIRE_REQUEST_GUILD_ID`  | Require guild id header; always required in public mode                    | `false`                           |
| `MCP_REQUIRE_REQUEST_BLOCKED_CHANNELS` | Require blocked channels header (if overrides enabled)              | `false`                           |
| `MCP_DISCORD_TOKEN_HEADER`      | Header name for bot token                                                  | `x-discord-bot-token`             |
| `MCP_DISCORD_GUILD_ID_HEADER`   | Header name for guild id                                                   | `x-discord-guild-id`              |
| `MCP_DISCORD_BLOCKED_CHANNELS_HEADER` | Header name for blocked channels                                    | `x-discord-blocked-channels`      |
| `MCP_DISCORD_ALLOW_ALL_READ_HEADER` | Header name to override `DISCORD_ALLOW_ALL_READ`                        | `x-discord-allow-all-read`        |
| `MCP_DISCORD_DM_ENABLED_HEADER` | Header name to override `DISCORD_DM_ENABLED`                              | `x-discord-dm-enabled`            |
| `MCP_ADMIN_TOOLS_ENABLED_HEADER` | Header name to override `MCP_ADMIN_TOOLS_ENABLED`                      | `x-mcp-admin-tools-enabled`       |
| `MCP_REQUIRE_CONFIRM_HEADER` | Header name to override `MCP_REQUIRE_CONFIRM`                              | `x-mcp-require-confirm`           |

Note: header auth only applies to HTTP transport; STDIO cannot pass headers.
Confirm gating is controlled by `MCP_REQUIRE_CONFIRM` (set `true` to enforce `confirm="CONFIRM APPLY"`).

</details>

### Hosted MCP (Portal-Brokered Discord Credentials)

The production Compose file enables public mode. It fails closed unless the Portal service grant
is configured, requires Discord credentials on each tool call, and never falls back to
`DISCORD_TOKEN` or `DISCORD_GUILD_ID` from the server environment.

Server env (required for public endpoints):

```bash
MCP_PUBLIC_MODE=true
MCP_PORTAL_GRANT_TOKEN=<private service grant>
MCP_PORTAL_GRANT_HEADER=X-MADPANDA-PORTAL-GRANT
MCP_ALLOW_REQUEST_OVERRIDES=true
MCP_REQUIRE_REQUEST_DISCORD_TOKEN=true
MCP_REQUIRE_REQUEST_GUILD_ID=true
MCP_REQUIRE_REQUEST_BLOCKED_CHANNELS=true
```

Headers forwarded by MAD MCP Portal after it authenticates the client:
- `X-MADPANDA-PORTAL-GRANT`: internal broker grant (never supplied by end users)
- `X-OpenAI-Api`: OpenAI API key (optional; required for `analyze_attachment`)
- `X-Discord-Bot-Token`: Discord bot token (required, not a user token)
- `X-Discord-Guild-Id`: guild id (required)
- `X-Discord-Blocked-Channels`: blocked channel names (required, may be empty)
- `X-Discord-Allow-All-Read`: optional override (`true`/`false`)
- `X-Discord-Dm-Enabled`: optional override (`true`/`false`)
- `X-Mcp-Admin-Tools-Enabled`: optional override (`true`/`false`)
- `X-Mcp-Require-Confirm`: optional override (`true`/`false`)

Blocked channel format: `#channel, #channel` (spaces optional). If a channel
name does not match, the request still succeeds and a warning is returned.

If required headers are missing, the server returns a JSON-RPC error with
`type=permission_denied` and `diagnostics.required_headers` listing the
missing header names.

Missing or invalid Portal grants are rejected before the MCP request body is parsed or any
Discord provider handling occurs. The unauthenticated `/health` endpoint reports only safe
configuration booleans; it never exposes credentials.

For direct self-hosted or STDIO use, set `MCP_PUBLIC_MODE=false`. Private mode preserves the
existing `DISCORD_TOKEN` and `DISCORD_GUILD_ID` fallback behavior.

Optional OpenAI vision (for `analyze_attachment`):

Server env:

```bash
OPENAI_VISION_ENABLED=true
MCP_OPENAI_API_HEADER=x-openai-api
```

Client header:
- `X-OpenAI-Api`: OpenAI API key (required for vision; omitted headers return permission_denied)

Example OCR call (include `X-OpenAI-Api` header in the request):

```json
{"jsonrpc":"2.0","id":30,"method":"tools/call","params":{"name":"analyze_attachment","arguments":{"channel_id":"123456789012345678","message_id":"123456789012345679","attachment_index":"0","mode":"ocr"}}}
```

## Examples (FastMCP JSON-RPC)

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

## License

MIT.

## Support

[![Donate to the Project](https://img.shields.io/badge/Donate_to_the_Project-Support_Development-ff69b4?style=for-the-badge&logo=heart&logoColor=white)](https://donate.stripe.com/cNidRbdkAbdP8iU7SD4ko0b)

## Affiliate Links

<details>
<summary>Services I use (affiliate)</summary>

Using these links helps support continued development.

### Hostinger VPS
- [KVM 1](https://www.hostinger.com/cart?product=vps%3Avps_kvm_1&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a491-d783-7057-85d2-27de6e01e2c5)
- [KVM 2](https://www.hostinger.com/cart?product=vps%3Avps_kvm_2&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a492-26cf-7333-b6d7-692e17bf8ce1)
- [KVM 4](https://www.hostinger.com/cart?product=vps%3Avps_kvm_4&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a492-531e-70d3-83f5-e28eb919466d)
- [KVM 8](https://www.hostinger.com/cart?product=vps%3Avps_kvm_8&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a492-7ce9-70fb-b96c-2184abc56764)

### Cloud Hosting
- [Cloud Economy](https://www.hostinger.com/cart?product=hosting%3Acloud_economy&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a48f-e7fa-7358-9ff0-f9ba2e8d6e36)
- [Cloud Professional](https://www.hostinger.com/cart?product=hosting%3Acloud_professional&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a490-20fd-70bc-959e-a1f2cd9a69a6)
- [Cloud Enterprise](https://www.hostinger.com/cart?product=hosting%3Acloud_enterprise&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a490-5972-72e4-850f-40d618988dc1)

### Web Hosting
- [Premium](https://www.hostinger.com/cart?product=hosting%3Ahostinger_premium&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a48f-4c21-7199-9918-8f31a3f6a0d9)
- [Business](https://www.hostinger.com/cart?product=hosting%3Ahostinger_business&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a48f-1135-72ba-acbb-13e0e7550db0)

### Website Builder
- [Premium](https://www.hostinger.com/cart?product=hosting%3Ahostinger_premium&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a492-f240-7309-b3fe-9f6909fbc769&product_type=website-builder)
- [Business](https://www.hostinger.com/cart?product=hosting%3Ahostinger_business&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a492-7ce9-70fb-b96c-2184abc56764)

### Agency Hosting
- [Startup](https://www.hostinger.com/cart?product=hosting%3Aagency_startup&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a490-d03c-71de-9acf-08fd4fa911de)
- [Growth](https://www.hostinger.com/cart?product=hosting%3Aagency_growth&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a491-6af4-731f-8947-f1458f07fa5b)
- [Professional](https://www.hostinger.com/cart?product=hosting%3Aagency_professional&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a491-03fb-73f8-9910-044a0a33393a)

### Email
- [Business Pro](https://www.hostinger.com/cart?product=hostinger_mail%3Apro&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a493-5c27-727b-b7f9-8747ffb4e5ee)
- [Business Premium](https://www.hostinger.com/cart?product=hostinger_mail%3Apremium&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a493-a3fc-72b8-a961-94ed6e1c70e6)

### Reach
- [Reach 500](https://www.hostinger.com/cart?product=reach%3A500&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a494-3ebf-7367-b409-9948de50a297)
- [Reach 1000](https://www.hostinger.com/cart?product=reach%3A1000&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a494-8bb9-726e-bb8d-9de9a72a3c21)
- [Reach 2500](https://www.hostinger.com/cart?product=reach%3A2500&period=12&referral_type=cart_link&REFERRALCODE=ZUWMADPANOFE&referral_id=0199a494-c9c1-7191-b600-cafa2e9adafc)

</details>

## Contact

Open an issue in `MADPANDA3D/DISCORD-MCP`.

<p align="center">
  <img src="https://assets.zyrosite.com/cdn-cgi/image/format=auto,w=316,fit=crop,q=95/dJo56xnDoJCnbgxg/official-logo-mxBMZGQ8Owc8p2M2.jpeg" width="160" alt="MADPANDA3D logo" />
  <br />
  <strong>MADPANDA3D</strong>
</p>
