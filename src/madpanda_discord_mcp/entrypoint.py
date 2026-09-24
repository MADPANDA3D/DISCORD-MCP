import copy
from collections.abc import Mapping
from typing import Any

from . import discord_admin_api, server, tool_manifest
from .response_bounds import bound_list_response, describe_admin_read_schema

_original_bound_response = discord_admin_api.bound_response
_original_enrich_input_schema = tool_manifest.enrich_input_schema


def _bound_response(value: Any) -> Any:
    if isinstance(value, list):
        return bound_list_response(
            value,
            redact=discord_admin_api._redact,
            max_response_bytes=discord_admin_api.MAX_RESPONSE_BYTES,
        )
    return _original_bound_response(value)


def _enrich_input_schema(tool_name: str, schema: Mapping[str, Any]) -> dict[str, Any]:
    return describe_admin_read_schema(tool_name, schema, _original_enrich_input_schema)


def _install_response_bounds() -> None:
    discord_admin_api.bound_response = _bound_response
    tool_manifest.enrich_input_schema = _enrich_input_schema
    server.enrich_input_schema = _enrich_input_schema
    for tool_name, runtime_tool in server.mcp._tool_manager._tools.items():
        runtime_tool.parameters = describe_admin_read_schema(
            tool_name,
            runtime_tool.parameters,
            lambda _name, schema: copy.deepcopy(dict(schema)),
        )


def main() -> None:
    _install_response_bounds()
    server.main()
