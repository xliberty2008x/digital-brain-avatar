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
- `digital_brain/tools/mcp_client.py`
- `digital_brain/services/entity_resolver.py`
- `digital_brain/services/recent_entries_service.py`
- `digital_brain/services/core_entity_service.py`
- `digital_brain/callbacks/journal_chain_guard.py`
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
- `write_neo4j_cypher(query, params, embed_text)` for mutations and embedding-backed `JournalEntry` writes.

Rules:

- Pass `params` as an object, not a JSON string, when using the MCP connector directly.
- `embed_text` is mandatory on JournalEntry writes — the MCP server hard-rejects a JournalEntry create/merge with no `embed_text`. Also use it on semantic read queries that rely on vector search.

## Read Workflow

1. If the task depends on relation names, inspect live schema first.

2. Mirror the repo's recency filter for journal lookups:
- require non-empty `content` or `raw_text`
- require non-empty `timestamp`, `entry_date`, or `created_at`
- sort by `entry_date DESC, timestamp DESC, created_at DESC`

3. Mirror core-entity lookup when the goal is stable memory:
- exclude `JournalEntry`, `Alias`, and `LearningLog`
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

1. Before creating a `JournalEntry`, fetch the latest valid journal id.

2. Every `JournalEntry` create or merge must include an explicit `id`.

3. If a previous journal id is known, the same query must link the new entry to it.
- Prefer `FOLLOWS`.
- Accept `NEXT_ENTRY`, `PRECEDED_BY`, or `NEXT` only when matching existing graph patterns.

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
- Do not create a `JournalEntry` without explicit `id` and chain link.
- Do not assume all mentions use one relationship type.
- Do not stringify `params` when calling the direct MCP connector.
- Do not run concurrent writer flows that can race on latest-entry lookup.
