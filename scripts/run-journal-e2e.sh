#!/bin/bash
# Run the disposable, no-host-port JournalEntry append E2E stack.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.journal-e2e.yml"
PROJECT_NAME="${JOURNAL_E2E_PROJECT_NAME:-avatar_digital_brain_journal_e2e}"
WAIT_TIMEOUT_SECONDS="${JOURNAL_E2E_WAIT_TIMEOUT_SECONDS:-360}"
# Docker Desktop's 6 GiB UI allocation reports as roughly 5.8 GiB through
# `docker info` because the Linux guest reserves memory for its kernel. Keep
# the documented Desktop requirement at 6 GiB while checking the guest value
# that is actually available to the E2E containers.
MIN_DOCKER_GUEST_MEMORY_BYTES=$((5900 * 1024 * 1024))

fail() {
  echo "journal-e2e: $*" >&2
  exit 1
}

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  fail "Docker Compose is required"
fi

docker_memory_bytes="$(docker info --format '{{.MemTotal}}' 2>/dev/null || true)"
case "$docker_memory_bytes" in
  ''|*[!0-9]*) fail "could not determine Docker memory; allocate at least 6 GiB" ;;
esac
if [ "$docker_memory_bytes" -lt "$MIN_DOCKER_GUEST_MEMORY_BYTES" ]; then
  fail "Docker has $((docker_memory_bytes / 1024 / 1024)) MiB; allocate at least 6 GiB before running this E2E"
fi

cd "$ROOT_DIR"
main_stack_running="$(docker compose -f docker-compose.yml ps -q 2>/dev/null || true)"
if [ -n "$main_stack_running" ]; then
  fail "the normal Compose stack is running; stop it before this isolated E2E to avoid competing resource use"
fi

compose=(docker compose --project-name "$PROJECT_NAME" --file "$COMPOSE_FILE")
cleanup() {
  if [ "${JOURNAL_E2E_KEEP_ARTIFACTS:-0}" = "1" ]; then
    echo "journal-e2e: preserving isolated containers and volumes for inspection"
  else
    "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "journal-e2e: starting isolated Neo4j, Ollama bge-m3, and MCP readiness checks..."
if ! "${compose[@]}" up --detach --build --wait --wait-timeout "$WAIT_TIMEOUT_SECONDS" journal-e2e-mcp-cypher; then
  "${compose[@]}" logs --no-color --tail=100 >&2 || true
  fail "isolated MCP stack did not become ready"
fi

echo "journal-e2e: running MCP-only bootstrap and append/replay/concurrency smoke..."
"${compose[@]}" run --rm --build --no-deps journal-e2e
