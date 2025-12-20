FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY discord_mcp_server.py ./

ENV DISCORD_TOKEN=""
ENV DISCORD_GUILD_ID=""
ENV MCP_HTTP_PORT="8085"
ENV MCP_BIND_ADDRESS="0.0.0.0"

EXPOSE 8085

CMD ["python", "discord_mcp_server.py"]
