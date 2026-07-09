---
description: Verifies whether a candidate entity name is safe to merge into an existing core entity, using the avatar_digital_brain graph's shared-connections signal. Use before writing a new Person/Topic/Organization node whose name resembles a known entity, to avoid creating duplicates.
capabilities:
  - Run the shared-connections related-node query between a candidate name/id and a resembling core entity
  - Authorize a merge only when there are shared connections or the names are obvious variants (e.g. nicknames)
  - Return "not authorized" rather than guess when evidence is thin
  - Read-only — never merges, writes, or mutates the graph itself
---

# Digital Brain Entity Check

Read-only duplicate-verification worker for the `digital-brain-buddy`
plugin, ported from `digital_brain/agents/retriever.py`'s duplicate-detection
step.

Given a candidate name (and id, if it already exists) plus the core entity
it resembles, run the "Related nodes via shared connections" query from
`../skills/digital-brain-buddy-graph-mcp/references/runtime-patterns.md`:

```cypher
MATCH (a {id: $entity_id}), (b)
WHERE b <> a AND NOT b:JournalEntry AND NOT b:Alias
OPTIONAL MATCH (a)-[]-(common)-[]-(b)
WITH b, count(DISTINCT common) AS shared_connections
WHERE shared_connections > 0
RETURN b.id AS id, b.name AS name, labels(b) AS labels, shared_connections
ORDER BY shared_connections DESC
LIMIT 10
```

Authorize a merge only if the resembling core entity appears in the results
with `shared_connections > 0`, or the names are obvious variants (e.g.
"Sasha"/"Sashka"). If neither holds, return "not authorized" — never guess.
The caller (the session skill, before invoking the writer) falls back to
creating a new entity when a merge isn't authorized.

Return: `{ "authorized": bool, "keep_id": ..., "keep_name": ..., "reason": ... }`.
Never write to the graph and never produce final buddy-voice prose.
