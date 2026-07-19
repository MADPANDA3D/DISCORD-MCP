# Provenance

## Upstream lineage

Discord MCP preserves MIT-licensed work from
[SaseQ/discord-mcp](https://github.com/SaseQ/discord-mcp).

| Boundary | Git object |
|---|---|
| Original repository root | `12ea461a01f737dc7ff370d85078af4a1fc50f95` |
| Last upstream SaseQ baseline | `ac33c4f492fcd99b08aa9dfbd95f8ec4279c1e72` |
| Upstream baseline tree | `ccceefc5b7b4191a93b5e80799d7ce1c694ec46e` |
| Deterministic `git archive` SHA-256 | `4131c1ceced7c9d68d4c7e333e21f3dfeae3be8cfb8f9ad2dc3785bfda0263ce` |
| Last pre-Python MADPANDA commit | `41c41092fb36ba0950e55e5f1e5d2c994847500e` |
| First FastMCP implementation | `b2e329fab83df87da9a7ba8ac222088e2fa21d43` |

The deterministic upstream archive hash can be reproduced from a repository containing the legacy
object with:

```bash
git archive --format=tar ac33c4f492fcd99b08aa9dfbd95f8ec4279c1e72 | sha256sum
```

The original SaseQ copyright and MIT text remain in [LICENSE](../LICENSE). MADPANDA3D's Python
implementation, security boundaries, packaging, documentation, and release automation are described
in [NOTICE](../NOTICE).

## Public-history boundary

The canonical public product begins from an allowlisted Python-only snapshot. Legacy Java/Node
builds, internal agent instructions, release experiments, screenshots, and operational evidence are
not imported into the new public Git history. The original refs are preserved in a separately
verified private archival bundle; that archive is not part of the product repository or release.

This boundary is deliberate. A deletion commit inside the old history would not remove historical
operator material, while a parentless audited root makes the public source set explicit and
reviewable.

The clean public release starts at version `1.0.0`. Legacy tags are not imported, because their
artifacts describe a different Java/Node-era product and release process.

## Asset lineage

- `assets/brand/discord-mcp-logo.svg` originates in the SaseQ root commit and remains under MIT.
- `assets/brand/discord-mcp-icon.svg` was added by SaseQ in
  `dc02349a96a2a5b5ea3dc1c49c87fa57fe26baa3` and remains under MIT.
- `assets/brand/header.jpg` was added by MADPANDA3D in
  `0245c5821521f26fcc2bb97a7cb5f747761e7a86`.

Discord names and marks belong to Discord Inc. Asset inclusion does not imply affiliation,
endorsement, or sponsorship.

## Release identity

The package version comes from `pyproject.toml`. MCP `serverInfo.version` and `/health.version`
report the installed distribution version. Release health also exposes only validated identity:

- `build_sha`: a Git commit hash or `unknown`;
- `source_fingerprint`: `development`, a lowercase SHA-256, or `unknown`;
- `image_reference`: `development`, the immutable MADPANDA3D GHCR digest, or `unknown`;
- `descriptor_hash`: the deterministic materialized tool-catalog hash.

Arbitrary environment text is never reflected through the unauthenticated health endpoint.
