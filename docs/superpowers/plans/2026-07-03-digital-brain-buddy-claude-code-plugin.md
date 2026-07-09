# Digital Brain Buddy: Claude Code + Cowork Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `plugins/digital-brain-buddy/` (today Codex-only) also install and run in the Claude Code CLI and Claude Desktop's Cowork feature, on top of the repo's already-local Neo4j/MCP stack, with hard-enforced embeddings, related-node read discovery, real native subagents, and a docker-compose lifecycle hook.

**Architecture:** One shared `skills/`/`.mcp.json`/persona tree under `plugins/digital-brain-buddy/`, read by three host manifests (`.codex-plugin/plugin.json` unchanged, new `.claude-plugin/plugin.json` read by both Claude Code and Cowork). New `agents/*.md` give Claude Code/Cowork real Task-tool subagents mirroring the ADK app's reader/writer/duplicate-check split. A `hooks/hooks.json` SessionStart hook brings up the local Docker stack. One shared-infra fix (`mcp_servers/cypher`) hard-enforces embeddings for every caller, not just this plugin.

**Tech Stack:** Python 3.12 (`mcp_servers/cypher`, pytest via `uv run pytest`), Markdown/YAML plugin manifests (Claude Code plugin format), Bash (compose lifecycle script), Neo4j + Cypher, Docker Compose.

## Global Constraints

- Local MCP endpoint default: `http://localhost:8000/api/mcp/` (override via `DIGITAL_BRAIN_MCP_URL`), matching `digital_brain/config.py`.
- `JournalEntry` writes must always carry `embed_text`; enforced in `mcp_servers/cypher/src/digital_brain_mcp_cypher/query_tools.py`, not just in prose.
- No changes to `.codex-plugin/plugin.json` or any skill's `agents/openai.yaml`.
- No changes to `digital_brain/agent.py` or its ADK pipeline beyond the shared `query_tools.py` fix.
- No new embedding models/providers/dimensions — stays on local `bge-m3` / 1024 dims.
- Test command: `uv run pytest tests/<file>.py -v` (confirmed working in this repo).

## Verification Loops

This plan uses two different iteration loops, and they are not interchangeable:

- **Programmatic parts** (Task 1's `query_tools.py` change, Task 8's
  `compose-up.sh`) — **code → simplify → review**. After the TDD steps turn
  the tests green, dispatch a `code-simplifier` agent on the changed file,
  re-run the tests to confirm the simplify pass didn't break anything, then
  dispatch a `pr-review-toolkit:code-reviewer` agent for a final check before
  committing. This is standard code review — one pass, no panel.
- **Plugin behavior as a whole** (Task 9) — **search → critic panel → fix →
  simplify → re-run search + critics**. Skill/subagent prose can't be unit
  tested, so Task 9 dispatches the real `digital-brain-reader` /
  `digital-brain-writer` / `digital-brain-entity-check` subagents against the
  live local graph, then dispatches 2-3 independent critic subagents to
  adversarially evaluate whether the observed behavior actually matches the
  spec. If the critics find a real problem, fix it, simplify, and re-run the
  whole search-and-critic pass — not just re-ask the same critics the same
  question. Cap at 3 loop iterations; if it's still failing after that, stop
  and report to the human rather than looping forever.

Implementation of this plan is subagent-driven end to end: use
`superpowers:subagent-driven-development` (fresh subagent per task, review
between tasks) rather than executing tasks inline.

---

## File Structure

**Create:**
- `plugins/digital-brain-buddy/.claude-plugin/plugin.json` — Claude Code / Cowork manifest.
- `plugins/digital-brain-buddy/version.json` — Cowork re-sync version marker.
- `.claude-plugin/marketplace.json` (repo root) — local marketplace descriptor so the plugin can be installed into Claude Code for manual verification.
- `plugins/digital-brain-buddy/agents/digital-brain-reader.md` — native read-only subagent.
- `plugins/digital-brain-buddy/agents/digital-brain-writer.md` — native write subagent.
- `plugins/digital-brain-buddy/agents/digital-brain-entity-check.md` — native duplicate-check subagent.
- `plugins/digital-brain-buddy/hooks/hooks.json` — SessionStart hook config.
- `plugins/digital-brain-buddy/scripts/compose-up.sh` — brings up `neo4j`, `mcp-cypher`, `mcp-memory`.
- `plugins/digital-brain-buddy/commands/digital-brain-up.md` — `/digital-brain-up` manual recovery command.

**Modify:**
- `mcp_servers/cypher/src/digital_brain_mcp_cypher/query_tools.py` — hard-reject `JournalEntry` writes missing `embed_text`.
- `tests/test_local_mcp_query_tools.py` — new coverage for the hard-reject rule.
- `plugins/digital-brain-buddy/.mcp.json` — local endpoint instead of the decommissioned Cloud Run URL.
- `plugins/digital-brain-buddy/skills/digital-brain-buddy-graph-mcp/references/runtime-patterns.md` — new related-node Cypher template; "embed_text mandatory" wording.
- `plugins/digital-brain-buddy/skills/digital-brain-buddy-read-memory/SKILL.md` — related-node discovery in scope/output.
- `plugins/digital-brain-buddy/skills/digital-brain-buddy-write-memory/SKILL.md` — mandatory (not preferred) embeddings.
- `plugins/digital-brain-buddy/skills/digital-brain-buddy-graph-mcp/SKILL.md` — mandatory embeddings wording.
- `plugins/digital-brain-buddy/skills/digital-brain-buddy-session/SKILL.md` — name concrete native subagents; add entity-check delegation step.

---

### Task 1: Hard-enforce `embed_text` on JournalEntry writes at the MCP server

**Files:**
- Modify: `mcp_servers/cypher/src/digital_brain_mcp_cypher/query_tools.py:40-46`
- Test: `tests/test_local_mcp_query_tools.py`

**Interfaces:**
- Produces: `validate_embedding_usage(query: str, embed_text: str | None) -> None` — now raises `ValueError` for *any* `CREATE`/`MERGE (:JournalEntry ...)` query where `embed_text` is falsy (previously only checked the `$embedding` param when `embed_text` was already provided). Call site is unchanged: `mcp_servers/cypher/src/digital_brain_mcp_cypher/server.py`'s `write_neo4j_cypher` already calls `validate_embedding_usage(query, embed_text)` before running the write.

- [x] **Step 1: Write the two new failing/characterization tests**

Add to the end of `tests/test_local_mcp_query_tools.py`:

```python
def test_validate_embedding_usage_rejects_journal_write_missing_embed_text():
    query = "CREATE (j:JournalEntry {id: $id, content: $content}) RETURN j"

    try:
        validate_embedding_usage(query, None)
    except ValueError as exc:
        assert "embed_text" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_validate_embedding_usage_allows_non_journal_write_without_embed_text():
    query = "CREATE (p:Person {id: $id, name: $name}) RETURN p"

    validate_embedding_usage(query, None)
```

- [x] **Step 2: Run the test file and confirm the expected failure**

Run: `uv run pytest tests/test_local_mcp_query_tools.py -v`
Expected: `test_validate_embedding_usage_rejects_journal_write_missing_embed_text` **FAILS** with `AssertionError: expected ValueError` (current code returns early on falsy `embed_text` without raising). The other 6 tests (5 existing + the new non-journal characterization test) **PASS** — the non-journal test already holds under today's implementation, it's here to lock in the "don't false-positive on non-JournalEntry writes" behavior going forward.

- [x] **Step 3: Implement the hard-reject rule**

In `mcp_servers/cypher/src/digital_brain_mcp_cypher/query_tools.py`, replace:

```python
def validate_embedding_usage(query: str, embed_text: str | None) -> None:
    """Require JournalEntry writes with embed_text to consume `$embedding`."""
    if not embed_text:
        return
    if JOURNAL_WRITE_RE.search(query or "") and not EMBEDDING_PARAM_RE.search(query or ""):
        raise ValueError(
            "JournalEntry writes that pass embed_text must set an embedding property with `$embedding`"
        )
```

with:

```python
def validate_embedding_usage(query: str, embed_text: str | None) -> None:
    """Require JournalEntry writes to always pass embed_text and consume `$embedding`."""
    if not JOURNAL_WRITE_RE.search(query or ""):
        return
    if not embed_text:
        raise ValueError(
            "JournalEntry writes must pass embed_text so the entry gets an embedding"
        )
    if not EMBEDDING_PARAM_RE.search(query or ""):
        raise ValueError(
            "JournalEntry writes that pass embed_text must set an embedding property with `$embedding`"
        )
```

- [x] **Step 4: Run the full test file and confirm all pass**

Run: `uv run pytest tests/test_local_mcp_query_tools.py -v`
Expected: `7 passed`

- [x] **Step 5: Simplify pass**

Dispatch a `code-simplifier` agent scoped to
`mcp_servers/cypher/src/digital_brain_mcp_cypher/query_tools.py` (reuse/
efficiency/clarity only — it must not change behavior). After it returns,
re-run `uv run pytest tests/test_local_mcp_query_tools.py -v` and confirm
`7 passed` again.

- [x] **Step 6: Review pass**

Dispatch a `pr-review-toolkit:code-reviewer` agent scoped to the diff of
`mcp_servers/cypher/src/digital_brain_mcp_cypher/query_tools.py`. Address any
high-confidence findings it raises, re-running the tests after each fix,
before moving on.

- [x] **Step 7: Commit**

```bash
git add mcp_servers/cypher/src/digital_brain_mcp_cypher/query_tools.py tests/test_local_mcp_query_tools.py
git commit -m "fix: hard-reject JournalEntry writes missing embed_text at the MCP server"
```

---

### Task 2: Fix the plugin's `.mcp.json` to point at the local Neo4j MCP endpoint

**Files:**
- Modify: `plugins/digital-brain-buddy/.mcp.json`

**Interfaces:**
- Produces: the `digital-brain-neo4j` MCP server entry all skills/subagents connect through, now resolving to the same local endpoint the rest of the repo uses.

- [x] **Step 1: Replace the Cloud Run URL with the local env-driven default**

Replace the full contents of `plugins/digital-brain-buddy/.mcp.json`:

```json
{
  "mcpServers": {
    "digital-brain-neo4j": {
      "type": "http",
      "url": "${DIGITAL_BRAIN_MCP_URL:-http://localhost:8000/api/mcp/}",
      "note": "Local Neo4j Cypher MCP endpoint for avatar_digital_brain. Exposes read_neo4j_cypher, write_neo4j_cypher, and get_neo4j_schema. Override with DIGITAL_BRAIN_MCP_URL."
    }
  }
}
```

- [x] **Step 2: Validate JSON syntax**

Run: `python3 -m json.tool plugins/digital-brain-buddy/.mcp.json`
Expected: pretty-printed JSON is echoed back with no error.

- [x] **Step 3: Confirm the stale URL is gone from the repo**

Run: `grep -rn "run.app" plugins/digital-brain-buddy/`
Expected: no output (no matches).

- [x] **Step 4: Commit**

```bash
git add plugins/digital-brain-buddy/.mcp.json
git commit -m "fix: point digital-brain-buddy plugin at the local Neo4j MCP endpoint"
```

---

### Task 3: Add the Claude Code / Cowork plugin manifest and a local marketplace for install/testing

**Files:**
- Create: `plugins/digital-brain-buddy/.claude-plugin/plugin.json`
- Create: `plugins/digital-brain-buddy/version.json`
- Create: `.claude-plugin/marketplace.json` (repo root)

**Interfaces:**
- Produces: a `digital-brain-buddy` plugin name that Claude Code and Cowork's plugin browsers recognize, and an `avatar-digital-brain-local` marketplace name that Task 9's manual verification installs from.

- [x] **Step 1: Create the Claude Code / Cowork manifest**

Create `plugins/digital-brain-buddy/.claude-plugin/plugin.json`:

```json
{
  "name": "digital-brain-buddy",
  "version": "0.1.0",
  "description": "Buddy persona with Neo4j memory tools for the avatar_digital_brain graph, running natively in Claude Code and Claude Desktop (Cowork).",
  "author": {
    "name": "Kyrylo Dubovyk"
  },
  "license": "UNLICENSED",
  "keywords": [
    "digital-brain",
    "neo4j",
    "buddy",
    "memory",
    "persona"
  ]
}
```

No custom `skills`/`agents`/`hooks`/`mcpServers` path overrides are needed — Claude Code and Cowork both auto-discover `skills/`, `agents/`, `hooks/hooks.json`, and `.mcp.json` at plugin root by default.

- [x] **Step 2: Create the Cowork re-sync version marker**

Create `plugins/digital-brain-buddy/version.json`:

```json
"0.1.0"
```

- [x] **Step 3: Create the repo-root local marketplace descriptor**

Create `.claude-plugin/marketplace.json`:

```json
{
  "name": "avatar-digital-brain-local",
  "owner": {
    "name": "Kyrylo Dubovyk"
  },
  "plugins": [
    {
      "name": "digital-brain-buddy",
      "source": "./plugins/digital-brain-buddy",
      "description": "Buddy persona with Neo4j memory tools for the avatar_digital_brain graph."
    }
  ]
}
```

- [x] **Step 4: Validate both manifests**

Run:
```bash
claude plugin validate plugins/digital-brain-buddy --strict
claude plugin validate . --strict
```
Expected: both commands report the manifest as valid (no errors; `--strict` also surfaces unrecognized-field warnings as errors, so a clean pass here means the JSON shape matches what Claude Code expects).

- [x] **Step 5: Commit**

```bash
git add plugins/digital-brain-buddy/.claude-plugin/plugin.json plugins/digital-brain-buddy/version.json .claude-plugin/marketplace.json
git commit -m "feat: add Claude Code/Cowork plugin manifest and local marketplace for digital-brain-buddy"
```

---

### Task 4: Add shared-connections related-node discovery to the read skill

**Files:**
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-graph-mcp/references/runtime-patterns.md`
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-read-memory/SKILL.md`

**Interfaces:**
- Produces: a documented `Related nodes via shared connections` Cypher template that Task 6's `digital-brain-entity-check` subagent also references by name.

- [x] **Step 1: Add the related-node Cypher template to `runtime-patterns.md`**

In `plugins/digital-brain-buddy/skills/digital-brain-buddy-graph-mcp/references/runtime-patterns.md`, insert a new section between `### Alias-first entity lookup` and `### Chain-safe JournalEntry write skeleton`:

Find this exact text:

```markdown
### Alias-first entity lookup

```cypher
MATCH (a:Alias)
WHERE toLower(a.from_name) = toLower($name)
RETURN a.canonical_id AS id, a.to_name AS name
LIMIT 1
```

### Chain-safe JournalEntry write skeleton
```

Replace it with:

```markdown
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
```

- [x] **Step 2: Add related-node discovery to the read-memory skill's scope**

In `plugins/digital-brain-buddy/skills/digital-brain-buddy-read-memory/SKILL.md`, find:

```markdown
- one-hop or two-hop traversals around matched nodes
- alias-first identity checks when a name is ambiguous
```

Replace with:

```markdown
- one-hop or two-hop traversals around matched nodes
- shared-connections related-node discovery around matched nodes (see
  `../digital-brain-buddy-graph-mcp/references/runtime-patterns.md`'s
  "Related nodes via shared connections" — nodes ranked by common
  neighbors, not just direct hops)
- alias-first identity checks when a name is ambiguous
```

- [x] **Step 3: Add related nodes to the read-memory skill's output shape**

In the same file, find:

```markdown
## Output Shape

Return a compact evidence pack for the parent session agent:

- what was queried
- bootstrap people map, top weighted nodes, and node type summary when requested
- strongest factual matches
- thin/contradictory areas
- ids or canonical names worth reusing in later writes
```

Replace with:

```markdown
## Output Shape

Return a compact evidence pack for the parent session agent:

- what was queried
- bootstrap people map, top weighted nodes, and node type summary when requested
- strongest factual matches
- related nodes ranked by shared connections, kept separate from exact/direct matches
- thin/contradictory areas
- ids or canonical names worth reusing in later writes
```

- [x] **Step 4: Confirm the file structure is intact**

Run: `grep -c "^##" plugins/digital-brain-buddy/skills/digital-brain-buddy-read-memory/SKILL.md`
Expected: `6` (unchanged section count — this task only edits list items inside existing sections, it doesn't add or remove `##` headings).

- [x] **Step 5: Commit**

```bash
git add plugins/digital-brain-buddy/skills/digital-brain-buddy-graph-mcp/references/runtime-patterns.md plugins/digital-brain-buddy/skills/digital-brain-buddy-read-memory/SKILL.md
git commit -m "feat: add shared-connections related-node discovery to the read skill"
```

---

### Task 5: Make "embeddings mandatory" wording consistent across the skill docs

**Files:**
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-write-memory/SKILL.md`
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-graph-mcp/SKILL.md`
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-graph-mcp/references/runtime-patterns.md`

**Interfaces:**
- Consumes: Task 1's hard-reject behavior in `query_tools.py` (this task only updates prose to match it).

- [x] **Step 1: Update write-memory's Write Rules**

In `plugins/digital-brain-buddy/skills/digital-brain-buddy-write-memory/SKILL.md`, find:

```markdown
## Write Rules

- Every `JournalEntry` must include explicit `id`.
- Prefer `FOLLOWS` for chain linking.
- Use `embed_text` for `JournalEntry` writes.
- Keep the query serial and deterministic.
```

Replace with:

```markdown
## Write Rules

- Every `JournalEntry` must include explicit `id`.
- Prefer `FOLLOWS` for chain linking.
- Every `JournalEntry` write must pass `embed_text` — the MCP server hard-rejects a `JournalEntry` create/merge with no `embed_text`, so this is mandatory, not a preference.
- Keep the query serial and deterministic.
```

- [x] **Step 2: Update graph-mcp's MCP Tools rules**

In `plugins/digital-brain-buddy/skills/digital-brain-buddy-graph-mcp/SKILL.md`, find:

```markdown
Rules:

- Pass `params` as an object, not a JSON string, when using the MCP connector directly.
- Use `embed_text` on JournalEntry writes, and on semantic read queries that rely on vector search.
```

Replace with:

```markdown
Rules:

- Pass `params` as an object, not a JSON string, when using the MCP connector directly.
- `embed_text` is mandatory on JournalEntry writes — the MCP server hard-rejects a JournalEntry create/merge with no `embed_text`. Also use it on semantic read queries that rely on vector search.
```

- [x] **Step 3: Update runtime-patterns' execution path note**

In `plugins/digital-brain-buddy/skills/digital-brain-buddy-graph-mcp/references/runtime-patterns.md`, find:

```markdown
### Execution path

`digital_brain/agents/executor.py`

- all mutations go through `write_neo4j_cypher`
- JournalEntry writes are expected to include `embed_text`
```

Replace with:

```markdown
### Execution path

`digital_brain/agents/executor.py`

- all mutations go through `write_neo4j_cypher`
- JournalEntry writes must include `embed_text` — the MCP server (`mcp_servers/cypher`) hard-rejects the write otherwise
```

- [x] **Step 4: Confirm no stale "expected to include" wording remains**

Run: `grep -rn "expected to include" plugins/digital-brain-buddy/`
Expected: no output.

- [x] **Step 5: Commit**

```bash
git add plugins/digital-brain-buddy/skills/digital-brain-buddy-write-memory/SKILL.md plugins/digital-brain-buddy/skills/digital-brain-buddy-graph-mcp/SKILL.md plugins/digital-brain-buddy/skills/digital-brain-buddy-graph-mcp/references/runtime-patterns.md
git commit -m "docs: make embed_text mandatory (not preferred) consistent across skill docs"
```

---

### Task 6: Add native reader/writer/entity-check subagents

**Files:**
- Create: `plugins/digital-brain-buddy/agents/digital-brain-reader.md`
- Create: `plugins/digital-brain-buddy/agents/digital-brain-writer.md`
- Create: `plugins/digital-brain-buddy/agents/digital-brain-entity-check.md`

**Interfaces:**
- Consumes: Task 4's "Related nodes via shared connections" template (referenced by name from `digital-brain-entity-check.md`).
- Produces: three subagent names — `digital-brain-reader`, `digital-brain-writer`, `digital-brain-entity-check` — that Task 7's session skill update names explicitly.

- [x] **Step 1: Create the reader subagent**

Create `plugins/digital-brain-buddy/agents/digital-brain-reader.md`:

```markdown
---
description: Fetches bounded, read-only evidence from the avatar_digital_brain Neo4j graph for a buddy session — mandatory BOOTSTRAP packs, recent entries, semantic search, and shared-connections related-node discovery. Use when the main session needs graph context without carrying the full retrieval workflow itself.
capabilities:
  - Fetch the mandatory BOOTSTRAP evidence pack (people map, top-weighted nodes, node-type summary) at the start of a new buddy conversation
  - Run recent JournalEntry lookups and semantic/vector search for READ turns
  - Run one/two-hop traversal and shared-connections related-node discovery around matched entities
  - Never write or mutate the graph, and never produce final buddy-voice prose
---

# Digital Brain Reader

Read-only worker for the `digital-brain-buddy` plugin. Follow
`../skills/digital-brain-buddy-read-memory/SKILL.md` exactly — that file is
the source of truth for scope, the BOOTSTRAP evidence pack shape, and output
format. Before running any query, read
`../skills/digital-brain-buddy-graph-mcp/references/runtime-patterns.md` for
the actual Cypher templates, including the shared-connections related-node
query.

Return a compact evidence pack to the caller. Do not answer the user
directly and do not write to the graph.
```

- [x] **Step 2: Create the writer subagent**

Create `plugins/digital-brain-buddy/agents/digital-brain-writer.md`:

```markdown
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
```

- [x] **Step 3: Create the entity-check subagent**

Create `plugins/digital-brain-buddy/agents/digital-brain-entity-check.md`:

```markdown
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
```

- [x] **Step 4: Validate the plugin manifest picks up the new agents**

Run: `claude plugin validate plugins/digital-brain-buddy --strict`
Expected: valid, no errors (agents are auto-discovered from `agents/*.md`, no manifest change needed since Task 3 didn't set custom paths).

- [x] **Step 5: Commit**

```bash
git add plugins/digital-brain-buddy/agents/
git commit -m "feat: add native digital-brain-reader/writer/entity-check subagents"
```

---

### Task 7: Wire the session skill to the native subagents

**Files:**
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-session/SKILL.md`

**Interfaces:**
- Consumes: `digital-brain-reader`, `digital-brain-writer`, `digital-brain-entity-check` (Task 6).

- [x] **Step 1: Name the concrete subagents in "Start Here" step 6**

Find:

```markdown
6. Treat delegated memory I/O as the default internal execution pattern for this skill:
- keep the main agent focused on conversation, judgment, and final phrasing
- delegate bounded graph retrieval to `../digital-brain-buddy-read-memory/SKILL.md`
- delegate persistence to `../digital-brain-buddy-write-memory/SKILL.md`
- serialize writes through one writer worker at a time
- if the host runtime requires explicit user permission for subagents, honor that constraint and fall back locally until permission exists
```

Replace with:

```markdown
6. Treat delegated memory I/O as the default internal execution pattern for this skill:
- keep the main agent focused on conversation, judgment, and final phrasing
- delegate bounded graph retrieval to `../digital-brain-buddy-read-memory/SKILL.md` — on hosts with native subagents (Claude Code, Cowork), invoke `digital-brain-reader` directly instead of improvising the delegation
- delegate persistence to `../digital-brain-buddy-write-memory/SKILL.md` — on hosts with native subagents, invoke `digital-brain-writer` directly
- before writing a new or ambiguous entity that resembles a known core entity, delegate the duplicate check to `digital-brain-entity-check` (native subagent hosts) or the equivalent verification step in `references/subagent-prompts.md` (Codex); never authorize a merge without it
- serialize writes through one writer worker at a time
- if the host runtime requires explicit user permission for subagents, honor that constraint and fall back locally until permission exists
```

- [x] **Step 2: Update the "Subagent Mode" section's opening and reader bullet**

Find:

```markdown
## Subagent Mode

Use this mode by default when the host environment allows delegated execution.

- Main session agent owns:
  - reading `SOUL.MD`
  - running the mandatory `BOOTSTRAP` read before the first user-facing response
  - deciding whether the turn is `SKIP`, `READ`, or `WRITE`
  - separating fact from inference
  - the final buddy-facing response
- Reader subagent owns:
  - mandatory `BOOTSTRAP` evidence pack on the first turn of a new buddy conversation
  - recent entries
  - core entities / heavy nodes
  - people map: names, ids, relations to the user, and recurring sensitive themes
  - semantic journal lookup
  - one-hop or two-hop graph traversal for evidence packs
- Writer subagent owns:
  - latest valid `JournalEntry.id`
  - alias-first entity resolution
  - one chain-safe `JournalEntry` write
  - returning the created id plus resolved entity ids

Rules:

- Do not offload the final interpretation of the user's situation to the reader or writer.
- Prefer running the reader in parallel with local drafting when retrieval is not blocking.
- Run writer tasks serially. Never have two unresolved writer tasks race for the latest journal id.
- If subagents are unavailable, fall back to the same workflow locally.
- If the host runtime requires explicit user approval for subagents, treat this mode as the preferred plan and switch to it as soon as that approval exists.
```

Replace with:

```markdown
## Subagent Mode

Use this mode by default when the host environment allows delegated execution.
On Claude Code and Cowork, the reader/writer/entity-check subagents are
`digital-brain-reader`, `digital-brain-writer`, and `digital-brain-entity-check`
(see `../../agents/`). On Codex, use the delegation shape declared in each
skill's `agents/openai.yaml` plus the prompt templates in
`references/subagent-prompts.md`.

- Main session agent owns:
  - reading `SOUL.MD`
  - running the mandatory `BOOTSTRAP` read before the first user-facing response
  - deciding whether the turn is `SKIP`, `READ`, or `WRITE`
  - separating fact from inference
  - the final buddy-facing response
- Reader subagent owns:
  - mandatory `BOOTSTRAP` evidence pack on the first turn of a new buddy conversation
  - recent entries
  - core entities / heavy nodes
  - people map: names, ids, relations to the user, and recurring sensitive themes
  - semantic journal lookup
  - one-hop, two-hop, and shared-connections related-node traversal for evidence packs
- Entity-check subagent owns:
  - verifying whether a new/existing entity name resembling a known core entity shares real graph connections
  - returning an authorized/not-authorized merge decision, never a guess
- Writer subagent owns:
  - latest valid `JournalEntry.id`
  - alias-first entity resolution
  - one chain-safe `JournalEntry` write with `embed_text` always set
  - returning the created id plus resolved entity ids

Rules:

- Do not offload the final interpretation of the user's situation to the reader, entity-check, or writer.
- Prefer running the reader in parallel with local drafting when retrieval is not blocking.
- Before the writer runs on a new/existing entity that resembles a known core entity, run the entity-check subagent first and only authorize a merge on an "authorized" result; otherwise create a new entity.
- Run writer tasks serially. Never have two unresolved writer tasks race for the latest journal id.
- If subagents are unavailable, fall back to the same workflow locally.
- If the host runtime requires explicit user approval for subagents, treat this mode as the preferred plan and switch to it as soon as that approval exists.
```

- [x] **Step 3: Confirm the file still has the same top-level section count plus no leftover duplicate headings**

Run: `grep -n "^## " plugins/digital-brain-buddy/skills/digital-brain-buddy-session/SKILL.md`
Expected: exactly one `## Subagent Mode` line in the output (no duplicated section from a bad replace).

- [x] **Step 4: Commit**

```bash
git add plugins/digital-brain-buddy/skills/digital-brain-buddy-session/SKILL.md
git commit -m "feat: wire session skill to native reader/writer/entity-check subagents"
```

---

### Task 8: Docker compose lifecycle (SessionStart hook + manual recovery command)

**Files:**
- Create: `plugins/digital-brain-buddy/scripts/compose-up.sh`
- Create: `plugins/digital-brain-buddy/hooks/hooks.json`
- Create: `plugins/digital-brain-buddy/commands/digital-brain-up.md`

**Interfaces:**
- Produces: `scripts/compose-up.sh` (invoked by both the hook and the command), always exits `0`.

- [x] **Step 1: Create the compose bring-up script**

Create `plugins/digital-brain-buddy/scripts/compose-up.sh`:

```bash
#!/bin/bash
set -uo pipefail

echo "digital-brain-buddy: bringing up local Neo4j + MCP stack..."

if [ -z "${CLAUDE_PROJECT_DIR:-}" ]; then
  echo "digital-brain-buddy: CLAUDE_PROJECT_DIR not set, skipping compose bring-up" >&2
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR" || exit 0

if ! command -v docker >/dev/null 2>&1; then
  echo "digital-brain-buddy: docker not found, skipping local Neo4j/MCP bring-up" >&2
  exit 0
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "digital-brain-buddy: docker compose not available, skipping local Neo4j/MCP bring-up" >&2
  exit 0
fi

if ! docker compose --profile ollama up -d neo4j; then
  echo "digital-brain-buddy: failed to start neo4j, skipping mcp-cypher/mcp-memory bring-up" >&2
  exit 0
fi

echo "digital-brain-buddy: waiting for neo4j healthcheck..."
attempt=0
max_attempts=30
while true; do
  container_id="$(docker compose ps -q neo4j 2>/dev/null)"
  status="$(docker inspect -f '{{.State.Health.Status}}' "$container_id" 2>/dev/null || true)"
  if [ "$status" = "healthy" ]; then
    break
  fi
  attempt=$((attempt + 1))
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "digital-brain-buddy: neo4j did not become healthy within 60s, skipping mcp bring-up" >&2
    exit 0
  fi
  sleep 2
done

if ! docker compose --profile ollama up -d mcp-cypher mcp-memory; then
  echo "digital-brain-buddy: failed to start mcp-cypher/mcp-memory" >&2
  exit 0
fi

echo "digital-brain-buddy: local Neo4j + MCP stack is up"
exit 0
```

- [x] **Step 2: Make the script executable**

Run: `chmod +x plugins/digital-brain-buddy/scripts/compose-up.sh`

- [x] **Step 3: Create the SessionStart hook config**

Create `plugins/digital-brain-buddy/hooks/hooks.json`:

```json
{
  "description": "Bring up local Neo4j + MCP stack for the digital-brain-buddy plugin",
  "hooks": {
    "SessionStart": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/compose-up.sh",
            "timeout": 90
          }
        ]
      }
    ]
  }
}
```

- [x] **Step 4: Create the manual recovery command**

Create `plugins/digital-brain-buddy/commands/digital-brain-up.md`:

```markdown
---
name: digital-brain-up
description: Bring up (or recover) the local Neo4j + MCP stack backing the digital-brain-buddy plugin.
---

Run `${CLAUDE_PLUGIN_ROOT}/scripts/compose-up.sh` and relay its output to the
user. If it reports the stack came up healthy, confirm the plugin's graph
tools are ready to use. If it reports docker/compose is unavailable or a
service failed to become healthy, show the exact warning it printed and
suggest running `docker compose ps` from the repo root to diagnose further.
```

- [x] **Step 5: Smoke-test the script directly (without a running session)**

Run: `CLAUDE_PROJECT_DIR="$(pwd)" bash plugins/digital-brain-buddy/scripts/compose-up.sh`
Expected: either ends with `digital-brain-buddy: local Neo4j + MCP stack is up` (if Docker is running and the stack starts cleanly) or a clear `digital-brain-buddy: ...` warning line on stderr followed by exit code `0` — run `echo $?` afterward and confirm it prints `0` either way.

- [x] **Step 6: Validate the hook JSON and plugin manifest**

Run:
```bash
python3 -m json.tool plugins/digital-brain-buddy/hooks/hooks.json
claude plugin validate plugins/digital-brain-buddy --strict
```
Expected: both succeed with no errors.

- [x] **Step 7: Simplify pass**

Dispatch a `code-simplifier` agent scoped to
`plugins/digital-brain-buddy/scripts/compose-up.sh` (clarity/reuse only, no
behavior change). Re-run Step 5's smoke test after it returns and confirm
the exit code is still `0`.

- [x] **Step 8: Review pass**

Dispatch a `pr-review-toolkit:code-reviewer` agent scoped to
`plugins/digital-brain-buddy/scripts/compose-up.sh` and
`plugins/digital-brain-buddy/hooks/hooks.json`. Address any high-confidence
findings (e.g. unquoted variables, missing error paths), re-running the
Step 5 smoke test after each fix.

- [x] **Step 9: Commit**

```bash
git add plugins/digital-brain-buddy/scripts/compose-up.sh plugins/digital-brain-buddy/hooks/hooks.json plugins/digital-brain-buddy/commands/digital-brain-up.md
git commit -m "feat: add SessionStart docker-compose bring-up hook and /digital-brain-up command"
```

---

### Task 9: Subagent-driven functional verification — searches + critic panel

**Files:** none directly (verification task; fixes it triggers land back in the
relevant Task 1-8 files). This is the task that proves Tasks 1-8 actually
work together against the real local graph — do not skip it and do not
replace it with only the manual smoke checks below.

**Interfaces:**
- Consumes: `digital-brain-reader`, `digital-brain-writer`,
  `digital-brain-entity-check` (Task 6), the local marketplace (Task 3), the
  hard-reject rule (Task 1).

- [ ] **Step 1: Install the plugin from the local marketplace**

Run:
```bash
claude plugin marketplace add . --scope project
claude plugin install digital-brain-buddy@avatar-digital-brain-local
```
Expected: both commands succeed; `claude plugin marketplace list` shows `avatar-digital-brain-local`, and `claude plugin list` shows `digital-brain-buddy` installed and enabled.

- [ ] **Step 2: Start a fresh Claude Code session and check the SessionStart hook**

Run `claude` (or restart your current session) from the repo root, then check the session's transcript/output for the `digital-brain-buddy: ...` lines from `compose-up.sh`.
Expected: either `digital-brain-buddy: local Neo4j + MCP stack is up`, or a clear warning — confirm `docker compose ps` shows `neo4j`, `mcp-cypher`, `mcp-memory` running if the hook reported success. If the stack isn't up, run `/digital-brain-up` before continuing.

- [ ] **Step 3: Dispatch the reader subagent across several real searches**

Dispatch the `digital-brain-reader` subagent (via the Task tool) for each of
the following, capturing its full evidence-pack output for each:
1. A `BOOTSTRAP` request (new-conversation evidence pack: people map, top-20
   weighted nodes, node-type summary).
2. A semantic/`READ` search on a topic from `docs/LOG.md`'s probe list (e.g.
   "father/family" or "EPAM/work").
3. A related-node request for one `Person` returned by step 1's people map
   (exercises the Task 4 shared-connections query specifically).

- [ ] **Step 4: Dispatch the entity-check and writer subagents**

Dispatch `digital-brain-entity-check` with a candidate name that's an
obvious variant of a real person from step 3's people map (e.g. a nickname).
Capture its authorized/not-authorized verdict and reasoning.

Dispatch `digital-brain-writer` with one small, real, rememberable fact.
Capture the created `JournalEntry` id, then confirm directly against Neo4j:
```bash
docker compose exec neo4j cypher-shell -u neo4j -p password \
  "MATCH (j:JournalEntry) RETURN j.id, size(j.embedding) AS dims ORDER BY j.entry_date DESC, j.timestamp DESC, j.created_at DESC LIMIT 1"
```
Expected: the most recent `JournalEntry` row shows `dims = 1024`.

- [ ] **Step 5: Dispatch a 3-agent critic panel**

Dispatch 3 independent subagents (fresh context each, no shared state), all
given the same prompt: the outputs captured in Steps 3-4, plus a pointer to
`docs/superpowers/specs/2026-07-03-digital-brain-buddy-claude-code-plugin-design.md`
and the relevant `SKILL.md`/`agents/*.md` files. Ask each to adversarially
judge, independently, whether the observed behavior actually satisfies the
spec — specifically:
- did the BOOTSTRAP pack include people map + top-20 weighted nodes + node-type summary, per `digital-brain-buddy-read-memory/SKILL.md`?
- did the related-node step return results ranked by shared connections, kept separate from exact matches, per Task 4?
- did entity-check return an explicit authorized/not-authorized verdict with a real shared-connections check, never a guess?
- did the writer's `JournalEntry` write include `embed_text` and land with a 1024-dim embedding?
- did any subagent leak final buddy-voice prose to the user instead of returning data to the caller (a violation of every skill's "Do Not" section)?

Each critic returns PASS or FAIL with specific reasons per bullet.

- [ ] **Step 6: Converge — fix, simplify, re-run search + critics**

If 2 or more critics FAIL (or any critic raises a specific, concrete
correctness issue even without a majority): fix the underlying file (the
relevant `SKILL.md`, `agents/*.md`, or `runtime-patterns.md` from Tasks 4-7),
dispatch a `code-simplifier` agent on the changed file for clarity, then
repeat Steps 3-5 from scratch (new searches, new critic panel — not the same
transcripts re-judged). Cap at 3 total loop iterations. If still failing
after 3 iterations, stop and report the specific unresolved disagreement to
the human instead of continuing to loop.

Once 2 or more of 3 critics PASS, commit any fixes made during this loop:
```bash
git add plugins/digital-brain-buddy/
git commit -m "fix: address critic-panel findings from functional verification"
```
(skip the commit if no fixes were needed — the panel passed on the first pass).

- [ ] **Step 7: Exercise the manual recovery command**

Run `docker compose stop mcp-cypher`, then inside the Claude Code session run `/digital-brain-up`.
Expected: the command reports bringing `mcp-cypher` back up; `docker compose ps mcp-cypher` shows it running again, and a subsequent reader-subagent search works without restarting the session.

---

### Task 10: Manual install + verification — Claude Desktop (Cowork)

**Files:** none (manual filesystem step + verification).

This task does not repeat Task 9's critic panel — the underlying
skill/subagent behavior was already adversarially verified there. This task
only confirms the same plugin installs and the same subagents are reachable
from Cowork, and answers the open SessionStart-hook-parity question.

- [ ] **Step 1: Copy the plugin into the Cowork org-plugins directory**

Run (requires `sudo` — this is a system directory, not user-writable by default):
```bash
sudo mkdir -p "/Library/Application Support/Claude/org-plugins"
sudo cp -R plugins/digital-brain-buddy "/Library/Application Support/Claude/org-plugins/digital-brain-buddy"
```
Expected: `ls -la "/Library/Application Support/Claude/org-plugins/digital-brain-buddy"` shows the full plugin tree, including `.claude-plugin/plugin.json` and `version.json`.

- [ ] **Step 2: Enable it in the Claude desktop app**

Open the Claude desktop app → plugin browser → Organization tab.
Expected: `digital-brain-buddy` appears with the description from Task 3's manifest; enable it.

- [ ] **Step 3: Start a Cowork session and confirm the subagents are available**

Start a new Cowork session, then ask what subagents/skills the plugin provides.
Expected: `digital-brain-reader`, `digital-brain-writer`, `digital-brain-entity-check` are available, same as Task 9 Step 3.

- [ ] **Step 4: Record whether the SessionStart hook actually fires in Cowork**

Check whether `docker compose ps` shows `neo4j`/`mcp-cypher`/`mcp-memory` coming up automatically when the Cowork session starts, without you running `compose-up.sh` manually.
Expected: record the actual outcome either way (fires / doesn't fire / fires with different timing) — this was flagged in the design spec (§5) as unconfirmed. If it does **not** fire, note that as a known gap for Cowork users (they can still run `/digital-brain-up` manually, or start the stack with `docker compose up -d` themselves before opening Cowork) rather than treating it as a bug to fix in this plan.

- [ ] **Step 5: Re-run the READ/WRITE checks from Task 9 (Steps 3-4) inside the Cowork session**

Expected: same outcomes as Task 9 — graph-backed READ response with related-node evidence, and a WRITE producing a 1024-dim `embedding` property.

No commit for this task. If Step 4 reveals the hook doesn't fire in Cowork, update `docs/superpowers/specs/2026-07-03-digital-brain-buddy-claude-code-plugin-design.md`'s §5 "Open question" note with the confirmed answer (a follow-up doc edit, not a code task) so the next person doesn't have to re-discover it.
