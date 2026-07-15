# Incident Report: MCP / Embeddings Write Path Failure

| Field | Value |
| --- | --- |
| **Date** | 2026-07-09 |
| **Severity** | High (buddy WRITE path unusable via MCP; multi-hour operator loop) |
| **Component** | `digital-brain-buddy` → `digital-brain-neo4j` MCP (`mcp-cypher`) → Ollama embeddings → Neo4j |
| **Repo** | `avatar_digital_brain` |
| **Plugin path** | `plugins/digital-brain-buddy/` |
| **Related services** | `mcp-cypher`, `ollama`, `neo4j` (Docker Compose) |
| **User impact** | Buddy session could not persist a JournalEntry through the intended MCP tool path; eventual write required a host/container bolt bypass; duplicate journal + broken `FOLLOWS` chain |

---

## 1. Summary

During a `/digital-brain-buddy-session` WRITE (a normal buddy memory append), creating a chain-safe `JournalEntry` via the plugin MCP server failed repeatedly.

The **root cause** was not Neo4j schema and not “slow Cypher.” It was a **broken Ollama URL inside the `mcp-cypher` container**: host `.env` set `OLLAMA_BASE_URL=http://localhost:11434`, Compose injected that into the container, and from inside the container `localhost` is the MCP process itself — not the Ollama service. Embedding generation failed with connection refused; JournalEntry writes hard-require embeddings, so the write path was dead.

Secondary failures amplified wall-clock damage:

1. Agent validation mistakes (`embed_text` without `$embedding` in Cypher).
2. MCP tool timeouts after long hangs (reported up to ~6000s in the host tool layer).
3. Docker Desktop had insufficient memory for the combined Neo4j + Ollama
   workload, making embedding failures/timeouts more likely after the URL was
   corrected.
4. Operator process failure: infrastructure debugging mixed into a buddy memory write, lasting ~2 hours from the user’s perspective.
5. Parallel/competing writers produced a **duplicate** JournalEntry and a forked `FOLLOWS` chain; later cleaned up.

---

## 2. Intended architecture

```
Buddy session / writer skill
        │
        ▼
digital-brain-neo4j MCP  (HTTP, plugin .mcp.json → http://localhost:8000/api/mcp/)
  tools: get_journal_chain_head | append_journal_entry
         | get_journal_append_receipt | read_neo4j_cypher
        │
        ├─► Neo4j  bolt://neo4j:7687  (from inside compose network)
        │
        └─► Ollama /api/embed  (bge-m3, 1024 dims)
              during append_journal_entry
```

### Current write contract (hard rules)

Source: `mcp_servers/cypher/src/digital_brain_mcp_cypher/query_tools.py`,
`journal.py`, `server.py`, and `embeddings.py`.

For a new journal memory:

1. Read the current chain head/version immediately before attempting the append.
2. Call `append_journal_entry` with one stable UUID `append_key`, content,
   timestamp, mood, and `expected_version`.
3. The server creates the stable `journal-{append_key}` ID, generates the
   `bge-m3` vector (1024 dimensions), serializes the `HEAD`/`FOLLOWS` update,
   and returns `created`, `replayed`, or `conflict`.
4. If a client times out, use the same key only with the receipt/replay path;
   do not issue a fresh blind write.

`write_neo4j_cypher` rejects new `JournalEntry` or `FOLLOWS` writes. If
embedding generation fails, the append fails — there is no “store text without
vector” path through MCP.

---

## 3. What broke

### 3.1 Primary: Ollama URL wrong inside `mcp-cypher`

| Layer | Value observed | Correct for that layer |
| --- | --- | --- |
| Host `.env` | `OLLAMA_BASE_URL=http://localhost:11434` | Correct for **host-side** tools talking to published port |
| Compose default (pre-fix) | `${OLLAMA_BASE_URL:-http://ollama:11434}` | Default was right, but **overridden by .env** |
| Env **inside** `mcp-cypher` container | `OLLAMA_BASE_URL=http://localhost:11434` | Wrong — should be `http://ollama:11434` (compose service DNS) |
| Ollama container | Running, model `bge-m3:latest` present | Healthy |
| Reachability | `localhost:11434` from host: works | `localhost:11434` from mcp-cypher: **connection refused** |

Error surfaced to the agent:

```text
RuntimeError: Ollama embedding request failed: <urlopen error [Errno 111] Connection refused>
```

Stack (container logs):

```text
digital_brain_mcp_cypher/embeddings.py → OllamaEmbeddingProvider.embed
→ urllib.request.urlopen(OLLAMA_BASE_URL + "/api/embed")
```

**Why this is easy to misconfigure**

- The same env var name is used for host tools and for the containerized MCP server.
- Host needs `localhost` (mapped port). Container needs the compose service hostname `ollama`.
- Interpolating host `.env` into the container silently breaks embeddings while Neo4j may still work for reads that do not embed.

### 3.2 Secondary: historical raw-Cypher write validation

Failed attempt without property wiring:

```text
JournalEntry writes that pass embed_text must set an embedding property with `$embedding`
```

The pre-hardening server correctly rejected writes that passed `embed_text` but
never consumed `$embedding` in the Cypher string. This was not the root outage,
but it cost an extra failed attempt. The current append API removes this
caller-managed embedding wiring and forbids raw JournalEntry/FOLLOWS Cypher.

### 3.3 Secondary: MCP session / tool hangs

After failures and container recreate:

- Subsequent `read_neo4j_cypher` / `write_neo4j_cypher` via the host MCP integration timed out (tool layer reported extreme timeouts).
- Direct `bolt` from inside `mcp-cypher` (Python `neo4j` driver → `bolt://neo4j:7687`) still worked once embedding was produced on the host.

Also observed earlier in session (read path):

```text
CypherTypeError: Invalid input for function 'toString()': ... got: StringArray[...]
```

Some graph properties (e.g. multi-value `name` / topic-like fields stored as string arrays) break naive `toString()` / `toLower()` in ad-hoc Cypher. That is a **query hygiene** issue, separate from embeddings, but it contributes to flaky reads.

### 3.4 Secondary: process / ops failure (human+agent)

Buddy WRITE should have been:

1. Resolve entities
2. Latest journal id
3. One write
4. Verify

What happened instead:

1. Write fail → debug Docker/Compose/Ollama
2. Edit `docker-compose.yml`, recreate containers
3. Bypass MCP with direct bolt write
4. Competing write created a second JournalEntry
5. Wall clock from user POV: **~2 hours**

Successful bolt write alone: **~59s** (after embed already computed).
Normal target for this path: **seconds**.

This is a process failure, not only an infra bug.

### 3.5 Data integrity consequence

Two JournalEntries for the same septic/Pasha episode:

| id | Role |
| --- | --- |
| `2026-07-09-pasha-septic-14k-half-or-resist` | Kept (fuller content, 1024-dim embedding) |
| `journal-2026-07-09-pasha-septic-popolam` | Duplicate (parallel writer); deleted |

Both originally `FOLLOWS` → `task9-verify-2026-07-09-plugin-e2e` (forked chain).

Cleanup (same day, later):

- Relinked `journal-2026-07-09-father-money-car-status-quo` → `FOLLOWS` kept septic entry
- `DETACH DELETE` on duplicate
- Verified single septic journal and repaired local chain segment

Note: `task9-verify-...` still has another non-septic follower (`journal-grok-plugin-test-...`) — pre-existing test fork, left untouched.

---

## 4. Timeline (compressed)

| Phase | What happened |
| --- | --- |
| Buddy WRITE start | User provided chat screenshot + context for a normal memory-worthy event |
| Attempt 1 | MCP write rejected: missing `$embedding` in Cypher |
| Attempt 2 | MCP write failed: Ollama connection refused from mcp-cypher |
| Diagnosis | Confirmed Ollama up with `bge-m3`; container env had `localhost`; compose default overridden by `.env` |
| Partial fix | `docker-compose.yml`: hardcode `OLLAMA_BASE_URL=http://ollama:11434` for `mcp-cypher`; recreate service |
| Host embed test | `POST /api/embed` on host → 1024 dims OK |
| MCP retry | Host MCP tools hung / timed out |
| Bypass write | Host embed → `docker exec mcp-cypher` Python neo4j driver → bolt write succeeded (~59s) |
| Aftermath | Duplicate journal discovered; chain cleanup performed under strict stop-rules |
| Report | This document |

---

## 5. Fix applied

### 5.1 Compose: do not leak host Ollama URL into mcp-cypher

File: `docker-compose.yml` (`mcp-cypher` service)

```yaml
# Application code keeps its OLLAMA_BASE_URL key, sourced from a compose-only
# override so host-side OLLAMA_BASE_URL cannot leak into the container.
OLLAMA_BASE_URL: "${MCP_OLLAMA_BASE_URL:-http://ollama:11434}"
```

Previously:

```yaml
OLLAMA_BASE_URL: "${OLLAMA_BASE_URL:-http://ollama:11434}"
```

Host `.env` may keep `OLLAMA_BASE_URL=http://localhost:11434` for host scripts;
that value is not interpolated into the MCP container. A trusted explicit
override uses `MCP_OLLAMA_BASE_URL=http://host.docker.internal:11434`.

On 2026-07-15 this incident class recurred because the hardcoded fix had been
replaced by the ambiguous host variable. Issue #21 restored the variable split,
added rendered-Compose regression tests, and made plugin recovery host-agnostic.

### 5.2 Operational recovery used during incident

Not the preferred long-term path, but it unblocked the write during the
incident:

1. Generate embedding on host: Ollama `bge-m3` → 1024 floats.
2. Write with Neo4j driver **inside** `mcp-cypher` (or any client on the compose network) using `bolt://neo4j:7687`.
3. Set `embedding` property explicitly on `JournalEntry`.
4. Pre-check `id` existence before create (idempotency).

This is historical only. Current operators must not use a direct-Bolt bypass
for normal journal writes; use the append/receipt API or stop and report a
readiness failure.

### 5.3 Graph cleanup

- Kept: `2026-07-09-pasha-septic-14k-half-or-resist`
- Deleted: `journal-2026-07-09-pasha-septic-popolam`
- Relinked next journal onto kept node

### 5.4 Runtime hardening added after the incident

The Compose URL correction remains in place, and the startup path now makes the
embedding dependency explicit:

1. `ollama` is a default service (not an opt-in Compose profile), and
   `mcp-cypher` waits for both Neo4j and Ollama health before it starts.
2. Neo4j is constrained to a 512 MiB initial heap, 1 GiB maximum heap, and a
   512 MiB page cache. Docker Desktop must expose at least 6 GiB to the stack,
   leaving room for Ollama and container overhead.
3. Ollama has an API healthcheck. `mcp-cypher` has `/livez` and `/readyz`; the
   latter checks Neo4j and a short real `bge-m3` embedding with exactly 1024
   dimensions. `OLLAMA_EMBEDDING_TIMEOUT_SECONDS` defaults to 20 seconds; an
   embedding timeout, DNS failure, or OOM-derived backend error keeps readiness
   non-200 rather than allowing a false “ready” state.
4. `compose-up.sh` performs the Docker-memory preflight, waits for Neo4j,
   Ollama, then MCP readiness, and stops with an actionable error if any stage
   fails. It does not restart a write blindly, bypass MCP over Bolt, or mutate
   graph data.

This is runtime containment, not historical data repair. Existing duplicate
IDs and `FOLLOWS` forks remain an audit/operator decision; startup performs no
automatic delete, relink, or chain bootstrap.

---

## 6. Verification checklist

Use after stack changes:

```bash
# 1) Container env must point at compose Ollama, not localhost
docker exec avatar_digital_brain-mcp-cypher-1 env | grep OLLAMA_BASE_URL
# expect: OLLAMA_BASE_URL=http://ollama:11434

# 2) Ollama has the model
docker exec avatar_digital_brain-ollama-1 ollama list
# expect: bge-m3

# 3) mcp-cypher is live and its end-to-end readiness probe is green
curl -fsS http://localhost:8000/livez
curl -fsS http://localhost:8000/readyz
# /readyz exercises Neo4j plus a short bge-m3 embedding and checks 1024 dims.

# 4) Minimal MCP write path smoke (via buddy writer or MCP tool):
#    - obtain the chain head and expected version
#    - append through append_journal_entry (not raw JournalEntry Cypher)
#    - read back size(j.embedding) = 1024

# 5) Optional isolated end-to-end runtime check. Requires Docker >= 6 GiB and
#    the normal Compose stack to be stopped; it uses no host ports or prod volumes.
bash scripts/run-journal-e2e.sh
```

---

## 7. Lessons and guardrails

### For runtime / repo maintainers

1. **Never share a single `OLLAMA_BASE_URL` between host and in-network containers**; use host `OLLAMA_BASE_URL` and compose-only `MCP_OLLAMA_BASE_URL`.
2. JournalEntry writes are **embedding-gated**; embedding outage = write outage. Monitor Ollama from the MCP container, not only from the host.
3. Prefer compose service DNS (`http://ollama:11434`) hardcoded or set only in the service block for `mcp-cypher`.
4. Document that `.env.example` host value is **host-only**.

### For buddy / writer agents

1. **Stop rules for WRITE**
   - Max 2–3 write attempts.
   - If two failures share the same root (Ollama refused, MCP timeout) → stop, report, ask before infra repair.
   - One technical step >2–3 minutes without progress → kill and report.
2. **Do not mix** memory write and stack repair in the same unbounded loop.
3. **Idempotency before create**
   - `MATCH` by planned `id` and/or semantic duplicate scan.
   - Never race two writers on “latest journal id” without serialization.
4. **Cypher hygiene**
   - Avoid blind `toString()` / `toLower()` on properties that may be lists; use safe coercion patterns from `runtime-patterns.md`.
5. **Always** use `get_journal_chain_head` followed by
   `append_journal_entry`; do not submit raw JournalEntry/FOLLOWS Cypher.

### For the human operator

If a buddy write takes more than a few minutes, treat it as an **incident**, not “still working.” Demand: attempt number, last error, stop.

---

## 8. Related files

| Path | Relevance |
| --- | --- |
| `plugins/digital-brain-buddy/.mcp.json` | Points at `http://localhost:8000/api/mcp/` |
| `plugins/digital-brain-buddy/skills/digital-brain-buddy-write-memory/SKILL.md` | Writer contract |
| `plugins/digital-brain-buddy/skills/digital-brain-buddy-graph-mcp/references/runtime-patterns.md` | Live Cypher/write patterns |
| `plugins/digital-brain-buddy/commands/digital-brain-up.md` | Stack bring-up |
| `plugins/digital-brain-buddy/scripts/compose-up.sh` | Rebuild/up path for neo4j+ollama+mcp-cypher |
| `mcp_servers/cypher/src/digital_brain_mcp_cypher/embeddings.py` | Ollama/HF providers |
| `mcp_servers/cypher/src/digital_brain_mcp_cypher/query_tools.py` | Journal append, receipt, and raw-write guard |
| `mcp_servers/cypher/src/digital_brain_mcp_cypher/journal.py` | Atomic chain, CAS, and idempotency implementation |
| `mcp_servers/cypher/src/digital_brain_mcp_cypher/server.py` | MCP tool entrypoints |
| `docker-compose.yml` | `mcp-cypher` env (fixed Ollama URL) |
| `.env` / `.env.example` | Host-side `OLLAMA_BASE_URL=http://localhost:11434` |

---

## 9. Open follow-ups (not done in this incident)

1. MCP host client timeout policy: fail fast (tens of seconds), not multi-thousand-second hangs.
2. Re-test full WRITE **only through MCP** after client session refresh (Bolt bypass must not remain the default).
3. Audit historical duplicate IDs and `FOLLOWS` forks, then have an operator
   explicitly select a canonical head before bootstrapping a new chain. Do not
   automate deletion or relinking as part of runtime recovery.

---

## 10. Bottom line

- **Broken piece:** embedding path from `mcp-cypher` → Ollama (`localhost` vs `ollama` hostname).
- **Why writes died:** JournalEntry creates hard-require successful embedding injection.
- **Why it felt like 2 hours:** infra debugging + MCP hangs + process without stop-rules, not Neo4j latency.
- **Fix in repo:** Compose maps the application-facing key from the isolated
  `MCP_OLLAMA_BASE_URL` (default `http://ollama:11434`), requires a healthy
  Ollama dependency, and only reports the stack ready after `/readyz` passes
  its real embedding check.
- **OOM guardrail:** Docker Desktop below 6 GiB is rejected before startup;
  Neo4j has fixed heap/page-cache limits so Ollama retains headroom.
- **Data:** startup never repairs legacy duplicates or forks; those require an
  explicit audit and an operator-selected canonical head.

If embeddings break again, check container env first:

```bash
docker exec avatar_digital_brain-mcp-cypher-1 env | grep OLLAMA
```

If it shows `localhost`, the outage will recur.
