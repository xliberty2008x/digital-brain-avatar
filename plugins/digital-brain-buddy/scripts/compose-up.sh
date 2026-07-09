#!/bin/bash
set -uo pipefail

PLUGIN_NAME="digital-brain-buddy"
# Cold neo4j pull/recreate often exceeds 60s (compose healthcheck: 10s interval × 12 retries).
# Leave headroom under hooks.json timeout for mcp-cypher --build after neo4j is healthy.
NEO4J_HEALTH_MAX_ATTEMPTS="${NEO4J_HEALTH_MAX_ATTEMPTS:-60}"  # 60 × 2s = 120s
NEO4J_HEALTH_SLEEP_SECS="${NEO4J_HEALTH_SLEEP_SECS:-2}"

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

max_wait_secs=$((NEO4J_HEALTH_MAX_ATTEMPTS * NEO4J_HEALTH_SLEEP_SECS))
echo "$PLUGIN_NAME: waiting for neo4j healthcheck (up to ${max_wait_secs}s)..."
attempt=0
exited_streak=0
while true; do
  container_id="$(docker compose ps -aq neo4j 2>/dev/null || true)"
  if [ -n "$container_id" ]; then
    running="$(docker inspect -f '{{.State.Running}}' "$container_id" 2>/dev/null || true)"
    exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$container_id" 2>/dev/null || true)"
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
    if [ "$status" = "healthy" ]; then
      echo "$PLUGIN_NAME: neo4j is healthy"
      break
    fi
    # Fail fast if the container crashed (e.g. image/store version mismatch).
    if [ "$running" = "false" ] && [ "${exit_code:-0}" != "0" ]; then
      exited_streak=$((exited_streak + 1))
      if [ "$exited_streak" -ge 3 ]; then
        warn_and_exit "neo4j container exited (status=${status}, exit=${exit_code}); check 'docker compose logs neo4j' — often an image vs data-volume version mismatch"
      fi
    else
      exited_streak=0
    fi
  else
    status="missing"
  fi
  attempt=$((attempt + 1))
  if [ "$attempt" -ge "$NEO4J_HEALTH_MAX_ATTEMPTS" ]; then
    warn_and_exit "neo4j did not become healthy within ${max_wait_secs}s (last status: ${status:-unknown}); re-run /digital-brain-up or check docker compose ps"
  fi
  # Progress every ~10s so SessionStart logs are not silent on cold start
  if [ $((attempt % 5)) -eq 0 ]; then
    echo "$PLUGIN_NAME: still waiting for neo4j (status=${status:-unknown}, ${attempt}/${NEO4J_HEALTH_MAX_ATTEMPTS})..."
  fi
  sleep "$NEO4J_HEALTH_SLEEP_SECS"
done

# Rebuild + recreate mcp-cypher so SessionStart / /digital-brain-up pick up
# local source changes (hard-reject, embeddings). Cached layers keep builds
# cheap when nothing changed; --force-recreate ensures the container is not
# left running an old image under the same tag.
echo "$PLUGIN_NAME: building and (re)starting mcp-cypher from local sources..."
if ! docker compose --profile ollama up -d --build --force-recreate mcp-cypher; then
  warn_and_exit "failed to build/start mcp-cypher"
fi

echo "$PLUGIN_NAME: local Neo4j + Cypher MCP stack is up (mcp-cypher rebuilt)"
exit 0
