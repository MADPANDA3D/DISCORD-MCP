# Repository Guidelines

## Project Structure & Module Organization
- `src/main/java/dev/saseq/`: Spring Boot MCP server (services, configs, controllers).
- `src/main/resources/`: application configuration (`application.properties`).
- `fastmcp/`: Python FastMCP HTTP transport (`discord_mcp_server.py`, `docker-compose.yaml`, `requirements.txt`, `.env.example`).
- `assets/`: logos and documentation assets.
- Top-level `Dockerfile` and `pom.xml`: Java build and containerization.

## Build, Test, and Development Commands
- `mvn clean package`: Build the Java server JAR into `target/` (requires JDK 17).
- `mvn spring-boot:run`: Run the Java MCP server locally.
- `java -jar target/discord-mcp-*.jar`: Run the built JAR.
- `pip install -r fastmcp/requirements.txt`: Install FastMCP Python dependencies.
- `python fastmcp/discord_mcp_server.py`: Run the FastMCP HTTP server.
- `docker compose -f fastmcp/docker-compose.yaml up -d --build`: Run FastMCP via Docker Compose.

## Coding Style & Naming Conventions
- Java: 4-space indent, `dev.saseq` packages, PascalCase classes, camelCase methods/fields.
- Python: 4-space indent, snake_case functions/variables, keep async APIs as `async def`.
- No formatter/linter is configured; follow existing file layout and import grouping.

## Testing Guidelines
- No automated tests are configured in this repo and no coverage requirements are defined.
- If you add tests, place Java tests under `src/test/java` and Python tests under `fastmcp/tests`, then document the run command here.

## Commit & Pull Request Guidelines
- Commit messages must follow Conventional Commits (enforced by commitlint in CI); e.g., `feat: add webhook audit`, `fix(fastmcp): handle header overrides`.
- Keep subjects short and imperative; include a scope only when it adds clarity.
- PRs should include: clear summary, linked issue (if any), steps to verify, and notes on any environment variable or config changes.
- Update `README.md` when behavior or deployment steps change.

## Security & Configuration Tips
- Required env: `DISCORD_TOKEN`; common optional: `DISCORD_GUILD_ID`.
- Transport/env settings: `MCP_HTTP_PORT`, `MCP_BIND_ADDRESS`, `MCP_TRANSPORT`, `MCP_STDIO`.
- FastMCP extras: `DISCORD_PRIMARY_CHANNEL_ID`, `DISCORD_ALLOWED_CHANNEL_IDS`, `MCP_ADMIN_TOOLS_ENABLED`, `DISCORD_CHANNEL_CACHE_TTL_SECONDS`, `DISCORD_JOB_TTL_SECONDS`, `LOG_LEVEL`.
- Use `fastmcp/.env.example` as a template and keep `.env` files out of git.

## Agent-Specific Instructions
- Use GitHub MCP for all GitHub actions (commits, pushes, PRs, issues). For large change sets, make local git commits with author name `MADPANDA3D`, then do a final polish commit via GitHub MCP; MCP use can be overridden when explicitly asked.
- Use Perplexity for open research and Context7 for library/framework documentation.
- After applying fixes, always rebuild and restart the Docker container (e.g., `docker compose -f fastmcp/docker-compose.yaml up -d --build`).
- Always use Conventional Commits for local changes; semantic-release relies on commitlint passing.
