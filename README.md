# Avatar Digital Brain

Local **graph memory** for a personal digital buddy: Neo4j journal + knowledge
graph, a hardened Cypher MCP server, optional Google ADK multi-agent code, and a
host plugin (`digital-brain-buddy`) for Claude / Codex / Grok.

This repository is intended for **local use on a machine you trust**. The MCP
and Neo4j endpoints are unauthenticated. See [SECURITY.md](SECURITY.md).

## What’s in the box

| Component | Path | Role |
| --- | --- | --- |
| Cypher MCP | `mcp_servers/cypher/` | Read/write Cypher + server-owned JournalEntry append |
| Compose stack | `docker-compose.yml` | Neo4j Enterprise, mcp-cypher, Ollama (`bge-m3`) |
| Buddy plugin | `plugins/digital-brain-buddy/` | SOUL, skills, SessionStart compose hook |
| ADK agents | `digital_brain/` | Multi-agent WRITE/READ paths (optional) |
| Schema contract | `docs/GRAPH_SCHEMA_CONTRACT.md` | Node/relationship rules |

**Plugin version (host cache):** `0.2.0` — server-owned append protocol.  
**Python package version** (`pyproject.toml`): independent scaffolding version.

## Requirements

- Docker Desktop (or compatible) with **≥ 6 GiB** RAM allocated to Docker
- Python 3.12+ (for scripts/tests)
- Neo4j **Enterprise** license for the default image (`neo4j:2026.05-enterprise`);
  operators must accept Neo4j’s license terms

## Quickstart (local stack)

```bash
cp .env.example .env
# Edit NEO4J_PASSWORD before any shared-machine use.

docker compose up -d ollama
docker compose exec ollama ollama pull bge-m3

# Builds mcp-cypher, starts deps, waits for /readyz
CLAUDE_PROJECT_DIR="$(pwd)" bash plugins/digital-brain-buddy/scripts/compose-up.sh
```

Endpoints (loopback only):

- MCP: `http://127.0.0.1:8000/api/mcp/`
- Neo4j Browser: `http://127.0.0.1:7474`
- Ollama: `http://127.0.0.1:11434`

Health: `GET http://127.0.0.1:8000/readyz` checks Neo4j **and** a real 1024-dim embedding.

### Shared harness pin (host ↔ MCP)

SessionStart / `compose-up.sh` pins a harness generation for the host session
and writes a well-known **active** pin under `$DIGITAL_BRAIN_STATE_DIR/active/`
(id only; no SOUL content). Default state dir matches XDG
(`$DIGITAL_BRAIN_STATE_DIR` or `$XDG_STATE_HOME/digital-brain` or
`~/.local/state/digital-brain`). `compose-up.sh` resolves and exports
`DIGITAL_BRAIN_STATE_DIR` before `docker compose up` so the mcp-cypher volume
mount tracks the same path the pin script writes. Dual-process emit requires
this shared state pin.

**Limitation (accepted for Milestone A):** the active pin is a single well-known
path (`active/harness_generation.{id,json}`), so only one host session’s pin is
“active” for MCP at a time (last-writer-wins). Concurrent SessionStarts
overwrite it; this does **not** provide exact concurrent multi-session MCP
attribution. Use per-session env (`DIGITAL_BRAIN_HARNESS_GENERATION_ID`) or
session pin files under `sessions/<id>/` when multiple sessions must instrument
in parallel. A session-keyed or per-request pin injection is deferred.
Host timeout paths use an in-process QualityStore recorder (not model-facing
`record_run_event`).

## JournalEntry write contract (v0.2)

Do **not** create journal chain edges with raw Cypher. Authoritative flow:

1. Mint one UUID `append_key` (reuse on retry)
2. `get_journal_chain_head` → `expected_version`
3. `append_journal_entry(...)` (server owns embedding, HEAD, FOLLOWS)
4. On timeout: `get_journal_append_receipt` → `found` | `not_found`
5. Post-append entity links: idempotent `MATCH`/`MERGE` via `write_neo4j_cypher` only

Full detail: [mcp_servers/cypher/README.md](mcp_servers/cypher/README.md) and
plugin skill `digital-brain-buddy-write-memory`.

## digital-brain-buddy plugin

Local marketplace entry: `.claude-plugin/marketplace.json` →
`digital-brain-buddy@avatar-digital-brain-local`.

See [plugins/digital-brain-buddy/README.md](plugins/digital-brain-buddy/README.md)
for install, version bumps, and host refresh. Changelog:
[plugins/digital-brain-buddy/CHANGELOG.md](plugins/digital-brain-buddy/CHANGELOG.md).

## Security (short)

- Ports bind to **127.0.0.1** by default; do not publish them to the internet.
- Default Neo4j password is for local dev only.
- Graph data and `backups/` are personal — keep them out of git (already gitignored).
- JWT / multi-user OAuth docs under `docs/architecture/` are design notes, not a shipped auth product.

Details: [SECURITY.md](SECURITY.md).

## Tests

```bash
# Unit / focused suites (repo-owned env; Python 3.12 + dev group)
uv run --group dev python -m pytest tests/ -q

# Optional isolated journal e2e (stop the normal stack first; needs ≥6 GiB Docker)
bash scripts/run-journal-e2e.sh
```

## Documentation map

| Doc | Audience |
| --- | --- |
| [mcp_servers/cypher/README.md](mcp_servers/cypher/README.md) | MCP tools, append protocol, e2e |
| [docs/GRAPH_SCHEMA_CONTRACT.md](docs/GRAPH_SCHEMA_CONTRACT.md) | Graph constitution |
| [docs/local_mcp_embeddings.md](docs/local_mcp_embeddings.md) | Embeddings / backfill |
| [plugins/digital-brain-buddy/docs/VERSIONING.md](plugins/digital-brain-buddy/docs/VERSIONING.md) | Plugin SemVer + release checklist |
| [docs/AGENT_PROMPTS.md](docs/AGENT_PROMPTS.md) | Historical MVP prompts (see header notice) |
| [docs/PRD_MULTI_AGENT_ARCHITECTURE.md](docs/PRD_MULTI_AGENT_ARCHITECTURE.md) | Historical product notes |

## License

MIT — see [LICENSE](LICENSE). Third-party containers (Neo4j Enterprise, Ollama,
etc.) remain under their own licenses.
