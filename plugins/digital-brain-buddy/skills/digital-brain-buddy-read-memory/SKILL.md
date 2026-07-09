---
name: digital-brain-buddy-read-memory
description: Fetch bounded evidence from the `avatar_digital_brain` graph for a buddy session. Use when the main session agent wants a subagent to gather recent entries, heavy nodes, semantic matches, or short graph traversals without carrying the full retrieval context itself.
---

# Digital Brain Buddy Read Memory

Use this skill for delegated read-only graph retrieval, including the mandatory `BOOTSTRAP` context read at the start of a new buddy conversation.

## Start Here

1. Read `../digital-brain-buddy-graph-mcp/references/runtime-patterns.md`.

2. Treat runtime code and live graph schema as the source of truth.

3. Read only what the parent task actually needs, except `BOOTSTRAP`, which must gather the full first-layer context.

## Scope

This worker is for retrieval only:

- mandatory `BOOTSTRAP` evidence packs for a new buddy conversation
- all known people with ids, names, roles/relations, direct relationship context,
  and compact recurring/sensitive theme summaries
- top-20 weighted core nodes and compact node label/type weight summaries
- recent valid `JournalEntry` rows
- core entities / heavy nodes
- vector or semantic journal lookups
- one-hop or two-hop traversals around matched nodes
- shared-connections related-node discovery around matched nodes (see
  `../digital-brain-buddy-graph-mcp/references/runtime-patterns.md`'s
  "Related nodes via shared connections" — nodes ranked by common
  neighbors, not just direct hops)
- alias-first identity checks when a name is ambiguous

## Bootstrap Evidence Pack

When the parent task type is `BOOTSTRAP` or says this is a new buddy conversation,
return a compact first-layer context pack before any interpretation or write:

1. `people_map`: all existing `Person` nodes. Include canonical `id`, `name`,
   `role`, `relation`, direct relationship types/properties when available, and
   how the person appears related to the user. If the graph has only partial
   evidence, mark the relation as inferred or thin.
2. `person_sensitive_themes`: for each meaningful person, summarize the topics,
   states, organizations, events, or relationship dynamics that repeatedly
   co-occur with them. Ground this in linked journal entries and keep it short.
3. `top_weighted_nodes`: top 20 non-internal nodes ranked by graph degree.
   Mirror the runtime heavy-node pattern from
   `../digital-brain-buddy-graph-mcp/references/runtime-patterns.md`: exclude
   `JournalEntry`, `Alias`, and `LearningLog`; use `COUNT { (n)--() }` as
   weight; sort descending.
4. `node_type_weight_summary`: compact non-internal label/type summary ranked
   by summed node weights.
5. `recent_baseline`: only the most recent valid journal entries needed to
   orient the current period.

Do not create people during this step. The point is to prevent duplicate people
and to give the session a stable social/context map before it answers.

## Output Shape

Return a compact evidence pack for the parent session agent:

- what was queried
- bootstrap people map, top weighted nodes, and node type summary when requested
- strongest factual matches
- related nodes ranked by shared connections, kept separate from exact/direct matches
- thin/contradictory areas
- ids or canonical names worth reusing in later writes

Prefer short bullet-like summaries over raw dumps.

## Rules

- Use only the plugin-owned `digital-brain-neo4j` MCP server from `.mcp.json`.
- Do not use the ChatGPT Apps connector `mcp__codex_apps__neo4j_cypher`; it is
  separate from this plugin and may point at the retired Cloud Run service.
- If the plugin-owned MCP tools are not visible in the thread, report that
  plugin MCP discovery failed and use the repo-local HTTP client with
  `DIGITAL_BRAIN_MCP_URL=<plugin .mcp.json url>` from the `avatar_digital_brain`
  repo instead of calling the stale app connector.
- Ground claims in graph evidence.
- Separate direct facts from inference.
- If the graph is thin or conflicting, say so directly.
- Favor compactness. The parent session agent owns final synthesis.

## Do Not

- Do not write or mutate the graph.
- Do not generate final buddy-tone prose for the user.
- Do not carry unrelated context just because it is interesting.
