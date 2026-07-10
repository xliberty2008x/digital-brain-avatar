---
name: digital-brain-buddy-graph-mcp
description: "Read and write the `avatar_digital_brain` Neo4j graph through the Neo4j Cypher MCP using the repo's real patterns: alias-first entity resolution, heavy-node lookup, deterministic JournalEntry chain linking, and live-schema-aware relationship selection. This is the low-level graph primitive behind the buddy session plus delegated read-memory and write-memory workers."
---

# Digital Brain Buddy Graph MCP

Use this skill when Codex needs to inspect or mutate the `avatar_digital_brain` graph directly through Neo4j MCP.

## Start Here

1. Read `references/runtime-patterns.md` before writing Cypher.

2. Treat runtime code and live schema as the source of truth.

3. If docs conflict with code or live schema, prefer:
- `mcp_servers/cypher/src/digital_brain_mcp_cypher/journal.py` (append protocol)
- `mcp_servers/cypher/src/digital_brain_mcp_cypher/query_tools.py` (generic write guard)
- `mcp_servers/cypher/src/digital_brain_mcp_cypher/server.py` (MCP tools)
- `digital_brain/tools/mcp_client.py`
- `digital_brain/services/entity_resolver.py`
- `digital_brain/callbacks/journal_chain_guard.py` (ADK defense-in-depth only)
- live `get_neo4j_schema`

## MCP Tools

Use the plugin-owned local Neo4j Cypher MCP server only:

- Server id: `digital-brain-neo4j`
- Config source: this plugin's `.mcp.json`
- Expected local URL shape: `http://<mac-reachable-host>:8000/api/mcp/`

Do not use the ChatGPT Apps connector named `Neo4j Cypher`
(`mcp__codex_apps__neo4j_cypher`). That connector is a separate app/link, not
this plugin's MCP server, and may still point at the retired Cloud Run service.
If only `mcp__codex_apps__neo4j_cypher` is visible, treat plugin MCP discovery
as broken for that thread and fall back to the repo-local HTTP client:
`DIGITAL_BRAIN_MCP_URL=<plugin .mcp.json url> uv run python ...` from the
`avatar_digital_brain` repo.

Expected tools on `digital-brain-neo4j`:

- `get_neo4j_schema(sample_size=100)` when labels or relationship names are uncertain.
- `read_neo4j_cypher(query, params, embed_text)` for reads and vector search.
- `write_neo4j_cypher(query, params)` for non-journal mutations and idempotent post-append links.
- `get_journal_chain_head()` immediately before a JournalEntry append.
- `bootstrap_journal_chain(head_element_id?, empty)` is operator-only; use it
  only after the integrity audit selects a legacy head (or on a fresh graph).
- `append_journal_entry(append_key, content, timestamp, mood, expected_version, properties?)` for the only supported JournalEntry write path.
- `get_journal_append_receipt(append_key)` to reconcile an uncertain append.

Rules:

- Pass `params` as an object, not a JSON string, when using the MCP connector directly.
- The append tool owns embedding generation. `embed_text` remains for semantic read queries, not JournalEntry writes.
- The plugin MCP URL in `.mcp.json` is a literal `http://localhost:8000/api/mcp/` (some hosts do not expand shell-style env defaults). To point elsewhere, edit that file or call the repo HTTP client with `DIGITAL_BRAIN_MCP_URL`.

## Read Workflow

1. If the task depends on relation names, inspect live schema first.

2. Mirror the repo's recency filter for journal lookups:
- require non-empty `content` or `raw_text`
- require non-empty `timestamp`, `entry_date`, or `created_at`
- sort by `entry_date DESC, timestamp DESC, created_at DESC`

3. Mirror core-entity lookup when the goal is stable memory:
- exclude `Operational`, `JournalEntry`, `Alias`, and `LearningLog`
- require `name`
- prioritize `Person` and `Organization`
- otherwise use connection count (`weight = COUNT { (n)--() }`)

4. Mirror entity resolution before assuming a node is new:
- check `Alias` first by `from_name`
- then use type-specific lookup
- `Person`: fuzzy or contains match on `name`
- `Topic`, `State`, `Organization`, `Location`, `Object`: case-insensitive exact `name`
- `Event`: lookup by `type` or time context, not free-text description alone

## Write Workflow

1. Mint one UUID `append_key`, then fetch `get_journal_chain_head()` immediately before append.

2. Call `append_journal_entry` with the returned `expected_version`. The server
   creates the stable id and embedding; first entry is HEAD-only, later entries
   also create FOLLOWS. Embedding runs outside the Neo4j lock.

3. On timeout, inspect `get_journal_append_receipt(append_key)` (`found` |
   `not_found`); never submit a new raw write or a new key blindly.

4. Reuse existing entities.
- `MERGE` by `id` when it exists.
- If an entity exists but its `id` is missing, match by name and repair the `id`.
- `CREATE` new entities only after resolution has failed.

5. Prefer live relationship names over schema assumptions.
- The live graph currently uses both generic `MENTIONS` and typed relations such as `MENTIONS_PERSON`, `MENTIONS_TOPIC`, `MENTIONS_ORG`, and `MENTIONS_PLACE`.
- `DESCRIBES` is the stable write path for `JournalEntry -> Event`.

## Do Not

- Do not trust stale prompt docs over the live graph.
- Do not use `mcp__codex_apps__neo4j_cypher`; it is not the plugin-owned MCP server.
- Do not create or merge `JournalEntry` or `FOLLOWS` through generic Cypher.
- Do not assume all mentions use one relationship type.
- Do not stringify `params` when calling the direct MCP connector.
- Do not run unresolved writer flows in parallel; a stale head produces an explicit append conflict.
