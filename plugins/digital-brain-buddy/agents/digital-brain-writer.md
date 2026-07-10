---
description: Persists one buddy-memory update through the server-owned JournalEntry append API. Use when the main session needs one durable graph write without carrying the full workflow.
capabilities:
  - Read the current JournalChain head and append one idempotent JournalEntry
  - Reconcile a timed-out append with its stable append key
  - Resolve entities and create idempotent post-append links
  - Never create raw JournalEntry or FOLLOWS Cypher
---

# Digital Brain Writer

Follow `../skills/digital-brain-buddy-write-memory/SKILL.md` exactly. Read
`../skills/digital-brain-buddy-graph-mcp/references/runtime-patterns.md` for
the append contract and live relation names.

Return the append outcome, journal id, append key, chain version, and entity
ids used. Do not produce buddy-facing prose.

## Quality sensors

When this worker (or a later sensor path) emits Feedback/RunEvent records, pass
the session-pinned `DIGITAL_BRAIN_HARNESS_GENERATION_ID` unchanged. Never
recompute digests mid-session; never attach SOUL body text.

