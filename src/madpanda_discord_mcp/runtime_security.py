"""Fail-closed HTTP access controls for the packaged Discord MCP runtime."""

from __future__ import annotations

import asyncio
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

VALID_MCP_MODES = frozenset({"portal", "standalone"})
DEFAULT_PORTAL_GRANT_HEADER = "X-MADPANDA-PORTAL-GRANT"
DEFAULT_REQUEST_BODY_MAX_BYTES = 1_048_576
DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS = 10
DEFAULT_ALLOWED_HOSTS = (
    "localhost:*",
    "127.0.0.1:*",
    "[::1]:*",
    "discord-mcp:*",
)
MIN_SERVICE_TOKEN_LENGTH = 32
MAX_REQUEST_BODY_MAX_BYTES = 16_777_216
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_SINGLETON_SECURITY_HEADERS = frozenset(
    {
        "authorization",
        "content-length",
        "host",
        "origin",
        "transfer-encoding",
    }
)


class RuntimeConfigurationError(RuntimeError):
    """Raised when the selected runtime access boundary is incomplete."""


@dataclass(frozen=True)
class RuntimeSecurityConfig:
    """Immutable startup-selected service access policy."""

    mode: str
    standalone_access_token: str
    portal_grant_token: str
    portal_grant_header: str
    request_body_max_bytes: int
    request_body_timeout_seconds: int
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]


def _required_service_token(environment: Mapping[str, str], name: str) -> str:
    value = str(environment.get(name, "") or "").strip()
    if len(value) < MIN_SERVICE_TOKEN_LENGTH:
        raise RuntimeConfigurationError(
            f"{name} must be configured with at least {MIN_SERVICE_TOKEN_LENGTH} characters."
        )
    return value


def _parse_positive_int(
    raw: str,
    *,
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = str(raw or "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeConfigurationError(f"{name} must be an integer.") from exc
    if parsed < minimum or parsed > maximum:
        raise RuntimeConfigurationError(f"{name} must be between {minimum} and {maximum}.")
    return parsed


def _normalize_origin(candidate: str) -> str:
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeConfigurationError("Origin contains an invalid port.") from exc
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeConfigurationError(
            "Origins must be exact HTTP(S) origins without credentials, paths, queries, or fragments."
        )
    host = hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = 80 if scheme == "http" else 443
    port_suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{scheme}://{host}{port_suffix}"


def _parse_allowed_origins(raw: str) -> tuple[str, ...]:
    origins: list[str] = []
    for candidate in str(raw or "").split(","):
        origin = candidate.strip()
        if not origin:
            continue
        if origin == "*":
            raise RuntimeConfigurationError(
                "MCP_ALLOWED_ORIGINS must contain exact HTTP(S) origins; wildcards are forbidden."
            )
        origins.append(_normalize_origin(origin))
    return tuple(dict.fromkeys(origins))


def _parse_allowed_hosts(raw: str) -> tuple[str, ...]:
    candidates = [candidate.strip() for candidate in str(raw or "").split(",")]
    hosts = tuple(dict.fromkeys(candidate for candidate in candidates if candidate))
    if not hosts:
        return DEFAULT_ALLOWED_HOSTS
    if "*" in hosts or any("/" in host or "://" in host for host in hosts):
        raise RuntimeConfigurationError(
            "MCP_ALLOWED_HOSTS must contain exact Host values or host:* port patterns."
        )
    return hosts


def load_runtime_security_config(
    environment: Mapping[str, str],
) -> RuntimeSecurityConfig:
    """Load and validate one authenticated runtime mode at process startup."""

    mode = str(environment.get("MCP_MODE", "") or "").strip().lower()
    if mode not in VALID_MCP_MODES:
        choices = ", ".join(sorted(VALID_MCP_MODES))
        raise RuntimeConfigurationError(f"MCP_MODE must be exactly one of: {choices}.")

    portal_grant_header = str(
        environment.get("MCP_PORTAL_GRANT_HEADER", DEFAULT_PORTAL_GRANT_HEADER)
        or DEFAULT_PORTAL_GRANT_HEADER
    ).strip()
    if not _HEADER_NAME.fullmatch(portal_grant_header):
        raise RuntimeConfigurationError("MCP_PORTAL_GRANT_HEADER is not a valid HTTP header name.")
    if portal_grant_header.lower() in _SINGLETON_SECURITY_HEADERS:
        raise RuntimeConfigurationError(
            "MCP_PORTAL_GRANT_HEADER must not reuse a reserved HTTP security header."
        )

    standalone_access_token = ""
    portal_grant_token = ""
    if mode == "standalone":
        standalone_access_token = _required_service_token(environment, "MCP_ACCESS_TOKEN")
    else:
        portal_grant_token = _required_service_token(environment, "MCP_PORTAL_GRANT_TOKEN")

    return RuntimeSecurityConfig(
        mode=mode,
        standalone_access_token=standalone_access_token,
        portal_grant_token=portal_grant_token,
        portal_grant_header=portal_grant_header,
        request_body_max_bytes=_parse_positive_int(
            str(environment.get("MCP_REQUEST_BODY_MAX_BYTES", "") or ""),
            name="MCP_REQUEST_BODY_MAX_BYTES",
            default=DEFAULT_REQUEST_BODY_MAX_BYTES,
            minimum=1_024,
            maximum=MAX_REQUEST_BODY_MAX_BYTES,
        ),
        request_body_timeout_seconds=_parse_positive_int(
            str(environment.get("MCP_REQUEST_BODY_TIMEOUT_SECONDS", "") or ""),
            name="MCP_REQUEST_BODY_TIMEOUT_SECONDS",
            default=DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS,
            minimum=1,
            maximum=60,
        ),
        allowed_hosts=_parse_allowed_hosts(str(environment.get("MCP_ALLOWED_HOSTS", "") or "")),
        allowed_origins=_parse_allowed_origins(
            str(environment.get("MCP_ALLOWED_ORIGINS", "") or "")
        ),
    )


def validate_request_header_configuration(
    config: RuntimeSecurityConfig,
    configured_headers: Mapping[str, str],
) -> tuple[str, ...]:
    """Return normalized request headers or fail on invalid, reserved, or colliding names."""

    seen = set(_SINGLETON_SECURITY_HEADERS)
    seen.add(config.portal_grant_header.lower())
    normalized_headers: list[str] = []
    for variable, configured_header in configured_headers.items():
        header = str(configured_header or "").strip().lower()
        if not _HEADER_NAME.fullmatch(header):
            raise RuntimeConfigurationError(f"{variable} is not a valid HTTP header name.")
        if header in seen:
            raise RuntimeConfigurationError(
                f"{variable} must use a unique, non-reserved HTTP security header."
            )
        seen.add(header)
        normalized_headers.append(header)
    return tuple(normalized_headers)


def _normalized_headers(
    scope: Mapping[str, Any],
    portal_grant_header: str,
    additional_singleton_headers: frozenset[str],
) -> tuple[dict[str, str], set[str]]:
    normalized: dict[str, str] = {}
    duplicates: set[str] = set()
    singleton_headers = (
        _SINGLETON_SECURITY_HEADERS | {portal_grant_header.lower()} | additional_singleton_headers
    )
    for key, value in scope.get("headers", []):
        name = key.decode("latin-1").lower()
        if name in normalized and name in singleton_headers:
            duplicates.add(name)
        normalized[name] = value.decode("latin-1").strip()
    return normalized, duplicates


def _validate_bearer(authorization: str, expected: str) -> str | None:
    if not authorization:
        return "missing_access_token"
    scheme, separator, provided = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not provided or " " in provided:
        return "invalid_access_token"
    if not hmac.compare_digest(provided, expected):
        return "invalid_access_token"
    return None


def _host_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    if not host:
        return False
    if host in allowed_hosts:
        return True
    return any(
        allowed.endswith(":*") and host.startswith(allowed[:-1]) for allowed in allowed_hosts
    )


def validate_service_access(
    headers: Mapping[str, str],
    config: RuntimeSecurityConfig,
) -> str | None:
    """Return a stable error code or None without reflecting credential values."""

    if config.mode == "standalone":
        return _validate_bearer(headers.get("authorization", ""), config.standalone_access_token)

    header_name = config.portal_grant_header.lower()
    provided = headers.get(header_name, "")
    if not provided:
        return "missing_portal_grant"
    if not hmac.compare_digest(provided, config.portal_grant_token):
        return "invalid_portal_grant"
    return None


def _error_response(code: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error": {
                "type": "permission_denied" if status_code in {401, 403} else "invalid_request",
                "code": code,
                "message": "Discord MCP request rejected by the service access boundary.",
            },
        },
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


async def _read_bounded_body(receive: Any, maximum: int) -> list[dict[str, Any]] | None:
    messages: list[dict[str, Any]] = []
    total = 0
    while True:
        message = await receive()
        messages.append(message)
        if message.get("type") != "http.request":
            return messages
        total += len(message.get("body", b""))
        if total > maximum:
            return None
        if not message.get("more_body", False):
            return messages


def _replay_receive(messages: list[dict[str, Any]]) -> Any:
    pending = list(messages)

    async def replay() -> dict[str, Any]:
        if pending:
            return pending.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    return replay


class AccessControlMiddleware:
    """Authenticate and bound every MCP request before protocol parsing."""

    def __init__(
        self,
        app: Any,
        config: RuntimeSecurityConfig,
        singleton_headers: tuple[str, ...] = (),
    ) -> None:
        self.app = app
        self.config = config
        normalized_singletons = frozenset(
            str(header or "").strip().lower() for header in singleton_headers
        )
        if any(not _HEADER_NAME.fullmatch(header) for header in normalized_singletons):
            raise RuntimeConfigurationError(
                "Request credential and policy header names must be valid HTTP headers."
            )
        self.singleton_headers = normalized_singletons

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = str(scope.get("path", ""))
        protects_mcp = path == "/mcp" or path.startswith("/mcp/")
        if scope.get("type") != "http" or not protects_mcp:
            await self.app(scope, receive, send)
            return

        headers, duplicate_headers = _normalized_headers(
            scope,
            self.config.portal_grant_header,
            self.singleton_headers,
        )
        if duplicate_headers:
            await _error_response("duplicate_security_header", 400)(scope, receive, send)
            return
        access_error = validate_service_access(headers, self.config)
        if access_error is not None:
            await _error_response(access_error, 401)(scope, receive, send)
            return

        if not _host_allowed(headers.get("host", ""), self.config.allowed_hosts):
            await _error_response("host_not_allowed", 421)(scope, receive, send)
            return

        origin = headers.get("origin", "")
        if origin:
            try:
                normalized_origin = _normalize_origin(origin)
            except RuntimeConfigurationError:
                normalized_origin = ""
            if normalized_origin not in self.config.allowed_origins:
                await _error_response("origin_not_allowed", 403)(scope, receive, send)
                return

        if str(scope.get("method", "GET")).upper() == "POST":
            raw_content_length = headers.get("content-length", "")
            if raw_content_length:
                try:
                    content_length = int(raw_content_length)
                except ValueError:
                    await _error_response("invalid_content_length", 400)(scope, receive, send)
                    return
                if content_length < 0:
                    await _error_response("invalid_content_length", 400)(scope, receive, send)
                    return
                if content_length > self.config.request_body_max_bytes:
                    await _error_response("request_body_too_large", 413)(scope, receive, send)
                    return

            try:
                async with asyncio.timeout(self.config.request_body_timeout_seconds):
                    buffered = await _read_bounded_body(receive, self.config.request_body_max_bytes)
            except TimeoutError:
                await _error_response("request_body_timeout", 408)(scope, receive, send)
                return
            if buffered is None:
                await _error_response("request_body_too_large", 413)(scope, receive, send)
                return
            receive = _replay_receive(buffered)

        await self.app(scope, receive, send)
