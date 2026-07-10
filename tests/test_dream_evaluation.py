"""Leakage-safe evaluation receipts, holdout gates, and invariant scenarios."""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from digital_brain.maintenance.analyzer import (  # noqa: E402
    ChangeIntent,
    analyze,
)
from digital_brain.maintenance.evaluation import (  # noqa: E402
    EVALUATOR_VERSION,
    EvaluationGateError,
    assert_evaluation_present_for_transition,
    assert_holdout_disjoint,
    evaluate,
    receipt_to_store_payload,
)
from digital_brain.maintenance.invariants import (  # noqa: E402
    INVARIANT_CATEGORIES,
    EvaluationContext,
    default_scenarios,
    ensure_categories_covered,
    load_scenarios,
    run_invariant,
    scenarios_digest,
)
from digital_brain.maintenance.models import EvaluationReceipt  # noqa: E402
from digital_brain.maintenance.snapshot import (  # noqa: E402
    SnapshotPolicy,
    freeze_snapshot,
    load_evidence_fixture,
)

GENERATION_ID = "hg-" + ("e" * 64)
CORRELATION_KEY = b"eval-test-correlation-key"
FIXTURE = ROOT / "tests" / "fixtures" / "dreams" / "evidence" / "sample_ledger.json"
SCENARIOS = ROOT / "tests" / "fixtures" / "dreams" / "scenarios"
CUTOFF = "2026-07-10T12:00:00Z"


def _policy(**kwargs):
    base = dict(
        cutoff_at=CUTOFF,
        harness_generation_id=GENERATION_ID,
        correlation_key=CORRELATION_KEY,
        holdout_ratio=0.0,
        holdout_ids=frozenset({"re-fail-2", "re-fail-3"}),
        graph_bookmark="bm-eval",
        base_commit="cafebabe",
    )
    base.update(kwargs)
    return SnapshotPolicy(**base)


def _frozen():
    return freeze_snapshot(
        load_evidence_fixture(FIXTURE),
        policy=_policy(),
        dream_id="dream-eval",
        snapshot_id="snap-eval",
    )


def _safe_proposal(**overrides):
    base = {
        "id": "prop-eval-1",
        "kind": "overlay",
        "title": "Fail-soft empty READ guidance",
        "lane": "behaviour",
        "effect_type": "overlay_rule",
        "evidence_ids": ["re-fail-1"],
        "status_projection": "draft",
    }
    base.update(overrides)
    return base


def test_evaluator_version_pinned():
    assert EVALUATOR_VERSION == "1"


def test_default_and_fixture_scenarios_cover_required_categories():
    defaults = default_scenarios()
    ensure_categories_covered(defaults)
    assert {s.category for s in defaults} == INVARIANT_CATEGORIES

    loaded = load_scenarios(SCENARIOS / "suite_manifest.json")
    ensure_categories_covered(loaded)
    # Individual category files load.
    for name in (
        "journal_safety.json",
        "identity.json",
        "bootstrap_exclusion.json",
        "privacy.json",
        "route_behavior.json",
        "fail_soft_language.json",
    ):
        one = load_scenarios(SCENARIOS / name)
        assert len(one) == 1
        assert one[0].category in INVARIANT_CATEGORIES


def test_holdout_must_be_disjoint_and_non_empty():
    with pytest.raises(EvaluationGateError, match="holdout_required"):
        assert_holdout_disjoint(holdout_ids=[], generation_ids=["a"])
    with pytest.raises(EvaluationGateError, match="holdout_not_disjoint"):
        assert_holdout_disjoint(holdout_ids=["a", "b"], generation_ids=["b", "c"])
    assert_holdout_disjoint(holdout_ids=["h1"], generation_ids=["g1"])


def test_evaluate_records_required_receipt_fields():
    frozen = _frozen()
    proposal = _safe_proposal(
        evidence_ids=list(frozen.generation_ids),
    )
    artifact = {
        "kind": "overlay_stub",
        "text": "When READ returns empty, say so gently; do not invent facts.",
    }
    receipt = evaluate(
        proposal,
        artifact,
        holdout=list(frozen.holdout_ids),
        generation_evidence_ids=list(frozen.generation_ids),
        baseline_ref="baseline:hg-current",
        candidate_ref="candidate:prop-eval-1",
        rubric_scores={"clarity": 0.9},  # advisory only
    )
    assert isinstance(receipt, EvaluationReceipt)
    assert receipt.evaluator_version == EVALUATOR_VERSION
    assert receipt.baseline_ref == "baseline:hg-current"
    assert receipt.candidate_ref == "candidate:prop-eval-1"
    assert receipt.fixture_snapshot
    assert "holdout_ids" in receipt.fixture_snapshot
    assert receipt.target_results
    assert receipt.guardrail_results
    assert receipt.privacy_result in {"passed", "failed"}
    assert receipt.invariant_result in {"passed", "failed", "inconclusive"}
    assert receipt.outcome in {"passed", "failed", "inconclusive"}
    assert receipt.outcome == "passed"
    assert receipt.request_fingerprint

    payload = receipt_to_store_payload(receipt, run_id="dream-eval", epoch=1)
    assert payload["outcome"] == "passed"
    assert payload["proposal_id"] == "prop-eval-1"


def test_generation_cannot_be_sole_test_set():
    proposal = _safe_proposal(evidence_ids=["g1", "g2"])
    with pytest.raises(EvaluationGateError, match="holdout_required"):
        evaluate(
            proposal,
            {"text": "ok"},
            holdout=[],
            generation_evidence_ids=["g1", "g2"],
        )
    with pytest.raises(EvaluationGateError, match="holdout_not_disjoint"):
        evaluate(
            proposal,
            {"text": "ok"},
            holdout=["g1"],  # overlaps generation
            generation_evidence_ids=["g1", "g2"],
        )


def test_hard_invariant_failure_blocks_review_transition():
    proposal = _safe_proposal(
        evidence_ids=["g1"],
        # Smuggle a journal write into the proposal surface.
        notes="please append_journal_entry for correction",
    )
    artifact = {
        "text": "Also call append_journal_entry to fix memory",
        "effect_type": "overlay_rule",
    }
    receipt = evaluate(
        proposal,
        artifact,
        holdout=["h-holdout-1"],
        generation_evidence_ids=["g1"],
    )
    assert receipt.outcome == "failed"
    assert receipt.invariant_result == "failed"

    with pytest.raises(EvaluationGateError, match="blocks_transition|failed_evaluation"):
        assert_evaluation_present_for_transition(
            target_status="review_pending",
            evaluation_receipt=receipt,
        )
    with pytest.raises(EvaluationGateError, match="blocks_transition|failed_evaluation"):
        assert_evaluation_present_for_transition(
            target_status="approved",
            evaluation_receipt=receipt,
        )


def test_evaluation_cannot_be_skipped_for_proposal_transition():
    with pytest.raises(EvaluationGateError, match="evaluation_required"):
        assert_evaluation_present_for_transition(
            target_status="validated",
            evaluation_receipt=None,
        )
    with pytest.raises(EvaluationGateError, match="evaluation_required"):
        assert_evaluation_present_for_transition(
            target_status="review_pending",
            evaluation_receipt=None,
        )
    # draft does not require evaluation.
    assert_evaluation_present_for_transition(
        target_status="draft",
        evaluation_receipt=None,
    )


def test_transition_rejects_digest_only_or_typed_receipt_without_holdout_ids():
    with pytest.raises(EvaluationGateError, match="holdout_proof"):
        assert_evaluation_present_for_transition(
            target_status="validated",
            evaluation_receipt={
                "outcome": "passed",
                "privacy_result": "passed",
                "invariant_result": "passed",
                "evaluator_version": "1",
                "fixture_digest": "a" * 64,
            },
        )

    forged = EvaluationReceipt(
        id="eval-forged",
        proposal_id="prop-forged",
        evaluator_version="1",
        baseline_ref="base",
        candidate_ref="candidate",
        fixture_snapshot=json.dumps({"scenario": "no holdout ids"}),
        target_results="{}",
        guardrail_results="{}",
        privacy_result="passed",
        invariant_result="passed",
        outcome="passed",
        created_at=CUTOFF,
    )
    with pytest.raises(EvaluationGateError, match="holdout_proof"):
        assert_evaluation_present_for_transition(
            target_status="review_pending", evaluation_receipt=forged
        )


def test_privacy_failure_is_hard_block():
    proposal = _safe_proposal(evidence_ids=["g1"])
    artifact = {
        "raw_payload": "intimate quote must fail privacy",
        "text": "overlay",
    }
    receipt = evaluate(
        proposal,
        artifact,
        holdout=["h1"],
        generation_evidence_ids=["g1"],
    )
    assert receipt.privacy_result == "failed"
    assert receipt.outcome == "failed"
    with pytest.raises(EvaluationGateError):
        assert_evaluation_present_for_transition(
            target_status="review_pending",
            evaluation_receipt=receipt,
        )


def test_advisory_rubric_cannot_override_hard_failure():
    proposal = _safe_proposal(
        evidence_ids=["g1"],
        kind="engineering",
        effect_type="apply_alias",  # semantic leak guardrail
    )
    receipt = evaluate(
        proposal,
        {"text": "safe looking text"},
        holdout=["h1"],
        generation_evidence_ids=["g1"],
        rubric_scores={"overall": 1.0, "style": 0.99},
    )
    assert receipt.outcome == "failed"
    targets = json.loads(receipt.target_results)
    assert targets["rubric_hard_gate"] is False
    assert targets["rubric_advisory"]["overall"] == 1.0


def test_identity_and_bootstrap_invariant_markers():
    scenarios = default_scenarios()
    by_cat = {s.category: s for s in scenarios}

    bad_identity = EvaluationContext(
        proposal={"id": "p", "plan": "auto_merge entities now"},
        artifact="DETACH DELETE leftover",
    )
    r = run_invariant(by_cat["identity"], bad_identity)
    assert r.outcome == "failed"

    bad_bootstrap = EvaluationContext(
        proposal={},
        artifact={"query": "bootstrap_include_operational true"},
    )
    r2 = run_invariant(by_cat["bootstrap_exclusion"], bad_bootstrap)
    assert r2.outcome == "failed"

    good = EvaluationContext(
        proposal={"id": "p", "kind": "overlay"},
        artifact={"text": "prefer empty-result honesty"},
    )
    for cat, sc in by_cat.items():
        assert run_invariant(sc, good).outcome == "passed", cat


def test_engineering_proposal_from_analyzer_passes_eval_without_semantic_effect():
    evidence = [
        {
            "id": "re-mcp-out",
            "label": "RunEvent",
            "route": "READ",
            "tool": "mcp",
            "tool_outcome": "fail",
            "error_class": "mcp_outage",
            "sensitivity": "public_ops",
            "created_at": "2026-07-09T10:00:00Z",
            "evidence_hash": "hash-mcp-out",
        },
        {
            "id": "re-holdout-x",
            "label": "RunEvent",
            "route": "READ",
            "tool": "mcp",
            "tool_outcome": "fail",
            "error_class": "mcp_timeout",
            "sensitivity": "public_ops",
            "created_at": "2026-07-09T10:05:00Z",
            "evidence_hash": "hash-holdout-x",
        },
    ]
    frozen = freeze_snapshot(
        evidence,
        policy=_policy(
            holdout_ids=frozenset({"re-holdout-x"}),
            holdout_ratio=0.0,
        ),
        dream_id="dream-eng-eval",
        snapshot_id="snap-eng-eval",
    )
    outputs = analyze(frozen)
    eng = [o for o in outputs if isinstance(o, ChangeIntent) and o.lane == "engineering"]
    assert eng
    intent = eng[0]
    proposal = {
        "id": "prop-eng-1",
        "kind": intent.proposal_kind,
        "lane": intent.lane,
        "effect_type": intent.effect_type,
        "evidence_ids": list(intent.evidence_ids),
        "title": intent.summary,
    }
    receipt = evaluate(
        proposal,
        {
            "text": "file infra issue for mcp_outage; do not mutate memory",
            "effect_type": intent.effect_type,
        },
        holdout=list(frozen.holdout_ids),
        generation_evidence_ids=list(frozen.generation_ids),
    )
    assert receipt.outcome == "passed"
    assert intent.effect_type not in {
        "apply_alias",
        "revoke_alias",
        "semantic_memory_write",
    }


def test_scenarios_digest_is_stable():
    a = scenarios_digest(default_scenarios())
    b = scenarios_digest(default_scenarios())
    assert a == b
    assert len(a) == 64


def test_passed_receipt_allows_review_pending_transition():
    receipt = evaluate(
        _safe_proposal(evidence_ids=["g1"]),
        {"text": "gentle empty-read language"},
        holdout=["h1"],
        generation_evidence_ids=["g1"],
    )
    assert receipt.outcome == "passed"
    assert_evaluation_present_for_transition(
        target_status="review_pending",
        evaluation_receipt=receipt,
    )
    assert_evaluation_present_for_transition(
        target_status="validated",
        evaluation_receipt=receipt,
    )
