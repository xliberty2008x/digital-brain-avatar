# Canonical Subagent Prompts

Use these prompt shapes whenever delegated execution is available in the host environment.

The goal is consistency:

- main session agent keeps judgment and user-facing prose
- reader worker gathers evidence
- entity-check worker verifies whether a candidate entity is a duplicate before a write
- writer worker persists one bounded memory update

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

Output contract:
- people_map
- person_sensitive_themes
- top_weighted_nodes
- node_type_weight_summary
- recent_baseline
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
- when evidence is thin or ambiguous, return not authorized rather than guessing
- do not produce buddy-tone prose for the user
```

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
- fetch the latest valid JournalEntry.id
- resolve entities alias-first
- create one chain-safe JournalEntry
- reuse existing entities where possible
- return the created journal id plus reused or created entity ids

Output contract:
- created journal id
- previous journal id used for FOLLOWS
- entities reused vs created
- uncertainty or schema caveats

Constraints:
- one JournalEntry only unless explicitly told otherwise
- writer tasks must be serialized; do not assume another writer is not running
- do not produce buddy-tone prose for the user
```

## Parent-Agent Reminder

After a delegated read:

- the parent session agent still decides what the evidence means
- the parent session agent still separates fact from inference

After a delegated write:

- the parent session agent still decides whether to mention that memory was stored
- the parent session agent still owns the conversational response
