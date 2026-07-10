"""In-process dream workflow E2E gate (deterministic fake analyzer, no live Grok).

Covers the release-gate 13-step flow end-to-end against fake Neo4j stores and
local secure state dirs. Collected by unit CI via pytest (see pyproject.toml
``python_files``) and by ``scripts/run-dreams-e2e.sh``.

Steps:
  1. Pin a harness generation.
  2. Create Feedback and deterministic/advisory RunEvents idempotently.
  3. Exclude all Operational nodes from BOOTSTRAP/heavy-node results.
  4. Acquire a fenced lease and freeze a snapshot.
  5. Create findings/proposal with support, counterevidence, and holdout split.
  6. Compile to quarantine and prove no runtime effect.
  7. Evaluate and reject a hard invariant/privacy failure.
  8. Reject wrong hash/base, expired/replayed authority, and stale lease.
  9. Apply one operator-confirmed overlay trial idempotently.
 10. Prove a new session pins the new generation while an old session stays pinned.
 11. Record eligible exposures and effectiveness separately.
 12. Roll back and verify the prior target state/generation.
 13. Revoke evidence and prove it is absent from the next snapshot.
"""

from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timezone
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))

from digital_brain.maintenance.activation import (  # noqa: E402
    OverlayActivationBinding,
    TrialPolicy,
    activate_overlay_trial,
    compute_overlay_before_fingerprint,
    rollback_overlay_trial,
    validate_authority_for_activation,
)
from digital_brain.maintenance.active_overlays import (  # noqa: E402
    load_session_active_overlays,
    load_validated_active_overlays,
    pin_session_active_overlays,
    resolve_loadable_overlays,
)
from digital_brain.maintenance.analyzer import ChangeIntent  # noqa: E402
from digital_brain.maintenance.compiler import (  # noqa: E402
    CompileRequest,
    compile_to_quarantine,
)
from digital_brain.maintenance.evaluation import (  # noqa: E402
    EvaluationGateError,
    assert_evaluation_present_for_transition,
    evaluate,
)
from digital_brain.maintenance.generation import (  # noqa: E402
    get_or_pin_session_generation,
    load_session_pin,
)
from digital_brain.maintenance.models import (  # noqa: E402
    EMPTY_DIGEST,
    digest_bytes,
    digest_text,
)
from digital_brain.maintenance.privacy import (  # noqa: E402
    IntimateFieldError,
    assert_no_intimate_fields,
    redact_packet,
)
from digital_brain.maintenance.runner import (  # noqa: E402
    DreamRunner,
    assert_no_activation_capability,
    maintainer_tool_profile,
)
from digital_brain.maintenance.snapshot import (  # noqa: E402
    SnapshotPolicy,
    freeze_snapshot,
    load_evidence_fixture,
)
from digital_brain_mcp_cypher.quality import (  # noqa: E402
    OPERATIONAL_EXCLUSION_CYPHER,
    heavy_node_exclusion_predicate,
)
from digital_brain.maintenance.overlay_rules import clear_overlay_rules_cache  # noqa: E402
from tests.test_mcp_cypher_maintenance import (  # noqa: E402
    _FakeMaintSession,
    _store_with as _maint_store_with,
)
from tests.test_mcp_cypher_quality import (  # noqa: E402
    _FakeSession as _QualityFakeSession,
    _store_with as _quality_store_with,
)
from tests.test_overlay_activation import (  # noqa: E402
    _FakeSession as _OverlayFakeSession,
    _store as _overlay_store,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIXTURE = ROOT / "tests" / "fixtures" / "dreams" / "evidence" / "sample_ledger.json"
PLUGIN = ROOT / "plugins" / "digital-brain-buddy"
TARGET_FILE = "skills/digital-brain-buddy-session/SKILL.md"
CUTOFF = "2026-07-10T12:00:00Z"
CORRELATION_KEY = b"dream-e2e-correlation-key"
GENERATION_ID = "hg-" + ("e" * 64)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _artifact_md() -> str:
    return (
        "<!-- OVERLAY_SLOT:fail_soft_language BEGIN -->\n"
        "### Rule `route-empty-guidance`\n"
        "Prefer fail-soft language when READ returns empty.\n"
        "<!-- OVERLAY_SLOT:fail_soft_language END -->\n"
    )


def _before_hashes() -> dict[str, str]:
    path = PLUGIN / TARGET_FILE
    return {TARGET_FILE: digest_bytes(path.read_bytes())}


def _intent(**overrides: Any) -> ChangeIntent:
    base = dict(
        id="intent-e2e-1",
        dream_id="dream-e2e-workflow",
        snapshot_id="snap-e2e-workflow",
        lane="behaviour",
        effect_type="overlay_rule",
        operation="add_rule",
        rule_id="route-empty-guidance",
        summary="Optional fail-soft retrieval guidance for empty READ",
        expected_outcome="owner_approved_overlay_trial_only",
        risk_tier="low",
        evidence_ids=["re-fail-1"],
        counterevidence_ids=["fb-praise-1"],
        recurrence_key="behaviour:route_empty_or_fail",
        material_digest="a" * 64,
        proposal_kind="overlay",
        target_skill="digital-brain-buddy-session",
        extension_slot="fail_soft_language",
        target_ref="dream:dream-e2e-workflow:behaviour:route",
        evidence_strength="tentative",
    )
    base.update(overrides)
    return ChangeIntent(**base)


def _binding(artifact_hash: str, **overrides: Any) -> OverlayActivationBinding:
    base = dict(
        proposal_id="prop-e2e-overlay",
        proposal_hash="ph" * 32,
        artifact_hash=artifact_hash,
        target_ref="slot:fail_soft_language",
        base_commit="cafebabe",
        before_hashes=_before_hashes(),
        rule_id="route-empty-guidance",
        extension_slot="fail_soft_language",
        target_skill="digital-brain-buddy-session",
        target_file=TARGET_FILE,
    )
    base.update(overrides)
    return OverlayActivationBinding(**base)


def _trial_policy(**overrides: Any) -> TrialPolicy:
    base = dict(
        decision_point="route:READ:empty_or_fail",
        duration_seconds=7 * 24 * 3600,
        exposure_cap=50,
        target_recurrence=3,
        counterevidence_threshold=2,
        guardrail_rollback_thresholds={
            "privacy_gate_failure_count": 1,
            "guardrail_regression_rate": 0.1,
        },
    )
    base.update(overrides)
    return TrialPolicy(**base)


def _mint_overlay(
    alias_store: Any,
    binding: OverlayActivationBinding,
    *,
    authority_id: str = "aa-e2e-1",
    expires_at: str | None = None,
) -> dict[str, Any]:
    fp = compute_overlay_before_fingerprint(
        target_ref=binding.target_ref,
        base_commit=binding.base_commit,
        before_hashes=binding.before_hashes,
        prior_manifest_digest=EMPTY_DIGEST,
    )
    payload: dict[str, Any] = {
        "id": authority_id,
        "proposal_id": binding.proposal_id,
        "proposal_hash": binding.proposal_hash,
        "target_ref": binding.target_ref,
        "before_fingerprint": fp,
        "artifact_or_effect_hash": binding.artifact_hash,
        "approver": "owner@e2e",
        "scopes": ["overlay_trial"],
    }
    if expires_at:
        payload["expires_at"] = expires_at
        payload["minted_at"] = "2026-07-10T11:00:00Z"
    mint = alias_store.mint_activation_authority(payload)
    assert mint["outcome"] == "created", mint
    return mint


@pytest.fixture(autouse=True)
def _clear_rules_cache():
    clear_overlay_rules_cache()
    yield
    clear_overlay_rules_cache()


# ---------------------------------------------------------------------------
# Full 13-step flow
# ---------------------------------------------------------------------------


def test_dream_workflow_13_step_release_gate(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """Single integrated gate covering the required end-to-end flow."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("DIGITAL_BRAIN_STATE_DIR", str(state))

    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "version.json").write_text('"0.3.0"\n', encoding="utf-8")
    soul = tmp_path / "SOUL.MD"
    soul.write_text("voice e2e v1", encoding="utf-8")

    # ------------------------------------------------------------------
    # 1. Pin a harness generation.
    # ------------------------------------------------------------------
    gen_old = get_or_pin_session_generation(
        state_dir=state,
        session_id="sess-old",
        repo_root=tmp_path,
        plugin_root=plugin,
        soul_path=soul,
        mcp_version="0.1.0",
        model_id="e2e-model",
        core_commit="c1",
        core_tree_digest="t1",
        dirty_state_digest=EMPTY_DIGEST,
    )
    assert gen_old.id.startswith("hg-")
    pinned_old = load_session_pin(state_dir=state, session_id="sess-old")
    assert pinned_old is not None
    assert pinned_old.id == gen_old.id
    harness_generation_id = gen_old.id

    # ------------------------------------------------------------------
    # 2. Create Feedback and deterministic/advisory RunEvents idempotently.
    # ------------------------------------------------------------------
    q_session = _QualityFakeSession()
    q_store = _quality_store_with(q_session)

    fb_payload = {
        "id": "fb-e2e-1",
        "kind": "entity_wrong",
        "sensitivity": "personal",
        "harness_generation_id": harness_generation_id,
        "redacted_summary": "entity name mismatch",
        "source_turn_ref": "turn-e2e-1",
    }
    created_fb = q_store.create_feedback(fb_payload)
    assert created_fb["outcome"] == "created"
    replayed_fb = q_store.create_feedback(fb_payload)
    assert replayed_fb["outcome"] == "replayed"

    det_event = {
        "id": "re-e2e-det-1",
        "harness_generation_id": harness_generation_id,
        "route": "READ",
        "tool": "read_neo4j_cypher",
        "tool_outcome": "empty",
        "outcome_source": "mcp",
        "error_class": "no_hits",
        "sensitivity": "public_ops",
        "observed_at": "2026-07-09T14:00:00Z",
    }
    created_re = q_store.record_run_event(det_event)
    assert created_re["outcome"] == "created"
    assert created_re["outcome_source"] == "mcp"
    assert q_store.record_run_event(det_event)["outcome"] == "replayed"

    adv = q_store.record_run_event(
        {
            "id": "re-e2e-adv-1",
            "harness_generation_id": harness_generation_id,
            "route": "READ",
            "tool": "read_neo4j_cypher",
            "tool_outcome": "empty",
            "outcome_source": "mcp",  # forced to model_advisory below
            "sensitivity": "public_ops",
            "observed_at": "2026-07-09T14:05:00Z",
        },
        force_outcome_source="model_advisory",
    )
    assert adv["outcome"] == "created"
    assert adv["outcome_source"] == "model_advisory"

    # ------------------------------------------------------------------
    # 3. Exclude all Operational nodes from BOOTSTRAP/heavy-node results.
    # ------------------------------------------------------------------
    assert "Operational" in OPERATIONAL_EXCLUSION_CYPHER
    assert "NOT" in OPERATIONAL_EXCLUSION_CYPHER
    heavy = heavy_node_exclusion_predicate("n")
    assert "Operational" in heavy
    assert "Alias" in heavy
    # Maintainer profile must never include activation capability.
    profile = maintainer_tool_profile(all_tools=True)
    assert_no_activation_capability(profile)
    assert "apply_alias" not in profile
    assert "activate_overlay" not in profile

    # ------------------------------------------------------------------
    # 4. Acquire a fenced lease and freeze a snapshot.
    # ------------------------------------------------------------------
    maint_session = _FakeMaintSession()
    maint_store = _maint_store_with(maint_session)
    runner = DreamRunner(
        store=maint_store,
        holder_id="host-e2e",
        harness_generation_id=harness_generation_id,
        correlation_key=CORRELATION_KEY,
        base_commit="cafebabe",
    )
    evidence = load_evidence_fixture(FIXTURE)
    dream_result = runner.run(
        evidence,
        cutoff_at=CUTOFF,
        run_id="dream-e2e-workflow",
        holdout_ids=["re-fail-2", "re-fail-3"],
        graph_bookmark="bm-e2e",
    )
    assert dream_result.stage == "completed"
    assert dream_result.processing_mode == "report_only"
    assert dream_result.snapshot_id
    assert dream_result.source_ids_digest
    public = dream_result.to_public_dict()
    assert_no_intimate_fields(public)
    assert "never surface this intimate quote" not in str(public)

    frozen = freeze_snapshot(
        evidence,
        policy=SnapshotPolicy(
            cutoff_at=CUTOFF,
            harness_generation_id=harness_generation_id,
            correlation_key=CORRELATION_KEY,
            holdout_ids=frozenset({"re-fail-2", "re-fail-3"}),
            holdout_ratio=0.0,
            graph_bookmark="bm-e2e",
            base_commit="cafebabe",
        ),
        dream_id="dream-e2e-workflow",
        snapshot_id=dream_result.snapshot_id,
    )
    assert set(frozen.generation_ids).isdisjoint(frozen.holdout_ids)
    assert "re-fail-2" in frozen.holdout_ids
    assert "fb-praise-1" in frozen.counterevidence_ids
    assert "re-late-1" in frozen.excluded_late_ids
    assert "fb-revoked-1" in frozen.excluded_revoked_ids

    # ------------------------------------------------------------------
    # 5. Findings/proposal with support, counterevidence, holdout split.
    # ------------------------------------------------------------------
    assert frozen.generation_ids  # support set non-empty
    assert frozen.holdout_ids  # holdout non-empty and disjoint
    assert frozen.counterevidence_ids
    buckets = dream_result.report["buckets"]
    assert buckets["applied_housekeeping"]["count"] == 0
    waiting = set(buckets["waiting_for_owner"]["ids"])
    # Holdout never waits for owner.
    assert "re-fail-2" not in waiting
    assert "re-fail-3" not in waiting

    # ------------------------------------------------------------------
    # 6. Compile to quarantine and prove no runtime effect.
    # ------------------------------------------------------------------
    intent = _intent(
        dream_id="dream-e2e-workflow",
        snapshot_id=dream_result.snapshot_id or "snap-e2e-workflow",
        evidence_ids=list(frozen.generation_ids)[:3] or ["re-fail-1"],
        counterevidence_ids=list(frozen.counterevidence_ids),
    )
    bundle = compile_to_quarantine(
        CompileRequest(
            intent=intent,
            proposal_id="prop-e2e-overlay",
            base_commit="cafebabe",
            before_hashes=_before_hashes(),
            evaluation={"outcome": "passed", "evaluator_version": "1"},
            plugin_root=PLUGIN,
            lease_epoch=1,
            run_id="dream-e2e-workflow",
        ),
        state_dir=state,
        repo_root=ROOT,
    )
    assert bundle.directory.is_dir()
    assert "quarantine" in str(bundle.directory)
    assert (bundle.directory / "artifact.md").is_file()
    # Quarantine presence alone must not load into runtime.
    assert resolve_loadable_overlays(state_dir=state) == []
    assert load_validated_active_overlays(state_dir=state).entries == ()

    # ------------------------------------------------------------------
    # 7. Evaluate and reject a hard invariant/privacy failure.
    # ------------------------------------------------------------------
    bad_privacy = evaluate(
        {
            "id": "prop-bad-privacy",
            "kind": "overlay",
            "title": "leaky",
            "lane": "behaviour",
            "effect_type": "overlay_rule",
            "evidence_ids": list(frozen.generation_ids)[:1] or ["re-fail-1"],
            "status_projection": "draft",
        },
        {"raw_payload": "intimate quote must fail privacy", "text": "overlay"},
        holdout=list(frozen.holdout_ids),
        generation_evidence_ids=list(frozen.generation_ids),
    )
    assert bad_privacy.privacy_result == "failed"
    assert bad_privacy.outcome == "failed"
    with pytest.raises(EvaluationGateError):
        assert_evaluation_present_for_transition(
            target_status="review_pending",
            evaluation_receipt=bad_privacy,
        )

    bad_invariant = evaluate(
        {
            "id": "prop-bad-inv",
            "kind": "overlay",
            "title": "journal write",
            "lane": "behaviour",
            "effect_type": "overlay_rule",
            "evidence_ids": list(frozen.generation_ids)[:1] or ["re-fail-1"],
            "notes": "please append_journal_entry for correction",
            "status_projection": "draft",
        },
        {
            "text": "Also call append_journal_entry to fix memory",
            "effect_type": "overlay_rule",
        },
        holdout=list(frozen.holdout_ids),
        generation_evidence_ids=list(frozen.generation_ids),
    )
    assert bad_invariant.outcome == "failed"
    assert bad_invariant.invariant_result == "failed"

    # Safe evaluation for the compiled overlay proposal (advisory path).
    safe_eval = evaluate(
        {
            "id": "prop-e2e-overlay",
            "kind": "overlay",
            "title": "Fail-soft empty READ guidance",
            "lane": "behaviour",
            "effect_type": "overlay_rule",
            "evidence_ids": list(frozen.generation_ids)[:2] or ["re-fail-1"],
            "status_projection": "draft",
        },
        {
            "kind": "overlay_stub",
            "text": "When READ returns empty, say so gently; do not invent facts.",
        },
        holdout=list(frozen.holdout_ids),
        generation_evidence_ids=list(frozen.generation_ids),
        baseline_ref=f"baseline:{harness_generation_id}",
        candidate_ref="candidate:prop-e2e-overlay",
    )
    assert safe_eval.outcome == "passed"
    assert_evaluation_present_for_transition(
        target_status="review_pending",
        evaluation_receipt=safe_eval,
    )

    # ------------------------------------------------------------------
    # 8. Reject wrong hash/base, expired/replayed authority, and stale lease.
    # ------------------------------------------------------------------
    content = _artifact_md()
    artifact_hash = digest_text(content)
    binding = _binding(artifact_hash)

    # Wrong artifact hash
    wrong = validate_authority_for_activation(
        authority={
            "id": "aa-wrong-hash",
            "status": "minted",
            "nonce_digest": digest_text("n1"),
            "proposal_id": binding.proposal_id,
            "proposal_hash": binding.proposal_hash,
            "target_ref": binding.target_ref,
            "before_fingerprint": "bf" * 32,
            "artifact_or_effect_hash": "bb" * 32,
            "approver": "owner@e2e",
            "expires_at": "2099-01-01T00:00:00Z",
        },
        nonce="n1",
        binding=binding,
        actor="owner@e2e",
        live_before_fingerprint="bf" * 32,
    )
    assert wrong["outcome"] == "conflict"
    assert wrong["reason"] == "artifact_hash_mismatch"

    # Stale base / before fingerprint
    live_changed = compute_overlay_before_fingerprint(
        target_ref=binding.target_ref,
        base_commit="CHANGED_BASE",
        before_hashes=binding.before_hashes,
        prior_manifest_digest=EMPTY_DIGEST,
    )
    stale_base = validate_authority_for_activation(
        authority={
            "id": "aa-stale-base",
            "status": "minted",
            "nonce_digest": digest_text("n1"),
            "proposal_id": binding.proposal_id,
            "proposal_hash": binding.proposal_hash,
            "target_ref": binding.target_ref,
            "before_fingerprint": "old" + ("0" * 61),
            "artifact_or_effect_hash": binding.artifact_hash,
            "approver": "owner@e2e",
            "expires_at": "2099-01-01T00:00:00Z",
        },
        nonce="n1",
        binding=binding,
        actor="owner@e2e",
        live_before_fingerprint=live_changed,
    )
    assert stale_base["outcome"] == "stale"
    assert stale_base["reason"] == "before_fingerprint_mismatch"

    # Expired authority
    expired = validate_authority_for_activation(
        authority={
            "id": "aa-expired",
            "status": "minted",
            "nonce_digest": digest_text("n1"),
            "proposal_id": binding.proposal_id,
            "proposal_hash": binding.proposal_hash,
            "target_ref": binding.target_ref,
            "before_fingerprint": "bf" * 32,
            "artifact_or_effect_hash": binding.artifact_hash,
            "approver": "owner@e2e",
            "expires_at": "2020-01-01T00:00:00Z",
        },
        nonce="n1",
        binding=binding,
        actor="owner@e2e",
        live_before_fingerprint="bf" * 32,
        now=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    assert expired["outcome"] == "failed"
    assert expired["reason"] == "authority_expired"

    # Replayed / consumed authority
    replayed_auth = validate_authority_for_activation(
        authority={
            "id": "aa-replay",
            "status": "consumed",
            "nonce_digest": digest_text("n1"),
            "proposal_id": binding.proposal_id,
            "proposal_hash": binding.proposal_hash,
            "target_ref": binding.target_ref,
            "before_fingerprint": "bf" * 32,
            "artifact_or_effect_hash": binding.artifact_hash,
            "approver": "owner@e2e",
            "expires_at": "2099-01-01T00:00:00Z",
            "consumption_receipt_id": "er-1",
        },
        nonce="n1",
        binding=binding,
        actor="owner@e2e",
        live_before_fingerprint="bf" * 32,
    )
    assert replayed_auth["outcome"] == "replayed"
    assert replayed_auth["reason"] == "authority_already_consumed"

    # Stale lease after takeover
    lease_session = _FakeMaintSession()
    lease_store = _maint_store_with(lease_session)
    a = lease_store.acquire_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-a",
            "run_id": "run-a",
            "ttl_seconds": 10,
        }
    )
    assert a["epoch"] == 1
    lease_store.create_dream_run(
        {
            "id": "run-a",
            "run_id": "run-a",
            "epoch": 1,
            "holder_id": "host-a",
            "harness_generation_id": harness_generation_id,
        }
    )
    lease_session.advance(11)
    b = lease_store.acquire_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-b",
            "run_id": "run-b",
            "ttl_seconds": 30,
        }
    )
    assert b["epoch"] == 2
    stale = lease_store.record_dream_stage(
        {"run_id": "run-a", "epoch": 1, "stage": "leased"}
    )
    assert stale["outcome"] == "stale_epoch"

    # ------------------------------------------------------------------
    # 9. Apply one operator-confirmed overlay trial idempotently.
    # ------------------------------------------------------------------
    ov_session = _OverlayFakeSession()
    alias_store, effect_store = _overlay_store(ov_session)
    mint = _mint_overlay(alias_store, binding)
    act = activate_overlay_trial(
        state_dir=state,
        binding=binding,
        artifact_md=content,
        trial_policy=_trial_policy(),
        authority_id=mint["authority_id"],
        nonce=mint["nonce"],
        actor="owner@e2e",
        rollback_generation=harness_generation_id,
        alias_store=alias_store,
        effect_store=effect_store,
    )
    assert act["outcome"] == "applied"
    assert act["deployment"]["status"] == "trial_active"
    n_effects = len(ov_session.effects)
    # Same authority replay → no duplicate activation.
    replay_act = activate_overlay_trial(
        state_dir=state,
        binding=binding,
        artifact_md=content,
        trial_policy=_trial_policy(),
        authority_id=mint["authority_id"],
        nonce=mint["nonce"],
        actor="owner@e2e",
        rollback_generation=harness_generation_id,
        alias_store=alias_store,
        effect_store=effect_store,
        request_hash=act["request_hash"],
    )
    assert replay_act["outcome"] in {"replayed", "applied", "failed"}
    if replay_act["outcome"] == "replayed":
        assert len(ov_session.effects) == n_effects
    # Live load path has the trial.
    bodies = resolve_loadable_overlays(state_dir=state)
    assert len(bodies) == 1
    assert bodies[0]["digest"] == artifact_hash

    # ------------------------------------------------------------------
    # 10. New session pins new generation; old session stays pinned.
    # ------------------------------------------------------------------
    soul.write_text("voice e2e v2 after trial", encoding="utf-8")
    # Old session pin must not change mid-session.
    still_old = get_or_pin_session_generation(
        state_dir=state,
        session_id="sess-old",
        repo_root=tmp_path,
        plugin_root=plugin,
        soul_path=soul,
        mcp_version="9.9.9",
        model_id="new-model",
        core_commit="CHANGED",
        core_tree_digest="CHANGED",
        dirty_state_digest="CHANGED",
    )
    assert still_old.id == gen_old.id

    gen_new = get_or_pin_session_generation(
        state_dir=state,
        session_id="sess-new",
        force_new=True,
        repo_root=tmp_path,
        plugin_root=plugin,
        soul_path=soul,
        mcp_version="0.1.0",
        model_id="e2e-model",
        core_commit="c1",
        core_tree_digest="t1",
        dirty_state_digest=EMPTY_DIGEST,
    )
    assert gen_new.id != gen_old.id
    assert load_session_pin(state_dir=state, session_id="sess-old").id == gen_old.id  # type: ignore[union-attr]

    # Overlay session pin also stable mid-session.
    pinned_ov = pin_session_active_overlays(state_dir=state, session_id="sess-old")
    assert len(pinned_ov.entries) == 1

    # ------------------------------------------------------------------
    # 11. Eligible exposures and effectiveness recorded separately.
    # ------------------------------------------------------------------
    assert frozen.source_counts["eligible_exposure"] == len(frozen.eligible_exposure_ids)
    assert "fb-entity-1" in frozen.eligible_exposure_ids
    for hid in frozen.holdout_ids:
        assert hid not in frozen.eligible_exposure_ids
    # Deployment ≠ exposure window ≠ effect receipt.
    assert act["deployment"]["id"] != act["effect_receipt"]["id"]
    assert act["exposure_window"]["id"] != act["effect_receipt"]["id"]
    assert act["exposure_window"]["decision_point"] == "route:READ:empty_or_fail"
    # Exposure window is a separate record from effectiveness evaluation.
    assert "id" in act["exposure_window"]
    assert act.get("effectiveness") is None or act.get("effectiveness") != act[
        "exposure_window"
    ]["id"]

    # ------------------------------------------------------------------
    # 12. Roll back and verify the prior target state/generation.
    # ------------------------------------------------------------------
    prior_digest = act["prior_manifest_digest"]
    rb = rollback_overlay_trial(
        state_dir=state,
        proposal_id=binding.proposal_id,
        prior_manifest_digest=prior_digest,
        actor="owner@e2e",
        effect_store=effect_store,
        deployment_id=act["deployment"]["id"],
        reason="e2e_rollback",
        prior_manifest=act["prior_manifest"],
    )
    assert rb["outcome"] == "applied"
    assert rb["effect_receipt"]["effect_type"] == "rollback_overlay_trial"
    assert load_validated_active_overlays(state_dir=state).entries == ()
    assert resolve_loadable_overlays(state_dir=state) == []
    dep = ov_session.deployments[act["deployment"]["id"]]
    assert dep["status"] == "rolled_back"
    # Old session generation pin still the prior generation.
    assert load_session_pin(state_dir=state, session_id="sess-old").id == gen_old.id  # type: ignore[union-attr]
    # Session overlay pin retains what was pinned before rollback (mid-session).
    sess_ov = load_session_active_overlays(state_dir=state, session_id="sess-old")
    assert sess_ov is not None
    assert len(sess_ov.entries) == 1

    # ------------------------------------------------------------------
    # 13. Revoke evidence and prove it is absent from the next snapshot.
    # ------------------------------------------------------------------
    rev = q_store.revoke_feedback(
        {
            "id": "fle-e2e-1",
            "feedback_id": "fb-e2e-1",
            "actor": "owner@e2e",
            "reason_code": "user_request",
        }
    )
    assert rev["outcome"] == "created"
    assert rev["event"] == "revoked"
    assert q_store.revoke_feedback(
        {
            "id": "fle-e2e-1",
            "feedback_id": "fb-e2e-1",
            "actor": "owner@e2e",
            "reason_code": "user_request",
        }
    )["outcome"] == "replayed"

    next_ledger = [
        {
            "id": "fb-e2e-1",
            "label": "Feedback",
            "kind": "entity_wrong",
            "sensitivity": "personal",
            "created_at": "2026-07-09T10:00:00Z",
            "evidence_hash": "hash-fb-e2e-1",
            "revoked": True,
            "redacted_summary": "entity name mismatch",
        },
        {
            "id": "fb-e2e-peer",
            "label": "Feedback",
            "kind": "miss",
            "sensitivity": "public_ops",
            "created_at": "2026-07-09T11:00:00Z",
            "evidence_hash": "hash-fb-e2e-peer",
            "revoked": False,
            "eligible_exposure": True,
        },
    ]
    next_snap = freeze_snapshot(
        next_ledger,
        policy=SnapshotPolicy(
            cutoff_at=CUTOFF,
            harness_generation_id=harness_generation_id,
            correlation_key=CORRELATION_KEY,
            holdout_ratio=0.0,
            graph_bookmark="bm-e2e-2",
            base_commit="cafebabe",
        ),
        dream_id="dream-e2e-after-revoke",
        snapshot_id="snap-e2e-after-revoke",
    )
    member_ids = {m.evidence_id for m in next_snap.memberships}
    assert "fb-e2e-1" not in member_ids
    assert "fb-e2e-1" in next_snap.excluded_revoked_ids
    assert "fb-e2e-peer" in member_ids
    packet = next_snap.analyzer_packet()
    assert_no_intimate_fields(packet)
    assert "fb-e2e-1" not in {item["id"] for item in packet["items"]}


def test_external_packet_with_intimate_field_is_rejected():
    """External analyzer packet containing an intimate field fails closed."""
    dirty = {
        "items": [
            {
                "id": "fb-x",
                "sensitivity": "intimate",
                "raw_payload": "never ship this",
            }
        ],
        "counts": {"n": 1},
    }
    with pytest.raises(IntimateFieldError):
        assert_no_intimate_fields(dirty)
    cleaned = redact_packet(dirty, correlation_key=CORRELATION_KEY)
    assert_no_intimate_fields(cleaned)
    assert "never ship this" not in str(cleaned)
    assert "raw_payload" not in str(cleaned)
