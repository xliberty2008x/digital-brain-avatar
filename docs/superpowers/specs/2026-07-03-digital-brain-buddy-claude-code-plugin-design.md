# Design: Port digital-brain-buddy to Claude Code, adapt to local Neo4j

Date: 2026-07-03
Status: Approved (pending spec review sign-off)

## Problem

`plugins/digital-brain-buddy/` is a Codex-only plugin (`.codex-plugin/plugin.json`
manifest, per-skill `agents/openai.yaml`). It gives Codex a "buddy" persona backed
by the `avatar_digital_brain` Neo4j graph. Two things are stale or missing:

1. Its `.mcp.json` still points at a decommissioned Cloud Run MCP endpoint. The
   rest of the repo (`digital_brain/config.py`, `.env.example`,
   `docker-compose.yml`) already migrated to a local Docker Neo4j + local MCP
   servers (`mcp-cypher` on `:8000`, `mcp-memory` on `:8001`).
2. The plugin only runs in Codex. There is no Claude Code plugin in this repo.

Separately, `digital_brain/` (the standalone Google ADK multi-agent app with a
router → entity-extractor → retriever → writer → executor → response pipeline)
already has proven patterns the plugin's prose-only skills don't yet use:
shared-connections duplicate detection in `agents/retriever.py`, a deterministic
chain-link guard (`callbacks/journal_chain_guard.py`), and an
embedding-usage check (`mcp_servers/cypher/.../query_tools.py`) that is
currently optional, not enforced.

## Goals

- Add a Claude Code plugin manifest that reuses the existing skill/persona
  content without duplicating it (dual-host, shared content).
- Fix the plugin's MCP wiring to point at the local Neo4j/MCP stack.
- Improve the read skill's related-node discovery by porting the ADK
  retriever's shared-connections technique into the plugin's documented
  Cypher templates.
- Make embeddings mandatory (not optional) for `JournalEntry` writes, enforced
  at the MCP server so every caller is protected, not just this plugin.
- Give Claude Code real, invokable subagents (reader / writer / entity-check)
  instead of prose-only "delegate when possible" instructions.
- Auto-start the local Docker stack (`neo4j`, `mcp-cypher`, `mcp-memory`) when
  a Claude Code session starts in this repo with the plugin enabled, plus a
  manual command to recover if a container crashes mid-session.

## Non-goals

- No changes to the Codex-side manifest, `.codex-plugin/plugin.json`, or the
  per-skill `agents/openai.yaml` files. Codex keeps working exactly as today.
- No changes to the ADK app's own orchestration (`digital_brain/agent.py` and
  friends) beyond the shared MCP server validation fix in §4, which the ADK
  app also benefits from incidentally.
- No new embedding models, providers, or index changes — `bge-m3` /
  1024-dimensional local embeddings stay as configured today.
- No attempt to make the plugin work without the local Docker stack running;
  offline/remote fallback is out of scope.

## Architecture: dual-host, shared content

```
plugins/digital-brain-buddy/
├── .codex-plugin/plugin.json        # existing, untouched
├── .claude-plugin/plugin.json       # NEW — Claude Code manifest
├── SOUL.MD                          # shared, untouched
├── .mcp.json                        # shared — URL fixed to local endpoint
├── skills/                          # shared SKILL.md content, both hosts read these
│   ├── digital-brain-buddy-session/
│   ├── digital-brain-buddy-read-memory/
│   ├── digital-brain-buddy-write-memory/
│   ├── digital-brain-buddy-graph-mcp/
│   │   └── references/runtime-patterns.md   # gets new related-node template
│   └── digital-brain-buddy-identity-bootstrap/
├── agents/                          # NEW — Claude-Code-only native subagents
│   ├── digital-brain-reader.md
│   ├── digital-brain-writer.md
│   └── digital-brain-entity-check.md
├── hooks/hooks.json                 # NEW — SessionStart docker compose bring-up
├── commands/digital-brain-up.md     # NEW — manual restart command
└── scripts/
    ├── init_soul.py                 # existing, untouched
    └── compose-up.sh                # NEW — used by hooks.json and the command
```

Each host manifest (`.codex-plugin/plugin.json` for Codex,
`.claude-plugin/plugin.json` for Claude Code) points at the same
`./skills/`, `./.mcp.json`, and (Claude Code only) `./agents/`, `./hooks/`,
`./commands/` at plugin root. Claude Code's plugin format supports this
directly — component directories load from plugin root regardless of which
manifest enabled the plugin. Codex's `agents/openai.yaml` files stay nested
inside each skill directory exactly as today; the new root-level `agents/*.md`
is a distinct, additive mechanism that Codex does not read.

## Components

### 1. `.mcp.json` — local endpoint

Replace the hardcoded Cloud Run URL with the same env-driven local default the
rest of the repo uses:

```json
{
  "mcpServers": {
    "digital-brain-neo4j": {
      "type": "http",
      "url": "${DIGITAL_BRAIN_MCP_URL:-http://localhost:8000/api/mcp/}"
    }
  }
}
```

This mirrors `digital_brain/config.py`'s `DEFAULT_LOCAL_MCP_URL` /
`DIGITAL_BRAIN_MCP_URL` env var convention.

### 2. Read skill — related-node discovery

Add one new Cypher template to
`skills/digital-brain-buddy-graph-mcp/references/runtime-patterns.md`,
ported from `digital_brain/agents/retriever.py`'s duplicate-verification
query:

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

`digital-brain-buddy-read-memory/SKILL.md` and
`digital-brain-buddy-session/SKILL.md` get a new instruction: for `BOOTSTRAP`
and `READ` tasks involving a matched entity, run this related-node query in
addition to one/two-hop traversal, and surface "related" nodes (ranked by
shared connections) separately from exact matches in the evidence pack.

### 3. Write skill + MCP server hard-reject on missing embeddings

**`mcp_servers/cypher/src/digital_brain_mcp_cypher/query_tools.py`** —
`validate_embedding_usage` currently only checks *if* `embed_text` was passed:

```python
def validate_embedding_usage(query: str, embed_text: str | None) -> None:
    if not embed_text:
        return
    if JOURNAL_WRITE_RE.search(query or "") and not EMBEDDING_PARAM_RE.search(query or ""):
        raise ValueError(...)
```

Change it to also reject a `JournalEntry` create/merge that has **no**
`embed_text` at all:

```python
def validate_embedding_usage(query: str, embed_text: str | None) -> None:
    if JOURNAL_WRITE_RE.search(query or ""):
        if not embed_text:
            raise ValueError(
                "JournalEntry writes must pass embed_text so the entry gets an embedding"
            )
        if not EMBEDDING_PARAM_RE.search(query or ""):
            raise ValueError(
                "JournalEntry writes that pass embed_text must set an embedding property with `$embedding`"
            )
```

This lives in shared infra (`mcp_servers/cypher`), so it protects every
caller — this plugin, the ADK app's `executor_agent`, and anything else
talking to `write_neo4j_cypher` — not just what an LLM remembers to do from
skill prose.

**`skills/digital-brain-buddy-write-memory/SKILL.md`** — update "Write Rules"
to state embeddings are mandatory (not "prefer" or "when the entry should
participate in vector search"), matching the new hard-reject behavior, so the
model doesn't need an error round-trip to learn this.

### 4. Native Claude Code subagents

Three new files under `plugins/digital-brain-buddy/agents/`, each a thin
wrapper that points at the corresponding shared skill and narrows scope for
direct Task-tool invocation:

- **`digital-brain-reader.md`** — scope matches
  `digital-brain-buddy-read-memory`: bootstrap pack, recent entries, semantic
  search, related-node discovery (§2). Read-only, never mutates the graph.
- **`digital-brain-writer.md`** — scope matches
  `digital-brain-buddy-write-memory`: chain-safe write, alias-first
  resolution, embeddings mandatory (§3). Never runs two writer invocations
  concurrently.
- **`digital-brain-entity-check.md`** — new, narrow scope ported from the ADK
  retriever's duplicate-verification step: given a candidate name and a core
  entity it resembles, run the shared-connections query (§2) and return
  whether a merge is safe to authorize. Read-only.

`digital-brain-buddy-session/SKILL.md`'s "Subagent Mode" section is updated
for the Claude Code path: instead of "delegate when the host allows it," it
names the concrete subagents (`digital-brain-reader`, `digital-brain-writer`,
`digital-brain-entity-check`) and says to call `digital-brain-entity-check`
before the writer whenever a new/existing entity name resembles a known core
entity. Codex's own delegation shape (declared via each skill's
`agents/openai.yaml`) is unchanged.

### 5. Docker compose lifecycle

**`scripts/compose-up.sh`** (new): `cd` to `$CLAUDE_PROJECT_DIR` (the repo
root, provided by Claude Code to every hook command — avoids guessing the
plugin's install-time relative depth), then:

```bash
docker compose --profile ollama up -d neo4j
# wait for the neo4j healthcheck (poll `docker compose ps --format json` or
# `docker inspect` health status, timeout after ~60s)
docker compose --profile ollama up -d mcp-cypher mcp-memory
```

Does **not** run `ollama pull bge-m3` — that's one-time model provisioning,
not a per-session concern. If `docker` is missing or compose fails, the
script exits non-zero with a message on stderr; the hook treats this as
non-fatal.

**`hooks/hooks.json`**:

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

A failing/timed-out hook must not block session start — `compose-up.sh`
always exits 0 for "docker not available" or "compose failed after retries"
cases, emitting a warning on stdout so it surfaces in the transcript instead
of blocking.

**`commands/digital-brain-up.md`** (`/digital-brain-up`): manual re-run of
the same `compose-up.sh`, for recovering from a mid-session container crash
without restarting the whole Claude Code session.

## Data flow (Claude Code path)

1. Session starts in this repo → `SessionStart` hook runs `compose-up.sh` →
   local Neo4j + MCP servers are up (or a warning is logged).
2. User talks to the buddy → `digital-brain-buddy-session` skill activates →
   classifies the turn as `SKIP` / `READ` / `WRITE`.
3. New conversation → session skill invokes `digital-brain-reader` for the
   mandatory `BOOTSTRAP` pack (people map, top-weighted nodes, related-node
   expansion) via the local `.mcp.json` endpoint.
4. `READ` turn → `digital-brain-reader` again, scoped to the turn.
5. `WRITE` turn with an ambiguous entity → session skill invokes
   `digital-brain-entity-check` first; if authorized, entity ids are reused.
   Then `digital-brain-writer` runs the chain-safe write with `embed_text`
   always set — the MCP server now hard-rejects the write otherwise.
6. Session skill composes the final buddy-voice response from the
   reader/writer/entity-check outputs; subagents never emit user-facing prose.

## Error handling

- **Docker/compose unavailable**: SessionStart hook warns, doesn't block;
  skills that depend on the MCP connection will get a normal connection error
  from the MCP client when actually invoked, which the session skill should
  surface as "graph memory unavailable" rather than crashing.
- **Missing `embed_text` on a JournalEntry write**: MCP server raises
  `ValueError` before touching Neo4j (fixed in §3) — caught the same way
  today's `$embedding`-consistency error is caught by any caller.
- **Ambiguous entity merge**: `digital-brain-entity-check` returning "not
  authorized" must not block the write — the writer creates a new entity
  instead of guessing, same fallback the ADK retriever uses.
- **Concurrent writer invocations**: unchanged existing rule — writer tasks
  must be serialized; this is a prose rule enforced by the session skill's
  orchestration, not new code.

## Testing

- `tests/test_local_mcp_query_tools.py`: new test —
  `validate_embedding_usage` must raise when a `JournalEntry` create/merge
  query has `embed_text=None`, must still pass for non-`JournalEntry` writes
  with no `embed_text`, and must still enforce the existing
  `$embedding`-param-present check when `embed_text` is provided.
- Manual verification (Claude Code, local):
  1. Add/enable the plugin locally, confirm `.claude-plugin/plugin.json`
     loads without errors.
  2. Start a fresh session in this repo; confirm the SessionStart hook brings
     up `neo4j`, `mcp-cypher`, `mcp-memory` (or logs a clear warning if
     Docker isn't running).
  3. Run `/plugin` → confirm `digital-brain-reader`, `digital-brain-writer`,
     `digital-brain-entity-check` are listed as available subagents.
  4. Run one `READ` turn and confirm related-node results appear for a known
     person/topic.
  5. Run one `WRITE` turn and confirm the created `JournalEntry` has a
     1024-dim `embedding` property in Neo4j.
  6. Attempt a `write_neo4j_cypher` call for a `JournalEntry` with no
     `embed_text` directly (e.g. via a scratch script) and confirm it is
     rejected.
  7. Kill `mcp-cypher` mid-session, run `/digital-brain-up`, confirm it comes
     back without restarting Claude Code.
