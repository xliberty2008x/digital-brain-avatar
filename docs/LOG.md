# Delivery Log

## 2026-07-09

### Task 9 functional verification — digital-brain-buddy Claude Code port

What was done:

- Confirmed Tasks 1–8 artifacts are present and plugin is installed (`digital-brain-buddy@avatar-digital-brain-local` v0.1.0, enabled).
- Smoked `plugins/digital-brain-buddy/scripts/compose-up.sh` successfully against the local stack.
- Exercised live graph paths that the native subagents encode:
  - BOOTSTRAP-style people map, top-weighted nodes, node-type weight summary, recent journals
  - Semantic journal vector search (`father/family`) via `embed_text` + `journal_entry_embedding_index`
  - Shared-connections related-node query for `Отец` and `Іра`
  - Entity-check shape for nickname `Ірочка` → authorize merge into `person-ira-wife` as an obvious variant
- Found and fixed a runtime gap: the **running** `mcp-cypher` image still had the pre-Task-1 validator (`if not embed_text: return`). Rebuilt/recreated `mcp-cypher` so live hard-reject matches repo source + unit tests.
- After rebuild:
  - JournalEntry write without `embed_text` raises hard-reject
  - Chain-safe write `task9-verify-2026-07-09-plugin-e2e` created with `dims=1024` and `FOLLOWS` chain to previous entry
  - Deleted accidental pre-rebuild node `should-fail-no-embed`

Critic-panel verdict (evidence-based, 3 lenses; all PASS after rebuild):

1. **Spec fidelity:** BOOTSTRAP pack shape, related-nodes ranked separately, entity-check authorized-with-reason for nickname, writer mandatory embedding — match design/skills.
2. **Safety:** Hard-reject now enforced at live MCP after image rebuild; unit tests still 10/10 green.
3. **Ops:** SessionStart/`compose-up.sh` bring-up works; stack healthy. Gap: deploy discipline — code commits alone do not update the running MCP container until `docker compose build/up mcp-cypher`.

Remaining:

- Task 9 optional recovery smoke (`docker compose stop mcp-cypher` + `/digital-brain-up`) not re-run in this pass.
- Task 10 (Cowork org-plugins install) still not done.
- Host MCP clients that cached the old container process may need reconnect after rebuild.

## 2026-07-02

### AuraDB restored into local Neo4j

What was done:

- Moved Aura snapshot `neo4j-2026-07-02T09-38-51-1a1e5411.backup` into ignored `backups/aura-export/`.
- Created pre-migration local dumps in `backups/neo4j/pre-aura-migration-20260702T094436Z/`.
- Switched local Neo4j from Community 5.26 to `neo4j:2026.05-enterprise` because the Aura backup uses block format and kernel version 29.
- Restored the Aura backup into the persistent local `avatar_digital_brain_neo4j-data` volume.
- Updated active `.env` to target local Neo4j/MCP and remove Aura from the active runtime configuration.
- Rebuilt `mcp-cypher`, forced container-internal Ollama access through `http://ollama:11434`, and normalized local embeddings to 1024 dimensions.

Current state:

- Local Docker services are running: Neo4j Enterprise 2026.05, `mcp-cypher`, `mcp-memory`, and Ollama.
- Restored graph contains 4,146 nodes and 2,564 relationships.
- Key label counts include 268 `JournalEntry`, 130 `Article`, 350 `Topic`, 54 `Person`, 25 `Organization`, and 2,831 `State` nodes.
- `journal_entry_embedding_index` and `article_embedding_idx` are online with 1024 dimensions.
- Aura should be retained/paused as rollback safety, but it is no longer the active runtime target.

What verified it:

- Aura backup was readable with `neo4j-admin database load --info`.
- Local restore completed with `neo4j:2026.05-enterprise`; offline consistency check completed with restore-related dirty-index warnings only.
- `scripts/full_embedding_backfill.py` created `backups/neo4j/pre-embedding-backfill-20260702T095114Z/`, recreated indexes, and ran focused regression tests.
- Manual re-backfill confirmed `JournalEntry` embeddings are 1024-dimensional for 260 searchable entries and `Article` embeddings are 1024-dimensional for all 130 articles.
- `scripts/probe_embedding_quality.py --limit 5` returns meaningful results for father/family, EPAM/work, swimming, Digital Brain, and AI dependency probes.

### Local MCP and embedding migration checkpoint

What was done:

- Deleted the old Google Cloud Run MCP services: `mcp-neo4j-cypher` and `mcp-neo4j-memory`.
- Added a local Docker Compose runtime for Neo4j, Cypher MCP, memory MCP, and optional Ollama embeddings.
- Switched MCP defaults to the local endpoint at `http://localhost:8000/api/mcp/`.
- Added local embedding support with `bge-m3` through Ollama as the default 1024-dimensional model.
- Added backup/backfill orchestration in `scripts/full_embedding_backfill.py`.

Current state:

- Local Docker services are running: Neo4j, `mcp-cypher`, `mcp-memory`, and Ollama.
- Neo4j is healthy and has `journal_entry_embedding_index` online with 1024 dimensions.
- The local Neo4j graph currently has 0 nodes, so no real embedding backfill has run yet.
- `bge-m3` is installed in the local Ollama volume and smoke-tested through MCP.
- Neo4j AuraDB may still exist as a free-tier database, but it is not the active runtime target after the Cloud Run MCP deletion.

What verified it:

- MCP read/write smoke created a temporary `JournalEntry`, stored a 1024-dimensional embedding, found it via vector search, and deleted it.
- `scripts/full_embedding_backfill.py` was run and correctly skipped backup/backfill because the local graph is empty.
- Focused regression tests passed for MCP config, MCP client, local Cypher MCP helpers, historical import, journal guard, and full backfill orchestration.

Remaining gap:

- Real graph data still needs to be imported/restored into local Neo4j.
- After import, run `scripts/full_embedding_backfill.py` to create an offline dump, recreate the vector index, regenerate embeddings, and run semantic probes.
- Remaining external Neo4j Aura resources and stored credentials should be reviewed separately if the cloud path is fully retired, or exported/imported if AuraDB is the source of truth.

## 2026-03-09

### Governance baseline for dual deployment planning

What was done:

- Added a structured feature registry for dual-mode platform planning.
- Added a repository-level test strategy for local and GCP delivery tracks.
- Added deployment planning docs and ADRs to separate architecture decisions from implementation work.

Why it was needed:

- The repository had architectural notes, but it did not yet have a canonical planning surface for delivery governance.
- Local bootstrap goals and GCP bootstrap goals needed to be tracked as separate but related outcomes.

What verified it:

- Manual repository inspection confirmed the presence of policy and architecture notes, but the absence of a feature registry, execution log, and test policy.

Remaining gap:

- No implementation has been added yet.
- The missing Cypher MCP topology remains the main planning blocker before deployment work starts.
