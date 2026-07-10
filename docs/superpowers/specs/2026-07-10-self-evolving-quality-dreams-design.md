# Design: Self-Evolving Quality Program + Memory Hygiene (Dreams)

**Status:** Draft **rev 3** — critics panel amendments incorporated (2026-07-10)  
**Repo:** `avatar_digital_brain`  
**Companions (gitignored):**  
- `tmp/2026-07-10-self-evolving-quality-design.html`  
- `tmp/2026-07-10-critics-panel-quality-dreams.html`  
**Related:** journal append protocol (`mcp_servers/cypher`), buddy plugin (`plugins/digital-brain-buddy`), entity resolver / Alias / LearningLog

---

## 0. Critics panel (rev 3)

Twelve independent critics reviewed rev 2. **No unconditional ship.** Consensus: keep the spine; harden contracts; resequence; demote mythology.

| Critic | Vote | Core attack |
| --- | --- | --- |
| #1 Anti-sink | Conditional fail | Archive ≠ shrink; correction journals re-vectorize noise |
| #2 Security | Conditional fail | Free-text policy + soft confirm + Alias = self-corruption |
| #3 Journal protocol | Conditional fail | Write matrix unlocked; dual-writer races |
| #4 Metrics | Fail as science / pass as telemetry | No denominators; Goodhart; selection bias |
| #5 Parse/UX | Conditional ship | False FEEDBACK worse than missed FEEDBACK |
| #6 Dream ops | Conditional fail | Neglect default; no lease/budget/quiet hours |
| #7 Identity | Conditional fail | Live `DETACH DELETE` merge contradicts design |
| #8 Harness hybrid | Conditional fail | Dual SoT without precedence |
| #9 Feasibility | Ship v0–v1 if tool exists | Need `create_feedback` MCP first |
| #10 Grounding | Conditional fail | No claim unit; demote-Person collateral |
| #11 Privacy | Fail ethics until redaction | Intimate raw survives archive |
| #12 Architecture | Resequence + simplify | Maintenance not “soul dreams”; fix entity/write first |

### Amendments locked by panel (non-negotiable for plan)

1. **Write matrix + first-class Feedback tool**  
2. **Hard confirm** for irreversible ops (not free-form “yes” alone)  
3. **Kill auto DETACH DELETE** merge on wake path (report-only dupes)  
4. **Archive redaction + regret path** (anti-sink is storage, not just context)  
5. **Structured AgentPolicy only** (JSON enums); files beat policy  
6. **Rename product frame:** dreams = **memory hygiene / maintenance**; evolution = diary of approved patches  
7. **Resequence:** sensor + identity safety before harness self-mod  
8. **Metrics = process health** until sampled labels/denominators exist  
9. **Thin Claim layer deferred** unless invent recurrence stays high after A–B fixes  

### Kill criteria (do not ship past)

- Free-text `AgentPolicy` body injected as system/instructions at BOOTSTRAP  
- Alias/demote applied on free-form “yes” without a **pending structured proposal**  
- Any production path auto-`DETACH DELETE`s Person/Org on name similarity alone  
- Feedback requires journal embeddings or enters `journal_entry_embedding_index`  
- Intimate Feedback `raw_text` in default backups/metrics/exports after archive  
- Unattended SOUL rewrite  
- Open Feedback unbounded across 2+ maintenance cycles with no hard gate  
- Concurrent multi-host maintenance writers without a lease  
- Harness auto-promote claiming “truth improved” without holdout / min sample  

---

## 1. Problem

The digital brain has strong **structural** safety (server-owned journal append, alias-first resolution, entity-check) but almost no measured loop for “is the memory/answer true?”

Known failure modes:

- Wrong or duplicate entities (e.g. typo orgs)  
- Retrieval misses or noise  
- Buddy claims without graph evidence  
- Write routing that could journal junk or miss durable facts  
- If every human correction becomes an immortal retrieval-heavy row, the system **fills up instead of evolving**  
- Existing reflex merge paths can **`DETACH DELETE`** “duplicates” — unsafe and conflicts with append-only identity learning  

We need: a **sensor** (live feedback), an **online thin loop** (structured propose & confirm), and **offline maintenance** (hygiene / “dreams”) that promotes durable ops learning and, gated, harness patches — without drowning context or immortalizing intimate correction text.

---

## 2. Goals

1. **Full-stack quality (A–D):** retrieval, identity, write decisions, answer grounding — **process metrics first**, not fake causal “truth scores.”  
2. **Live human sensor (v1):** free-form in-chat corrections as the signal (no offline gold required to start).  
3. **Propose-and-confirm ops learning:** audit always; irreversible graph effects only after **hard confirm**.  
4. **Anti-sink (context + storage):** Feedback is staging; archive redacts; promote few durable artifacts.  
5. **Maintenance sessions:** offline digest (dreams renamed in product language) that archives noise and proposes patches.  
6. **Append-only life history:** no DELETE of disputed journals; corrections move truth forward.  
7. **Identity safety first:** no silent destructive merge; Alias reversible via audit.  

### Non-goals (v1–v1.5)

- Storing the entire plugin source tree in Neo4j  
- Silent SOUL rewrites (wake or unattended maintenance)  
- Loading full Feedback history into BOOTSTRAP  
- `DETACH DELETE` of life entities as default “fix”  
- Multi-user social trust  
- Free-text policies as a second SOUL  
- Claiming causal quality improvement from free-form rates alone  
- Full Claim graph in v0–v1 (deferred)  
- Requiring offline gold labels before shipping the sensor  

---

## 3. Locked decisions

| Question | Choice |
| --- | --- |
| Scope | Full stack, but **resequenced** (sensor + identity → maintenance → soft policy → claims/harness PR) |
| Ground truth (v1) | Live human feedback as **sensor**; optional later sampled rubrics |
| Channel | Free-form chat for *signals*; **structured confirms** for *mutations* |
| Learning aggressiveness | Propose & **hard** confirm for Alias/demote/policy activate |
| History | Append-only journals; Alias / claim-or-entity dispute — never auto DETACH |
| Offline sessions | **Memory hygiene / maintenance** (internal name may stay “dream”) |
| Harness landing | Hybrid: structured graph policy (demoted) + gated **file** diffs; SOUL file-only |
| Whole plugin in DB? | **No** |
| Product frame | Instrument + human gate; not “soul evolves while you sleep” |

---

## 4. Three memory layers

| Layer | Content | Lifetime | Examples |
| --- | --- | --- | --- |
| **Life memory** | Journal chain, people, orgs, events | Forever (append-only) | EPAM, Audi, Іра |
| **Operational learning** | Identity and dispute fixes | Durable, small | `Alias CarPlace→CarID` |
| **Harness evolution** | How the agent behaves | Versioned, gated | FEEDBACK routing, structured policy, skill patches |

**Feedback is not a fourth long-term memory.** Lifecycle: hot sensor → confirm/promote few effects → **archive with redaction** → metrics on aggregates. Buddy never loads archived Feedback by default.

---

## 5. Wake vs maintenance (dreams)

### Product language

| Internal | User-facing preferred |
| --- | --- |
| Dream / DreamRun | Memory hygiene run / maintenance report |
| Self-evolving agent | Quality loop + approved patches |

### Wake (buddy)

- Routes: `SKIP` | `READ` | `WRITE` | `FEEDBACK`  
- Life WRITE → `append_journal_entry` only  
- FEEDBACK → thin audit via **`create_feedback`** (+ optional one-shot propose / hard confirm)  
- Does **not** rewrite skills mid-conversation  
- BOOTSTRAP: people, heavy nodes, recent journals, **active structured AgentPolicy (bounded K)**  
- Never loads archived Feedback; never loads Feedback `raw_text` for intimate tier into multi-user sessions  

### Maintenance (dream / hygiene)

- Trigger: primary **scheduled window** (e.g. weekly local business hours) + manual `/digital-brain-dream` boost + heartbeat **only if** `open_fb > N` **and** outside quiet hours **and** cooldown elapsed  
- Single **run lease** (one host at a time); host tag on `DreamRun`  
- Bounded budget (max Feedback rows, max proposals, max tokens/RAM)  
- Unattended default: **propose-only** for harness; may auto-apply only allowlisted low-risk digests (archive + counters) after prior human policy  
- Cap high-risk skill diffs ≤ 5 per run; rest summarize/defer  
- Stages with run id: ingest → propose → apply(allowlist) → archive → report (idempotent resume)  
- Output: short ops report — **counts, not intimate quotes**

```text
wake                                   maintenance
────                                   ───────────
talk, life WRITE                       batch open Feedback + dupe report
FEEDBACK → create_feedback             distill by kind
propose + hard confirm (ops)           graph effects (confirmed/allowlist)
                                       propose file/policy diffs (gated)
                                       redact+archive → DreamRun
```

---

## 6. Anti-sink rules

| Rule | Mechanism |
| --- | --- |
| Hot set only | Only `open` / `proposed` (briefly `accepted` pre-apply) active |
| **Hard open gate** | If `open_feedback_count > N`, refuse new Feedback **or** coalesce; force maintenance flag — not soft nudge only |
| Archive = shrink | On archive: set status `archived`; **strip or hash `raw_text` / `assistant_claim`** (always for `intimate`, default for all after absorb window); keep kind, target ids, timestamps |
| No journal vector pollution | Feedback never in `journal_entry_embedding_index` |
| Promote, don’t pile | Prefer Alias/demote over correction journals; **cap** correction journals per maintenance run / week |
| Praise without nodes | `praise` increments counters / session stats; **no Feedback node** (or 1/N sample) |
| Metrics ≠ context | Daily/weekly **aggregate** nodes or Cypher rollups — not full raw history in buddy packs |
| GC | Drop or soft-unlink `ABOUT` after archive; one active policy per key; GC inactive policy versions; slim DreamRun (counters, not full text dumps) |
| TTL stuck states | `open|proposed` max age → auto-park; `accepted` not applied in T → re-open or force archive |

**Lifecycle:**  
`created (open) → proposed → accepted|rejected → applied → archived(redacted)`  

**Regret path:** `revoke_feedback(id)` → redact + archive; reverse unconfirmed Alias if still reversible; deactivate derived policy; for correction journals append superseding note (never silent chain delete).

---

## 7. Online feedback loop (wake)

1. **Detect** — FEEDBACK only if: (a) pending proposal, or (b) explicit correction cue + grounded entity from last 1–2 assistant turns, or (c) high-confidence entity rewrite span. Else CHAT / silent park. Prefer **false park over false write**.  
2. **Parse** — `kind` + targets; heuristics first (negation + entity, “not X—Y”, invent phrases); LLM only if ambiguous.  
3. **Audit** — `create_feedback` (hot). Praise → counter only.  
4. **Propose** — one-line reversible proposal **or** silent park for maintenance. Max **1 confirm prompt per user turn**.  
5. **Hard confirm** — soft acks (`👍`, `ok`, `yes`, `да`) valid **only** if a pending proposal is open; irreversible Alias/demote require explicit `APPLY alias:<id>`-style token **or** unambiguous restatement of the proposed mapping.  
6. **Apply or park** — writer MERGE Alias / demote / optional journal; always LearningLog; else leave open for maintenance.  

### Feedback kinds

| kind | Example | Wake behavior |
| --- | --- | --- |
| `entity_wrong` | “not CarPlace — CarID” | Propose Alias after entity-check |
| `claim_false` | “that never happened” | Dispute target claim/edge (not whole Person); park if unclear |
| `miss` | “you forgot EPAM Dec” | Propose life WRITE via append |
| `invent` | “you made that up” | Log; session negation cache; maintenance may draft structured policy |
| `praise` | “exactly”, “👍” | Counter only — never life journal, rarely Feedback node |

### Park for maintenance when

- Ambiguous targets  
- Would change SOUL / skill wording  
- Pattern seen repeatedly (harness candidate)  
- User defers  
- Open Feedback high / confirm budget exhausted  

---

## 8. Confirm hardness & identity safety

### Confirm

| Op | Gate |
| --- | --- |
| Soft praise / “ok” | Only closes pending proposal of equal or lower risk |
| Alias create/repoint | Pending proposal + hard confirm + entity-check authorized |
| Demote / dispute | Pending proposal + hard confirm; **pinned** entities need elevated confirm |
| AgentPolicy activate | Never free-form chat alone; maintenance propose + explicit accept per key |
| SOUL / skill file | Human merge only; never unattended apply |

### Pinned set

User-defined (and defaults for core graph people/orgs) immune to demote/Alias without elevated confirm + LearningLog reason.

### Identity ops (P0 vs live code)

| Rule | Detail |
| --- | --- |
| No auto DETACH DELETE | Disable/remove wake-path merge that deletes nodes; dupe = **report only** |
| Real collapse later | Transfer rels or soft-collapse (`ALIAS_OF` / `active=false`) — never drop edges first |
| Alias-first resolver | Follow chain max depth 3; cycle fail; no bind to missing canonical |
| Unalias | First-class reverse: freeze old mapping, LearningLog, retrieval ignores inactive |
| Ambiguous names | Never `CONTAINS` + `LIMIT 1` as sole resolution; ask or create parallel with audit |
| Shared connections alone | Insufficient for “same entity” |

---

## 9. Maintenance pipeline

1. **Acquire lease** — fail if another host holds run  
2. **Load (bounded)** — open Feedback (structured fields preferred), last DreamRun summary (ops-only), dupe candidates  
3. **Cluster** by kind / entity / theme  
4. **Graph effects** — only pre-confirmed or allowlisted low-risk; Alias/demote templates; rare correction journals (budget)  
5. **Harness effects** — structured policy drafts; file diffs with **base commit SHA** when possible; never silent SOUL  
6. **Redact + archive** Feedback; absorb id → DreamRun  
7. **Report** — ops counts + proposal list for human  

Sanitize: the model context that **authors** policy/skill text must not include full intimate `raw_text` (use kind, ids, short hashes).

---

## 10. Harness landing (hybrid + precedence)

| Layer | Role |
| --- | --- |
| Skill / SOUL **files** | Hard behavior; wins on conflict |
| Structured `AgentPolicy` | Demoted overlay: enum keys + typed values only (e.g. `grounding_threshold`, `retrieval_bias`) |
| Model default | Fallback |

**Precedence:** hard skill/SOUL text → active structured policy (named slots only) → model default.  
Policy **cannot** invent new routes or silence safety lines.  
**One active version per `key`.** Accept deactivates prior; GC old versions.  
BOOTSTRAP: max K actives, total chars ≤ C, order `(priority, updated_at)`; log drops.  
**Host pin:** record `workspace_root`, `soul_sha256`, skill manifest hash; mismatch → warn and prefer files only.  
DreamRun stores `base_commit` / policy versions applied when possible.

---

## 11. Full-stack levers (A–D)

| Layer | Improvements |
| --- | --- |
| **A Retrieval** | Hybrid; respect disputes; Alias expand; never archived Feedback; prefer time/order when conflict |
| **B Identity** | Entity-check; hard-confirm Alias; dupe report only; no auto DETACH |
| **C Writes** | FEEDBACK route; `create_feedback`; praise ≠ journal; write matrix locked |
| **D Grounding** | Fact vs inference; session blocklist after invent/claim_false; **thin Claim deferred** until needed |

### Deferred: thin Claim layer

If invent re-assertion rate stays high after A–B:

```text
Claim { id, text, status: asserted|disputed|retracted|inferred,
        trust, subject_ids[], evidence[], supersedes_id? }
```

Demote **claims/edges**, not whole Persons. Optional citation `[j:…]` on memory asserts.

---

## 12. Evaluation — process health first

Live free-form feedback is a **sensor**, not a valid sole estimator of quality.

### v1 process metrics (ship)

| Metric | Role |
| --- | --- |
| `open_feedback_count` | Anti-sink health (must stay bounded) |
| `dream_absorb_rate` / archive rate | Maintenance digests |
| `proposal_accept_rate` | Parser + proposal quality |
| `error_recurrence` | Same **stable error key** after apply (entity_id + kind + claim_hash) |
| `kind_mix` | Exploratory only (parse noise) |
| `feedback_rate` | Trend only; ambiguous without denominators |

### Later (science upgrade)

- **Exposure denominators:** rates per factual claim / graph write / retrieval, not only per turn  
- **Active sampling:** ~5–10% turns, 1-tap rubric (correct / invent / wrong entity / meh)  
- **Holdouts** before harness auto-promote  
- **Anti-Goodhart:** joint invent↓ + claim coverage / utility — refuse pure invent optimization  
- Dual parse (rules + LLM); drop low-confidence kinds from mix  

Reporting: Cypher / weekly aggregates — **no raw intimate samples** in default dumps.

---

## 13. Schema sketch

### Feedback

```text
(:Feedback {
  id: UUID,                    // MERGE key
  raw_text: string?,           // stripped/null after archive (esp. intimate)
  raw_hash: string?,
  kind: entity_wrong | claim_false | miss | invent | praise | other,
  status: open | proposed | accepted | rejected | applied | archived,
  sensitivity: public_ops | personal | intimate | legal,
  assistant_claim: string?,    // stripped on archive
  proposal_id: string?,
  session_ref: string?,
  created_at, resolved_at?,
  absorbed_by_dream_id: string?
})
```

### DreamRun (maintenance run)

```text
(:DreamRun {
  id: UUID,
  host_tag: string?,
  started_at, finished_at,
  stage: ingest|propose|apply|archive|done|failed,
  summary: string,             // ops-only; no intimate quotes
  metrics_json: string,        // counters only
  base_commit: string?,
  lease_until: datetime?
})
```

### AgentPolicy (structured only)

```text
(:AgentPolicy {
  key: string,                 // enum-like controlled vocabulary
  version: int,
  body_json: string,           // JSON object, schema-validated at write
  active: boolean,             // at most one active per key
  source_dream_id: string?,
  expires_at: datetime?,       // required for relationship-domain policies
  domain: ops | retrieval | grounding | relationship
})
```

### Relationships

```text
(:Feedback)-[:ABOUT]->(entity|journal)
(:Feedback)-[:ABSORBED_BY]->(:DreamRun)
(:DreamRun)-[:PROPOSED_POLICY]->(:AgentPolicy)
```

### Existing

- `Alias {from_name, to_name, canonical_id, active?}`  
- `LearningLog {type, entity, timestamp, feedback_id?, reason?}`  
- JournalEntry **only** via `append_journal_entry`  

### Write matrix (hard rules)

| Node | Path | Shape |
| --- | --- | --- |
| JournalEntry (life + rare corrections) | `append_journal_entry` | CAS + server embedding |
| Feedback | **`create_feedback` / `upsert_feedback` MCP tool** (preferred); else MERGE Cypher template | No embedding |
| DreamRun, AgentPolicy, Alias, LearningLog | `write_neo4j_cypher` | **MERGE** on stable id/key; property SET; no DELETE |
| Status / archive / activate | same | CAS-friendly status transitions |

- Feedback **never** in journal embedding index  
- Unique constraints: `Feedback.id`, `DreamRun.id`, `AgentPolicy(key,version)`; single active per `key`  
- Policy write rejects free-text instruction bodies / unknown keys  
- Dual writers: Feedback status CAS; dream exclusive lease  

---

## 14. Session protocols (skills)

### Buddy

- Routes: `SKIP | READ | WRITE | FEEDBACK`  
- FEEDBACK intent gates (section 7); never `append_journal_entry` for praise/👎  
- Hard confirm for Alias/demote  
- BOOTSTRAP: structured active policies only; no archived Feedback  
- Session negation cache for recent claim_false / invent  

### Maintenance skill

- Name: `digital-brain-dream` / command `/digital-brain-dream` (user copy: “memory hygiene”)  
- Lease → bounded load → cluster → effects → redact archive → DreamRun  
- Unattended propose-only for harness  
- Never buddy-tone monologue; never silent SOUL  

---

## 15. Privacy / sensitivity

| Control | Rule |
| --- | --- |
| Sensitivity tier | Tag Feedback (and optionally journals/policies): `public_ops` \| `personal` \| `intimate` \| `legal` |
| Redaction | Intimate raw stripped on archive; default strip all raw after absorb |
| Dream summaries | Ops counts only in default report |
| Relationship policies | ≥N confirms over ≥D days + `expires_at`; no third-party character judgments as permanent policy |
| Regret | `revoke_feedback` cascade (section 6) |
| Export | Profiles; default scrub intimate Feedback text; metrics without raw samples |
| Shared sessions | Never load intimate policies/Feedback outside main private session |

---

## 16. Phased rollout (resequenced)

| Phase | Deliverable |
| --- | --- |
| **v0.0** | Disable/report-only auto DETACH merge on wake path |
| **v0** | `create_feedback` MCP + FEEDBACK route + heuristics; hot only; **not** in BOOTSTRAP; process metrics on open_count |
| **v1** | Structured propose + hard confirm; Alias/demote templates; LearningLog; unalias; pin set |
| **v1.1** | Sensitivity + archive redaction + revoke_feedback |
| **v1.5** | Maintenance skill: lease, budget, digest, redact archive, DreamRun; file diffs as text + base SHA; structured AgentPolicy optional |
| **v2** | Optional high-precision auto-Alias (same hard gates); harness PR loop; sampled labels; thin Claim if invent persists |

---

## 17. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Feedback sink (storage) | Redaction, hard open gate, praise counters, GC |
| Soft confirm abuse | Pending proposal + hard token for irreversible ops |
| Self-injection via policy | Structured JSON only; files win; sanitize authoring context |
| Auto-merge destruction | Kill DETACH path; report-only dupes |
| Dream neglect | Scheduled primary window; hard gate; last_run nag |
| Dual host races | Exclusive lease + status CAS |
| Goodhart metrics | Process health only until sampling/holdouts |
| Intimate residue | Sensitivity tiers, redaction, export scrub, regret |
| Journal pollution | Write matrix + skill “never append on FEEDBACK” |
| False FEEDBACK | Intent gates; silent park; confirm budget |

---

## 18. Direct answers (design Q&A)

| Question | Answer |
| --- | --- |
| How not to sink in feedback? | Staging + hard open gate + **redacting archive** + promote Alias/policy + maintenance absorb + praise without nodes |
| Evolution sessions? | **Yes — maintenance/hygiene** (dreams), separate from buddy wake |
| Plugin in DB? | **No whole plugin.** Graph: life + Alias + structured policy. Files: skills/SOUL via gated diffs |
| What are dreams? | Offline **maintenance** that digests sensors and proposes approved patches — not overnight personality rewrite |
| How know it’s true? | Recurrence of **stable error keys** + process health; free-form rate is not causal truth; sampling later |

---

## 19. Implementation notes (for plan)

- MCP: `create_feedback` / `upsert_feedback`, optional `record_dream_run`, `revoke_feedback`  
- Unique constraints + AgentPolicy schema validation at write  
- Remove or gate `consistency_checker` DETACH DELETE  
- Buddy skill FEEDBACK route + confirm state machine  
- Maintenance skill + lease file or graph lease node  
- Metrics Cypher on aggregates  
- Tests: bootstrap excludes Feedback; no embed on Feedback; hard confirm required for Alias; archive redaction; lease exclusivity; MERGE idempotency  

---

## 20. Approval

| Rev | Note |
| --- | --- |
| rev 1–2 | Direction approved in principle (“I like it so far”) |
| rev 3 | Critics panel amendments incorporated; user asked to patch spec |

**Next:** user reviews rev 3; then implementation plan (writing-plans). No implementation until explicit go-ahead after plan review.
