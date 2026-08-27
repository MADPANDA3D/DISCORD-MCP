# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and releases use
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-08-27

### Added

- Added three policy-bounded server-management tools backed by an explicit allowlist of 125
  official stable bot-token actions: 56 read, 22 write, and 47 destructive.
- Added complete endpoint coverage documentation, action-enum parity tests, permission preflight,
  effective channel-permission calculation, role hierarchy and protected-target guards, bounded
  provider results, and credential redaction.

### Changed

- Raised the immutable runtime catalog to `discord-2026.08.27.1` with 55 registered and 49
  agent-ready tools.
- Raised the package, server, image, and release version to `1.1.0`.

## [1.0.1] - 2026-08-19

### Changed

- Restored the preserved 52-tool Discord contract, including three hidden webhook compatibility
  tools, while keeping credential-bearing webhook URLs out of results and logs.
- Added a machine-checked compatibility projection and the full tool-by-tool parity matrix.
- Raised `aiohttp` and `cryptography` to patched security floors and regenerated both lock surfaces
  with the repository-pinned uv version.

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

[1.1.0]: https://github.com/MADPANDA3D/DISCORD-MCP/releases/tag/v1.1.0
[1.0.1]: https://github.com/MADPANDA3D/DISCORD-MCP/releases/tag/v1.0.1
[1.0.0]: https://github.com/MADPANDA3D/DISCORD-MCP/releases/tag/v1.0.0
