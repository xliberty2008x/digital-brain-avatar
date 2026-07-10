---
name: digital-brain-session
description: Open or show the portable harness session handle for this brain chat (any host).
---

# `/digital-brain-session`

Bind **this** conversation to a frozen harness generation so quality sensors
(Feedback / RunEvent) can improve the harness on **any** brain host (Grok,
Claude, Codex). Memory (journal) works without this; sensors do not invent a pin.

## Arguments

Parse `$ARGUMENTS` loosely:

| Token | Meaning |
| --- | --- |
| `status` (default) | Resolve/show handle without force-recollect when possible |
| `open` | Open or resume session (mint host-prefixed id if none) |
| `force` / `clear` | Recollect pin (`--force-new`) |
| host name `grok` / `claude` / `codex` | Passed as `--host` |

Examples: `/digital-brain-session`, `/digital-brain-session open grok`,
`/digital-brain-session force`.

## Steps

1. Resolve repo root: `${CLAUDE_PROJECT_DIR}` or the workspace containing
   `scripts/pin_harness_generation.py`.

2. Prefer an existing env handle when `status` and both are set:
   - `DIGITAL_BRAIN_SESSION_ID`
   - `DIGITAL_BRAIN_HARNESS_GENERATION_ID`  
   Report them; do not re-pin unless `force` / `open` requested.

3. Otherwise run the **plugin wrapper only** (no bare python3):

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT:-plugins/digital-brain-buddy}/scripts/open-harness-session.sh" \
     --host "${HOST:-unknown}" \
     ${SESSION_ID:+--session-id "$SESSION_ID"} \
     ${FORCE:+--force-new}
   ```

   The wrapper sets `--use-open-api --skip-record --json` and picks uv / `.venv` /
   stdlib python so the host never fails on missing pydantic.

4. Parse JSON → sticky for this chat:
   - `session_id`
   - `harness_generation_id`
   - `pin_path`, `mode`, `host`

5. Tell the user in one short block: mode, session id, generation id prefix
   (`hg-…`), and that sensors may now use this id. Never print SOUL content.

## Rules

- **Never** treat `$STATE/active/harness_generation.json` alone as “my” session
  (it may be a leftover verify pin). Session open writes a real
  `sessions/<id>/` pin.
- Open is **not** apply: no Alias, overlay, or SOUL activation.
- If the pin script fails (no repo / sandbox): say quality sensors stay off;
  memory MCP may still work.
- Spec: `docs/superpowers/specs/2026-07-10-host-agnostic-harness-session-design.md`
