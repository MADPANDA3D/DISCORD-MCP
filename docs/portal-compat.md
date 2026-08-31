# Portal compatibility

## Contract

Portal mode is an authenticated service-to-service deployment of the same MCP server. It changes
credential ownership, not tool behavior:

- the Portal broker authenticates and authorizes the end user;
- the broker holds the MCP service grant;
- Discord BYOK values remain request-scoped;
- the Discord MCP independently enforces its server policy ceiling;
- the provider receives only the final scoped Discord call.

Portal mode requires:

```dotenv
MCP_MODE=portal
DISCORD_CREDENTIAL_MODE=request
MCP_PORTAL_GRANT_TOKEN=<fresh-service-grant>
DISCORD_ALLOWED_CHANNEL_IDS=ALL
```

`DISCORD_ALLOWED_CHANNEL_IDS=ALL` is appropriate only when the trusted broker always injects a real
per-request channel scope. Use a narrower server list when the deployment must impose an additional
global ceiling.

The base Compose file publishes only to loopback and allows local/container Host values. A Portal
deployment therefore needs a separately managed TLS proxy or isolated trusted network plus the
exact upstream Host in `MCP_ALLOWED_HOSTS`. Wildcard hostnames are forbidden; `host:*` may wildcard
only the port.

## Broker request flow

For every request, the broker should:

1. Authenticate the client and resolve the tenant server-side.
2. Authorize access to the Discord MCP and selected Discord connection.
3. Remove all client-supplied service, provider, and privileged policy headers.
4. Resolve secrets from the broker's secret store.
5. Inject the service grant and request-scoped Discord headers.
6. Forward to the private MCP endpoint over TLS or an isolated trusted network.
7. Enforce response size, timeout, audit, and tenant-delivery policy.
8. Never persist or log raw Discord/service credentials.

The client must never receive the service grant.

## Required headers

```http
X-MADPANDA-PORTAL-GRANT: <service-grant>
x-discord-bot-token: <discord-bot-token>
x-discord-guild-id: <guild-id>
x-discord-allowed-channels: <channel-id>,<channel-id>
```

The service-grant header is configurable with `MCP_PORTAL_GRANT_HEADER`. Provider and policy headers
are configurable through their `MCP_*_HEADER` variables. Each security-sensitive header is a
singleton; duplicate instances fail before dispatch.

Navigation tools need only the service grant. Provider calls additionally require the three Discord
headers above.

Optional request headers:

```http
x-discord-blocked-channels: <channel-id>,<channel-id>
x-discord-allow-all-read: false
x-discord-dm-enabled: false
x-mcp-admin-tools-enabled: false
x-mcp-require-confirm: true
x-openai-api: <optional-provider-key>
```

Optional booleans are not authority grants. The server intersects them with its own ceilings.

## Generic registry example

This example is intentionally deployment-neutral and contains no real endpoint or secret:

```yaml
service_id: discord
transport: streamable-http
endpoint: https://mcp.example.net/discord/mcp
health: https://mcp.example.net/discord/health
service_auth:
  header: X-MADPANDA-PORTAL-GRANT
  value_from_secret: discord-mcp-service-grant
provider_credentials:
  mode: request
  required_headers:
    - x-discord-bot-token
    - x-discord-guild-id
    - x-discord-allowed-channels
catalog:
  version: discord-2026.08.31.2
  raw_tools: 56
  agent_ready_tools: 50
  legacy_tools: 3
  hidden_tools: 3
```

Replace the example host and secret reference in private operator configuration. Do not commit a
production hostname, tenant identifier, secret-store path, or broker topology to this repository.

## Catalog ingestion

The broker can call:

```json
{
  "name": "list_capabilities",
  "arguments": {"include_descriptors": true}
}
```

Admission should record the service ID, catalog version, descriptor hash, raw/agent-ready/legacy
counts, build SHA, source fingerprint, and immutable image digest. A registry count or hash mismatch
is a hard admission failure.

## Admission checklist

- [ ] Canonical source repository is public and contains only the audited parentless history.
- [ ] Tag version equals the installed package and MCP `serverInfo.version`.
- [ ] GHCR image is public and anonymously pullable by exact digest.
- [ ] Release checksum, SBOM, provenance, and attestations exist.
- [ ] `/health` reports the expected build, source fingerprint, image reference, catalog, and counts.
- [ ] Missing and invalid service grants fail before request parsing.
- [ ] Duplicate privileged headers fail.
- [ ] Browser origins are deny-by-default.
- [ ] Provider credentials are request-scoped and absent from logs/responses.
- [ ] `check_configuration` succeeds with a synthetic scoped request without contacting Discord.
- [ ] Navigation tools work without provider credentials.
- [ ] Server channel, DM, admin, confirmation, attachment, and output ceilings are reviewed.
- [ ] One-replica or sticky-routing behavior is explicit.

Production Portal admission and deployment evidence belong in the private operator system, not in
the public source repository.
