---
name: digital-brain-up
description: Bring up (or recover) the local Neo4j + Cypher MCP stack backing the digital-brain-buddy plugin.
---

Run `${CLAUDE_PLUGIN_ROOT}/scripts/compose-up.sh` and relay its output to the
user. That script starts neo4j + ollama, waits for neo4j healthy (up to ~120s
for cold pull/recreate), **rebuilds** `mcp-cypher` from local sources, then
brings the container up — same path as the SessionStart hook.

If it reports the stack came up healthy, confirm the plugin's graph tools are
ready to use. If it reports docker/compose is unavailable or neo4j/mcp failed,
show the exact warning it printed and suggest running `docker compose ps`
from the repo root (or re-run this command after neo4j finishes starting).
