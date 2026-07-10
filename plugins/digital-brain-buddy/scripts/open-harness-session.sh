#!/usr/bin/env bash
# Portable harness session open for any brain host (Grok / Claude / Codex).
# Always resolves repo-root python correctly so agents never hit bare-python
# ModuleNotFoundError for pin (stdlib path) or optional MCP record (venv).
#
# Usage (from skill step 0 — no manual flags required by the user):
#   open-harness-session.sh [--host grok|claude|codex] [extra pin_harness args…]
#
# Default: --use-open-api --skip-record --json
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# plugins/digital-brain-buddy/scripts → repo root is ../../..
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
if [ ! -f "$REPO_ROOT/scripts/pin_harness_generation.py" ]; then
  # Fallback: CLAUDE_PROJECT_DIR or cwd
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
    *)
      EXTRA+=("$1")
      shift
      ;;
  esac
done

# Prefer explicit project interpreters when present; bare python3 works for
# --skip-record after stdlib-only maintenance package load.
run_pin() {
  local py="$1"
  shift
  "$py" "$PIN_PY" --host "$HOST" --use-open-api --skip-record --json "$@"
}

cd "$REPO_ROOT"
if command -v uv >/dev/null 2>&1 && [ -f "$REPO_ROOT/pyproject.toml" ]; then
  # uv keeps record path + full deps available if caller drops --skip-record later
  exec uv run --group dev python "$PIN_PY" --host "$HOST" --use-open-api --skip-record --json "${EXTRA[@]+"${EXTRA[@]}"}"
fi
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  exec "$REPO_ROOT/.venv/bin/python" "$PIN_PY" --host "$HOST" --use-open-api --skip-record --json "${EXTRA[@]+"${EXTRA[@]}"}"
fi
# Stdlib pin path — works for open/resume without pydantic after lazy maintenance __init__
exec python3 "$PIN_PY" --host "$HOST" --use-open-api --skip-record --json "${EXTRA[@]+"${EXTRA[@]}"}"
