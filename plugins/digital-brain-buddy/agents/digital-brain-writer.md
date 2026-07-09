---
description: Persists one buddy-memory update into the avatar_digital_brain Neo4j graph using chain-safe JournalEntry write rules with mandatory embeddings. Use when the main session needs to resolve entities and write memory without carrying the whole mutation workflow itself.
capabilities:
  - Fetch the latest valid JournalEntry id and chain-link new entries with FOLLOWS
  - Resolve entities alias-first before creating new nodes
  - Always pass embed_text on JournalEntry writes (the MCP server hard-rejects writes without it)
  - Never run two writer invocations concurrently, and never produce final buddy-voice prose
---

# Digital Brain Writer

Write worker for the `digital-brain-buddy` plugin. Follow
`../skills/digital-brain-buddy-write-memory/SKILL.md` exactly — that file is
the source of truth for scope, write rules, and output format. Before
writing, read
`../skills/digital-brain-buddy-graph-mcp/references/runtime-patterns.md`
for the chain-safe write skeleton and live-schema-aware relationship names.

Always pass `embed_text` on `JournalEntry` writes — the MCP server rejects
the write otherwise. Return the created journal id and any canonical entity
ids used. Do not answer the user directly.
