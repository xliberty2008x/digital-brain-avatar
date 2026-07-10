# Initiate Protocol

Source of truth for empty/incomplete buddy first run. Session skill routes here
when `initiation_status.mode == INITIATE`.

Pure status rules also live in
`../../../scripts/initiation_status.py` (must stay aligned with this doc).

## When to run

After SOUL load and mandatory BOOTSTRAP, before the first normal buddy reply:

1. Build evidence (see below).
2. Compute status via the same rules as `compute_initiation_status` (or run
   `python3 ../../../scripts/initiation_status.py '<json>'` if helpful).
3. If `complete` is false → `INITIATE` mode for this conversation.
   INITIATE takes priority over SKIP / READ / WRITE. **FEEDBACK remains
   available** for grounded corrections/praise even during INITIATE; it still
   requires the session-pinned `harness_generation_id` and never activates
   Alias / policy / overlay / SOUL from prose or generic acks.
4. If `complete` is true → normal Routing: SKIP / READ / WRITE / FEEDBACK;
   soft hooks only if `soft_hooks_allowed`.

## Evidence → flags

| Flag | How to set true |
| --- | --- |
| `has_language` | SOUL `## User overlay` has non-empty Preferred language |
| `has_self` | Person with `relation = "self"` (or equivalent) exists |
| `has_anchor_person` | At least one non-self Person with a clear relation/role |
| `has_focus` | At least one Topic treated as current focus |
| `has_soul_overlay_beyond_language` | User overlay has at least one of: how hard to push / protect / hard boundaries filled |
| `has_receipt` | JournalEntry with `kind = "initiation_complete"` exists |
| `non_self_person_count` | Count of Person nodes without relation self |
| `topic_count` | Count of Topic nodes |

### Graph probes (read-only Cypher examples)

```cypher
// self
MATCH (p:Person)
WHERE toLower(coalesce(p.relation, '')) = 'self'
RETURN p.id AS id, p.name AS name
LIMIT 5

// receipt
MATCH (j:JournalEntry {kind: 'initiation_complete'})
RETURN j.id AS id, j.timestamp AS timestamp
ORDER BY j.timestamp DESC
LIMIT 1

// non-self people
MATCH (p:Person)
WHERE toLower(coalesce(p.relation, '')) <> 'self'
RETURN count(p) AS non_self_person_count

// topics
MATCH (t:Topic)
RETURN count(t) AS topic_count
```

## Status → next_step

| status | next_step | Agent action |
| --- | --- | --- |
| `missing_language` | `language` | Ask language once (or detect from user message); write SOUL Preferred language; then full intro in that language |
| `missing_self` | `self` | If language already set: one-line re-orient (not full intro). Ask name; create self Person |
| `missing_anchor_person` | `anchor_person` | Ask one important person + relation; create Person |
| `missing_focus` | `focus` | Ask current focus; create Topic |
| `missing_soul_overlay` | `soul_overlay` | Ask push style / protect / hard nos; edit User overlay |
| `missing_receipt` | `receipt` | Summarize; append receipt; announce normal mode |
| `complete` | null | Normal buddy |

## Opening order (first incomplete session)

1. **Language** — detect or ask; persist to SOUL immediately; all further initiate text in that language.
2. **Short intro** (2–4 sentences in that language) — buddy = personal graph memory + direct stance; brain is empty; this meeting only seeds you, one person, one focus, light stance; then normal chat.
3. **Q&A** for remaining gaps only.

On **resume** (language already set): skip full intro; one-line re-orient; next missing Q&A only.

## Writes

- Self: `Person` with `relation: "self"`, name = what user wants to be called.
- Anchor: `Person` with `relation` (partner, friend, …); link to self when schema allows.
- Focus: `Topic` with clear name; link / MENTIONS as appropriate.
- Receipt: **only** via `append_journal_entry`:
  - mint UUID `append_key`
  - `get_journal_chain_head` → `expected_version`
  - `append_journal_entry(append_key, content, timestamp, expected_version, properties={"kind": "initiation_complete"})`
  - timeout → `get_journal_append_receipt` with same key
  - then idempotent MENTIONS to self, anchor, focus
- Never create JournalEntry/FOLLOWS with raw Cypher.
- Never invent people/topics the user refused.

## Tone

Same buddy DNA: direct, compact, not therapist. One question at a time.
If user dumps multiple answers, extract all present fields and only ask for gaps.

## Soft hooks (only when complete and soft_hooks_allowed)

- At most **one** per session.
- Only on SKIP / low-stakes turns — never mid-crisis, dense WRITE, focused
  READ, or FEEDBACK turns.
- Examples: second person, second focus, stance refinement.
- Persist via normal WRITE path.

## Error handling

- MCP/graph down: do not mark complete; tell user to run `/digital-brain-up` or compose-up.
- Write failure: leave incomplete; resume re-reads graph; merge idempotently.
- User refuses a step: stay on that stage; end session incomplete if needed.
