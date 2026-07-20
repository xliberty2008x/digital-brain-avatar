# Changelog

## 0.6.1 — 2026-07-20

Safer Neo4j defaults and compose-up recovery guidance under ~6 GiB Docker (fixes #23).

- Compose Neo4j defaults: 384M initial heap / 768M max / 384M pagecache (was 512M/1G/512M OOM-prone near the 6 GiB floor)
- `compose-up.sh` prints a single recovery recipe on low Docker memory, Neo4j exit 137/OOMKilled, and failed neo4j/ollama start
- Host Ollama publish-port clash: auto-remap default `:11434` → `OLLAMA_PORT=11435` when free; refuse busy explicit ports with empty-host vs compose-volume guidance (MCP still uses `http://ollama:11434` in-network)
- Operator docs (README, `.env.example`, cypher README, `/digital-brain-up`) share the same budget and recipe
- Contract tests cover rendered defaults, overrides, memory refuse, port remap/refuse, and OOM messaging
- Fresh Codex cache suffix (`0.6.1+codex.…`); marketplaces at `0.6.1`

## 0.6.0 — 2026-07-15

Host-agnostic stack recovery and durable Ollama URL isolation (fixes #21).

- `mcp-cypher` maps its application `OLLAMA_BASE_URL` from compose-only
  `MCP_OLLAMA_BASE_URL`, so host `.env` localhost values cannot leak in
- `/digital-brain-up` resolves validated Codex/Grok/Claude workspaces without
  requiring `CLAUDE_PROJECT_DIR`; explicit `DIGITAL_BRAIN_PROJECT_DIR` wins
- Rendered Compose and launcher resolution regression tests cover the recurrence
- Fresh Codex cache suffix (`0.6.0+codex.20260715102426`); marketplaces at `0.6.0`

## 0.5.0 — 2026-07-14

Durable gotcha learning loop after FEEDBACK corrections (closes the “no gotcha” gap).

- Session skill + subagent prompts: mandatory quality-plane gotcha after
  correction FEEDBACK; exact `create_feedback` fields/enums; forbid journal-as-gotcha;
  user-visible `gotcha staged: …` / `parked: sensor down`
- MCP `create_feedback` DX: alias kwargs (`summary`/`detail`/…) rejected with
  agent-actionable contract hint on the tool path
- Dream analyzer clusters `task_outcome=corrected` RunEvents by
  `recurrence_key` / `error_class` / `approach` (taxonomy survives freeze)
- EvidenceItem freeze retains `recurrence_key`, `approach`, `decision_point`
- Repo `AGENTS.md`: e2e ship loop includes review→fix before merge; plugin
  host-update path documented
- Fresh Codex cache suffix (`0.5.0+codex.…`); marketplace entries at `0.5.0`

## 0.4.0 — 2026-07-10

Initiate protocol for empty buddy first run.

- Auto-detect incomplete initiation after BOOTSTRAP; session mode `INITIATE`
- Progressive meeting: language → intro → self + anchor person + focus + light SOUL
- Graph markers + `JournalEntry.kind = initiation_complete` receipt
- Resume from next missing piece; soft progressive hooks when graph is thin
- Pure status helper: `scripts/initiation_status.py`
- SOUL template `## User overlay` section

## 0.3.0 — 2026-07-10

Quality sensors, harness generation pins, and guided report-only maintenance.

- FEEDBACK route on the buddy session (evidence + propose-only review cards)
- Typed MCP sensors (`create_feedback`, `record_run_event`, harness generation)
- Active overlay trial pins (operator-activated; models never activate)
- New skill `digital-brain-buddy-maintenance` + native agent
  `digital-brain-maintainer` (Read/Grep/Glob only; no Bash/Edit/activation)
- Command `/digital-brain-dream` (run/status/review/show/try/apply/defer/reject/
  undo/history/privacy) — report-only default; try/apply operator-gated
- Capability ceiling matches `MAINTAINER_ALLOWED_OPERATIONS`; exact-token
  `APPLY alias:…` is intent only, not authorization
- No scheduled run, no heartbeat, no private proposal dump in shared sessions
- Fresh Codex cache suffix (`0.3.0+codex.…`); marketplace entries at `0.3.0`

## 0.2.0 — 2026-07-10

Server-owned JournalEntry write path (major agent contract change).

- Append protocol: `get_journal_chain_head` → `append_journal_entry` →
  `get_journal_append_receipt` (`found` / `not_found`)
- Generic `write_neo4j_cypher` limited to idempotent post-append links;
  rejects JournalEntry/FOLLOWS/HEAD/JournalChain bypasses
- Skills, writer agent, session prompts, and compose-up / `/readyz` aligned
- Version taxonomy documented in `docs/VERSIONING.md`

## 0.1.0 — 2026-07

Initial local marketplace packaging: SOUL template, session/read/write skills,
SessionStart compose hook, Neo4j MCP URL. (Personal `SOUL.MD` is local-only.)
