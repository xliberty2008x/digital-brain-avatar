# Design: Initiate Protocol (Empty Buddy First Run)

Date: 2026-07-10  
Status: Ready for user review  
Scope: `plugins/digital-brain-buddy` session path (Approach 1 — session-mode extension)

## Problem

On a clean install the buddy is empty:

- No personal `SOUL.MD` (or only a neutral copy of `assets/SOUL.template.md`)
- Empty Neo4j graph: no self `Person`, no relationships, no focus topics, no journal
- Session skill runs mandatory `BOOTSTRAP`, finds nothing, and falls into thin “I have no memory” buddy mode

Today’s tools are incomplete for release:

| Existing piece | What it does | Gap |
| --- | --- | --- |
| `init_soul.py` | Copies template → `SOUL.MD` | No co-creation meeting |
| `digital-brain-buddy-identity-bootstrap` | Refine SOUL on demand | Manual; graph untouched |
| Session `BOOTSTRAP` | People map + heavy nodes | Empty pack is not an onboarding flow |
| Write-memory / journal append | Durable graph writes | No first-run script or completeness markers |

Release needs a deliberate **initiate protocol**: auto-enter a first meeting when the buddy is empty, seed minimum durable identity and graph, then hand off to normal buddy mode with progressive soft fill later.

## Goals

1. **Auto-detect** incomplete initiation from graph evidence (and SOUL language/overlay) before the first normal buddy reply.
2. **Meeting 1** (progressive minimum): language → short in-language intro → Q&A that seeds **self Person**, **one anchor Person + relation**, **one focus Topic**, plus a **light SOUL overlay**.
3. **Resume** from the next missing piece if the user abandons mid-meeting; no wipe/reset in this release.
4. **Exit** to normal `SKIP` / `READ` / `WRITE` once the meeting-1 checklist and initiation receipt exist.
5. **Soft progressive hooks** after complete when the graph is still thin (at most one gentle gap question per session on low-stakes turns).
6. Reuse existing write path: `append_journal_entry`, alias-first entities, chain rules, reader/writer split where available.

## Non-goals

- Full SOUL co-rewrite or therapy-style multi-session intake
- Dedicated initiate skill or `/digital-brain-initiate` command (can add later)
- Operator wipe/reset of initiation state
- Seeding a rich life graph in one sitting (multiple people, full history)
- Changes to Docker compose bring-up, MCP URL, or host install packaging beyond docs + version bump
- Separate local state file (`INITIATE.md`); graph (+ SOUL for language/overlay) only

## Product decisions (locked)

| Decision | Choice |
| --- | --- |
| Shape | Progressive: minimal meeting 1, grow later |
| Meeting-1 exit bar | Self + one relationship + one focus + light SOUL |
| Entry | Auto-detect empty/incomplete → initiate before normal buddy |
| State storage | Graph markers only (SOUL holds language/overlay text) |
| After meeting 1 | Soft gap hooks only (not session-open interviews) |
| SOUL depth | Light overlay on template, not guided full rewrite |
| Mid-fail / redo | Resume only; no full wipe |
| Architecture | Approach 1: session-mode extension + reference protocol doc |

## Success criteria

- Fresh install + empty graph → first user-facing turn is **initiate** (language → intro → Q&A), not normal buddy with empty memory.
- Partial seed (e.g. self only) → next conversation **resumes** without re-asking completed facts.
- Checklist + receipt present → subsequent sessions are normal buddy; soft hooks only when graph is thin.
- All initiation completeness is **discoverable from the graph** (plus SOUL for language/overlay); no side control-plane file.
- Writes never invent people/topics the user refused to provide; incomplete sessions remain incomplete.

## Architecture

### Approach

Fold initiate into `digital-brain-buddy-session`. After SOUL load and mandatory `BOOTSTRAP`, compute `initiation_status`. If incomplete, use session mode `INITIATE` (or `INITIATE_RESUME`) instead of `SKIP` / `READ` / `WRITE`. Meeting script and stage rules live in a dedicated reference file so the main skill stays a thin router.

### Component layout

```
plugins/digital-brain-buddy/
├── skills/digital-brain-buddy-session/
│   ├── SKILL.md                         # gate: BOOTSTRAP → status → INITIATE | normal
│   └── references/
│       ├── initiate-protocol.md         # NEW: stages, script, resume, soft hooks
│       └── subagent-prompts.md          # extend BOOTSTRAP prompt with initiation evidence
├── skills/digital-brain-buddy-identity-bootstrap/
│   └── SKILL.md                         # light User overlay edits only during initiate
├── skills/digital-brain-buddy-write-memory/
│   └── …                                # unchanged contract; used for seeds + receipt
├── skills/digital-brain-buddy-read-memory/
│   └── …                                # BOOTSTRAP surfaces self / anchor / focus / receipt
└── assets/SOUL.template.md              # add empty ## User overlay section
```

No new skill, slash command, MCP tool, or SessionStart hook for v1.

### Session flow

```
load SOUL (copy template if missing)
  → BOOTSTRAP evidence pack
       + initiation inputs: self? anchor? focus? receipt? language in SOUL?
  → compute initiation_status
  → if incomplete:
       INITIATE / INITIATE_RESUME
         language (if needed) → intro (full or one-line re-orient)
         → next missing Q&A step → seed writes
         → when checklist met: receipt → normal buddy (same session ok)
  → if complete:
       normal SKIP / READ / WRITE
       if graph_thin: at most one soft hook on low-stakes turn
```

### Ownership

| Actor | Responsibility |
| --- | --- |
| Main session agent | Status, initiate dialogue, SOUL overlay, final phrasing |
| Reader | BOOTSTRAP + initiation evidence fields |
| Writer | Self / anchor / focus nodes, links, receipt `JournalEntry` |
| Identity-bootstrap rules | Structure-preserving light overlay on `SOUL.MD` |

## Detection and graph markers

### When to check

On every **new buddy conversation**, after mandatory `BOOTSTRAP` and SOUL load, **before** the first normal-buddy user-facing reply.

### Completeness checkpoints

| Checkpoint | Evidence |
| --- | --- |
| **Language** | `SOUL.MD` User overlay has preferred language set (not blank) |
| **Self** | `Person` with `relation = "self"` (canonical name = what the user wants to be called) |
| **Anchor person** | At least one other `Person` with a clear relation/role to the user (partner, friend, colleague, …), created or linked during initiation |
| **Focus** | At least one `Topic` treated as current focus and linked/mentioned in initiation context |
| **Receipt** | One `JournalEntry` created via `append_journal_entry` with optional `properties` including **`kind: "initiation_complete"`**, content summarizing the seed, and post-append MENTIONS to self + anchor + focus |

YAGNI: no separate `InitiationState` node in v1. Detection query:

```cypher
MATCH (j:JournalEntry {kind: "initiation_complete"})
RETURN j.id AS id, j.timestamp AS timestamp
ORDER BY j.timestamp DESC
LIMIT 1
```

`append_journal_entry` already accepts flat `properties` (reserved journal fields excluded); `kind` is not reserved and is the canonical receipt marker.

### Status progression

Logical order for **what is missing** (resume jumps to first incomplete):

```
missing_language
  → needs_intro_beat          # session-local: full intro once per incomplete session if language just set / first turn
  → missing_self
  → missing_anchor_person
  → missing_focus
  → missing_soul_overlay      # User overlay beyond language still empty
  → missing_receipt
  → complete
```

Notes:

- `needs_intro_beat` is **not** a durable graph marker. On resume, if language already exists, use a **one-line re-orient** in that language, not the full first-run intro.
- Do **not** write the receipt until self, anchor, focus, language, and light overlay (beyond language) are done.
- **complete** when receipt exists and checkpoints above are satisfied.

### Detection rules

1. No self `Person` (`relation = "self"`) → `missing_self` (even if unrelated experimental nodes exist).
2. Self exists, no non-self Person with relation context → `missing_anchor_person`.
3. Self + anchor, no focus Topic → `missing_focus`.
4. Graph minimum ok but User overlay incomplete → finish overlay before receipt.
5. All seeds + overlay ok, no receipt → write receipt (may be same turn as last seed).
6. Receipt present and checkpoints hold → `complete`.

### Thin graph (post-complete)

After `complete`, optional internal flag `graph_thin` when e.g. only one non-self Person or fewer than two Topics. Used only to allow soft hooks; does not re-enter full initiate.

## Meeting script

### Tone in `INITIATE` mode

Same buddy DNA (direct, compact, not therapist/cheerleader), slightly more **guided**: one clear question at a time, confirm what was saved, no long wizard forms.

### Opening (always before other Q&A)

1. **Language first**  
   - If the user’s first message clearly establishes language, adopt it.  
   - Otherwise ask once what language to use.  
   - Persist immediately in SOUL User overlay (`Preferred language`).  
   - **All subsequent initiate turns use that language.**

2. **Short intro in that language**  
   - 2–4 sentences: what the buddy is (personal graph memory + direct stance), that the brain is empty, and that this meeting only seeds *you*, *one important person*, *one current focus*, plus a light stance overlay — then normal conversation.  
   - Not a product essay.

3. **Q&A** (skip steps already satisfied on resume)

| Step | Prompt intent | Write |
| --- | --- | --- |
| Self | What should I call you? | `Person`, `relation: "self"` |
| Anchor | One person who matters a lot right now + how related | `Person` + relation; link to self |
| Focus | Main thing on mind / focus for this period | `Topic` + link |
| Overlay | How hard to push; what to protect; hard nos (if not already answered) | `SOUL.MD` User overlay only |
| Close | Short summary → normal mode handoff | Receipt `JournalEntry` + MENTIONS |

If the user dumps multiple answers in one message, extract all present fields, write what is valid, and only ask for missing pieces.

### Resume behavior

- Recompute status from graph + SOUL every new conversation.
- State what is already known (“I already have you as X and Y as partner”).
- Ask only the next gap.
- Never re-run full language discovery if language is already in SOUL.
- No wipe/reset path in this release.

### SOUL overlay contract

- Preserve template sections: Core, Tone, How to think, What to protect, What not to do, Response shape.
- Ship template with an empty `## User overlay` section so first copy has a stable place to write:
  - Preferred language
  - How hard to push
  - What to protect (user-specific)
  - Hard boundaries (“never…”)
- Durable stance only; no temporary moods (identity-bootstrap rules).

## Data flow and writes

1. User answer → main agent extracts structured facts.
2. Writer: create/merge self `Person` (`relation: "self"`).
3. Writer: create/merge anchor `Person` + relationship to self. Prefer live schema relationship types; if typed edge is unclear, still create the Person with `relation` property and ensure receipt MENTIONS both.
4. Writer: create/merge focus `Topic` + link / MENTIONS.
5. Main: patch SOUL User overlay (identity-bootstrap discipline).
6. Writer: `append_journal_entry` for receipt with `properties: { "kind": "initiation_complete" }` (and optional stable content prefix); then idempotent MENTIONS for self, anchor, focus.

All JournalEntry creation goes through the server-owned append protocol (append key, chain head, no raw FOLLOWS). Entity links via idempotent MERGE only after append.

## Soft progressive hooks (post-complete)

- Only when `initiation_status = complete` and `graph_thin`.
- At most **one** soft question per session.
- Only on `SKIP` / low-stakes turns — never interrupt deep distress, dense WRITE, or focused READ.
- Examples: second important person, second focus, stance refinement (“what should I never sugarcoat?”).
- Answers use normal `WRITE` path; no new initiation stages required.

## Error handling

| Situation | Behavior |
| --- | --- |
| Graph/MCP down | Do not fake initiation; tell user to bring stack up (`/digital-brain-up` / compose). SOUL-only is not “complete.” |
| Write failure | Leave status incomplete; next resume re-reads graph; use idempotent merge, no blind double-create |
| User refuses a step | Stay on that stage; do not invent entities; session may end incomplete |
| Template SOUL, no overlay | Do not write receipt until light overlay applied |
| Nodes exist, receipt missing | Prefer write receipt after brief confirm rather than full re-interview |

## Testing

1. **Status derivation** from fixture graphs: empty; self-only; self+anchor; self+anchor+focus without receipt; complete with receipt; language missing with graph present.
2. **Resume path**: incomplete markers → next question only.
3. **Soft hooks**: complete + thin → allowed once on low-stakes; complete + not thin → no hook; incomplete → no soft-hook path (still INITIATE).
4. **Write contract**: receipt uses append protocol; no raw JournalEntry Cypher create.
5. Skill/doc scenarios sufficient for v1; full multi-host E2E UI optional.

## Packaging and docs

- Plugin README: first session on empty install **is** initiation (language → intro → seed).
- Session `SKILL.md`: document `INITIATE` mode and pointer to `references/initiate-protocol.md`.
- Bump `plugins/digital-brain-buddy/version.json` (and related manifests) when shipping — agent-contract change.
- CHANGELOG entry under the new plugin version.

## Implementation outline (for planning skill)

1. Add `## User overlay` to `SOUL.template.md`.
2. Extend BOOTSTRAP / reader expectations to return initiation evidence fields.
3. Write `references/initiate-protocol.md` (stages, script, resume, soft hooks, marker queries).
4. Update session `SKILL.md`: status gate, `INITIATE` routing, soft-hook rules.
5. Align identity-bootstrap with User overlay section.
6. Add status-derivation tests / fixtures.
7. Docs + version bump + CHANGELOG.

## Open points resolved in design

| Topic | Resolution |
| --- | --- |
| Progressive vs deep one-shot | Progressive |
| Entry | Auto-detect |
| State | Graph markers (+ SOUL for language/overlay text) |
| Architecture | Session extension, not dedicated skill |
| Meeting open | Language → in-language intro → Q&A |
| Redo | Resume only |

## Out of scope follow-ups (later)

- Explicit `/digital-brain-initiate` or reset/wipe operator path
- Dedicated initiate skill packaging
- Multi-person / multi-topic guided intake
- Separate `InitiationState` node if receipt queries prove brittle
