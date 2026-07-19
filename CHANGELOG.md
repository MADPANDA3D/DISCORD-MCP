# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and releases use
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-07-19

### Added

- Python 3.12/3.13 package `mad-mcp-discord` and `mad-mcp-discord` console command.
- Authenticated Streamable HTTP service with standalone and Portal modes.
- Server-owned and request-scoped Discord credential modes.
- Deterministic 50-tool catalog with 47 agent-ready and 3 legacy tools.
- Channel, DM, admin, confirmation, protected-target, attachment, output, and log boundaries.
- Non-root, read-only, loopback-only container and digest-selectable Compose deployment.
- Locked dependencies, wheel/sdist allowlist, provider-free smoke of all three authenticated
  credential profiles, source/image scans, CodeQL, SBOM, provenance, attestations, and anonymous
  GHCR pull gate.
- Public operator, Portal, security, endpoint, tool, and provenance documentation.

### Changed

- Replaced the legacy Java/Node hybrid build with one Python package and one runtime.
- Normalized tool errors and bounded retained job, provider, and catalog output.
- Made webhook support credential-safe by limiting it to listing and guarded deletion.
- Reset the public release line to `1.0.0` at the clean Python-history boundary.

### Removed

- Java/Maven runtime, Node/semantic-release automation, n8n screenshots, internal agent instructions,
  and private operational material from the public candidate.
- Webhook creation and token-URL execution tools.
- Unauthenticated and permissive public-mode behavior.

[1.0.0]: https://github.com/MADPANDA3D/DISCORD-MCP/releases/tag/v1.0.0
