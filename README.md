<p align="center">
  <img src="assets/brand/header.jpg" alt="MADPANDA3D Discord MCP" width="100%">
</p>

<h1 align="center">Discord MCP</h1>

<p align="center">
  An authenticated, policy-bounded Discord control plane for agents.<br>
  Fifty-five deterministic tools. Two service modes. Zero unauthenticated paths to Discord.
</p>

<p align="center">
  <a href="https://github.com/MADPANDA3D/DISCORD-MCP/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/MADPANDA3D/DISCORD-MCP/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/MADPANDA3D/DISCORD-MCP/actions/workflows/codeql.yml"><img alt="CodeQL" src="https://github.com/MADPANDA3D/DISCORD-MCP/actions/workflows/codeql.yml/badge.svg"></a>
  <img alt="Python 3.12 and 3.13" src="https://img.shields.io/badge/python-3.12%20%7C%203.13-3776AB?logo=python&logoColor=white">
  <img alt="MCP Streamable HTTP" src="https://img.shields.io/badge/MCP-Streamable%20HTTP-5865F2">
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-22c55e"></a>
</p>

<pre align="center">
┌────────────────────────────────────────────────────────────────────────────┐
│  DISCOVER  ──▶  SCOPE  ──▶  READ  ──▶  CONFIRM  ──▶  MUTATE  ──▶  VERIFY │
│      55          tenant       safe       exact          policy       typed  │
│     tools         bounds     defaults    phrase         gates        result │
└────────────────────────────────────────────────────────────────────────────┘
</pre>

## What this server does

Discord MCP gives MCP-compatible agents a controlled interface for guild metadata, channels,
categories, messages, attachments, threads, reactions, moderation, roles, direct messages,
audits, webhooks, and deterministic tool discovery.

It is designed around four boundaries:

- **Service authentication** protects `/mcp` before request bodies are parsed.
- **Provider credentials** are either server-owned or request-scoped BYOK.
- **Policy ceilings** bound guild, channel, DM, admin, attachment, and confirmation behavior.
- **Typed discovery** keeps the runtime registry, schemas, catalog, and documentation aligned.

This is not an unauthenticated proxy or an arbitrary Discord API client. It does not expose raw
Gateway access, OAuth installation, application-command management, or webhook credentials. Its
stable server-management REST surface is exposed through an explicit action allowlist; three
legacy webhook tools remain hidden from agent discovery.

## Access modes

Service credentials and Discord credentials are deliberately separate.

| Service mode | Provider mode | Service credential | Discord credential |
|---|---|---|---|
| Standalone | `server` | `Authorization: Bearer <MCP_ACCESS_TOKEN>` | Ignored `.env` values |
| Standalone BYOK | `request` | Bearer token | Request headers |
| Portal BYOK | `request` required | `X-MADPANDA-PORTAL-GRANT` | Broker-injected request headers |

There is no unauthenticated mode. Tokens must be at least 32 characters, and Portal mode fails
closed unless request-scoped Discord credentials are selected.

For standalone BYOK, set `DISCORD_CREDENTIAL_MODE=request` and configure a nonempty server channel
ceiling in `DISCORD_ALLOWED_CHANNEL_IDS`—a narrow list is preferred; use `ALL` only when every
request receives an independently trusted scope. Authenticate with the same bearer token shown
below, then supply `x-discord-bot-token`, `x-discord-guild-id`, and
`x-discord-allowed-channels` on each provider call. Navigation tools need only the bearer token.

## Five-minute standalone deployment

Requirements: Docker with Compose v2 plus Python 3 for the safe setup helper, or Python 3.12/3.13
plus [uv](https://docs.astral.sh/uv/) for the direct runtime.

```bash
git clone https://github.com/MADPANDA3D/DISCORD-MCP.git
cd DISCORD-MCP
python3 scripts/init_runtime_env.py --mode standalone
```

Edit the generated mode-`0600` `.env` and add only private runtime values:

```dotenv
DISCORD_TOKEN=<discord-bot-token>
DISCORD_GUILD_ID=<guild-id>
DISCORD_ALLOWED_CHANNEL_IDS=<channel-id>,<channel-id>
```

Then start and verify with Docker Compose:

```bash
docker compose up --detach --build --wait
curl --fail http://127.0.0.1:8085/health
```

The container listens on internal port `8085`; change only the loopback host port with
`MCP_HOST_PORT`. The default Compose boundary is non-root, read-only, capability-free, protected
by `no-new-privileges`, PID-bounded, and exposed only on `127.0.0.1`.

The published OCI image currently targets `linux/amd64`. On another architecture, run the Python
package directly after executing the local gates:

```bash
uv sync --frozen --no-dev --python 3.12
uv run --env-file .env mad-mcp-discord
```

The generated direct-Python configuration binds to `127.0.0.1:8085`. Keep that listener private or
place it behind an operator-managed authenticated transport boundary.

### MCP client configuration

```json
{
  "mcpServers": {
    "discord": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8085/mcp",
      "headers": {
        "Authorization": "Bearer ${MCP_ACCESS_TOKEN}"
      }
    }
  }
}
```

`${MCP_ACCESS_TOKEN}` is a secret-store placeholder. Confirm how your MCP client resolves secrets;
do not paste service or Discord tokens into a tracked configuration file.

## Portal and request-scoped BYOK

Generate a safe Portal scaffold:

```bash
python3 scripts/init_runtime_env.py --mode portal
```

Portal mode keeps Discord credentials out of server configuration. The trusted broker supplies the
service grant and these required provider headers on each non-navigation call:

```http
X-MADPANDA-PORTAL-GRANT: <service-to-service-grant>
x-discord-bot-token: <secret-backed-bot-token>
x-discord-guild-id: <guild-id>
x-discord-allowed-channels: <comma-separated-channel-ids-or-ALL>
```

The base Compose file publishes loopback only; it is not an Internet-facing Portal deployment. A
Portal operator must provide a separately managed TLS proxy or isolated trusted network and add the
exact upstream Host value to `MCP_ALLOWED_HOSTS`. Wildcard hostnames are forbidden; a `host:*`
pattern may wildcard only the port.

Optional request headers can narrow or opt into capabilities already allowed by the server:

```http
x-discord-blocked-channels
x-discord-allow-all-read
x-discord-dm-enabled
x-mcp-admin-tools-enabled
x-mcp-require-confirm
x-openai-api
```

Request policy never exceeds server policy. Blocked channels always win; request flags cannot
enable DMs, read-all, admin tools, or relaxed confirmation beyond the server ceiling. A broker must
strip client-supplied privileged headers and inject trusted values after tenant authorization.

See [Portal compatibility](docs/portal-compat.md) for the complete broker and admission contract.

## Tool inventory

The immutable catalog `discord-2026.08.27.1` contains **55 registered tools**:

- **49 agent-ready**
- **3 legacy compatibility tools**
- **3 hidden compatibility tools**
- **22 read, 16 write, 17 destructive**

<details>
<summary><strong>Show all tools by domain</strong></summary>

| Domain | Tools |
|---|---|
| Navigation | `check_configuration`, `list_capabilities`, `get_endpoint_coverage`, `get_tool_usage`, `find_tools` |
| Server | `get_server_info`, `discord_server_read`, `discord_server_write`, `discord_server_destructive` |
| Configuration | `discord_health_check` |
| Channels | `create_text_channel`, `delete_channel`, `find_channel`, `list_channels` |
| Categories | `create_category`, `delete_category`, `find_category`, `list_channels_in_category` |
| Messages | `discord_ack`, `send_message`, `edit_message`, `delete_message`, `read_messages`, `search_messages`, `analyze_attachment` |
| Threads | `list_threads`, `create_thread`, `archive_thread`, `unarchive_thread` |
| Reactions | `add_reaction`, `remove_reaction` |
| Members | `edit_nickname`, `get_user_id_by_name` |
| Moderation | `timeout_member`, `remove_timeout`, `kick_member`, `ban_member`, `unban_member` |
| Roles | `add_role`, `remove_role` |
| Direct messages | `send_private_message`, `edit_private_message`, `delete_private_message`, `read_private_messages` |
| Audits | `channel_daily_audit`, `daily_audit_job_submit`, `daily_audit_job_status`, `daily_audit_job_next` |
| Webhooks | `create_webhook`, `delete_webhook`, `list_webhooks`, `send_webhook_message` |
| Legacy operations | `discord_smoke_test`, `discord_job_submit`, `discord_job_status` |

</details>

### Complete server management

The three `discord_server_*` tools expose 125 reviewed Discord REST actions through a fixed
registry: 56 read actions, 22 write actions, and 47 destructive actions. Call
`discord_server_read` for non-mutating queries, `discord_server_write` for confirmed state
creation or updates, and `discord_server_destructive` for confirmed deletion, moderation, and
other high-impact operations. The tools never accept an arbitrary HTTP method or path.

Server-management actions retain the same guild, channel, member, role-hierarchy, protected-ID,
admin-ceiling, and confirmation boundaries as the typed tools. Responses are size-bounded and
webhook credentials are redacted. See [Endpoint coverage](docs/endpoint-coverage.md) for the exact
official endpoint mapping and documented technical exclusions.

Use `find_tools` for intent search, `get_tool_usage` for the exact descriptor, and
`list_capabilities(include_descriptors=true)` for the lossless catalog. Every descriptor includes
input/output schemas, risk annotations, admin state, confirmation policy, tier, and a stable hash.

The full risk and permission matrix lives in [Tool catalog](docs/tool-catalog.md). The frozen
tool-by-tool parity proof lives in [Compatibility matrix](docs/compatibility-matrix.md). Coverage
and intentional exclusions are recorded in [Endpoint coverage](docs/endpoint-coverage.md).

## Safety policy

### Confirmation and administrative tools

Mutation tools marked for confirmation require this exact value in their `confirm` argument:

```text
CONFIRM APPLY
```

Administrative tools also require `MCP_ADMIN_TOOLS_ENABLED=true`. Request mode can opt into an
admin operation only when that server ceiling is already enabled. Confirmation remains enabled by
default.

### Channel and DM boundaries

- Writes require both the server channel policy and, in request mode, the request channel policy.
- Reads are constrained by the same intersection unless the server explicitly enables read-all.
- `DISCORD_BLOCKED_CHANNEL_IDS` is an absolute deny list.
- Direct-message tools are off by default.
- Roles and users can be protected with dedicated deny lists.

### Attachments and optional vision

Attachment URLs must be HTTPS on port 443 with no credentials, fragments, redirects, IP literals,
or private DNS answers. Connections use pinned public DNS results, ignore environment proxies, and
enforce time and size ceilings. Server-local paths are permitted only in standalone/server mode and
only beneath `MCP_ATTACHMENT_ALLOWED_DIRS`.

`analyze_attachment` is disabled until `OPENAI_VISION_ENABLED=true`. When enabled, the selected
Discord image and prompt cross a separate OpenAI data-transfer and cost boundary. The configured
endpoint must be HTTPS with a valid hostname and no URL user information or fragment. All
service/provider modes supply the OpenAI key per call with `x-openai-api`; a Portal broker must
strip client input and inject that value from its trusted secret store.

## Configuration

The public configuration reference is in [.env.example](.env.example). The core settings are:

| Variable | Purpose | Safe default |
|---|---|---|
| `MCP_MODE` | `standalone` or `portal` service boundary | required |
| `MCP_ACCESS_TOKEN` | Standalone bearer credential | required in standalone |
| `MCP_PORTAL_GRANT_TOKEN` | Portal service-to-service grant | required in Portal |
| `DISCORD_CREDENTIAL_MODE` | `server` or `request`; Portal derives `request` | mode-derived |
| `DISCORD_TOKEN` | Server-owned Discord bot token | empty |
| `DISCORD_GUILD_ID` | Server-owned guild scope | empty |
| `DISCORD_ALLOWED_CHANNEL_IDS` | Server channel ceiling | deny-all when empty |
| `DISCORD_BLOCKED_CHANNEL_IDS` | Absolute channel deny list | empty |
| `MCP_ADMIN_TOOLS_ENABLED` | Administrative operation ceiling | `false` |
| `MCP_REQUIRE_CONFIRM` | Require exact mutation confirmation | `true` |
| `DISCORD_DM_ENABLED` | Direct-message ceiling | `false` |
| `OPENAI_VISION_ENABLED` | Optional attachment analysis boundary | `false` |
| `MCP_BIND_ADDRESS` | Direct-Python listener address | `127.0.0.1` |
| `MCP_HTTP_PORT` | Direct-Python listener port | `8085` |
| `MCP_HOST_PORT` | Loopback host port for Compose | `8085` |

`/health` reports process readiness and safe release identity. In request mode it cannot prove a
particular tenant's Discord credentials; call `check_configuration` with that request context.

## Immutable deployment

Tagged releases target this OCI namespace:

```text
ghcr.io/madpanda3d/discord-mcp-server
```

After a release passes the public-package visibility gate, deploy the exact digest—not `latest`:

```bash
export MCP_RUNTIME_IMAGE='ghcr.io/madpanda3d/discord-mcp-server@sha256:<digest>'
export MCP_IMAGE_REFERENCE="$MCP_RUNTIME_IMAGE"
docker compose pull
docker compose up --detach --no-build --wait
```

Stable tags must point to a commit already reachable from protected `main`. The release workflow
rechecks that ancestry and exact tag identity, tests Python 3.12/3.13, audits locked dependencies,
checks wheel/sdist allowlists, scans source and image content, emits provenance/SBOM data, requires
an anonymous digest pull, and smokes standalone/server, standalone/request-BYOK, and Portal/request
profiles before creating the GitHub release.

A private bootstrap run is expected to stop at the package-visibility gate. After the exact GHCR
package and canonical source repository are public, rerun that same tagged workflow; it reads live
repository visibility and will create attestations and the GitHub release only on the public run.

## Local development

```bash
uv sync --frozen --group dev --python 3.12
uv run python -m compileall -q src tests scripts
uv run python -m unittest discover -s tests -p 'test_*.py'
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv build
uv run twine check dist/*
uv run python scripts/check_package_archives.py
uv run python scripts/check_source_safety.py
```

The distribution is `mad-mcp-discord`; the console command is `mad-mcp-discord`. PyPI publishing is
not claimed or enabled. Attested Python artifacts are attached to GitHub releases, and GHCR is the
initial package channel.

## Operational truth

Request-scoped clients, audit cursors, jobs, and caches are process-local. A restart discards
transient jobs, and multiple replicas require sticky routing or an external state design that this
release does not provide. Authorized read tools can return Discord message content and user
metadata; treat those results as sensitive.

For source verification, startup, upgrades, rollback, and incident handling, use the
[Operator runbook](docs/operator-runbook.md). The complete trust model is in
[Security model](docs/security-model.md).

## Documentation

- [Tool catalog](docs/tool-catalog.md)
- [Endpoint coverage](docs/endpoint-coverage.md)
- [Security model](docs/security-model.md)
- [Operator runbook](docs/operator-runbook.md)
- [Portal compatibility](docs/portal-compat.md)
- [Provenance](docs/provenance.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Support](SUPPORT.md)

## License and provenance

Licensed under the [MIT License](LICENSE). This project preserves and credits work from
[SaseQ/discord-mcp](https://github.com/SaseQ/discord-mcp); see [NOTICE](NOTICE) and
[Provenance](docs/provenance.md) for the exact lineage and clean-history boundary.

Discord is a trademark of Discord Inc. This project is not affiliated with, endorsed by, or
sponsored by Discord Inc.
