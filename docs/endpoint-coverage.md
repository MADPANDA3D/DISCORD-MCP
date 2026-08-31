# Discord API endpoint coverage

Verified against the official Discord developer documentation on 2026-07-12:

- [API reference](https://docs.discord.com/developers/reference)
- [Guild resource](https://docs.discord.com/developers/resources/guild)
- [Channel resource](https://docs.discord.com/developers/resources/channel)
- [Message resource](https://docs.discord.com/developers/resources/message)
- [Webhook resource](https://docs.discord.com/developers/resources/webhook)
- [OAuth2 and permissions](https://docs.discord.com/developers/platform/oauth2-and-permissions)

The FastMCP transport uses `discord.py`, so several tools map to provider-library
operations rather than issuing raw HTTP requests directly. “Partial” is intentional:
this provider does not claim total Discord API coverage. Unimplemented resource
families are recorded below instead of being silently implied as supported.

| Resource / endpoint pattern | Method | Coverage | MCP tools | Notes |
| --- | --- | --- | --- | --- |
| `/guilds/{guild.id}` and cached guild state | GET | Partial | `get_server_info`, `discord_health_check` | Identity, counts, owner, boosts, bot permissions, and readiness only. |
| `/guilds/{guild.id}/channels` | GET, POST | Partial | `list_channels`, `find_channel`, `create_text_channel`, `create_category`, `find_category`, `list_channels_in_category` | Text channels and categories only. Voice, stage, forum, positions, overwrites, and invites are excluded. |
| `/channels/{channel.id}` | PATCH, DELETE | Partial | `delete_channel`, `delete_category`, `archive_thread`, `unarchive_thread` | Delete and thread archive state only; general channel mutation is excluded. |
| `/channels/{channel.id}/messages` | GET, POST | Partial | `send_message`, `discord_ack`, `read_messages`, `search_messages`, `channel_daily_audit` | Bounded history and bot delivery. Pins, polls, crossposts, bulk delete, and typing are excluded. |
| `/channels/{channel.id}/messages/{message.id}` | PATCH, DELETE | Covered for bot-managed operations | `edit_message`, `delete_message` | Admin policy, channel allowlists, dry run, and confirmation apply. |
| `/channels/{channel.id}/messages/{message.id}/reactions/...` | PUT, DELETE | Partial | `add_reaction`, `remove_reaction` | The configured bot’s own reactions only. |
| Message attachment CDN plus configured vision endpoint | GET, POST | Provider extension | `read_attachment`, `analyze_attachment` | Bounded attachment bytes and safe ZIP text inspection; image OCR/description additionally requires explicit OpenAI vision setup. |
| `/channels/{channel.id}/messages/{message.id}/threads` and thread channel PATCH | POST, PATCH | Partial | `create_thread`, `list_threads`, `archive_thread`, `unarchive_thread` | Forum posts, thread membership, and standalone private threads are excluded. |
| `/guilds/{guild.id}/members/{user.id}` | GET, PATCH, DELETE | Partial | `get_user_id_by_name`, `timeout_member`, `remove_timeout`, `kick_member`, `edit_nickname` | Guarded targeted member operations; bulk member inventory, prune, and voice mutation are excluded. |
| `/guilds/{guild.id}/bans/{user.id}` | PUT, DELETE | Covered for targeted moderation | `ban_member`, `unban_member` | Hierarchy, protected-target, and confirmation checks apply. |
| `/guilds/{guild.id}/members/{user.id}/roles/{role.id}` | PUT, DELETE | Covered for assignment | `add_role`, `remove_role` | Role create/edit/delete is not implemented. |
| `/users/@me/channels` plus DM message endpoints | POST, GET, PATCH, DELETE | Partial | `send_private_message`, `read_private_messages`, `edit_private_message`, `delete_private_message` | One-to-one DMs only and disabled unless policy enables them. Group DM management is excluded. |
| `/channels/{channel.id}/webhooks`, `/webhooks/{webhook.id}`, webhook execution | GET, POST, DELETE | Legacy/hidden | `create_webhook`, `list_webhooks`, `delete_webhook`, `send_webhook_message` | Legacy create/list expose credential-bearing URLs; affected tools stay hidden from agent-ready discovery pending redesign. |
| In-memory audit and job state | N/A | Provider extension | `daily_audit_job_submit`, `daily_audit_job_status`, `daily_audit_job_next`, `discord_job_submit`, `discord_job_status` | Generic legacy job dispatch remains outside agent-ready discovery. |
| OAuth installation, application commands, and raw Gateway lifecycle | Multiple | Intentionally not exposed | None | Portal owns client authentication; these are outside this provider contract. |
| Invites, emojis, stickers, scheduled events, automod, stages, templates, entitlements, SKUs, soundboards, monetization | Multiple | Intentionally not exposed | None | No implementation; recorded explicitly for discovery truthfulness. |

Call `get_endpoint_coverage` for the machine-readable matrix. Call
`list_capabilities(include_descriptors=true)` for the immutable ordered descriptor
catalog and `get_tool_usage` for a lossless per-tool schema.
