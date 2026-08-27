"""Bounded Discord REST v10 server-management operation registry.

The registry is intentionally explicit. Agents select a reviewed action instead
of supplying arbitrary methods or paths, which keeps bot-token access scoped to
stable guild-administration endpoints and lets the MCP descriptor separate read,
write, and destructive behavior before execution.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote

import aiohttp


DISCORD_API_BASE = "https://discord.com/api/v10"
MAX_RESPONSE_BYTES = 48 * 1024
MAX_STRING_LENGTH = 4_096
MAX_COLLECTION_ITEMS = 100
MAX_RETRY_AFTER_SECONDS = 15.0
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_SNOWFLAKE = re.compile(r"^[0-9]{15,22}$")
_SENSITIVE_KEYS = {
    "authorization",
    "token",
    "webhook_token",
}


@dataclass(frozen=True)
class DiscordAdminOperation:
    action: str
    method: str
    path: str
    risk: str
    resource: str
    permission: str | None = None
    query_fields: tuple[str, ...] = ()
    max_limit: int = 100
    body_fields: tuple[str, ...] = ()
    required_body_fields: tuple[str, ...] = ()
    send_empty_json: bool = False
    audit_reason: bool = False
    body_mode: str = "json"
    member_guard: bool = False
    role_guard: bool = False

    @property
    def required_identifiers(self) -> tuple[str, ...]:
        return tuple(_PLACEHOLDER.findall(self.path))


def _operation(
    action: str,
    method: str,
    path: str,
    risk: str,
    resource: str,
    *,
    permission: str | None = None,
    query: tuple[str, ...] = (),
    max_limit: int = 100,
    body: tuple[str, ...] = (),
    required_body: tuple[str, ...] = (),
    send_empty_json: bool = False,
    reason: bool = False,
    body_mode: str = "json",
    member_guard: bool = False,
    role_guard: bool = False,
) -> DiscordAdminOperation:
    return DiscordAdminOperation(
        action=action,
        method=method,
        path=path,
        risk=risk,
        resource=resource,
        permission=permission,
        query_fields=query,
        max_limit=max_limit,
        body_fields=body,
        required_body_fields=required_body,
        send_empty_json=send_empty_json,
        audit_reason=reason,
        body_mode=body_mode,
        member_guard=member_guard,
        role_guard=role_guard,
    )


_OPERATIONS = (
    # Guild, member, role, moderation, and community configuration reads.
    _operation("get_guild", "GET", "/guilds/{guild_id}", "read", "guild", query=("with_counts",)),
    _operation("get_guild_preview", "GET", "/guilds/{guild_id}/preview", "read", "guild"),
    _operation("list_guild_channels", "GET", "/guilds/{guild_id}/channels", "read", "channel"),
    _operation("list_active_threads", "GET", "/guilds/{guild_id}/threads/active", "read", "thread"),
    _operation("get_member", "GET", "/guilds/{guild_id}/members/{user_id}", "read", "member"),
    _operation("list_members", "GET", "/guilds/{guild_id}/members", "read", "member", query=("limit", "after")),
    _operation("search_members", "GET", "/guilds/{guild_id}/members/search", "read", "member", query=("query", "limit")),
    _operation("list_bans", "GET", "/guilds/{guild_id}/bans", "read", "moderation", permission="ban_members", query=("limit", "before", "after")),
    _operation("get_ban", "GET", "/guilds/{guild_id}/bans/{user_id}", "read", "moderation", permission="ban_members"),
    _operation("list_roles", "GET", "/guilds/{guild_id}/roles", "read", "role"),
    _operation("get_role", "GET", "/guilds/{guild_id}/roles/{role_id}", "read", "role"),
    _operation("get_role_member_counts", "GET", "/guilds/{guild_id}/roles/member-counts", "read", "role"),
    _operation("get_prune_count", "GET", "/guilds/{guild_id}/prune", "read", "moderation", permission="kick_members", query=("days", "include_roles")),
    _operation("list_guild_voice_regions", "GET", "/guilds/{guild_id}/regions", "read", "voice"),
    _operation("list_guild_invites", "GET", "/guilds/{guild_id}/invites", "read", "invite", permission="manage_guild"),
    _operation("list_integrations", "GET", "/guilds/{guild_id}/integrations", "read", "integration", permission="manage_guild"),
    _operation("get_widget_settings", "GET", "/guilds/{guild_id}/widget", "read", "guild"),
    _operation("get_widget", "GET", "/guilds/{guild_id}/widget.json", "read", "guild"),
    _operation("get_vanity_url", "GET", "/guilds/{guild_id}/vanity-url", "read", "invite", permission="manage_guild"),
    _operation("get_welcome_screen", "GET", "/guilds/{guild_id}/welcome-screen", "read", "guild"),
    _operation("get_onboarding", "GET", "/guilds/{guild_id}/onboarding", "read", "guild", permission="manage_guild"),
    # Channels, permissions, invites, forums, and thread membership reads.
    _operation("get_channel", "GET", "/channels/{channel_id}", "read", "channel"),
    _operation("get_effective_channel_permissions", "GET", "/channels/{channel_id}", "read", "permission", query=("target_type",)),
    _operation("list_channel_invites", "GET", "/channels/{channel_id}/invites", "read", "invite", permission="manage_channels"),
    _operation("get_thread_member", "GET", "/channels/{channel_id}/thread-members/{user_id}", "read", "thread", query=("with_member",)),
    _operation("list_thread_members", "GET", "/channels/{channel_id}/thread-members", "read", "thread", query=("with_member", "after", "limit")),
    _operation("list_public_archived_threads", "GET", "/channels/{channel_id}/threads/archived/public", "read", "thread", query=("before", "limit")),
    _operation("list_private_archived_threads", "GET", "/channels/{channel_id}/threads/archived/private", "read", "thread", query=("before", "limit")),
    _operation("list_joined_private_archived_threads", "GET", "/channels/{channel_id}/users/@me/threads/archived/private", "read", "thread", query=("before", "limit")),
    # Message, reaction, pin, and audit reads.
    _operation("get_channel_messages", "GET", "/channels/{channel_id}/messages", "read", "message", query=("around", "before", "after", "limit")),
    _operation("search_guild_messages", "GET", "/guilds/{guild_id}/messages/search", "read", "message", query=("limit", "offset", "max_id", "min_id", "slop", "content", "channel_id", "author_type", "author_id", "mentions", "mentions_role_id", "mention_everyone", "replied_to_user_id", "replied_to_message_id", "pinned", "has", "embed_type", "embed_provider", "link_hostname", "attachment_filename", "attachment_extension", "sort_by", "sort_order", "include_nsfw"), max_limit=25),
    _operation("get_message", "GET", "/channels/{channel_id}/messages/{message_id}", "read", "message"),
    _operation("get_reactions", "GET", "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}", "read", "reaction", query=("type", "after", "limit")),
    _operation("get_channel_pins", "GET", "/channels/{channel_id}/messages/pins", "read", "message", query=("before", "limit"), max_limit=50),
    _operation("get_audit_log", "GET", "/guilds/{guild_id}/audit-logs", "read", "audit", permission="view_audit_log", query=("user_id", "action_type", "before", "after", "limit")),
    # Auto moderation and scheduled-event reads.
    _operation("list_automod_rules", "GET", "/guilds/{guild_id}/auto-moderation/rules", "read", "automod", permission="manage_guild"),
    _operation("get_automod_rule", "GET", "/guilds/{guild_id}/auto-moderation/rules/{rule_id}", "read", "automod", permission="manage_guild"),
    _operation("list_scheduled_events", "GET", "/guilds/{guild_id}/scheduled-events", "read", "scheduled_event", query=("with_user_count",)),
    _operation("get_scheduled_event", "GET", "/guilds/{guild_id}/scheduled-events/{event_id}", "read", "scheduled_event", query=("with_user_count",)),
    _operation("list_scheduled_event_users", "GET", "/guilds/{guild_id}/scheduled-events/{event_id}/users", "read", "scheduled_event", query=("limit", "with_member", "before", "after")),
    # Stage, voice, expression, soundboard, webhook, and template reads.
    _operation("get_stage_instance", "GET", "/stage-instances/{channel_id}", "read", "stage"),
    _operation("list_voice_regions", "GET", "/voice/regions", "read", "voice"),
    _operation("get_current_voice_state", "GET", "/guilds/{guild_id}/voice-states/@me", "read", "voice"),
    _operation("get_user_voice_state", "GET", "/guilds/{guild_id}/voice-states/{user_id}", "read", "voice", permission="move_members"),
    _operation("list_guild_emojis", "GET", "/guilds/{guild_id}/emojis", "read", "emoji"),
    _operation("get_guild_emoji", "GET", "/guilds/{guild_id}/emojis/{emoji_id}", "read", "emoji"),
    _operation("list_guild_stickers", "GET", "/guilds/{guild_id}/stickers", "read", "sticker"),
    _operation("get_guild_sticker", "GET", "/guilds/{guild_id}/stickers/{sticker_id}", "read", "sticker"),
    _operation("list_default_soundboard_sounds", "GET", "/soundboard-default-sounds", "read", "soundboard"),
    _operation("list_guild_soundboard_sounds", "GET", "/guilds/{guild_id}/soundboard-sounds", "read", "soundboard"),
    _operation("get_guild_soundboard_sound", "GET", "/guilds/{guild_id}/soundboard-sounds/{sound_id}", "read", "soundboard"),
    _operation("list_channel_webhooks", "GET", "/channels/{channel_id}/webhooks", "read", "webhook", permission="manage_webhooks"),
    _operation("list_guild_webhooks", "GET", "/guilds/{guild_id}/webhooks", "read", "webhook", permission="manage_webhooks"),
    _operation("get_webhook", "GET", "/webhooks/{webhook_id}", "read", "webhook", permission="manage_webhooks"),
    _operation("get_guild_templates", "GET", "/guilds/{guild_id}/templates", "read", "template", permission="manage_guild"),
    _operation("get_guild_template", "GET", "/guilds/templates/{template_code}", "read", "template"),
    # Additive/reversible writes.
    _operation("create_guild_channel", "POST", "/guilds/{guild_id}/channels", "write", "channel", permission="manage_channels", body=("name", "type", "topic", "bitrate", "user_limit", "rate_limit_per_user", "position", "permission_overwrites", "parent_id", "nsfw", "rtc_region", "video_quality_mode", "default_auto_archive_duration", "default_reaction_emoji", "available_tags", "default_sort_order", "default_forum_layout", "default_tag_setting"), required_body=("name",), reason=True),
    _operation("add_member_role", "PUT", "/guilds/{guild_id}/members/{user_id}/roles/{role_id}", "write", "role", permission="manage_roles", reason=True, member_guard=True, role_guard=True),
    _operation("create_role", "POST", "/guilds/{guild_id}/roles", "write", "role", permission="manage_roles", body=("name", "permissions", "colors", "color", "hoist", "icon", "unicode_emoji", "mentionable"), required_body=("name",), reason=True),
    _operation("create_channel_invite", "POST", "/channels/{channel_id}/invites", "write", "invite", permission="create_instant_invite", body=("max_age", "max_uses", "temporary", "unique", "target_type", "target_user_id", "target_application_id", "role_ids"), send_empty_json=True, reason=True),
    _operation("follow_announcement_channel", "POST", "/channels/{channel_id}/followers", "write", "channel", permission="manage_webhooks", body=("webhook_channel_id",), required_body=("webhook_channel_id",), reason=True),
    _operation("trigger_typing", "POST", "/channels/{channel_id}/typing", "write", "message"),
    _operation("start_thread_from_message", "POST", "/channels/{channel_id}/messages/{message_id}/threads", "write", "thread", body=("name", "auto_archive_duration", "rate_limit_per_user"), required_body=("name",), reason=True),
    _operation("start_thread_without_message", "POST", "/channels/{channel_id}/threads", "write", "thread", body=("name", "auto_archive_duration", "type", "invitable", "rate_limit_per_user"), required_body=("name",), reason=True),
    _operation("start_forum_thread", "POST", "/channels/{channel_id}/threads", "write", "thread", body=("name", "auto_archive_duration", "rate_limit_per_user", "message", "applied_tags"), required_body=("name", "message"), reason=True),
    _operation("join_thread", "PUT", "/channels/{channel_id}/thread-members/@me", "write", "thread"),
    _operation("add_thread_member", "PUT", "/channels/{channel_id}/thread-members/{user_id}", "write", "thread"),
    _operation("crosspost_message", "POST", "/channels/{channel_id}/messages/{message_id}/crosspost", "write", "message"),
    _operation("pin_message", "PUT", "/channels/{channel_id}/messages/pins/{message_id}", "write", "message", permission="pin_messages", reason=True),
    _operation("create_automod_rule", "POST", "/guilds/{guild_id}/auto-moderation/rules", "write", "automod", permission="manage_guild", body=("name", "event_type", "trigger_type", "trigger_metadata", "actions", "enabled", "exempt_roles", "exempt_channels"), required_body=("name", "event_type", "trigger_type", "actions"), reason=True),
    _operation("create_scheduled_event", "POST", "/guilds/{guild_id}/scheduled-events", "write", "scheduled_event", permission="manage_events", body=("channel_id", "entity_metadata", "name", "privacy_level", "scheduled_start_time", "scheduled_end_time", "description", "entity_type", "image", "recurrence_rule"), required_body=("name", "privacy_level", "scheduled_start_time", "entity_type"), reason=True),
    _operation("create_stage_instance", "POST", "/stage-instances", "write", "stage", permission="manage_channels", body=("channel_id", "topic", "privacy_level", "send_start_notification", "guild_scheduled_event_id"), required_body=("channel_id", "topic"), reason=True),
    _operation("create_guild_emoji", "POST", "/guilds/{guild_id}/emojis", "write", "emoji", permission="manage_guild_expressions", body=("name", "image", "roles"), required_body=("name", "image"), reason=True),
    _operation("create_guild_sticker", "POST", "/guilds/{guild_id}/stickers", "write", "sticker", permission="manage_guild_expressions", body=("name", "description", "tags", "file_base64", "filename"), required_body=("name", "tags", "file_base64"), reason=True, body_mode="sticker_multipart"),
    _operation("send_soundboard_sound", "POST", "/channels/{channel_id}/send-soundboard-sound", "write", "soundboard", permission="speak", body=("sound_id", "source_guild_id")),
    _operation("create_guild_soundboard_sound", "POST", "/guilds/{guild_id}/soundboard-sounds", "write", "soundboard", permission="manage_guild_expressions", body=("name", "sound", "volume", "emoji_id", "emoji_name"), required_body=("name", "sound"), reason=True),
    _operation("create_webhook_safe", "POST", "/channels/{channel_id}/webhooks", "write", "webhook", permission="manage_webhooks", body=("name", "avatar"), required_body=("name",), reason=True),
    _operation("create_guild_template", "POST", "/guilds/{guild_id}/templates", "write", "template", permission="manage_guild", body=("name", "description"), required_body=("name",)),
    # Overwrites, reorders, moderation, removals, and deletes are destructive.
    _operation("modify_guild", "PATCH", "/guilds/{guild_id}", "destructive", "guild", permission="manage_guild", body=("name", "verification_level", "default_message_notifications", "explicit_content_filter", "afk_channel_id", "afk_timeout", "icon", "owner_id", "splash", "discovery_splash", "banner", "system_channel_id", "system_channel_flags", "rules_channel_id", "public_updates_channel_id", "preferred_locale", "features", "description", "premium_progress_bar_enabled", "safety_alerts_channel_id"), reason=True),
    _operation("modify_channel_positions", "PATCH", "/guilds/{guild_id}/channels", "destructive", "channel", permission="manage_channels", body=("positions",), reason=True, body_mode="positions"),
    _operation("modify_member", "PATCH", "/guilds/{guild_id}/members/{user_id}", "destructive", "member", permission="moderate_members", body=("nick", "roles", "mute", "deaf", "channel_id", "communication_disabled_until", "flags"), reason=True, member_guard=True),
    _operation("modify_current_member", "PATCH", "/guilds/{guild_id}/members/@me", "destructive", "member", body=("nick",), reason=True),
    _operation("modify_current_nick", "PATCH", "/guilds/{guild_id}/members/@me/nick", "destructive", "member", body=("nick",), reason=True),
    _operation("remove_member_role", "DELETE", "/guilds/{guild_id}/members/{user_id}/roles/{role_id}", "destructive", "role", permission="manage_roles", reason=True, member_guard=True, role_guard=True),
    _operation("bulk_ban", "POST", "/guilds/{guild_id}/bulk-ban", "destructive", "moderation", permission="ban_members", body=("user_ids", "delete_message_seconds"), reason=True),
    _operation("modify_role_positions", "PATCH", "/guilds/{guild_id}/roles", "destructive", "role", permission="manage_roles", body=("positions",), reason=True, body_mode="positions"),
    _operation("modify_role", "PATCH", "/guilds/{guild_id}/roles/{role_id}", "destructive", "role", permission="manage_roles", body=("name", "permissions", "colors", "color", "hoist", "icon", "unicode_emoji", "mentionable"), reason=True, role_guard=True),
    _operation("delete_role", "DELETE", "/guilds/{guild_id}/roles/{role_id}", "destructive", "role", permission="manage_roles", reason=True, role_guard=True),
    _operation("begin_prune", "POST", "/guilds/{guild_id}/prune", "destructive", "moderation", permission="kick_members", body=("days", "compute_prune_count", "include_roles"), reason=True),
    _operation("delete_integration", "DELETE", "/guilds/{guild_id}/integrations/{integration_id}", "destructive", "integration", permission="manage_guild", reason=True),
    _operation("modify_widget", "PATCH", "/guilds/{guild_id}/widget", "destructive", "guild", permission="manage_guild", body=("enabled", "channel_id"), reason=True),
    _operation("modify_welcome_screen", "PATCH", "/guilds/{guild_id}/welcome-screen", "destructive", "guild", permission="manage_guild", body=("enabled", "welcome_channels", "description"), reason=True),
    _operation("modify_onboarding", "PUT", "/guilds/{guild_id}/onboarding", "destructive", "guild", permission="manage_guild", body=("prompts", "default_channel_ids", "enabled", "mode"), reason=True),
    _operation("modify_incident_actions", "PUT", "/guilds/{guild_id}/incident-actions", "destructive", "guild", permission="manage_guild", body=("invites_disabled_until", "dms_disabled_until"), reason=True),
    _operation("modify_channel", "PATCH", "/channels/{channel_id}", "destructive", "channel", permission="manage_channels", body=("name", "type", "position", "topic", "nsfw", "rate_limit_per_user", "bitrate", "user_limit", "permission_overwrites", "parent_id", "rtc_region", "video_quality_mode", "default_auto_archive_duration", "flags", "available_tags", "default_reaction_emoji", "default_thread_rate_limit_per_user", "default_sort_order", "default_forum_layout", "archived", "auto_archive_duration", "locked", "invitable", "applied_tags"), reason=True),
    _operation("set_voice_channel_status", "PUT", "/channels/{channel_id}/voice-status", "destructive", "voice", permission="set_voice_channel_status", body=("status",), reason=True),
    _operation("edit_channel_permissions", "PUT", "/channels/{channel_id}/permissions/{target_id}", "destructive", "permission", permission="manage_roles", body=("allow", "deny", "type"), reason=True, role_guard=True),
    _operation("delete_channel_permission", "DELETE", "/channels/{channel_id}/permissions/{target_id}", "destructive", "permission", permission="manage_roles", reason=True, role_guard=True),
    _operation("delete_channel", "DELETE", "/channels/{channel_id}", "destructive", "channel", permission="manage_channels", reason=True),
    _operation("delete_invite", "DELETE", "/invites/{invite_code}", "destructive", "invite", permission="manage_channels", reason=True),
    _operation("leave_thread", "DELETE", "/channels/{channel_id}/thread-members/@me", "destructive", "thread"),
    _operation("remove_thread_member", "DELETE", "/channels/{channel_id}/thread-members/{user_id}", "destructive", "thread", permission="manage_threads"),
    _operation("delete_all_reactions", "DELETE", "/channels/{channel_id}/messages/{message_id}/reactions", "destructive", "reaction", permission="manage_messages"),
    _operation("delete_reactions_for_emoji", "DELETE", "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}", "destructive", "reaction", permission="manage_messages"),
    _operation("bulk_delete_messages", "POST", "/channels/{channel_id}/messages/bulk-delete", "destructive", "message", permission="manage_messages", body=("messages",), reason=True),
    _operation("unpin_message", "DELETE", "/channels/{channel_id}/messages/pins/{message_id}", "destructive", "message", permission="pin_messages", reason=True),
    _operation("modify_automod_rule", "PATCH", "/guilds/{guild_id}/auto-moderation/rules/{rule_id}", "destructive", "automod", permission="manage_guild", body=("name", "event_type", "trigger_metadata", "actions", "enabled", "exempt_roles", "exempt_channels"), reason=True),
    _operation("delete_automod_rule", "DELETE", "/guilds/{guild_id}/auto-moderation/rules/{rule_id}", "destructive", "automod", permission="manage_guild", reason=True),
    _operation("modify_scheduled_event", "PATCH", "/guilds/{guild_id}/scheduled-events/{event_id}", "destructive", "scheduled_event", permission="manage_events", body=("channel_id", "entity_metadata", "name", "privacy_level", "scheduled_start_time", "scheduled_end_time", "description", "entity_type", "status", "image", "recurrence_rule"), reason=True),
    _operation("delete_scheduled_event", "DELETE", "/guilds/{guild_id}/scheduled-events/{event_id}", "destructive", "scheduled_event", permission="manage_events", reason=True),
    _operation("modify_stage_instance", "PATCH", "/stage-instances/{channel_id}", "destructive", "stage", permission="manage_channels", body=("topic", "privacy_level"), reason=True),
    _operation("delete_stage_instance", "DELETE", "/stage-instances/{channel_id}", "destructive", "stage", permission="manage_channels", reason=True),
    _operation("modify_current_voice_state", "PATCH", "/guilds/{guild_id}/voice-states/@me", "destructive", "voice", body=("channel_id", "suppress", "request_to_speak_timestamp")),
    _operation("modify_user_voice_state", "PATCH", "/guilds/{guild_id}/voice-states/{user_id}", "destructive", "voice", permission="move_members", body=("channel_id", "suppress"), member_guard=True),
    _operation("modify_guild_emoji", "PATCH", "/guilds/{guild_id}/emojis/{emoji_id}", "destructive", "emoji", permission="manage_guild_expressions", body=("name", "roles"), reason=True),
    _operation("delete_guild_emoji", "DELETE", "/guilds/{guild_id}/emojis/{emoji_id}", "destructive", "emoji", permission="manage_guild_expressions", reason=True),
    _operation("modify_guild_sticker", "PATCH", "/guilds/{guild_id}/stickers/{sticker_id}", "destructive", "sticker", permission="manage_guild_expressions", body=("name", "description", "tags"), reason=True),
    _operation("delete_guild_sticker", "DELETE", "/guilds/{guild_id}/stickers/{sticker_id}", "destructive", "sticker", permission="manage_guild_expressions", reason=True),
    _operation("modify_guild_soundboard_sound", "PATCH", "/guilds/{guild_id}/soundboard-sounds/{sound_id}", "destructive", "soundboard", permission="manage_guild_expressions", body=("name", "volume", "emoji_id", "emoji_name"), reason=True),
    _operation("delete_guild_soundboard_sound", "DELETE", "/guilds/{guild_id}/soundboard-sounds/{sound_id}", "destructive", "soundboard", permission="manage_guild_expressions", reason=True),
    _operation("modify_webhook_safe", "PATCH", "/webhooks/{webhook_id}", "destructive", "webhook", permission="manage_webhooks", body=("name", "avatar", "channel_id"), reason=True),
    _operation("delete_webhook_safe", "DELETE", "/webhooks/{webhook_id}", "destructive", "webhook", permission="manage_webhooks", reason=True),
    _operation("sync_guild_template", "PUT", "/guilds/{guild_id}/templates/{template_code}", "destructive", "template", permission="manage_guild"),
    _operation("modify_guild_template", "PATCH", "/guilds/{guild_id}/templates/{template_code}", "destructive", "template", permission="manage_guild", body=("name", "description")),
    _operation("delete_guild_template", "DELETE", "/guilds/{guild_id}/templates/{template_code}", "destructive", "template", permission="manage_guild"),
)


OPERATIONS: Mapping[str, DiscordAdminOperation] = {item.action: item for item in _OPERATIONS}
if len(OPERATIONS) != len(_OPERATIONS):
    raise RuntimeError("Discord admin action names must be unique.")

READ_ACTIONS = tuple(item.action for item in _OPERATIONS if item.risk == "read")
WRITE_ACTIONS = tuple(item.action for item in _OPERATIONS if item.risk == "write")
DESTRUCTIVE_ACTIONS = tuple(item.action for item in _OPERATIONS if item.risk == "destructive")


def get_operation(action: str, expected_risk: str) -> DiscordAdminOperation:
    operation = OPERATIONS.get(str(action or "").strip())
    if operation is None or operation.risk != expected_risk:
        allowed = ", ".join(
            READ_ACTIONS
            if expected_risk == "read"
            else WRITE_ACTIONS if expected_risk == "write" else DESTRUCTIVE_ACTIONS
        )
        raise ValueError(f"Unsupported {expected_risk} action. Allowed actions: {allowed}.")
    return operation


def _identifier(value: Any, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required for this action.")
    if name not in {"emoji", "invite_code", "template_code"} and not _SNOWFLAKE.fullmatch(normalized):
        raise ValueError(f"{name} must be a Discord snowflake.")
    if name in {"invite_code", "template_code"} and not re.fullmatch(r"[A-Za-z0-9_-]{2,100}", normalized):
        raise ValueError(f"{name} contains unsupported characters.")
    return quote(normalized, safe="")


def build_operation_path(
    operation: DiscordAdminOperation,
    identifiers: Mapping[str, Any],
) -> str:
    path = operation.path
    for name in operation.required_identifiers:
        path = path.replace("{" + name + "}", _identifier(identifiers.get(name), name))
    return path


def _bounded_mapping(
    value: Mapping[str, Any] | None,
    allowed: tuple[str, ...],
    label: str,
) -> dict[str, Any]:
    normalized = dict(value or {})
    unexpected = sorted(set(normalized) - set(allowed))
    if unexpected:
        raise ValueError(f"Unsupported {label} fields: {', '.join(unexpected)}.")
    return normalized


def validate_query(operation: DiscordAdminOperation, query: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = _bounded_mapping(query, operation.query_fields, "query")
    if "limit" in normalized:
        limit = int(normalized["limit"])
        if limit < 1 or limit > operation.max_limit:
            raise ValueError(f"query.limit must be between 1 and {operation.max_limit}.")
        normalized["limit"] = limit
    if operation.action == "get_channel_messages":
        cursors = [key for key in ("around", "before", "after") if normalized.get(key)]
        if len(cursors) > 1:
            raise ValueError("Only one of query.around, query.before, or query.after may be set.")
    if operation.action == "search_guild_messages":
        for field, maximum in (("offset", 9975), ("slop", 100)):
            if field in normalized:
                value = int(normalized[field])
                if value < 0 or value > maximum:
                    raise ValueError(f"query.{field} must be between 0 and {maximum}.")
                normalized[field] = value
        if "content" in normalized and len(str(normalized["content"])) > 1024:
            raise ValueError("query.content must be 1024 characters or fewer.")
        list_limits = {
            "channel_id": 500,
            "author_type": 100,
            "author_id": 100,
            "mentions": 100,
            "mentions_role_id": 100,
            "replied_to_user_id": 100,
            "replied_to_message_id": 100,
            "has": 100,
            "embed_type": 100,
            "embed_provider": 100,
            "link_hostname": 100,
            "attachment_filename": 100,
            "attachment_extension": 100,
        }
        for field, maximum in list_limits.items():
            if field not in normalized:
                continue
            values = normalized[field]
            if not isinstance(values, list) or not 1 <= len(values) <= maximum:
                raise ValueError(f"query.{field} must be a list of 1 to {maximum} values.")
    if "target_type" in normalized:
        target_type = str(normalized["target_type"] or "bot").strip().lower()
        if target_type not in {"bot", "member", "role"}:
            raise ValueError("query.target_type must be bot, member, or role.")
        normalized["target_type"] = target_type
    return {
        key: (str(value).lower() if isinstance(value, bool) else value)
        for key, value in normalized.items()
        if value is not None and value != ""
    }


def validate_payload(operation: DiscordAdminOperation, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = _bounded_mapping(payload, operation.body_fields, "payload")
    if (
        operation.method in {"POST", "PUT", "PATCH"}
        and operation.body_fields
        and not normalized
        and not operation.send_empty_json
    ):
        raise ValueError("payload must contain at least one reviewed field for this action.")
    missing = [field for field in operation.required_body_fields if field not in normalized]
    if missing:
        raise ValueError(f"payload is missing required fields: {', '.join(missing)}.")
    if operation.action == "bulk_ban":
        user_ids = normalized.get("user_ids")
        if not isinstance(user_ids, list) or not 1 <= len(user_ids) <= 200:
            raise ValueError("bulk_ban payload.user_ids must contain 1 to 200 snowflakes.")
        normalized["user_ids"] = [_identifier(value, "user_id") for value in user_ids]
    if operation.action == "bulk_delete_messages":
        messages = normalized.get("messages")
        if not isinstance(messages, list) or not 2 <= len(messages) <= 100:
            raise ValueError("bulk_delete_messages payload.messages must contain 2 to 100 snowflakes.")
        normalized["messages"] = [_identifier(value, "message_id") for value in messages]
    if operation.body_mode == "positions":
        positions = normalized.get("positions")
        if not isinstance(positions, list) or not 1 <= len(positions) <= 100:
            raise ValueError("payload.positions must contain 1 to 100 position objects.")
        for entry in positions:
            if not isinstance(entry, dict) or not _SNOWFLAKE.fullmatch(str(entry.get("id", ""))):
                raise ValueError("Each position entry requires a Discord snowflake id.")
    serialized_size = len(
        json.dumps(normalized, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    if serialized_size > 1024 * 1024:
        raise ValueError("payload must be 1048576 bytes or fewer.")
    return normalized


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return {"wire_truncated": True, "reason": "max_depth"}
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:MAX_COLLECTION_ITEMS]:
            key = str(raw_key)
            if key.lower() in _SENSITIVE_KEYS or "token" in key.lower():
                result[key] = "[REDACTED]"
            else:
                result[key] = _redact(raw_value, depth + 1)
        if len(value) > MAX_COLLECTION_ITEMS:
            result["wire_truncated"] = True
        return result
    if isinstance(value, list):
        items = [_redact(item, depth + 1) for item in value[:MAX_COLLECTION_ITEMS]]
        if len(value) > MAX_COLLECTION_ITEMS:
            items.append({"wire_truncated": True, "original_count": len(value)})
        return items
    if isinstance(value, str):
        if "/webhooks/" in value and len(value.split("/webhooks/", 1)[1].split("/")) > 1:
            return "[REDACTED_WEBHOOK_URL]"
        if len(value) > MAX_STRING_LENGTH:
            return value[:MAX_STRING_LENGTH] + "...[wire truncated]"
    return value


def bound_response(value: Any) -> Any:
    safe = _redact(value)
    if len(json.dumps(safe, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) <= MAX_RESPONSE_BYTES:
        return safe
    if isinstance(safe, dict):
        for key, item in list(safe.items()):
            if isinstance(item, list) and len(item) > 10:
                safe[key] = item[:10] + [{"wire_truncated": True, "original_count": len(item)}]
    if len(json.dumps(safe, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) <= MAX_RESPONSE_BYTES:
        return safe
    return {
        "wire_truncated": True,
        "summary": "Discord returned more data than the MCP wire budget permits.",
        "response_type": type(value).__name__,
    }


def _sticker_form(payload: dict[str, Any]) -> aiohttp.FormData:
    encoded = str(payload.pop("file_base64", ""))
    if "," in encoded and encoded.lower().startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("payload.file_base64 must be valid base64 sticker bytes.") from exc
    if not raw or len(raw) > 512 * 1024:
        raise ValueError("Sticker file must contain 1 to 524288 bytes.")
    filename = str(payload.pop("filename", "sticker.png"))[:100]
    form = aiohttp.FormData()
    for key in ("name", "description", "tags"):
        if key in payload and payload[key] is not None:
            form.add_field(key, str(payload[key]))
    form.add_field("file", raw, filename=filename, content_type="application/octet-stream")
    return form


async def execute_operation(
    operation: DiscordAdminOperation,
    *,
    token: str,
    identifiers: Mapping[str, Any],
    query: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
    reason: str = "",
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    if not token:
        raise ValueError("A request-scoped Discord bot token is required.")
    path = build_operation_path(operation, identifiers)
    query_data = validate_query(operation, query)
    payload_data = validate_payload(operation, payload)
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "DiscordBot (https://github.com/MADPANDA3D/DISCORD-MCP, 1.0)",
    }
    clean_reason = str(reason or "").strip()
    if clean_reason and not operation.audit_reason:
        raise ValueError("This Discord endpoint does not support an audit-log reason.")
    if clean_reason and operation.audit_reason:
        if len(clean_reason) > 512:
            raise ValueError("reason must be 512 characters or fewer.")
        headers["X-Audit-Log-Reason"] = quote(clean_reason, safe=" ")

    request_kwargs: dict[str, Any] = {"params": query_data or None, "headers": headers}
    if payload_data or operation.send_empty_json:
        if operation.body_mode == "positions":
            request_kwargs["json"] = payload_data["positions"]
        elif operation.body_mode == "sticker_multipart":
            request_kwargs["data"] = _sticker_form(dict(payload_data))
        else:
            request_kwargs["json"] = payload_data

    owns_session = session is None
    active_session = session or aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30, connect=10)
    )
    try:
        attempts = 2 if operation.risk == "read" else 1
        for attempt in range(attempts):
            async with active_session.request(
                operation.method,
                DISCORD_API_BASE + path,
                **request_kwargs,
            ) as response:
                content_type = response.headers.get("Content-Type", "")
                body: Any
                if response.status == 204:
                    body = None
                elif "application/json" in content_type:
                    body = await response.json(content_type=None)
                else:
                    text = await response.text()
                    body = {"text": text[:MAX_STRING_LENGTH], "content_type": content_type}
                if response.status == 429 and attempt + 1 < attempts:
                    retry_after = 0.0
                    if isinstance(body, dict):
                        retry_after = float(body.get("retry_after") or 0.0)
                    if 0 < retry_after <= MAX_RETRY_AFTER_SECONDS:
                        await asyncio.sleep(retry_after)
                        continue
                rate_limit = {
                    "limit": response.headers.get("X-RateLimit-Limit"),
                    "remaining": response.headers.get("X-RateLimit-Remaining"),
                    "reset_after": response.headers.get("X-RateLimit-Reset-After"),
                    "global": bool(isinstance(body, dict) and body.get("global")),
                }
                if response.status < 200 or response.status >= 300:
                    message = body.get("message") if isinstance(body, dict) else None
                    code = body.get("code") if isinstance(body, dict) else None
                    return {
                        "ok": False,
                        "status": response.status,
                        "action": operation.action,
                        "error": {
                            "type": "rate_limited" if response.status == 429 else "discord_api_error",
                            "message": str(message or "Discord rejected the server-management request.")[:512],
                            "discord_error_code": code if isinstance(code, int) else None,
                        },
                        "rate_limit": rate_limit,
                    }
                return {
                    "ok": True,
                    "status": response.status,
                    "action": operation.action,
                    "resource": operation.resource,
                    "data": bound_response(body),
                    "rate_limit": rate_limit,
                }
        raise RuntimeError("Discord request retry loop ended unexpectedly.")
    finally:
        if owns_session:
            await active_session.close()
