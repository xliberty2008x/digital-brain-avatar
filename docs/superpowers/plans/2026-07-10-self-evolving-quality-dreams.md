# Self-Evolving Quality + Maintenance (Dreams) Implementation Plan

> **Status:** Proposed execution plan for design rev 5. Do not implement later
> phases until the preceding phase gate and human review pass.

**Goal:** Add a trustworthy quality loop to `avatar_digital_brain`: immutable
human/machine evidence, version attribution, read-only maintenance runs,
evaluated change proposals, owner/operator-mediated effects, quarantined harness
artifacts, bounded trials, and rollback—without giving a maintenance model the
ability to activate its own changes.

**Architecture:** Neo4j is the single authoritative bounded quality/control
ledger behind typed server-owned transactions. Reviewed Git is the core and
permanent harness source; an operator-controlled exact-digest runtime manifest
is the only bounded trial source. Runtime state outside the repo separates
immutable proposal quarantine from the active-trial directory.
The maintenance model sees sanitized frozen snapshots and emits typed findings
or `ChangeIntent`; deterministic code owns leases, state transitions, patch
rendering, validation, receipts, activation, and rollback.

**Tech stack:** Python 3.12, FastMCP, Neo4j Enterprise/Cypher, Pydantic, pytest,
Markdown/YAML plugin assets, Git worktrees, optional Grok headless critic over
redacted fixtures only.

**Source design:**
`docs/superpowers/specs/2026-07-10-self-evolving-quality-dreams-design.md`

---

## Locked implementation decisions

1. **One ops ledger:** structured Feedback, RunEvent, DreamRun, snapshot,
   Finding, Proposal, receipt, and lease records are authoritative in Neo4j.
   Do not add an authoritative SQLite or JSONL copy in this plan. A volume spool
   requires a later measured-need design and is never a synchronous dual-write.
2. **Separate credentials/capabilities:** generic runtime Cypher cannot mutate
   `Operational`, Alias, policy, or receipt records. Typed quality tools use a
   constrained quality role. Alias/policy/harness activation uses an operator
   boundary not exposed to maintenance/model toolsets.
3. **Raw payload separation:** immutable evidence metadata is separate from
   removable raw payload nodes/properties so redaction does not rewrite the
   observation or its request fingerprint.
4. **Plugin is the first product surface:** the ADK runtime receives the P0
   safety fix and shared filtering/telemetry adapters, but full FEEDBACK/dream
   conversational parity is not required before the plugin flow ships.
5. **Version first:** `HarnessGeneration` exists before the first RunEvent is
   accepted. Version attribution cannot be reconstructed later.
6. **No proposal-by-presence:** nothing under quarantine is scanned or loaded.
   Only reviewed Git files and an exact-digest active manifest affect sessions.
7. **No silent semantic automation:** unattended work may analyze, propose, and
   run explicitly configured deterministic retention only.

## Global safety constraints

- Keep `append_journal_entry` as the only JournalEntry creation path.
- Keep generic DELETE/DETACH/REMOVE rejection.
- Never send personal graph, journal, SOUL, `.env`, credentials, backups, or raw
  intimate Feedback to Grok or another external provider.
- Dream/analyzer/evaluator agents get explicit tool allowlists and no Bash,
  Edit, generic graph write, activation, or network tools.
- A prompt/tool allowlist is defense in depth; Neo4j roles and server-owned
  typed transactions enforce the mutation boundary.
- Every write-like operation has a stable key, canonical request fingerprint,
  replay/conflict outcome, and reconciliation read.
- Every phase adds tests before adding capability.
- Stop rather than silently weaken a phase gate.

## Baseline and standard commands

Canonical full-suite command (Task 0 hermetic baseline):

```bash
uv run --group dev python -m pytest tests/ -q
```

Verified: `70 passed @ 872f5f5`; Python 3.12.7;
`uv run --group dev python -m pytest tests/ -q`.

Historical (pre–Task 0) collection depended on an explicit path injection:

```bash
PYTHONPATH="$PWD" uv run pytest tests/ -q
# 70 passed at plan authoring time; superseded by the hermetic command above
```

Full integration remains:

```bash
bash scripts/run-journal-e2e.sh
```

## Milestones

| Milestone | Tasks | Capability ceiling |
| --- | --- | --- |
| A · Safe evidence | 0–4 | Evidence recording only |
| B · Controlled memory + read-only dreams | 5–8 | Host-confirmed Alias; retention after opt-in |
| C · Evaluated harness learning | 9–11 | Owner-approved bounded overlay trial |
| D · Product/release hardening | 12–13 | Reviewed permanent harness deployment |

---

## Task 0: Make the test baseline hermetic

**Files:**

- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `scripts/__init__.py`
- Optional modify: pytest configuration in `pyproject.toml`

**Interfaces/outcome:** one repository-owned Python/pytest environment and one
canonical full-suite command. Do not begin schema or safety changes while test
collection depends on a globally installed namespace package.

- [x] Add a dev dependency group with compatible pytest and test-time packages.
- [x] Make `scripts` an explicit local package so
  `scripts.full_embedding_backfill` resolves deterministically.
- [x] Run the old explicit-PYTHONPATH command and record the baseline.
- [x] Run the new canonical command:

  ```bash
  uv run --group dev python -m pytest tests/ -q
  ```

- [x] Confirm the same tests collect under Python 3.12 and no global pytest path
  appears in the header.
- [x] Document the command in `README.md` only when stable.

**Gate:** stop if collection or interpreter selection is environment-dependent.

**Suggested commit:** `test: make the repository pytest baseline hermetic`

---

## Task 1: Remove every legacy automatic identity mutation

**Phase:** v0.0

**Files:**

- Modify: `digital_brain/agent.py`
- Modify: `digital_brain/services/consistency_checker.py`
- Modify: `digital_brain/services/__init__.py`
- Modify: `digital_brain/callbacks/query_sanitizer.py`
- Modify: `digital_brain/agents/retriever.py`
- Modify: `digital_brain/models/retriever_output.py`
- Modify: `docs/GRAPH_SCHEMA_CONTRACT.md`
- Create: `tests/test_consistency_checker.py`
- Create or extend: `tests/test_query_sanitizer.py`

**Interfaces/outcome:** `find_duplicate_candidates(...)` is read-only and
returns evidence/proposal inputs. There is no `merge_duplicate_nodes`,
`create_alias`, `MergeCommand`, or post-WRITE consistency mutation.

- [x] Write a test whose generic write/MCP mutation stub always raises; prove
  duplicate detection still returns candidates without calling it.
- [x] Write a characterization test proving current MCP DELETE rejection stays
  active.
- [x] Remove the post-WRITE reflex-loop call from `digital_brain/agent.py`.
- [x] Replace the consistency checker with report-only candidate discovery.
- [x] Rename mutation-shaped `MergeCommand` output to `DuplicateCandidate` or
  `AliasProposalInput`; remove `remove_id="NEW"` semantics.
- [x] Remove sanitizer/help text that recommends APOC merge or DETACH DELETE.
- [x] Rewrite Graph Contract Rule 4 to detect-and-propose only.
- [x] Run:

  ```bash
  uv run --group dev python -m pytest \
    tests/test_consistency_checker.py \
    tests/test_query_sanitizer.py \
    tests/test_local_mcp_query_tools.py \
    tests/test_journal_chain_guard.py -v
  rg -n "merge_duplicate_nodes|create_alias|DETACH DELETE remove|MergeCommand" \
    digital_brain docs/GRAPH_SCHEMA_CONTRACT.md
  ```

**Gate:** no wake, post-write, callback, prompt, or service path may merge,
delete, or create Alias automatically.

**Suggested commit:** `fix: make duplicate consistency checks report only`

---

## Task 2: Establish the Operational boundary and database roles

**Phase:** v0.1

**Files:**

- Create: `mcp_servers/cypher/src/digital_brain_mcp_cypher/quality.py`
- Create: `mcp_servers/cypher/src/digital_brain_mcp_cypher/quality_control_api.py`
- Modify: `mcp_servers/cypher/src/digital_brain_mcp_cypher/query_tools.py`
- Modify: `mcp_servers/cypher/src/digital_brain_mcp_cypher/server.py`
- Create: `scripts/init-quality-roles.cypher` or equivalent reviewed bootstrap
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `plugins/digital-brain-buddy/scripts/compose-up.sh`
- Modify: `digital_brain/services/core_entity_service.py`
- Modify: `digital_brain/services/recent_entries_service.py`
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-graph-mcp/references/runtime-patterns.md`
- Modify: session/read/graph skill exclusion language
- Create: `tests/test_operational_filtering.py`
- Extend: `tests/test_local_mcp_query_tools.py`
- Create integration coverage: `tests/e2e/quality_control_smoke.py`

**Interfaces/outcome:**

- Every quality/control node is labeled `Operational`.
- Generic model-facing Cypher is denied write access to `Operational`, Alias,
  policy, LearningLog, and receipt labels at both server validation and Neo4j
  role layers.
- Typed quality transactions use a separate quality driver/credential.
- Recorder/read operations are the only quality tools registered on the
  model-facing FastMCP surface.
- Coordinator operations use an authenticated non-MCP local control API; the
  coordinator secret is absent from analyzer/evaluator environments.
- Operator activation credentials are not mounted into the model-facing MCP.
- Retrieval excludes `Operational` centrally, with legacy exclusions kept until
  migration is complete.

- [x] Add failing lexical-guard tests for CREATE, MERGE, SET label, dynamic
  label/property, full replacement, and relationship-based access to protected
  control records.
- [x] Add a real Neo4j integration test showing the runtime role is denied even
  for a syntactic bypass not caught by a simple regex.
- [x] Implement the separate runtime/quality roles and deterministic bootstrap.
- [x] Add an authenticated local coordinator route with bounded typed payloads;
  never register lease, snapshot, proposal decision, authority, effect, or
  deployment operations as model-facing MCP tools.
- [x] Add a tool-list contract test proving those coordinator/operator names are
  absent from FastMCP discovery.
- [x] Add an idempotent constraint/bootstrap pattern analogous to
  `JournalStore.ensure_constraints()`.
- [x] Make `write_neo4j_cypher` reject protected quality/control mutations.
- [x] Add/backfill `Operational` on legacy Alias/LearningLog through an explicit
  reviewed migration; do not run it silently at session startup.
- [x] Update heavy-node/BOOTSTRAP/default export patterns to `NOT n:Operational`
  plus temporary legacy exclusions.
- [x] Prove journal and normal post-append entity-link writes still work.
- [x] Run focused tests plus isolated Neo4j smoke.

**Gate:** stop if the boundary is only prompt/regex enforcement, if generic
writers can mutate protected records, or if Operational nodes can enter
BOOTSTRAP/heavy-node output.

**Suggested commit:** `feat: establish protected operational graph records`

---

## Task 3: Pin and record exact harness generations

**Phase:** v0.1 prerequisite for v0.3

**Files:**

- Create: `digital_brain/maintenance/__init__.py`
- Create: `digital_brain/maintenance/models.py`
- Create: `digital_brain/maintenance/generation.py`
- Create: `scripts/pin_harness_generation.py`
- Modify: `plugins/digital-brain-buddy/hooks/hooks.json`
- Modify: `plugins/digital-brain-buddy/scripts/compose-up.sh`
- Modify: `digital_brain/tools/mcp_client.py`
- Modify: `digital_brain/tools/__init__.py`
- Modify: plugin session skill and canonical subagent prompts
- Create: `tests/test_harness_generation.py`

**Interface:**

```python
collect_harness_generation(...) -> HarnessGeneration
```

The canonical digest covers core commit/dirty state, plugin version, SOUL hash
only, active overlay manifest digest, active policy digest, MCP version, model
id when known, schema version, and taxonomy version.

- [x] Write deterministic serialization/digest tests.
- [x] Test that each meaningful input changes the generation id.
- [x] Test that SOUL content is never stored—only its local digest.
- [x] Add a typed `record_harness_generation` transaction and replay/conflict
  receipt.
- [x] Pin one generation at private session start and pass its id into sensor
  calls; do not recompute halfway through a session.
- [x] Add a readback/reconciliation API.
- [x] Add a deterministic SessionStart host entrypoint that runs after the
  local stack is ready, records/reconciles the generation, and exports/persists
  the pinned id for the session without exposing SOUL content.
- [x] Require every session capable of emitting RunEvents—not only private buddy
  sessions—to pass that same pinned id unchanged to every event.
- [x] Test that a mid-session file/policy change does not change the session's
  pinned id; only a new session receives a new generation.

**Gate:** RunEvent work cannot begin until generation id is required and stable.

**Accepted Milestone A residual (not blocking Task 4):** the dual-process
**active** pin under `$DIGITAL_BRAIN_STATE_DIR/active/` is last-writer-wins
across concurrent host sessions. Exact concurrent-session MCP attribution
requires per-request pin injection or session-keyed active pins (later). For
single interactive SessionStart this is acceptable; multi-session parallel
instrumentation must pass `DIGITAL_BRAIN_HARNESS_GENERATION_ID` per host or use
`sessions/<id>/` pins. See README “Shared harness pin”.

**Suggested commit:** `feat: pin versioned buddy harness generations`

---

## Task 4: Add typed Feedback and RunEvent transactions

**Phase:** v0.2–v0.3

**Files:**

- Expand: `mcp_servers/cypher/src/digital_brain_mcp_cypher/quality.py`
- Modify: `mcp_servers/cypher/src/digital_brain_mcp_cypher/server.py`
- Modify: `digital_brain/tools/mcp_client.py`
- Modify: `digital_brain/tools/neo4j_toolkit.py`
- Modify: `digital_brain/tools/__init__.py`
- Create: `tests/test_mcp_cypher_quality.py`
- Extend: `tests/test_mcp_cypher_server.py`
- Extend: `tests/test_mcp_client.py`

**Tools/interfaces:**

- `create_feedback`
- `revoke_feedback`
- `record_run_event`
- `get_quality_receipt`
- trusted internal recorder for deterministic MCP/host outcomes

**Data rules:** canonical request fingerprint, stable id, tight enums and length
caps, bounded reference arrays, required generation id, no embeddings, no
JournalEntry, separate removable raw payload, and explicit outcome source.

- [x] Write replay-vs-conflict tests for every write tool.
- [x] Test malformed enums, oversized summaries, invalid sensitivities, excessive
  references, and missing generation id.
- [x] Test that model-facing `record_run_event` is forced to
  `model_advisory`; callers cannot claim MCP/user authority.
- [x] Test deterministic tool outcome recording independently of model prose.
- [x] Instrument and test meaningful READ empty/fail plus WRITE conflict/timeout
  paths; each event must carry deterministic/advisory source correctly and the
  unchanged session-pinned HarnessGeneration id.
- [x] Test that no sensor path calls embeddings or journal-chain code.
- [x] Store raw Feedback payload separately so redaction can remove it while
  immutable evidence metadata/request fingerprint remains.
- [x] Add receipt reconciliation to the client; write calls never blind-retry.
- [x] Update MCP documentation and plugin `.mcp.json` tool note.

**Gate:** no sensor may be written through generic Cypher or enter the journal
vector index. A timeout must reconcile by receipt.

**Instrumentation note:** MCP query/transport timeouts emit
`tool_outcome=timeout` with `error_class=query_timeout`; host transport
timeouts emit `tool_outcome=timeout` with host error classes. Non-timeout
errors remain `tool_outcome=fail`.

**Suggested commit:** `feat: add typed versioned quality sensors`

---

## Task 5: Add durable workflow records and fenced coordination

**Phase:** v0.2 control-schema prerequisite; extended through v0.5 coordination

**Files:**

- Create: `mcp_servers/cypher/src/digital_brain_mcp_cypher/maintenance.py`
- Modify: `mcp_servers/cypher/src/digital_brain_mcp_cypher/server.py`
- Expand: `digital_brain/maintenance/models.py`
- Create: `tests/test_mcp_cypher_maintenance.py`
- Create: `tests/test_dream_lease.py`

**Typed records:** DreamRun, stage receipt, EvidenceSnapshot, Finding, Proposal,
PatchArtifact metadata, EvaluationReceipt, Decision, EffectReceipt,
ActivationAuthority, Deployment, ExposureWindow, MaintenanceLease.

**Authenticated non-MCP coordinator interfaces:**

- `acquire_maintenance_lease`
- `renew_maintenance_lease`
- `release_maintenance_lease`
- `create_dream_run`
- `record_dream_stage`
- `create_evidence_snapshot`
- `create_finding`
- `create_proposal`
- `record_evaluation`
- `record_decision`

These operations are called by deterministic coordinator code, not registered
as FastMCP tools and not exposed to analyzer/evaluator agents.

- [x] Write legal/illegal state-transition tests before store code.
- [x] Acquire using database time and increment a monotonic epoch after expiry.
- [x] Require `run_id + epoch` on stage transitions and retention effects.
- [x] Test takeover: the expired holder cannot commit after a new epoch exists.
- [x] Test crash/replay after every stage using stable stage keys.
- [x] Keep observation lifecycle, proposal lifecycle, decision, application, and
  effectiveness as separate records/projections.
- [x] Define ActivationAuthority with nonce digest, proposal/decision/effect
  hashes, target/base binding, approver, expiry, minted/consumed/revoked state,
  and reconciliation receipt; do not expose mint/consume on model-facing MCP.
- [x] Add relationship/provenance tests: evidence may support multiple findings
  and proposals; no single `absorbed_by_dream_id` field.

**Gate:** no dream runner is built until workflow schemas, legal transitions,
receipts, and stale-epoch rejection exist.

**Suggested commit:** `feat: add fenced maintenance workflow records`

---

## Task 6: Add FEEDBACK routing and safe Alias proposals/effects

**Phase:** v0.2 + v0.4

**Files:**

- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-session/SKILL.md`
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-session/references/subagent-prompts.md`
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-graph-mcp/SKILL.md`
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-graph-mcp/references/runtime-patterns.md`
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-read-memory/SKILL.md`
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-write-memory/SKILL.md`
- Modify: `plugins/digital-brain-buddy/agents/digital-brain-reader.md`
- Modify: `plugins/digital-brain-buddy/agents/digital-brain-writer.md`
- Modify: `plugins/digital-brain-buddy/agents/digital-brain-entity-check.md`
- Modify: `digital_brain/services/entity_resolver.py`
- Create: `digital_brain/maintenance/alias_effects.py`
- Create: `scripts/digital_brain_apply_proposal.py`
- Create: `tests/test_feedback_route_contract.py`
- Create: `tests/test_alias_effects.py`

**Interfaces/outcome:** FEEDBACK captures immutable evidence and optionally a
typed Alias proposal. It never activates from prose. Existing Alias lookup
becomes scoped, active-revision-aware, deterministic, and direct-to-canonical.

- [x] Add static plugin contract tests for the FEEDBACK route, one-prompt budget,
  generic-ack rejection, and `claim_false` propose-only behavior.
- [x] Add migration/audit output for existing unscoped/conflicting/cyclic Alias
  nodes; require human review before new resolution semantics activate.
- [x] Implement operator-only Alias apply/revoke transactions validating
  namespace, type, normalized source, target existence/type, uniqueness,
  provenance, before fingerprint, pinned target authority, and request hash.
- [x] Implement revisioned `EntityProtection` records and operator-only
  set/revoke effects; pinned Alias source/target changes require authority scope
  `pinned_identity`.
- [x] Use a single-use expiring authority bound to proposal/effect/target/base
  and approver. Provide operator-only mint, receipt-read, and atomic
  consume-with-effect interfaces; reconcile a lost response to the linked
  EffectReceipt without minting a replacement implicitly.
- [x] Keep operator credentials and scripts out of maintainer/analyzer toolsets;
  provide no unattended `--yes` path.
- [x] Write replay, changed-payload conflict, stale target, wrong type, missing
  target, duplicate active alias, Alias-target, authority replay/expiry, revoke,
  and rollback tests.
- [x] Make unalias a compensating revision/receipt, not delete.

**Gate:** if activation remains reachable from the model-facing MCP/toolset or
generic Cypher, ship proposal-only and defer Alias apply.

**Suggested commits:**

- `feat: add feedback routing and review proposals`
- `feat: add operator-confirmed reversible alias effects`

---

## Task 7: Build the report-only DreamRun coordinator

**Phase:** v0.5

**Files:**

- Create: `digital_brain/maintenance/runner.py`
- Create: `digital_brain/maintenance/snapshot.py`
- Create: `digital_brain/maintenance/privacy.py`
- Create: `scripts/digital_brain_dream.py`
- Create: `tests/test_dream_runner.py`
- Create: `tests/test_evidence_snapshot.py`
- Create: `tests/fixtures/dreams/evidence/`

**Initial mode:** manual, local-only, report-only. No retention, memory effect,
policy change, patch compilation, or activation.

- [x] Freeze a deterministic snapshot binding cutoff, selected ids/hashes,
  revocation projection, redaction version, generation, taxonomy/schema,
  graph bookmark, and base commit.
- [x] Partition proposal-generation evidence and hidden holdout ids now, even
  before patch evaluation exists.
- [x] Include supporting and contradicting evidence plus eligible exposures.
- [x] Use local keyed HMAC for retained low-entropy correlations.
- [x] Add late-event, revoked-event, counterevidence, deterministic digest,
  disjoint holdout, and intimate-field exclusion tests.
- [x] Implement runner state checkpoints and crash resume through Task 5 tools.
- [x] Produce only counts/ids and three buckets: applied housekeeping (zero in
  this phase), waiting for owner, deliberately left alone.
- [x] Prove an all-tools maintainer profile still has no activation capability.

**Gate:** same ledger state/policy must produce the same snapshot; raw intimate
data must not enter analyzer/report packets.

**Suggested commit:** `feat: add fenced report-only dream runs`

---

## Task 8: Add deterministic retention and regret handling

**Phase:** v0.6

**Files:**

- Create: `digital_brain/maintenance/retention.py`
- Expand: MCP quality/maintenance stores with dedicated retention transaction
- Create: `tests/test_quality_retention.py`
- Extend: `tests/test_evidence_snapshot.py`

**Interface:** an owner-configured allowlist/TTL selects raw payload redaction,
archive, or purge. The model cannot choose or expand the policy.

- [x] Represent retention configuration as a reviewed structured local setting
  with schema/version/digest.
- [x] Test dry-run counts before any automatic effect.
- [x] Redact/delete removable raw payload in a dedicated quality transaction;
  keep immutable metadata, fingerprint, and EffectReceipt.
- [x] Test intimate raw fields disappear from normal reads and exports.
- [x] Test revoke excludes future snapshots and marks directly derived pending
  proposals stale without rewriting journals or unrelated proposals.
- [x] Document backup limitations: historical backups may predate redaction.
- [x] Enable automatic retention only after explicit owner opt-in.

**Gate:** generic delete remains blocked; only the dedicated policy-bound
transaction may remove raw payload, and every effect is receipted.

**Suggested commit:** `feat: add policy-bound quality evidence retention`

---

## Task 9: Add typed findings, proposals, and leakage-safe evaluation

**Phase:** v0.7

**Files:**

- Create: `digital_brain/maintenance/analyzer.py`
- Create: `digital_brain/maintenance/evaluation.py`
- Create: `digital_brain/maintenance/invariants.py`
- Create: `digital_brain/maintenance/prompts/analyze.md`
- Create: `tests/test_dream_analyzer.py`
- Create: `tests/test_dream_evaluation.py`
- Create: `tests/fixtures/dreams/scenarios/`

**Interfaces:**

```python
analyze(snapshot: SanitizedEvidenceSnapshot) -> list[Finding | ChangeIntent]
evaluate(proposal, artifact, holdout, invariants) -> EvaluationReceipt
```

The analyzer may classify into housekeeping, memory, behaviour, or engineering
lanes. It writes only typed output through the coordinator; it cannot access
Neo4j, repo files, quarantine, activation code, or network tools directly.

- [x] Define strict Pydantic schemas and reject unknown lanes, effect types,
  extension slots, fields, and overlong summaries.
- [x] Treat all evidence strings as untrusted data; delimit them and reject
  instruction/tool-shaped content from ChangeIntent fields.
- [x] Build deterministic invariant scenarios for journal safety, identity,
  BOOTSTRAP exclusion, privacy, route behavior, and fail-soft language.
- [x] Require holdout fixtures disjoint from proposal-generation evidence.
- [x] Record baseline/candidate, fixture digest, evaluator version, target
  results, guardrails, privacy, invariants, and `passed|failed|inconclusive`.
- [x] Route embedding/MCP outages and code failures to engineering proposals;
  add a test proving they cannot produce semantic memory effects.
- [x] Test rejected-proposal suppression by finding/recurrence key: unrelated
  new evidence must not recreate it; a material same-key delta may.
- [x] Keep model rubrics advisory initially; hard invariant/privacy failures
  block review/approval.
- [x] If a Grok adapter is added, run it outside the repo against a sanitized
  snapshot directory with strict read-only tools, schema-constrained output,
  bounded turns, no auto-update, and **no `--yolo`**. Inspect argv/env in tests.

**Gate:** evaluation cannot be skipped in a Proposal transition, and generating
evidence cannot be the proposal's sole test set.

**Suggested commit:** `feat: add typed dream findings and evaluation receipts`

---

## Task 10: Compile ChangeIntent into quarantined overlay artifacts

**Phase:** v0.8

**Files:**

- Create: `digital_brain/maintenance/artifacts.py`
- Create: `digital_brain/maintenance/compiler.py`
- Create: `digital_brain/maintenance/overlay_rules.py`
- Create: `plugins/digital-brain-buddy/quality/overlay-slots.json`
- Create: `plugins/digital-brain-buddy/quality/locked-rules.json`
- Create: `tests/test_dream_artifacts.py`
- Create: `tests/test_overlay_compiler.py`

**Quarantine layout:**

```text
$DIGITAL_BRAIN_STATE_DIR/dreams/quarantine/<dream-id>/<proposal-id>/
├── intent.json
├── artifact.md
├── manifest.json
├── evaluation.json
└── checksums.json
```

**Compiler contract:** early versions render typed additive rules into named
extension slots from repository-owned templates. The model does not emit
arbitrary deployable Markdown. Core skill/code diffs route to the engineering
lane until separately approved.

- [x] Resolve a secure state directory (`0700`) outside the repo and plugin
  caches; refuse symlinked/unowned/insecure paths.
- [x] Bind artifact to proposal, snapshot, target skill/slot, rule id, base
  commit, exact target-file before hashes, compiler/schema versions, and patch
  digest.
- [x] Make compilation deterministic and immutable per dream/epoch/proposal.
- [x] Reject path traversal, symlinks, executable-mode changes, deletes,
  frontmatter/tool injection, arbitrary include paths, locked-rule changes,
  unknown slots, conflicts, and size/file-count overflow.
- [x] Stop on base drift; never automatically rebase or three-way merge.
- [x] Add coordinator-only `publish_patch_artifact`: revalidate
  `run_id + lease_epoch`, artifact digest, snapshot/proposal state, and base
  fingerprints before recording the published manifest in the control plane.
- [x] Test that a stale worker may leave an orphan in its epoch quarantine but
  cannot publish it; review/runtime ignores all unrecorded artifacts.
- [x] Validate in an isolated worktree using repository-owned fixed commands.
- [x] Add a static test proving quarantine is not mentioned by any runtime
  loader/session skill.

**Gate:** writing or modifying any quarantine file must have zero runtime effect.

**Suggested commit:** `feat: compile typed dream rules into quarantine`

---

## Task 11: Add reviewed overlay trials, generation pinning, and rollback

**Phase:** v0.9–v1.0

**Files:**

- Create: `digital_brain/maintenance/activation.py`
- Create: `digital_brain/maintenance/active_overlays.py`
- Create: `digital_brain/maintenance/reconcile.py`
- Create: `scripts/digital_brain_activate_overlay.py`
- Modify: plugin session skill to load only manifest-listed reviewed digests
- Create: `tests/test_overlay_activation.py`
- Create: `tests/test_effect_reconciliation.py`
- Extend: `tests/test_harness_generation.py`

**Authorization:** proposal id/hash, artifact hash, exact target/slot, base
commit/file hashes, approver, expiry, and single-use nonce. There is no
unattended `--yes` path and no activation tool in model-facing MCP or maintainer
toolsets.

- [x] Add wrong/expired/replayed nonce, changed hash/target/base, and stale
  proposal rejection tests before activation code.
- [x] Stage reviewed overlay and manifest on the same filesystem; fsync and use
  atomic manifest replacement.
- [x] Promote only to
  `$DIGITAL_BRAIN_STATE_DIR/dreams/active-overlays/<proposal-id>/<digest>.md`;
  quarantine and plugin cache/repo paths are never runtime trial sources.
- [x] Make the active manifest list exact file digests, rule ids, proposal ids,
  trial expiry, exposure budget, and rollback generation.
- [x] At session start, validate all manifest/file digests; on any mismatch,
  fail closed to the prior known-good/no-overlay generation.
- [x] Pin the manifest once per session; an existing session never changes
  behavior halfway through.
- [x] Record activation EffectReceipt, Deployment, and ExposureWindow separately.
- [x] Define eligible decision point, duration/exposure cap, target recurrence,
  counterevidence, and guardrail rollback thresholds before trial activation.
- [x] Trials expire and disable; they never silently promote.
- [x] Make rollback a new compensating effect restoring the prior manifest;
  preserve all audit history.
- [x] Reconcile crashes between filesystem manifest replacement and graph
  receipt; same request replays without duplicate activation.
- [x] For permanent deployment, require reviewed Git content, plugin version
  bump, host reload, and proof that a new generation actually loaded.

**Gate:** stop if activation is reachable by the analyzer/model, if presence
loads a file, if a mismatch fails open, or if rollback is artifact-agnostic.

**Suggested commit:** `feat: add reviewed overlay trials and rollback`

---

## Task 12: Add the maintenance skill, command UX, and plugin release

**Phase:** product surface for v0.5–v1.0; expose features only as their gates pass

**Files:**

- Create: `plugins/digital-brain-buddy/skills/digital-brain-buddy-maintenance/SKILL.md`
- Create: `plugins/digital-brain-buddy/skills/digital-brain-buddy-maintenance/agents/openai.yaml`
- Create: `plugins/digital-brain-buddy/skills/digital-brain-buddy-maintenance/references/quality-control-contract.md`
- Create: `plugins/digital-brain-buddy/agents/digital-brain-maintainer.md`
- Create: `plugins/digital-brain-buddy/commands/digital-brain-dream.md`
- Modify: plugin session/read/write skills and native agents
- Modify: plugin README and `CHANGELOG.md`
- Modify: `plugins/digital-brain-buddy/docs/VERSIONING.md`
- Modify: `plugins/digital-brain-buddy/version.json`
- Modify: `.claude-plugin/plugin.json`
- Modify: `.codex-plugin/plugin.json`
- Modify: root `.claude-plugin/marketplace.json`
- Modify: root `.agents/plugins/marketplace.json`
- Modify: root `README.md`
- Create: `tests/test_plugin_contract.py`
- Create: `tests/test_plugin_dream_contract.py`

**UX contract:** manual/report-only first; next private session reports applied
housekeeping, waiting proposals, and ambiguity left untouched. Evidence uses
progressive disclosure. Approval, application, deployment, and effectiveness
are separate messages.

- [ ] Give Claude/Grok maintainer agents explicit read + typed proposal tool
  allowlists; omit Bash/Edit/generic write/activation.
- [ ] For Codex, rely on server-side capability separation because
  `agents/openai.yaml` is not a hard per-worker tool boundary.
- [ ] Add commands for run/status/review/show/try/apply/defer/reject/undo/history/privacy,
  but only expose apply/try once operator paths and phase gates exist.
- [ ] Test that the skill never directs unattended identity, policy, overlay,
  code, SOUL, or journal changes.
- [ ] Test exact-token intent does not claim to be authorization.
- [ ] Test reports contain counts/ids and processing mode, not raw quotes.
- [ ] Bump the plugin to `0.3.0` because MCP tools and the session contract
  change; update all host/cache manifests including `.agents` marketplace and
  create a fresh Codex suffix.
- [ ] Validate manifests and hosts:

  ```bash
  python3 -m json.tool plugins/digital-brain-buddy/.mcp.json
  claude plugin validate plugins/digital-brain-buddy --strict
  grok plugin validate plugins/digital-brain-buddy
  ```

**Gate:** no scheduled run by default, no heartbeat, and no private proposal
queue in shared/non-owner sessions.

**Suggested commit:** `feat: ship guided digital-brain maintenance workflow`

---

## Task 13: Run full E2E, adversarial, and crash-recovery gates

**Phase:** release gate

**Files:**

- Extend/create: `tests/e2e/quality_control_smoke.py`
- Create: `tests/e2e/dream_workflow_smoke.py`
- Create: `tests/e2e/dream_crash_recovery.py`
- Create: `docker-compose.dreams-e2e.yml`
- Create: `scripts/run-dreams-e2e.sh`
- Modify: `tests/e2e/Dockerfile` as needed

Use a deterministic fake analyzer in required CI. Live Grok is an optional
manual smoke over synthetic/redacted fixtures and never a required correctness
gate.

**Required end-to-end flow:**

1. Pin a harness generation.
2. Create Feedback and deterministic/advisory RunEvents idempotently.
3. Exclude all Operational nodes from BOOTSTRAP/heavy-node results.
4. Acquire a fenced lease and freeze a snapshot.
5. Create findings/proposal with support, counterevidence, and holdout split.
6. Compile to quarantine and prove no runtime effect.
7. Evaluate and reject a hard invariant/privacy failure.
8. Reject wrong hash/base, expired/replayed authority, and stale lease.
9. Apply one operator-confirmed Alias or overlay trial idempotently.
10. Prove a new session pins the new generation while an old session stays pinned.
11. Record eligible exposures and effectiveness separately.
12. Roll back and verify the prior target state/generation.
13. Revoke evidence and prove it is absent from the next snapshot.

**Chaos points:** after every DreamRun checkpoint; after Alias mutation before
response; after manifest rename before graph receipt; lease takeover;
concurrent/double activation; stale base; symlink substitution; external packet
containing an intimate field.

**Final commands:**

```bash
uv run --group dev python -m pytest tests/ -q
bash scripts/run-journal-e2e.sh
bash scripts/run-dreams-e2e.sh
```

Then run a fresh independent security/correctness review against the spec's 20
acceptance criteria. Fix, rerun, and cap the review loop at three iterations;
stop and report remaining gaps rather than weakening gates.

**Gate:** all unit, existing journal E2E, dream E2E, crash, privacy, capability,
and rollback tests pass from a clean checkout.

**Suggested commit:** `test: add dream workflow and crash-recovery gates`

---

## Cross-task stop conditions

Do not advance or call the system self-evolving while any statement is true:

- legacy wake/post-write identity mutation remains;
- raw sensors have two authoritative stores;
- RunEvents lack exact generation attribution;
- generic/model-facing Cypher can mutate Operational/Alias/policy/receipt state;
- the model-facing MCP holds operator activation credentials;
- BOOTSTRAP or journal vector search can return operational evidence;
- analyzer/evaluator can use shell, repo, arbitrary network, generic write, or activation tools;
- a quarantined artifact affects runtime;
- evaluation or holdout separation is optional;
- activation authority is not exact-hash, expiring, single-use, and replay-safe;
- a stale lease can publish/mutate;
- crash reconciliation can duplicate an effect;
- a trial has no eligibility, deadline, guardrails, or tested rollback;
- external processing can receive raw personal/intimate evidence;
- `claim_false` mutates life memory without Claim/Assertion provenance;
- plugin caches/hosts cannot prove which generation loaded.

## Final handoff artifacts

When implementation completes, hand off:

- source and tests for every task;
- graph role/constraint/migration instructions;
- generated tool/API documentation;
- evidence taxonomy and generation schema versions;
- retention/privacy configuration guide;
- proposal/evaluation/effect receipt examples using synthetic data;
- operator activation and rollback runbook;
- plugin version/cache refresh instructions;
- unit/E2E/chaos test results;
- an explicit list of deferred items: heartbeat, auto semantic changes, Claim
  provenance, core skill auto-diffs, and any volume spool.
