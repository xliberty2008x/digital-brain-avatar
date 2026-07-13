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
5. **Review → fix loop (mandatory before merge)** — do not merge a green PR you never reviewed:
   - Run a real code review of the PR/diff (reviewer subagent / `/review --pr N` / equivalent).
   - Fix **bugs** and high-value **suggestions**; re-test changed paths.
   - Re-review if the fix set is non-trivial (new behavior, security, or > small nits).
   - Only then merge. Skip this loop only if the user explicitly says so.
6. **Merge** the PR after the review→fix loop (or leave ready-for-review if merge is blocked / user asked not to merge).
7. **Close the issue** if merge did not auto-close it; comment with PR link + brief verification evidence.

Do **not** report “issue resolved” or “goal complete” while changes sit only in the working tree with no commit/PR.
Do **not** treat “tests passed” as a substitute for the review→fix loop before merge.

Optional when the plan says so: leave the issue open and only comment evidence — but say that explicitly; default for “resolve e2e” is ship + close.

## Issues, PRs, commits (agent conventions)

Audience: **agents** (Grok / Claude / Codex). Lightweight prefixes only — no
required GitHub templates or labels. Title prefix is the source of truth.

### Issue titles

| Prefix | Use for |
| --- | --- |
| `[bug]` | Wrong behavior, regression, broken path |
| `[feat]` | New product capability |
| `[dx]` | Agent/host ergonomics (schemas, errors, discoverability) |
| `[docs]` | Docs-only product/operator docs (rare as issues) |
| `[release]` | Version / host-cache packaging work |

Body (short): **what / expected / actual or ask / evidence** (logs, session note).
Link related issues when known.

### When to open an issue vs PR-only

| Open a GitHub issue | PR-only is OK |
| --- | --- |
| `[bug]`, `[feat]`, `[dx]`, multi-step product gaps | Tiny docs typos, pure `chore`, version bump already tracked by an issue, drive-by nits the user asked for in-chat |

Default: **bugs / feats / dx → issue first**, then implement on a branch and open
a PR that closes it.

### Commits and branches

- **Commits:** conventional — `fix:`, `feat:`, `docs:`, `chore:`, `release:`
  (optional scope: `fix(buddy):`, `release(buddy):`). Message = **why**, not only what.
- **Branches:** `fix/…`, `feat/…`, `dx/…`, `docs/…`, `release/…` (match the type).

### Pull requests

- **Title:** same conventional form as the main commit (not a free-form essay).
- **Body (required minimum):**

  ```markdown
  ## Summary
  - …

  ## Test plan
  - [x] …   # or N/A with a one-line reason

  Closes #N   # when an issue exists (Fixes #N also fine)
  ```

- **Merge:** follow the review→fix loop above. Do not merge unreviewed work
  unless the user explicitly opts out.
- **Labels:** optional. Do not block if the host cannot set labels.

### Plugin contract changes

If skills/agent hard rules or host-cached package change, also follow
**Plugin release / host updates** below (version bump + marketplaces + host
refresh). Issue type is often `[feat]`, `[dx]`, or `[release]` depending on the work.

## Quality / buddy sensors (high level)

- Life journal (`JournalEntry`) is **not** the ops-learning store. FEEDBACK corrections use the quality plane (`create_feedback`, optional `record_run_event`).
- After a clear user correction: quality observation + durable gotcha seed + user-visible `gotcha staged: …` or `parked: sensor down`. Never journal-as-gotcha.
- `create_feedback` required fields: `id`, `kind`, `sensitivity`, `harness_generation_id`. Prefer typed `digital_brain.tools.mcp_client.create_feedback`.
- Do not silently activate SOUL / policy / overlay / Alias from FEEDBACK prose or generic acks.

## Plugin release / host updates (`digital-brain-buddy`)

Merge to `master` does **not** push skills to Claude / Codex / Grok by itself.
Hosts install this plugin into a **versioned cache**. Same SemVer after a skill
contract change means reloads often keep serving **old** skills.

**Source of truth:** `plugins/digital-brain-buddy/docs/VERSIONING.md` (full
taxonomy + checklist). Canonical number: `plugins/digital-brain-buddy/version.json`.

### When to bump (must not skip)

Bump when the **agent contract** or host-cached package changes, including:

- Skill / agent hard rules reverse or gain new mandatory steps (e.g. FEEDBACK gotcha)
- MCP tools agents are taught to call add/remove/rename
- SessionStart hooks / compose bring-up operators rely on
- SOUL **template** default persona/memory policy (not personal gitignored `SOUL.MD`)

| Bump | Typical case |
| --- | --- |
| **PATCH** `0.x.Y` | Skill/docs wording only |
| **MINOR** (0.x middle) | New capability or breaking agent contract |
| **MAJOR** `1.0+` | Stable public surface / intentional hard break |

Internal MCP server fixes with the **same** tool names/outcomes need a Docker
rebuild, not necessarily a plugin bump. Plugin version ≠ image tag.

### Release checklist (e2e includes this)

1. Edit `plugins/digital-brain-buddy/version.json` first.
2. Sync `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`
   (Codex: `BASE+codex.YYYYMMDDHHMMSS` to force a new cache dir).
3. Sync repo marketplaces: `.claude-plugin/marketplace.json` **and**
   `.agents/plugins/marketplace.json` (digital-brain-buddy entry).
4. `plugins/digital-brain-buddy/CHANGELOG.md` (+ root `CHANGELOG.md` pointer).
5. Keep `tests/test_plugin_contract.py` version asserts in sync.
6. Ship via the issue loop above (PR → **review→fix** → merge).
7. Refresh **each host** so subscribers actually load the new cache:
   - Claude: `claude plugin update digital-brain-buddy@avatar-digital-brain-local` + restart
   - Codex: marketplace refresh / re-add (new version string → new cache path)
   - Grok: `grok plugin update digital-brain-buddy`
8. If MCP server code changed: rebuild stack
   `CLAUDE_PROJECT_DIR=$PWD bash plugins/digital-brain-buddy/scripts/compose-up.sh`

Do **not** call plugin contract work “done for subscribers” until the version is
bumped **and** host update steps are documented or run.

## General engineering

- Prefer minimal, focused diffs; match existing style and patterns.
- Prefer dedicated file tools and existing helpers over reimplementation.
- Never commit secrets, credentials, or personal `SOUL.MD` content.
- Risky shared actions (force-push, production deploys) need explicit user intent.
