# digital-brain-buddy version taxonomy

Hosts install this plugin into a **versioned cache** (Claude, Codex, Grok).
Leaving the version at `0.1.0` after a large behavior change means `plugin update`
and “reload” keep serving the old skills. Bump the version when the contract
changes so installs land in a new cache path and reloads actually pick up work.

## Canonical version

| File | Role |
| --- | --- |
| `version.json` | Single source of truth: plain SemVer string, e.g. `"0.3.0"` |
| `.claude-plugin/plugin.json` → `version` | Must equal `version.json` |
| `.codex-plugin/plugin.json` → `version` | Base SemVer **or** `BASE+codex.YYYYMMDDHHMMSS` to force a new Codex cache dir |
| repo `.claude-plugin/marketplace.json` → `plugins[].version` | Must equal `version.json` for the digital-brain-buddy entry |
| repo `.agents/plugins/marketplace.json` → `plugins[].version` | Host/cache marketplace; must equal `version.json` for digital-brain-buddy |

Do not invent a third number. If they disagree, fix them before merge.

## SemVer for this plugin (0.x)

While the major is `0`, treat the **middle** number as the product surface:

| Bump | When | Examples |
| --- | --- | --- |
| **PATCH** `0.3.x` | Docs/skills wording only; no new tools; no change to write/read/maintenance contract | Typo, clearer receipt outcomes, compose timeout tweak docs |
| **MINOR** (middle digit: `0.2.0` → `0.3.0`) | New capability or **breaking agent contract**, still compatible with same MCP stack family | New MCP tools agents must call; append protocol; maintenance skill; new hooks; renamed skills |
| **MAJOR** `1.0.0+` | Stable public surface, or intentional hard break for all hosts | Shipping as a published marketplace plugin; removing a skill agents rely on |

Rule of thumb: **if buddy writers must change how they call MCP or how they
chain JournalEntry, bump at least MINOR** (and never leave the version
unchanged).

## What counts as “must bump” (do not skip)

- Journal / chain write protocol change (e.g. raw Cypher → `append_journal_entry`)
- MCP tool set change (add/remove/rename tools agents are taught to use)
- Skill or agent instructions that reverse a previous hard rule
- SessionStart hook / compose bring-up behavior that operators rely on
- SOUL **template** (`assets/SOUL.template.md`) or session contract changes that
  alter default persona / memory policy (personal `SOUL.MD` is gitignored and
  not part of the published package identity)

## What does not require a bump

- Internal MCP server fixes that stay behind the same tool names + outcomes
- Test-only or ops-script changes under `scripts/` / repo root
- Pure incident postmortems under `docs/`

Still **rebuild mcp-cypher** when server code changes; version bumps are about
the **plugin package hosts cache**, not Docker layers.

## Release checklist

1. Edit `version.json` first.
2. Copy the same base into `.claude-plugin/plugin.json`.
3. Set `.codex-plugin/plugin.json` to the base or `BASE+codex.<utc stamp>`.
4. Set the same base on the digital-brain-buddy entry in repo
   `.claude-plugin/marketplace.json` **and** `.agents/plugins/marketplace.json`.
5. Add a short entry to `CHANGELOG.md`.
6. Merge to `master`.
7. Refresh hosts so they install the new version:
   - Claude: `claude plugin update digital-brain-buddy@avatar-digital-brain-local` + restart
   - Codex: marketplace refresh + re-add (new cache dir from new version string)
   - Grok: `grok plugin update digital-brain-buddy`
8. Rebuild local MCP: `bash plugins/digital-brain-buddy/scripts/compose-up.sh`

## Why these minors

`0.1.0` was the first local buddy packaging (skills, hooks, MCP URL).

`0.2.0` is the **server-owned JournalEntry append protocol**: CAS chain head,
receipts, hardened generic Cypher, append-first skills/agents, readiness and
compose resource gates. Hosts still on a `0.1.0` cache teach the obsolete
write path even when the MCP server rejects it.

`0.3.0` is the **quality + maintenance session contract**: FEEDBACK sensors,
harness generation pins, guided report-only DreamRun skill/command, and a
capability-fenced maintainer agent. MCP tools and the agent contract change;
hosts must pick up a new cache path (including a fresh Codex `+codex.` suffix).

`0.4.0` is the **initiate-on-first-run** session contract (language → intro →
seed people/focus → light SOUL → initiation receipt).

`0.5.0` is the **durable gotcha loop** after correction FEEDBACK: mandatory
quality-plane seed + user-visible confirmation, agent-actionable
`create_feedback` DX, and Dream clustering of corrected RunEvent taxonomy.
Skill hard rules change; hosts on `0.4.0` caches keep teaching the incomplete
path until they update.

`0.6.0` is host-agnostic stack recovery plus a durable Ollama trust-boundary
fix. The launcher works from Codex/Grok workspaces as well as Claude hooks, and
the MCP container no longer inherits a host-only `OLLAMA_BASE_URL`.
