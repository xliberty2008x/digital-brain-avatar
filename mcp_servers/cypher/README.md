# Digital Brain Cypher MCP

Local MCP server for Digital Brain Neo4j access. It exposes general read tools
alongside a serialized, idempotent JournalEntry append path and typed quality
sensor recorders:

- `get_neo4j_schema`
- `read_neo4j_cypher`
- `write_neo4j_cypher`
- `get_journal_chain_head`
- `bootstrap_journal_chain`
- `append_journal_entry`
- `get_journal_append_receipt`
- `get_quality_receipt`
- `get_harness_generation` / `record_harness_generation`
- `create_feedback` / `revoke_feedback`
- `record_run_event` (model-facing; `outcome_source` forced to `model_advisory`)

`write_neo4j_cypher` remains available for idempotent post-append graph links
(`MATCH`/`MERGE` only). It rejects JournalEntry/FOLLOWS/HEAD/JournalChain
creation, DELETE/DETACH/REMOVE, full node replacement (`SET n = {...}`),
mutation of protected journal/chain fields, and protected quality/control
labels (`Operational`, `Feedback`, `RunEvent`, …). Normal writers must use the
append API below for the journal core and typed quality tools for sensors.

## JournalEntry append contract

1. Generate one UUID `append_key` before the first request and retain it for
   every retry of that logical entry.
2. Read `get_journal_chain_head` immediately before writing; pass its `version`
   to `append_journal_entry` as `expected_version`. Head outcomes are `ok`,
   `chain_uninitialized`, or `chain_invalid`.
3. `append_journal_entry` creates the stable `journal-{append_key}` ID and
   embedding (embedding runs **outside** the Neo4j write lock). It then
   atomically advances `(:JournalChain {key: "primary"})-[:HEAD]->(:JournalEntry)`.
   The first entry on an empty chain is HEAD-only; later appends also create
   `FOLLOWS` to the previous head.
4. Append outcomes:
   - `created` / `replayed` — success; use returned `journal_id` for post-append links
   - `conflict` with `reason`:
     - `stale_version` / `chain_changed` — no node for this attempt; re-read head
       and retry **same** key + same payload (`journal_id` is null;
       `current_head_journal_id` is the live head)
     - `append_key_reused` — same key, different fingerprint; mint a new key only
       for a truly new entry
   - `chain_uninitialized` / `chain_invalid` — operator action required
5. Timeout reconciliation uses `get_journal_append_receipt(append_key)`, which
   returns only `found` or `not_found` (not `created`/`replayed`). Never issue a
   new append key for the same logical entry after a timeout.

Appending before an operator bootstraps the selected legacy head returns
`chain_uninitialized`; it does not guess, repair, or rewrite an old chain.
Run `scripts/audit_journal_integrity.py`, review a candidate, then invoke
`scripts/bootstrap_journal_chain.py --head-element-id <element-id> --apply`.
The script calls the dedicated bootstrap tool; generic Cypher cannot access
`JournalChain`, `HEAD`, `FOLLOWS`, or protected JournalEntry fields.

## Quality sensors (Feedback / RunEvent)

Typed quality writes use the quality Neo4j credential (not the model-facing
runtime role). Sensors never create `JournalEntry` nodes or enter the journal
vector index.

1. Pin a session harness generation (`record_harness_generation` / host pin
   script) and pass that `harness_generation_id` on every sensor. Host pin also
   writes `$DIGITAL_BRAIN_STATE_DIR/active/harness_generation.{id,json}` (id
   only). Compose mounts `DIGITAL_BRAIN_STATE_DIR` into this container so MCP
   instrumentation can resolve the pin without host-only env injection.
2. Mint one stable client id per logical Feedback / RunEvent / lifecycle event
   and retain it for retries of that logical write.
3. Outcomes: `created` / `replayed` (same id + fingerprint) / `conflict`
   (same id, different fingerprint). On transport timeout, call
   `get_quality_receipt(id)` — never blind-retry a changed payload.
4. `create_feedback` stores optional raw text on a separate
   `Operational:QualityPayload` node (`raw_payload_ref`); immutable metadata and
   `request_fingerprint` remain after redaction removes the payload.
5. Model-facing `record_run_event` always stores `outcome_source=model_advisory`.
   Deterministic MCP/host/user tool outcomes use the in-process trusted
   recorder (`QualityStore.record_deterministic_run_event`) — not model prose.
   Host `mcp_client` timeouts use the same QualityStore path by default when
   Neo4j quality credentials are configured.

### `create_feedback` contract (thin hosts)

Required exact kwargs: `id`, `kind`
(`entity_wrong|claim_false|miss|invent|praise`), `sensitivity`
(`public_ops|personal|intimate`), `harness_generation_id`. Optional:
`redacted_summary`, `raw_payload`, `source_turn_ref`, … Forbidden aliases
(`summary` / `detail` / `payload` / `note` / …) raise an agent-actionable
error that remaps to `redacted_summary` / `raw_payload` and lists enums.

Prefer the typed helper `digital_brain.tools.mcp_client.create_feedback` over
free-form MCP `tools/call` when the host cannot open a Streamable HTTP
session for `tools/list`.

Session-less schema (no MCP session headers, no dual Accept):

```bash
curl -sS http://localhost:8000/tool-schemas/create_feedback
```

Do **not** use `append_journal_entry` as the FEEDBACK primary path; journal is
life memory, not the quality plane.

## Health endpoints

- `GET /livez` confirms that the MCP process is running.
- `GET /readyz` confirms Neo4j connectivity and generates a short real
  `bge-m3` embedding with exactly 1024 dimensions. Dependency failures return
  HTTP 503 with a safe, bounded reason rather than reporting a false ready
  state.
- `GET /tool-schemas/create_feedback` returns the quality-plane Feedback write
  contract (required fields, enums, alias remaps) without an MCP session.

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
bash plugins/digital-brain-buddy/scripts/compose-up.sh
```

`OLLAMA_BASE_URL=http://localhost:11434` in `.env.example` is for host-side
tools only. `mcp-cypher` defaults to `http://ollama:11434` inside the Compose
network. Its explicit trusted override is `MCP_OLLAMA_BASE_URL` (for example,
`http://host.docker.internal:11434` for host Ollama); container-local
`localhost`, `127.0.0.1`, and `::1` are invalid. The startup script reports
success only after `/readyz` passes; it does not mutate graph data or repair
legacy journal forks.

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
