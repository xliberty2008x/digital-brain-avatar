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
   the same key. Receipt outcomes are only:
   - `found` — entry exists for this key (success; use its `journal_id`)
   - `not_found` — safe to retry the **same** append payload and key once
   Append tool outcomes (`created` / `replayed` / `conflict`) are separate.
6. Conflict recovery for `append_journal_entry`:
   - `stale_version` / `chain_changed`: re-read head; retry **same** key +
     same content/timestamp/mood/properties with the new `expected_version`.
     `journal_id` is null on these conflicts; do not post-link using the head.
   - `append_key_reused`: stop; only mint a new key for a truly different entry.
7. Only after append `created`/`replayed` (or receipt `found`), create entity
   links with idempotent `MATCH`/`MERGE` Cypher using that `journal_id`. Never
   create a JournalEntry or `FOLLOWS` through `write_neo4j_cypher`.

## Output

Return a compact mutation report:

- append outcome (`created`, `replayed`, or `conflict`) or receipt (`found` /
  `not_found`)
- journal id and append key
- chain version and previous journal id when supplied
- entities reused/created and any incomplete post-append links

## Do not

- Do not create more than one JournalEntry unless explicitly asked.
- Do not use `embed_text`, raw `CREATE (:JournalEntry)`, or raw `FOLLOWS`.
- Do not mint a new append key after timeout.
- Do not treat receipt `found` as `created`/`replayed` vocabulary.
- Do not run unresolved writer tasks in parallel.
- Do not create, activate, or revoke Alias / EntityProtection via generic Cypher.
- Do not handle FEEDBACK / `claim_false` as a journal write; FEEDBACK is
  evidence + propose-only on the parent session route.
- Do not apply maintenance proposals, overlays, policy, SOUL, or code changes;
  writer scope is one JournalEntry append path only. Maintenance is
  `digital-brain-buddy-maintenance` (report-only) + operator scripts.
- Do not answer the user in buddy voice.
