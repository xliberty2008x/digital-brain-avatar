#!/bin/bash
# Dreams release gate: in-process pytest E2E + optional isolated Neo4j role smoke.
#
# Always runs (no Docker required):
#   - tests/e2e/dream_workflow_smoke.py
#   - tests/e2e/dream_crash_recovery.py
#
# Optional Docker stack (set DREAMS_E2E_DOCKER=1):
#   - docker-compose.dreams-e2e.yml with quality role smoke
#   Skips cleanly when Docker is unavailable unless DREAMS_E2E_REQUIRE_DOCKER=1.
#
# Live Grok is never required. Deterministic fake analyzer only.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.dreams-e2e.yml"
PROJECT_NAME="${DREAMS_E2E_PROJECT_NAME:-avatar_digital_brain_dreams_e2e}"
WAIT_TIMEOUT_SECONDS="${DREAMS_E2E_WAIT_TIMEOUT_SECONDS:-180}"
MIN_DOCKER_GUEST_MEMORY_BYTES=$((1500 * 1024 * 1024))

cd "$ROOT_DIR"

fail() {
  echo "dreams-e2e: $*" >&2
  exit 1
}

info() {
  echo "dreams-e2e: $*"
}

# ---------------------------------------------------------------------------
# 1) Required in-process gates (unit-CI equivalent)
# ---------------------------------------------------------------------------
info "running in-process dream workflow + crash-recovery gates..."
if command -v uv >/dev/null 2>&1; then
  uv run --group dev python -m pytest \
    tests/e2e/dream_workflow_smoke.py \
    tests/e2e/dream_crash_recovery.py \
    -q --tb=short
else
  python -m pytest \
    tests/e2e/dream_workflow_smoke.py \
    tests/e2e/dream_crash_recovery.py \
    -q --tb=short
fi
info "in-process gates passed"

# ---------------------------------------------------------------------------
# 2) Optional Docker role smoke
# ---------------------------------------------------------------------------
if [ "${DREAMS_E2E_DOCKER:-0}" != "1" ]; then
  info "skip docker role smoke (set DREAMS_E2E_DOCKER=1 to enable)"
  exit 0
fi

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  if [ "${DREAMS_E2E_REQUIRE_DOCKER:-0}" = "1" ]; then
    fail "Docker Compose required when DREAMS_E2E_REQUIRE_DOCKER=1"
  fi
  info "skip docker role smoke: Docker Compose not available"
  exit 0
fi

docker_memory_bytes="$(docker info --format '{{.MemTotal}}' 2>/dev/null || true)"
case "$docker_memory_bytes" in
  ''|*[!0-9]*)
    if [ "${DREAMS_E2E_REQUIRE_DOCKER:-0}" = "1" ]; then
      fail "could not determine Docker memory"
    fi
    info "skip docker role smoke: could not read Docker memory"
    exit 0
    ;;
esac
if [ "$docker_memory_bytes" -lt "$MIN_DOCKER_GUEST_MEMORY_BYTES" ]; then
  if [ "${DREAMS_E2E_REQUIRE_DOCKER:-0}" = "1" ]; then
    fail "Docker has $((docker_memory_bytes / 1024 / 1024)) MiB; need ~1.5 GiB guest"
  fi
  info "skip docker role smoke: insufficient Docker memory"
  exit 0
fi

main_stack_running="$(docker compose -f docker-compose.yml ps -q 2>/dev/null || true)"
if [ -n "$main_stack_running" ]; then
  if [ "${DREAMS_E2E_REQUIRE_DOCKER:-0}" = "1" ]; then
    fail "the normal Compose stack is running; stop it before isolated dreams E2E"
  fi
  info "skip docker role smoke: main compose stack is running"
  exit 0
fi

compose=(docker compose --project-name "$PROJECT_NAME" --file "$COMPOSE_FILE")
cleanup() {
  if [ "${DREAMS_E2E_KEEP_ARTIFACTS:-0}" = "1" ]; then
    info "preserving isolated containers and volumes for inspection"
  else
    "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

info "starting isolated Neo4j + quality role smoke..."
if ! "${compose[@]}" up --detach --build --wait --wait-timeout "$WAIT_TIMEOUT_SECONDS" dreams-e2e-neo4j; then
  "${compose[@]}" logs --no-color --tail=100 >&2 || true
  fail "isolated Neo4j did not become ready"
fi

info "applying quality roles and running quality_control_smoke..."
"${compose[@]}" run --rm --build --no-deps dreams-e2e
info "docker role smoke passed"
