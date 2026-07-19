# Security model

## Scope

Discord MCP is a network service that accepts MCP calls, uses a Discord bot credential, and can
read or mutate Discord resources. Its security model assumes the host, container runtime, trusted
Portal broker, and configured Discord bot are administered correctly. It does not claim to protect
against a compromised host or a malicious Discord bot owner.

The primary threats are unauthenticated access, cross-tenant credential/state reuse, excessive
Discord scope, accidental destructive calls, credential leakage, SSRF through attachments, oversized
inputs or outputs, and ambiguous release identity.

## Service access boundary

`/mcp` authenticates before request-body parsing:

- Standalone mode requires `Authorization: Bearer <MCP_ACCESS_TOKEN>`.
- Portal mode requires the configured service-grant header, defaulting to
  `X-MADPANDA-PORTAL-GRANT`.
- Service credentials must contain at least 32 characters.
- Missing and invalid credentials return stable `401` errors without reflecting values.
- Duplicate authorization, provider-credential, policy, host, origin, content-length, and
  transfer-encoding headers are rejected.

The middleware also enforces exact Host values, exact browser origins, a 1 MiB default body ceiling,
a 10-second default body-read timeout, and authentication before malformed JSON can reach MCP
protocol parsing.

`/health` is intentionally unauthenticated. It returns process readiness, catalog counts, and
validated release identity only. It never returns provider credentials, service credentials,
tenant identifiers, runtime paths, arbitrary environment values, or Discord content.

## Provider credential modes

### Server-owned

`DISCORD_CREDENTIAL_MODE=server` loads `DISCORD_TOKEN` and `DISCORD_GUILD_ID` at startup. The token
remains process-private and is never returned. Standalone health is ready only when the provider
credential, guild, and a non-empty server channel policy are configured.

### Request-scoped BYOK

`DISCORD_CREDENTIAL_MODE=request` requires each provider call to supply a Discord bot token, guild
ID, and allowed-channel policy through configured singleton headers. Portal mode requires this
credential mode. Navigation calls remain available behind service authentication without a Discord
credential so agents can discover the contract safely.

Request-scoped clients are pooled only in memory. The pool key is an HMAC credential fingerprint,
not a raw token. The default TTL is 900 seconds and the default maximum is 32 entries. Expiration
closes the Discord client and clears its credential-scoped channel cache.

## Authorization and policy intersection

Server policy is a ceiling; request policy can only narrow it.

- Server and request channel policies intersect.
- `DISCORD_BLOCKED_CHANNEL_IDS` and its request equivalent always deny access.
- Request flags cannot enable read-all, DMs, or admin operations unless the server enables them.
- A request cannot disable confirmation when the server requires it.
- Guild IDs and channel/user/role/message identifiers must parse as Discord snowflakes.
- Protected user and role lists block targeted operations even when Discord permissions exist.
- Discord's own permission and role hierarchy remains the final provider boundary.

Mutation descriptors marked for confirmation require the exact phrase `CONFIRM APPLY`. Admin-class
tools also require the server admin ceiling. DMs and OpenAI vision default off.

## Tenant and transient state

Request overrides use context-local state so concurrent calls do not share provider credentials or
policy. Credential-scoped clients and channel caches use opaque keyed fingerprints. Retained job,
audit, cache, and diagnostic data passes through secret-removal and output-boundary logic before
storage or return.

Jobs, cursors, caches, and pool ownership are process-local. A restart drops transient work. Multiple
replicas require sticky routing or an externalized state model that is not part of this release.

## Attachment ingestion

Remote attachment URLs must satisfy all of these conditions:

- HTTPS only, port 443 only;
- no URL credentials, fragments, malformed authority, IP literal, or non-canonical hostname;
- DNS resolution must produce only public addresses;
- the validated address set is pinned into the connection resolver;
- redirects and environment proxies are disabled;
- connect/read timeouts and the configured decoded-size ceiling apply.

The default attachment ceiling is 25 MiB. Local paths are allowed only in standalone/server mode,
must resolve beneath `MCP_ATTACHMENT_ALLOWED_DIRS`, and are revalidated before opening. Base64 input
is decoded incrementally under the same boundary.

These controls reduce SSRF and time-of-check/time-of-use risk; they do not make arbitrary remote
content trustworthy. Discord or OpenAI still parses content after admission.

## Optional OpenAI vision boundary

`analyze_attachment` is disabled unless `OPENAI_VISION_ENABLED=true`. Enabling it creates an
independent data-transfer, retention, trust, availability, and cost boundary. The selected Discord
image and the bounded prompt are sent only to the configured HTTPS OpenAI endpoint. Redirects,
environment proxies, response size, and request timeout are bounded. Startup rejects enabled
endpoints without HTTPS, a valid hostname, or with URL user information or fragments. Every mode
obtains the key per call from the configured singleton header; a trusted Portal broker must replace
client-supplied values before forwarding.

Operators must evaluate their own Discord data policy before enabling this feature.

## Output and logging controls

- Ordinary tool results are capped at 49,152 serialized bytes.
- Full catalog responses are capped at 1 MiB.
- Provider exceptions become fixed semantic error classes; raw exception detail is not returned.
- Logs scrub active credentials, webhook capability URLs, Discord CDN capability URLs, general
  URLs, and exception traces.
- Message content is redacted from logs by default.
- Structured third-party logging arguments are scrubbed without breaking formatter contracts.
- Health identity accepts only commit/digest formats and fixed sentinels.

Authorized reads can still return Discord message content and user metadata by design. Clients and
brokers must treat successful MCP results as sensitive data.

## Container boundary

The supplied Compose service runs as UID/GID `10001`, uses a read-only root filesystem, drops all
Linux capabilities, enables `no-new-privileges`, sets a PID limit, mounts only a small no-exec tmpfs,
and publishes the service on loopback. The image contains no default credential, runtime volume, or
operator path.

No CPU or memory limit is claimed in the base Compose file. Production operators should add limits
appropriate to their runtime and expected Discord workload.

## Release and supply-chain boundary

CI verifies full-history secrets, public-source content, Python 3.12/3.13 behavior, locked dependency
resolution, package allowlists, source/image vulnerabilities, provider-free wire behavior, and
container hardening. Tagged releases verify exact tag/version equality, generate checksums and SBOM
data, attest artifacts, publish an immutable GHCR digest, then require an anonymous pull and repeat
the image smoke.

Deployment should select the exact digest and set the matching validated image reference. Mutable
tags are convenience aliases, not deployment identity.

## Known limitations

- The service trusts Discord to enforce provider permissions and role hierarchy correctly.
- State is single-process and non-durable.
- There is no built-in per-principal rate limiter beyond Discord/provider and bounded pool/job limits.
- `/health` proves service readiness, not tenant-specific Discord readiness.
- The server does not provide OAuth installation or token rotation.
- A compromised host, broker, or bot credential remains outside the application boundary.

Report suspected vulnerabilities using the private path in [SECURITY.md](../SECURITY.md), never a
public issue containing credentials or exploit details.
