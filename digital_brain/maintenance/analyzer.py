"""Deterministic dream analyzer: sanitized snapshot → typed findings/intents.

The analyzer classifies evidence into housekeeping, memory, behaviour, or
engineering lanes. It has no Neo4j, repo, quarantine, activation, or network
access — only pure functions over a sanitized packet.

All evidence strings are untrusted data. ChangeIntent text fields reject
instruction/tool-shaped content. Engineering outages cannot emit semantic
memory effects.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Literal, Mapping, Sequence, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from digital_brain.maintenance.invariants import SEMANTIC_MEMORY_EFFECT_TYPES
from digital_brain.maintenance.models import (
    EVIDENCE_STRENGTHS,
    FINDING_LANES,
    PROPOSAL_KINDS,
    digest_text,
)
from digital_brain.maintenance.privacy import assert_no_intimate_fields, redact_packet
from digital_brain.maintenance.snapshot import FrozenSnapshot

ANALYZER_VERSION = "1"
MAX_SUMMARY_LEN = 512
MAX_RULE_ID_LEN = 128
MAX_ID_LEN = 128
MAX_EVIDENCE_IDS = 64

# Named extension slots the overlay compiler may later target (closed set).
EXTENSION_SLOTS: frozenset[str] = frozenset(
    {
        "session_preamble",
        "route_guidance",
        "fail_soft_language",
        "retrieval_hints",
        "engineering_note",
    }
)

CHANGE_OPERATIONS: frozenset[str] = frozenset(
    {
        "add_rule",
        "revise_rule",
        "report",
        "file_issue",
        "propose_only",
    }
)

# Closed effect-type vocabulary for ChangeIntent.
EFFECT_TYPES: frozenset[str] = frozenset(
    {
        # Memory (proposal only — never auto)
        "propose_alias",
        "propose_revoke_alias",
        "propose_missing_memory",
        "propose_dispute",
        # Behaviour / overlay
        "overlay_rule",
        "policy_knob",
        # Housekeeping
        "retention_report",
        "sensor_digest",
        # Engineering (non-semantic)
        "file_issue",
        "infra_note",
        "engineering_patch",
        "test_gap",
    }
)

# Engineering-only effect types (cannot be used for semantic memory).
ENGINEERING_EFFECT_TYPES: frozenset[str] = frozenset(
    {
        "file_issue",
        "infra_note",
        "engineering_patch",
        "test_gap",
    }
)

MEMORY_KINDS: frozenset[str] = frozenset(
    {"entity_wrong", "miss", "invent", "claim_false"}
)

# Infra / code failure classes that must route to engineering.
ENGINEERING_ERROR_CLASSES: frozenset[str] = frozenset(
    {
        "mcp_outage",
        "mcp_timeout",
        "mcp_error",
        "embedding_timeout",
        "embedding_failure",
        "embedding_outage",
        "code_error",
        "code_exception",
        "infra_timeout",
        "timeout",
        "connection_error",
        "ollama_timeout",
        "ollama_outage",
    }
)

# Tools whose failures are infrastructure, not life-memory.
ENGINEERING_TOOLS: frozenset[str] = frozenset(
    {
        "mcp",
        "embed",
        "embedding",
        "ollama",
        "read_neo4j_cypher",
        "write_neo4j_cypher",
    }
)

RISK_TIERS: frozenset[str] = frozenset({"low", "medium", "high"})

# Instruction / tool-shaped injection patterns (case-insensitive).
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?",
        r"disregard\s+(all\s+)?(previous|prior)\s+instructions?",
        r"system\s*:\s*",
        r"<\|?(system|assistant|tool)\|?>",
        r"</?tool\b",
        r"function_call\s*[:(]",
        r"tool_call\s*[:(]",
        r"run_terminal_command",
        r"write_neo4j_cypher\s*\(",
        r"apply_alias\s*\(",
        r"mint_activation_authority",
        r"\bexec\s*\(",
        r"\bos\.system\b",
        r"```\s*(bash|sh|zsh|shell)\b",
        r"you\s+are\s+now\s+",
        r"new\s+instructions?\s*:",
        r"\[INST\]",
        r"<<\s*SYS\s*>>",
    )
)

Lane = Literal["housekeeping", "memory", "behaviour", "engineering"]
Strength = Literal["tentative", "moderate", "strong"]
RiskTier = Literal["low", "medium", "high"]


class SchemaRejectError(ValueError):
    """Raised when typed analyzer output violates the closed schema."""


class InjectionRejectError(ValueError):
    """Raised when ChangeIntent fields contain instruction/tool-shaped content."""


def delimit_evidence(text: str) -> str:
    """Wrap untrusted evidence text in explicit delimiters."""
    return (
        "<<<EVIDENCE_UNTRUSTED>>>\n"
        f"{text}\n"
        "<<<END_EVIDENCE_UNTRUSTED>>>"
    )


def contains_injection(text: str) -> bool:
    if not text:
        return False
    for pat in _INJECTION_PATTERNS:
        if pat.search(text):
            return True
    return False


def assert_no_injection(text: str, *, field: str) -> None:
    if contains_injection(text):
        raise InjectionRejectError(f"injection_shaped_content:{field}")


def _reject_unknown_keys(data: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    extra = set(data.keys()) - allowed
    if extra:
        raise SchemaRejectError(f"unknown_{label}_fields:{sorted(extra)}")


class EvidenceItemView(BaseModel):
    """Sanitized evidence row visible to the analyzer (no holdout, no intimate raw)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., max_length=MAX_ID_LEN)
    evidence_id: str | None = Field(default=None, max_length=MAX_ID_LEN)
    evidence_label: str | None = Field(default=None, max_length=64)
    label: str | None = Field(default=None, max_length=64)
    role: str | None = Field(default=None, max_length=32)
    evidence_hash: str | None = Field(default=None, max_length=128)
    sensitivity: str | None = Field(default=None, max_length=32)
    kind: str | None = Field(default=None, max_length=64)
    route: str | None = Field(default=None, max_length=32)
    tool: str | None = Field(default=None, max_length=128)
    tool_outcome: str | None = Field(default=None, max_length=64)
    task_outcome: str | None = Field(default=None, max_length=64)
    error_class: str | None = Field(default=None, max_length=128)
    observed_at: str | None = Field(default=None, max_length=64)
    created_at: str | None = Field(default=None, max_length=64)
    eligible_exposure: bool | None = None
    revoked: bool | None = None
    redacted_summary: str | None = Field(default=None, max_length=MAX_SUMMARY_LEN)
    correlation_hmac: str | None = Field(default=None, max_length=128)
    hmac_key_version: str | None = Field(default=None, max_length=64)
    recurrence_key: str | None = Field(default=None, max_length=256)
    decision_point: str | None = Field(default=None, max_length=128)
    approach: str | None = Field(default=None, max_length=128)
    outcome_source: str | None = Field(default=None, max_length=64)
    class_key: str | None = Field(default=None, max_length=128)
    lane: str | None = Field(default=None, max_length=32)
    evidence_strength: str | None = Field(default=None, max_length=32)


class SanitizedEvidenceSnapshot(BaseModel):
    """Analyzer-visible snapshot projection (holdout omitted by construction)."""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(..., max_length=MAX_ID_LEN)
    dream_id: str = Field(..., max_length=MAX_ID_LEN)
    cutoff_at: str = Field(..., max_length=64)
    source_ids_digest: str = Field(..., max_length=128)
    source_counts: dict[str, Any] = Field(default_factory=dict)
    sensitivity_max: str = Field(default="public_ops", max_length=32)
    harness_generation_id: str = Field(..., max_length=128)
    taxonomy_version: str = Field(default="1", max_length=32)
    redaction_policy_version: str = Field(default="1", max_length=32)
    generation_ids: list[str] = Field(default_factory=list)
    counterevidence_ids: list[str] = Field(default_factory=list)
    eligible_exposure_ids: list[str] = Field(default_factory=list)
    eligible_exposure_count: int = 0
    items: list[EvidenceItemView] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_holdout_leak(self) -> SanitizedEvidenceSnapshot:
        if "holdout" in self.source_counts or "holdout_ids" in self.source_counts:
            raise SchemaRejectError("holdout_must_not_appear_in_analyzer_snapshot")
        # Items must only reference generation/counterevidence.
        allowed = set(self.generation_ids) | set(self.counterevidence_ids)
        for item in self.items:
            if allowed and item.id not in allowed:
                raise SchemaRejectError(f"item_not_in_generation_or_counterevidence:{item.id}")
        return self

    @classmethod
    def from_frozen(cls, frozen: FrozenSnapshot) -> SanitizedEvidenceSnapshot:
        packet = frozen.analyzer_packet()
        assert_no_intimate_fields(packet)
        return cls.from_packet(packet)

    @classmethod
    def from_packet(cls, packet: Mapping[str, Any]) -> SanitizedEvidenceSnapshot:
        """Build from an analyzer_packet dict; strips unknown top-level keys."""
        clean = redact_packet(dict(packet))
        assert_no_intimate_fields(clean)
        # Drop holdout keys if a caller accidentally included them.
        counts = dict(clean.get("source_counts") or {})
        counts.pop("holdout", None)
        counts.pop("holdout_ids", None)
        items_raw = clean.get("items") or []
        # Evidence items: keep only known fields (extra keys stripped, not error —
        # redacted packets may carry future-safe metadata; strictness is on *output*).
        allowed_item = set(EvidenceItemView.model_fields.keys())
        items: list[dict[str, Any]] = []
        for raw in items_raw:
            if not isinstance(raw, Mapping):
                continue
            items.append({k: v for k, v in raw.items() if k in allowed_item})
        payload = {
            "snapshot_id": clean.get("snapshot_id"),
            "dream_id": clean.get("dream_id"),
            "cutoff_at": clean.get("cutoff_at"),
            "source_ids_digest": clean.get("source_ids_digest"),
            "source_counts": counts,
            "sensitivity_max": clean.get("sensitivity_max") or "public_ops",
            "harness_generation_id": clean.get("harness_generation_id"),
            "taxonomy_version": clean.get("taxonomy_version") or "1",
            "redaction_policy_version": clean.get("redaction_policy_version") or "1",
            "generation_ids": list(clean.get("generation_ids") or []),
            "counterevidence_ids": list(clean.get("counterevidence_ids") or []),
            "eligible_exposure_ids": list(clean.get("eligible_exposure_ids") or []),
            "eligible_exposure_count": int(clean.get("eligible_exposure_count") or 0),
            "items": items,
        }
        return cls.model_validate(payload)


class Finding(BaseModel):
    """Typed analyzer finding (coordinator persists via create_finding)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., max_length=MAX_ID_LEN)
    dream_id: str = Field(..., max_length=MAX_ID_LEN)
    snapshot_id: str = Field(..., max_length=MAX_ID_LEN)
    class_key: str = Field(..., max_length=MAX_ID_LEN)
    lane: Lane
    summary: str = Field(..., min_length=1, max_length=MAX_SUMMARY_LEN)
    evidence_strength: Strength
    evidence_ids: list[str] = Field(default_factory=list, max_length=MAX_EVIDENCE_IDS)
    recurrence_key: str = Field(..., max_length=256)
    support_counts: dict[str, Any] = Field(default_factory=dict)
    counterevidence_ids: list[str] = Field(default_factory=list, max_length=MAX_EVIDENCE_IDS)
    material_digest: str = Field(..., max_length=128)

    @field_validator("lane")
    @classmethod
    def _lane_closed(cls, v: str) -> str:
        if v not in FINDING_LANES:
            raise ValueError(f"unknown_lane:{v}")
        return v

    @field_validator("evidence_strength")
    @classmethod
    def _strength_closed(cls, v: str) -> str:
        if v not in EVIDENCE_STRENGTHS:
            raise ValueError(f"unknown_evidence_strength:{v}")
        return v

    @field_validator("summary")
    @classmethod
    def _summary_bounds(cls, v: str) -> str:
        if len(v) > MAX_SUMMARY_LEN:
            raise ValueError("summary_too_long")
        return v

    def to_store_payload(self, *, run_id: str, epoch: int, lease_key: str = "maintenance") -> dict[str, Any]:
        return {
            "id": self.id,
            "dream_id": self.dream_id,
            "snapshot_id": self.snapshot_id,
            "class_key": self.class_key,
            "lane": self.lane,
            "summary": self.summary,
            "evidence_strength": self.evidence_strength,
            "support_counts_json": json.dumps(
                self.support_counts, sort_keys=True, separators=(",", ":")
            ),
            "counterevidence_json": json.dumps(
                list(self.counterevidence_ids), sort_keys=True, separators=(",", ":")
            ),
            "evidence_ids": list(self.evidence_ids),
            "run_id": run_id,
            "epoch": epoch,
            "lease_key": lease_key,
        }


class ChangeIntent(BaseModel):
    """Schema-validated change intent for later deterministic compilation."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., max_length=MAX_ID_LEN)
    dream_id: str = Field(..., max_length=MAX_ID_LEN)
    snapshot_id: str = Field(..., max_length=MAX_ID_LEN)
    lane: Lane
    effect_type: str = Field(..., max_length=64)
    operation: str = Field(..., max_length=64)
    rule_id: str = Field(..., max_length=MAX_RULE_ID_LEN)
    summary: str = Field(..., min_length=1, max_length=MAX_SUMMARY_LEN)
    expected_outcome: str = Field(..., min_length=1, max_length=MAX_SUMMARY_LEN)
    risk_tier: RiskTier = "low"
    evidence_ids: list[str] = Field(default_factory=list, max_length=MAX_EVIDENCE_IDS)
    counterevidence_ids: list[str] = Field(default_factory=list, max_length=MAX_EVIDENCE_IDS)
    recurrence_key: str = Field(..., max_length=256)
    material_digest: str = Field(..., max_length=128)
    proposal_kind: str = Field(..., max_length=64)
    target_skill: str | None = Field(default=None, max_length=128)
    extension_slot: str | None = Field(default=None, max_length=64)
    target_ref: str | None = Field(default=None, max_length=256)
    evidence_strength: Strength = "tentative"

    @field_validator("lane")
    @classmethod
    def _lane_closed(cls, v: str) -> str:
        if v not in FINDING_LANES:
            raise ValueError(f"unknown_lane:{v}")
        return v

    @field_validator("effect_type")
    @classmethod
    def _effect_closed(cls, v: str) -> str:
        if v not in EFFECT_TYPES:
            raise ValueError(f"unknown_effect_type:{v}")
        return v

    @field_validator("operation")
    @classmethod
    def _op_closed(cls, v: str) -> str:
        if v not in CHANGE_OPERATIONS:
            raise ValueError(f"unknown_operation:{v}")
        return v

    @field_validator("extension_slot")
    @classmethod
    def _slot_closed(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in EXTENSION_SLOTS:
            raise ValueError(f"unknown_extension_slot:{v}")
        return v

    @field_validator("proposal_kind")
    @classmethod
    def _kind_closed(cls, v: str) -> str:
        if v not in PROPOSAL_KINDS:
            raise ValueError(f"unknown_proposal_kind:{v}")
        return v

    @field_validator("risk_tier")
    @classmethod
    def _risk_closed(cls, v: str) -> str:
        if v not in RISK_TIERS:
            raise ValueError(f"unknown_risk_tier:{v}")
        return v

    @field_validator("summary", "expected_outcome", "rule_id")
    @classmethod
    def _no_injection_fields(cls, v: str, info: Any) -> str:
        assert_no_injection(v, field=str(info.field_name))
        return v

    @model_validator(mode="after")
    def _engineering_no_semantic(self) -> ChangeIntent:
        if self.lane == "engineering":
            if self.effect_type in SEMANTIC_MEMORY_EFFECT_TYPES:
                raise ValueError(
                    f"engineering_cannot_emit_semantic_memory_effect:{self.effect_type}"
                )
            if self.effect_type not in ENGINEERING_EFFECT_TYPES:
                raise ValueError(
                    f"engineering_effect_type_not_allowed:{self.effect_type}"
                )
            if self.proposal_kind != "engineering":
                raise ValueError("engineering_lane_requires_engineering_proposal_kind")
        if self.effect_type in SEMANTIC_MEMORY_EFFECT_TYPES:
            # Semantic types are not in EFFECT_TYPES; belt-and-suspenders.
            raise ValueError(f"semantic_memory_effect_forbidden_in_intent:{self.effect_type}")
        if self.extension_slot is not None and self.lane not in {"behaviour", "engineering"}:
            # Overlays are behaviour; engineering_note slot allowed for eng.
            if self.extension_slot != "engineering_note":
                raise ValueError("extension_slot_only_for_behaviour_or_engineering")
        return self


AnalyzerOutput = Union[Finding, ChangeIntent]


def material_digest_for(
    *,
    recurrence_key: str,
    evidence_ids: Sequence[str],
    evidence_hashes: Sequence[str] | None = None,
    class_key: str = "",
) -> str:
    """Stable digest for suppression: same key + same material → suppress."""
    payload = {
        "class_key": class_key,
        "evidence_hashes": sorted(str(h) for h in (evidence_hashes or ())),
        "evidence_ids": sorted(str(i) for i in evidence_ids),
        "recurrence_key": recurrence_key,
    }
    return digest_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def is_suppressed(
    recurrence_key: str,
    material_digest: str,
    rejected: Mapping[str, str] | None,
) -> bool:
    """Rejected proposals stay suppressed until a material same-key delta.

    ``rejected`` maps recurrence_key → last rejected material_digest.
    Unrelated new evidence (different key) does not unsuppress.
    Same key + same digest stays suppressed; same key + different digest may emit.
    """
    if not rejected:
        return False
    prior = rejected.get(recurrence_key)
    if prior is None:
        return False
    return prior == material_digest


def _strength(count: int) -> Strength:
    if count >= 3:
        return "strong"
    if count >= 2:
        return "moderate"
    return "tentative"


def _is_engineering_item(item: EvidenceItemView) -> bool:
    err = (item.error_class or "").lower().strip()
    if err in ENGINEERING_ERROR_CLASSES:
        return True
    tool = (item.tool or "").lower()
    outcome = (item.tool_outcome or "").lower()
    if outcome in {"fail", "timeout", "conflict"} and any(
        t in tool for t in ("embed", "mcp", "ollama")
    ):
        return True
    # Explicit infra error classes on READ/WRITE failures.
    if err and any(
        token in err
        for token in ("mcp", "embed", "ollama", "infra", "connection", "code_")
    ):
        return True
    return False


def _is_memory_item(item: EvidenceItemView) -> bool:
    kind = (item.kind or "").lower().strip()
    return kind in MEMORY_KINDS


def _is_behaviour_item(item: EvidenceItemView) -> bool:
    # Non-engineering route failures that suggest harness guidance.
    if _is_engineering_item(item) or _is_memory_item(item):
        return False
    route = (item.route or "").upper()
    outcome = (item.tool_outcome or "").lower()
    if route in {"READ", "WRITE", "FEEDBACK", "SKIP"} and outcome in {
        "empty",
        "fail",
    }:
        # Soft behavioural signal (no_hits etc.) — not infra.
        err = (item.error_class or "").lower()
        if err in {"no_hits", "empty_result", "route_mismatch"}:
            return True
        if not err and outcome == "empty":
            return True
    return False


def _stable_id(*parts: str) -> str:
    h = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:16]
    return h


def _item_map(snapshot: SanitizedEvidenceSnapshot) -> dict[str, EvidenceItemView]:
    return {i.id: i for i in snapshot.items}


def analyze(
    snapshot: SanitizedEvidenceSnapshot | FrozenSnapshot | Mapping[str, Any],
    *,
    rejected: Mapping[str, str] | None = None,
    max_findings: int = 32,
) -> list[AnalyzerOutput]:
    """Classify sanitized evidence into typed findings and change intents.

    Parameters
    ----------
    snapshot:
        ``SanitizedEvidenceSnapshot``, ``FrozenSnapshot``, or analyzer_packet dict.
    rejected:
        Optional map of ``recurrence_key → material_digest`` for suppressed
        rejections. Unrelated keys never recreate a suppressed proposal; a
        material same-key delta may.
    """
    if isinstance(snapshot, FrozenSnapshot):
        snap = SanitizedEvidenceSnapshot.from_frozen(snapshot)
    elif isinstance(snapshot, SanitizedEvidenceSnapshot):
        snap = snapshot
    elif isinstance(snapshot, Mapping):
        snap = SanitizedEvidenceSnapshot.from_packet(snapshot)
    else:
        raise TypeError("snapshot must be SanitizedEvidenceSnapshot, FrozenSnapshot, or mapping")

    assert_no_intimate_fields(snap.model_dump())

    items_by_id = _item_map(snap)
    gen_ids = [i for i in snap.generation_ids if i in items_by_id]
    counter_ids = list(snap.counterevidence_ids)

    memory_ids: list[str] = []
    eng_ids: list[str] = []
    behaviour_ids: list[str] = []
    other_ids: list[str] = []

    for eid in gen_ids:
        item = items_by_id[eid]
        # Intimate sensitivity: metadata only — never drive memory proposals.
        if (item.sensitivity or "") == "intimate":
            other_ids.append(eid)
            continue
        if _is_engineering_item(item):
            eng_ids.append(eid)
        elif _is_memory_item(item):
            memory_ids.append(eid)
        elif _is_behaviour_item(item):
            behaviour_ids.append(eid)
        else:
            other_ids.append(eid)

    outputs: list[AnalyzerOutput] = []

    # --- Memory lane ---
    if memory_ids:
        by_kind: dict[str, list[str]] = {}
        for eid in memory_ids:
            kind = (items_by_id[eid].kind or "memory").lower()
            by_kind.setdefault(kind, []).append(eid)

        for kind, ids in sorted(by_kind.items()):
            rkey = f"memory:{kind}"
            hashes = [items_by_id[i].evidence_hash or "" for i in ids]
            md = material_digest_for(
                recurrence_key=rkey,
                evidence_ids=ids,
                evidence_hashes=hashes,
                class_key=kind,
            )
            if is_suppressed(rkey, md, rejected):
                continue
            fid = f"find-{snap.dream_id}-{_stable_id(rkey, md)}"
            finding = Finding(
                id=fid,
                dream_id=snap.dream_id,
                snapshot_id=snap.snapshot_id,
                class_key=f"memory_{kind}",
                lane="memory",
                summary=f"{len(ids)} {kind} signal(s) need owner review",
                evidence_strength=_strength(len(ids)),
                evidence_ids=sorted(ids),
                recurrence_key=rkey,
                support_counts={"generation": len(ids)},
                counterevidence_ids=list(counter_ids),
                material_digest=md,
            )
            outputs.append(finding)

            effect = {
                "entity_wrong": "propose_alias",
                "miss": "propose_missing_memory",
                "invent": "propose_dispute",
                "claim_false": "propose_dispute",
            }.get(kind, "propose_missing_memory")
            intent = ChangeIntent(
                id=f"intent-{snap.dream_id}-{_stable_id(rkey, md, 'ci')}",
                dream_id=snap.dream_id,
                snapshot_id=snap.snapshot_id,
                lane="memory",
                effect_type=effect,
                operation="propose_only",
                rule_id=f"mem-{kind}",
                summary=f"Owner review for {kind} memory signal",
                expected_outcome="proposal_only_no_auto_memory_mutation",
                risk_tier="medium" if kind in {"entity_wrong", "claim_false"} else "low",
                evidence_ids=sorted(ids),
                counterevidence_ids=list(counter_ids),
                recurrence_key=rkey,
                material_digest=md,
                proposal_kind="memory_suggestion" if kind != "entity_wrong" else "alias",
                target_ref=f"dream:{snap.dream_id}:memory:{kind}",
                evidence_strength=_strength(len(ids)),
            )
            outputs.append(intent)

    # --- Engineering lane ---
    if eng_ids:
        by_err: dict[str, list[str]] = {}
        for eid in eng_ids:
            item = items_by_id[eid]
            err = (item.error_class or item.tool_outcome or "infra_failure").lower()
            by_err.setdefault(err, []).append(eid)

        for err, ids in sorted(by_err.items()):
            rkey = f"engineering:{err}"
            hashes = [items_by_id[i].evidence_hash or "" for i in ids]
            md = material_digest_for(
                recurrence_key=rkey,
                evidence_ids=ids,
                evidence_hashes=hashes,
                class_key=err,
            )
            if is_suppressed(rkey, md, rejected):
                continue
            fid = f"find-{snap.dream_id}-{_stable_id(rkey, md)}"
            finding = Finding(
                id=fid,
                dream_id=snap.dream_id,
                snapshot_id=snap.snapshot_id,
                class_key=f"engineering_{err}",
                lane="engineering",
                summary=f"{len(ids)} engineering/infra signal(s): {err}",
                evidence_strength=_strength(len(ids)),
                evidence_ids=sorted(ids),
                recurrence_key=rkey,
                support_counts={"generation": len(ids)},
                counterevidence_ids=[],
                material_digest=md,
            )
            outputs.append(finding)

            # Non-semantic effects only.
            effect_type = "infra_note"
            if "embed" in err:
                effect_type = "infra_note"
            elif "code" in err:
                effect_type = "file_issue"
            intent = ChangeIntent(
                id=f"intent-{snap.dream_id}-{_stable_id(rkey, md, 'ci')}",
                dream_id=snap.dream_id,
                snapshot_id=snap.snapshot_id,
                lane="engineering",
                effect_type=effect_type,
                operation="file_issue",
                rule_id=f"eng-{err}"[:MAX_RULE_ID_LEN],
                summary=f"Engineering follow-up for {err} (non-semantic)",
                expected_outcome="engineering_issue_no_semantic_memory_effect",
                risk_tier="low",
                evidence_ids=sorted(ids),
                counterevidence_ids=[],
                recurrence_key=rkey,
                material_digest=md,
                proposal_kind="engineering",
                extension_slot="engineering_note",
                target_skill=None,
                target_ref=f"dream:{snap.dream_id}:engineering:{err}",
                evidence_strength=_strength(len(ids)),
            )
            # Guard: must not be a semantic memory effect.
            assert intent.effect_type not in SEMANTIC_MEMORY_EFFECT_TYPES
            assert intent.effect_type in ENGINEERING_EFFECT_TYPES
            outputs.append(intent)

    # --- Behaviour lane ---
    if behaviour_ids:
        rkey = "behaviour:route_empty_or_fail"
        hashes = [items_by_id[i].evidence_hash or "" for i in behaviour_ids]
        md = material_digest_for(
            recurrence_key=rkey,
            evidence_ids=behaviour_ids,
            evidence_hashes=hashes,
            class_key="route_signal",
        )
        if not is_suppressed(rkey, md, rejected):
            outputs.append(
                Finding(
                    id=f"find-{snap.dream_id}-{_stable_id(rkey, md)}",
                    dream_id=snap.dream_id,
                    snapshot_id=snap.snapshot_id,
                    class_key="behaviour_route_signal",
                    lane="behaviour",
                    summary=f"{len(behaviour_ids)} route empty/fail signal(s)",
                    evidence_strength=_strength(len(behaviour_ids)),
                    evidence_ids=sorted(behaviour_ids),
                    recurrence_key=rkey,
                    support_counts={"generation": len(behaviour_ids)},
                    counterevidence_ids=list(counter_ids),
                    material_digest=md,
                )
            )
            outputs.append(
                ChangeIntent(
                    id=f"intent-{snap.dream_id}-{_stable_id(rkey, md, 'ci')}",
                    dream_id=snap.dream_id,
                    snapshot_id=snap.snapshot_id,
                    lane="behaviour",
                    effect_type="overlay_rule",
                    operation="add_rule",
                    rule_id="route-empty-guidance",
                    summary="Optional fail-soft retrieval guidance for empty READ",
                    expected_outcome="owner_approved_overlay_trial_only",
                    risk_tier="low",
                    evidence_ids=sorted(behaviour_ids),
                    counterevidence_ids=list(counter_ids),
                    recurrence_key=rkey,
                    material_digest=md,
                    proposal_kind="overlay",
                    target_skill="digital-brain-buddy-session",
                    extension_slot="fail_soft_language",
                    target_ref=f"dream:{snap.dream_id}:behaviour:route",
                    evidence_strength=_strength(len(behaviour_ids)),
                )
            )

    # --- Housekeeping digest (always when any generation evidence) ---
    if gen_ids:
        rkey = "housekeeping:sensor_digest"
        md = material_digest_for(
            recurrence_key=rkey,
            evidence_ids=gen_ids[:16],
            evidence_hashes=[items_by_id[i].evidence_hash or "" for i in gen_ids[:16]],
            class_key="sensor_digest",
        )
        if not is_suppressed(rkey, md, rejected):
            total = snap.source_counts.get("total") or len(gen_ids)
            flat_counts = {
                k: v
                for k, v in snap.source_counts.items()
                if isinstance(v, int) and k not in {"holdout", "holdout_ids"}
            }
            outputs.append(
                Finding(
                    id=f"find-{snap.dream_id}-{_stable_id(rkey, md)}",
                    dream_id=snap.dream_id,
                    snapshot_id=snap.snapshot_id,
                    class_key="sensor_digest",
                    lane="housekeeping",
                    summary=(
                        f"Reviewed {total} evidence item(s); "
                        f"{len(memory_ids)} memory, {len(eng_ids)} engineering, "
                        f"{len(behaviour_ids)} behaviour"
                    )[:MAX_SUMMARY_LEN],
                    evidence_strength="strong",
                    evidence_ids=sorted(gen_ids[:16]),
                    recurrence_key=rkey,
                    support_counts=flat_counts,
                    counterevidence_ids=list(counter_ids),
                    material_digest=md,
                )
            )

    if len(outputs) > max_findings:
        outputs = outputs[:max_findings]

    # Final schema pass — re-validate to catch any drift.
    validated: list[AnalyzerOutput] = []
    for item in outputs:
        if isinstance(item, Finding):
            validated.append(Finding.model_validate(item.model_dump()))
        else:
            validated.append(ChangeIntent.model_validate(item.model_dump()))
    return validated


def validate_change_intent_dict(data: Mapping[str, Any]) -> ChangeIntent:
    """Strict parse of a ChangeIntent mapping (rejects unknown fields)."""
    try:
        return ChangeIntent.model_validate(dict(data))
    except ValidationError as exc:
        raise SchemaRejectError(str(exc)) from exc


def validate_finding_dict(data: Mapping[str, Any]) -> Finding:
    try:
        return Finding.model_validate(dict(data))
    except ValidationError as exc:
        raise SchemaRejectError(str(exc)) from exc


def engineering_intents_are_non_semantic(outputs: Sequence[AnalyzerOutput]) -> bool:
    """Return True when no engineering output carries a semantic memory effect."""
    for item in outputs:
        if isinstance(item, ChangeIntent) and item.lane == "engineering":
            if item.effect_type in SEMANTIC_MEMORY_EFFECT_TYPES:
                return False
            if item.effect_type not in ENGINEERING_EFFECT_TYPES:
                return False
            if item.proposal_kind != "engineering":
                return False
        if isinstance(item, Finding) and item.lane == "engineering":
            if item.class_key.startswith("memory_"):
                return False
    return True


# Optional Grok adapter scaffolding (tests inspect argv; never uses --yolo).
GROK_FORBIDDEN_FLAGS: frozenset[str] = frozenset(
    {"--yolo", "--auto-update", "--auto-approve", "--writable"}
)


def build_grok_analyzer_argv(
    *,
    snapshot_dir: str,
    schema_path: str,
    max_turns: int = 4,
    model: str = "grok",
) -> list[str]:
    """Construct a safe headless Grok argv for external sanitized snapshots.

    Must run **outside** the repo against a sanitized snapshot directory with
    read-only tools, schema-constrained output, bounded turns, no auto-update,
    and **no ``--yolo``**.
    """
    if max_turns < 1 or max_turns > 8:
        raise ValueError("max_turns out of bounds")
    argv = [
        "grok",
        "-p",
        "--prompt-file",
        str(
            # Relative name only in argv surface; caller supplies real path.
            "digital_brain/maintenance/prompts/analyze.md"
        ),
        "--input-dir",
        snapshot_dir,
        "--schema",
        schema_path,
        "--max-turns",
        str(max_turns),
        "--readonly",
        "--no-auto-update",
        "--output-format",
        "json",
        "--model",
        model,
    ]
    for flag in argv:
        if flag in GROK_FORBIDDEN_FLAGS:
            raise RuntimeError(f"forbidden_grok_flag:{flag}")
    if "--yolo" in argv:
        raise RuntimeError("yolo_forbidden")
    return argv


def assert_safe_grok_argv(argv: Sequence[str]) -> None:
    lowered = [str(a) for a in argv]
    for bad in GROK_FORBIDDEN_FLAGS:
        if bad in lowered:
            raise AssertionError(f"unsafe_grok_flag:{bad}")
    if any(a == "--yolo" or a.startswith("--yolo=") for a in lowered):
        raise AssertionError("yolo_forbidden")
    if "--readonly" not in lowered and "--read-only" not in lowered:
        raise AssertionError("grok_must_be_readonly")
    if "--no-auto-update" not in lowered:
        raise AssertionError("grok_must_disable_auto_update")
