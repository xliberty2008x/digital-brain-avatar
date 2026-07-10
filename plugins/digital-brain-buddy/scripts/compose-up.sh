#!/bin/bash
set -uo pipefail

PLUGIN_NAME="digital-brain-buddy"
# Cold service pulls/recreates can exceed a minute. Leave headroom under the
# hook timeout for the mcp-cypher build plus its real /readyz embedding probe.
NEO4J_HEALTH_MAX_ATTEMPTS="${NEO4J_HEALTH_MAX_ATTEMPTS:-60}"  # 60 × 2s = 120s
OLLAMA_HEALTH_MAX_ATTEMPTS="${OLLAMA_HEALTH_MAX_ATTEMPTS:-60}"  # 60 × 2s = 120s
MCP_READY_MAX_ATTEMPTS="${MCP_READY_MAX_ATTEMPTS:-90}"  # 90 × 2s = 180s
NEO4J_HEALTH_SLEEP_SECS="${NEO4J_HEALTH_SLEEP_SECS:-2}"
MIN_DOCKER_MEMORY_BYTES=$((6 * 1024 * 1024 * 1024))

warn_and_exit() {
  echo "$PLUGIN_NAME: $1" >&2
  exit 0
}

wait_for_service_health() {
  service="$1"
  max_attempts="$2"
  wait_label="$3"
  max_wait_secs=$((max_attempts * NEO4J_HEALTH_SLEEP_SECS))
  attempt=0
  exited_streak=0

  echo "$PLUGIN_NAME: waiting for ${wait_label} healthcheck (up to ${max_wait_secs}s)..."
  while true; do
    container_id="$(docker compose ps -aq "$service" 2>/dev/null | head -n 1 || true)"
    if [ -n "$container_id" ]; then
      running="$(docker inspect -f '{{.State.Running}}' "$container_id" 2>/dev/null || true)"
      exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$container_id" 2>/dev/null || true)"
      status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
      if [ "$status" = "healthy" ]; then
        echo "$PLUGIN_NAME: ${wait_label} is healthy"
        return 0
      fi
      if [ "$status" = "unhealthy" ]; then
        echo "$PLUGIN_NAME: ${wait_label} healthcheck is unhealthy; inspect 'docker compose logs $service'" >&2
        return 1
      fi
      # Fail fast if the container crashed (for example, a store/image mismatch
      # or an OOM-killed service) instead of later claiming the stack is ready.
      if [ "$running" = "false" ] && [ "${exit_code:-0}" != "0" ]; then
        exited_streak=$((exited_streak + 1))
        if [ "$exited_streak" -ge 3 ]; then
          echo "$PLUGIN_NAME: ${wait_label} container exited (status=${status}, exit=${exit_code}); inspect 'docker compose logs $service'" >&2
          return 1
        fi
      else
        exited_streak=0
      fi
    else
      status="missing"
    fi
    attempt=$((attempt + 1))
    if [ "$attempt" -ge "$max_attempts" ]; then
      echo "$PLUGIN_NAME: ${wait_label} did not become healthy within ${max_wait_secs}s (last status: ${status:-unknown}); inspect 'docker compose logs $service'" >&2
      return 1
    fi
    # Progress every ~10s so SessionStart logs are not silent on cold start.
    if [ $((attempt % 5)) -eq 0 ]; then
      echo "$PLUGIN_NAME: still waiting for ${wait_label} (status=${status:-unknown}, ${attempt}/${max_attempts})..."
    fi
    sleep "$NEO4J_HEALTH_SLEEP_SECS"
  done
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

docker_memory_bytes="$(docker info --format '{{.MemTotal}}' 2>/dev/null || true)"
case "$docker_memory_bytes" in
  ''|*[!0-9]*)
    warn_and_exit "could not determine Docker memory; allocate at least 6 GiB in Docker Desktop before bringing up the embedding stack"
    ;;
esac
if [ "$docker_memory_bytes" -lt "$MIN_DOCKER_MEMORY_BYTES" ]; then
  docker_memory_mib=$((docker_memory_bytes / 1024 / 1024))
  warn_and_exit "Docker has ${docker_memory_mib} MiB available; allocate at least 6 GiB in Docker Desktop before bringing up Neo4j + Ollama"
fi

if ! docker compose up -d neo4j ollama; then
  warn_and_exit "failed to start neo4j/ollama, skipping mcp-cypher bring-up"
fi

if ! wait_for_service_health neo4j "$NEO4J_HEALTH_MAX_ATTEMPTS" "neo4j"; then
  warn_and_exit "neo4j is not healthy; no MCP service was started"
fi

if ! wait_for_service_health ollama "$OLLAMA_HEALTH_MAX_ATTEMPTS" "ollama"; then
  warn_and_exit "ollama is not healthy; no MCP service was started"
fi

# Optional: apply runtime/quality Neo4j roles when credentials are configured.
# Explicit, non-silent — skipped unless DIGITAL_BRAIN_APPLY_QUALITY_ROLES=1 and
# NEO4J_RUNTIME_PASSWORD + NEO4J_QUALITY_PASSWORD are set. Never mounts operator
# activation credentials into mcp-cypher. Does not run Operational backfill
# migration (scripts/migrate_operational_labels.py is operator-reviewed only).
if [ "${DIGITAL_BRAIN_APPLY_QUALITY_ROLES:-0}" = "1" ]; then
  if [ -n "${NEO4J_RUNTIME_PASSWORD:-}" ] && [ -n "${NEO4J_QUALITY_PASSWORD:-}" ]; then
    echo "$PLUGIN_NAME: applying Neo4j runtime/quality roles (reviewed bootstrap)..."
    if command -v uv >/dev/null 2>&1; then
      if ! uv run --group dev python scripts/init_quality_roles.py --apply; then
        echo "$PLUGIN_NAME: quality role bootstrap failed; continuing with existing Neo4j auth" >&2
      fi
    else
      echo "$PLUGIN_NAME: uv not found; skip quality role bootstrap" >&2
    fi
  else
    echo "$PLUGIN_NAME: DIGITAL_BRAIN_APPLY_QUALITY_ROLES=1 but runtime/quality passwords unset; skip" >&2
  fi
fi

# Rebuild + recreate mcp-cypher so SessionStart / /digital-brain-up pick up
# local source changes (hard-reject, embeddings). Cached layers keep builds
# cheap when nothing changed; --force-recreate ensures the container is not
# left running an old image under the same tag.
echo "$PLUGIN_NAME: building and (re)starting mcp-cypher from local sources..."
if ! docker compose up -d --build --force-recreate --no-deps mcp-cypher; then
  warn_and_exit "failed to build/start mcp-cypher"
fi

if ! wait_for_service_health mcp-cypher "$MCP_READY_MAX_ATTEMPTS" "mcp-cypher (/readyz)"; then
  warn_and_exit "mcp-cypher is not ready for writes; bge-m3 may be unavailable or the embedding backend may be unhealthy"
fi

echo "$PLUGIN_NAME: local Neo4j + Ollama + Cypher MCP stack is ready for writes"
exit 0
