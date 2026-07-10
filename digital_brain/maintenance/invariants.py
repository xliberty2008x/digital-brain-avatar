"""Deterministic invariant scenarios for dream proposal evaluation.

Scenarios cover journal safety, identity, BOOTSTRAP exclusion, privacy,
route behavior, and fail-soft language. Hard failures block review/approval;
model rubrics remain advisory and live outside this module.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from digital_brain.maintenance.models import digest_text
from digital_brain.maintenance.privacy import (
    INTIMATE_FIELD_NAMES,
    contains_intimate_fields,
)

INVARIANT_SCHEMA_VERSION = "1"

# Closed set of scenario categories required by the design.
INVARIANT_CATEGORIES: frozenset[str] = frozenset(
    {
        "journal_safety",
        "identity",
        "bootstrap_exclusion",
        "privacy",
        "route_behavior",
        "fail_soft_language",
    }
)

# Artifact / proposal surfaces that must never touch the journal chain.
_JOURNAL_WRITE_MARKERS: tuple[str, ...] = (
    "append_journal_entry",
    "create_journal",
    "journalentry",
    "journal_entry",
    "embed_text",
    "vector_index",
    "correction journal",
    "correction_journal",
)

# Identity blast-radius patterns.
_IDENTITY_DANGER_MARKERS: tuple[str, ...] = (
    "detach delete",
    "detach_delete",
    "auto_merge",
    "auto-merge",
    "merge_entities",
    "delete node",
    "unscoped_alias",
    "alias_to_alias",
)

# Operational labels must stay out of BOOTSTRAP / heavy-node / default export.
_BOOTSTRAP_MARKERS: tuple[str, ...] = (
    "bootstrap_include_operational",
    "operational_in_bootstrap",
    "include_operational",
    "export_operational",
    "vector_index_operational",
)

# Route escalation without a gate.
_ROUTE_DANGER_MARKERS: tuple[str, ...] = (
    "skip_to_write",
    "skip→write",
    "silent_write",
    "bypass_route_gate",
    "route_override_write",
)

# Fail-soft infra notes must not become life-memory claims.
_FAIL_SOFT_DANGER_MARKERS: tuple[str, ...] = (
    "timeout_is_life_fact",
    "outage_as_journal",
    "mcp_failure_is_memory",
    "embedding_failure_is_truth",
    "infra_as_personal_memory",
)

# Semantic memory effect types (must not pass as engineering side-effects).
SEMANTIC_MEMORY_EFFECT_TYPES: frozenset[str] = frozenset(
    {
        "apply_alias",
        "revoke_alias",
        "entity_merge",
        "entity_delete",
        "correction_journal",
        "dispute_claim",
        "semantic_memory_write",
        "claim_false_mutate",
        "bulk_relation_rewrite",
    }
)


@dataclass(frozen=True)
class InvariantResult:
    scenario_id: str
    category: str
    outcome: str  # passed | failed | inconclusive
    detail: str = ""
    hard: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "category": self.category,
            "outcome": self.outcome,
            "detail": self.detail,
            "hard": self.hard,
        }


@dataclass(frozen=True)
class InvariantScenario:
    """One deterministic check against proposal/artifact evaluation context."""

    id: str
    category: str
    description: str
    # Optional expected markers that *must* be present for a positive control.
    require_absent_markers: tuple[str, ...] = ()
    # Markers that force failure when found (case-insensitive substring).
    fail_on_markers: tuple[str, ...] = ()
    # When True, also run the category's built-in hard checks.
    use_builtin: bool = True
    hard: bool = True
    # Fixture metadata only (not model instructions).
    notes: str = ""

    def __post_init__(self) -> None:
        if self.category not in INVARIANT_CATEGORIES:
            raise ValueError(f"unknown invariant category: {self.category!r}")
        if not self.id or not str(self.id).strip():
            raise ValueError("invariant scenario id required")


@dataclass
class EvaluationContext:
    """Inputs available to invariant checks (no network/repo tools)."""

    proposal: Mapping[str, Any] = field(default_factory=dict)
    artifact: Mapping[str, Any] | str | None = None
    holdout_ids: tuple[str, ...] = ()
    generation_evidence_ids: tuple[str, ...] = ()
    packet: Mapping[str, Any] | None = None

    def blob(self) -> str:
        parts: list[str] = [json.dumps(dict(self.proposal), default=str, sort_keys=True)]
        if self.artifact is None:
            parts.append("")
        elif isinstance(self.artifact, str):
            parts.append(self.artifact)
        else:
            parts.append(json.dumps(dict(self.artifact), default=str, sort_keys=True))
        if self.packet is not None:
            parts.append(json.dumps(dict(self.packet), default=str, sort_keys=True))
        return "\n".join(parts).lower()


def _builtin_fail_markers(category: str) -> tuple[str, ...]:
    if category == "journal_safety":
        return _JOURNAL_WRITE_MARKERS
    if category == "identity":
        return _IDENTITY_DANGER_MARKERS
    if category == "bootstrap_exclusion":
        return _BOOTSTRAP_MARKERS
    if category == "route_behavior":
        return _ROUTE_DANGER_MARKERS
    if category == "fail_soft_language":
        return _FAIL_SOFT_DANGER_MARKERS
    if category == "privacy":
        return tuple(sorted(INTIMATE_FIELD_NAMES))
    return ()


def _check_privacy_structures(ctx: EvaluationContext) -> str | None:
    """Return failure detail if intimate structures are present."""
    surfaces: list[Any] = [ctx.proposal]
    if ctx.artifact is not None and not isinstance(ctx.artifact, str):
        surfaces.append(ctx.artifact)
    if ctx.packet is not None:
        surfaces.append(ctx.packet)
    for surface in surfaces:
        if contains_intimate_fields(surface):
            return "intimate_or_raw_field_present"
    # String artifact: look for known intimate payload phrases only via field names.
    if isinstance(ctx.artifact, str):
        lower = ctx.artifact.lower()
        for name in INTIMATE_FIELD_NAMES:
            # Require field-shaped occurrence, not casual English.
            if re.search(rf'["\']?{re.escape(name)}["\']?\s*[:=]', lower):
                return f"intimate_field_in_artifact_text:{name}"
    return None


def _check_semantic_engineering_leak(ctx: EvaluationContext) -> str | None:
    """Engineering proposals must not smuggle semantic memory effects."""
    lane = str(ctx.proposal.get("lane") or ctx.proposal.get("kind") or "")
    effect = str(
        ctx.proposal.get("effect_type")
        or (ctx.artifact if isinstance(ctx.artifact, dict) else {}).get("effect_type")
        or ""
    )
    kind = str(ctx.proposal.get("kind") or "")
    if kind == "engineering" or lane == "engineering":
        if effect in SEMANTIC_MEMORY_EFFECT_TYPES:
            return f"engineering_semantic_memory_effect:{effect}"
    return None


def run_invariant(
    scenario: InvariantScenario,
    ctx: EvaluationContext,
) -> InvariantResult:
    """Run one scenario; hard failures return outcome=failed."""
    blob = ctx.blob()
    markers = list(scenario.fail_on_markers)
    if scenario.use_builtin:
        markers.extend(_builtin_fail_markers(scenario.category))

    # Privacy structural check.
    if scenario.category == "privacy":
        detail = _check_privacy_structures(ctx)
        if detail:
            return InvariantResult(
                scenario_id=scenario.id,
                category=scenario.category,
                outcome="failed",
                detail=detail,
                hard=scenario.hard,
            )

    # Engineering / semantic leak is always hard when identity or journal category.
    if scenario.category in {"identity", "journal_safety", "fail_soft_language"}:
        leak = _check_semantic_engineering_leak(ctx)
        if leak:
            return InvariantResult(
                scenario_id=scenario.id,
                category=scenario.category,
                outcome="failed",
                detail=leak,
                hard=True,
            )

    for marker in markers:
        m = str(marker).lower().strip()
        if not m:
            continue
        if m in blob:
            return InvariantResult(
                scenario_id=scenario.id,
                category=scenario.category,
                outcome="failed",
                detail=f"forbidden_marker:{marker}",
                hard=scenario.hard,
            )

    for required_absent in scenario.require_absent_markers:
        # Alias for fail_on; kept for fixture readability.
        if str(required_absent).lower() in blob:
            return InvariantResult(
                scenario_id=scenario.id,
                category=scenario.category,
                outcome="failed",
                detail=f"required_absent_present:{required_absent}",
                hard=scenario.hard,
            )

    return InvariantResult(
        scenario_id=scenario.id,
        category=scenario.category,
        outcome="passed",
        detail="ok",
        hard=scenario.hard,
    )


def run_invariants(
    scenarios: Sequence[InvariantScenario],
    ctx: EvaluationContext,
) -> list[InvariantResult]:
    return [run_invariant(s, ctx) for s in scenarios]


def summarize_invariant_results(
    results: Sequence[InvariantResult],
) -> dict[str, Any]:
    hard_failures = [r for r in results if r.outcome == "failed" and r.hard]
    soft_failures = [r for r in results if r.outcome == "failed" and not r.hard]
    inconclusive = [r for r in results if r.outcome == "inconclusive"]
    if hard_failures:
        overall = "failed"
    elif inconclusive and not soft_failures:
        overall = "inconclusive"
    elif soft_failures:
        # Soft-only failures stay advisory → do not hard-block; surface as passed
        # with advisory notes (caller may still record details).
        overall = "passed"
    else:
        overall = "passed"
    return {
        "overall": overall,
        "hard_failure_count": len(hard_failures),
        "soft_failure_count": len(soft_failures),
        "results": [r.to_dict() for r in results],
        "schema_version": INVARIANT_SCHEMA_VERSION,
    }


def default_scenarios() -> list[InvariantScenario]:
    """Built-in deterministic suite used when no fixture path is supplied."""
    return [
        InvariantScenario(
            id="inv-journal-safety",
            category="journal_safety",
            description=(
                "Proposals must not create JournalEntry nodes, touch the journal "
                "vector index, or emit correction journals from maintenance."
            ),
        ),
        InvariantScenario(
            id="inv-identity",
            category="identity",
            description=(
                "No auto DETACH DELETE merge, unscoped alias, or Alias-to-Alias "
                "chains from evaluated artifacts."
            ),
        ),
        InvariantScenario(
            id="inv-bootstrap-exclusion",
            category="bootstrap_exclusion",
            description=(
                "Operational evidence must remain excluded from BOOTSTRAP, "
                "heavy-node, vector, and default export paths."
            ),
        ),
        InvariantScenario(
            id="inv-privacy",
            category="privacy",
            description=(
                "Intimate/raw fields must not appear in proposal or artifact "
                "surfaces under evaluation."
            ),
        ),
        InvariantScenario(
            id="inv-route-behavior",
            category="route_behavior",
            description=(
                "Artifacts must not silently escalate SKIP→WRITE or bypass route "
                "gates."
            ),
        ),
        InvariantScenario(
            id="inv-fail-soft-language",
            category="fail_soft_language",
            description=(
                "Infra timeouts/outages stay fail-soft notes; they must not become "
                "life-memory claims."
            ),
        ),
    ]


def scenario_from_mapping(data: Mapping[str, Any]) -> InvariantScenario:
    if not isinstance(data, Mapping):
        raise TypeError("scenario must be a mapping")
    return InvariantScenario(
        id=str(data["id"]),
        category=str(data["category"]),
        description=str(data.get("description") or ""),
        require_absent_markers=tuple(
            str(x) for x in (data.get("require_absent_markers") or ())
        ),
        fail_on_markers=tuple(str(x) for x in (data.get("fail_on_markers") or ())),
        use_builtin=bool(data.get("use_builtin", True)),
        hard=bool(data.get("hard", True)),
        notes=str(data.get("notes") or ""),
    )


def load_scenarios(path: str | Path) -> list[InvariantScenario]:
    """Load scenarios from a JSON file or directory of JSON files."""
    p = Path(path)
    files: list[Path]
    if p.is_dir():
        files = sorted(p.glob("*.json"))
    elif p.is_file():
        files = [p]
    else:
        raise FileNotFoundError(f"scenario path not found: {p}")

    scenarios: list[InvariantScenario] = []
    for fp in files:
        raw = json.loads(fp.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            for item in raw:
                scenarios.append(scenario_from_mapping(item))
        elif isinstance(raw, dict) and "scenarios" in raw:
            for item in raw["scenarios"]:
                scenarios.append(scenario_from_mapping(item))
        elif isinstance(raw, dict):
            scenarios.append(scenario_from_mapping(raw))
        else:
            raise ValueError(f"unsupported scenario file shape: {fp}")
    return scenarios


def scenarios_digest(scenarios: Sequence[InvariantScenario]) -> str:
    payload = [
        {
            "id": s.id,
            "category": s.category,
            "description": s.description,
            "fail_on_markers": list(s.fail_on_markers),
            "require_absent_markers": list(s.require_absent_markers),
            "use_builtin": s.use_builtin,
            "hard": s.hard,
        }
        for s in scenarios
    ]
    return digest_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def ensure_categories_covered(scenarios: Iterable[InvariantScenario]) -> None:
    """Raise if the suite misses a required category."""
    seen = {s.category for s in scenarios}
    missing = INVARIANT_CATEGORIES - seen
    if missing:
        raise ValueError(f"invariant suite missing categories: {sorted(missing)}")
