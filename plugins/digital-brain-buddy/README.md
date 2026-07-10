# digital-brain-buddy

Host plugin for a direct, pattern-aware **buddy session** on top of the
`avatar_digital_brain` Neo4j graph. Bundles a SOUL **template**, skills, agents,
a SessionStart compose hook, and Neo4j Cypher MCP config.

**Personal identity:** `SOUL.MD` in the plugin root is created per user (from
`assets/SOUL.template.md`) and is **gitignored**. Do not commit a personal SOUL.

**Version:** see `version.json` (currently `0.2.0`).  
**License:** MIT (same as the repository root).

## What you get

| Piece | Purpose |
| --- | --- |
| `assets/SOUL.template.md` + local `SOUL.MD` | Shipped template; per-user identity (gitignored) |
| `digital-brain-buddy-session` | Main session workflow |
| `digital-brain-buddy-read-memory` | Bounded graph reads |
| `digital-brain-buddy-write-memory` | Server-owned JournalEntry append |
| `digital-brain-buddy-graph-mcp` | Low-level Cypher / MCP patterns |
| `/digital-brain-up` + SessionStart hook | Bring up local Compose stack |

## Install (local marketplace)

From the **repository root** (this checkout is the marketplace source):

1. Register the marketplace that points at this repo’s
   `.claude-plugin/marketplace.json` (`avatar-digital-brain-local`).
2. Install / update:
   - Claude: `claude plugin update digital-brain-buddy@avatar-digital-brain-local` (or install once), then restart the host.
   - Codex: refresh marketplace + re-add so the versioned cache path updates.
   - Grok: `grok plugin update digital-brain-buddy`
3. Start the stack:
   ```bash
   CLAUDE_PROJECT_DIR="$(pwd)" bash plugins/digital-brain-buddy/scripts/compose-up.sh
   ```
   or use the `/digital-brain-up` command inside a host that loads the plugin.

MCP URL (literal, loopback): `http://localhost:8000/api/mcp/`  
Configured in `.mcp.json`. Hosts that do not expand env vars need the literal URL.

## Journal write contract (0.2.0)

Writers must **not** create JournalEntry / FOLLOWS / HEAD with raw Cypher.

1. Mint UUID `append_key`
2. `get_journal_chain_head` → `expected_version`
3. `append_journal_entry(...)`
4. Timeout? `get_journal_append_receipt` → `found` | `not_found`
5. Links only via idempotent `write_neo4j_cypher` `MATCH`/`MERGE`

Details: skill `digital-brain-buddy-write-memory` and repo
`mcp_servers/cypher/README.md`.

## Versioning

Hosts cache plugins by version. After any agent-contract change, bump
`version.json` and keep manifests in sync — see
[docs/VERSIONING.md](docs/VERSIONING.md).

Changelog: [CHANGELOG.md](CHANGELOG.md).

## Security

This plugin talks to a **local, unauthenticated** MCP server that can read and
mutate your personal graph. Run only on a trusted machine; do not expose MCP or
Neo4j ports. See the repository [SECURITY.md](../../SECURITY.md).
