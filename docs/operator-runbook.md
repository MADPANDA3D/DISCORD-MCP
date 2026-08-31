# Operator runbook

This runbook covers public-source verification and service-scoped operation. Production hostnames,
secret-store locations, tenant data, private deployment evidence, and infrastructure commands belong
in the operator's private system.

## 1. Verify the release input

For a source checkout:

```bash
git status --short
git rev-parse HEAD
git archive HEAD | sha256sum
uv sync --frozen --group dev --python 3.12
uv run python scripts/check_source_safety.py
uv run python -m unittest discover -s tests -p 'test_*.py'
```

For a tagged release, first verify that the tagged commit is reachable from protected `main`, then
verify the attached `SHA256SUMS`, artifact attestations, and exact GHCR digest. Do not substitute a
mutable tag for the recorded digest.

During a private package bootstrap, the tagged workflow intentionally stops before release
finalization. Once both the GHCR package and canonical source are public, rerun the exact tagged
workflow so its live-visibility gate emits the public attestations and GitHub release.

## 2. Create private configuration

Standalone/server-owned credentials:

```bash
python3 scripts/init_runtime_env.py --mode standalone
```

Add `DISCORD_TOKEN`, `DISCORD_GUILD_ID`, and the narrowest practical
`DISCORD_ALLOWED_CHANNEL_IDS` to the ignored mode-`0600` `.env`.

For standalone/request BYOK, change `DISCORD_CREDENTIAL_MODE` to `request` and still configure a
nonempty server channel ceiling. Each provider call must then carry the request-scoped Discord
token, guild, and allowed-channel headers.

Portal/request-scoped credentials:

```bash
python3 scripts/init_runtime_env.py --mode portal
```

Keep provider values in the broker's secret store. The generated Portal scaffold sets a server
channel ceiling of `ALL` because the service requires a per-request allowed-channel header. Replace
it with a narrower global ceiling when appropriate.

Never copy a real `.env`, runtime compose override, credential export, or deployment evidence into
the repository. `.env.example` is the only public template.

## 3. Start a source build

```bash
docker compose up --detach --build --wait
docker compose ps
curl --fail http://127.0.0.1:8085/health
```

The published container targets `linux/amd64`. On other architectures, run the Python path after
the same source gates with `uv run --env-file .env mad-mcp-discord`; the generated listener is
loopback-only.

Expected health facts:

- `status` is `healthy`;
- `version` matches the package;
- `tool_count` is `56` and `agent_ready_tool_count` is `50`;
- `catalog_version` is `discord-2026.08.31.2`;
- build, source, image, and descriptor identities are present and non-sensitive;
- provider scope is `server_scoped` or `request_scoped` as configured.

`/health` is process readiness. In request mode, use `check_configuration` with the request's
Discord headers to prove that tenant's provider configuration.

## 4. Verify authentication and discovery

Use a disposable service credential and a private client configuration. Do not place a literal
token in shell history. The minimum wire checks are:

1. `/health` succeeds without authentication and contains no credential values.
2. `/mcp` without service authentication returns `401` before parsing malformed input.
3. A wrong service credential returns `401` without reflection.
4. An untrusted `Origin` returns `403`.
5. MCP `initialize` reports server version `1.1.1`.
6. `tools/list` returns exactly 56 tools.
7. `list_capabilities` returns the expected catalog and descriptor hash without a Discord call.
8. `check_configuration` reports ready for the intended provider mode.

CI performs this provider-free flow inside the final image for standalone/server,
standalone/request-BYOK, and Portal/request profiles with no external network.

## 5. Deploy an immutable release

Set both the Compose image selector and the validated health reference to the same digest:

```bash
export MCP_RUNTIME_IMAGE='ghcr.io/madpanda3d/discord-mcp-server@sha256:<digest>'
export MCP_IMAGE_REFERENCE="$MCP_RUNTIME_IMAGE"
docker compose pull
docker compose up --detach --no-build --wait
```

Confirm the pulled image ID and health identity before shifting traffic. The release image runs as
UID/GID `10001`, with a read-only root filesystem, no Linux capabilities, `no-new-privileges`, a PID
limit, and loopback-only host publication in the base Compose file.

## 6. Upgrade

1. Read `CHANGELOG.md` and release notes.
2. Verify checksums, attestations, SBOM, tag/version equality, and anonymous digest pull.
3. Record the current digest privately as the rollback target.
4. Update `MCP_RUNTIME_IMAGE` and `MCP_IMAGE_REFERENCE` to the new exact digest.
5. Pull without restarting the old container.
6. Re-run health, auth, discovery, catalog/hash, and request-mode configuration checks.
7. Restart the service and verify the same gates.
8. Confirm transient-job loss is acceptable before shifting traffic.

There is no database migration in this release. Request-scoped bot clients, audit cursors, jobs, and
caches are process-local and reset during replacement.

## 7. Roll back

1. Select the previously recorded immutable digest.
2. Set both image variables to that digest.
3. Start with `--no-build`.
4. Verify version, build, descriptor hash, tool counts, auth rejection, and configuration readiness.
5. Record the reason and evidence privately.

Do not roll back source or shared infrastructure to compensate for an application-only release
failure.

## 8. Logs and incident handling

Application logs intentionally remove credentials, URLs, exception detail, and message content by
default. Treat logs as sensitive anyway: guild/channel/user identifiers and action metadata can be
operationally revealing.

If a credential is suspected to be exposed:

1. Preserve relevant private evidence without copying it into a public issue.
2. Determine whether exposure is proven; do not assume a private value was leaked merely because a
   repository existed.
3. Rotate only the affected service/provider credential when evidence or policy requires it.
4. Re-run source/history secret scans and provider-free response/log tests.
5. Publish a sanitized advisory when users need action.

Use the private reporting path in `SECURITY.md` for vulnerabilities.

## 9. Troubleshooting

### Container is unhealthy

- Confirm server mode has a Discord token, valid guild snowflake, and non-empty channel ceiling.
- Confirm request mode has the correct service credential and derives/selects `request`.
- Confirm the container's internal port remains `8085`; change only `MCP_HOST_PORT`.
- Read `/health` and distinguish `degraded` configuration from process failure.

### MCP returns `401`

- Standalone requires a Bearer token matching `MCP_ACCESS_TOKEN`.
- Portal requires the exact configured service-grant header.
- Tokens shorter than 32 characters fail startup, not just a request.

### MCP returns `403` or `421`

- Check exact Origin and Host policy.
- Confirm the broker stripped duplicate/client-supplied privileged headers.

### Tool returns permission denied

- Run `check_configuration` in the same request context.
- Check server and request channel-policy intersection.
- Check blocked channels, DM/admin ceilings, confirmation, protected users/roles, and Discord role
  hierarchy.
- Confirm the bot has the provider intent/permission listed in `docs/tool-catalog.md`.

### Request mode works on one replica only

This is expected without sticky routing. Client pools, jobs, and cursors are local to one process.
Operate one replica or design and verify an external state layer before scaling out.
