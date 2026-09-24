import json
from typing import Any, Callable, Mapping


def _encoded_size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def bound_list_response(
    value: list[Any],
    *,
    redact: Callable[[Any], Any],
    max_response_bytes: int,
) -> Any:
    """Keep a useful, resumable prefix when a Discord list exceeds the wire budget."""
    safe = redact(value)
    if _encoded_size(safe) <= max_response_bytes:
        return safe

    items: list[Any] = []
    next_after: str | None = None
    for item in safe:
        candidate = [*items, item]
        cursor = _member_cursor(item)
        marker = {
            "items": candidate,
            "wire_truncated": True,
            "original_count": len(safe),
            "returned_count": len(candidate),
            "next_after": cursor,
        }
        if _encoded_size(marker) > max_response_bytes:
            break
        items = candidate
        next_after = cursor

    return {
        "items": items,
        "wire_truncated": True,
        "original_count": len(safe),
        "returned_count": len(items),
        "next_after": next_after,
    }


def describe_admin_read_schema(
    tool_name: str,
    schema: Mapping[str, Any],
    enrich: Callable[[str, Mapping[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    enriched = enrich(tool_name, schema)
    if tool_name != "discord_server_read":
        return enriched
    query = enriched.get("properties", {}).get("query")
    if isinstance(query, dict):
        note = (
            " For action=list_members, limit must be between 1 and 100; use the returned "
            "next_after value as query.after when wire_truncated is true."
        )
        description = str(query.get("description", "")).rstrip()
        if note.strip() not in description:
            query["description"] = description + note
    return enriched


def _member_cursor(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    user = item.get("user")
    if isinstance(user, dict) and user.get("id") is not None:
        return str(user["id"])
    if item.get("id") is not None:
        return str(item["id"])
    return None
