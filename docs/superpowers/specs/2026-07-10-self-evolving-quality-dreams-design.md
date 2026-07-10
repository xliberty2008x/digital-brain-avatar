# Design: Self-Evolving Quality + Maintenance (Dreams)

**Status:** Draft **rev 4** — sensors beyond Feedback; harness file patch pipeline  
**Date:** 2026-07-10  
**Repo:** `avatar_digital_brain`  

**Tracked source of truth:** this file  

**Visual companions (gitignored, not the spec):**  
- `tmp/2026-07-10-self-evolving-quality-design.html` — readable design (rev 4)  
- `tmp/2026-07-10-critics-panel-quality-dreams.html` — 12-critic panel  
- `tmp/2026-07-10-harness-architecture-with-proposals.html` — harness diagrams  

**Related runtime:**  
- Plugin harness: `plugins/digital-brain-buddy/` (skills, agents, SOUL)  
- MCP: `mcp_servers/cypher/` (append protocol, write guards)  
- Graph contracts: `docs/GRAPH_SCHEMA_CONTRACT.md`

---

## 0. What this system is

### Harness (behaviour)

The **agentic harness** is **not** the graph:

| Piece | Location | Role |
| --- | --- | --- |
| Skills | MD under plugin | Routing, procedures, write rules |
| Agents / subagents | plugin `agents/` | Reader, writer, entity-check |
| SOUL | `SOUL.MD` (local) | Voice / stance |
| MCP tools | mcp-cypher | What mutations are possible |
| Soft `AgentPolicy` | Neo4j JSON (optional) | Enum/numeric nudges only |

### Digital brain (memory + sensors)

Neo4j holds **life memory**, **ops learning** (Alias…), and **sensors** (Feedback, RunEvent…), not the full plugin source tree.

### Self-evolution = two paths

```text
Maintenance / dream
├── Memory path  → Neo4j (Alias, trust, rare journals, archive sensors)
└── Harness path → files (skill diffs / learned overlays) [+ optional policy JSON]
                   human merge → next session loads better behaviour
```

**Optional policy alone is not self-evolution of the harness.** Real procedure change lands in **MD skills/overlays**.

---

## 1. Problem

Strong structural safety (append protocol, alias-first, entity-check) but weak closed loop for:

- User-visible mistakes (wrong entity, invent, miss)  
- Tool/approach failures (timeouts, empty retrieval, chain conflict)  
- Tool/approach **successes** worth reinforcing (“gotchas” that worked)  
- Improving **skills** without stuffing the whole harness into Neo4j  
- Not drowning context in immortal correction/telemetry text  

---

## 2. Goals

1. Full-stack quality (retrieval · identity · writes · grounding) with **process metrics** first.  
2. **Sensors:** user Feedback **and** RunEvent (success/fail + approach).  
3. Online **hard confirm** for irreversible memory ops.  
4. Offline **maintenance** (dreams): graph hygiene **and** harness patch proposals.  
5. Anti-sink for sensors (hot → digest → redact/archive/aggregate).  
6. Append-only life history; no auto DETACH DELETE identity “fixes.”  
7. Harness remains **files-first** (reviewable); graph never becomes second SOUL via free-text policy.  

### Non-goals

- Whole plugin source stored in Neo4j  
- Silent SOUL rewrite  
- Full graph weight recompute / bulk relation restructure as dream job  
- Free-text AgentPolicy as system prompt  
- Metrics that claim causal “truth” from free-form rates alone  

---

## 3. Locked decisions

| Question | Choice |
| --- | --- |
| Spec location | This file under `docs/superpowers/specs/` |
| Harness source of truth | Skill/agent/SOUL **files** |
| Sensors | Feedback + **RunEvent** (success/fail/approach) |
| Online learning | Propose + **hard confirm** for Alias/demote |
| Offline | Maintenance digests sensors → memory ops + harness patches |
| Harness upgrade mechanism | **Diff / learned overlay** (+ optional structured policy) |
| Soft policy | Structured JSON enums only; files win on conflict |
| Life journals | `append_journal_entry` only |

---

## 4. Sensors — mistakes, successes, gotchas

User Feedback is necessary but **not sufficient**. Self-evolution needs operational telemetry.

### 4.1 Feedback (human)

| kind | Example | Typical promote |
| --- | --- | --- |
| `entity_wrong` | “not CarPlace — CarID” | Alias after hard confirm |
| `claim_false` | “that never happened” | Dispute claim/edge (not whole Person) |
| `miss` | “you forgot EPAM Dec” | Life WRITE via append |
| `invent` | “you made that up” | Session blocklist; later grounding skill/policy |
| `praise` | “exactly”, “👍” | **Counter only** — not a life journal |

### 4.2 RunEvent (machine) — mistakes **and** successes

Every meaningful tool/approach outcome can emit a compact event:

```text
RunEvent {
  id, timestamp, session_ref?, host?,
  route: SKIP|READ|WRITE|FEEDBACK|MAINTAIN,
  approach: string,           // short label, e.g. "vector_only", "alias_first+entity_check"
  tool: string?,              // append_journal_entry, read_neo4j_cypher, ...
  outcome: success | fail | empty | conflict | timeout,
  error_class: string?,       // chain_conflict, embed_down, no_hits, ...
  entity_ids: [], journal_ids: [],
  latency_ms?: number,
  notes?: string,             // short, no intimate dump
  sensitivity: public_ops | personal | intimate
}
```

| Signal | Example | Harness use |
| --- | --- | --- |
| **Fail** | append version conflict | Skill: always receipt + retry with same append_key |
| **Empty** | vector READ no hits | Skill: fall back recent + Alias expand |
| **Success gotcha** | entity-check denied duplicate correctly | Overlay: “always entity-check before CREATE org-like names” |
| **Success gotcha** | hybrid READ found right person | Reinforce retrieval order in skill |
| **Timeout** | Ollama/MCP | Infra note + fail-soft language; not a life journal |

### 4.3 Sensor rules

- Sensors feed **maintenance**, not full buddy BOOTSTRAP.  
- Prefer structured fields over long free text.  
- Same anti-sink: hot window → digest → archive/aggregate; redact intimate raw.  
- **Praise / success gotchas** must not pollute journal vector index.  

### 4.4 Storage options for RunEvent

| Option | Where | Notes |
| --- | --- | --- |
| Graph `:RunEvent` | Neo4j | Queryable with Feedback; needs retention |
| JSONL | `logs/run-events/YYYY-MM-DD.jsonl` (gitignored) | Cheap; maintenance reads files |
| Hybrid | fail/conflict in graph; verbose in JSONL | Practical default |

**Recommendation:** hybrid — graph for high-signal fails + accepted Feedback; JSONL for volume.

---

## 5. Three memory / store grades

| Grade | Content | Lifetime in context |
| --- | --- | --- |
| **Life memory** | Journal chain, entities, relations | Forever (append-only) |
| **Ops learning** | Alias, LearningLog, light trust/dispute | Durable, small |
| **Sensors** | Feedback, RunEvent | Hot then redacted/archived |
| **Harness** | Skills, SOUL, overlays, soft policy | Files (+ optional policy nodes) |

---

## 6. Wake path (buddy)

Routes: `SKIP` | `READ` | `WRITE` | `FEEDBACK`

| Route | Graph | Telemetry |
| --- | --- | --- |
| SKIP | none | optional low-priority RunEvent |
| READ | read only | RunEvent success/empty/fail + approach |
| WRITE | append + MERGE links | RunEvent + entity-check result |
| FEEDBACK | create_feedback; maybe Alias after hard confirm | Feedback + confirm outcome |

### FEEDBACK online loop

1. Intent gate (pending proposal **or** grounded correction cue; prefer silent park).  
2. `create_feedback` (hot).  
3. One-line proposal **or** park for maintenance.  
4. **Hard confirm** for irreversible ops (soft “yes” only if pending proposal; prefer `APPLY alias:<id>` style).  
5. Apply Alias/demote/miss WRITE + LearningLog, or leave open.  

Max one confirm prompt per user turn. Praise → counters only.

### Identity safety (P0)

- No auto `DETACH DELETE` merge on wake.  
- Dupe candidates = report only until gated merge.  
- Alias-first with chain limits; unalias supported; pinned entities elevated confirm.  

---

## 7. Maintenance (dreams) — dual path

**Product language:** memory hygiene / maintenance run.  
**Internal:** DreamRun.

### Triggers

- Primary: scheduled local window (e.g. weekly)  
- Manual: `/digital-brain-dream`  
- Heartbeat only if open sensors high **and** outside quiet hours **and** cooldown  

### Constraints

- Exclusive **lease** (one host)  
- Budget: max sensors, max proposals, tokens/RAM  
- Unattended: **propose-only** for harness files; allowlisted low-risk archive may auto  
- Stages: ingest → cluster → memory apply → harness draft → archive → report  
- Report: **ops counts**, not intimate quotes  

### 7.1 Memory path outputs

| Output | Notes |
| --- | --- |
| Alias / unalias | Primary digital-brain self-evolution |
| LearningLog | Always on identity ops |
| Dispute / trust | Light; not whole-Person nuke |
| Rare correction journals | Append only; **capped** |
| Archive/redact sensors | Strip raw for intimate / default |

**Not in scope:** global weight recompute, bulk relation rewrite, silent journal DELETE.

### 7.2 Harness path outputs

| Mechanism | Description | Gate |
| --- | --- | --- |
| **A · Unified diff** | Patch against skill/agent MD | Human merge/commit |
| **B · Learned overlay** | Write `learned/<topic>.md`; core skill includes if present | Human approve; easy disable |
| **C · Structured AgentPolicy** | JSON enums in Neo4j | Explicit activate; files still win |

**Recommended:** A + B for real behaviour change; C for knobs only.

**SOUL:** never silent-write; explicit human edit only.

### 7.3 How MD harness improves (even though skills are files)

```text
1 Sensors (Feedback + RunEvent success/fail/gotcha)
2 Maintenance clusters failure classes + success gotchas
3 Draft harness change (diff and/or overlay and/or policy)
4 Human gate
5 Merge into plugins/digital-brain-buddy/...
6 Next session host reloads skills → new behaviour
```

Files are the **deployable harness**. Graph/logs are the **evidence warehouse**. Maintenance is the **compiler** from evidence → proposed harness patches.

### 7.4 Precedence

1. Hard skill / SOUL / overlay files  
2. Active structured AgentPolicy (named slots only)  
3. Model default  

---

## 8. Write matrix

| Node / artifact | Path | Notes |
| --- | --- | --- |
| JournalEntry | `append_journal_entry` only | Embedding server-side |
| Feedback | `create_feedback` MCP (preferred) | No journal embedding index |
| RunEvent | MERGE graph and/or JSONL append | No journal index |
| Alias, LearningLog, DreamRun, AgentPolicy | `write_neo4j_cypher` MERGE | No DELETE default |
| Skill overlays / diffs | filesystem | Outside Neo4j |

---

## 9. Schema sketch (additions)

### Feedback

```text
(:Feedback {
  id, kind, status, sensitivity,
  raw_text?, raw_hash?, assistant_claim?,
  proposal_id?, created_at, resolved_at?,
  absorbed_by_dream_id?
})
```

Statuses: `open | proposed | accepted | rejected | applied | archived`

### RunEvent

```text
(:RunEvent {
  id, timestamp, route, approach, tool?,
  outcome, error_class?, latency_ms?,
  session_ref?, host?, sensitivity,
  notes?, absorbed_by_dream_id?
})
```

### DreamRun

```text
(:DreamRun {
  id, host_tag?, stage, started_at, finished_at,
  summary,              // ops-only
  metrics_json,         // counters
  base_commit?,         // for skill diffs
  harness_patch_paths?, // relative paths proposed
  lease_until?
})
```

### AgentPolicy

```text
(:AgentPolicy {
  key, version, body_json,  // schema-validated JSON
  active, domain, expires_at?, source_dream_id?
})
```

At most one `active` per `key`. Reject free-text instruction bodies.

### Existing

- Alias, LearningLog, JournalChain / FOLLOWS / HEAD via append protocol only  

---

## 10. Evaluation (process health)

| Metric | Role |
| --- | --- |
| `open_feedback_count` / open high-signal RunEvents | Anti-sink |
| `error_recurrence` (stable keys) | Same mistake after fix |
| `success_gotcha_promote_rate` | Useful successes became overlay/skill rules |
| `proposal_accept_rate` | Confirm UX / parse quality |
| `harness_diff_accept` | Human merged skill patches |
| `tool_fail_rate` by `error_class` | Infra / skill procedure health |
| free-form `feedback_rate` | Trend only — not causal truth |

Later: exposure denominators, sampled rubrics, holdouts before auto harness promote.

---

## 11. Privacy

- Sensitivity tiers on Feedback / RunEvent  
- Redact raw on archive  
- Dream summaries = counts  
- `revoke_feedback` regret path  
- Default exports scrub intimate sensor text  
- Shared sessions: no intimate sensors/policies  

---

## 12. Phased rollout

| Phase | Deliverable |
| --- | --- |
| **v0.0** | Disable/report-only auto DETACH merge |
| **v0** | `create_feedback` + FEEDBACK route; sensors out of BOOTSTRAP |
| **v0.5** | RunEvent logging (JSONL and/or graph) for success/fail/approach |
| **v1** | Hard confirm + Alias/demote + LearningLog + unalias |
| **v1.1** | Redaction + revoke |
| **v1.5** | Maintenance: memory hygiene + skill **diff/overlay** proposals + DreamRun |
| **v2** | Gated auto-Alias; richer harness PR loop; optional thin Claim if invent persists |

---

## 13. Critics panel summary (rev 3, still in force)

Hard amendments: write matrix + create_feedback; hard confirm; kill auto DETACH; archive redaction; structured policy only; resequence; metrics as process health; maintenance framing not “soul dreams.”

**Kill criteria:** free-text policy as system prompt; Alias on soft yes; auto DETACH merge; sensors in journal vectors; silent SOUL rewrite; whole plugin in Neo4j; unbounded open sensors without hard gate.

---

## 14. FAQ

| Question | Answer |
| --- | --- |
| Where is the spec? | **This file** — `docs/superpowers/specs/2026-07-10-self-evolving-quality-dreams-design.md` |
| What about the HTML? | Readable companion under `tmp/` (gitignored), not source of truth |
| Self-evolving how? | Sensors → maintenance → **memory ops + harness file patches** |
| Only Feedback? | **No** — also success/fail RunEvents and approach gotchas |
| Only optional policy? | **No** — policy is thin; skills/overlays are the real harness change |
| How if skills are MD? | Maintenance emits diffs/overlays; human merges; host reloads |

---

## 15. Implementation notes (for plan)

- MCP: `create_feedback`, optional `record_run_event`, `record_dream_run`, `revoke_feedback`  
- Skill: emit RunEvent on WRITE/READ failures and notable successes  
- `learned/` overlay convention + session skill include rule  
- Maintenance skill + lease  
- Disable consistency_checker DETACH path  
- Tests: sensors not in bootstrap; hard confirm; archive redaction; harness patch is propose-only unattended  

---

## 16. Approval history

| Rev | Note |
| --- | --- |
| 1–2 | Direction OK (“I like it so far”) |
| 3 | Critics panel amendments |
| 4 | Sensors beyond Feedback; harness file evolution; explicit dual path |

**Next:** implementation plan when user green-lights rev 4. No implementation until plan approval.
