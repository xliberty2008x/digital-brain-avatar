---
name: digital-brain-buddy-write-memory
description: Persist one buddy-memory update through the server-owned, idempotent JournalEntry append protocol.
---

# Digital Brain Buddy Write Memory

Use this skill for one bounded persistence task. Read
`../digital-brain-buddy-graph-mcp/references/runtime-patterns.md` first.

## Required workflow

1. Resolve entities alias-first and inspect live relation names when needed.
2. Mint one UUID `append_key` before the first append attempt; never replace it
   while reconciling the same memory.
3. Immediately before mutation, call `get_journal_chain_head()`.
4. Call `append_journal_entry(append_key, content, timestamp, mood,
   expected_version, properties?)` exactly once.
5. On timeout or transport uncertainty, call `get_journal_append_receipt` with
   the same key. A `created` or matching `replayed` receipt is success; a
   `conflict` creates no node and requires a fresh head read before a new
   append attempt.
6. Only after a successful append, create entity links with idempotent
   `MATCH`/`MERGE` Cypher using the returned `journal_id`. Never create a
   JournalEntry or `FOLLOWS` through `write_neo4j_cypher`.

## Output

Return a compact mutation report:

- append outcome (`created`, `replayed`, or `conflict`)
- journal id and append key
- chain version and previous journal id when supplied by the receipt
- entities reused/created and any incomplete post-append links

## Do not

- Do not create more than one JournalEntry unless explicitly asked.
- Do not use `embed_text`, raw `CREATE (:JournalEntry)`, or raw `FOLLOWS`.
- Do not mint a new append key after timeout.
- Do not run unresolved writer tasks in parallel.
- Do not answer the user in buddy voice.
