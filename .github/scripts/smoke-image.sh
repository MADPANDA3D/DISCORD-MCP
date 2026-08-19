#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -lt 2 || $# -gt 4 ]]; then
  echo "usage: $0 IMAGE BUILD_SHA [SOURCE_FINGERPRINT] [IMAGE_REFERENCE]" >&2
  exit 2
fi

image=$1
build_sha=$2
source_fingerprint=${3:-development}
image_reference=${4:-development}
portal_grant=ci-portal-grant-000000000000000000000000000000000000000000
access_token=ci-standalone-token-000000000000000000000000000000000000000000
discord_token=synthetic-discord-token-000000000000000000000000000000000000000
guild_id=123456789012345678
active_container=

cleanup() {
  if [[ -n "$active_container" ]]; then
    docker rm -f "$active_container" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

for profile in portal standalone-server standalone-request; do
  active_container="discord-mcp-smoke-$profile"
  cleanup
  active_container="discord-mcp-smoke-$profile"

  case "$profile" in
    portal)
      mode_env=(
        -e MCP_MODE=portal
        -e DISCORD_CREDENTIAL_MODE=request
        -e DISCORD_ALLOWED_CHANNEL_IDS=ALL
        -e "MCP_PORTAL_GRANT_TOKEN=$portal_grant"
      )
      ;;
    standalone-server)
      mode_env=(
        -e MCP_MODE=standalone
        -e DISCORD_CREDENTIAL_MODE=server
        -e DISCORD_ALLOWED_CHANNEL_IDS=ALL
        -e "MCP_ACCESS_TOKEN=$access_token"
        -e "DISCORD_TOKEN=$discord_token"
        -e "DISCORD_GUILD_ID=$guild_id"
      )
      ;;
    standalone-request)
      mode_env=(
        -e MCP_MODE=standalone
        -e DISCORD_CREDENTIAL_MODE=request
        -e DISCORD_ALLOWED_CHANNEL_IDS=ALL
        -e "MCP_ACCESS_TOKEN=$access_token"
      )
      ;;
  esac

  docker run -d --rm --name "$active_container" \
    --init --network none --read-only --user 10001:10001 \
    --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777 \
    "${mode_env[@]}" \
    -e "MCP_BUILD_SHA=$build_sha" \
    -e "MCP_SOURCE_FINGERPRINT=$source_fingerprint" \
    -e "MCP_IMAGE_REFERENCE=$image_reference" \
    -e MCP_EXPECTED_TOOL_COUNT=52 \
    -e MCP_EXPECTED_AGENT_READY_COUNT=46 \
    -e MCP_EXPECTED_CATALOG_VERSION=discord-2026.08.19.1 \
    -e MCP_ALLOWED_HOSTS=127.0.0.1:*,localhost:* \
    "$image" >/dev/null

  ready=false
  for _ in {1..30}; do
    if docker exec "$active_container" python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8085/health', timeout=2).read()" \
      >/dev/null 2>&1; then
      ready=true
      break
    fi
    sleep 1
  done
  if [[ "$ready" != true ]]; then
    docker logs "$active_container" >&2 || true
    exit 1
  fi
  if ! smoke_output=$(
    docker exec "$active_container" python /app/scripts/runtime_smoke.py 2>&1
  ); then
    printf '%s\n' "$smoke_output" >&2
    docker logs "$active_container" >&2 || true
    exit 1
  fi
  printf '%s\n' "$smoke_output"
  cleanup
  active_container=
done
