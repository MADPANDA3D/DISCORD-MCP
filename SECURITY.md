# Security policy

## Supported version

Security fixes target the latest released major/minor line. Before the first public release, the
audited `main` candidate is the only supported source state.

| Version | Supported |
|---|---|
| Latest `1.x` | Yes |
| Legacy Java/Node history and tags | No |

## Report a vulnerability

Use a private
[GitHub security advisory](https://github.com/MADPANDA3D/DISCORD-MCP/security/advisories/new).
Do not open a public issue for vulnerabilities, suspected credential exposure, tenant data, or an
exploit that has not been coordinated.

Include only sanitized information needed to reproduce the problem:

- affected version, commit, or immutable image digest;
- impacted mode and credential mode;
- minimal provider-free reproduction when possible;
- expected and observed behavior;
- security impact and any known mitigations.

Never send a real service token, Discord bot token, webhook URL, OpenAI key, message export, private
hostname, secret-store path, or runtime `.env`. Replace values with unmistakable synthetic markers.

Maintainers will acknowledge the report, validate impact, coordinate a fix and release, and publish
sanitized guidance when users need to act. Timing depends on severity and reproducibility; no fixed
embargo or response SLA is promised.

## Security boundary

Read [Security model](docs/security-model.md) before reporting expected behavior as a vulnerability.
In particular, a compromised host/broker, an intentionally over-permissioned Discord bot, authorized
access to sensitive Discord content, and process-local transient state are documented trust or
operational boundaries.
