---
name: digital-brain-up
description: Bring up (or recover) the local Neo4j + Cypher MCP stack backing the digital-brain-buddy plugin.
---

Run `${CLAUDE_PLUGIN_ROOT}/scripts/compose-up.sh` and relay its output to the
user. It first verifies that Docker Desktop has at least **6 GiB** allocated;
if not, it stops before starting services and reports the required adjustment.

The launcher resolves the checkout from `DIGITAL_BRAIN_PROJECT_DIR`, legacy
`CLAUDE_PROJECT_DIR`, the current workspace (including subdirectories), or a
validated source-checkout path above the script. A versioned plugin cache is
not accepted as a checkout; when running elsewhere, set
`DIGITAL_BRAIN_PROJECT_DIR=/path/to/avatar_digital_brain`.

The script starts neo4j + ollama, waits for both healthchecks (up to ~120s each
for a cold pull/recreate), **rebuilds** `mcp-cypher` from local sources, and
waits up to ~180s for its `/readyz` healthcheck. `/readyz` verifies Neo4j plus
a real 1024-dimension `bge-m3` embedding, so only the final “ready for writes”
message means the plugin's JournalEntry write path is usable.

Host-side `OLLAMA_BASE_URL` never configures the MCP container. The explicit
trusted container override is `MCP_OLLAMA_BASE_URL`; a host-published Ollama
must use `host.docker.internal`, never container-local `localhost`.

If it reports the stack is ready for writes, confirm the plugin's graph tools
are ready to use. If it reports Docker memory, Ollama, Neo4j, or MCP readiness
failure, show the exact warning and suggest `docker compose ps` plus
`docker compose logs <service>` from the repo root. Do not call the graph ready
and do not attempt a direct-Bolt workaround; this command never mutates graph
data or repairs journal chains.
