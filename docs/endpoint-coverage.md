# Discord endpoint coverage

This document mirrors the runtime coverage contract in catalog
`discord-2026.07.18.1`. The catalog is intentionally partial: a listed Discord domain does not
imply complete API coverage.

| Feature | Status | Exposed tools | Important exclusions |
|---|---|---:|---|
| Guild metadata | Partial | 2 | Guild-settings mutation |
| Channels and categories | Partial | 8 | Voice, stage, forum, overwrites, invites, positions |
| Messages and reactions | Partial | 9 | Pins, polls, crossposts, bulk delete, typing, interactions |
| Threads | Partial | 4 | Forum posts, membership, standalone private threads |
| Members, roles, moderation | Partial | 9 | Bulk inventory, prune, voice mutation, role lifecycle |
| Direct messages | Partial | 4 | Group-DM recipient management |
| Webhooks | Partial | 2 | Creation and token-URL execution |
| Audits and jobs | Provider extension | 6 | Durable distributed job orchestration |
| OAuth, commands, Gateway management | Not exposed | 0 | Entire surface intentionally excluded |
| Other Discord resources | Not exposed | 0 | Invites, emojis, stickers, events, automod, stages, commerce |

## Guild metadata

Tools: `get_server_info`, `discord_health_check`.

The server reads guild identity, counts, bot permissions, and provider readiness. It does not mutate
guild configuration. Typical prerequisites are guild membership and the `GUILDS` intent.

## Channels and categories

Tools: `create_text_channel`, `delete_channel`, `find_channel`, `list_channels`,
`create_category`, `delete_category`, `find_category`, `list_channels_in_category`.

This surface covers text-channel and category inventory plus guarded lifecycle operations. Voice,
stage, forum, permission-overwrite, invite, and position management remain outside the contract.

## Messages and reactions

Tools: `discord_ack`, `send_message`, `edit_message`, `delete_message`, `read_messages`,
`search_messages`, `analyze_attachment`, `add_reaction`, `remove_reaction`.

The server covers bounded message reads, bot-authored writes, filters, one attachment, and bot
reactions. It does not expose pins, polls, crossposts, bulk delete, typing indicators, or interaction
responses. Reading message content requires the applicable Discord intent and channel permissions.

## Threads

Tools: `list_threads`, `create_thread`, `archive_thread`, `unarchive_thread`.

The implementation covers message-based thread creation and archive lifecycle. Forum posts,
membership management, and standalone private-thread creation are excluded.

## Members, roles, and moderation

Tools: `get_user_id_by_name`, `timeout_member`, `remove_timeout`, `kick_member`, `ban_member`,
`unban_member`, `add_role`, `remove_role`, `edit_nickname`.

This is a targeted, confirmation-gated surface. It does not provide bulk member inventory, prune,
voice-state mutation, role creation/deletion, or verification-level management. Discord role
hierarchy and protected-user/protected-role policies still apply.

## Direct messages

Tools: `send_private_message`, `edit_private_message`, `delete_private_message`,
`read_private_messages`.

One-to-one bot DMs are available only when the server enables the DM policy. Group-DM recipient
management is not implemented.

## Webhooks

Tools: `list_webhooks`, `delete_webhook`.

Only credential-safe listing and guarded deletion are exposed. Webhook creation and webhook-token
execution are intentionally excluded because a Discord webhook URL is a bearer credential.

## Audits and jobs

Tools: `channel_daily_audit`, `daily_audit_job_submit`, `daily_audit_job_status`,
`daily_audit_job_next`, `discord_job_submit`, `discord_job_status`.

These are provider extensions built on bounded message reads. The generic `discord_job_*` dispatcher
is retained as legacy compatibility and is not agent-ready. All job and cursor state is process-local.

## Intentionally unexposed

OAuth installation, application commands, raw Gateway lifecycle, arbitrary REST requests, invites,
emojis, stickers, scheduled events, automod, stages, templates, entitlements, SKUs, soundboards, and
monetization are not part of this server.

Authoritative upstream references:

- [Discord guild resource](https://docs.discord.com/developers/resources/guild)
- [Discord channel resource](https://docs.discord.com/developers/resources/channel)
- [Discord message resource](https://docs.discord.com/developers/resources/message)
- [Discord webhook resource](https://docs.discord.com/developers/resources/webhook)
- [Discord OAuth and permissions](https://docs.discord.com/developers/platform/oauth2-and-permissions)

When this document and runtime behavior differ, `list_capabilities(include_descriptors=true)` and
`get_endpoint_coverage` are the machine-readable release contract; the discrepancy is a release
blocker.
