# Security Policy

## What this project is (threat model)

`avatar_digital_brain` is a **local personal memory stack**: Neo4j graph, unauthenticated
HTTP MCP tools, local embeddings (Ollama), optional multi-agent ADK code, and the
`digital-brain-buddy` host plugin.

It is **not** multi-tenant production software. Anyone who can reach the MCP or
Neo4j ports can read and mutate your graph (within Cypher guards on journal chain
fields). Treat the graph and backups as **sensitive personal data**.

## Supported local deployment

- Run only on a machine you trust.
- Docker Compose publishes Neo4j, MCP, and Ollama on **loopback** (`127.0.0.1`).
- Default Neo4j credentials (`neo4j` / `password`) are for local development only.
  Change `NEO4J_PASSWORD` in `.env` before using a shared machine.
- Do **not** port-forward, reverse-proxy, or open these services to a LAN or the
  public internet without adding authentication, TLS, and network controls
  yourself (not provided here).

## Journal write integrity (what is hardened)

The Cypher MCP server owns the JournalEntry append protocol:

- `append_journal_entry` / chain head / receipts
- Generic `write_neo4j_cypher` rejects JournalEntry/FOLLOWS/HEAD/JournalChain
  bypasses and several destructive patterns

That protects **chain integrity**, not **network access**. Local MCP clients are
still fully trusted.

## What is experimental / incomplete

- JWT helpers under `digital_brain/security/` are learning/experimental code.
  Do not rely on the default secret for any real auth.
- Docs under `docs/architecture/auth_architecture.md` and `docs/mentorship/`
  describe future multi-user designs; they are **not** shipped production auth.

## Secrets and data you must not commit

- `.env` and real credentials (gitignored)
- Neo4j dumps under `backups/` (gitignored)
- Cloud/service account JSON (covered by `*.json` ignore with narrow allowlists)
- Personal journal transcripts or agent dumps under `digital_brain/misc/`

## Reporting a vulnerability

If you find a security issue in this repository:

1. Prefer a **private** report to the repository owner (GitHub Security Advisory
   if enabled, or the contact on the GitHub profile/org).
2. Do not open a public issue that includes personal graph data, credentials, or
   exploit PoCs against live deployments.
3. We will acknowledge reports that affect local data exposure, Cypher guards,
   or dependency supply chain when we can.

## Third-party licenses

Neo4j Enterprise images require a valid Neo4j license agreement. This project
sets `NEO4J_ACCEPT_LICENSE_AGREEMENT=yes` for local Compose only; operators are
responsible for compliance.
