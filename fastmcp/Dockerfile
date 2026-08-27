FROM python:3.12-slim

ARG BUILD_SHA=unknown

LABEL org.opencontainers.image.title="MADPANDA3D Discord MCP" \
      org.opencontainers.image.revision="${BUILD_SHA}" \
      org.opencontainers.image.version="${BUILD_SHA}"

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    groupadd --system --gid 10001 mcp && \
    useradd --system --uid 10001 --gid 10001 --home-dir /nonexistent --no-create-home mcp

COPY discord_mcp_server.py ./
COPY discord_admin_api.py ./
COPY tool_manifest.py ./

ENV DISCORD_TOKEN=""
ENV DISCORD_GUILD_ID=""
ENV MCP_BUILD_SHA="${BUILD_SHA}"
ENV MCP_HTTP_PORT="8085"
ENV MCP_BIND_ADDRESS="0.0.0.0"

EXPOSE 8085

USER 10001:10001

CMD ["python", "discord_mcp_server.py"]
