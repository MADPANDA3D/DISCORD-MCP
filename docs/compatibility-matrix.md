# Discord compatibility matrix

This matrix freezes the owner-approved Discord contract reconciled for MAD-728. The preserved
compatibility projection is SHA-256
`be7a0b26064d2a8b9ec167532cb1acce29007fe2d10b6f0ab24aa7f119393924`. It covers every native
tool name, alias, contract tier, annotation, input property, required input, and declared output
field. The approved access and confirmation projection is SHA-256
`48c0cf795764f4e822d8e54616d2aa2d6e1f53873da016073804575b8c8056a5`. CI recomputes both.

All 52 tools retain the normalized `ok/data/meta` or `ok/error/meta` envelope and semantic error
types. Provider calls remain request-scoped, retained jobs and cursors remain tenant-fingerprinted,
tool output and retained state remain bounded, and the daily-audit/legacy-job helpers continue to
offload multi-step work without sharing tenant state. Hidden tools are registered for compatibility
but excluded from agent-ready discovery.

| Tool | Tier | Risk | Aliases | Required inputs | Declared outputs | Confirm | Reconciliation |
|---|---|---|---|---|---|---|---|
| `get_server_info` | agent_ready | read | `server_info`, `guild_info` | — | `id`, `name`, `owner`, `boosts`, `channels`, `created_on`, `member_count` | none | Exact contract |
| `discord_health_check` | agent_ready | read | `health_check`, `check_discord_health` | — | `bot`, `guild`, `status`, `healthy`, `warnings`, `capabilities`, `discord_config`, `last_successful_api_at` | none | Exact contract |
| `discord_ack` | agent_ready | write | `acknowledge`, `send_ack` | — | `jump_url`, `channel_id`, `message_id` | required | Descriptor corrected to match runtime confirmation gate |
| `send_message` | agent_ready | write | `post_message`, `discord_send_message` | — | `dry_run`, `jump_url`, `thread_id`, `channel_id`, `message_id`, `attachments`, `diagnostics`, `planned_parts`, `sent_message_ids` | required | Exact contract |
| `discord_smoke_test` | legacy | destructive | `smoke_test` | — | `ok`, `steps`, `channel_id`, `message_id`, `duration_ms` | required | Exact contract |
| `discord_job_submit` | legacy | write | `submit_job` | `action` | `action`, `status`, `task_id` | none | Exact contract |
| `discord_job_status` | legacy | read | `job_status` | `task_id` | `error`, `action`, `result`, `status`, `task_id`, `created_at`, `started_at`, `finished_at` | none | Exact contract |
| `edit_message` | agent_ready | destructive | `update_message` | — | `dry_run`, `jump_url`, `channel_id`, `message_id`, `diagnostics` | required | Exact contract |
| `delete_message` | agent_ready | destructive | `remove_message` | — | `dry_run`, `channel_id`, `message_id`, `diagnostics` | required | Exact contract |
| `read_messages` | agent_ready | read | `list_messages`, `get_messages` | — | `count`, `messages`, `channel_id`, `after_message_id`, `before_message_id` | none | Exact contract |
| `search_messages` | agent_ready | read | `find_messages`, `message_search` | — | `count`, `limit`, `filters`, `messages`, `channel_id` | none | Exact contract |
| `analyze_attachment` | agent_ready | read | `ocr_attachment`, `describe_attachment` | — | `mode`, `text`, `model`, `usage`, `attachment`, `channel_id`, `message_id` | none | Exact contract |
| `list_threads` | agent_ready | read | `get_threads` | — | `count`, `threads`, `channel_id` | none | Exact contract |
| `create_thread` | agent_ready | write | `start_thread` | `channel_id`, `message_id`, `name` | `name`, `thread_id`, `message_id` | required | Exact contract |
| `archive_thread` | agent_ready | destructive | `close_thread` | `thread_id` | `archived`, `thread_id` | required | Exact contract |
| `unarchive_thread` | agent_ready | write | `reopen_thread` | `thread_id` | `archived`, `thread_id` | required | Exact contract |
| `channel_daily_audit` | agent_ready | read | `daily_channel_audit` | `channel_id` | `date`, `blockers`, `timezone`, `decisions`, `questions`, `range_utc`, `channel_id`, `highlights`, `links_topN`, `top_authors`, `channel_name`, `message_count`, `unique_authors`, `include_threads`, `attachments_count` | none | Exact contract |
| `daily_audit_job_submit` | agent_ready | write | `submit_daily_audit` | — | `status`, `task_id`, `total_channels` | none | Exact contract |
| `daily_audit_job_status` | agent_ready | read | `daily_audit_status` | `task_id` | `date`, `error`, `status`, `results`, `task_id`, `timezone`, `created_at`, `finished_at`, `total_channels`, `completed_count`, `next_channel_id`, `remaining_count` | none | Exact contract |
| `daily_audit_job_next` | agent_ready | write | `next_daily_audit` | `task_id` | `job`, `status`, `results`, `task_id`, `channel_id`, `channel_result`, `completed_count`, `remaining_count` | none | Exact contract |
| `add_reaction` | agent_ready | write | `react_to_message` | `channel_id`, `message_id`, `emoji` | `jump_url`, `channel_id`, `message_id` | required | Exact contract |
| `remove_reaction` | agent_ready | destructive | `unreact_to_message` | `channel_id`, `message_id`, `emoji` | `jump_url`, `channel_id`, `message_id` | required | Exact contract |
| `timeout_member` | agent_ready | destructive | `mute_member` | `user_id`, `duration_minutes` | `reason`, `user_id`, `timeout_until`, `duration_minutes` | required | Exact contract |
| `remove_timeout` | agent_ready | write | `untimeout_member`, `unmute_member` | `user_id` | `reason`, `user_id`, `timeout_removed` | required | Exact contract |
| `kick_member` | agent_ready | destructive | `remove_member` | `user_id` | `kicked`, `reason`, `user_id` | required | Exact contract |
| `ban_member` | agent_ready | destructive | `block_member` | `user_id` | `banned`, `reason`, `user_id`, `delete_message_days` | required | Exact contract |
| `unban_member` | agent_ready | write | `remove_ban` | `user_id` | `reason`, `user_id`, `unbanned` | required | Exact contract |
| `add_role` | agent_ready | destructive | `grant_role` | `user_id`, `role_id` | `added`, `reason`, `role_id`, `user_id`, `role_name` | required | Exact contract |
| `remove_role` | agent_ready | destructive | `revoke_role` | `user_id`, `role_id` | `reason`, `removed`, `role_id`, `user_id`, `role_name` | required | Exact contract |
| `edit_nickname` | agent_ready | destructive | `set_nickname` | `user_id`, `nickname` | `reason`, `cleared`, `user_id`, `nickname` | required | Exact contract |
| `get_user_id_by_name` | agent_ready | read | `find_user`, `resolve_user_id` | `username` | `user_id`, `username` | none | Exact contract |
| `send_private_message` | agent_ready | write | `send_dm` | `user_id`, `message` | `user_id`, `jump_url`, `message_id` | required | Exact contract |
| `edit_private_message` | agent_ready | destructive | `edit_dm` | `user_id`, `message_id`, `new_message` | `user_id`, `jump_url`, `message_id` | required | Exact contract |
| `delete_private_message` | agent_ready | destructive | `delete_dm` | `user_id`, `message_id` | `user_id`, `message_id` | required | Exact contract |
| `read_private_messages` | agent_ready | read | `read_dms` | `user_id` | `count`, `messages` | none | Exact contract |
| `create_text_channel` | agent_ready | write | `add_channel` | `name` | `name`, `channel_id`, `category_id`, `category_name` | required | Exact contract |
| `delete_channel` | agent_ready | destructive | `remove_channel` | `channel_id` | `name`, `type`, `channel_id` | required | Exact contract |
| `find_channel` | agent_ready | read | `resolve_channel` | `channel_name` | `count`, `channels` | none | Exact contract |
| `list_channels` | agent_ready | read | `get_channels` | — | `count`, `channels` | none | Exact contract |
| `create_category` | agent_ready | write | `add_category` | `name` | `name`, `category_id` | required | Exact contract |
| `delete_category` | agent_ready | destructive | `remove_category` | `category_id` | `name`, `category_id` | required | Exact contract |
| `find_category` | agent_ready | read | `resolve_category` | `category_name` | `count`, `categories` | none | Exact contract |
| `list_channels_in_category` | agent_ready | read | `category_channels` | `category_id` | `count`, `channels` | none | Exact contract |
| `create_webhook` | hidden | write | `add_webhook` | `channel_id`, `name` | `url`, `name`, `webhook_id` | required | Restored; URL field is runtime-redacted |
| `delete_webhook` | agent_ready | destructive | `remove_webhook` | `webhook_id` | `name`, `webhook_id` | required | Exact contract with tenant channel-scope enforcement |
| `list_webhooks` | hidden | read | `get_webhooks` | `channel_id` | `count`, `webhooks` | required | Descriptor corrected to match runtime confirmation gate; URLs omitted |
| `send_webhook_message` | hidden | write | `post_webhook_message` | `webhook_url`, `message` | `jump_url`, `message_id` | required | Restored; fixed Discord origin, 2,000-character bound |
| `check_configuration` | agent_ready | read | `configuration_status` | — | `ready`, `missing`, `capabilities`, `configuration` | none | Exact contract |
| `list_capabilities` | agent_ready | read | `get_manifest`, `list_tools_manifest` | — | `tools`, `counts`, `buildSha`, `serviceId`, `categories`, `nextAction`, `schemaVersion`, `catalogVersion`, `descriptorHash`, `serviceAliases`, `descriptorsIncluded` | none | Exact contract |
| `get_endpoint_coverage` | agent_ready | read | `endpoint_coverage` | — | `count`, `filter`, `coverage`, `serviceId`, `retrievedAt`, `catalogVersion` | none | Exact contract |
| `get_tool_usage` | agent_ready | read | `describe_tool`, `tool_reference` | `tool_name` | `descriptor`, `nextAction` | none | Exact contract |
| `find_tools` | agent_ready | read | `search_tools`, `discover_tools` | `query` | `count`, `query`, `filters`, `matches` | none | Exact contract |

Optional inputs, concrete JSON types, bounds, descriptions, failure envelopes, access metadata, and
navigation instructions remain losslessly available from `list_capabilities(include_descriptors=true)`.
The descriptor hash binds that full representation; the compatibility projection above separately
prevents a descriptive catalog improvement from masking owner-approved surface drift.
