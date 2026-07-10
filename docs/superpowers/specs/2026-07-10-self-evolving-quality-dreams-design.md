# Design: Self-Evolving Quality Program + Dreams

**Status:** Draft approved in principle (2026-07-10) — awaiting final user review before implementation plan  
**Repo:** `avatar_digital_brain`  
**Companion visual (gitignored):** `tmp/2026-07-10-self-evolving-quality-design.html`  
**Related:** journal append protocol (`mcp_servers/cypher`), buddy plugin (`plugins/digital-brain-buddy`), entity resolver / Alias / LearningLog

---

## 1. Problem

The digital brain has strong **structural** safety (server-owned journal append, alias-first resolution, entity-check) but almost no measured loop for “is the memory/answer true?”

Known failure modes:

- Wrong or duplicate entities (e.g. typo orgs)
- Retrieval misses or noise
- Buddy claims without graph evidence
- Write routing that could journal junk or miss durable facts
- If every human correction becomes an immortal retrieval-heavy row, the system **fills up instead of evolving**

We need: a **sensor** (live feedback), an **online thin loop** (propose & confirm), and an **offline consolidation** (dreams) that upgrades life/ops memory and, gated, the agent harness — without drowning context in feedback history.

---

## 2. Goals

1. **Full-stack quality (A–D):** retrieval, identity, write decisions, answer grounding — metrics first.
2. **Live human ground truth (v1):** free-form in-chat corrections as the truth signal (no offline gold set required to start).
3. **Propose-and-confirm learning:** always audit; graph identity effects only after explicit yes / edit / no.
4. **Anti-sink:** Feedback is staging, not long-term narrative memory.
5. **Dreams:** separate evolution sessions that digest feedback, promote durable artifacts, archive noise, and propose harness changes.
6. **Preserve append-only life history:** no DELETE of disputed journals; corrections move truth forward.

### Non-goals (v1–v1.5)

- Storing the entire plugin source tree in Neo4j
- Silent SOUL rewrites mid-buddy session
- Loading full Feedback history into BOOTSTRAP
- `DETACH DELETE` of “wrong” life history as the default fix
- Multi-user social trust
- Requiring an offline labeled gold set before shipping the sensor

---

## 3. Locked decisions

| Question | Choice |
| --- | --- |
| Scope | Full stack (retrieval · identity · writes · grounding) |
| Ground truth (v1) | Live human feedback |
| Channel | Free-form chat (not slash-commands required) |
| Learning aggressiveness | Propose & confirm |
| History | Append-only journals; Alias / demote for identity |
| Harness evolution | Dreams + **hybrid** landing (graph soft policy + gated file diffs) |
| Whole plugin in DB? | **No** |

---

## 4. Three memory layers

Do not mix these grades in storage or in what BOOTSTRAP loads.

| Layer | Content | Lifetime | Examples |
| --- | --- | --- | --- |
| **Life memory** | Journal chain, people, orgs, events | Forever (append-only) | EPAM, Audi, Іра |
| **Operational learning** | Identity and trust fixes | Durable, small | `Alias CarPlace→CarID`, demote |
| **Harness evolution** | How the agent behaves | Versioned, gated | FEEDBACK routing, grounding policy, skill/SOUL patches |

**Feedback is not a fourth long-term memory.** It is a **staging sensor**: hot set → dream digests → promote (Alias / correction journal / policy) → archive. Metrics may query archives; buddy context must not load them by default.

---

## 5. Dreams (harness + memory evolution)

A **dream** is a scheduled or intentional offline session. It does not buddy-chat. It consolidates — analogous to sleep / daily notes → long-term MEMORY.

### Wake (buddy)

- Routes: `SKIP` | `READ` | `WRITE` | `FEEDBACK`
- Life WRITE uses existing append protocol
- FEEDBACK → thin audit (+ optional one-shot propose/confirm)
- Does **not** rewrite skills mid-conversation
- BOOTSTRAP loads: people, heavy nodes, recent journals, **active** policies (bounded top-K)
- Never loads archived Feedback into context

### Dream (evolve)

- Trigger: `/digital-brain-dream`, heartbeat when open feedback &gt; N, or weekly cron
- Batch open Feedback + disputes + optional dupe candidates
- Apply confirmed graph ops (Alias, demote, missing life journals)
- Propose harness diffs (file patches and/or soft `AgentPolicy`)
- Archive absorbed Feedback; write one `DreamRun` report
- Emit a short human-readable “what I changed about myself”

```text
wake                              dream
────                              ─────
talk, life WRITE                  batch Feedback + disputes
FEEDBACK → thin audit             distill by kind
maybe 1 quick proposal            graph effects + gated harness
                                  archive Feedback → DreamRun
```

---

## 6. Anti-sink rules

| Rule | Mechanism |
| --- | --- |
| Hot set only | Only `open` / `proposed` (and briefly `accepted` pre-apply) Feedback is active |
| TTL + rollup | After dream (or age threshold), status → `archived`, linked to `dream_id` |
| No journal vector pollution | Feedback is its own label; **not** in `journal_entry_embedding_index` |
| Promote, don’t pile | Dream outputs few durable artifacts, not hundreds of Feedback rows in context |
| Metrics ≠ context | Rates computed in dream / weekly Cypher, not stuffed into buddy packs |
| Cap open Feedback | If open &gt; threshold, nudge or require dream |

**Lifecycle:**  
`created (open) → proposed → accepted|rejected → applied → archived(by dream)`  
Durable side-effects: Alias, LearningLog, optional correction JournalEntry, optional AgentPolicy / file diff. Report: one `DreamRun` per dream.

---

## 7. Online feedback loop (wake)

1. **Detect** — turn is FEEDBACK, not life WRITE  
2. **Parse** — `kind` + optional targets (entity names/ids, last assistant claim)  
3. **Audit** — always write `Feedback` (hot)  
4. **Propose** — one-line fix, **or** park for dream if ambiguous / harness-level  
5. **Confirm** — free-form yes / no / edit  
6. **Apply or park** — Alias/demote/optional journal now, or leave open for dream  

### Feedback kinds

| kind | Example signal | Wake behavior |
| --- | --- | --- |
| `entity_wrong` | “not CarPlace — CarID” | Propose Alias if clear |
| `claim_false` | “that never happened” | Soft demote; dream may consolidate |
| `miss` | “you forgot EPAM Dec” | Propose life WRITE |
| `invent` | “you made that up” | Log; dream may draft grounding policy |
| `praise` | “exactly”, “👍” | Log only — never life journal |

### Park for dream when

- Ambiguous targets  
- Would change SOUL / skill wording  
- Same pattern seen repeatedly (harness rule candidate)  
- User defers / rejects mid-session  
- Open Feedback count high  

---

## 8. Dream pipeline

1. **Load (bounded)** — open Feedback, last DreamRun summary, dupe candidates, invent/claim_false themes  
2. **Cluster** — by kind / entity / theme  
3. **Graph effects** — Alias, demote, correction journals (append protocol only)  
4. **Harness effects** — soft `AgentPolicy` and/or file diffs (gated)  
5. **Archive** — Feedback `absorbed_by_dream_id`  
6. **Report** — `DreamRun` summary + metrics delta  

Human-facing output example:  
“Dream 2026-07-10: 4 Alias proposed (2 applied), 1 policy draft, 12 Feedback archived — review skill diff?”

---

## 9. Harness landing (hybrid)

| Option | Description | Verdict |
| --- | --- | --- |
| A Files only | SOUL / skills / policies on disk | Reviewable; multi-host sync manual |
| B Graph only | All behavior as `AgentPolicy` nodes | Portable; review/self-injection risk |
| **C Hybrid (chosen)** | Graph for ops + soft policy; files for skill/SOUL | Default |

| Change type | Landing | Gate |
| --- | --- | --- |
| Entity Alias / demote | Neo4j | User confirm (wake) or dream batch confirm |
| Soft operational rules | `AgentPolicy` (versioned, `active`) | Dream propose → accept |
| Skill markdown / runtime patterns | Files under plugin | Dream exports diff; human merge |
| SOUL voice | `SOUL.MD` file only | Explicit human edit — dreams never silent-write SOUL |

---

## 10. Full-stack levers (A–D)

| Layer | Improvements |
| --- | --- |
| **A Retrieval** | Hybrid retrieval; respect disputed/low trust; Alias expand; never retrieve archived Feedback |
| **B Identity** | Entity-check before create; feedback → Alias; dream dupe report (no auto DETACH DELETE) |
| **C Writes** | Routes include FEEDBACK; dream mode separate; praise/👎 never life journals |
| **D Grounding** | Fact vs inference; invent → dream policy; lower confidence when evidence thin |

---

## 11. Evaluation — how we know it’s true

With live feedback as ground truth, “true” means: **you stop correcting the same class of error**, and when you correct, dreams absorb it so context stays small.

| Metric | Definition | Proves |
| --- | --- | --- |
| `feedback_rate` | Feedback count / buddy turns | Overall quality trend |
| `open_feedback_count` | status in open\|proposed | Anti-sink health |
| `dream_absorb_rate` | archived per dream / open before | Dreams digest |
| `proposal_accept_rate` | accepted / (accepted+rejected+edited) | Parse + proposal quality |
| `error_recurrence` | same target wrong after apply | Evolution stuck |
| `harness_diff_accept` | merged skill diffs / proposed | Harness evolution useful |
| `grounding_complaint_rate` | invent+claim_false / factual claims | Answer honesty |
| `kind_mix` | share by kind | Which layer A–D fails most |

Reporting v1: Cypher dashboard / weekly dump — no product UI required.

---

## 12. Schema sketch

### Feedback

```text
(:Feedback {
  id: UUID,
  raw_text: string,
  kind: entity_wrong | claim_false | miss | invent | praise | other,
  status: open | proposed | accepted | rejected | applied | archived,
  assistant_claim: string?,
  session_ref: string?,
  created_at: datetime,
  resolved_at: datetime?,
  absorbed_by_dream_id: string?
})
```

### DreamRun

```text
(:DreamRun {
  id: UUID,
  started_at, finished_at,
  summary: string,
  metrics_json: string,
  feedback_ids: list?
})
```

### AgentPolicy (soft harness)

```text
(:AgentPolicy {
  key: string,
  version: int,
  body: string,          // bounded size
  active: boolean,
  source_dream_id: string?,
  risk: soft | skill_level
})
```

### Relationships (illustrative)

```text
(:Feedback)-[:ABOUT]->(:JournalEntry|Person|Organization|…)
(:Feedback)-[:ABSORBED_BY]->(:DreamRun)
(:DreamRun)-[:PROPOSED_POLICY]->(:AgentPolicy)
```

### Existing (unchanged contracts)

- `Alias {from_name, to_name, canonical_id}` — primary identity learning  
- `LearningLog {type, entity, timestamp, …}` — machine audit of merges/corrections  
- JournalEntry **only** via `append_journal_entry` (HEAD/FOLLOWS/CAS/embedding)  
- Optional soft trust: `trust` float, `disputed` boolean on entities  

### Hard rules

- Feedback **never** enters the journal embedding index  
- Generic Cypher remains MERGE-only post-append; no raw JournalEntry/FOLLOWS creation  
- Policy bodies are bounded; raw Feedback text is never injected as an untrusted system prompt wholesale  

---

## 13. Session protocols (skills)

### Buddy skill extensions

- Classify FEEDBACK separately from WRITE  
- On FEEDBACK: audit → propose or park → confirm → apply  
- BOOTSTRAP: add active AgentPolicy (K); exclude archived Feedback  
- Keep fact vs inference response rules; measure invent feedback against them  

### New dream skill (conceptual)

- Name e.g. `digital-brain-dream` / command `/digital-brain-dream`  
- Bounded load → cluster → graph effects → harness proposals → archive → DreamRun  
- Never buddy-tone monologue; never silent SOUL rewrite  

Writer/reader workers stay as today for graph I/O; dream may reuse writer for Alias/links and append for life corrections only.

---

## 14. Phased rollout

| Phase | Deliverable |
| --- | --- |
| **v0** | Feedback sensor + hot/archive fields; measure open_count + kind_mix; manual archive OK |
| **v1** | Propose & confirm online for ops learning only (Alias / demote / optional correction journal) |
| **v1.5** | Dream skill: digest, archive, DreamRun; soft AgentPolicy optional; file diffs as text for human paste/commit |
| **v2** | Harness PR-style loop; optional high-precision auto-Alias (“not X — Y” + entity-check); still no silent SOUL rewrite; optional offline gold later |

---

## 15. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Feedback sink | Hot set, archive, no vectors, dream pressure, open cap |
| Dream neglect | Nudge when open_feedback_count &gt; N |
| False parse / sarcasm | Propose-and-confirm; park ambiguous |
| Policy self-injection | Bound policy size; never treat raw Feedback as system prompt |
| Silent SOUL rewrite | Forbidden; file + human merge only |
| Journal pollution | FEEDBACK ≠ life WRITE |
| Infra fragility | Same hardened MCP write path; Feedback writes must not invent side channels |

---

## 16. Direct answers (design Q&A)

| Question | Answer |
| --- | --- |
| How not to sink in feedback records? | Staging + hot/archive + no vector pollution + promote to Alias/policy + dreams digest |
| Evolution sessions? | **Yes — dreams**, separate from buddy wake mode |
| Save plugin inside DB? | **No whole plugin.** Graph: life + Alias + soft AgentPolicy. Files: skills/SOUL via gated dream diffs |
| What are dreams? | Offline consolidation that upgrades memory and (gated) agent rules without drowning chat context |

---

## 17. Implementation notes (for later plan — not in this approval)

Out of scope for this doc’s approval gate; listed so planning is easier:

- Schema migration / constraints for Feedback, DreamRun, AgentPolicy  
- Buddy skill FEEDBACK route + free-form parser heuristics  
- Dream skill + optional heartbeat trigger  
- Metrics Cypher scripts  
- Ensure write path for Feedback does not require journal embeddings (or uses a non-vector node create path allowed by MCP rules)  
- Tests: anti-sink (archived not in bootstrap), Alias after confirm, dream archive idempotency  

---

## 18. Approval

**Design direction:** approved in principle by user (“I like it so far”) on 2026-07-10.

**Next:** user reviews this written spec; then implementation plan via writing-plans skill. No implementation until explicit go-ahead after plan review.
