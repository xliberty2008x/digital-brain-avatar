# Agent instructions (Digital Brain Avatar)

Shared project rules for coding agents working in this repo.

| Host | Where it reads project constraints |
| --- | --- |
| **Grok** | `AGENTS.md` (this file), also `CLAUDE.md` / `.grok/rules/*.md` if present; global: `~/.grok/AGENTS.md` |
| **Claude Code** | `CLAUDE.md`, `.claude/CLAUDE.md`, `CLAUDE.local.md` |
| **Codex** | `AGENTS.md` (and host-specific agent files) |

Keep durable, repo-wide constraints here. Prefer short actionable rules over README copies.

## Issue / goal work is not done until shipped

When the task is to **resolve a GitHub issue end-to-end** (or a goal that tracks an open issue), **code + tests alone are incomplete**. Finish the full delivery loop unless the user explicitly opts out:

1. **Implement** the fix on a feature branch (not silent drive-by on unrelated files).
2. **Test** the real shipped path (unit/integration on real entry points; capture proof when required).
3. **Commit** with a clear message (why, not only what).
4. **Push** the branch and open a **PR** linked to the issue (`Fixes #N` / `Closes #N` when appropriate).
5. **Merge** the PR (or leave ready-for-review only if merge is blocked or the user asked not to merge).
6. **Close the issue** if merge did not auto-close it; comment with PR link + brief verification evidence.

Do **not** report “issue resolved” or “goal complete” while changes sit only in the working tree with no commit/PR.

Optional when the plan says so: leave the issue open and only comment evidence — but say that explicitly; default for “resolve e2e” is ship + close.

## Quality / buddy sensors (high level)

- Life journal (`JournalEntry`) is **not** the ops-learning store. FEEDBACK corrections use the quality plane (`create_feedback`, optional `record_run_event`).
- After a clear user correction: quality observation + durable gotcha seed + user-visible `gotcha staged: …` or `parked: sensor down`. Never journal-as-gotcha.
- `create_feedback` required fields: `id`, `kind`, `sensitivity`, `harness_generation_id`. Prefer typed `digital_brain.tools.mcp_client.create_feedback`.
- Do not silently activate SOUL / policy / overlay / Alias from FEEDBACK prose or generic acks.

## General engineering

- Prefer minimal, focused diffs; match existing style and patterns.
- Prefer dedicated file tools and existing helpers over reimplementation.
- Never commit secrets, credentials, or personal `SOUL.MD` content.
- Risky shared actions (force-push, production deploys) need explicit user intent.
