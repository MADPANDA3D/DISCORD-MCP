# Discord API endpoint coverage

Verified against the official Discord developer documentation on 2026-08-27:

- [Server and channel management](https://docs.discord.com/developers/platform/server-and-channel-management)
- [API reference](https://docs.discord.com/developers/reference)
- [Guild resource](https://docs.discord.com/developers/resources/guild)
- [Channel resource](https://docs.discord.com/developers/resources/channel)
- [Message resource](https://docs.discord.com/developers/resources/message)
- [Auto Moderation](https://docs.discord.com/developers/resources/auto-moderation)
- [Audit logs](https://docs.discord.com/developers/resources/audit-log)
- [Scheduled events](https://docs.discord.com/developers/resources/guild-scheduled-event)
- [Stage instances](https://docs.discord.com/developers/resources/stage-instance)
- [Voice](https://docs.discord.com/developers/resources/voice)
- [Emoji](https://docs.discord.com/developers/resources/emoji)
- [Sticker](https://docs.discord.com/developers/resources/sticker)
- [Soundboard](https://docs.discord.com/developers/resources/soundboard)
- [Webhook resource](https://docs.discord.com/developers/resources/webhook)
- [OAuth2 and permissions](https://docs.discord.com/developers/platform/oauth2-and-permissions)

## Execution model

The existing purpose-built tools remain the preferred interface for routine
messages, moderation, channel lookup, audits, and one-to-one DMs. Complete
stable bot-token server-management coverage is provided by three additional
risk-separated tools:

| Tool | Risk | Contract |
| --- | --- | --- |
| `discord_server_read` | read | One immutable `action` enum; bounded query fields, channel policy, permission preflight, one bounded read retry after a provider 429, and credential-redacted results. |
| `discord_server_write` | write | Additive or reversible actions only; reviewed action-specific JSON fields, admin policy, channel/permission checks, audit-log reasons where supported, and provider confirmation policy. |
| `discord_server_destructive` | destructive | Overwrites, reorders, moderation, removals, prune, bulk operations, and deletes; always requires `confirm=CONFIRM APPLY`, plus protected-target and role-hierarchy checks where applicable. |

Agents cannot supply arbitrary HTTP methods or paths. `action` is a generated
enum from the reviewed operation registry. Unknown query or payload fields are
rejected before Discord is contacted. Lists are capped at 100 items, output is
bounded below the MCP wire budget, and webhook tokens/URLs are always redacted.

## Stable bot-token server-management inventory

| Resource family | Coverage | Read actions | Write/destructive actions | Notes |
| --- | --- | --- | --- | --- |
| Guild settings and community | Covered | `get_guild`, `get_guild_preview`, widget, vanity URL, welcome screen, onboarding | `modify_guild`, widget, welcome screen, onboarding, incident actions | Owner-only fields remain provider-enforced. |
| Channels, categories, permissions, and ordering | Covered | guild channel inventory, `get_channel`, and `get_effective_channel_permissions` | create/modify/delete channel, channel positions, permission overwrite edit/delete, voice status | Covers text, category, announcement, voice, stage, forum, and media channel objects plus effective bot/member/role permissions and raw overwrites. |
| Messages, reactions, pins, and bulk moderation | Covered | channel history, guild search, message, reactions, current paginated pins | crosspost, typing, pin/unpin, clear reactions, bulk delete | Dedicated message tools remain preferred for ordinary sends/edits/deletes. |
| Forums and threads | Covered | active and archived public/private/joined thread inventory and thread members | message thread, standalone thread, forum/media post, join/add/leave/remove member | Fixes TKT-000293: forum/media channels are handled through the official `/channels/{id}/threads` route instead of text-channel history assumptions. |
| Members, roles, bans, prune, and voice state | Covered | member get/list/search, ban get/list, role list/get/counts, prune count, voice regions/states | role lifecycle/order, member changes, role assignment/removal, bulk ban, prune, voice-state changes | Mutations enforce permission, protected-target, and role-hierarchy checks. |
| Invites, integrations, widgets, onboarding, templates | Covered | guild/channel invites, integrations, widget, welcome, onboarding, templates | invite creation/delete, integration delete, widget/welcome/onboarding changes, template CRUD/sync | Community invite campaign target-user jobs are outside server structure. |
| Auto Moderation and audit logs | Covered | rule list/get, bounded guild audit log | rule create/modify/delete | Audit reads require `VIEW_AUDIT_LOG`; mutations carry supported audit reasons. |
| Scheduled events, stages, and voice | Covered | event/list/users, stage instance, global/guild voice regions and states | event/stage lifecycle and guild voice-state changes | Stable v10 routes only. |
| Guild emojis, stickers, and soundboard | Covered | guild expression and sound inventory | emoji/sticker/sound lifecycle plus sound playback | Sticker upload is bounded multipart; application-owned emojis are not guild administration. |
| Credential-safe webhooks | Covered with exclusion | channel/guild list and bot-authorized get | safe create/modify/delete | Tokens and credential-bearing URLs are redacted. Token-authenticated execute/message routes are excluded; use `send_message`. |
| One-to-one direct messages | Covered with exclusion | `read_private_messages` | send/edit/delete dedicated tools | Group-DM recipient routes require user-account OAuth and are not bot-token server administration. |

## Exact technical exclusions

The following official endpoint families are intentionally not exposed because
they are not stable bot-token server-management operations:

- OAuth installation/grants, application commands/interactions, and raw Gateway
  lifecycle: Portal or application control-plane responsibilities.
- `Add Guild Member`: requires a user OAuth2 access token with `guilds.join`, not
  a bot token alone.
- Group DMs, user relationships/connections, and Discord Social SDK lobbies:
  user-account or Social SDK credentials; bot use is technically inapplicable.
- Monetization SKUs, entitlements, subscriptions, and application-owned emojis:
  application commerce/configuration rather than guild administration.
- Webhook-token get/execute/message endpoints: require bearer-like webhook
  secrets that this MCP must neither accept nor return. Bot-authorized webhook
  management and `send_message` cover the safe administration path.
- Binary guild widget image export: public media rendering rather than server
  configuration; widget settings and JSON are covered.

Call `get_endpoint_coverage` for the machine-readable matrix.
Call `list_capabilities(include_descriptors=true)` for the immutable ordered
ToolManifest and `get_tool_usage` for a lossless per-tool schema.
