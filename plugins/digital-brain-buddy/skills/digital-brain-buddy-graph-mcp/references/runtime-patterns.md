# Runtime Patterns For `avatar_digital_brain`

This file summarizes the actual Neo4j and MCP patterns used by the repo and the live graph.

## Ground Truth Order

Prefer these sources in this order:

1. Live `get_neo4j_schema`
2. Runtime code in `digital_brain/`
3. Historical write traces in `digital_brain/misc/request.txt`
4. Design docs in `docs/`

## Actual Read Path

### Direct MCP wrapper

`digital_brain/tools/mcp_client.py`

- `call_mcp_tool()` sends JSON-RPC `tools/call` requests to the Neo4j Cypher MCP endpoint.
- `execute_cypher()` is the common read helper.
- Reads pass `params` as a plain object.
- The helper parses MCP `content[0].text` as JSON rows.

### Recent journal reads

`digital_brain/services/recent_entries_service.py`

Runtime filter:

```cypher
trim(coalesce(toString(j.content), toString(j.raw_text), '')) <> ''
AND trim(coalesce(toString(j.timestamp), toString(j.entry_date), toString(j.created_at), '')) <> ''
```

Ordering:

```cypher
ORDER BY j.entry_date DESC, j.timestamp DESC, j.created_at DESC
```

Entity expansion pattern:

- `OPTIONAL MATCH (j)-[r]->(e)`
- drop `JournalEntry` and `Alias`
- expose `label = head(labels(e))`
- expose `relation = type(r)`

### Core-entity reads

`digital_brain/services/core_entity_service.py`

Heavy-node pattern:

- exclude `JournalEntry`, `Alias`, `LearningLog`
- require `n.name`
- include all `Person` and `Organization`
- include everything else only if `COUNT { (n)--() } >= threshold`
- return `weight = COUNT { (n)--() }`

### Entity resolution before write

`digital_brain/services/entity_resolver.py`

Lookup order:

1. `Alias` lookup by `from_name`
2. Type-specific node lookup

Type-specific patterns:

- `Person`: case-insensitive contains or fuzzy match on `name`
- `Topic`: exact case-insensitive `name`
- `State`: exact case-insensitive `name`
- `Event`: lookup by `type`
- fallback labels: exact case-insensitive `name`

## Actual Write Path

### Writer contract

`digital_brain/agents/writer.py`

- `MERGE` existing entities by id
- repair missing ids when a node exists by name
- `CREATE` new entities only after resolution
- create `JournalEntry` with explicit `id`
- link each new `JournalEntry` to the previous one with `FOLLOWS`

### Deterministic guard

`digital_brain/callbacks/journal_chain_guard.py`

Before `write_neo4j_cypher`, if a query creates or merges `(:JournalEntry ...)`, the guard requires:

1. explicit `id` in the property map
2. chain link to the previous journal id when available

Accepted chain relations:

- `FOLLOWS`
- `NEXT_ENTRY`
- `PRECEDED_BY`
- `NEXT`

### Execution path

`digital_brain/agents/executor.py`

- all mutations go through `write_neo4j_cypher`
- JournalEntry writes must include `embed_text` — the MCP server (`mcp_servers/cypher`) hard-rejects the write otherwise

## Live Graph Snapshot Checked On 2026-04-08

Direct MCP queries showed:

- JournalEntry to JournalEntry relations:
  - `FOLLOWS`: 49
  - `PRECEDED_BY`: 2
  - `NEXT`: 1

- JournalEntry outbound relationship counts:
  - `MENTIONS`: 615
  - `DESCRIBES`: 414
  - `MENTIONS_TOPIC`: 27
  - `MENTIONS_PERSON`: 25
  - `MENTIONS_ORG`: 22
  - `MENTIONS_PLACE`: 6
  - `EXPRESSES`: 5

Interpretation:

- `FOLLOWS` is the dominant chain relation and should be the default for new writes.
- Generic `MENTIONS` is still the dominant mention relation in the live graph.
- Typed mention relations also exist and are real, not theoretical.

## Safe Cypher Templates

### Startup people map

Use at the start of a new buddy conversation before interpreting people or
writing new `Person` nodes.

```cypher
MATCH (p:Person)
OPTIONAL MATCH (u:Person {id: $self_id})-[ur]-(p)
RETURN
  p.id AS id,
  CASE
    WHEN p.name IS :: LIST<STRING> THEN p.name[0]
    ELSE p.name
  END AS name,
  CASE WHEN p.role IS :: LIST<STRING> THEN p.role[0] ELSE p.role END AS role,
  CASE WHEN p.relation IS :: LIST<STRING> THEN p.relation[0] ELSE p.relation END AS relation,
  collect(DISTINCT {
    type: type(ur),
    direction: CASE
      WHEN ur IS NULL THEN NULL
      WHEN startNode(ur) = u THEN 'out'
      ELSE 'in'
    END,
    props: properties(ur)
  }) AS user_relationships
ORDER BY role, name
```

If `self_id` is unknown, run the same query without the optional `u` match and
return `id`, `name`, `role`, and `relation`.

### Person-sensitive theme scan

Use this after the startup people map to summarize themes that repeatedly attach
to people. Keep the result compact; do not paste raw journal dumps.

```cypher
MATCH (p:Person)<-[:MENTIONS|MENTIONS_PERSON]-(j:JournalEntry)
WHERE j.id IS NOT NULL
  AND trim(coalesce(toString(j.content), toString(j.raw_text), '')) <> ''
OPTIONAL MATCH (j)-[r]->(e)
WHERE NOT e:JournalEntry AND NOT e:Alias AND e <> p
WITH
  p,
  collect(DISTINCT {
    label: head(labels(e)),
    name: CASE
      WHEN e.name IS :: LIST<STRING> THEN e.name[0]
      WHEN e.name IS NOT NULL THEN e.name
      WHEN e.type IS NOT NULL THEN e.type
      WHEN e.description IS NOT NULL THEN left(e.description, 100)
      ELSE NULL
    END,
    relation: type(r)
  }) AS themes,
  count(DISTINCT j) AS journal_count
RETURN
  p.id AS person_id,
  CASE WHEN p.name IS :: LIST<STRING> THEN p.name[0] ELSE p.name END AS person_name,
  journal_count,
  [t IN themes WHERE t.name IS NOT NULL][0..12] AS themes
ORDER BY journal_count DESC
```

### Top 20 weighted context nodes

This is the startup-session version of the heavy-node pattern.

```cypher
MATCH (n)
WHERE n.name IS NOT NULL
  AND NOT 'JournalEntry' IN labels(n)
  AND NOT 'Alias' IN labels(n)
  AND NOT 'LearningLog' IN labels(n)
WITH n, COUNT { (n)--() } AS weight
RETURN
  coalesce(n.id, elementId(n)) AS id,
  CASE
    WHEN n.name IS :: LIST<STRING> THEN n.name[0]
    ELSE n.name
  END AS name,
  labels(n) AS labels,
  weight
ORDER BY weight DESC
LIMIT 20
```

### Node label/type weight summary

Use this alongside the top weighted node list when the session needs to know
which node categories dominate the user's graph.

```cypher
MATCH (n)
WHERE NOT 'JournalEntry' IN labels(n)
  AND NOT 'Alias' IN labels(n)
  AND NOT 'LearningLog' IN labels(n)
WITH n, labels(n) AS labels, COUNT { (n)--() } AS node_weight
UNWIND labels AS label
RETURN
  label,
  count(*) AS node_count,
  sum(node_weight) AS weight
ORDER BY weight DESC, node_count DESC
LIMIT 20
```

### Latest valid JournalEntry id

```cypher
MATCH (j:JournalEntry)
WHERE j.id IS NOT NULL
  AND trim(toString(j.id)) <> ''
  AND trim(coalesce(toString(j.content), toString(j.raw_text), '')) <> ''
  AND trim(coalesce(toString(j.timestamp), toString(j.entry_date), toString(j.created_at), '')) <> ''
RETURN j.id AS id
ORDER BY j.entry_date DESC, j.timestamp DESC, j.created_at DESC
LIMIT 1
```

### Alias-first entity lookup

```cypher
MATCH (a:Alias)
WHERE toLower(a.from_name) = toLower($name)
RETURN a.canonical_id AS id, a.to_name AS name
LIMIT 1
```

### Related nodes via shared connections

Use this to find nodes related to a matched entity beyond direct one-hop
mentions — ranked by how many neighbors they share, mirroring the ADK
retriever's duplicate-verification technique
(`digital_brain/agents/retriever.py`). Useful both for surfacing "related
people/topics" in read evidence packs and for verifying whether a
candidate name is the same entity as an existing core entity before
authorizing a merge.

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

### Chain-safe JournalEntry write skeleton

```cypher
MATCH (prev:JournalEntry {id: $prev_id})
CREATE (j:JournalEntry {
  id: $journal_id,
  content: $content,
  timestamp: $timestamp,
  mood: $mood
})
MERGE (j)-[:FOLLOWS]->(prev)
RETURN j.id AS id
```

Use `embed_text = $content` on the MCP write call when the entry should participate in vector search.
