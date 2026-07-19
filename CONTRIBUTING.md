# Contributing

Thank you for improving Discord MCP. Contributions should preserve the server's fail-closed access,
policy, packaging, and public/private boundaries.

## Before opening a change

- Search existing issues and confirm the request belongs in the documented endpoint scope.
- Use a private security advisory for vulnerabilities or possible credential exposure.
- Never attach real Discord tokens, webhook URLs, guild exports, message content, private hostnames,
  runtime `.env` files, agent instructions, tickets, handovers, or deployment evidence.
- Keep Discord and OpenAI calls mocked in tests. Public CI must remain provider-free.
- Do not create stable tags from feature branches. Release tags are maintainer-only and must point
  to commits already reachable from protected `main`.

## Development setup

```bash
git clone https://github.com/MADPANDA3D/DISCORD-MCP.git
cd DISCORD-MCP
uv sync --frozen --group dev --python 3.12
```

Run the release-relevant local gates:

```bash
uv run python -m compileall -q src tests scripts
uv run python -m unittest discover -s tests -p 'test_*.py'
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run bandit -q -r src/madpanda_discord_mcp -lll
uv run pip-audit --requirement requirements.lock
uv run python scripts/check_source_safety.py
uv build
uv run twine check dist/*
uv run python scripts/check_package_archives.py
```

Test Python 3.13 as well when behavior or dependencies change.

## Tool-contract changes

A tool change is incomplete until all affected surfaces agree:

1. native FastMCP registration;
2. deterministic ToolManifest definition;
3. input and output descriptions/schemas;
4. risk, tier, admin, and confirmation annotations;
5. endpoint coverage and tool documentation;
6. unit and wire tests;
7. catalog version and descriptor-hash expectations.

Do not add arbitrary raw Discord request tools or return webhook credential URLs. New provider
operations must have a clear scope, stable semantic errors, bounded results, and explicit policy.

## Security-sensitive changes

Authentication, request headers, attachment fetching, file paths, output/log redaction, state
retention, confirmation, and Discord permission changes need adversarial tests. Do not weaken a gate
to make CI pass. Document residual risk in `docs/security-model.md`.

## Pull requests

- Keep the change focused and explain the user-visible outcome.
- Include tests and documentation for behavior changes.
- State which Python versions and gates you ran.
- Call out compatibility, catalog, release, and security impact.
- Do not mix private deployment work with public source changes.

Maintainers may request a smaller change when reviewability or the public boundary would otherwise
be compromised.
