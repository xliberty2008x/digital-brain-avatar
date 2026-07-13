"""Deterministic evidence snapshot freeze for DreamRun.

Same ledger projection + policy → same source_ids_digest and membership roles.
Revoked and late events are excluded. Generation / counterevidence / holdout
roles are partitioned up front so later evaluators can keep holdout hidden.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from digital_brain.maintenance.models import (
    EVIDENCE_ROLES,
    MAINTENANCE_SCHEMA_VERSION,
    TAXONOMY_VERSION,
    digest_text,
)
from digital_brain.maintenance.privacy import (
    REDACTION_POLICY_VERSION,
    correlation_hmac,
    max_sensitivity,
    redact_evidence_record,
    redact_packet,
)

# Default holdout fraction when policy does not pin explicit holdout ids.
DEFAULT_HOLDOUT_RATIO = 0.2

# Permille scale for stable ratio comparisons (0–1000).
_HOLDOUT_SCALE = 1000


def _canonical_json(payload: Mapping[str, Any] | Sequence[Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def parse_iso_utc(value: str) -> datetime:
    """Parse an ISO-8601 timestamp to aware UTC datetime."""
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_ts(value: str) -> str:
    """Canonical Zulu form for cutoff comparisons and digests."""
    return (
        parse_iso_utc(value)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class EvidenceItem:
    """One ledger evidence row available to snapshot selection."""

    id: str
    label: str  # Feedback | RunEvent
    observed_at: str
    evidence_hash: str
    sensitivity: str = "public_ops"
    revoked: bool = False
    role_hint: str | None = None  # generation | counterevidence (not holdout)
    eligible_exposure: bool | None = None
    kind: str | None = None
    route: str | None = None
    tool: str | None = None
    tool_outcome: str | None = None
    task_outcome: str | None = None
    error_class: str | None = None
    redacted_summary: str | None = None
    # Gotcha / corrected RunEvent taxonomy (must survive freeze → analyzer).
    recurrence_key: str | None = None
    approach: str | None = None
    decision_point: str | None = None
    # Never copied into analyzer/report packets.
    raw_payload: str | None = None
    request_fingerprint: str | None = None
    is_counterevidence: bool = False

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "evidence_id": self.id,
            "evidence_label": self.label,
            "label": self.label,
            "observed_at": self.observed_at,
            "evidence_hash": self.evidence_hash,
            "sensitivity": self.sensitivity,
            "revoked": self.revoked,
            "role_hint": self.role_hint,
            "eligible_exposure": self.eligible_exposure,
            "kind": self.kind,
            "route": self.route,
            "tool": self.tool,
            "tool_outcome": self.tool_outcome,
            "task_outcome": self.task_outcome,
            "error_class": self.error_class,
            "redacted_summary": self.redacted_summary,
            "recurrence_key": self.recurrence_key,
            "approach": self.approach,
            "decision_point": self.decision_point,
            "raw_payload": self.raw_payload,
            "request_fingerprint": self.request_fingerprint,
            "is_counterevidence": self.is_counterevidence,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> EvidenceItem:
        if not isinstance(data, Mapping):
            raise TypeError("evidence item must be a mapping")
        eid = str(data.get("id") or data.get("evidence_id") or "").strip()
        if not eid:
            raise ValueError("evidence item requires id")
        label = str(
            data.get("label")
            or data.get("evidence_label")
            or data.get("type")
            or "Feedback"
        ).strip()
        observed = data.get("observed_at") or data.get("created_at")
        if not observed:
            raise ValueError(f"evidence {eid!r} requires observed_at/created_at")
        observed_at = normalize_ts(str(observed))
        evidence_hash = str(
            data.get("evidence_hash")
            or data.get("hash")
            or data.get("request_fingerprint")
            or digest_text(eid)
        ).strip()
        sensitivity = str(data.get("sensitivity") or "public_ops").strip()
        role_hint = data.get("role_hint") or data.get("role")
        if role_hint is not None:
            role_hint = str(role_hint).strip()
            if role_hint == "holdout":
                # Holdout is assigned by policy, not self-declared on sensors.
                role_hint = "generation"
            if role_hint not in EVIDENCE_ROLES and role_hint != "generation":
                if role_hint not in {"generation", "counterevidence"}:
                    role_hint = None
        is_counter = bool(
            data.get("is_counterevidence")
            or role_hint == "counterevidence"
            or data.get("task_outcome") == "success"
            and data.get("kind") == "praise"
        )
        if is_counter and role_hint is None:
            role_hint = "counterevidence"
        return cls(
            id=eid,
            label=label,
            observed_at=observed_at,
            evidence_hash=evidence_hash,
            sensitivity=sensitivity,
            revoked=bool(data.get("revoked")),
            role_hint=role_hint,
            eligible_exposure=(
                None
                if data.get("eligible_exposure") is None
                else bool(data.get("eligible_exposure"))
            ),
            kind=None if data.get("kind") is None else str(data.get("kind")),
            route=None if data.get("route") is None else str(data.get("route")),
            tool=None if data.get("tool") is None else str(data.get("tool")),
            tool_outcome=(
                None
                if data.get("tool_outcome") is None
                else str(data.get("tool_outcome"))
            ),
            task_outcome=(
                None
                if data.get("task_outcome") is None
                else str(data.get("task_outcome"))
            ),
            error_class=(
                None
                if data.get("error_class") is None
                else str(data.get("error_class"))
            ),
            redacted_summary=(
                None
                if data.get("redacted_summary") is None
                else str(data.get("redacted_summary"))
            ),
            recurrence_key=(
                None
                if data.get("recurrence_key") is None
                else str(data.get("recurrence_key"))
            ),
            approach=(
                None
                if data.get("approach") is None
                else str(data.get("approach"))
            ),
            decision_point=(
                None
                if data.get("decision_point") is None
                else str(data.get("decision_point"))
            ),
            raw_payload=(
                None
                if data.get("raw_payload") is None
                else str(data.get("raw_payload"))
            ),
            request_fingerprint=(
                None
                if data.get("request_fingerprint") is None
                else str(data.get("request_fingerprint"))
            ),
            is_counterevidence=is_counter,
        )


@dataclass(frozen=True)
class SnapshotPolicy:
    """Freeze policy bound into the snapshot identity."""

    cutoff_at: str
    harness_generation_id: str
    redaction_policy_version: str = REDACTION_POLICY_VERSION
    taxonomy_version: str = TAXONOMY_VERSION
    schema_version: str = MAINTENANCE_SCHEMA_VERSION
    base_commit: str | None = None
    graph_bookmark: str | None = None
    holdout_ratio: float = DEFAULT_HOLDOUT_RATIO
    holdout_ids: frozenset[str] | None = None
    # Material mixed into deterministic holdout assignment (not secret-sensitive).
    holdout_seed: str = "dream-holdout-v1"
    correlation_key: bytes | str | None = None

    def normalized(self) -> SnapshotPolicy:
        ratio = float(self.holdout_ratio)
        if ratio < 0.0 or ratio > 1.0:
            raise ValueError("holdout_ratio must be in [0, 1]")
        holdout_ids = (
            None
            if self.holdout_ids is None
            else frozenset(str(x) for x in self.holdout_ids)
        )
        return SnapshotPolicy(
            cutoff_at=normalize_ts(self.cutoff_at),
            harness_generation_id=str(self.harness_generation_id).strip(),
            redaction_policy_version=str(self.redaction_policy_version).strip(),
            taxonomy_version=str(self.taxonomy_version).strip(),
            schema_version=str(self.schema_version).strip(),
            base_commit=(
                None if self.base_commit is None else str(self.base_commit).strip()
            ),
            graph_bookmark=(
                None
                if self.graph_bookmark is None
                else str(self.graph_bookmark).strip()
            ),
            holdout_ratio=ratio,
            holdout_ids=holdout_ids,
            holdout_seed=str(self.holdout_seed),
            correlation_key=self.correlation_key,
        )


@dataclass(frozen=True)
class Membership:
    evidence_id: str
    evidence_label: str
    role: str
    evidence_hash: str

    def to_store_dict(self) -> dict[str, str]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_label": self.evidence_label,
            "role": self.role,
            "evidence_hash": self.evidence_hash,
        }


@dataclass
class FrozenSnapshot:
    """Result of freeze_snapshot — store payload + local projections."""

    snapshot_id: str
    dream_id: str
    cutoff_at: str
    source_ids_digest: str
    source_counts: dict[str, int]
    redaction_policy_version: str
    sensitivity_max: str
    harness_generation_id: str
    taxonomy_version: str
    schema_version: str
    graph_bookmark: str | None
    base_commit: str | None
    memberships: list[Membership]
    # Local partitions (holdout hidden from analyzer).
    generation_ids: list[str] = field(default_factory=list)
    counterevidence_ids: list[str] = field(default_factory=list)
    holdout_ids: list[str] = field(default_factory=list)
    eligible_exposure_ids: list[str] = field(default_factory=list)
    excluded_late_ids: list[str] = field(default_factory=list)
    excluded_revoked_ids: list[str] = field(default_factory=list)
    # Sanitized evidence maps by id (no raw intimate fields).
    redacted_items: dict[str, dict[str, Any]] = field(default_factory=dict)

    def source_counts_json(self) -> str:
        return _canonical_json(self.source_counts)

    def to_store_payload(
        self,
        *,
        run_id: str,
        epoch: int,
        lease_key: str = "maintenance",
    ) -> dict[str, Any]:
        return {
            "id": self.snapshot_id,
            "dream_id": self.dream_id,
            "run_id": run_id,
            "epoch": epoch,
            "lease_key": lease_key,
            "cutoff_at": self.cutoff_at,
            "source_ids_digest": self.source_ids_digest,
            "source_counts_json": self.source_counts_json(),
            "redaction_policy_version": self.redaction_policy_version,
            "sensitivity_max": self.sensitivity_max,
            "harness_generation_id": self.harness_generation_id,
            "taxonomy_version": self.taxonomy_version,
            "graph_bookmark": self.graph_bookmark,
            "base_commit": self.base_commit,
            "memberships": [m.to_store_dict() for m in self.memberships],
        }

    def analyzer_packet(self) -> dict[str, Any]:
        """Projection for analyzers: generation + counterevidence, no holdout."""
        visible_ids = set(self.generation_ids) | set(self.counterevidence_ids)
        items = [
            self.redacted_items[i]
            for i in sorted(visible_ids)
            if i in self.redacted_items
        ]
        packet = {
            "snapshot_id": self.snapshot_id,
            "dream_id": self.dream_id,
            "cutoff_at": self.cutoff_at,
            "source_ids_digest": self.source_ids_digest,
            "source_counts": {
                k: v
                for k, v in self.source_counts.items()
                if k not in {"holdout", "holdout_ids"}
            },
            "sensitivity_max": self.sensitivity_max,
            "harness_generation_id": self.harness_generation_id,
            "taxonomy_version": self.taxonomy_version,
            "redaction_policy_version": self.redaction_policy_version,
            "generation_ids": list(self.generation_ids),
            "counterevidence_ids": list(self.counterevidence_ids),
            # Holdout ids intentionally omitted from analyzer projection.
            "eligible_exposure_ids": list(self.eligible_exposure_ids),
            "eligible_exposure_count": len(self.eligible_exposure_ids),
            "items": items,
        }
        return redact_packet(packet)

    def membership_role_map(self) -> dict[str, str]:
        return {m.evidence_id: m.role for m in self.memberships}


def compute_source_ids_digest(
    memberships: Sequence[Membership | Mapping[str, str]],
) -> str:
    """Deterministic digest over sorted (id, role, hash) triples."""
    triples: list[dict[str, str]] = []
    for item in memberships:
        if isinstance(item, Membership):
            triples.append(
                {
                    "evidence_hash": item.evidence_hash,
                    "evidence_id": item.evidence_id,
                    "role": item.role,
                }
            )
        else:
            triples.append(
                {
                    "evidence_hash": str(
                        item.get("evidence_hash") or item.get("hash") or ""
                    ),
                    "evidence_id": str(
                        item.get("evidence_id") or item.get("id") or ""
                    ),
                    "role": str(item.get("role") or "generation"),
                }
            )
    triples.sort(key=lambda t: (t["evidence_id"], t["role"], t["evidence_hash"]))
    return digest_text(_canonical_json(triples))


def assign_holdout(
    candidate_ids: Sequence[str],
    *,
    policy: SnapshotPolicy,
) -> set[str]:
    """Deterministically choose holdout ids disjoint from generation set.

    Explicit ``policy.holdout_ids`` win when provided (intersected with
    candidates). Otherwise a stable fraction is taken via seeded digests.
    """
    candidates = sorted({str(x) for x in candidate_ids})
    if not candidates:
        return set()

    if policy.holdout_ids is not None:
        return {i for i in candidates if i in policy.holdout_ids}

    if policy.holdout_ratio <= 0.0:
        return set()
    if policy.holdout_ratio >= 1.0:
        return set(candidates)

    threshold = int(policy.holdout_ratio * _HOLDOUT_SCALE)
    selected: set[str] = set()
    for eid in candidates:
        material = f"{policy.holdout_seed}\0{eid}\0{policy.cutoff_at}"
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) % _HOLDOUT_SCALE
        if bucket < threshold:
            selected.add(eid)

    # Guarantee at least one holdout when ratio > 0 and pool is large enough,
    # and leave at least one generation id when pool has 2+ items.
    if not selected and candidates:
        selected.add(candidates[0])
    if len(selected) >= len(candidates) and len(candidates) > 1:
        # Keep the last id for generation so partitions stay disjoint & non-empty.
        keep = candidates[-1]
        selected.discard(keep)
    return selected


def _effective_hash(item: EvidenceItem, policy: SnapshotPolicy) -> str:
    """Prefer keyed correlation HMAC for retained hash when key is available."""
    if policy.correlation_key is not None:
        return correlation_hmac(item.id, key=policy.correlation_key)
    return item.evidence_hash


def freeze_snapshot(
    items: Iterable[Mapping[str, Any] | EvidenceItem],
    *,
    policy: SnapshotPolicy,
    dream_id: str,
    snapshot_id: str | None = None,
) -> FrozenSnapshot:
    """Select, partition, and digest evidence for a DreamRun snapshot."""
    pol = policy.normalized()
    cutoff = parse_iso_utc(pol.cutoff_at)

    parsed: list[EvidenceItem] = []
    for raw in items:
        if isinstance(raw, EvidenceItem):
            parsed.append(raw)
        else:
            parsed.append(EvidenceItem.from_mapping(raw))

    # Stable order for selection.
    parsed.sort(key=lambda e: (e.observed_at, e.id))

    excluded_late: list[str] = []
    excluded_revoked: list[str] = []
    eligible: list[EvidenceItem] = []
    for item in parsed:
        if item.revoked:
            excluded_revoked.append(item.id)
            continue
        if parse_iso_utc(item.observed_at) > cutoff:
            excluded_late.append(item.id)
            continue
        eligible.append(item)

    # Holdout is drawn only from non-counterevidence pool so counterevidence
    # remains available as opposing signal for generation-time analysis.
    non_counter_ids = [
        e.id for e in eligible if not (e.is_counterevidence or e.role_hint == "counterevidence")
    ]
    holdout_set = assign_holdout(non_counter_ids, policy=pol)

    memberships: list[Membership] = []
    generation_ids: list[str] = []
    counterevidence_ids: list[str] = []
    holdout_ids: list[str] = []
    eligible_exposure_ids: list[str] = []
    redacted_items: dict[str, dict[str, Any]] = {}
    sensitivities: list[str] = []

    for item in eligible:
        is_counter = item.is_counterevidence or item.role_hint == "counterevidence"
        if item.id in holdout_set and not is_counter:
            role = "holdout"
            holdout_ids.append(item.id)
        elif is_counter:
            role = "counterevidence"
            counterevidence_ids.append(item.id)
        else:
            role = "generation"
            generation_ids.append(item.id)

        if item.eligible_exposure and role != "holdout":
            eligible_exposure_ids.append(item.id)

        eff_hash = _effective_hash(item, pol)
        memberships.append(
            Membership(
                evidence_id=item.id,
                evidence_label=item.label,
                role=role,
                evidence_hash=eff_hash,
            )
        )
        sensitivities.append(item.sensitivity)

        public = item.to_public_mapping()
        public["role"] = role
        public["evidence_hash"] = eff_hash
        redacted_items[item.id] = redact_evidence_record(
            public,
            correlation_key=pol.correlation_key,
        )

    # Disjointness invariant.
    g_set, c_set, h_set = set(generation_ids), set(counterevidence_ids), set(holdout_ids)
    if g_set & h_set:
        raise RuntimeError("holdout/generation partition not disjoint")
    if c_set & h_set:
        raise RuntimeError("holdout/counterevidence partition not disjoint")

    memberships.sort(key=lambda m: (m.evidence_id, m.role))
    source_ids_digest = compute_source_ids_digest(memberships)

    by_label: dict[str, int] = {}
    by_role: dict[str, int] = {
        "generation": len(generation_ids),
        "counterevidence": len(counterevidence_ids),
        "holdout": len(holdout_ids),
    }
    for item in eligible:
        by_label[item.label] = by_label.get(item.label, 0) + 1

    source_counts = {
        "total": len(memberships),
        "generation": by_role["generation"],
        "counterevidence": by_role["counterevidence"],
        "holdout": by_role["holdout"],
        "eligible_exposure": len(eligible_exposure_ids),
        "excluded_late": len(excluded_late),
        "excluded_revoked": len(excluded_revoked),
        "by_label": by_label,
    }

    sid = snapshot_id or f"snap-{dream_id}"
    return FrozenSnapshot(
        snapshot_id=sid,
        dream_id=dream_id,
        cutoff_at=pol.cutoff_at,
        source_ids_digest=source_ids_digest,
        source_counts=source_counts,
        redaction_policy_version=pol.redaction_policy_version,
        sensitivity_max=max_sensitivity(sensitivities) if sensitivities else "public_ops",
        harness_generation_id=pol.harness_generation_id,
        taxonomy_version=pol.taxonomy_version,
        schema_version=pol.schema_version,
        graph_bookmark=pol.graph_bookmark,
        base_commit=pol.base_commit,
        memberships=memberships,
        generation_ids=sorted(generation_ids),
        counterevidence_ids=sorted(counterevidence_ids),
        holdout_ids=sorted(holdout_ids),
        eligible_exposure_ids=sorted(eligible_exposure_ids),
        excluded_late_ids=sorted(excluded_late),
        excluded_revoked_ids=sorted(excluded_revoked),
        redacted_items=redacted_items,
    )


def load_evidence_fixture(path: str | Any) -> list[dict[str, Any]]:
    """Load a JSON evidence list (or ``{"evidence": [...]}``) from disk."""
    from pathlib import Path

    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [dict(x) for x in data]
    if isinstance(data, Mapping) and isinstance(data.get("evidence"), list):
        return [dict(x) for x in data["evidence"]]
    raise ValueError(f"evidence fixture must be a list or {{evidence: [...]}}: {p}")
