# Changelog

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
- No scheduled run, no heartbeat, no private proposal queue in shared sessions
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
