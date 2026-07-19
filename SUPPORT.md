# Support

## Usage and documentation

Start with:

- [README](README.md)
- [Operator runbook](docs/operator-runbook.md)
- [Tool catalog](docs/tool-catalog.md)
- [Endpoint coverage](docs/endpoint-coverage.md)
- [Portal compatibility](docs/portal-compat.md)
- [Security model](docs/security-model.md)

For a reproducible non-security bug, open a GitHub issue with the issue template. Include the package
version or image digest, service/credential mode, sanitized health fields, expected behavior, and the
smallest provider-free reproduction available.

Feature requests should explain the agent workflow, intended Discord resource, required permissions,
output bounds, and why the existing typed tools do not cover the use case.

## Not appropriate for public issues

Do not post credentials, webhook URLs, guild/message exports, private endpoint names, runtime files,
deployment evidence, tenant identifiers, or uncoordinated vulnerability details. Use the private
path in [SECURITY.md](SECURITY.md) for security reports.

This project does not provide Discord account support, bot verification, OAuth installation, managed
hosting, or a guaranteed support SLA.
