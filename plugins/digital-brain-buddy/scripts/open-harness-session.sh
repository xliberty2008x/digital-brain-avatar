#!/usr/bin/env bash
# Portable harness session open for any brain host (Grok / Claude / Codex).
#
# Always opens a local SessionHandle. When MCP /readyz is healthy, also records
# the generation on the quality plane so FEEDBACK / RunEvent / DreamRun can
# resolve the id (closes the "local pin but get_harness_generation not_found" gap).
# If MCP is down, falls back to local-only pin so buddy memory still starts.
#
# Usage (skill step 0 — user never runs this by hand):
#   open-harness-session.sh [--host grok|claude|codex] [extra pin args…]
#
# Stdout: SessionHandle JSON only (pin script --json).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
if [ ! -f "$REPO_ROOT/scripts/pin_harness_generation.py" ]; then
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -f "${CLAUDE_PROJECT_DIR}/scripts/pin_harness_generation.py" ]; then
    REPO_ROOT="$(cd "$CLAUDE_PROJECT_DIR" && pwd)"
  elif [ -f "$(pwd)/scripts/pin_harness_generation.py" ]; then
    REPO_ROOT="$(pwd)"
  else
    echo "open-harness-session: cannot find scripts/pin_harness_generation.py" >&2
    exit 1
  fi
fi

PIN_PY="$REPO_ROOT/scripts/pin_harness_generation.py"
HOST="${DIGITAL_BRAIN_HOST:-unknown}"
EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --host)
      HOST="${2:-unknown}"
      shift 2 || true
      ;;
    --host=*)
      HOST="${1#--host=}"
      shift
      ;;
    --skip-record|--no-record)
      # Explicit local-only (tests / offline).
      FORCE_SKIP_RECORD=1
      shift
      ;;
    *)
      EXTRA+=("$1")
      shift
      ;;
  esac
done

MCP_URL="${DIGITAL_BRAIN_MCP_URL:-http://localhost:8000/api/mcp/}"
# Derive readyz from MCP URL host (…/api/mcp/ → …/readyz)
READYZ_URL="${DIGITAL_BRAIN_READYZ_URL:-}"
if [ -z "$READYZ_URL" ]; then
  READYZ_URL="$(printf '%s' "$MCP_URL" | sed -E 's#/api/mcp/?$##')/readyz"
fi

mcp_ready() {
  [ "${FORCE_SKIP_RECORD:-0}" = "1" ] && return 1
  code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 1 --max-time 3 "$READYZ_URL" 2>/dev/null || echo 000)"
  [ "$code" = "200" ]
}

# Build pin argv: record when MCP healthy so quality ledger matches local pin.
PIN_ARGS=(--host "$HOST" --use-open-api --json)
if mcp_ready; then
  echo "open-harness-session: MCP ready → pin + record_harness_generation" >&2
  # One-line quality write recipe for thin hosts (exact create_feedback fields).
  SCHEMA_BASE="$(printf '%s' "$MCP_URL" | sed -E 's#/api/mcp/?$##')"
  echo "open-harness-session: quality write recipe → create_feedback(id, kind∈{entity_wrong|claim_false|miss|invent|praise}, sensitivity∈{public_ops|personal|intimate}, harness_generation_id=<pin>; optional redacted_summary=imperative gotcha rule). Prefer digital_brain.tools.mcp_client.create_feedback. Session-less schema: GET ${SCHEMA_BASE}/tool-schemas/create_feedback (no MCP session). On correction FEEDBACK surface: gotcha staged: <id> — <rule> (or parked: sensor down). Never journal-as-gotcha." >&2
else
  echo "open-harness-session: MCP not ready → local pin only (--skip-record)" >&2
  echo "open-harness-session: sensors may park — user-visible line: parked: sensor down" >&2
  PIN_ARGS+=(--skip-record)
fi
if [ ${#EXTRA[@]} -gt 0 ]; then
  PIN_ARGS+=("${EXTRA[@]}")
fi

cd "$REPO_ROOT"
run_pin() {
  local py="$1"
  shift
  "$py" "$PIN_PY" "${PIN_ARGS[@]}" "$@"
}

if command -v uv >/dev/null 2>&1 && [ -f "$REPO_ROOT/pyproject.toml" ]; then
  # Recording needs network deps; uv project env has them.
  exec uv run --group dev python "$PIN_PY" "${PIN_ARGS[@]}"
fi
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  exec "$REPO_ROOT/.venv/bin/python" "$PIN_PY" "${PIN_ARGS[@]}"
fi
# Bare python3: local pin always works (stdlib path). If record was requested
# and fails, pin script keeps local pin and exits 0 unless --require-record.
exec python3 "$PIN_PY" "${PIN_ARGS[@]}"
