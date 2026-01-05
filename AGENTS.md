# Repository Guidelines

## Project Structure & Module Organization
- `src/main/java/dev/saseq/`: Spring Boot MCP server (services, configs, controllers).
- `src/main/resources/`: application configuration (`application.properties`).
- `fastmcp/`: Python FastMCP HTTP transport (`discord_mcp_server.py`, `docker-compose.yaml`, `requirements.txt`).
- `assets/`: logos and documentation assets.
- Top-level `Dockerfile` and `pom.xml`: Java build and containerization.

## Build, Test, and Development Commands
- `mvn clean package`: Build the Java server JAR into `target/`.
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
- If you add tests, introduce the framework in `pom.xml` or `fastmcp/requirements.txt` and document the run command here.

## Commit & Pull Request Guidelines
- Commit messages use short, imperative subjects (e.g., “Fix FastMCP port mapping”); avoid scopes/prefixes unless needed.
- PRs should include: clear summary, linked issue (if any), steps to verify, and notes on any environment variable or config changes.
- Update `README.md` when behavior or deployment steps change.

## Security & Configuration Tips
- Required env: `DISCORD_TOKEN`. Optional: `DISCORD_GUILD_ID`.
- Transport/env settings: `MCP_HTTP_PORT`, `MCP_BIND_ADDRESS`, `MCP_TRANSPORT`, `MCP_STDIO`.
- Never commit tokens or `.env` files.
