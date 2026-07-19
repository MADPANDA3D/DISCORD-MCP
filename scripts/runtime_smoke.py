#!/usr/bin/env python3
"""Provider-free wire smoke for both authenticated Discord MCP modes."""

from __future__ import annotations

import http.client
import json
import os
from importlib.metadata import version
from typing import Any

HOST = "127.0.0.1"
PORT = int(os.getenv("MCP_HTTP_PORT", "8085"))
MODE = os.environ["MCP_MODE"]
CREDENTIAL_MODE = os.environ["DISCORD_CREDENTIAL_MODE"]
EXPECTED_TOOL_COUNT = int(os.getenv("MCP_EXPECTED_TOOL_COUNT", "50"))
EXPECTED_AGENT_READY_COUNT = int(os.getenv("MCP_EXPECTED_AGENT_READY_COUNT", "47"))
EXPECTED_BUILD_SHA = os.environ["MCP_BUILD_SHA"]
EXPECTED_SOURCE_FINGERPRINT = os.environ["MCP_SOURCE_FINGERPRINT"]
EXPECTED_IMAGE_REFERENCE = os.environ["MCP_IMAGE_REFERENCE"]
EXPECTED_CATALOG_VERSION = os.getenv("MCP_EXPECTED_CATALOG_VERSION", "discord-2026.07.18.1")
ACCESS_TOKEN = os.getenv("MCP_ACCESS_TOKEN", "")
PORTAL_GRANT = os.getenv("MCP_PORTAL_GRANT_TOKEN", "")
PORTAL_HEADER = os.getenv("MCP_PORTAL_GRANT_HEADER", "X-MADPANDA-PORTAL-GRANT")
PACKAGE_VERSION = version("mad-mcp-discord")
SYNTHETIC_PROVIDER_TOKEN = "synthetic-discord-provider-token-000000000000000000"
SYNTHETIC_GUILD_ID = "123456789012345678"
SYNTHETIC_CHANNEL_ID = "123456789012345679"


def auth_headers(*, valid: bool = True) -> dict[str, str]:
    if MODE == "standalone":
        token = ACCESS_TOKEN if valid else "wrong-standalone-token-000000000000"
        return {"Authorization": f"Bearer {token}"}
    if MODE == "portal":
        token = PORTAL_GRANT if valid else "wrong-portal-grant-0000000000000000"
        return {PORTAL_HEADER: token}
    raise AssertionError(f"unexpected MCP_MODE={MODE!r}")


def request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], Any]:
    body = (
        json.dumps(payload, separators=(",", ":")).encode()
        if isinstance(payload, dict)
        else payload
    )
    merged = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if headers:
        merged.update(headers)
    connection = http.client.HTTPConnection(HOST, PORT, timeout=8)
    try:
        connection.request(method, path, body=body, headers=merged)
        response = connection.getresponse()
        raw = response.read()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
    finally:
        connection.close()
    try:
        decoded: Any = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        decoded = raw.decode("utf-8", errors="replace")
    return response.status, response_headers, decoded


def rpc(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def tool_payload(response: Any, tool_name: str) -> dict[str, Any]:
    require(isinstance(response, dict), f"{tool_name} response is not JSON")
    result = response.get("result")
    require(isinstance(result, dict), f"{tool_name} response has no result")
    require(not result.get("isError", False), f"{tool_name} returned an MCP error: {result}")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for item in result.get("content", []):
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        try:
            decoded = json.loads(item.get("text", ""))
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(decoded, dict):
            return decoded
    raise AssertionError(f"{tool_name} returned no structured object")


def call_tool(
    headers: dict[str, str],
    request_id: int,
    name: str,
    arguments: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    status, _, response = request(
        "POST",
        "/mcp",
        payload=rpc("tools/call", request_id, {"name": name, "arguments": arguments}),
        headers=headers,
    )
    require(status == 200, f"{name} failed with HTTP {status}: {response}")
    return response, tool_payload(response, name)


def main() -> None:
    observed: list[Any] = []

    status, _, health = request("GET", "/health", headers={"Accept": "application/json"})
    observed.append(health)
    require(status == 200 and isinstance(health, dict), f"health={status} {health}")
    require(health.get("status") == "healthy", f"health={health}")
    require(health.get("version") == PACKAGE_VERSION, f"version={health}")
    require(health.get("build_sha") == EXPECTED_BUILD_SHA, f"build_sha={health}")
    require(
        health.get("source_fingerprint") == EXPECTED_SOURCE_FINGERPRINT,
        f"source_fingerprint={health}",
    )
    require(health.get("image_reference") == EXPECTED_IMAGE_REFERENCE, f"image={health}")
    require(health.get("catalog_version") == EXPECTED_CATALOG_VERSION, f"catalog={health}")
    require(health.get("tool_count") == EXPECTED_TOOL_COUNT, f"tool_count={health}")
    require(
        health.get("agent_ready_tool_count") == EXPECTED_AGENT_READY_COUNT,
        f"agent_ready={health}",
    )
    require(health.get("public_mode") is (MODE == "portal"), f"mode={health}")

    status, _, denied = request("POST", "/mcp", payload=b"malformed-before-auth")
    observed.append(denied)
    require(status == 401, f"missing auth was not rejected first: {status} {denied}")

    status, _, denied = request(
        "POST", "/mcp", payload=b"malformed-before-auth", headers=auth_headers(valid=False)
    )
    observed.append(denied)
    require(status == 401, f"invalid auth was not rejected first: {status} {denied}")

    origin_headers = auth_headers()
    origin_headers["Origin"] = "https://untrusted.invalid"
    status, _, denied = request(
        "POST", "/mcp", payload=rpc("tools/list", 2, {}), headers=origin_headers
    )
    observed.append(denied)
    require(status == 403, f"browser Origin was not rejected: {status} {denied}")

    status, response_headers, initialized = request(
        "POST",
        "/mcp",
        payload=rpc(
            "initialize",
            3,
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "discord-mcp-image-smoke", "version": "1"},
            },
        ),
        headers=auth_headers(),
    )
    observed.append(initialized)
    require(status == 200, f"initialize failed: {status} {initialized}")
    server_info = initialized.get("result", {}).get("serverInfo", {})
    require(server_info.get("version") == PACKAGE_VERSION, f"serverInfo={server_info}")

    discovery_headers = auth_headers()
    session_id = response_headers.get("mcp-session-id")
    if session_id:
        discovery_headers["Mcp-Session-Id"] = session_id
    status, _, tools = request(
        "POST", "/mcp", payload=rpc("tools/list", 4, {}), headers=discovery_headers
    )
    observed.append(tools)
    require(status == 200, f"tools/list failed: {status} {tools}")
    listed = tools.get("result", {}).get("tools", []) if isinstance(tools, dict) else []
    require(len(listed) == EXPECTED_TOOL_COUNT, f"tools/list count={len(listed)}")
    names = {tool.get("name") for tool in listed if isinstance(tool, dict)}
    for required_name in ("list_capabilities", "send_message", "delete_webhook"):
        require(required_name in names, f"missing tool: {required_name}")
    require("create_webhook" not in names, "retired webhook credential tool is exposed")
    require("send_webhook_message" not in names, "retired webhook credential tool is exposed")

    raw_response, capabilities = call_tool(
        discovery_headers,
        5,
        "list_capabilities",
        {"include_descriptors": False},
    )
    observed.append(raw_response)
    require(capabilities.get("ok") is True, f"capabilities={capabilities}")
    capability_data = capabilities.get("data", {})
    require(capability_data.get("catalogVersion") == EXPECTED_CATALOG_VERSION, str(capabilities))
    require(capability_data.get("counts", {}).get("raw") == EXPECTED_TOOL_COUNT, str(capabilities))
    require(
        capability_data.get("descriptorHash") == health.get("descriptor_hash"),
        "health and navigation descriptor hashes differ",
    )

    configuration_headers = dict(discovery_headers)
    if CREDENTIAL_MODE == "request":
        configuration_headers.update(
            {
                "x-discord-bot-token": SYNTHETIC_PROVIDER_TOKEN,
                "x-discord-guild-id": SYNTHETIC_GUILD_ID,
                "x-discord-allowed-channels": SYNTHETIC_CHANNEL_ID,
            }
        )
    raw_response, configuration = call_tool(
        configuration_headers,
        6,
        "check_configuration",
        {},
    )
    observed.append(raw_response)
    require(configuration.get("ok") is True, f"configuration={configuration}")
    require(configuration.get("data", {}).get("ready") is True, f"configuration={configuration}")
    require(
        configuration.get("data", {}).get("configuration", {}).get("credentialMode")
        == CREDENTIAL_MODE,
        f"configuration={configuration}",
    )

    serialized = json.dumps(observed, ensure_ascii=True)
    for secret in (ACCESS_TOKEN, PORTAL_GRANT, SYNTHETIC_PROVIDER_TOKEN):
        if secret:
            require(secret not in serialized, "a synthetic credential appeared in a response")

    print(
        json.dumps(
            {
                "ok": True,
                "mode": MODE,
                "credential_mode": CREDENTIAL_MODE,
                "tool_count": len(listed),
                "catalog_version": EXPECTED_CATALOG_VERSION,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
