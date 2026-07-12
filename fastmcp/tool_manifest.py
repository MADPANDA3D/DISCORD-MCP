"""Provider-owned deterministic ToolManifest for the Discord MCP.

Runtime credentials and configuration values never enter descriptor hashes.
FastMCP input schemas are enriched from this canonical metadata so native
``tools/list`` and Portal catalog ingestion cannot silently drift apart.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping


SCHEMA_VERSION = "1.0.0"
SERVICE_ID = "discord"
SERVICE_ALIASES = ("discord-mcp", "discord_mcp", "discord server")
CATALOG_VERSION = "discord-2026.07.12.1"
REPOSITORY_DOCS_URL = "https://github.com/MADPANDA3D/DISCORD-MCP#tools"
GUILD_DOCS = "https://docs.discord.com/developers/resources/guild"
CHANNEL_DOCS = "https://docs.discord.com/developers/resources/channel"
MESSAGE_DOCS = "https://docs.discord.com/developers/resources/message"
WEBHOOK_DOCS = "https://docs.discord.com/developers/resources/webhook"

CONTRACT_TIERS = {"agent_ready", "legacy", "hidden"}
RISK_LEVELS = {"read", "write", "destructive"}
_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class ToolDefinition:
    native_tool_name: str
    aliases: tuple[str, ...]
    title: str
    description: str
    category: str
    annotations: Mapping[str, bool]
    confirmation: Mapping[str, Any]
    documentation_url: str
    navigation_role: str | None
    contract_tier: str
    output_data_description: str
    deprecation: Mapping[str, Any]


def _d(
    name: str,
    title: str,
    category: str,
    use: str,
    effect: str,
    result: str,
    *,
    aliases: tuple[str, ...] = (),
    avoid: str = "",
    read: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
    open_world: bool = True,
    confirm: bool = False,
    docs: str = REPOSITORY_DOCS_URL,
    navigation: str | None = None,
    tier: str = "agent_ready",
) -> ToolDefinition:
    if tier not in CONTRACT_TIERS:
        raise ValueError(f"Unsupported contract tier: {tier}")
    avoid_sentence = f" Do not use it {avoid}." if avoid else ""
    prerequisite = (
        "It requires an authenticated MAD MCP Portal request, but does not contact Discord."
        if not open_world
        else "It requires a configured Discord bot and guild with the relevant permissions."
    )
    return ToolDefinition(
        native_tool_name=name,
        aliases=aliases,
        title=title,
        description=(
            f"Use this when {use}.{avoid_sentence} This {effect} through Discord's API "
            f"and returns {result}. {prerequisite}"
        ),
        category=category,
        annotations={
            "readOnlyHint": read,
            "destructiveHint": destructive,
            "idempotentHint": idempotent,
            "openWorldHint": open_world,
        },
        confirmation={
            "required": confirm,
            "parameter": "confirm" if confirm else None,
            "exactPhrase": "CONFIRM APPLY" if confirm else None,
            "when": (
                "Required when the provider confirmation policy is enabled."
                if confirm
                else "Not required by this tool descriptor."
            ),
        },
        documentation_url=docs,
        navigation_role=navigation,
        contract_tier=tier,
        output_data_description=result,
        deprecation={
            "deprecated": False,
            "since": None,
            "sunsetAt": None,
            "replacedBy": None,
            "message": None,
        },
    )


_DEFINITIONS = (
    _d("get_server_info", "Get Discord Server Information", "server", "you need identity and high-level statistics for the configured server", "reads guild metadata without changing state", "guild identity, owner, creation date, channel/member counts, and boost status", aliases=("server_info", "guild_info"), read=True, idempotent=True, docs=GUILD_DOCS),
    _d("discord_health_check", "Check Discord Provider Health", "configuration", "you need a live permission and readiness check for the configured guild", "reads bot, guild, channel, permission, and rate-limit state", "a red/yellow/green report, safe configuration flags, warnings, and capabilities", aliases=("health_check", "check_discord_health"), read=True, idempotent=True, docs=GUILD_DOCS),
    _d("discord_ack", "Send Discord Acknowledgement", "messages", "you need to post a short standardized acknowledgement in an allowed channel", "creates one guild message", "the channel ID, created message ID, and jump URL", aliases=("acknowledge", "send_ack"), docs=MESSAGE_DOCS),
    _d("send_message", "Send Discord Message", "messages", "you need to send text, an embed, or one attachment to an allowed channel", "creates one or more messages and can create a continuation thread", "message/channel/thread IDs, jump URL, attachment metadata, and a safe delivery plan", aliases=("post_message", "discord_send_message"), avoid="for DMs or webhook delivery; use their dedicated tools", confirm=True, docs=MESSAGE_DOCS),
    _d("discord_smoke_test", "Run Discord Write Smoke Test", "operations", "an operator needs an end-to-end write diagnostic", "may send, edit, read back, and delete a test message", "step-by-step health, write, read-back, and cleanup evidence", aliases=("smoke_test",), avoid="for routine health checks because it changes state", destructive=True, confirm=True, tier="legacy"),
    _d("discord_job_submit", "Submit Legacy Discord Job", "operations", "a legacy automation must run a supported action asynchronously", "queues behavior selected through a generic action field", "a task ID, queued status, and selected action", aliases=("submit_job",), avoid="when a typed direct tool is available", tier="legacy"),
    _d("discord_job_status", "Get Legacy Discord Job Status", "operations", "you need status or an optional result for discord_job_submit", "reads the in-memory legacy job record without changing Discord", "job status, timing, action, and an optional result or error", aliases=("job_status",), read=True, idempotent=True, open_world=False, tier="legacy"),
    _d("edit_message", "Edit Discord Message", "messages", "you need to replace an existing message's content", "overwrites a message after admin and confirmation checks", "message/channel IDs, updated content, jump URL, or a dry-run plan", aliases=("update_message",), destructive=True, confirm=True, docs=MESSAGE_DOCS),
    _d("delete_message", "Delete Discord Message", "messages", "you need to permanently remove an existing message", "deletes a message after admin and confirmation checks", "deleted message/channel IDs or a dry-run plan", aliases=("remove_message",), destructive=True, confirm=True, docs=MESSAGE_DOCS),
    _d("read_messages", "Read Discord Messages", "messages", "you need recent messages from one readable channel", "reads channel history without changing state", "message IDs, authors, timestamps, content, embeds, attachments, reactions, and pagination context", aliases=("list_messages", "get_messages"), read=True, idempotent=True, docs=MESSAGE_DOCS),
    _d("search_messages", "Search Discord Messages", "messages", "you need history filtered by text, author, date, link, file, or thread", "reads matching channel history without changing state", "matching messages, applied filters, counts, and pagination context", aliases=("find_messages", "message_search"), read=True, idempotent=True, docs=MESSAGE_DOCS),
    _d("analyze_attachment", "Analyze Discord Image Attachment", "messages", "you need OCR or visual analysis of an attached image", "reads the attachment and sends it to the configured OpenAI vision endpoint", "attachment metadata and extracted or described text", aliases=("ocr_attachment", "describe_attachment"), avoid="for non-images or when vision is not configured", read=True, idempotent=True, docs=MESSAGE_DOCS),
    _d("list_threads", "List Discord Threads", "threads", "you need active and optionally archived threads under a channel", "reads thread metadata without changing state", "thread IDs, names, archive state, parent channel, and counts", aliases=("get_threads",), read=True, idempotent=True, docs=CHANNEL_DOCS),
    _d("create_thread", "Create Discord Thread", "threads", "you need to start a thread from an existing message", "creates a thread under the specified channel and message", "the new thread ID, name, parent channel, and archive duration", aliases=("start_thread",), confirm=True, docs=CHANNEL_DOCS),
    _d("archive_thread", "Archive Discord Thread", "threads", "you need to close an active thread while preserving history", "changes a thread to archived state", "thread ID, name, and archived state", aliases=("close_thread",), destructive=True, confirm=True, docs=CHANNEL_DOCS),
    _d("unarchive_thread", "Unarchive Discord Thread", "threads", "you need to reopen an archived thread", "changes a thread to active state", "thread ID, name, and archived state", aliases=("reopen_thread",), confirm=True, docs=CHANNEL_DOCS),
    _d("channel_daily_audit", "Audit One Discord Channel Day", "audits", "you need a categorized summary of one channel day", "reads bounded channel history without changing state", "audit totals, classifications, samples, date bounds, and warnings", aliases=("daily_channel_audit",), read=True, idempotent=True, docs=MESSAGE_DOCS),
    _d("daily_audit_job_submit", "Start Discord Daily Audit Job", "audits", "you need a resumable audit across multiple channels", "creates an in-memory audit cursor without changing Discord", "task ID, channel totals, date, timezone, and status", aliases=("submit_daily_audit",), open_world=False),
    _d("daily_audit_job_status", "Get Discord Daily Audit Status", "audits", "you need progress or accumulated results for a daily audit", "reads an in-memory audit record without contacting Discord", "cursor, status, completed counts, timing, and optional results", aliases=("daily_audit_status",), read=True, idempotent=True, open_world=False),
    _d("daily_audit_job_next", "Process Next Discord Audit Channel", "audits", "you need to advance a resumable audit by one channel", "reads one channel and advances the in-memory cursor", "the processed audit, updated cursor, progress, and task status", aliases=("next_daily_audit",), docs=MESSAGE_DOCS),
    _d("add_reaction", "Add Discord Reaction", "reactions", "you need the bot to react to an existing message", "adds one emoji reaction", "channel/message IDs and the applied emoji", aliases=("react_to_message",), confirm=True, docs=MESSAGE_DOCS),
    _d("remove_reaction", "Remove Discord Reaction", "reactions", "you need to remove the bot's reaction from a message", "removes one emoji reaction", "channel/message IDs and the removed emoji", aliases=("unreact_to_message",), destructive=True, confirm=True, docs=MESSAGE_DOCS),
    _d("timeout_member", "Timeout Discord Member", "moderation", "an authorized moderator must temporarily restrict a member", "sets a communication timeout after hierarchy checks", "member ID, expiry, duration, and reason", aliases=("mute_member",), destructive=True, confirm=True, docs=GUILD_DOCS),
    _d("remove_timeout", "Remove Discord Member Timeout", "moderation", "an authorized moderator must restore a timed-out member", "clears the member timeout after hierarchy checks", "member ID and moderation reason", aliases=("untimeout_member", "unmute_member"), confirm=True, docs=GUILD_DOCS),
    _d("kick_member", "Kick Discord Member", "moderation", "an authorized moderator must remove a member without banning re-entry", "removes the member after hierarchy checks", "kicked member ID and reason", aliases=("remove_member",), destructive=True, confirm=True, docs=GUILD_DOCS),
    _d("ban_member", "Ban Discord Member", "moderation", "an authorized moderator must prohibit a user from joining", "bans the member and can delete bounded recent messages", "banned member ID, deleted-message days, and reason", aliases=("block_member",), destructive=True, confirm=True, docs=GUILD_DOCS),
    _d("unban_member", "Unban Discord User", "moderation", "an authorized moderator must allow a banned user to rejoin", "removes a guild ban", "unbanned user ID and reason", aliases=("remove_ban",), confirm=True, docs=GUILD_DOCS),
    _d("add_role", "Add Discord Member Role", "roles", "an authorized operator must grant a configured role", "adds one role after hierarchy checks", "member ID, role ID, and reason", aliases=("grant_role",), destructive=True, confirm=True, docs=GUILD_DOCS),
    _d("remove_role", "Remove Discord Member Role", "roles", "an authorized operator must revoke a configured role", "removes one role after hierarchy checks", "member ID, role ID, and reason", aliases=("revoke_role",), destructive=True, confirm=True, docs=GUILD_DOCS),
    _d("edit_nickname", "Edit Discord Member Nickname", "members", "an authorized operator must set or clear a nickname", "overwrites the guild nickname after hierarchy checks", "member ID, resulting nickname, and reason", aliases=("set_nickname",), destructive=True, confirm=True, docs=GUILD_DOCS),
    _d("get_user_id_by_name", "Find Discord User ID by Name", "members", "you need a member snowflake from a username or display name", "searches guild members without changing state", "matching IDs, usernames, display names, and match quality", aliases=("find_user", "resolve_user_id"), read=True, idempotent=True, docs=GUILD_DOCS),
    _d("send_private_message", "Send Discord Direct Message", "direct_messages", "you need to send a private message and DM access is enabled", "creates one direct message", "recipient/channel/message IDs and jump URL", aliases=("send_dm",), avoid="for guild channels; use send_message", confirm=True, docs=MESSAGE_DOCS),
    _d("edit_private_message", "Edit Discord Direct Message", "direct_messages", "you need to replace a bot-authored direct message", "overwrites an existing direct message", "recipient/channel/message IDs, content, and jump URL", aliases=("edit_dm",), destructive=True, confirm=True, docs=MESSAGE_DOCS),
    _d("delete_private_message", "Delete Discord Direct Message", "direct_messages", "you need to permanently remove a bot-authored direct message", "deletes an existing direct message", "recipient/channel/deleted-message IDs", aliases=("delete_dm",), destructive=True, confirm=True, docs=MESSAGE_DOCS),
    _d("read_private_messages", "Read Discord Direct Messages", "direct_messages", "you need recent DM history and DM access is enabled", "reads direct-message history without changing state", "recipient/channel IDs and recent message records", aliases=("read_dms",), read=True, idempotent=True, docs=MESSAGE_DOCS),
    _d("create_text_channel", "Create Discord Text Channel", "channels", "you need a new text channel in the guild or category", "creates a guild text channel", "new channel ID, name, guild ID, and category ID", aliases=("add_channel",), confirm=True, docs=GUILD_DOCS),
    _d("delete_channel", "Delete Discord Channel", "channels", "you need to permanently remove a channel", "deletes the guild channel", "deleted channel ID, name, and guild ID", aliases=("remove_channel",), destructive=True, confirm=True, docs=CHANNEL_DOCS),
    _d("find_channel", "Find Discord Channel", "channels", "you need a channel ID from a name", "searches readable guild channels without changing state", "matching IDs, names, types, and category context", aliases=("resolve_channel",), read=True, idempotent=True, docs=GUILD_DOCS),
    _d("list_channels", "List Discord Channels", "channels", "you need the readable channel inventory", "reads guild channels without changing state", "channel IDs, names, types, positions, and category context", aliases=("get_channels",), read=True, idempotent=True, docs=GUILD_DOCS),
    _d("create_category", "Create Discord Category", "categories", "you need a new category to organize channels", "creates a channel category", "new category ID, name, and guild ID", aliases=("add_category",), confirm=True, docs=GUILD_DOCS),
    _d("delete_category", "Delete Discord Category", "categories", "you need to remove a category after handling child channels", "deletes the category object", "deleted category ID, name, and guild ID", aliases=("remove_category",), destructive=True, confirm=True, docs=CHANNEL_DOCS),
    _d("find_category", "Find Discord Category", "categories", "you need a category ID from its name", "searches guild categories without changing state", "matching category IDs, names, positions, and guild ID", aliases=("resolve_category",), read=True, idempotent=True, docs=GUILD_DOCS),
    _d("list_channels_in_category", "List Discord Category Channels", "categories", "you need readable children of one category", "reads category membership without changing state", "category ID plus channel IDs, names, types, and count", aliases=("category_channels",), read=True, idempotent=True, docs=GUILD_DOCS),
    _d("create_webhook", "Create Discord Webhook", "webhooks", "a legacy private client must create a webhook", "creates a channel webhook", "webhook metadata including a credential-bearing URL", aliases=("add_webhook",), avoid="in agent-core discovery because its legacy output contains a credential", confirm=True, docs=WEBHOOK_DOCS, tier="hidden"),
    _d("delete_webhook", "Delete Discord Webhook", "webhooks", "you need to permanently remove a known webhook by ID", "deletes the webhook", "deleted webhook ID and name", aliases=("remove_webhook",), destructive=True, confirm=True, docs=WEBHOOK_DOCS),
    _d("list_webhooks", "List Discord Webhooks", "webhooks", "a legacy private client needs channel webhooks", "reads channel webhook records", "webhook IDs, names, and credential-bearing URLs", aliases=("get_webhooks",), avoid="in agent-core discovery because its legacy output contains credentials", read=True, idempotent=True, docs=WEBHOOK_DOCS, tier="hidden"),
    _d("send_webhook_message", "Send Discord Webhook Message", "webhooks", "a legacy private client must send through a credential-bearing webhook URL", "creates a webhook message", "created message ID and jump URL", aliases=("post_webhook_message",), avoid="in agent-core discovery; use send_message when possible", confirm=True, docs=WEBHOOK_DOCS, tier="hidden"),
    _d("check_configuration", "Check Discord MCP Configuration", "navigation", "you need to know whether this request has required provider setup", "checks safe configuration presence and policy flags", "readiness, missing field names, capability flags, and warnings without credential values", aliases=("configuration_status",), read=True, idempotent=True, open_world=False, navigation="configuration"),
    _d("list_capabilities", "List Discord MCP Capabilities", "navigation", "you need capability groups, counts, or the lossless ToolManifest", "reads the provider-owned catalog from memory", "catalog identity, counts, categories, and optionally every descriptor", aliases=("get_manifest", "list_tools_manifest"), read=True, idempotent=True, open_world=False, navigation="catalog"),
    _d("get_endpoint_coverage", "Get Discord Endpoint Coverage", "navigation", "you need to understand covered and excluded Discord API areas", "reads the maintained coverage matrix from memory", "official links, coverage state, tools, configuration, and gap reasons", aliases=("endpoint_coverage",), read=True, idempotent=True, open_world=False, navigation="coverage"),
    _d("get_tool_usage", "Get Discord Tool Usage", "navigation", "you need the lossless descriptor for one tool", "reads one canonical descriptor from memory", "complete parameters, output, risk, confirmation, and follow-up guidance", aliases=("describe_tool", "tool_reference"), read=True, idempotent=True, open_world=False, navigation="reference"),
    _d("find_tools", "Find Discord Tools", "navigation", "you need ranked tools for a multi-word task or alias", "searches the deterministic catalog in memory", "compact ranked matches with risk, category, tier, and next action", aliases=("search_tools", "discover_tools"), read=True, idempotent=True, open_world=False, navigation="discovery"),
)

TOOL_DEFINITIONS: Mapping[str, ToolDefinition] = {
    definition.native_tool_name: definition for definition in _DEFINITIONS
}
if len(TOOL_DEFINITIONS) != len(_DEFINITIONS):
    raise RuntimeError("Discord ToolManifest contains duplicate native tool names.")


PARAMETER_DESCRIPTIONS = {
    "action": "Exact supported native tool name to execute asynchronously.",
    "after_message_id": "Optional Discord message snowflake; return records created after it.",
    "attachment": "Legacy alias for file. Provide one object with base64, url, or path plus optional filename and content_type, or one source string.",
    "attachment_index": "Zero-based decimal index of the image attachment on the message.",
    "author_id": "Optional Discord user snowflake used to filter message authors.",
    "auto_archive_duration": "Thread auto-archive duration in minutes: 60, 1440, 4320, or 10080 when supported.",
    "before_message_id": "Optional Discord message snowflake; return records created before it.",
    "category": "Optional exact manifest category filter; empty includes every category.",
    "category_id": "Discord category snowflake identifying the parent category or target category.",
    "category_name": "Human-readable category name used for normalized exact matching.",
    "channel_id": "Discord channel snowflake; empty uses the primary channel only where documented.",
    "channel_ids": "Ordered Discord channel snowflakes for the resumable daily audit.",
    "channel_name": "Human-readable channel name used for normalized exact matching.",
    "confirm": "Exact phrase CONFIRM APPLY when confirmation policy is enabled; never place credentials here.",
    "count": "Maximum records to return as a positive decimal string within the tool's limit.",
    "date": "Calendar date in YYYY-MM-DD; empty selects the current date in the chosen timezone.",
    "date_from": "Optional inclusive lower date/time bound for message search.",
    "date_to": "Optional inclusive upper date/time bound for message search.",
    "debug": "When true, include safe connection diagnostics without credential values.",
    "delete_message_days": "Recent message days to delete with a ban, as a decimal string from 0 through 7.",
    "dry_run": "When true, validate and return the plan without changing Discord state.",
    "duration_minutes": "Positive timeout minutes as a decimal string, bounded by Discord's limit.",
    "embed_color": "Optional embed color as a base-10 integer string from 0 through 16777215.",
    "embed_description": "Optional embed body text; Discord size limits are validated.",
    "embed_title": "Optional embed title, limited to Discord's 256-character title limit.",
    "emoji": "Unicode emoji or Discord custom-emoji notation to add or remove.",
    "file": "Optional attachment object or source string; use exactly one attachment source field.",
    "file_base64": "Base64 or data-URL bytes for one attachment within the configured decoded-size limit.",
    "file_content_type": "Optional attachment MIME type when it cannot be safely inferred.",
    "file_name": "Safe attachment filename including extension.",
    "file_path": "Allowlisted server-local path; hosted agents should use file_base64 or file_url.",
    "file_url": "Public HTTP(S) attachment URL; private and loopback destinations are rejected.",
    "guild_id": "Optional Discord guild snowflake override; otherwise uses request-scoped configuration.",
    "has_file": "When true, restrict search to messages with an attachment.",
    "has_link": "When true, restrict search to messages containing an HTTP(S) link.",
    "include_admin": "When true, smoke testing may edit and delete its test message if admin tools are enabled.",
    "include_archived": "When true, include archived threads in addition to active threads.",
    "include_descriptors": "When true, include all ordered lossless descriptors for Portal ingestion.",
    "include_legacy": "When true, include legacy matches; hidden tools remain excluded.",
    "include_result": "When true, include a completed legacy job result.",
    "include_results": "When true, include accumulated audit results.",
    "include_threads": "When true, include eligible thread messages.",
    "include_timestamp": "When true, append a UTC timestamp to the acknowledgement.",
    "limit": "Maximum records or matches, expressed as a positive decimal string unless typed as integer.",
    "message": "Message text to send; do not include credentials or confirmation tokens.",
    "message_id": "Discord message snowflake identifying the target message.",
    "mode": "Attachment analysis mode: ocr extracts text; describe summarizes visual content.",
    "name": "Human-readable name for the Discord resource being created.",
    "new_message": "Replacement text for an existing bot-authored message.",
    "nickname": "New guild nickname; an empty string clears the nickname.",
    "params": "JSON arguments for the selected legacy action; prefer its typed direct tool.",
    "prompt": "Optional task-specific instruction appended to the safe vision prompt.",
    "query": "Punctuation-normalized multi-token search text; all terms must match.",
    "reason": "Optional concise audit-log reason for the change.",
    "risk": "Optional risk filter: read, write, or destructive.",
    "role_id": "Discord role snowflake; hierarchy and protected-role policy still apply.",
    "task_id": "Opaque task identifier returned by the matching submit tool.",
    "thread_id": "Discord thread snowflake identifying the target thread.",
    "thread_if_split": "When true, create a continuation thread if splitting is required and allowed.",
    "thread_name": "Optional continuation-thread name for split delivery.",
    "timezone_name": "IANA timezone used for daily boundaries; empty uses configured audit timezone.",
    "tool_name": "Canonical tool name or exact compatibility alias from this manifest.",
    "user_id": "Discord user snowflake identifying the member or DM recipient.",
    "username": "Discord username, global name, or guild display name to resolve.",
    "webhook_id": "Discord webhook snowflake identifying the webhook to delete.",
    "webhook_url": "Credential-bearing webhook URL; use only in hidden legacy/private mode and never log it.",
}

FILE_OBJECT_SCHEMA = {
    "type": "object",
    "description": "One attachment source object.",
    "properties": {
        "base64": {"type": "string", "description": "Base64 or data-URL attachment bytes."},
        "url": {"type": "string", "format": "uri", "description": "Public HTTP(S) attachment URL."},
        "path": {"type": "string", "description": "Allowlisted server-local attachment path."},
        "filename": {"type": "string", "description": "Attachment filename including extension."},
        "content_type": {"type": "string", "description": "Optional attachment MIME type."},
    },
    "additionalProperties": False,
}

META_SCHEMA = {
    "type": "object",
    "description": "Safe execution metadata containing no credentials or raw headers.",
    "required": ["duration_ms", "rate_limit", "warnings"],
    "properties": {
        "duration_ms": {"type": "integer", "minimum": 0},
        "rate_limit": {"type": "object", "additionalProperties": True},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "request_id": {"type": "string"},
        "guild_id": {"type": "string"},
        "channel_id": {"type": "string"},
        "thread_id": {"type": "string"},
    },
    "additionalProperties": True,
}

ERROR_SCHEMA = {
    "type": "object",
    "required": ["type", "message"],
    "properties": {
        "type": {"type": "string"},
        "message": {"type": "string"},
        "required_perms": {"type": "array", "items": {"type": "string"}},
        "discord_error_code": {"type": "integer"},
        "diagnostics": {"type": "object", "additionalProperties": True},
    },
    "additionalProperties": False,
}

# Declared provider data fields keep output schemas useful to agents while
# allowing additive Discord/provider metadata for backwards compatibility.
OUTPUT_DATA_FIELDS = {
    "get_server_info": ("name", "id", "owner", "created_on", "member_count", "channels", "boosts"),
    "discord_health_check": ("status", "healthy", "warnings", "bot", "guild", "discord_config", "capabilities", "last_successful_api_at"),
    "discord_ack": ("channel_id", "message_id", "jump_url"),
    "send_message": ("dry_run", "channel_id", "message_id", "sent_message_ids", "thread_id", "jump_url", "planned_parts", "attachments", "diagnostics"),
    "discord_smoke_test": ("ok", "steps", "message_id", "channel_id", "duration_ms"),
    "discord_job_submit": ("task_id", "status", "action"),
    "discord_job_status": ("task_id", "action", "status", "created_at", "started_at", "finished_at", "error", "result"),
    "edit_message": ("dry_run", "channel_id", "message_id", "jump_url", "diagnostics"),
    "delete_message": ("dry_run", "channel_id", "message_id", "diagnostics"),
    "read_messages": ("channel_id", "count", "before_message_id", "after_message_id", "messages"),
    "search_messages": ("channel_id", "count", "limit", "messages", "filters"),
    "analyze_attachment": ("mode", "text", "model", "attachment", "message_id", "channel_id", "usage"),
    "list_threads": ("channel_id", "count", "threads"),
    "create_thread": ("thread_id", "name", "message_id"),
    "archive_thread": ("thread_id", "archived"),
    "unarchive_thread": ("thread_id", "archived"),
    "channel_daily_audit": ("message_count", "unique_authors", "top_authors", "links_topN", "attachments_count", "highlights", "blockers", "decisions", "questions", "channel_id", "channel_name", "date", "timezone", "range_utc", "include_threads"),
    "daily_audit_job_submit": ("task_id", "status", "total_channels"),
    "daily_audit_job_status": ("task_id", "status", "created_at", "finished_at", "date", "timezone", "total_channels", "completed_count", "remaining_count", "next_channel_id", "error", "results"),
    "daily_audit_job_next": ("channel_id", "channel_result", "job", "task_id", "status", "completed_count", "remaining_count", "results"),
    "add_reaction": ("channel_id", "message_id", "jump_url"),
    "remove_reaction": ("channel_id", "message_id", "jump_url"),
    "timeout_member": ("user_id", "timeout_until", "duration_minutes", "reason"),
    "remove_timeout": ("user_id", "timeout_removed", "reason"),
    "kick_member": ("user_id", "kicked", "reason"),
    "ban_member": ("user_id", "banned", "delete_message_days", "reason"),
    "unban_member": ("user_id", "unbanned", "reason"),
    "add_role": ("user_id", "role_id", "role_name", "added", "reason"),
    "remove_role": ("user_id", "role_id", "role_name", "removed", "reason"),
    "edit_nickname": ("user_id", "nickname", "cleared", "reason"),
    "get_user_id_by_name": ("user_id", "username"),
    "send_private_message": ("user_id", "message_id", "jump_url"),
    "edit_private_message": ("user_id", "message_id", "jump_url"),
    "delete_private_message": ("user_id", "message_id"),
    "read_private_messages": ("count", "messages"),
    "create_text_channel": ("channel_id", "name", "category_id", "category_name"),
    "delete_channel": ("channel_id", "name", "type"),
    "find_channel": ("count", "channels"),
    "list_channels": ("count", "channels"),
    "create_category": ("category_id", "name"),
    "delete_category": ("category_id", "name"),
    "find_category": ("count", "categories"),
    "list_channels_in_category": ("count", "channels"),
    "create_webhook": ("webhook_id", "name", "url"),
    "delete_webhook": ("webhook_id", "name"),
    "list_webhooks": ("count", "webhooks"),
    "send_webhook_message": ("message_id", "jump_url"),
    "check_configuration": ("ready", "missing", "configuration", "capabilities"),
    "list_capabilities": ("schemaVersion", "serviceId", "serviceAliases", "catalogVersion", "buildSha", "descriptorHash", "counts", "categories", "tools", "descriptorsIncluded", "nextAction"),
    "get_endpoint_coverage": ("serviceId", "catalogVersion", "retrievedAt", "filter", "count", "coverage"),
    "get_tool_usage": ("descriptor", "nextAction"),
    "find_tools": ("query", "filters", "count", "matches"),
}

_BOOLEAN_OUTPUT_FIELDS = {
    "added", "archived", "banned", "cleared", "descriptorsIncluded", "dry_run",
    "healthy", "include_threads", "kicked", "ok", "ready", "removed",
    "timeout_removed", "unbanned",
}
_INTEGER_OUTPUT_FIELDS = {
    "attachments_count", "completed_count", "count", "delete_message_days",
    "duration_minutes", "duration_ms", "limit", "member_count", "message_count",
    "planned_parts", "remaining_count", "total_channels", "unique_authors",
}
_ARRAY_OUTPUT_FIELDS = {
    "attachments", "blockers", "categories", "channels", "coverage", "decisions",
    "highlights", "links_topN", "matches", "messages", "missing", "questions",
    "sent_message_ids", "serviceAliases", "steps", "threads", "tools", "top_authors",
    "warnings",
}
_OBJECT_OUTPUT_FIELDS = {
    "attachment", "boosts", "bot", "capabilities", "channel_result", "configuration",
    "counts", "descriptor", "diagnostics", "discord_config", "filters", "guild", "job",
    "nextAction", "range_utc", "results", "usage",
}

ENDPOINT_COVERAGE = (
    {"feature": "guild-metadata", "documentationUrl": GUILD_DOCS, "status": "partial", "configuration": ["Discord bot token", "guild ID", "GUILDS intent"], "tools": ["get_server_info", "discord_health_check"], "notes": "Covers guild identity, counts, bot permissions, and readiness; guild-settings mutation is excluded."},
    {"feature": "channels-and-categories", "documentationUrl": CHANNEL_DOCS, "status": "partial", "configuration": ["Discord bot token", "guild ID", "channel policy"], "tools": ["create_text_channel", "delete_channel", "find_channel", "list_channels", "create_category", "delete_category", "find_category", "list_channels_in_category"], "notes": "Covers text-channel/category inventory and lifecycle; voice, stage, forum, permissions, invites, and positions are not exposed."},
    {"feature": "messages-and-reactions", "documentationUrl": MESSAGE_DOCS, "status": "partial", "configuration": ["Discord bot token", "guild ID", "MESSAGE_CONTENT intent", "channel policy"], "tools": ["discord_ack", "send_message", "edit_message", "delete_message", "read_messages", "search_messages", "analyze_attachment", "add_reaction", "remove_reaction"], "notes": "Covers bot messages, history, filters, attachments, and bot reactions; pins, polls, crossposts, bulk delete, typing, and interactions are not exposed."},
    {"feature": "threads", "documentationUrl": CHANNEL_DOCS, "status": "partial", "configuration": ["Discord bot token", "guild ID", "thread permissions"], "tools": ["list_threads", "create_thread", "archive_thread", "unarchive_thread"], "notes": "Covers message-based creation and archive lifecycle; forum posts, membership, and standalone private threads are not exposed."},
    {"feature": "members-roles-and-moderation", "documentationUrl": GUILD_DOCS, "status": "partial", "configuration": ["Discord bot token", "guild ID", "GUILD_MEMBERS intent", "moderation permissions"], "tools": ["get_user_id_by_name", "timeout_member", "remove_timeout", "kick_member", "ban_member", "unban_member", "add_role", "remove_role", "edit_nickname"], "notes": "Covers targeted lookup and guarded moderation; bulk inventory, prune, voice mutation, role lifecycle, and verification are not exposed."},
    {"feature": "direct-messages", "documentationUrl": CHANNEL_DOCS, "status": "partial", "configuration": ["Discord bot token", "DM enabled policy"], "tools": ["send_private_message", "edit_private_message", "delete_private_message", "read_private_messages"], "notes": "Covers one-to-one bot DMs when enabled; group-DM recipient management is excluded."},
    {"feature": "webhooks", "documentationUrl": WEBHOOK_DOCS, "status": "legacy_hidden", "configuration": ["Discord bot token", "guild ID", "MANAGE_WEBHOOKS permission"], "tools": ["create_webhook", "delete_webhook", "list_webhooks", "send_webhook_message"], "notes": "Legacy create/list outputs expose credential-bearing URLs, so affected tools are hidden pending a credential-safe redesign."},
    {"feature": "audits-and-jobs", "documentationUrl": REPOSITORY_DOCS_URL, "status": "provider_extension", "configuration": ["Discord bot token", "guild ID", "channel read policy"], "tools": ["channel_daily_audit", "daily_audit_job_submit", "daily_audit_job_status", "daily_audit_job_next", "discord_job_submit", "discord_job_status"], "notes": "Provider orchestration built on message reads; the generic legacy dispatcher is not agent-ready."},
    {"feature": "oauth-app-commands-and-gateway-management", "documentationUrl": "https://docs.discord.com/developers/platform/oauth2-and-permissions", "status": "intentionally_not_exposed", "configuration": [], "tools": [], "notes": "Portal owns client auth; installation, OAuth grants, command registration, and raw gateway lifecycle are outside this contract."},
    {"feature": "other-discord-resources", "documentationUrl": "https://docs.discord.com/developers/reference", "status": "intentionally_not_exposed", "configuration": [], "tools": [], "notes": "Invites, emojis, stickers, events, automod, stages, templates, entitlements, SKUs, soundboards, and monetization are not implemented."},
)


def normalize_terms(value: Any) -> tuple[str, ...]:
    """Normalize punctuation, snake/camel case, and repeated whitespace."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
    return tuple(_TOKEN_PATTERN.findall(text.lower().replace("_", " ")))


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def descriptor_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def get_build_sha(value: str | None = None) -> str:
    """Return only a commit hash, never arbitrary environment content."""
    candidate = value if value is not None else os.getenv("MCP_BUILD_SHA", "")
    candidate = str(candidate or "").strip()
    return candidate.lower() if _SHA_PATTERN.fullmatch(candidate) else "unknown"


def get_tool_definition(tool_name: str) -> ToolDefinition | None:
    return TOOL_DEFINITIONS.get(tool_name)


def is_navigation_tool(tool_name: str) -> bool:
    definition = get_tool_definition(tool_name)
    return bool(definition and definition.navigation_role)


def runtime_registration(tool_name: str) -> dict[str, Any]:
    """Return the metadata used by the native FastMCP descriptor."""
    definition = get_tool_definition(tool_name)
    if definition is None:
        raise RuntimeError(f"Tool {tool_name!r} is missing from the Discord ToolManifest.")
    return {
        "title": definition.title,
        "description": definition.description,
        "annotations": dict(definition.annotations),
        "meta": {
            "madpanda": {
                "serviceId": SERVICE_ID,
                "category": definition.category,
                "tier": definition.contract_tier,
                "catalogVersion": CATALOG_VERSION,
                "navigationRole": definition.navigation_role,
            }
        },
        # Navigation results need MCP structuredContent for Portal ingestion.
        "structured_output": bool(definition.navigation_role),
    }


def _parameter_description(tool_name: str, parameter_name: str) -> str:
    return PARAMETER_DESCRIPTIONS.get(
        parameter_name,
        f"Input parameter {parameter_name} for {tool_name}; use the documented Discord value format.",
    )


def enrich_input_schema(tool_name: str, input_schema: Mapping[str, Any]) -> dict[str, Any]:
    """Add lossless semantics without altering FastMCP argument validation."""
    schema = copy.deepcopy(dict(input_schema))
    schema["title"] = f"{tool_name} input"
    schema["description"] = f"Validated input for the Discord MCP {tool_name} tool."
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
        schema["properties"] = properties
    for parameter_name, parameter_schema in properties.items():
        if not isinstance(parameter_schema, dict):
            continue
        parameter_schema["description"] = _parameter_description(tool_name, parameter_name)
        if parameter_name in {"file", "attachment"}:
            any_of = parameter_schema.get("anyOf")
            if isinstance(any_of, list):
                for index, branch in enumerate(any_of):
                    if isinstance(branch, dict) and branch.get("type") == "object":
                        any_of[index] = copy.deepcopy(FILE_OBJECT_SCHEMA)
                        break
        if parameter_name == "mode":
            parameter_schema["enum"] = ["ocr", "describe"]
        elif tool_name == "discord_job_submit" and parameter_name == "action":
            parameter_schema["enum"] = [
                "channel_daily_audit",
                "discord_ack",
                "discord_health_check",
                "discord_smoke_test",
                "find_channel",
                "get_server_info",
                "list_channels",
                "list_threads",
                "read_messages",
                "search_messages",
                "send_message",
            ]
        elif tool_name == "find_tools" and parameter_name == "risk":
            parameter_schema["enum"] = ["", "read", "write", "destructive"]
    return schema


def _output_field_schema(tool_name: str, field_name: str) -> dict[str, Any]:
    description = f"Provider data field {field_name} returned by {tool_name}."
    if field_name in _BOOLEAN_OUTPUT_FIELDS:
        return {"type": ["boolean", "null"], "description": description}
    if field_name in _INTEGER_OUTPUT_FIELDS:
        return {"type": ["integer", "null"], "description": description}
    if field_name in _ARRAY_OUTPUT_FIELDS:
        return {"type": "array", "items": {}, "description": description}
    if field_name in _OBJECT_OUTPUT_FIELDS:
        return {"type": ["object", "null"], "additionalProperties": True, "description": description}
    if field_name in {"error", "result"}:
        return {"description": description}
    return {"type": ["string", "null"], "description": description}


def _provider_output_schema(tool_name: str, data_description: str) -> dict[str, Any]:
    fields = OUTPUT_DATA_FIELDS.get(tool_name)
    if not fields:
        raise RuntimeError(f"Tool {tool_name!r} is missing declared output data fields.")
    success = {
        "type": "object",
        "required": ["ok", "data", "meta"],
        "properties": {
            "ok": {"const": True},
            "data": {
                "type": "object",
                "description": data_description,
                "properties": {
                    field_name: _output_field_schema(tool_name, field_name)
                    for field_name in fields
                },
                "additionalProperties": True,
            },
            "meta": copy.deepcopy(META_SCHEMA),
        },
        "additionalProperties": False,
    }
    failure = {
        "type": "object",
        "required": ["ok", "error", "meta"],
        "properties": {
            "ok": {"const": False},
            "error": copy.deepcopy(ERROR_SCHEMA),
            "meta": copy.deepcopy(META_SCHEMA),
        },
        "additionalProperties": False,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Discord MCP provider result",
        "description": "Normalized success or semantic-error response; ok=false is a failed call.",
        "oneOf": [success, failure],
    }


def _registered_tool_mapping(registered_tools: Any) -> Mapping[str, Any]:
    if isinstance(registered_tools, Mapping):
        return registered_tools
    for attribute in ("_tools", "tools"):
        candidate = getattr(registered_tools, attribute, None)
        if isinstance(candidate, Mapping):
            return candidate
    raise TypeError("Registered tools must be a mapping or FastMCP ToolManager.")


def _input_schema_for(tool: Any) -> Mapping[str, Any]:
    if isinstance(tool, Mapping):
        candidate = tool.get("inputSchema") or tool.get("parameters") or tool
    else:
        candidate = getattr(tool, "parameters", None)
    if not isinstance(candidate, Mapping):
        raise TypeError("Registered tool does not expose an input JSON schema.")
    return candidate


def build_tool_manifest(
    registered_tools: Any,
    *,
    build_sha: str | None = None,
) -> dict[str, Any]:
    """Build the ordered catalog and hashes, excluding all runtime config."""
    registered = _registered_tool_mapping(registered_tools)
    expected = set(TOOL_DEFINITIONS)
    actual = set(registered)
    if actual != expected:
        raise RuntimeError(
            "Discord ToolManifest drift: "
            f"missing registered tools={sorted(expected - actual)}; "
            f"unexpected registered tools={sorted(actual - expected)}."
        )

    canonical_descriptors = []
    for definition in _DEFINITIONS:
        name = definition.native_tool_name
        canonical_descriptors.append(
            {
                "serviceId": SERVICE_ID,
                "nativeToolName": name,
                "aliases": list(definition.aliases),
                "title": definition.title,
                "description": definition.description,
                "category": definition.category,
                "deprecation": dict(definition.deprecation),
                "inputSchema": enrich_input_schema(name, _input_schema_for(registered[name])),
                "outputSchema": _provider_output_schema(
                    name, definition.output_data_description
                ),
                "annotations": dict(definition.annotations),
                "confirmation": dict(definition.confirmation),
                "documentationUrl": definition.documentation_url,
                "navigationRole": definition.navigation_role,
                "catalogVersion": CATALOG_VERSION,
                "tier": definition.contract_tier,
            }
        )

    tools = []
    for descriptor in canonical_descriptors:
        materialized = copy.deepcopy(descriptor)
        materialized["descriptorHash"] = descriptor_hash(descriptor)
        tools.append(materialized)

    counts = {
        "raw": len(tools),
        "agentReady": sum(tool["tier"] == "agent_ready" for tool in tools),
        "legacy": sum(tool["tier"] == "legacy" for tool in tools),
        "hidden": sum(tool["tier"] == "hidden" for tool in tools),
        "documented": len(tools),
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "serviceId": SERVICE_ID,
        "serviceAliases": list(SERVICE_ALIASES),
        "catalogVersion": CATALOG_VERSION,
        "buildSha": get_build_sha(build_sha),
        "descriptorHash": descriptor_hash(canonical_descriptors),
        "counts": counts,
        "tools": tools,
    }


def manifest_categories(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    categories: dict[str, dict[str, int]] = {}
    for tool in manifest.get("tools", []):
        category = tool["category"]
        tier = tool["tier"]
        bucket = categories.setdefault(
            category, {"raw": 0, "agentReady": 0, "legacy": 0, "hidden": 0}
        )
        bucket["raw"] += 1
        bucket["agentReady" if tier == "agent_ready" else tier] += 1
    return [{"category": name, **counts} for name, counts in sorted(categories.items())]


def _risk_for(tool: Mapping[str, Any]) -> str:
    annotations = tool["annotations"]
    if annotations["destructiveHint"]:
        return "destructive"
    if annotations["readOnlyHint"]:
        return "read"
    return "write"


def find_manifest_tools(
    manifest: Mapping[str, Any],
    query: str,
    *,
    category: str = "",
    risk: str = "",
    limit: int = 8,
    include_legacy: bool = False,
) -> list[dict[str, Any]]:
    query_terms = normalize_terms(query)
    if not query_terms:
        raise ValueError("query must contain at least one letter or number.")
    category_filter = " ".join(normalize_terms(category))
    risk_filter = " ".join(normalize_terms(risk))
    if risk_filter and risk_filter not in RISK_LEVELS:
        raise ValueError("risk must be read, write, or destructive.")
    bounded_limit = max(1, min(int(limit), 25))

    matches = []
    for index, tool in enumerate(manifest.get("tools", [])):
        tier = tool["tier"]
        if tier == "hidden" or (tier == "legacy" and not include_legacy):
            continue
        if category_filter and " ".join(normalize_terms(tool["category"])) != category_filter:
            continue
        tool_risk = _risk_for(tool)
        if risk_filter and tool_risk != risk_filter:
            continue

        name_terms = normalize_terms(tool["nativeToolName"])
        alias_terms = tuple(term for alias in tool["aliases"] for term in normalize_terms(alias))
        title_terms = normalize_terms(tool["title"])
        category_terms = normalize_terms(tool["category"])
        description_terms = normalize_terms(tool["description"])
        searchable = set(name_terms + alias_terms + title_terms + category_terms + description_terms)
        if not all(term in searchable for term in query_terms):
            continue
        exact_name = query_terms == name_terms
        exact_alias = any(query_terms == normalize_terms(alias) for alias in tool["aliases"])
        score = (
            (1000 if exact_name else 0)
            + (900 if exact_alias else 0)
            + sum(80 for term in query_terms if term in name_terms)
            + sum(60 for term in query_terms if term in alias_terms)
            + sum(30 for term in query_terms if term in title_terms)
            + sum(15 for term in query_terms if term in category_terms)
            + sum(5 for term in query_terms if term in description_terms)
        )
        matches.append(
            (
                -score,
                index,
                {
                    "serviceId": SERVICE_ID,
                    "toolName": tool["nativeToolName"],
                    "title": tool["title"],
                    "category": tool["category"],
                    "risk": tool_risk,
                    "tier": tier,
                    "summary": tool["description"],
                    "score": score,
                    "nextAction": {
                        "toolName": "get_tool_usage",
                        "arguments": {"tool_name": tool["nativeToolName"]},
                    },
                },
            )
        )
    matches.sort(key=lambda item: (item[0], item[1], item[2]["toolName"]))
    return [item[2] for item in matches[:bounded_limit]]


def find_tool_descriptor(manifest: Mapping[str, Any], tool_name: str) -> dict[str, Any] | None:
    target = normalize_terms(tool_name)
    if not target:
        return None
    for tool in manifest.get("tools", []):
        if target == normalize_terms(tool["nativeToolName"]):
            return copy.deepcopy(tool)
        if any(target == normalize_terms(alias) for alias in tool["aliases"]):
            return copy.deepcopy(tool)
    return None


def filter_endpoint_coverage(feature: str = "") -> list[dict[str, Any]]:
    terms = normalize_terms(feature)
    if not terms:
        return copy.deepcopy(list(ENDPOINT_COVERAGE))
    matches = []
    for item in ENDPOINT_COVERAGE:
        searchable = set(
            normalize_terms(item["feature"])
            + normalize_terms(item["status"])
            + normalize_terms(item["notes"])
            + tuple(term for tool in item["tools"] for term in normalize_terms(tool))
        )
        if all(term in searchable for term in terms):
            matches.append(copy.deepcopy(item))
    return matches
