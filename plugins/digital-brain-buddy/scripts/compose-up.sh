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
DEFAULT_OLLAMA_HOST_PORT="${DEFAULT_OLLAMA_HOST_PORT:-11434}"
FALLBACK_OLLAMA_HOST_PORT="${FALLBACK_OLLAMA_HOST_PORT:-11435}"

warn_and_exit() {
  echo "$PLUGIN_NAME: $1" >&2
  exit 0
}

# Single operator recovery recipe (keep in sync with README / .env.example).
print_recovery_recipe() {
  cat >&2 <<'EOF'
digital-brain-buddy: recovery recipe (Neo4j OOM / low Docker memory / port clash):
  1) Prefer Docker Desktop ≥ 8 GiB (compose-up requires ≥ 6 GiB).
  2) Tighter Neo4j budget (defaults; re-export if your shell overrode them):
       export NEO4J_HEAP_INITIAL_SIZE=384M NEO4J_HEAP_MAX_SIZE=768M NEO4J_PAGECACHE_SIZE=384M
  3) If host Ollama holds :11434 (often empty models) while compose volume has bge-m3:
       export OLLAMA_PORT=11435
     MCP still uses http://ollama:11434 on the compose network.
  4) Re-up from the avatar_digital_brain checkout:
       CLAUDE_PROJECT_DIR=$PWD bash plugins/digital-brain-buddy/scripts/compose-up.sh
       # or: docker compose up -d neo4j ollama
       #     docker compose up -d --build --no-deps mcp-cypher
EOF
}

# True if something accepts TCP on 127.0.0.1:port (host publish clash).
# Test hook: DIGITAL_BRAIN_TEST_PORT_PROBE=1 makes the check consult only
# DIGITAL_BRAIN_TEST_PORTS_IN_USE (comma-separated ports), never the real host.
host_port_in_use() {
  port="$1"
  case "$port" in
    ''|*[!0-9]*) return 1 ;;
  esac
  if [ "${DIGITAL_BRAIN_TEST_PORT_PROBE:-}" = "1" ]; then
    case ",${DIGITAL_BRAIN_TEST_PORTS_IN_USE:-}," in
      *,"$port",*) return 0 ;;
      *) return 1 ;;
    esac
  fi
  if command -v lsof >/dev/null 2>&1; then
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      return 0
    fi
  fi
  # Bash /dev/tcp probe (works when lsof is missing).
  if (echo >/dev/tcp/127.0.0.1/"$port") >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

# Host port currently published by this project's compose ollama (if any).
# `docker compose port ollama 11434` → e.g. "127.0.0.1:11435". Empty if down.
compose_ollama_host_port() {
  if [ "${DIGITAL_BRAIN_TEST_PORT_PROBE:-}" = "1" ]; then
    # Test hook: optional pre-seeded "already published" host port.
    if [ -n "${DIGITAL_BRAIN_TEST_COMPOSE_OLLAMA_PORT:-}" ]; then
      printf '%s\n' "$DIGITAL_BRAIN_TEST_COMPOSE_OLLAMA_PORT"
      return 0
    fi
    return 1
  fi
  out="$(docker compose port ollama 11434 2>/dev/null || true)"
  case "$out" in
    *:*)
      # Take the last colon segment (host port), strip CR.
      host_port="${out##*:}"
      host_port="$(printf '%s' "$host_port" | tr -d '\r\n')"
      case "$host_port" in
        ''|*[!0-9]*) return 1 ;;
        *)
          printf '%s\n' "$host_port"
          return 0
          ;;
      esac
      ;;
  esac
  return 1
}

# Ensure OLLAMA_PORT is free for docker publish; remap or refuse with guidance.
# Re-entry safe: if this project's compose ollama already publishes a host port,
# reuse it instead of treating our own bind as a foreign clash (SessionStart).
resolve_ollama_publish_port() {
  existing_host_port="$(compose_ollama_host_port || true)"

  # Operator set OLLAMA_PORT explicitly.
  if [ -n "${OLLAMA_PORT:-}" ]; then
    requested="$OLLAMA_PORT"
    if [ -n "$existing_host_port" ] && [ "$existing_host_port" = "$requested" ]; then
      export OLLAMA_PORT="$requested"
      return 0
    fi
    if ! host_port_in_use "$requested"; then
      export OLLAMA_PORT="$requested"
      return 0
    fi
    echo "$PLUGIN_NAME: host publish port ${OLLAMA_PORT} is already in use (Ollama publish clash)" >&2
    if [ -n "$existing_host_port" ] && [ "$existing_host_port" != "$requested" ]; then
      echo "$PLUGIN_NAME: compose ollama already publishes 127.0.0.1:${existing_host_port}; set OLLAMA_PORT=${existing_host_port} to re-enter, or stop that service first" >&2
    else
      echo "$PLUGIN_NAME: host Ollama on that port may have an empty model list while the compose volume holds bge-m3" >&2
      echo "$PLUGIN_NAME: free the port or set OLLAMA_PORT to a free host port; MCP still uses http://ollama:11434 in-network" >&2
    fi
    print_recovery_recipe
    return 1
  fi

  # Unset OLLAMA_PORT: prefer whatever this stack already publishes (stable re-up).
  if [ -n "$existing_host_port" ]; then
    export OLLAMA_PORT="$existing_host_port"
    if [ "$existing_host_port" != "$DEFAULT_OLLAMA_HOST_PORT" ]; then
      echo "$PLUGIN_NAME: reusing existing compose Ollama publish on 127.0.0.1:${OLLAMA_PORT} (MCP still uses http://ollama:11434 in-network)" >&2
    fi
    return 0
  fi

  requested="$DEFAULT_OLLAMA_HOST_PORT"
  if ! host_port_in_use "$requested"; then
    export OLLAMA_PORT="$requested"
    return 0
  fi

  # Default 11434 busy (foreign process) → auto-remap to fallback when free.
  if ! host_port_in_use "$FALLBACK_OLLAMA_HOST_PORT"; then
    export OLLAMA_PORT="$FALLBACK_OLLAMA_HOST_PORT"
    echo "$PLUGIN_NAME: host :${DEFAULT_OLLAMA_HOST_PORT} is in use; publishing compose Ollama on 127.0.0.1:${OLLAMA_PORT}" >&2
    echo "$PLUGIN_NAME: host Ollama may be empty while the compose volume holds bge-m3; MCP still uses http://ollama:11434 in-network" >&2
    return 0
  fi

  echo "$PLUGIN_NAME: host ports ${DEFAULT_OLLAMA_HOST_PORT} and ${FALLBACK_OLLAMA_HOST_PORT} are both in use; set OLLAMA_PORT to a free port" >&2
  echo "$PLUGIN_NAME: host Ollama may be empty while the compose volume holds bge-m3; MCP still uses http://ollama:11434 in-network" >&2
  print_recovery_recipe
  return 1
}

is_avatar_project_root() {
  candidate="$1"
  [ -d "$candidate" ] || return 1

  compose_file=""
  if [ -f "$candidate/docker-compose.yml" ]; then
    compose_file="$candidate/docker-compose.yml"
  elif [ -f "$candidate/compose.yml" ]; then
    compose_file="$candidate/compose.yml"
  else
    return 1
  fi

  # A generic Compose checkout (or a versioned plugin cache) is not enough.
  # Require stable avatar repo markers plus the three services this hook owns.
  [ -f "$candidate/pyproject.toml" ] || return 1
  grep -Fq 'name = "avatar-digital-brain"' "$candidate/pyproject.toml" || return 1
  [ -d "$candidate/mcp_servers/cypher" ] || return 1
  [ -f "$candidate/scripts/pin_harness_generation.py" ] || return 1
  grep -Eq '^[[:space:]]{2}neo4j:' "$compose_file" || return 1
  grep -Eq '^[[:space:]]{2}ollama:' "$compose_file" || return 1
  grep -Eq '^[[:space:]]{2}mcp-cypher:' "$compose_file" || return 1
}

canonical_dir() {
  (cd -- "$1" 2>/dev/null && pwd -P)
}

find_project_upwards() {
  search_dir="$(canonical_dir "$1")" || return 1
  while true; do
    if is_avatar_project_root "$search_dir"; then
      printf '%s\n' "$search_dir"
      return 0
    fi
    parent_dir="$(dirname -- "$search_dir")"
    [ "$parent_dir" != "$search_dir" ] || return 1
    search_dir="$parent_dir"
  done
}

resolve_project_dir() {
  if [ -n "${DIGITAL_BRAIN_PROJECT_DIR:-}" ]; then
    resolved="$(canonical_dir "$DIGITAL_BRAIN_PROJECT_DIR")" || return 1
    is_avatar_project_root "$resolved" || return 1
    printf '%s\n' "$resolved"
    return 0
  fi

  if [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then
    resolved="$(canonical_dir "$CLAUDE_PROJECT_DIR")" || return 1
    is_avatar_project_root "$resolved" || return 1
    printf '%s\n' "$resolved"
    return 0
  fi

  resolved="$(find_project_upwards "$PWD")" && {
    printf '%s\n' "$resolved"
    return 0
  }

  # This succeeds for the script in a source checkout. A cached plugin path
  # has no avatar repo markers above it and therefore cannot become the root.
  script_dir="$(canonical_dir "$(dirname -- "${BASH_SOURCE[0]}")")" || return 1
  find_project_upwards "$script_dir"
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
      oom_killed="$(docker inspect -f '{{.State.OOMKilled}}' "$container_id" 2>/dev/null || true)"
      status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
      if [ "$status" = "healthy" ]; then
        echo "$PLUGIN_NAME: ${wait_label} is healthy"
        return 0
      fi
      if [ "$status" = "unhealthy" ]; then
        echo "$PLUGIN_NAME: ${wait_label} healthcheck is unhealthy; inspect 'docker compose logs $service'" >&2
        if [ "$service" = "neo4j" ]; then
          print_recovery_recipe
        fi
        return 1
      fi
      # Fail fast if the container crashed (for example, a store/image mismatch
      # or an OOM-killed service) instead of later claiming the stack is ready.
      if [ "$running" = "false" ] && [ "${exit_code:-0}" != "0" ]; then
        exited_streak=$((exited_streak + 1))
        if [ "$exited_streak" -ge 3 ]; then
          echo "$PLUGIN_NAME: ${wait_label} container exited (status=${status}, exit=${exit_code}, OOMKilled=${oom_killed:-unknown}); inspect 'docker compose logs $service'" >&2
          if [ "$service" = "neo4j" ] && { [ "$exit_code" = "137" ] || [ "$oom_killed" = "true" ]; }; then
            echo "$PLUGIN_NAME: neo4j looks OOM-killed (exit 137 / OOMKilled=true)" >&2
            print_recovery_recipe
          elif [ "$service" = "neo4j" ]; then
            print_recovery_recipe
          fi
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
      if [ "$service" = "neo4j" ]; then
        print_recovery_recipe
      fi
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

PROJECT_DIR="$(resolve_project_dir)" || warn_and_exit "could not resolve a validated avatar_digital_brain checkout; set DIGITAL_BRAIN_PROJECT_DIR (or legacy CLAUDE_PROJECT_DIR), or run from the repo"
export DIGITAL_BRAIN_PROJECT_DIR="$PROJECT_DIR"
cd -- "$PROJECT_DIR" || warn_and_exit "cannot cd to resolved project dir ($PROJECT_DIR), skipping compose bring-up"

if ! command -v docker >/dev/null 2>&1; then
  warn_and_exit "docker not found, skipping local Neo4j/MCP bring-up"
fi

if ! docker compose version >/dev/null 2>&1; then
  warn_and_exit "docker compose not available, skipping local Neo4j/MCP bring-up"
fi

docker_memory_bytes="$(docker info --format '{{.MemTotal}}' 2>/dev/null || true)"
case "$docker_memory_bytes" in
  ''|*[!0-9]*)
    echo "$PLUGIN_NAME: could not determine Docker memory; allocate at least 6 GiB in Docker Desktop before bringing up the embedding stack" >&2
    print_recovery_recipe
    exit 0
    ;;
esac
if [ "$docker_memory_bytes" -lt "$MIN_DOCKER_MEMORY_BYTES" ]; then
  docker_memory_mib=$((docker_memory_bytes / 1024 / 1024))
  echo "$PLUGIN_NAME: Docker has ${docker_memory_mib} MiB available; allocate at least 6 GiB in Docker Desktop before bringing up Neo4j + Ollama" >&2
  print_recovery_recipe
  exit 0
fi

# Resolve host state dir before any compose up that mounts it into mcp-cypher.
# Must match digital_brain.maintenance.generation.resolve_state_dir / XDG rules
# and the pin write location later in this script so the volume bind tracks pins.
STATE_DIR="${DIGITAL_BRAIN_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/digital-brain}"
export DIGITAL_BRAIN_STATE_DIR="$STATE_DIR"
mkdir -p "$STATE_DIR"

# Avoid bind: address already in use on the Ollama host publish port.
if ! resolve_ollama_publish_port; then
  warn_and_exit "refusing to start ollama while the host publish port is unavailable"
fi

if ! docker compose up -d neo4j ollama; then
  echo "$PLUGIN_NAME: failed to start neo4j/ollama, skipping mcp-cypher bring-up" >&2
  print_recovery_recipe
  exit 0
fi

if ! wait_for_service_health neo4j "$NEO4J_HEALTH_MAX_ATTEMPTS" "neo4j"; then
  warn_and_exit "neo4j is not healthy; no MCP service was started"
fi

if ! wait_for_service_health ollama "$OLLAMA_HEALTH_MAX_ATTEMPTS" "ollama"; then
  warn_and_exit "ollama is not healthy; no MCP service was started"
fi

# Optional: apply runtime/quality Neo4j roles when credentials are configured.
# Explicit, non-silent — skipped unless DIGITAL_BRAIN_APPLY_QUALITY_ROLES=1 and
# NEO4J_RUNTIME_PASSWORD + NEO4J_QUALITY_PASSWORD are set. Documented path for
# dual-layer security (see .env.example). Never mounts operator activation
# credentials into mcp-cypher. Does not run Operational backfill migration
# (scripts/migrate_operational_labels.py is operator-reviewed only).
#
# After roles apply, mcp-cypher below receives NEO4J_RUNTIME_* / NEO4J_QUALITY_*
# from the host env / .env. Verify with:
#   DIGITAL_BRAIN_REQUIRE_ROLE_SMOKE=1 uv run --group dev python tests/e2e/quality_control_smoke.py
if [ "${DIGITAL_BRAIN_APPLY_QUALITY_ROLES:-0}" = "1" ]; then
  if [ -n "${NEO4J_RUNTIME_PASSWORD:-}" ] && [ -n "${NEO4J_QUALITY_PASSWORD:-}" ]; then
    echo "$PLUGIN_NAME: applying Neo4j runtime/quality roles (reviewed bootstrap)..."
    if command -v uv >/dev/null 2>&1; then
      if ! uv run --group dev python scripts/init_quality_roles.py --apply; then
        echo "$PLUGIN_NAME: quality role bootstrap failed; continuing with existing Neo4j auth" >&2
      else
        echo "$PLUGIN_NAME: quality roles applied (runtime DENY covers all PROTECTED_QUALITY_LABELS)"
      fi
    else
      echo "$PLUGIN_NAME: uv not found; skip quality role bootstrap" >&2
    fi
  else
    echo "$PLUGIN_NAME: DIGITAL_BRAIN_APPLY_QUALITY_ROLES=1 but runtime/quality passwords unset; skip" >&2
  fi
else
  echo "$PLUGIN_NAME: skip role bootstrap (set DIGITAL_BRAIN_APPLY_QUALITY_ROLES=1 after configuring NEO4J_RUNTIME_PASSWORD + NEO4J_QUALITY_PASSWORD)"
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

# ---------------------------------------------------------------------------
# Harness generation pin (session-scoped)
# ---------------------------------------------------------------------------
# Claude SessionStart hooks send JSON on stdin with session_id + source.
#   startup|clear  → force-new pin (recollect)
#   resume|compact → reload existing pin for that session
# Fallback when no host session id: mint a timestamped local id (never reuse a
# sticky global "current" pin across SessionStarts). Prefer DIGITAL_BRAIN_SESSION_ID
# when the operator sets it explicitly.
#
# Exports: state-dir pin JSON + harness_generation.env, process env, and when
# CLAUDE_ENV_FILE is set (SessionStart), host session env via the pin script:
#   export DIGITAL_BRAIN_HARNESS_GENERATION_ID=...
#   export DIGITAL_BRAIN_HARNESS_PIN_PATH=...
# ---------------------------------------------------------------------------

# Capture SessionStart stdin once (empty when run interactively /digital-brain-up).
HOOK_STDIN=""
if [ ! -t 0 ]; then
  HOOK_STDIN="$(cat || true)"
fi

HOOK_SESSION_ID=""
HOOK_SOURCE=""
if [ -n "$HOOK_STDIN" ]; then
  # Parse with Python so we do not depend on jq.
  HOOK_META="$(
    printf '%s' "$HOOK_STDIN" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    data = {}
if not isinstance(data, dict):
    data = {}
sid = str(data.get("session_id") or "").strip()
src = str(data.get("source") or "").strip().lower()
# TAB-separated; values should be UUID-like / simple tokens
print(sid.replace("\t", " ").replace("\n", " "))
print(src.replace("\t", " ").replace("\n", " "))
' 2>/dev/null || true
  )"
  HOOK_SESSION_ID="$(printf '%s\n' "$HOOK_META" | sed -n '1p')"
  HOOK_SOURCE="$(printf '%s\n' "$HOOK_META" | sed -n '2p')"
fi

PIN_CMD=(python3)
if command -v uv >/dev/null 2>&1; then
  PIN_CMD=(uv run --group dev python)
fi

# Resolve session id + force-new via shared Python helper (single source of truth).
BINDING_OUT="$(
  DIGITAL_BRAIN_SESSION_ID="${DIGITAL_BRAIN_SESSION_ID:-}" \
  HOOK_SESSION_ID="$HOOK_SESSION_ID" \
  HOOK_SOURCE="$HOOK_SOURCE" \
  "${PIN_CMD[@]}" -c '
import os
from digital_brain.maintenance.generation import resolve_session_binding
sid, force = resolve_session_binding(
    env_session_id=os.environ.get("DIGITAL_BRAIN_SESSION_ID") or None,
    hook_session_id=os.environ.get("HOOK_SESSION_ID") or None,
    hook_source=os.environ.get("HOOK_SOURCE") or None,
)
print(sid)
print("1" if force else "0")
' 2>/dev/null || true
)"
if [ -z "$BINDING_OUT" ]; then
  # Interpreter/import failure fallback: still avoid sticky "current".
  RESOLVED_SESSION_ID="${DIGITAL_BRAIN_SESSION_ID:-${HOOK_SESSION_ID:-local-$(date -u +%Y%m%dT%H%M%SZ)-$$}}"
  case "$HOOK_SOURCE" in
    startup|clear) PIN_FORCE_NEW=1 ;;
    *) PIN_FORCE_NEW=0 ;;
  esac
  if [ -z "${DIGITAL_BRAIN_SESSION_ID:-}" ] && [ -z "$HOOK_SESSION_ID" ]; then
    PIN_FORCE_NEW=1
  fi
else
  RESOLVED_SESSION_ID="$(printf '%s\n' "$BINDING_OUT" | sed -n '1p')"
  PIN_FORCE_NEW="$(printf '%s\n' "$BINDING_OUT" | sed -n '2p')"
fi
export DIGITAL_BRAIN_SESSION_ID="$RESOLVED_SESSION_ID"

echo "$PLUGIN_NAME: pinning harness generation for session=${DIGITAL_BRAIN_SESSION_ID} source=${HOOK_SOURCE:-manual} force_new=${PIN_FORCE_NEW}..."
PIN_SCRIPT="$PROJECT_DIR/scripts/pin_harness_generation.py"
if [ ! -f "$PIN_SCRIPT" ]; then
  echo "$PLUGIN_NAME: pin script missing at $PIN_SCRIPT; continuing without pin" >&2
  exit 0
fi
# Prefer plugin SOUL when present; pin script also falls back to repo SOUL.
SOUL_ARGS=()
if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "${CLAUDE_PLUGIN_ROOT}/SOUL.MD" ]; then
  SOUL_ARGS=(--soul-path "${CLAUDE_PLUGIN_ROOT}/SOUL.MD")
elif [ -f "$PROJECT_DIR/SOUL.MD" ]; then
  SOUL_ARGS=(--soul-path "$PROJECT_DIR/SOUL.MD")
fi
PLUGIN_ARGS=()
if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
  PLUGIN_ARGS=(--plugin-root "$CLAUDE_PLUGIN_ROOT")
fi
PIN_EXTRA=()
if [ "$PIN_FORCE_NEW" = "1" ]; then
  PIN_EXTRA+=(--force-new)
fi
if [ -n "$HOOK_SOURCE" ]; then
  PIN_EXTRA+=(--hook-source "$HOOK_SOURCE")
fi
if ! "${PIN_CMD[@]}" "$PIN_SCRIPT" \
    --repo-root "$PROJECT_DIR" \
    "${PLUGIN_ARGS[@]}" \
    "${SOUL_ARGS[@]}" \
    --session-id "$DIGITAL_BRAIN_SESSION_ID" \
    "${PIN_EXTRA[@]}"; then
  echo "$PLUGIN_NAME: harness generation pin failed; session continues without a recorded pin" >&2
  # Non-fatal for compose bring-up so journal writes still work if MCP quality
  # path is unavailable; RunEvent emission must refuse without a pin (Task 4).
  exit 0
fi

# Load pin into this shell (pin script cannot export to the parent process).
# STATE_DIR already resolved + exported before docker compose up (see above).
STATE_DIR="${DIGITAL_BRAIN_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/digital-brain}"
ENV_FILE="${STATE_DIR}/sessions/${DIGITAL_BRAIN_SESSION_ID}/harness_generation.env"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

# Ensure host session env sees the pin even if pin script missed CLAUDE_ENV_FILE.
if [ -n "${CLAUDE_ENV_FILE:-}" ] && [ -n "${DIGITAL_BRAIN_HARNESS_GENERATION_ID:-}" ]; then
  {
    echo "export DIGITAL_BRAIN_HARNESS_GENERATION_ID='${DIGITAL_BRAIN_HARNESS_GENERATION_ID}'"
    echo "export DIGITAL_BRAIN_HARNESS_PIN_PATH='${DIGITAL_BRAIN_HARNESS_PIN_PATH:-${DIGITAL_BRAIN_HARNESS_GENERATION_PIN:-}}'"
    echo "export DIGITAL_BRAIN_SESSION_ID='${DIGITAL_BRAIN_SESSION_ID}'"
  } >> "$CLAUDE_ENV_FILE"
fi

# Ensure well-known active pin exists for mcp-cypher (mounted DIGITAL_BRAIN_STATE_DIR).
# Pin script / pin_session_generation already write it; reinforce after env load.
if [ -n "${DIGITAL_BRAIN_HARNESS_GENERATION_ID:-}" ]; then
  ACTIVE_DIR="${STATE_DIR}/active"
  mkdir -p "$ACTIVE_DIR"
  printf '%s\n' "${DIGITAL_BRAIN_HARNESS_GENERATION_ID}" > "${ACTIVE_DIR}/harness_generation.id"
  printf '{\n  "id": "%s",\n  "session_id": "%s"\n}\n' \
    "${DIGITAL_BRAIN_HARNESS_GENERATION_ID}" \
    "${DIGITAL_BRAIN_SESSION_ID}" > "${ACTIVE_DIR}/harness_generation.json"
  echo "$PLUGIN_NAME: harness generation pinned id=${DIGITAL_BRAIN_HARNESS_GENERATION_ID} session=${DIGITAL_BRAIN_SESSION_ID} active_pin=${ACTIVE_DIR}/harness_generation.id"
else
  echo "$PLUGIN_NAME: harness generation pin completed but id env not visible in this shell (checked $ENV_FILE)"
fi

# Pin validated active overlay manifest once per session (Task 11).
# Fail-closed load if digests mismatch; mid-session live edits do not change pin.
echo "$PLUGIN_NAME: pinning session active overlays for session=${DIGITAL_BRAIN_SESSION_ID}..."
if ! "${PIN_CMD[@]}" -c "
from digital_brain.maintenance.active_overlays import pin_session_active_overlays
import os
m = pin_session_active_overlays(
    state_dir=os.environ.get('DIGITAL_BRAIN_STATE_DIR'),
    session_id=os.environ['DIGITAL_BRAIN_SESSION_ID'],
)
print(f'overlay_pin entries={len(m.loadable_entries())} fail_closed={m.fail_closed}')
"; then
  echo "$PLUGIN_NAME: session overlay pin failed; refusing an unpinned session" >&2
  exit 1
fi

exit 0
