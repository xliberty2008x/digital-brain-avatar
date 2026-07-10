"""Leakage-safe evaluation of dream proposals against holdout + invariants.

Evaluation cannot be skipped for a Proposal transition to validated /
review_pending. Generation evidence alone is never a sufficient test set —
holdout fixtures must be present and disjoint.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any, Mapping, Sequence

from digital_brain.maintenance.invariants import (
    EvaluationContext,
    InvariantScenario,
    default_scenarios,
    run_invariants,
    scenarios_digest,
    summarize_invariant_results,
)
from digital_brain.maintenance.models import (
    EVALUATION_OUTCOMES,
    EvaluationReceipt,
    digest_text,
)
from digital_brain.maintenance.privacy import (
    IntimateFieldError,
    assert_no_intimate_fields,
    contains_intimate_fields,
    redact_packet,
)

EVALUATOR_VERSION = "1"

# Proposal status projections that require a prior evaluation receipt.
STATUSES_REQUIRING_EVALUATION: frozenset[str] = frozenset(
    {
        "validated",
        "review_pending",
        "approved",
    }
)


class EvaluationGateError(ValueError):
    """Raised when evaluation preconditions fail (skip / leakage / holdout)."""


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _as_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    raise TypeError(f"{name} must be a mapping or model")


def _holdout_ids(holdout: Sequence[Any]) -> list[str]:
    ids: list[str] = []
    for item in holdout:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, Mapping):
            eid = item.get("id") or item.get("evidence_id")
            if eid:
                ids.append(str(eid))
        else:
            eid = getattr(item, "id", None) or getattr(item, "evidence_id", None)
            if eid:
                ids.append(str(eid))
    return ids


def _generation_ids(
    proposal: Mapping[str, Any],
    generation_evidence_ids: Sequence[str] | None,
) -> list[str]:
    if generation_evidence_ids is not None:
        return [str(x) for x in generation_evidence_ids]
    # Common proposal shapes from analyzer / store.
    summary = proposal.get("evidence_summary_json") or proposal.get("evidence_summary")
    if isinstance(summary, str) and summary.strip():
        try:
            parsed = json.loads(summary)
            if isinstance(parsed, dict) and "ids" in parsed:
                return [str(x) for x in parsed["ids"]]
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            pass
    if isinstance(summary, Mapping) and "ids" in summary:
        return [str(x) for x in summary["ids"]]
    ids = proposal.get("evidence_ids") or proposal.get("generation_evidence_ids")
    if isinstance(ids, (list, tuple)):
        return [str(x) for x in ids]
    return []


def assert_holdout_disjoint(
    *,
    holdout_ids: Sequence[str],
    generation_ids: Sequence[str],
) -> None:
    """Gate: holdout fixtures must be non-empty and disjoint from generation."""
    h = set(holdout_ids)
    g = set(generation_ids)
    if not h:
        raise EvaluationGateError("holdout_required_non_empty")
    overlap = h & g
    if overlap:
        raise EvaluationGateError(
            f"holdout_not_disjoint_from_generation:{sorted(overlap)}"
        )


def assert_evaluation_present_for_transition(
    *,
    target_status: str,
    evaluation_receipt: Mapping[str, Any] | EvaluationReceipt | None,
) -> None:
    """Gate: evaluation cannot be skipped for validated/review_pending/approved.

    Self-attested receipts (outcome=passed with empty holdout / missing privacy
    and invariant results) are rejected. Advanced status requires a non-failed
    outcome with explicit privacy_result + invariant_result == passed and a
    non-empty holdout fixture proof.
    """
    if target_status not in STATUSES_REQUIRING_EVALUATION:
        return
    if evaluation_receipt is None:
        raise EvaluationGateError(
            f"evaluation_required_for_status:{target_status}"
        )
    if isinstance(evaluation_receipt, EvaluationReceipt):
        outcome = evaluation_receipt.outcome
        inv = evaluation_receipt.invariant_result
        priv = evaluation_receipt.privacy_result
        evaluator_version = str(evaluation_receipt.evaluator_version or "")
        fixture_snapshot = str(evaluation_receipt.fixture_snapshot or "")
        fixture_digest = digest_text(fixture_snapshot) if fixture_snapshot else ""
        holdout_ids: list[str] = []
        if fixture_snapshot:
            try:
                parsed_fs = json.loads(fixture_snapshot)
                if isinstance(parsed_fs, Mapping):
                    ids = parsed_fs.get("holdout_ids") or parsed_fs.get("ids")
                    if isinstance(ids, (list, tuple)):
                        holdout_ids = [str(x) for x in ids]
            except json.JSONDecodeError:
                pass
    else:
        outcome = str(evaluation_receipt.get("outcome") or "")
        inv = str(evaluation_receipt.get("invariant_result") or "")
        priv = str(evaluation_receipt.get("privacy_result") or "")
        # holdout proof: holdout_ids list or fixture_snapshot.ids / fixture_digest
        holdout_ids = []
        raw_holdout = evaluation_receipt.get("holdout_ids")
        if isinstance(raw_holdout, (list, tuple)):
            holdout_ids = [str(x) for x in raw_holdout]
        fixture = evaluation_receipt.get("fixture_snapshot")
        if not holdout_ids and fixture is not None:
            if isinstance(fixture, str):
                try:
                    fixture = json.loads(fixture)
                except json.JSONDecodeError:
                    fixture = None
            if isinstance(fixture, Mapping):
                ids = fixture.get("ids") or fixture.get("holdout_ids")
                if isinstance(ids, (list, tuple)):
                    holdout_ids = [str(x) for x in ids]
        fixture_digest = str(
            evaluation_receipt.get("fixture_digest")
            or evaluation_receipt.get("fixture_snapshot_digest")
            or ""
        )
        evaluator_version = str(evaluation_receipt.get("evaluator_version") or "")
    if outcome not in EVALUATION_OUTCOMES:
        raise EvaluationGateError("evaluation_receipt_missing_outcome")
    if not evaluator_version:
        raise EvaluationGateError("evaluation_receipt_missing_evaluator_version")
    # Explicit privacy/invariant results required (no silent default-to-passed).
    if not priv or not inv:
        raise EvaluationGateError("evaluation_receipt_missing_hard_results")
    # Holdout-backed evaluation: non-empty holdout or non-empty fixture digest.
    if not holdout_ids and not fixture_digest:
        raise EvaluationGateError("evaluation_receipt_missing_holdout_proof")
    # Hard privacy/invariant failures block review/approval transitions.
    if target_status in {"review_pending", "approved", "validated"}:
        if priv in {"failed", "fail"} or inv in {"failed", "fail"}:
            raise EvaluationGateError(
                f"hard_gate_blocks_transition:{target_status}:privacy={priv}:invariant={inv}"
            )
        if outcome == "failed":
            raise EvaluationGateError(
                f"failed_evaluation_blocks_transition:{target_status}"
            )
        if outcome != "passed":
            raise EvaluationGateError(
                f"evaluation_outcome_blocks_transition:{target_status}:{outcome}"
            )


def _privacy_check(surfaces: Sequence[Any]) -> dict[str, Any]:
    for surface in surfaces:
        if surface is None:
            continue
        if isinstance(surface, str):
            # String artifacts: structural field check only via packet-like parse.
            continue
        if contains_intimate_fields(surface):
            return {"result": "failed", "detail": "intimate_field_present"}
        try:
            assert_no_intimate_fields(surface)
        except IntimateFieldError as exc:
            return {"result": "failed", "detail": str(exc)}
    return {"result": "passed", "detail": "ok"}


def _guardrail_check(
    proposal: Mapping[str, Any],
    artifact: Any,
) -> dict[str, Any]:
    """Static guardrails (non-rubric): schema hints, semantic leak, size."""
    checks: list[dict[str, str]] = []
    ok = True
    kind = str(proposal.get("kind") or "")
    effect = str(proposal.get("effect_type") or "")
    if kind == "engineering" and effect in {
        "apply_alias",
        "revoke_alias",
        "entity_merge",
        "correction_journal",
        "semantic_memory_write",
        "dispute_claim",
    }:
        ok = False
        checks.append(
            {
                "name": "engineering_no_semantic_memory",
                "result": "failed",
                "detail": effect,
            }
        )
    else:
        checks.append(
            {
                "name": "engineering_no_semantic_memory",
                "result": "passed",
                "detail": "ok",
            }
        )

    # Artifact must not claim generation-only evaluation.
    art = artifact if isinstance(artifact, Mapping) else {}
    if art.get("evaluated_on_generation_only") is True:
        ok = False
        checks.append(
            {
                "name": "no_generation_only_eval",
                "result": "failed",
                "detail": "artifact_flags_generation_only",
            }
        )
    else:
        checks.append(
            {
                "name": "no_generation_only_eval",
                "result": "passed",
                "detail": "ok",
            }
        )

    return {
        "result": "passed" if ok else "failed",
        "checks": checks,
    }


def _target_results(
    *,
    holdout_ids: Sequence[str],
    proposal: Mapping[str, Any],
    rubric_scores: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Target/holdout replay results. Rubric scores are advisory only."""
    return {
        "holdout_count": len(holdout_ids),
        "holdout_ids_digest": digest_text(
            _canonical_json(sorted(str(x) for x in holdout_ids))
        ),
        "proposal_id": proposal.get("id") or proposal.get("proposal_id"),
        # Advisory model rubric — never hard-gates by itself.
        "rubric_advisory": dict(rubric_scores or {}),
        "rubric_hard_gate": False,
    }


def evaluate(
    proposal: Any,
    artifact: Any,
    holdout: Sequence[Any],
    invariants: Sequence[InvariantScenario] | None = None,
    *,
    generation_evidence_ids: Sequence[str] | None = None,
    baseline_ref: str = "baseline:current",
    candidate_ref: str = "candidate:proposal",
    rubric_scores: Mapping[str, Any] | None = None,
    evaluation_id: str | None = None,
    packet: Mapping[str, Any] | None = None,
) -> EvaluationReceipt:
    """Evaluate a proposal against holdout fixtures and invariant scenarios.

    Hard failures: privacy, invariants (hard), holdout leakage/absence, guardrails.
    Model rubric scores are recorded as advisory only and never alone pass a
    proposal that fails hard gates.
    """
    prop = _as_mapping(proposal, name="proposal")
    art: Any
    if artifact is None or isinstance(artifact, (str, Mapping)):
        art = artifact
    elif hasattr(artifact, "model_dump"):
        art = artifact.model_dump()
    else:
        art = artifact

    h_ids = _holdout_ids(holdout)
    g_ids = _generation_ids(prop, generation_evidence_ids)

    # Gate: holdout required and disjoint (generation cannot be sole test set).
    assert_holdout_disjoint(holdout_ids=h_ids, generation_ids=g_ids)

    scenarios = list(invariants) if invariants is not None else default_scenarios()
    if not scenarios:
        raise EvaluationGateError("invariants_required_non_empty")

    ctx = EvaluationContext(
        proposal=prop,
        artifact=art if isinstance(art, (Mapping, str)) or art is None else str(art),
        holdout_ids=tuple(h_ids),
        generation_evidence_ids=tuple(g_ids),
        packet=dict(packet) if packet is not None else None,
    )
    inv_results = run_invariants(scenarios, ctx)
    inv_summary = summarize_invariant_results(inv_results)

    privacy = _privacy_check(
        [
            prop,
            art if isinstance(art, Mapping) else None,
            packet,
        ]
    )
    guardrails = _guardrail_check(prop, art)
    targets = _target_results(
        holdout_ids=h_ids, proposal=prop, rubric_scores=rubric_scores
    )

    # Overall outcome: hard privacy/invariant/guardrail failures → failed.
    # Advisory rubric cannot rescue a hard failure or sole-pass without holdout
    # (holdout already enforced).
    hard_fail = (
        privacy["result"] == "failed"
        or inv_summary["overall"] == "failed"
        or guardrails["result"] == "failed"
    )
    if hard_fail:
        outcome = "failed"
    elif inv_summary["overall"] == "inconclusive":
        outcome = "inconclusive"
    else:
        outcome = "passed"

    # High advisory rubric with hard fail still failed (explicit).
    if hard_fail and rubric_scores:
        outcome = "failed"

    fixture_payload = {
        "holdout_ids": sorted(h_ids),
        "scenarios_digest": scenarios_digest(scenarios),
        "evaluator_version": EVALUATOR_VERSION,
    }
    fixture_snapshot = _canonical_json(fixture_payload)

    pid = str(prop.get("id") or prop.get("proposal_id") or "unknown-proposal")
    eid = evaluation_id or f"eval-{digest_text(pid + fixture_snapshot)[:16]}"

    receipt = EvaluationReceipt(
        id=eid,
        proposal_id=pid,
        evaluator_version=EVALUATOR_VERSION,
        baseline_ref=baseline_ref,
        candidate_ref=candidate_ref,
        fixture_snapshot=fixture_snapshot,
        target_results=_canonical_json(targets),
        guardrail_results=_canonical_json(guardrails),
        privacy_result=str(privacy["result"]),
        invariant_result=str(inv_summary["overall"]),
        outcome=outcome,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        request_fingerprint=digest_text(
            _canonical_json(
                {
                    "baseline_ref": baseline_ref,
                    "candidate_ref": candidate_ref,
                    "evaluator_version": EVALUATOR_VERSION,
                    "id": eid,
                    "outcome": outcome,
                    "proposal_id": pid,
                }
            )
        ),
    )
    if receipt.outcome not in EVALUATION_OUTCOMES:
        raise RuntimeError(f"invalid_evaluation_outcome:{receipt.outcome}")
    return receipt


def receipt_to_store_payload(
    receipt: EvaluationReceipt,
    *,
    run_id: str,
    epoch: int,
    lease_key: str = "maintenance",
) -> dict[str, Any]:
    return {
        "id": receipt.id,
        "proposal_id": receipt.proposal_id,
        "evaluator_version": receipt.evaluator_version,
        "baseline_ref": receipt.baseline_ref,
        "candidate_ref": receipt.candidate_ref,
        "fixture_snapshot": receipt.fixture_snapshot,
        "target_results": receipt.target_results,
        "guardrail_results": receipt.guardrail_results,
        "privacy_result": receipt.privacy_result,
        "invariant_result": receipt.invariant_result,
        "outcome": receipt.outcome,
        "run_id": run_id,
        "epoch": epoch,
        "lease_key": lease_key,
    }


def sanitize_eval_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Redact a packet before any external/advisory rubric use."""
    clean = redact_packet(dict(packet))
    assert_no_intimate_fields(clean)
    return clean
