# Changelog

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
