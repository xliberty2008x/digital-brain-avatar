# Self-evolving quality / Dreams 0.3.0 operator handoff

This release is a bounded quality-maintenance system. It observes typed local
signals, produces report-only DreamRuns, and can compile reviewed proposals to
an immutable quarantine. Alias application and trial-overlay activation remain
operator-only. There is no unattended semantic self-modification and no
quarantined file has runtime effect by presence.

## Trust boundaries and deployment

1. Rebuild and redeploy `mcp-cypher` from the reviewed commit.
2. Set distinct `NEO4J_RUNTIME_*` and `NEO4J_QUALITY_*` credentials. Never give
   either process the Neo4j admin password.
3. As an operator, apply the generated role policy:

   ```bash
   NEO4J_ADMIN_PASSWORD='...' \
   NEO4J_RUNTIME_PASSWORD='...' \
   NEO4J_QUALITY_PASSWORD='...' \
   uv run --group dev python scripts/init_quality_roles.py --apply
   ```

4. Prove the runtime/quality separation against the live database:

   ```bash
   DIGITAL_BRAIN_REQUIRE_ROLE_SMOKE=1 \
   uv run --group dev python tests/e2e/quality_control_smoke.py
   ```

The source of truth for protected labels is
`digital_brain_mcp_cypher.quality.PROTECTED_QUALITY_LABELS`. Regenerate
`scripts/init-quality-roles.cypher` with `--write-cypher` after changing it.
Always apply with the Python helper: it primes every protected Neo4j label
token before installing label-scoped DENYs, including on an empty database.
The runtime role may write ordinary life-graph labels but is denied CREATE,
DELETE, SET LABEL, and SET PROPERTY for every protected control label.

## Schemas and tool surfaces

- Harness generation schema: `1`; quality taxonomy: `1`; maintenance schema:
  `1`; quarantine artifact schema: `1`.
- Model-facing MCP exposes quality reads plus typed `create_feedback`,
  `revoke_feedback`, and `record_run_event`. It does not expose lease,
  evaluation, publish, retention, Alias apply, or overlay activation methods.
- Coordinator operations are enumerated by
  `digital_brain_mcp_cypher.quality_control_api.WORKFLOW_OPERATIONS` and run
  outside FastMCP.
- Generic Cypher is a second line of defense; Neo4j role DENYs remain the
  authoritative live boundary.

## Synthetic record chain

An auditable successful proposal has separate records, never one mutable truth:

```json
{
  "feedback": {"id": "feedback-demo", "generation_id": "hg-demo"},
  "snapshot": {"id": "snapshot-demo", "holdout_ids": ["ev-h1", "ev-h2"]},
  "proposal": {"id": "proposal-demo", "status": "draft"},
  "evaluation": {
    "id": "evaluation-demo",
    "proposal_id": "proposal-demo",
    "holdout_ids": ["ev-h1", "ev-h2"],
    "outcome": "passed"
  },
  "decision": {"proposal_id": "proposal-demo", "decision": "approved"},
  "effect_receipt": {
    "effect_type": "activate_overlay_trial",
    "before_ref": "sha256-before",
    "after_ref": "sha256-after",
    "undo_ref": "sha256-before"
  }
}
```

The durable evaluator verifies that the concrete holdout set exactly matches
the snapshot's `INCLUDES_EVIDENCE {role:'holdout'}` set and does not overlap
generation evidence. A digest without concrete holdout IDs is not proof.

## Retention and privacy

Retention is dry-run by default. Automatic action requires an explicit owner
opt-in plus a reviewed policy digest. Redaction removes payload content, not
audit identity; revocation changes evidence eligibility and causes future
snapshots/proposals to exclude revoked material. External analyzers receive
only synthetic/redacted packets; raw personal or intimate evidence stays local.
Keep `$DIGITAL_BRAIN_STATE_DIR` private (`0700` directories, `0600` files), and
do not place it inside the repository or plugin cache.

## Quarantine, activation, expiry, and rollback

Publication re-reads a closed five-file quarantine bundle, rejects symlinks and
extra files, verifies every checksum, recomputes the patch digest, and binds the
manifest to proposal, snapshot, base commit, compiler, and schema. Publication
still has zero runtime effect.

To start a trial, the operator reviews the proposal and artifact, mints an
exact-hash expiring single-use `ActivationAuthority`, then runs
`scripts/digital_brain_activate_overlay.py` without an unattended confirmation
flag. The activation CLI accepts only a complete checksum-verified quarantine
bundle and rebinds its proposal/base/slot/rule/before-hash manifest fields. The
active manifest is updated with flock + compare-and-set and the graph gets a
separate effect/deployment/exposure receipt. SessionStart must successfully pin
the validated active manifest; a failed pin aborts the session hook.

Expiry never promotes. Rollback restores the exact prior manifest digest. A
pending operation record bridges filesystem and graph writes; run the overlay
reconciler after an interrupted activation, rollback, or expiry. Replays use the
same request hash and do not duplicate effects.

## Plugin rollout

The plugin manifest and marketplace version are `0.3.0`. After merging, refresh
the host/plugin cache using the host-specific steps in
`plugins/digital-brain-buddy/docs/VERSIONING.md`, restart the host, and verify
the loaded generation/version rather than trusting the source checkout alone.

## Release gates

Required from a clean checkout:

```bash
uv run --group dev python -m pytest tests/ -q
bash scripts/run-dreams-e2e.sh
python -m json.tool plugins/digital-brain-buddy/.mcp.json
claude plugin validate plugins/digital-brain-buddy --strict
grok plugin validate plugins/digital-brain-buddy
```

The disposable live role proof is:

```bash
DREAMS_E2E_DOCKER=1 DREAMS_E2E_REQUIRE_DOCKER=1 \
  bash scripts/run-dreams-e2e.sh
```

The Docker proof is an environment-dependent operator gate, not a substitute
for applying roles and re-running the smoke against the deployed database.

Pre-merge verification on 2026-07-10 completed with:

- `396 passed` in the canonical Python 3.12 hermetic suite;
- `21 passed` in the host Dreams workflow/crash gate;
- `21 passed` again inside the disposable Dreams container;
- live Neo4j proof that runtime could write `Person` but was denied protected
  CREATE, SET LABEL, and SET PROPERTY operations, while the distinct quality
  user could create and clean an `Operational:EffectReceipt`;
- Claude strict plugin validation and Grok plugin validation both passed.

## Explicitly deferred

- periodic heartbeat scheduling;
- automatic semantic changes or permanent auto-promotion;
- full Claim/Assertion provenance for `claim_false`;
- automatic diffs of locked/core skills;
- any volume-backed sensor spool;
- session-keyed MCP attribution for multiple concurrent host sessions;
- live Grok as a correctness dependency (deterministic fixtures remain the gate).
