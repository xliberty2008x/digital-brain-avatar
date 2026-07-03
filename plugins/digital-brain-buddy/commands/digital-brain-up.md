---
name: digital-brain-up
description: Bring up (or recover) the local Neo4j + MCP stack backing the digital-brain-buddy plugin.
---

Run `${CLAUDE_PLUGIN_ROOT}/scripts/compose-up.sh` and relay its output to the
user. If it reports the stack came up healthy, confirm the plugin's graph
tools are ready to use. If it reports docker/compose is unavailable or a
service failed to become healthy, show the exact warning it printed and
suggest running `docker compose ps` from the repo root to diagnose further.
