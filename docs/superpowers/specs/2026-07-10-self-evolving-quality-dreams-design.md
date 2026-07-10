# Design: Self-Evolving Quality + Maintenance (Dreams)

**Status:** Draft **rev 5** — maintenance compiler; evidence, proposals, receipts, trials, and rollback
**Date:** 2026-07-10
**Repo:** `avatar_digital_brain`

**Tracked source of truth:** this file

**Visual companions (gitignored, not the spec):**
- `tmp/2026-07-10-self-evolving-quality-design.html` — readable design (rev 4; stale after this revision)
- `tmp/2026-07-10-critics-panel-quality-dreams.html` — 12-critic panel
- `tmp/2026-07-10-harness-architecture-with-proposals.html` — harness diagrams

**Related runtime:**
- Plugin harness: `plugins/digital-brain-buddy/` (skills, agents, SOUL)
- MCP: `mcp_servers/cypher/` (append protocol, write guards)
- Graph contracts: `docs/GRAPH_SCHEMA_CONTRACT.md`
- Execution plan: `docs/superpowers/plans/2026-07-10-self-evolving-quality-dreams.md`

---

## 0. Thesis and human contract

A **DreamRun** is a bounded maintenance compiler over recent quality evidence.
It may observe, normalize, cluster, hypothesize, draft, and evaluate without a
human present. It may not silently change identity resolution, life memory
semantics, the loaded harness, or SOUL.

The owner can always distinguish:

1. immutable observations,
2. model-derived findings,
3. proposals waiting for review,
4. allowlisted housekeeping already applied,
5. changes activated by an owner/operator,
6. ambiguity deliberately left untouched.

The trust promise is not “the model will be careful.” The trust promise is that
the maintenance model does not possess the tools required to activate its own
proposals.

### 0.1 What this system is

#### Harness (behaviour)

The **agentic harness** is **not** the graph:

| Piece | Location | Role |
| --- | --- | --- |
| Skills | MD under plugin | Routing, procedures, write rules |
| Agents / subagents | plugin `agents/` | Reader, writer, entity-check |
| SOUL | `SOUL.MD` (local) | Voice / stance |
| MCP tools | mcp-cypher | What mutations are possible |
| Soft `AgentPolicy` | Immutable Neo4j revisions + active slot (optional) | Enum/numeric nudges only |
| Quality control plane | Recorder MCP + authenticated coordinator/operator interfaces + bounded Neo4j nodes | Sensors, leases, proposals, receipts |
| Dream runner | Python/CLI + maintenance skill | Snapshot, cluster, compile, validate, report |
| Proposal quarantine | Runtime state outside plugin load paths | Immutable, non-loadable patch artifacts |
| Active trial overlays | Operator-controlled runtime directory + digest manifest | Reviewed, bounded, generation-pinned trial rules |

### 0.2 Digital brain (memory + bounded quality control plane)

Neo4j holds **life memory**, **ops learning** (Alias…), bounded structured
**quality evidence** (Feedback, high-signal RunEvent…), and control-plane
metadata (DreamRun, Proposal, EffectReceipt, lease). It does not hold the full
plugin source tree or long model transcripts.

Rev 5 does not dual-write every RunEvent to graph and JSONL. Structured,
proposal-eligible events land once through a typed tool. If volume later
requires a local spool, it lives under `DIGITAL_BRAIN_STATE_DIR` (or the XDG
state directory), has a short retention window, and promotes by stable event id.
It is never a second source of truth and never lives under a repo `logs/` path.

### 0.3 Self-evolution = four output lanes

```text
Maintenance / dream
├── Housekeeping → deterministic retention/redaction receipts
├── Memory       → Alias/dispute/missing-memory proposals → typed effect tools
├── Behaviour    → quarantined overlay/diff/policy proposal → evaluation → review
└── Engineering  → code/infra issue or patch proposal → normal repo workflow
```

**Optional policy alone is not self-evolution of the harness.** Real procedure
change lands in reviewed **MD skills/overlays**. Tool or infrastructure failures
may correctly produce an engineering proposal instead of a memory or prompt
change.

### 0.4 Core lifecycle

```text
Observation → Finding → Proposal → Evaluation → Decision
            → EffectReceipt → ExposureWindow → Retain | Rollback
```

These are separate durable facts. Approval does not imply successful apply;
successful apply does not imply effectiveness.

---

## 1. Problem

Strong structural safety (append protocol, alias-first, entity-check) but weak closed loop for:

- User-visible mistakes (wrong entity, invent, miss)
- Tool/approach failures (timeouts, empty retrieval, chain conflict)
- Tool/approach **successes** worth reinforcing (“gotchas” that worked)
- Improving **skills** without stuffing the whole harness into Neo4j
- Not drowning context in immortal correction/telemetry text
- Distinguishing memory, procedure, code/infra, and housekeeping problems
- Proving which harness generation produced an outcome
- Applying, observing, and undoing a change without rule accumulation

---

## 2. Goals

1. Full-stack quality (retrieval · identity · writes · grounding) with **process metrics** first.
2. **Sensors:** user Feedback **and** RunEvent (success/fail + approach).
3. Owner/operator-mediated activation for semantic memory and harness changes.
4. Offline **maintenance**: housekeeping, memory proposals, harness proposals, and engineering proposals.
5. Anti-sink for sensors (hot → digest → redact/archive/aggregate).
6. Append-only life history; no auto DETACH DELETE identity “fixes.”
7. Harness remains **files-first** (reviewable); graph never becomes second SOUL via free-text policy.
8. Every proposal has provenance, counterevidence, a risk tier, validation evidence, and an undo story.
9. Every session and RunEvent is attributable to an exact harness generation.
10. External model processing is local-only by default; raw personal evidence never leaves the machine silently.

### Non-goals

- Whole plugin source stored in Neo4j
- Silent SOUL rewrite
- Full graph weight recompute / bulk relation restructure as dream job
- Free-text AgentPolicy as system prompt
- Metrics that claim causal “truth” from free-form rates alone
- Maintenance models with raw Cypher, patch-apply, policy-activate, or Alias-activate authority
- Proposed overlays written directly into an auto-loaded plugin path
- Silent trials of behaviour changes
- Automatic semantic Alias activation, entity merge/delete, or correction journals
- Using the same evidence bundle as both patch-generation and sole evaluation data

---

## 3. Locked decisions

| Question | Choice |
| --- | --- |
| Spec location | This file under `docs/superpowers/specs/` |
| Harness source of truth | Reviewed Git core files + operator-controlled exact-digest trial manifest |
| Sensors | Feedback + **RunEvent** (success/fail/approach) |
| Online learning | Propose + host/operator-confirmed typed effect; textual “yes” is not authority |
| Offline | Maintenance digests sensors → typed proposals; only deterministic retention may auto-apply |
| Harness upgrade mechanism | Quarantined **diff / learned overlay** (+ optional structured policy) |
| Overlay activation | Active manifest lists reviewed file digests; file presence never activates |
| Soft policy | Immutable structured JSON revision + CAS active slot; files and locked rules win |
| Life journals | `append_journal_entry` only |
| Semantic memory automation | No unattended Alias/dispute/correction-journal apply |
| Proposal approval | Exact proposal id + base fingerprint + artifact/effect hash |
| External critics | Redacted/structured evidence only; explicit opt-in for anything richer |
| Evaluation attribution | Harness/plugin/MCP/model/policy versions captured from first sensor release |

### Rev 4 decisions superseded by rev 5

| Rev 4 wording | Rev 5 decision |
| --- | --- |
| Hybrid graph + JSONL is the practical default | One authoritative typed event write; optional short-lived spool only after measured need |
| `learned/<topic>.md`; core includes if present | Proposals stay outside load paths; reviewed digest must appear in active manifest |
| Hard-confirm text applies an Alias | Text expresses intent; a separately permissioned typed tool performs the exact reviewed effect |
| Rare correction journals may be applied in maintenance | Never unattended; missing memory remains an owner-confirmed normal append |
| Gated auto-Alias in v2 | Deferred indefinitely; only deterministic, non-semantic housekeeping auto-applies |

---

## 4. Sensors — mistakes, successes, gotchas

User Feedback is necessary but **not sufficient**. Self-evolution needs operational telemetry.

### 4.1 Feedback (human)

| kind | Example | Typical promote |
| --- | --- | --- |
| `entity_wrong` | “not CarPlace — CarID” | Alias Proposal → reviewed typed effect |
| `claim_false` | “that never happened” | Evidence/proposal only until Claim provenance exists |
| `miss` | “you forgot EPAM Dec” | Owner-confirmed normal life WRITE |
| `invent` | “you made that up” | Session blocklist; later grounding skill/policy |
| `praise` | “exactly”, “👍” | **Counter only** — not a life journal |

Feedback is an immutable observation, not a one-row workflow. Its lifecycle
uses orthogonal fields:

```text
Feedback {
  id, kind, created_at, source_turn_ref?, sensitivity,
  raw_payload_ref?, raw_hmac?, hmac_key_version?, redacted_summary?
}

FeedbackLifecycleEvent {
  id, feedback_id,
  event: triaged | closed | dismissed | revoked |
         redacted | archived | purged,
  actor, created_at, reason_code?
}
```

One Feedback item may support multiple proposals or no proposal. Proposal and
effect state never live inside `Feedback.status`. Triage, consent, and retention
statuses are derived projections over append-only FeedbackLifecycleEvent
records. Revocation excludes the item from future evidence snapshots and
triggers review of still-active derived changes through explicit provenance
links.

Removable raw text lives in a separate `Operational:QualityPayload` record
referenced by `raw_payload_ref`; the immutable Feedback observation retains only
bounded metadata and an optional keyed HMAC. Redaction/purge removes the payload
through a dedicated receipted transaction without rewriting the observation.

### 4.2 RunEvent (machine) — mistakes **and** successes

Every meaningful tool/approach outcome can emit a compact event:

```text
RunEvent {
  id, schema_version, observed_at, ingested_at,
  trace_id?, attempt_id?, session_ref?, host?,
  harness_generation_id,         // required foreign key
  route: SKIP|READ|WRITE|FEEDBACK|MAINTAIN,
  recurrence_key?, taxonomy_version,
  approach?: string,          // advisory label, e.g. "vector_only"
  tool: string?,              // append_journal_entry, read_neo4j_cypher, ...
  tool_outcome: success | fail | empty | conflict | timeout,
  task_outcome?: success | fail | corrected | unknown,
  outcome_source: mcp | host | user | model_advisory,
  error_class: string?,       // chain_conflict, embed_down, no_hits, ...
  decision_point?: string, eligible_exposure?: bool,
  entity_refs: [], journal_refs: [],
  latency_ms?: number,
  redacted_summary?: string,
  sensitivity: public_ops | personal | intimate,
  harness_revision?, plugin_version?, policy_digest?,
  mcp_version?, model_id?         // denormalized diagnostics
}
```

`tool_outcome` is recorded by deterministic host/MCP code where available.
`task_outcome` comes from user feedback or an explicit host rubric. A
model-authored “success” is advisory and cannot by itself justify a proposal.
Approach labels are versioned taxonomy annotations, not a reward oracle.

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
- Capture eligible decision points and version attribution from the first telemetry release.
- Evidence bundles include failures, successes, counterexamples, and exposure denominators.
- Model-advisory events may help triage but cannot be sole proposal evidence.
- Operational labels are excluded through one shared rule, not repeated ad hoc allow/deny lists.

### 4.4 RunEvent storage contract

Rev 5 starts with bounded structured `:RunEvent` records written once through a
dedicated MCP tool. Events use stable ids, uniqueness constraints, retention,
and no raw intimate transcript by default.

If measured volume later requires a spool:

- path: `$DIGITAL_BRAIN_STATE_DIR/run-events/` or the XDG state directory,
- short-lived rotated segments, never a repository path,
- stable event ids and segment/hash/offset snapshot manifests,
- asynchronous idempotent promotion to the authoritative ledger,
- partial-line recovery and explicit purge semantics,
- no synchronous graph + file dual-write.

---

## 5. Store grades

| Grade | Content | Lifetime in context |
| --- | --- | --- |
| **Life memory** | Journal chain, entities, relations | Forever (append-only) |
| **Ops learning** | Alias revisions, LearningLog, EffectReceipt | Durable, small |
| **Quality evidence** | Feedback, RunEvent, EvidenceSnapshot | Hot then redacted/archived |
| **Control plane** | DreamRun, Proposal, EvaluationReceipt, lease | Durable metadata, bounded detail |
| **Harness** | Skills, SOUL, overlays, soft policy | Files (+ optional policy nodes) |

---

## 6. Wake path (buddy)

Routes: `SKIP` | `READ` | `WRITE` | `FEEDBACK`

| Route | Graph | Telemetry |
| --- | --- | --- |
| SKIP | none | optional low-priority RunEvent |
| READ | read only | RunEvent success/empty/fail + approach |
| WRITE | append + MERGE links | RunEvent + entity-check result |
| FEEDBACK | `create_feedback`; optionally create a typed Proposal | Feedback + decision outcome |

### FEEDBACK online loop

1. Intent gate (pending proposal **or** grounded correction cue; prefer silent park).
2. `create_feedback` writes an immutable hot observation idempotently.
3. Create a typed one-line Proposal or park the observation for maintenance.
4. Show exact scope, evidence band, effect hash, blast radius, and undo path.
5. The user may express intent with an exact stable token such as `APPLY alias:<proposal_id>`.
6. A separately permissioned host/operator confirmation mints a single-use, expiring authority bound to proposal id, effect hash, target, before fingerprint/base, and approver.
7. The typed operator effect interface validates and consumes that authority atomically.
8. The server verifies target state and writes an EffectReceipt + LearningLog, or returns stale/conflict without mutation.

Generic “yes”, “ok”, or 👍 never activates identity, pinned-entity, permanent
harness, policy, or SOUL changes. Max one confirmation prompt per user turn.
Praise remains an observation/counter only.

### Identity safety (P0)

- No auto `DETACH DELETE` merge on wake.
- Dupe candidates = report only until gated merge.
- Alias maps directly to a validated canonical id; no Alias-to-Alias chains.
- Alias has namespace/entity type, normalized source, revision, provenance, active/revoked state, and uniqueness.
- Unalias is a compensating revocation receipt, never history deletion.
- Pinned entities require elevated host confirmation.
- `claim_false` remains propose-only until a Claim/Assertion provenance model identifies the exact disputable fact.

---

## 7. Maintenance (dreams) — compiler and review loop

**Product language:** memory hygiene / maintenance run.
**Internal:** DreamRun.

### Triggers

- Primary: manual `/digital-brain-dream run` during rollout
- Scheduled weekly local window only after explicit owner opt-in
- Heartbeat trigger is off by default and deferred until scheduled operation is trustworthy

### Constraints

- Exclusive fenced **lease** (one host/run/epoch)
- Budget: max sensors, max proposals, tokens/RAM
- Unattended: analysis/proposal-only except deterministic owner-configured retention
- Model workers may read sanitized snapshots and emit typed Finding/ChangeIntent output only
- Only the deterministic coordinator/compiler may write quarantine artifacts
- Raw evidence is untrusted input; it cannot supply instructions or tool permissions
- External providers receive structured/redacted metadata only unless explicitly opted in
- Every stage is resumable/idempotent and writes a receipt
- Report: applied safely / waiting for owner / deliberately left alone

### 7.1 Dream state machine

```text
queued → leased → snapshotting → normalizing → clustering → planning
       → compiling → validating → publishing → completed

terminal: failed | aborted | lease_lost
```

`publishing` means publishing immutable proposal metadata/artifacts to the
review inbox, not activating them. Each stage has an idempotency key, input
digest, output digest, start/finish timestamps, and resume rule.

The evidence snapshot freezes:

- input event ids and graph bookmark/watermark,
- harness revision, plugin/MCP/model/policy versions,
- taxonomy/compiler/evaluator versions,
- source counts, sensitivity ceiling, and redaction mode,
- base Git commit for any harness proposal.

Revocation after snapshot does not rewrite history; it marks the snapshot and
derived proposals invalid/stale for future decision or activation.

### 7.2 Lease and fencing

```text
MaintenanceLease {
  key, holder_id, run_id,
  epoch, lease_until, heartbeat_at
}
```

Acquire/renew/release are authenticated non-model coordinator transactions using database
time. Every control-plane transition and automatic retention effect validates
the current `run_id + epoch`. Acquisition after expiry increments the monotonic
epoch, so a stale worker cannot continue mutating after losing its lease.

Filesystem artifacts are immutable under `dream_id/epoch`. The graph lease does
not itself fence arbitrary filesystem writes, so only the current epoch may
publish an artifact manifest and no maintenance worker may write a deployable
plugin path. A stale worker may leave an unreferenced orphan in its epoch
quarantine; review/runtime ignores every artifact lacking a current-epoch
published control-plane record.

### 7.3 Output lanes and gates

| Lane | Outputs | Unattended gate |
| --- | --- | --- |
| Housekeeping | Sensor digest, retention, redaction/purge receipt | Only deterministic owner-configured policy |
| Memory | Alias/revoke Alias, scoped dispute, missing-memory suggestion | Proposal only; exact host-confirmed effect |
| Behaviour | Learned overlay, core skill diff, structured policy revision | Quarantine + eval + owner-approved trial/merge |
| Engineering | Code/infra diagnosis, issue, test or patch artifact | Normal reviewed repo workflow |

**Never automatic:** correction journals, semantic Alias/dispute activation,
entity merge/delete, global weight recompute, bulk relation rewrite, schema
changes, policy activation, or SOUL edits.

### 7.4 Proposal model

```text
Proposal {
  id, dream_id?, kind, title,
  status_projection: draft | validated | review_pending | approved | rejected |
                     stale | invalid | superseded | withdrawn,
  target_ref, scope, risk_tier, reversibility,
  evidence_snapshot_id, evidence_strength,
  evidence_summary_json, counterevidence_json,
  sensitivity_max, expected_outcome,
  before_fingerprint?, proposed_effect_hash?, artifact_ref?,
  trial_json?, created_at, expires_at?
}
```

`status_projection` is a cached/read projection. EvaluationReceipt, Decision,
EffectReceipt, and Deployment records are authoritative for validation,
approval/rejection, application, and effectiveness.

Approval binds the exact `proposal_id + before_fingerprint + effect/artifact
hash`. Activation additionally requires a single-use, expiring authority from
the host/operator confirmation boundary. A changed target or base commit makes
the approval stale. Rejected proposals do not reappear without materially new
evidence for the same finding/recurrence key; unrelated changes elsewhere in a
snapshot do not unsuppress them.

Evidence strength uses explainable bands (`tentative | moderate | strong`), not
pseudo-scientific confidence percentages. The card reports recurrence,
distinct sessions, recency, eligible exposure count, agreement, counterexamples,
and maximum sensitivity.

### 7.5 Effect and evaluation receipts

```text
EffectReceipt {
  id, effect_key, request_hash, proposal_id, dream_id?,
  effect_type, actor, authority_digest?, fence_epoch?,
  before_ref, after_ref?,
  outcome: applied | replayed | conflict | stale | failed | reverted,
  verification_status, applied_at, undo_ref?, reverted_at?
}

Decision {
  id, proposal_id,
  decision: approved | rejected | deferred | withdrawn,
  proposal_hash, target_ref, before_fingerprint,
  artifact_or_effect_hash, decided_by, decided_at,
  reason_code?, expires_at?
}

ActivationAuthority {
  id, decision_id, proposal_id,
  proposal_hash, target_ref, before_fingerprint,
  artifact_or_effect_hash, approver,
  status: minted | consumed | expired | revoked,
  minted_at, expires_at, consumed_at?,
  nonce_digest, request_fingerprint, consumption_receipt_id?
}

EvaluationReceipt {
  id, proposal_id, evaluator_version,
  baseline_ref, candidate_ref, fixture_snapshot,
  target_results, guardrail_results,
  privacy_result, invariant_result,
  outcome: passed | failed | inconclusive,
  created_at
}
```

Same effect key + same request hash returns `replayed`; the same key + a
different hash returns `conflict`. Verification and receipt creation occur in
the same transaction as graph effects where possible.

Authority lifecycle uses operator-only `mint_activation_authority`,
`get_activation_authority_receipt`, and atomic consume-with-effect interfaces.
Minting and consumption both have idempotency receipts. If a response is lost
after consumption, reconciliation returns the linked EffectReceipt; it never
mints or consumes a second authority for the same request implicitly.

### 7.6 Harness patch compiler

The maintenance model does not author arbitrary deployable Markdown. It emits
a schema-validated `ChangeIntent`:

```text
ChangeIntent {
  proposal_id, target_skill, extension_slot, operation,
  rule_id, evidence_ids, counterevidence,
  expected_outcome, risk_tier
}
```

A deterministic compiler renders an overlay/diff from managed templates and
named extension slots. Every `PatchArtifact` records:

- proposal id and evidence snapshot digest,
- base commit plus per-file before hashes,
- compiler/schema versions,
- target path allowlist,
- patch SHA-256 and immutable artifact path,
- validation and evaluation receipts,
- expected plugin generation and rollback reference.

Early phases allow overlays only. Core skill/agent diffs and engineering patches
come later through the normal Git workflow.

Hard patch gates:

- candidate artifacts live under runtime state, never `plugins/.../learned/`,
- no SOUL, scripts, hooks, manifests, MCP config, secrets, symlinks, path
  traversal, deletes, executable-bit changes, or arbitrary include paths,
- locked safety rules cannot be overridden,
- size/file-count limits and deterministic conflict detection,
- isolated-worktree validation and fixed repository-owned test commands,
- stale base stops; no automatic rebase or three-way merge,
- approval binds exact base + patch hash,
- validation reruns after apply,
- activation requires reviewed Git content or an approved active-manifest digest.

Approved overlays are additive within named slots and have globally unique rule
ids. Conflicting rules are rejected; “files win on conflict” is not a runtime
resolution algorithm.

After approval, the operator copies a trial artifact to
`$DIGITAL_BRAIN_STATE_DIR/dreams/active-overlays/<proposal-id>/<digest>.md` and
atomically updates the exact-digest manifest in that active directory. The
runtime never loads quarantine. Permanent behavior moves into reviewed Git
content; runtime trial files never become an alternative permanent source tree.

### 7.7 Trial, deployment, and rollback

Harness approval creates one of three clearly reported states:

- `drafted`: artifact exists only in quarantine,
- `trial_active`: owner approved a time/exposure-bounded generation,
- `deployed`: reviewed content was merged/reloaded and the host proved the new generation was loaded.

Every session pins one generation. Trials expire after a time or eligible
exposure budget; they never silently become permanent. The observation window
compares recurrence per eligible decision point against the prior generation
and checks guardrails. Regression produces a rollback recommendation; an owner
may immediately disable an overlay or restore the prior manifest/commit.

### 7.8 How MD harness improves (even though skills are files)

```text
1 Sensors (Feedback + RunEvent success/fail/gotcha)
2 Maintenance clusters failure classes + success gotchas
3 Create typed ChangeIntent
4 Deterministically compile into quarantined artifact
5 Run privacy, static, invariant, targeted replay, and regression gates
6 Owner reviews exact diff/effect + evidence + counterevidence + undo
7 Activate a bounded trial or merge reviewed Git content
8 Next session pins/reports the new generation
9 Observe eligible exposures → retain or roll back
```

Files are the **deployable harness**. Neo4j is the bounded evidence/control
plane. Runtime state is proposal quarantine. Maintenance is the compiler from
evidence to evaluated, reviewable change artifacts.

### 7.9 Precedence

1. Executable MCP/write guards and locked safety contract
2. SOUL and reviewed core skill/agent files
3. Active-manifest overlays in named extension slots
4. Active structured AgentPolicy slot (named enum/numeric knobs only)
5. Model default

No lower layer may weaken a higher layer. The session records the hashes and
versions of every active layer in its harness-generation receipt.

---

## 8. Capability and write matrix

### 8.1 Capability separation

| Role | May | Must not |
| --- | --- | --- |
| Recorder | Append typed Feedback/RunEvent | Edit prior evidence or create effects |
| Coordinator | Acquire fenced lease, snapshot, checkpoint stages | Interpret raw evidence as authority or activate changes |
| Analyzer/model | Read sanitized snapshot; emit Finding/ChangeIntent | Generic graph write, raw patch apply, Alias/policy activation |
| Deterministic compiler | Render allowlisted artifact in quarantine | Write deployable plugin paths |
| Evaluator | Read fixtures/artifacts; write EvaluationReceipt | Approve or activate |
| Reviewer | Approve/reject exact hash and scope | Mutate graph/files implicitly through prose |
| Operator/activator | Invoke dedicated reviewed effect/deployment tools | Change target/hash/base after approval |
| Runtime | Load one active version-pinned generation | Scan arbitrary proposal directories |

The model-facing MCP surface contains sensor recorder/read tools only; it never
registers coordinator, decision, authority, effect, deployment, or operator
transactions. Maintenance and evaluator agents receive no
`write_neo4j_cypher`, patch-apply, policy-activate, Alias-activate, or SOUL-write
capability.

### 8.2 Artifact/write matrix

| Node / artifact | Authoritative path | Notes |
| --- | --- | --- |
| JournalEntry | `append_journal_entry` only | Server embedding + chain CAS |
| Feedback | `create_feedback` / `revoke_feedback` | Typed, idempotent, never journal-indexed |
| RunEvent | `record_run_event` or trusted internal recorder | Source and schema validated |
| DreamRun / snapshot / Finding / Proposal | Authenticated non-MCP coordinator API | Fenced transitions and stable ids |
| Lease | Authenticated coordinator acquire/renew/release | Database-time CAS + monotonic epoch |
| Alias revision / revoke | Dedicated host-confirmed operator interface | Target validation + EffectReceipt |
| AgentPolicy revision / active slot | Dedicated operator tools | Immutable revision + slot CAS |
| PatchArtifact | Runtime-state quarantine | Immutable; never auto-loaded |
| Approved trial overlay | `$DIGITAL_BRAIN_STATE_DIR/dreams/active-overlays/` + manifest | Hash-pinned, expiring; next session proves load |
| Permanent overlay/diff | Reviewed Git content | Plugin version/reload required |
| SOUL | Explicit owner edit only | Never a DreamRun output |

Generic `write_neo4j_cypher` remains for allowlisted idempotent life-graph links.
It must reject direct mutation of protected quality/control labels just as it
rejects protected journal-chain state.

---

## 9. Schema sketch (additions)

All quality/control nodes carry an `Operational` label in addition to their
specific label. BOOTSTRAP, heavy-node, vector, and default export queries exclude
`Operational` centrally.

### 9.1 EvidenceSnapshot and Finding

```text
(:Operational:Feedback {
  id, kind, created_at, source_turn_ref?, sensitivity,
  raw_payload_ref?, raw_hmac?, hmac_key_version?, redacted_summary?,
  request_fingerprint
})

(:Operational:FeedbackLifecycleEvent {
  id, feedback_id, event, actor, created_at,
  reason_code?, request_fingerprint
})

(:Operational:QualityPayload {
  id, owner_evidence_id, payload_text,
  sensitivity, created_at, expires_at?
})

(:Operational:EvidenceSnapshot {
  id, dream_id, cutoff_at, created_at,
  source_ids_digest, source_counts_json,
  redaction_policy_version, sensitivity_max,
  harness_generation_id, taxonomy_version,
  graph_bookmark?, base_commit?
})

(:Operational:Finding {
  id, dream_id, snapshot_id, class_key,
  lane, summary, evidence_strength,
  support_counts_json, counterevidence_json,
  created_at
})
```

Feedback id and lifecycle-event id are unique. Stable id + same canonical
request fingerprint replays; stable id + different fingerprint conflicts.
QualityPayload has a unique owner and is reachable only through dedicated
quality reads/retention—not default graph retrieval.

Provenance uses explicit relationships such as `(:Proposal)-[:SUPPORTED_BY]->
(:Finding)` and `(:Finding)-[:USES_EVIDENCE]->(:Feedback|:RunEvent)`. A sensor
may participate in multiple attempts/runs; there is no single
`absorbed_by_dream_id` property.

The exact frozen set is represented by
`(:EvidenceSnapshot)-[:INCLUDES_EVIDENCE {role, evidence_hash}]->
(:Feedback|:RunEvent)`, where `role` is `generation | counterevidence |
holdout`. The analyzer projection excludes holdout membership/content. The
snapshot digest is a summary/check, not a replacement for auditable membership.

### 9.2 DreamRun

```text
(:Operational:DreamRun {
  id, owner_status, stage, attempt,
  holder_id?, lease_epoch?,
  started_at, finished_at?,
  processing_mode, input_digest?, output_digest?,
  harness_generation_id, base_commit?,
  reviewed_count, auto_applied_count, suppressed_candidate_count,
  metrics_json, error_class?
})
```

`owner_status`: `scheduled | running | needs_review | completed_clean |
completed_partial | failed | cancelled | lease_lost`.

### 9.3 Proposal, receipts, deployment

Proposal and receipt property contracts follow §7.4–7.5. Additional deployment
state:

```text
(:Operational:HarnessGeneration {
  id, core_commit, core_tree_digest, dirty_state_digest,
  plugin_version, soul_sha,
  overlay_manifest_digest, policy_digest,
  mcp_version, model_id?,
  schema_version, taxonomy_version, created_at
})

(:Operational:Deployment {
  id, proposal_id, generation_id,
  status: drafted | trial_active | deployed | expired | rolled_back,
  activated_at?, retired_at?, rollback_ref?
})

(:Operational:ExposureWindow {
  id, deployment_id, decision_point,
  eligible_target, eligible_seen,
  started_at, ends_at,
  recurrence_count, counterevidence_count,
  guardrail_json, effectiveness_status
})
```

### 9.4 Alias revision

```text
(:Operational:Alias {
  id, namespace, entity_type,
  normalized_from, display_from,
  canonical_id, canonical_name,
  revision, status: active | revoked,
  proposal_id, effect_receipt_id,
  confirmed_by, created_at, revoked_at?
})

(:Operational:EntityProtection {
  entity_id, protection_level: pinned,
  revision, reason_code, set_by,
  created_at, revoked_at?
})
```

There is at most one active Alias per `(namespace, entity_type,
normalized_from)`. The canonical target must exist and match type. Alias targets
are canonical entities, never other Alias nodes. A pinned source or target
requires an ActivationAuthority whose reviewed scope explicitly includes
`pinned_identity`; the protected EntityProtection record is itself changed only
through an operator-only revisioned effect.

### 9.5 AgentPolicy

```text
(:Operational:AgentPolicyRevision {
  key, version, domain, body_json,
  schema_version, source_proposal_id,
  created_at, expires_at?
})

(:Operational:PolicySlot {
  key, active_version, slot_version, updated_at,
  effect_receipt_id
})
```

Policy bodies contain only schema-approved enums/numerics. Activation is a CAS
update of `PolicySlot`; there are not multiple mutable `active=true` nodes.

---

## 10. Evaluation and process health

### 10.1 Pre-activation gates

Every behaviour proposal passes:

1. schema and allowlist validation,
2. secret/personal-data and prompt-injection scan,
3. locked-rule and conflict checks,
4. sanitized targeted replay on the affected route,
5. holdout scenarios not used to generate the change,
6. repository invariant/regression scenarios,
7. baseline-vs-candidate comparison,
8. fixed trial eligibility and rollback thresholds.

Model disagreement is a reason to escalate, not proof. Multi-model critics may
improve coverage but never substitute for deterministic gates or owner review.

### 10.2 Metrics

| Metric | Role |
| --- | --- |
| `open_feedback_count` / unresolved high-signal events | Anti-sink |
| `correction_to_resolution_time` | User-visible responsiveness |
| `error_recurrence_per_eligible_exposure` by generation | Post-change process signal |
| `tool_fail_rate` by stable `error_class` and version | Infra/procedure health |
| `proposal_stale_rate` | Patch pipeline health |
| `guardrail_regression_rate` | Change safety |
| `applied_change_receipt_coverage` | Auditability; target 100% |
| `rollback_time` / `rollback_success` | Regret-path health |
| `privacy_gate_failure_count` | Safety guardrail; target 0 dispatches |
| free-form `feedback_rate` / proposal acceptance | Descriptive trend only |

Do not optimize `success_gotcha_promote_rate` or proposal acceptance. Those
metrics reward unnecessary rule creation. Exposure denominators, generation
attribution, and stable taxonomies ship with the first RunEvent schema.

---

## 11. Privacy, retention, and external processing

Processing modes:

| Mode | Behaviour |
| --- | --- |
| `local_only` | Default; no external quality evidence dispatch |
| `metadata_external` | Explicit opt-in; structured/redacted counts and synthetic fixtures only |
| `external_opt_in` | Per-run explicit approval for a reviewed packet; still no raw journal dump |

Rules:

- Sensitivity tiers apply to Feedback, RunEvent, snapshots, findings, proposals, and reports.
- Raw personal/intimate fields are excluded from analyzer context by default.
- External dispatch uses an auditable packet and fails closed if intimate fields remain.
- Raw hashes use a local keyed HMAC when correlation is needed; do not retain low-entropy plain-text hashes.
- Redaction clears all raw payload fields; archive is not merely a status label.
- Purge/redaction is receipted and intentionally not advertised as reversible.
- `revoke_feedback` excludes evidence from future snapshots and marks directly derived pending changes stale.
- Revocation never rewrites append-only life journals or unrelated proposals silently.
- Dream summaries contain counts, ids, risk, and processing boundary—not intimate quotes.
- Shared/non-owner sessions cannot inspect intimate proposal queues or derived policy.
- Backups/exports document whether redacted historical raw data may still exist.

---

## 12. Product experience and command surface

Default next-private-session recap:

```text
Maintenance complete. I reviewed 42 quality signals, archived 18 expired
operational records, and left 3 ambiguous patterns alone. Two proposals are
ready. No identity or buddy behaviour changed without you.
```

Each proposal answers: what changes, why now, strength/counterevidence, blast
radius, trial/verification plan, and undo path. Raw evidence is progressively
disclosed only on request in a verified private/local session.

```text
/digital-brain-dream run
/digital-brain-dream status
/digital-brain-dream review [proposal-id]
/digital-brain-dream show <proposal-id>
/digital-brain-dream try <proposal-id>
/digital-brain-dream apply <proposal-id>
/digital-brain-dream defer|reject <proposal-id>
/digital-brain-dream undo <receipt-id>
/digital-brain-dream history
/digital-brain-dream privacy
```

Modes:

- **Report-only:** default rollout; no effects.
- **Guided maintenance:** run now, then review one proposal at a time.
- **Trusted housekeeping:** explicit opt-in for deterministic retention only.

A warmer “I dreamed about the week” voice may be an opt-in presentation layer,
but it never changes processing, privacy, or authority.

---

## 13. Acceptance criteria and kill criteria

1. No wake, dream, or legacy ADK path automatically merges/deletes identity nodes.
2. Generic MCP writes continue rejecting DELETE/DETACH/REMOVE and protected quality/control mutations.
3. BOOTSTRAP/heavy-node/vector/default-export paths return no `Operational` nodes or sensor raw text.
4. Feedback and RunEvent writes never create JournalEntry nodes or enter the journal vector index.
5. An analyzer using all of its available tools cannot activate Alias, policy, overlay, code, or SOUL changes.
6. A proposal written to quarantine has zero runtime effect.
7. Approval/application fails on changed hash, target, effect, base, expired authority, or stale state.
8. Replaying the same approved effect yields one effect and a replay receipt; changed payload conflicts.
9. A crash after each DreamRun stage resumes without duplicate effects; a stale lease epoch cannot transition authoritative graph state, apply retention, or publish an artifact manifest. Orphan quarantine files remain inert.
10. Alias activation rejects missing/wrong-type targets, conflicting active aliases, Alias targets, and unauthorized pinned targets.
11. Unattended maintenance can perform only owner-configured deterministic retention.
12. Personal/intimate archival removes raw payloads from normal reads/exports; reports contain no quotes.
13. Every session and RunEvent carries the exact harness generation.
14. A behaviour proposal cannot pass evaluation using only its generation evidence.
15. Every trial has eligibility, duration, rollback thresholds, a receipt, and an artifact-specific undo path.
16. Approval, application, deployment/exposure, and effectiveness are reported separately.
17. External dispatch rejects a fixture/packet containing intimate or unapproved raw fields.
18. `claim_false` cannot mutate life memory until a Claim/Assertion provenance model exists.
19. Engineering failures route to engineering proposals and cannot trigger semantic memory mutation.
20. Rejected proposals stay suppressed until materially new evidence changes the same finding/recurrence key; unrelated new events do not unsuppress them.
21. Missing, tampered, conflicting, or expired active-manifest entries fail closed to the prior known-good/no-overlay generation; an existing session remains pinned.
22. Meaningful READ empty/fail and WRITE conflict/timeout paths emit source-attributed RunEvents carrying the session's pinned harness generation.

**Kill criteria:** free-text policy as system prompt; textual ack as activation
authority; auto Alias/merge/delete; correction journals from maintenance;
sensors in journal vectors; silent SOUL rewrite; proposed artifacts in load
paths; evaluator or dream worker with activation tools; external raw intimate
dispatch; unbounded evidence without retention; missing receipt/rollback.

---

## 14. Phased rollout

| Phase | Deliverable | Activation ceiling |
| --- | --- | --- |
| **v0.0** | Remove legacy auto-merge invocation; report-only duplicate candidates; amend graph contract | None |
| **v0.1** | Shared `Operational` exclusion; typed schemas; generic-write protection | None |
| **v0.2** | `create_feedback`/revoke + FEEDBACK route + private review primitives | Feedback only |
| **v0.3** | Versioned RunEvent and generation receipts; deterministic source separation | Evidence only |
| **v0.4** | Dedicated Alias proposal/effect/revoke tools + receipts | Host-confirmed identity effect |
| **v0.5** | Read-only manual DreamRun: fenced lease, frozen snapshot, digest/report | Retention disabled |
| **v0.6** | Owner-configured deterministic retention/redaction | Housekeeping only |
| **v0.7** | Proposal/Finding/Evaluation pipeline + engineering lane | Proposal only |
| **v0.8** | Quarantined overlay compiler + invariant/holdout evaluation | No load/activation |
| **v0.9** | Owner-approved, expiring overlay trial + generation pin + rollback | Approved trial |
| **v1.0** | Reviewed Git merge/reload proof, history, full privacy/undo UX | Permanent reviewed harness |
| **Later** | Core diffs, policy slots, optional Claim model, measured spool need | Separate design approval |

Scheduled weekly maintenance is enabled only after v0.6 is stable and explicit
owner consent exists. Heartbeat remains off until a later design review.

---

## 15. FAQ

| Question | Answer |
| --- | --- |
| Where is the spec? | **This file** under `docs/superpowers/specs/` |
| What about the HTML? | Rev 4 visual companions under `tmp/` are now stale and remain non-authoritative |
| Self-evolving how? | Evidence → findings → evaluated proposals → owner/operator activation → generation-pinned observation |
| Can a dream change memory? | It can propose; only a typed, separately confirmed effect can change semantic memory |
| Can a dream edit skills? | It can compile a quarantined artifact; presence never loads it |
| Only Feedback? | No—deterministic tool/host events plus user outcomes and counterevidence |
| Why not trust success events? | Model-authored success is a self-reward loop; only deterministic/user/host outcomes are proposal-grade |
| Why no auto-Alias? | Identity mistakes have high semantic blast radius and cheap owner confirmation |
| What if the failure is code/infra? | It becomes an engineering proposal, not a memory or prompt mutation |
| Can Grok/another model criticise? | Yes, with explicit processing mode and an auditable redacted packet only |

---

## 16. Implementation and approval history

Execution plan:
`docs/superpowers/plans/2026-07-10-self-evolving-quality-dreams.md`.

| Rev | Note |
| --- | --- |
| 1–2 | Direction OK (“I like it so far”) |
| 3 | Critics panel amendments |
| 4 | Sensors beyond Feedback; harness file evolution; explicit dual path |
| 5 | Maintenance compiler; capability separation; evidence/proposal/receipt lifecycle; quarantine; trials/rollback; four output lanes |

**Next:** review and approve the saved execution plan. Implementation begins
only after plan approval and follows the phase gates above.
