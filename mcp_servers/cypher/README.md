# Digital Brain Cypher MCP

Local MCP server for Digital Brain Neo4j access. It exposes general read tools
alongside a serialized, idempotent JournalEntry append path:

- `get_neo4j_schema`
- `read_neo4j_cypher`
- `write_neo4j_cypher`
- `get_journal_chain_head`
- `bootstrap_journal_chain`
- `append_journal_entry`
- `get_journal_append_receipt`

`write_neo4j_cypher` remains available for non-journal graph mutations, but it
rejects Cypher that creates or merges `JournalEntry` nodes or `FOLLOWS`
relationships. Normal writers must use the append API below.

## JournalEntry append contract

1. Generate one UUID `append_key` before the first request and retain it for
   every retry of that logical entry.
2. Read `get_journal_chain_head` immediately before writing; pass its `version`
   to `append_journal_entry` as `expected_version`.
3. `append_journal_entry` creates the stable `journal-{append_key}` ID,
   generates the embedding, and atomically advances `(:JournalChain
   {key: "primary"})-[:HEAD]->(:JournalEntry)` plus its single `FOLLOWS`
   relation.
4. A request returns `created`, `replayed` (same key and request fingerprint),
   or `conflict` (stale version or mismatched reuse of a key). A timeout must be
   resolved by `get_journal_append_receipt` or a replay using the same key —
   never by issuing a new append key.

Appending before an operator bootstraps the selected legacy head returns
`chain_uninitialized`; it does not guess, repair, or rewrite an old chain.
Run `scripts/audit_journal_integrity.py`, review a candidate, then invoke
`scripts/bootstrap_journal_chain.py --head-element-id <element-id> --apply`.
The script calls the dedicated bootstrap tool; generic Cypher cannot access
`JournalChain`, `HEAD`, `FOLLOWS`, or protected JournalEntry fields.

## Health endpoints

- `GET /livez` confirms that the MCP process is running.
- `GET /readyz` confirms Neo4j connectivity and generates a short real
  `bge-m3` embedding with exactly 1024 dimensions. Dependency failures return
  HTTP 503 with a safe, bounded reason rather than reporting a false ready
  state.

Docker Compose uses `/readyz` for the `mcp-cypher` healthcheck, so a healthy
container is ready for JournalEntry writes rather than merely listening on a
port.

## Embedding Providers

Use `EMBEDDING_PROVIDER=ollama` for Ollama's `/api/embed` endpoint, or
`EMBEDDING_PROVIDER=huggingface` for local `sentence-transformers`.

The supported Compose runtime is Ollama with `bge-m3` and 1024 dimensions.
Allocate Docker Desktop at least **6 GiB** of memory; Compose caps Neo4j at a
512 MiB initial heap, 1 GiB maximum heap, and a 512 MiB page cache so Ollama
has headroom. `OLLAMA_EMBEDDING_TIMEOUT_SECONDS` defaults to 20 seconds and
can be raised deliberately for unusually slow local hardware.

For first-time model provisioning:

```bash
docker compose up -d ollama
docker compose exec ollama ollama pull bge-m3
CLAUDE_PROJECT_DIR="$(pwd)" bash plugins/digital-brain-buddy/scripts/compose-up.sh
```

`OLLAMA_BASE_URL=http://localhost:11434` in `.env.example` is for host-side
tools only. `mcp-cypher` always uses `http://ollama:11434` inside the Compose
network. The startup script reports success only after `/readyz` passes; it
does not mutate graph data or repair legacy journal forks.

## Isolated Docker E2E

Run the opt-in smoke only when the normal Compose stack is stopped and Docker
Desktop has at least 6 GiB allocated:

```bash
bash scripts/run-journal-e2e.sh
```

It uses `docker-compose.journal-e2e.yml`: no host ports, separate disposable
volumes, a fresh `bge-m3` pull, and an MCP-only bootstrap. The smoke verifies
two appends, a 1024-dimension vector, exactly one `HEAD`/current `FOLLOWS`,
replay without a duplicate, and one CAS conflict from concurrent appends.
Volumes are removed on exit; set `JOURNAL_E2E_KEEP_ARTIFACTS=1` to retain them
for diagnosis.
