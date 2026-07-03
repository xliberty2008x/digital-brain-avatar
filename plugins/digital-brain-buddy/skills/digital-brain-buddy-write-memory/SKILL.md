---
name: digital-brain-buddy-write-memory
description: Persist one buddy-memory update into the `avatar_digital_brain` graph using the repo's chain-safe JournalEntry write rules. Use when the main session agent wants a subagent to resolve entities and write memory without carrying the whole mutation workflow in the main context.
---

# Digital Brain Buddy Write Memory

Use this skill for delegated graph writes.

## Start Here

1. Read `../digital-brain-buddy-graph-mcp/references/runtime-patterns.md`.

2. Treat runtime code and live schema as the source of truth.

3. Before any write:
- fetch the latest valid `JournalEntry.id`
- resolve entities alias-first
- check live relation names if they matter

## Scope

This worker owns one bounded persistence task:

- create one `JournalEntry`
- chain-link it to the previous journal entry
- reuse existing entities when possible
- create minimal new nodes only after resolution fails
- return the created journal id and any canonical ids used

## Write Rules

- Every `JournalEntry` must include explicit `id`.
- Prefer `FOLLOWS` for chain linking.
- Every `JournalEntry` write must pass `embed_text` — the MCP server hard-rejects a `JournalEntry` create/merge with no `embed_text`, so this is mandatory, not a preference.
- Keep the query serial and deterministic.
- When the parent session is running multiple tasks, writer invocations must still be serialized.

## Output Shape

Return a compact mutation report:

- created journal id
- previous journal id used
- entities reused vs created
- any uncertainty or schema caveat

## Do Not

- Do not write more than one `JournalEntry` unless the parent explicitly asks.
- Do not answer the user in buddy voice.
- Do not run in parallel with another unresolved writer task.
