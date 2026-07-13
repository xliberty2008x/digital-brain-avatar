# Canonical Subagent Prompts

Use these prompt shapes whenever delegated execution is available in the host environment.

The goal is consistency:

- main session agent keeps judgment and user-facing prose
- reader worker gathers evidence
- entity-check worker verifies whether a candidate entity is a duplicate before a write
- writer worker persists one bounded memory update
- FEEDBACK evidence and review cards stay with the parent session agent
  (subagents never mint ActivationAuthority or apply Alias)
- maintenance / DreamRun review uses `digital-brain-maintainer` (report-only;
  no Bash/Edit/activation)

If the host runtime has an explicit permission gate for subagents, this file still defines the default intended pattern. The parent session agent should switch to it as soon as that gate is satisfied.

## Reader Worker

Use this when the main session needs graph evidence but should not carry the full retrieval context.

### Startup Reader Worker

Use this before the first user-facing answer in a new buddy conversation.

```text
Use $digital-brain-buddy-read-memory.

Task type: BOOTSTRAP

What I need from you:
- build the startup evidence pack for this buddy session
- fetch all existing Person nodes with id, canonical name, role/relation, direct relationship context to the user when available
- summarize recurring/sensitive themes for each meaningful person from linked JournalEntry/co-mentioned Topic, State, Event, Organization, or relationship dynamics
- fetch the top 20 weighted core nodes using graph degree as weight
- fetch a compact node label/type weight summary
- include only the recent valid JournalEntry rows needed to orient the current period
- include initiation_evidence: self Person (relation=self), non-self people,
  topics, JournalEntry kind=initiation_complete if any, and counts

Output contract:
- people_map
- person_sensitive_themes
- top_weighted_nodes
- node_type_weight_summary
- recent_baseline
- initiation_evidence
- thin/conflicting areas
- reusable ids or canonical names for later writes

Constraints:
- do not write to the graph
- do not create or infer new people
- separate direct graph facts from inference
- keep the pack compact enough for the parent session agent to use as first-layer context
```

### Turn Reader Worker

```text
Use $digital-brain-buddy-read-memory.

Task type: READ
User turn:
<paste exact turn or a tight distilled version>

What I need from you:
- fetch only the graph evidence needed for this turn
- prioritize recent valid JournalEntry rows, core entities, then semantic or local traversal if needed
- resolve ambiguous names alias-first when relevant
- return a compact evidence pack, not final buddy prose

Output contract:
- factual matches
- strongest repeated pattern, if any
- thin/conflicting areas
- reusable ids or canonical names for later writes

Constraints:
- do not write to the graph
- do not answer the user directly
- keep the output short enough for the parent session agent to absorb quickly
```

## Entity Check Worker

Use this when the main session has a candidate name/id for a new or ambiguous entity that resembles an existing core entity, and needs a duplicate-detection verdict before writing.

```text
Use $digital-brain-entity-check.

Task type: ENTITY_CHECK

Candidate name/id:
<paste the candidate entity's name, and id if it already exists>

Resembling core entity:
<paste the name/id of the existing core entity it resembles>

What I need from you:
- run the "Related nodes via shared connections" query between the candidate and the resembling core entity
- authorize a merge only when shared_connections > 0, or the names are obvious variants (e.g. nicknames)
- otherwise return not authorized rather than guess

Output contract:
- authorized
- keep_id
- keep_name
- reason

Constraints:
- do not write to the graph
- never merge entities yourself; only report whether a merge is authorized
- never create or activate Alias / EntityProtection records
- when evidence is thin or ambiguous, return not authorized rather than guessing
- do not produce buddy-tone prose for the user
```

## FEEDBACK (parent session only)

Do not spawn reader/writer/entity-check workers to activate identity effects.
On a `FEEDBACK` turn the parent session agent:

1. classifies kind (`entity_wrong` | `claim_false` | `miss` | `invent` | `praise`)
2. calls `create_feedback` with **exact** required fields only:
   - `id` (client-minted)
   - `kind` (`entity_wrong` | `claim_false` | `miss` | `invent` | `praise`)
   - `sensitivity` (`public_ops` | `personal` | `intimate`)
   - `harness_generation_id` (session pin, unchanged)
   - optional: `redacted_summary` (short imperative gotcha rule), `raw_payload`,
     `source_turn_ref`
   - **never** pass `summary` / `detail` / `payload` / `note` aliases
   - prefer typed `digital_brain.tools.mcp_client.create_feedback`
3. for corrections (`miss`/`invent`/`entity_wrong`/`claim_false`): **must**
   stage a durable quality-plane gotcha (the Feedback row is the seed; optional
   `record_run_event` with `task_outcome=corrected` + approach/`error_class`/
   `recurrence_key`). **Must not** treat life-journal append as the gotcha path
   (no journal-as-gotcha).
4. surfaces one user-visible line: `gotcha staged: <id> — <rule>` or
   `parked: sensor down`
5. for `entity_wrong`, may show a propose-only Alias review card
6. for `claim_false`, remains propose-only (no life-memory mutation)
7. for `praise`, records a counter only (never a JournalEntry; no gotcha required)
8. never treats generic ack (`yes`/`ok`/👍) as activation
9. max one confirmation prompt per user turn
10. leaves apply/revoke to the operator script
    `scripts/digital_brain_apply_proposal.py` (no unattended `--yes`)

## Writer Worker

Use this when the main session has already decided that the turn should be remembered.

```text
Use $digital-brain-buddy-write-memory.

Task type: WRITE
Write payload:
<paste the exact memory candidate or a clean distilled write payload>

Known entities to reuse when possible:
<optional names or ids>

What I need from you:
- mint one stable append key and fetch the JournalChain head immediately before append
- resolve entities alias-first
- append one JournalEntry through append_journal_entry, then create idempotent links
- reuse existing entities where possible
- return the created journal id plus reused or created entity ids

Output contract:
- created journal id
- append key, outcome, and chain version returned by the receipt
- entities reused vs created
- uncertainty or schema caveats

Constraints:
- one JournalEntry only unless explicitly told otherwise
- writer tasks must be serialized; on timeout reconcile the same append key rather than retrying blindly
- if emitting any quality sensor (RunEvent/Feedback), pass the session-pinned
  `harness_generation_id` (`DIGITAL_BRAIN_HARNESS_GENERATION_ID`) unchanged; never recompute it
- do not create Alias, ActivationAuthority, or EntityProtection nodes
- do not treat FEEDBACK / claim_false as a journal write path
- do not produce buddy-tone prose for the user
```

## Maintainer Worker (report-only)

Use for DreamRun report framing and proposal review cards. Never for activation.

```text
Use $digital-brain-buddy-maintenance (or native digital-brain-maintainer).

Task type: MAINTENANCE_REVIEW
processing_mode expectation: report_only / local_only

What I need from you:
- summarize the public DreamRun report: counts, ids, processing_mode
- list waiting_for_owner proposal ids and deliberately_left_alone ids
- prepare at most one progressive-disclosure review card when asked
- never paste raw intimate Feedback/journal quotes in the default report

Constraints:
- host tools: Read/Grep/Glob only — no Bash, Edit, Write, or activation
- do not call apply/activate scripts or mint ActivationAuthority
- exact-token APPLY alias:<id> is intent only, not authorization
- no scheduled run, no heartbeat, no shared-session private proposal queue
- approval, application, deployment, and effectiveness are separate messages
```

## Parent-Agent Reminder

After a delegated read:

- the parent session agent still decides what the evidence means
- the parent session agent still separates fact from inference

After a delegated write:

- the parent session agent still decides whether to mention that memory was stored
- the parent session agent still owns the conversational response

## Harness Generation (all sensor-capable sessions)

Every session that may emit RunEvents or Feedback — not only private buddy
sessions — must carry the SessionStart-pinned generation id:

- source: `DIGITAL_BRAIN_HARNESS_GENERATION_ID` or the session pin file
- pass the **same** id into every sensor call for the session
- do not recollect core commit, SOUL hash, overlay/policy digests mid-session
- never include SOUL content in worker prompts or tool arguments; digests only

When spawning workers that might emit sensors, include:

```text
Pinned harness_generation_id (do not recompute):
<paste DIGITAL_BRAIN_HARNESS_GENERATION_ID>
```
