# Tool catalog

Catalog: `discord-2026.08.19.1`

| Contract field | Value |
|---|---:|
| Registered | 52 |
| Agent-ready | 46 |
| Legacy | 3 |
| Hidden | 3 |
| Read | 21 |
| Write | 15 |
| Destructive | 16 |

Risk is an execution-planning signal, not a substitute for Discord permissions or operator review:

- **Read** does not intentionally change Discord state.
- **Write** creates or advances state without being classified as destructive.
- **Destructive** deletes, moderates, overwrites, or otherwise makes a high-impact change.

`confirm` means the exact `CONFIRM APPLY` phrase is required when confirmation policy is enabled.
`admin` means the server admin ceiling must also be enabled. “Typical capability” is a concise
operator aid; Discord remains authoritative for the exact permission calculation and role hierarchy.

| Tool | Domain | Risk | Tier | Gate | Typical Discord capability |
|---|---|---|---|---|---|
| `get_server_info` | Server | Read | Agent-ready | Standard | Guild membership / View Server |
| `discord_health_check` | Configuration | Read | Agent-ready | Standard | Guild and sampled channel visibility |
| `discord_ack` | Messages | Write | Agent-ready | Confirm | Send Messages |
| `send_message` | Messages | Write | Agent-ready | Confirm | Send Messages; optional Embed Links / Attach Files |
| `discord_smoke_test` | Operations | Destructive | Legacy | Admin + confirm | Read, send, edit, delete test message |
| `discord_job_submit` | Operations | Write | Legacy | Standard | Depends on delegated legacy action |
| `discord_job_status` | Operations | Read | Legacy | Standard | None beyond original action context |
| `edit_message` | Messages | Destructive | Agent-ready | Admin + confirm | Edit bot-authored message |
| `delete_message` | Messages | Destructive | Agent-ready | Admin + confirm | Manage Messages or bot ownership |
| `read_messages` | Messages | Read | Agent-ready | Standard | View Channel + Read Message History |
| `search_messages` | Messages | Read | Agent-ready | Standard | View Channel + Read Message History |
| `analyze_attachment` | Messages | Read | Agent-ready | Vision opt-in | Read history + attachment access + OpenAI boundary |
| `list_threads` | Threads | Read | Agent-ready | Standard | View Channel + thread visibility |
| `create_thread` | Threads | Write | Agent-ready | Confirm | Create Public Threads / Send Messages in Threads |
| `archive_thread` | Threads | Destructive | Agent-ready | Confirm | Manage Threads or thread ownership |
| `unarchive_thread` | Threads | Write | Agent-ready | Confirm | Manage Threads or thread ownership |
| `channel_daily_audit` | Audits | Read | Agent-ready | Standard | View Channel + Read Message History |
| `daily_audit_job_submit` | Audits | Write | Agent-ready | Standard | Same read scope as audit |
| `daily_audit_job_status` | Audits | Read | Agent-ready | Standard | Process-local job ownership |
| `daily_audit_job_next` | Audits | Write | Agent-ready | Standard | Same read scope as audit |
| `add_reaction` | Reactions | Write | Agent-ready | Confirm | Add Reactions + Read Message History |
| `remove_reaction` | Reactions | Destructive | Agent-ready | Confirm | Manage Messages or bot reaction ownership |
| `timeout_member` | Moderation | Destructive | Agent-ready | Admin + confirm | Moderate Members |
| `remove_timeout` | Moderation | Write | Agent-ready | Admin + confirm | Moderate Members |
| `kick_member` | Moderation | Destructive | Agent-ready | Admin + confirm | Kick Members |
| `ban_member` | Moderation | Destructive | Agent-ready | Admin + confirm | Ban Members |
| `unban_member` | Moderation | Write | Agent-ready | Admin + confirm | Ban Members |
| `add_role` | Roles | Destructive | Agent-ready | Admin + confirm | Manage Roles + valid hierarchy |
| `remove_role` | Roles | Destructive | Agent-ready | Admin + confirm | Manage Roles + valid hierarchy |
| `edit_nickname` | Members | Destructive | Agent-ready | Admin + confirm | Manage Nicknames + valid hierarchy |
| `get_user_id_by_name` | Members | Read | Agent-ready | Standard | Guild Members intent / member visibility |
| `send_private_message` | Direct messages | Write | Agent-ready | DM opt-in + confirm | Bot can open recipient DM |
| `edit_private_message` | Direct messages | Destructive | Agent-ready | DM opt-in + confirm | Bot-authored DM ownership |
| `delete_private_message` | Direct messages | Destructive | Agent-ready | DM opt-in + confirm | Bot-authored DM ownership |
| `read_private_messages` | Direct messages | Read | Agent-ready | DM opt-in | Bot DM-channel history |
| `create_text_channel` | Channels | Write | Agent-ready | Admin + confirm | Manage Channels |
| `delete_channel` | Channels | Destructive | Agent-ready | Admin + confirm | Manage Channels |
| `find_channel` | Channels | Read | Agent-ready | Standard | View Channel |
| `list_channels` | Channels | Read | Agent-ready | Standard | View Channel |
| `create_category` | Categories | Write | Agent-ready | Admin + confirm | Manage Channels |
| `delete_category` | Categories | Destructive | Agent-ready | Admin + confirm | Manage Channels |
| `find_category` | Categories | Read | Agent-ready | Standard | View Channel |
| `list_channels_in_category` | Categories | Read | Agent-ready | Standard | View Channel |
| `create_webhook` | Webhooks | Write | Hidden | Admin + confirm | Manage Webhooks; URL output redacted |
| `delete_webhook` | Webhooks | Destructive | Agent-ready | Admin + confirm | Manage Webhooks |
| `list_webhooks` | Webhooks | Read | Hidden | Admin + confirm | Manage Webhooks + write allowlist; URLs omitted |
| `send_webhook_message` | Webhooks | Write | Hidden | Admin + confirm + all-channel policy | Separately supplied Discord webhook URL |
| `check_configuration` | Navigation | Read | Agent-ready | Service auth | No Discord call |
| `list_capabilities` | Navigation | Read | Agent-ready | Service auth | No Discord call |
| `get_endpoint_coverage` | Navigation | Read | Agent-ready | Service auth | No Discord call |
| `get_tool_usage` | Navigation | Read | Agent-ready | Service auth | No Discord call |
| `find_tools` | Navigation | Read | Agent-ready | Service auth | No Discord call |

## Provider intents and permissions

The bot generally needs the Guilds, Guild Members, and Message Content intents for the documented
read and lookup behavior. Grant only the Discord permissions required by the selected tool set.
Server policy does not turn a missing Discord permission into authority, and a broad Discord bot
permission does not bypass server policy.

Role hierarchy, bot ownership rules, channel overwrites, Discord rate limits, and provider-side
validation can still reject a call that passes MCP policy.

## Discovery contract

The runtime registry is authoritative and can be inspected without contacting Discord:

- `list_capabilities(include_descriptors=false)` returns counts, categories, and hashes.
- `list_capabilities(include_descriptors=true)` returns all ordered lossless descriptors.
- `find_tools` performs deterministic intent search.
- `get_tool_usage` resolves one name or compatibility alias.
- `get_endpoint_coverage` exposes the partial-coverage declaration.

Each descriptor carries a per-tool hash. The catalog carries a hash of the materialized descriptors,
including those per-tool hashes. Build SHA and runtime credentials are excluded from descriptor
hashes.

## Legacy tools

`discord_smoke_test`, `discord_job_submit`, and `discord_job_status` remain for compatibility only.
New integrations should call typed direct tools. Legacy does not mean unauthenticated or exempt from
policy; it means the surface is not considered agent-ready and may be removed in a future major
release.
