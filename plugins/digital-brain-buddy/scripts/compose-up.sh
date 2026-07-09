#!/bin/bash
set -uo pipefail

PLUGIN_NAME="digital-brain-buddy"

warn_and_exit() {
  echo "$PLUGIN_NAME: $1" >&2
  exit 0
}

echo "$PLUGIN_NAME: bringing up local Neo4j + Cypher MCP stack..."

if [ -z "${CLAUDE_PROJECT_DIR:-}" ]; then
  warn_and_exit "CLAUDE_PROJECT_DIR not set, skipping compose bring-up"
fi

cd "$CLAUDE_PROJECT_DIR" || warn_and_exit "cannot cd to CLAUDE_PROJECT_DIR ($CLAUDE_PROJECT_DIR), skipping compose bring-up"

if [ ! -f docker-compose.yml ] && [ ! -f compose.yml ]; then
  warn_and_exit "no docker-compose.yml in $CLAUDE_PROJECT_DIR (expected avatar_digital_brain repo root), skipping compose bring-up"
fi

if ! command -v docker >/dev/null 2>&1; then
  warn_and_exit "docker not found, skipping local Neo4j/MCP bring-up"
fi

if ! docker compose version >/dev/null 2>&1; then
  warn_and_exit "docker compose not available, skipping local Neo4j/MCP bring-up"
fi

if ! docker compose --profile ollama up -d neo4j ollama; then
  warn_and_exit "failed to start neo4j/ollama, skipping mcp-cypher bring-up"
fi

echo "$PLUGIN_NAME: waiting for neo4j healthcheck..."
attempt=0
max_attempts=30
while true; do
  container_id="$(docker compose ps -q neo4j 2>/dev/null)"
  status="$(docker inspect -f '{{.State.Health.Status}}' "$container_id" 2>/dev/null || true)"
  if [ "$status" = "healthy" ]; then
    break
  fi
  attempt=$((attempt + 1))
  if [ "$attempt" -ge "$max_attempts" ]; then
    warn_and_exit "neo4j did not become healthy within 60s, skipping mcp bring-up"
  fi
  sleep 2
done

# Always rebuild mcp-cypher so SessionStart / /digital-brain-up pick up
# local source changes (hard-reject, embeddings). Cached layers keep this
# cheap when nothing changed; cold builds may approach the hook timeout.
echo "$PLUGIN_NAME: building mcp-cypher image from local sources..."
if ! docker compose --profile ollama build mcp-cypher; then
  warn_and_exit "failed to build mcp-cypher"
fi

if ! docker compose --profile ollama up -d mcp-cypher; then
  warn_and_exit "failed to start mcp-cypher"
fi

echo "$PLUGIN_NAME: local Neo4j + Cypher MCP stack is up (mcp-cypher rebuilt)"
exit 0
