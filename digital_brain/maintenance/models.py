"""Typed harness generation identity and maintenance workflow records.

``HarnessGeneration`` is the exact version attribution for a session. SOUL
content never appears here — only a local content digest (``soul_sha``).

Workflow records (DreamRun, leases, receipts, proposals, authorities) live on
the quality/control plane. Observation lifecycle, proposal lifecycle, decision,
application, and effectiveness are separate records — never collapsed into one
mutable row. Evidence provenance uses relationships, not a single
``absorbed_by_dream_id`` field.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

# Schema of the HarnessGeneration record itself (not the life-graph schema).
HARNESS_SCHEMA_VERSION = "1"

# Evidence/route taxonomy used by future RunEvent / Feedback sensors.
TAXONOMY_VERSION = "1"

# Maintenance / dream workflow schema (independent of harness schema).
MAINTENANCE_SCHEMA_VERSION = "1"

# Canonical empty digest (sha256 of zero bytes).
EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()

# Generation id prefix for stable, greppable foreign keys.
GENERATION_ID_PREFIX = "hg-"

# ---------------------------------------------------------------------------
# DreamRun stage machine
# ---------------------------------------------------------------------------

# Linear pipeline stages (non-terminal).
DREAM_PIPELINE_STAGES: tuple[str, ...] = (
    "queued",
    "leased",
    "snapshotting",
    "normalizing",
    "clustering",
    "planning",
    "compiling",
    "validating",
    "publishing",
    "completed",
)

DREAM_TERMINAL_STAGES: frozenset[str] = frozenset(
    {"completed", "failed", "aborted", "lease_lost"}
)

DREAM_STAGES: frozenset[str] = frozenset(DREAM_PIPELINE_STAGES) | DREAM_TERMINAL_STAGES

# Owner-facing projection on DreamRun (separate from stage).
DREAM_OWNER_STATUSES: frozenset[str] = frozenset(
    {
        "scheduled",
        "running",
        "needs_review",
        "completed_clean",
        "completed_partial",
        "failed",
        "cancelled",
        "lease_lost",
    }
)

DREAM_OWNER_TERMINAL: frozenset[str] = frozenset(
    {
        "completed_clean",
        "completed_partial",
        "failed",
        "cancelled",
        "lease_lost",
    }
)

# Proposal status is a cached projection; Decision / EvaluationReceipt /
# EffectReceipt / Deployment are authoritative for each concern.
PROPOSAL_STATUS_PROJECTIONS: frozenset[str] = frozenset(
    {
        "draft",
        "validated",
        "review_pending",
        "approved",
        "rejected",
        "stale",
        "invalid",
        "superseded",
        "withdrawn",
    }
)

PROPOSAL_KINDS: frozenset[str] = frozenset(
    {
        "alias",
        "revoke_alias",
        "overlay",
        "policy",
        "engineering",
        "retention",
        "housekeeping_report",
        "memory_suggestion",
    }
)

EVIDENCE_STRENGTHS: frozenset[str] = frozenset({"tentative", "moderate", "strong"})
FINDING_LANES: frozenset[str] = frozenset(
    {"housekeeping", "memory", "behaviour", "engineering"}
)
DECISION_VALUES: frozenset[str] = frozenset(
    {"approved", "rejected", "deferred", "withdrawn"}
)
EVALUATION_OUTCOMES: frozenset[str] = frozenset({"passed", "failed", "inconclusive"})
EFFECT_OUTCOMES: frozenset[str] = frozenset(
    {"applied", "replayed", "conflict", "stale", "failed", "reverted"}
)
AUTHORITY_STATUSES: frozenset[str] = frozenset(
    {"minted", "consumed", "expired", "revoked"}
)
DEPLOYMENT_STATUSES: frozenset[str] = frozenset(
    {"drafted", "trial_active", "deployed", "expired", "rolled_back"}
)
EVIDENCE_ROLES: frozenset[str] = frozenset(
    {"generation", "counterevidence", "holdout"}
)

# Forbidden observation→dream absorption field (must use relationships).
FORBIDDEN_ABSORPTION_FIELDS: frozenset[str] = frozenset(
    {"absorbed_by_dream_id", "absorbed_by_dream", "dream_absorption_id"}
)


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_text(text: str) -> str:
    return sha256_hex(text.encode("utf-8"))


def digest_bytes(data: bytes) -> str:
    return sha256_hex(data)


@dataclass(frozen=True)
class HarnessGeneration:
    """Exact harness generation pin for a session.

    Identity fields contribute to ``id`` / ``request_fingerprint``.
    ``created_at`` is bookkeeping only and must not change the generation id.
    """

    id: str
    core_commit: str
    core_tree_digest: str
    dirty_state_digest: str
    plugin_version: str
    soul_sha: str
    overlay_manifest_digest: str
    policy_digest: str
    mcp_version: str
    model_id: str | None
    schema_version: str
    taxonomy_version: str
    created_at: str | None = None

    def identity_payload(self) -> dict[str, Any]:
        """Canonical fields that define the generation id (no timestamps)."""
        return {
            "core_commit": self.core_commit,
            "core_tree_digest": self.core_tree_digest,
            "dirty_state_digest": self.dirty_state_digest,
            "mcp_version": self.mcp_version,
            "model_id": self.model_id,
            "overlay_manifest_digest": self.overlay_manifest_digest,
            "plugin_version": self.plugin_version,
            "policy_digest": self.policy_digest,
            "schema_version": self.schema_version,
            "soul_sha": self.soul_sha,
            "taxonomy_version": self.taxonomy_version,
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize for pin files / MCP — never includes SOUL content."""
        payload = asdict(self)
        # Defensive: strip any accidental soul-content keys if callers mutate.
        for forbidden in ("soul_content", "soul_text", "soul", "SOUL"):
            payload.pop(forbidden, None)
        return payload

    def to_record_params(self) -> dict[str, Any]:
        """Flat Neo4j property map for the Operational:HarnessGeneration node."""
        return {
            "id": self.id,
            "core_commit": self.core_commit,
            "core_tree_digest": self.core_tree_digest,
            "dirty_state_digest": self.dirty_state_digest,
            "plugin_version": self.plugin_version,
            "soul_sha": self.soul_sha,
            "overlay_manifest_digest": self.overlay_manifest_digest,
            "policy_digest": self.policy_digest,
            "mcp_version": self.mcp_version,
            "model_id": self.model_id,
            "schema_version": self.schema_version,
            "taxonomy_version": self.taxonomy_version,
            "request_fingerprint": generation_request_fingerprint(self),
            "created_at": self.created_at,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> HarnessGeneration:
        required = (
            "id",
            "core_commit",
            "core_tree_digest",
            "dirty_state_digest",
            "plugin_version",
            "soul_sha",
            "overlay_manifest_digest",
            "policy_digest",
            "mcp_version",
            "schema_version",
            "taxonomy_version",
        )
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(f"HarnessGeneration missing fields: {missing}")
        model_id = data.get("model_id")
        if model_id is not None and not isinstance(model_id, str):
            raise ValueError("model_id must be a string or null")
        return cls(
            id=str(data["id"]),
            core_commit=str(data["core_commit"]),
            core_tree_digest=str(data["core_tree_digest"]),
            dirty_state_digest=str(data["dirty_state_digest"]),
            plugin_version=str(data["plugin_version"]),
            soul_sha=str(data["soul_sha"]),
            overlay_manifest_digest=str(data["overlay_manifest_digest"]),
            policy_digest=str(data["policy_digest"]),
            mcp_version=str(data["mcp_version"]),
            model_id=model_id if model_id is None else str(model_id),
            schema_version=str(data["schema_version"]),
            taxonomy_version=str(data["taxonomy_version"]),
            created_at=(
                None
                if data.get("created_at") is None
                else str(data.get("created_at"))
            ),
        )


def generation_request_fingerprint(
    generation: HarnessGeneration | Mapping[str, Any],
) -> str:
    """Stable request fingerprint for replay-vs-conflict receipts."""
    if isinstance(generation, HarnessGeneration):
        payload = generation.identity_payload()
    else:
        # Allow dicts that may include id/created_at; only identity fields matter.
        tmp = HarnessGeneration.from_mapping(
            {
                **dict(generation),
                "id": generation.get("id") or "pending",
            }
        )
        payload = tmp.identity_payload()
    return digest_text(_canonical_json(payload))


def compute_generation_id(
    generation: HarnessGeneration | Mapping[str, Any],
) -> str:
    """Derive the stable generation id from identity fields only."""
    fingerprint = generation_request_fingerprint(generation)
    return f"{GENERATION_ID_PREFIX}{fingerprint}"


def build_harness_generation(
    *,
    core_commit: str,
    core_tree_digest: str,
    dirty_state_digest: str,
    plugin_version: str,
    soul_sha: str,
    overlay_manifest_digest: str,
    policy_digest: str,
    mcp_version: str,
    model_id: str | None = None,
    schema_version: str = HARNESS_SCHEMA_VERSION,
    taxonomy_version: str = TAXONOMY_VERSION,
    created_at: str | None = None,
    generation_id: str | None = None,
) -> HarnessGeneration:
    """Construct a HarnessGeneration with a deterministic id."""
    provisional = HarnessGeneration(
        id=generation_id or "pending",
        core_commit=core_commit,
        core_tree_digest=core_tree_digest,
        dirty_state_digest=dirty_state_digest,
        plugin_version=plugin_version,
        soul_sha=soul_sha,
        overlay_manifest_digest=overlay_manifest_digest,
        policy_digest=policy_digest,
        mcp_version=mcp_version,
        model_id=model_id,
        schema_version=schema_version,
        taxonomy_version=taxonomy_version,
        created_at=created_at,
    )
    gid = generation_id or compute_generation_id(provisional)
    return HarnessGeneration(
        id=gid,
        core_commit=provisional.core_commit,
        core_tree_digest=provisional.core_tree_digest,
        dirty_state_digest=provisional.dirty_state_digest,
        plugin_version=provisional.plugin_version,
        soul_sha=provisional.soul_sha,
        overlay_manifest_digest=provisional.overlay_manifest_digest,
        policy_digest=provisional.policy_digest,
        mcp_version=provisional.mcp_version,
        model_id=provisional.model_id,
        schema_version=provisional.schema_version,
        taxonomy_version=provisional.taxonomy_version,
        created_at=provisional.created_at,
    )


# ---------------------------------------------------------------------------
# Workflow transition validators (pure — no I/O)
# ---------------------------------------------------------------------------


class IllegalTransitionError(ValueError):
    """Raised when a dream stage / owner-status / authority transition is illegal."""

    def __init__(self, reason: str, *, from_state: str | None = None, to_state: str | None = None):
        self.reason = reason
        self.from_state = from_state
        self.to_state = to_state
        detail = reason
        if from_state is not None or to_state is not None:
            detail = f"{reason} ({from_state!r} → {to_state!r})"
        super().__init__(detail)


def _pipeline_index(stage: str) -> int:
    try:
        return DREAM_PIPELINE_STAGES.index(stage)
    except ValueError as exc:
        raise IllegalTransitionError(
            "unknown_pipeline_stage", from_state=stage
        ) from exc


def is_legal_dream_stage_transition(
    from_stage: str | None,
    to_stage: str,
    *,
    allow_replay: bool = True,
) -> bool:
    """Return True when ``from_stage → to_stage`` is a legal DreamRun transition.

    Rules:
    - First stage must be ``queued`` (from None / empty).
    - Pipeline may advance only one step forward.
    - Same stage may be re-recorded when ``allow_replay`` (idempotent stage key).
    - Any non-terminal stage may enter ``failed`` / ``aborted`` / ``lease_lost``.
    - Terminal stages accept no further transitions (except replay of self).
    """
    to_stage = str(to_stage or "").strip()
    if to_stage not in DREAM_STAGES:
        return False

    if from_stage is None or str(from_stage).strip() == "":
        return to_stage == "queued"

    from_stage = str(from_stage).strip()
    if from_stage not in DREAM_STAGES:
        return False

    if from_stage == to_stage:
        return allow_replay

    if from_stage in DREAM_TERMINAL_STAGES:
        return False

    if to_stage in {"failed", "aborted", "lease_lost"}:
        return True

    if from_stage not in DREAM_PIPELINE_STAGES or to_stage not in DREAM_PIPELINE_STAGES:
        return False

    return _pipeline_index(to_stage) == _pipeline_index(from_stage) + 1


def assert_legal_dream_stage_transition(
    from_stage: str | None,
    to_stage: str,
    *,
    allow_replay: bool = True,
) -> None:
    if not is_legal_dream_stage_transition(
        from_stage, to_stage, allow_replay=allow_replay
    ):
        raise IllegalTransitionError(
            "illegal_dream_stage_transition",
            from_state=from_stage,
            to_state=to_stage,
        )


def is_legal_owner_status_transition(
    from_status: str | None,
    to_status: str,
    *,
    allow_replay: bool = True,
) -> bool:
    """Owner-status projection transitions (orthogonal to pipeline stage)."""
    to_status = str(to_status or "").strip()
    if to_status not in DREAM_OWNER_STATUSES:
        return False

    if from_status is None or str(from_status).strip() == "":
        return to_status == "scheduled"

    from_status = str(from_status).strip()
    if from_status not in DREAM_OWNER_STATUSES:
        return False

    if from_status == to_status:
        return allow_replay

    if from_status in DREAM_OWNER_TERMINAL:
        return False

    allowed: dict[str, frozenset[str]] = {
        "scheduled": frozenset({"running", "cancelled", "failed", "lease_lost"}),
        "running": frozenset(
            {
                "needs_review",
                "completed_clean",
                "completed_partial",
                "failed",
                "cancelled",
                "lease_lost",
            }
        ),
        "needs_review": frozenset(
            {
                "completed_clean",
                "completed_partial",
                "failed",
                "cancelled",
                "lease_lost",
            }
        ),
    }
    return to_status in allowed.get(from_status, frozenset())


def assert_legal_owner_status_transition(
    from_status: str | None,
    to_status: str,
    *,
    allow_replay: bool = True,
) -> None:
    if not is_legal_owner_status_transition(
        from_status, to_status, allow_replay=allow_replay
    ):
        raise IllegalTransitionError(
            "illegal_owner_status_transition",
            from_state=from_status,
            to_state=to_status,
        )


def is_legal_authority_transition(
    from_status: str | None,
    to_status: str,
    *,
    allow_replay: bool = True,
) -> bool:
    """ActivationAuthority lifecycle: minted → consumed | expired | revoked."""
    to_status = str(to_status or "").strip()
    if to_status not in AUTHORITY_STATUSES:
        return False

    if from_status is None or str(from_status).strip() == "":
        return to_status == "minted"

    from_status = str(from_status).strip()
    if from_status not in AUTHORITY_STATUSES:
        return False

    if from_status == to_status:
        return allow_replay

    if from_status == "minted":
        return to_status in {"consumed", "expired", "revoked"}
    # consumed / expired / revoked are terminal
    return False


def assert_legal_authority_transition(
    from_status: str | None,
    to_status: str,
    *,
    allow_replay: bool = True,
) -> None:
    if not is_legal_authority_transition(
        from_status, to_status, allow_replay=allow_replay
    ):
        raise IllegalTransitionError(
            "illegal_authority_transition",
            from_state=from_status,
            to_state=to_status,
        )


def stage_idempotency_key(*, run_id: str, stage: str, attempt: int = 0) -> str:
    """Stable stage receipt key for crash/replay (not a content fingerprint)."""
    return f"{run_id}:{stage}:{int(attempt)}"


def assert_no_absorption_field(payload: Mapping[str, Any]) -> None:
    """Reject single-field absorption — evidence supports many findings/proposals."""
    for key in payload:
        if str(key) in FORBIDDEN_ABSORPTION_FIELDS:
            raise ValueError(
                f"forbidden field {key!r}: use relationships "
                "(USES_EVIDENCE / SUPPORTED_BY / INCLUDES_EVIDENCE), "
                "not absorbed_by_dream_id"
            )


# ---------------------------------------------------------------------------
# Typed workflow records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MaintenanceLease:
    """Fenced exclusive lease. Epoch increments after expiry/takeover."""

    key: str
    holder_id: str
    run_id: str
    epoch: int
    lease_until: str
    heartbeat_at: str


@dataclass(frozen=True)
class DreamRun:
    """Bounded maintenance run. Stage receipts are separate nodes."""

    id: str
    owner_status: str
    stage: str
    attempt: int
    harness_generation_id: str
    processing_mode: str = "report_only"
    holder_id: str | None = None
    lease_epoch: int | None = None
    lease_key: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    input_digest: str | None = None
    output_digest: str | None = None
    base_commit: str | None = None
    reviewed_count: int = 0
    auto_applied_count: int = 0
    suppressed_candidate_count: int = 0
    metrics_json: str | None = None
    error_class: str | None = None
    schema_version: str = MAINTENANCE_SCHEMA_VERSION


@dataclass(frozen=True)
class DreamStageReceipt:
    """Idempotent per-stage checkpoint for crash resume."""

    id: str
    run_id: str
    stage: str
    stage_key: str
    attempt: int
    lease_epoch: int
    input_digest: str | None = None
    output_digest: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    outcome: str = "recorded"
    error_class: str | None = None
    request_fingerprint: str | None = None


@dataclass(frozen=True)
class EvidenceSnapshot:
    """Frozen evidence set for a dream. Membership is relational, not a digest alone."""

    id: str
    dream_id: str
    cutoff_at: str
    source_ids_digest: str
    source_counts_json: str
    redaction_policy_version: str
    sensitivity_max: str
    harness_generation_id: str
    taxonomy_version: str = TAXONOMY_VERSION
    created_at: str | None = None
    graph_bookmark: str | None = None
    base_commit: str | None = None
    lease_epoch: int | None = None


@dataclass(frozen=True)
class EvidenceMembership:
    """One edge of the frozen snapshot membership set."""

    snapshot_id: str
    evidence_id: str
    evidence_label: str  # Feedback | RunEvent
    role: str
    evidence_hash: str


@dataclass(frozen=True)
class Finding:
    id: str
    dream_id: str
    snapshot_id: str
    class_key: str
    lane: str
    summary: str
    evidence_strength: str
    support_counts_json: str = "{}"
    counterevidence_json: str = "[]"
    created_at: str | None = None
    # evidence_ids linked via USES_EVIDENCE — never absorbed_by_dream_id
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Proposal:
    """Proposal card. status_projection is a cache; Decision/Effect are authoritative."""

    id: str
    kind: str
    title: str
    status_projection: str
    target_ref: str
    scope: str
    risk_tier: str
    reversibility: str
    evidence_snapshot_id: str
    evidence_strength: str
    dream_id: str | None = None
    evidence_summary_json: str = "{}"
    counterevidence_json: str = "[]"
    sensitivity_max: str = "public_ops"
    expected_outcome: str | None = None
    before_fingerprint: str | None = None
    proposed_effect_hash: str | None = None
    artifact_ref: str | None = None
    trial_json: str | None = None
    created_at: str | None = None
    expires_at: str | None = None
    finding_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PatchArtifactMetadata:
    """Immutable patch/artifact metadata (quarantine only — never auto-deploy)."""

    id: str
    proposal_id: str
    evidence_snapshot_id: str
    base_commit: str
    before_hashes_json: str
    compiler_version: str
    schema_version: str
    target_path_allowlist_json: str
    patch_sha256: str
    artifact_path: str
    expected_plugin_generation: str | None = None
    rollback_ref: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class EvaluationReceipt:
    id: str
    proposal_id: str
    evaluator_version: str
    baseline_ref: str
    candidate_ref: str
    fixture_snapshot: str
    target_results: str
    guardrail_results: str
    privacy_result: str
    invariant_result: str
    outcome: str
    created_at: str | None = None
    request_fingerprint: str | None = None


@dataclass(frozen=True)
class Decision:
    """Approval/rejection record — separate from application and effectiveness."""

    id: str
    proposal_id: str
    decision: str
    proposal_hash: str
    target_ref: str
    before_fingerprint: str
    artifact_or_effect_hash: str
    decided_by: str
    decided_at: str | None = None
    reason_code: str | None = None
    expires_at: str | None = None
    request_fingerprint: str | None = None


@dataclass(frozen=True)
class EffectReceipt:
    """Application outcome — separate from Decision and ExposureWindow."""

    id: str
    effect_key: str
    request_hash: str
    proposal_id: str
    effect_type: str
    actor: str
    before_ref: str
    outcome: str
    verification_status: str
    dream_id: str | None = None
    authority_digest: str | None = None
    fence_epoch: int | None = None
    after_ref: str | None = None
    applied_at: str | None = None
    undo_ref: str | None = None
    reverted_at: str | None = None


@dataclass(frozen=True)
class ActivationAuthority:
    """Single-use expiring authority. Mint/consume stay off model-facing MCP."""

    id: str
    decision_id: str
    proposal_id: str
    proposal_hash: str
    target_ref: str
    before_fingerprint: str
    artifact_or_effect_hash: str
    approver: str
    status: str
    minted_at: str
    expires_at: str
    nonce_digest: str
    request_fingerprint: str
    consumed_at: str | None = None
    consumption_receipt_id: str | None = None
    reconciliation_receipt_id: str | None = None
    revoked_at: str | None = None


@dataclass(frozen=True)
class Deployment:
    """Deployment state — separate from Decision and effectiveness window."""

    id: str
    proposal_id: str
    generation_id: str
    status: str
    activated_at: str | None = None
    retired_at: str | None = None
    rollback_ref: str | None = None


@dataclass(frozen=True)
class ExposureWindow:
    """Trial/effectiveness observation — not approval and not application."""

    id: str
    deployment_id: str
    decision_point: str
    eligible_target: int
    eligible_seen: int
    started_at: str
    ends_at: str
    recurrence_count: int = 0
    counterevidence_count: int = 0
    guardrail_json: str = "{}"
    effectiveness_status: str = "observing"


def activation_authority_identity_payload(
    authority: ActivationAuthority | Mapping[str, Any],
) -> dict[str, Any]:
    """Fields that bind an authority (no timestamps that are bookkeeping-only)."""
    if isinstance(authority, ActivationAuthority):
        return {
            "artifact_or_effect_hash": authority.artifact_or_effect_hash,
            "before_fingerprint": authority.before_fingerprint,
            "decision_id": authority.decision_id,
            "nonce_digest": authority.nonce_digest,
            "proposal_hash": authority.proposal_hash,
            "proposal_id": authority.proposal_id,
            "target_ref": authority.target_ref,
        }
    return {
        "artifact_or_effect_hash": str(authority["artifact_or_effect_hash"]),
        "before_fingerprint": str(authority["before_fingerprint"]),
        "decision_id": str(authority["decision_id"]),
        "nonce_digest": str(authority["nonce_digest"]),
        "proposal_hash": str(authority["proposal_hash"]),
        "proposal_id": str(authority["proposal_id"]),
        "target_ref": str(authority["target_ref"]),
    }


def compute_authority_request_fingerprint(
    authority: ActivationAuthority | Mapping[str, Any],
) -> str:
    return digest_text(_canonical_json(activation_authority_identity_payload(authority)))


def dream_stage_request_fingerprint(
    *,
    run_id: str,
    stage: str,
    stage_key: str,
    lease_epoch: int,
    input_digest: str | None,
    output_digest: str | None,
    attempt: int,
) -> str:
    return digest_text(
        _canonical_json(
            {
                "attempt": int(attempt),
                "input_digest": input_digest,
                "lease_epoch": int(lease_epoch),
                "output_digest": output_digest,
                "run_id": run_id,
                "stage": stage,
                "stage_key": stage_key,
            }
        )
    )
