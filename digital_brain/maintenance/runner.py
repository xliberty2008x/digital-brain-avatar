"""Report-only DreamRun coordinator.

Manual, local-only, fenced through Task 5 MaintenanceStore. Freezes a
deterministic evidence snapshot, walks the stage machine with crash-resume
receipts, and emits a counts/ids report with three buckets:

- applied_housekeeping (always empty in report-only phase)
- waiting_for_owner
- deliberately_left_alone

No retention, alias apply, overlay activation, patch compile, or authority mint.
Even an "all tools" maintainer profile cannot activate effects.
"""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from digital_brain.maintenance.models import (
    DREAM_PIPELINE_STAGES,
    MAINTENANCE_SCHEMA_VERSION,
    TAXONOMY_VERSION,
    digest_text,
    stage_idempotency_key,
)
from digital_brain.maintenance.privacy import (
    REDACTION_POLICY_VERSION,
    assert_no_intimate_fields,
    redact_packet,
)
from digital_brain.maintenance.snapshot import (
    EvidenceItem,
    FrozenSnapshot,
    SnapshotPolicy,
    freeze_snapshot,
)

DEFAULT_LEASE_KEY = "maintenance"
DEFAULT_LEASE_TTL_SECONDS = 300
PROCESSING_MODE_REPORT_ONLY = "report_only"


class DreamRunCheckpoint(Exception):
    """Raised by ``stop_after_stage`` to simulate a crash after a receipt."""

    def __init__(
        self,
        *,
        run_id: str,
        epoch: int,
        stage: str,
        stage_outcomes: dict[str, str],
    ):
        self.run_id = run_id
        self.epoch = epoch
        self.stage = stage
        self.stage_outcomes = stage_outcomes
        super().__init__(f"dream_checkpoint:{run_id}:{stage}")

# Operations a report-only maintainer may invoke on the control plane.
MAINTAINER_ALLOWED_OPERATIONS: frozenset[str] = frozenset(
    {
        "acquire_maintenance_lease",
        "renew_maintenance_lease",
        "release_maintenance_lease",
        "create_dream_run",
        "record_dream_stage",
        "create_evidence_snapshot",
        "create_finding",
        "create_proposal",
        # Evaluation/decision are recording only — not activation.
        "record_evaluation",
        "record_decision",
    }
)

# Explicit denylist of activation / effect surfaces. Maintainer profiles must
# never gain these even when "all tools" are enabled.
MAINTAINER_FORBIDDEN_OPERATIONS: frozenset[str] = frozenset(
    {
        "activate_alias",
        "apply_alias",
        "revoke_alias",
        "mint_activation_authority",
        "consume_activation_authority",
        "activate_policy",
        "activate_overlay",
        "publish_deployment",
        "record_effect",
        "apply_effect",
        "operator_activate",
        "record_retention_effect",  # retention is Task 8; blocked in report-only
        "compile_patch",
        "write_quarantine_artifact",
        "load_overlay_manifest",
    }
)

# Report-only pipeline still walks every stage for resume/checkpoint fidelity.
REPORT_ONLY_STAGES: tuple[str, ...] = tuple(
    s for s in DREAM_PIPELINE_STAGES if s != "queued"
)


class MaintenanceStoreProtocol(Protocol):
    def acquire_maintenance_lease(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def renew_maintenance_lease(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def release_maintenance_lease(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def create_dream_run(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def record_dream_stage(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def create_evidence_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def create_finding(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def create_proposal(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def dispatch(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]: ...


def _canonical_json(payload: Mapping[str, Any] | Sequence[Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _digest_mapping(payload: Mapping[str, Any]) -> str:
    return digest_text(_canonical_json(payload))


def new_run_id() -> str:
    return f"dream-{uuid.uuid4().hex}"


def maintainer_tool_profile(*, all_tools: bool = True) -> frozenset[str]:
    """Return the operation set available to a maintainer agent.

    ``all_tools=True`` still excludes every activation surface — proving that
    capability ceiling is structural, not a prompt filter alone.
    """
    if not all_tools:
        return frozenset(
            {
                "create_evidence_snapshot",
                "create_finding",
                "create_proposal",
                "record_dream_stage",
            }
        )
    # Union of allowed ops; forbidden never included.
    return frozenset(MAINTAINER_ALLOWED_OPERATIONS)


def assert_no_activation_capability(tools: Iterable[str]) -> None:
    tools_set = {str(t) for t in tools}
    leaked = tools_set & MAINTAINER_FORBIDDEN_OPERATIONS
    if leaked:
        raise AssertionError(f"activation_capability_present:{sorted(leaked)}")
    # Structural check: allowed and forbidden must stay disjoint.
    overlap = MAINTAINER_ALLOWED_OPERATIONS & MAINTAINER_FORBIDDEN_OPERATIONS
    if overlap:
        raise AssertionError(f"profile_definition_overlap:{sorted(overlap)}")


@dataclass
class ReportBuckets:
    applied_housekeeping: list[str] = field(default_factory=list)
    waiting_for_owner: list[str] = field(default_factory=list)
    deliberately_left_alone: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied_housekeeping": {
                "count": len(self.applied_housekeeping),
                "ids": list(self.applied_housekeeping),
            },
            "waiting_for_owner": {
                "count": len(self.waiting_for_owner),
                "ids": list(self.waiting_for_owner),
            },
            "deliberately_left_alone": {
                "count": len(self.deliberately_left_alone),
                "ids": list(self.deliberately_left_alone),
            },
        }


@dataclass
class DreamRunResult:
    run_id: str
    epoch: int
    stage: str
    owner_status: str
    processing_mode: str
    snapshot_id: str | None
    source_ids_digest: str | None
    report: dict[str, Any]
    stage_outcomes: dict[str, str] = field(default_factory=dict)
    finding_ids: list[str] = field(default_factory=list)
    proposal_ids: list[str] = field(default_factory=list)
    resumed: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        packet = {
            "run_id": self.run_id,
            "epoch": self.epoch,
            "stage": self.stage,
            "owner_status": self.owner_status,
            "processing_mode": self.processing_mode,
            "snapshot_id": self.snapshot_id,
            "source_ids_digest": self.source_ids_digest,
            "report": self.report,
            "finding_ids": list(self.finding_ids),
            "proposal_ids": list(self.proposal_ids),
            "resumed": self.resumed,
            "stage_outcomes": dict(self.stage_outcomes),
        }
        return redact_packet(packet)


@dataclass
class DreamRunner:
    """Fenced report-only coordinator over a MaintenanceStore."""

    store: MaintenanceStoreProtocol
    holder_id: str
    harness_generation_id: str
    lease_key: str = DEFAULT_LEASE_KEY
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS
    processing_mode: str = PROCESSING_MODE_REPORT_ONLY
    correlation_key: bytes | str | None = None
    base_commit: str | None = None
    # Local stage checkpoint cache for crash-resume diagnostics (not authority).
    _local_checkpoints: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    def tool_profile(self, *, all_tools: bool = True) -> frozenset[str]:
        tools = maintainer_tool_profile(all_tools=all_tools)
        assert_no_activation_capability(tools)
        return tools

    def dispatch(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatch only maintainer-allowed operations (no activation)."""
        op = str(operation).strip()
        if op in MAINTAINER_FORBIDDEN_OPERATIONS:
            return {
                "outcome": "forbidden",
                "operation": op,
                "reason": "activation_or_effect_not_available_in_report_only",
            }
        if op not in MAINTAINER_ALLOWED_OPERATIONS:
            return {
                "outcome": "forbidden",
                "operation": op,
                "reason": "operation_not_in_maintainer_profile",
            }
        return self.store.dispatch(op, payload)

    def run(
        self,
        evidence: Iterable[Mapping[str, Any] | EvidenceItem],
        *,
        cutoff_at: str,
        run_id: str | None = None,
        snapshot_id: str | None = None,
        holdout_ratio: float = 0.2,
        holdout_ids: Iterable[str] | None = None,
        graph_bookmark: str | None = None,
        release_lease: bool = True,
        stop_after_stage: str | None = None,
    ) -> DreamRunResult:
        """Execute (or resume) a full report-only dream pipeline.

        ``stop_after_stage`` is a test/debug hook that raises
        :class:`DreamRunCheckpoint` after recording that stage so crash resume
        can be exercised without killing the process.
        """
        if self.processing_mode != PROCESSING_MODE_REPORT_ONLY:
            raise ValueError(
                f"unsupported processing_mode {self.processing_mode!r}; "
                f"only {PROCESSING_MODE_REPORT_ONLY!r} is enabled in v0.5"
            )

        rid = (run_id or new_run_id()).strip()
        evidence_list = list(evidence)

        lease = self.store.acquire_maintenance_lease(
            {
                "key": self.lease_key,
                "holder_id": self.holder_id,
                "run_id": rid,
                "ttl_seconds": self.ttl_seconds,
            }
        )
        # First acquire → acquired; same holder+run while active → renewed.
        if lease.get("outcome") not in {"acquired", "renewed"}:
            if lease.get("outcome") == "held":
                raise RuntimeError(
                    f"maintenance lease held by {lease.get('holder_id')!r} "
                    f"run_id={lease.get('run_id')!r}"
                )
            raise RuntimeError(f"lease_acquire_failed:{lease!r}")

        epoch = int(lease["epoch"])
        stage_outcomes: dict[str, str] = {}
        resumed = lease.get("outcome") == "renewed"

        dream = self.store.create_dream_run(
            {
                "id": rid,
                "run_id": rid,
                "epoch": epoch,
                "holder_id": self.holder_id,
                "lease_key": self.lease_key,
                "harness_generation_id": self.harness_generation_id,
                "processing_mode": self.processing_mode,
                "base_commit": self.base_commit,
            }
        )
        if dream.get("outcome") not in {"created", "replayed"}:
            raise RuntimeError(f"create_dream_run_failed:{dream!r}")
        if dream.get("outcome") == "replayed":
            resumed = True

        policy = SnapshotPolicy(
            cutoff_at=cutoff_at,
            harness_generation_id=self.harness_generation_id,
            redaction_policy_version=REDACTION_POLICY_VERSION,
            taxonomy_version=TAXONOMY_VERSION,
            schema_version=MAINTENANCE_SCHEMA_VERSION,
            base_commit=self.base_commit,
            graph_bookmark=graph_bookmark,
            holdout_ratio=holdout_ratio,
            holdout_ids=(
                None if holdout_ids is None else frozenset(str(x) for x in holdout_ids)
            ),
            correlation_key=self.correlation_key,
        )

        frozen: FrozenSnapshot | None = None
        finding_ids: list[str] = []
        proposal_ids: list[str] = []
        buckets = ReportBuckets()
        report: dict[str, Any] = {}

        # Stage handlers — pure-ish work + store writes; re-runnable.
        def stage_leased() -> dict[str, Any]:
            return {"owner_status": "running"}

        def stage_snapshotting() -> dict[str, Any]:
            nonlocal frozen
            frozen = freeze_snapshot(
                evidence_list,
                policy=policy,
                dream_id=rid,
                snapshot_id=snapshot_id or f"snap-{rid}",
            )
            payload = frozen.to_store_payload(
                run_id=rid, epoch=epoch, lease_key=self.lease_key
            )
            snap_result = self.store.create_evidence_snapshot(payload)
            if snap_result.get("outcome") not in {"created", "replayed"}:
                raise RuntimeError(f"create_evidence_snapshot_failed:{snap_result!r}")
            return {
                "output_digest": frozen.source_ids_digest,
                "snapshot_id": frozen.snapshot_id,
                "source_ids_digest": frozen.source_ids_digest,
            }

        def stage_normalizing() -> dict[str, Any]:
            assert frozen is not None
            packet = frozen.analyzer_packet()
            assert_no_intimate_fields(packet)
            return {"output_digest": _digest_mapping(packet)}

        def stage_clustering() -> dict[str, Any]:
            assert frozen is not None
            clusters: dict[str, list[str]] = {}
            for eid in frozen.generation_ids:
                item = frozen.redacted_items.get(eid, {})
                key = str(item.get("kind") or item.get("error_class") or "unclassified")
                clusters.setdefault(key, []).append(eid)
            digest = _digest_mapping({k: sorted(v) for k, v in sorted(clusters.items())})
            return {"output_digest": digest, "cluster_count": len(clusters)}

        def stage_planning() -> dict[str, Any]:
            nonlocal finding_ids, proposal_ids, buckets
            assert frozen is not None
            finding_ids = []
            proposal_ids = []
            buckets = ReportBuckets()

            # Report-only planning: deterministic typed records, no activation.
            waiting: list[str] = []
            left_alone: list[str] = []

            for eid in frozen.generation_ids:
                item = frozen.redacted_items.get(eid, {})
                kind = item.get("kind")
                sensitivity = item.get("sensitivity") or "public_ops"
                if kind in {"entity_wrong", "miss", "invent"} and sensitivity != "intimate":
                    waiting.append(eid)
                else:
                    left_alone.append(eid)

            # Counterevidence does not generate proposals; supports later eval.
            for eid in frozen.counterevidence_ids:
                if eid not in left_alone and eid not in waiting:
                    left_alone.append(eid)

            # Holdout never appears in proposal generation.
            for eid in frozen.holdout_ids:
                assert eid not in waiting

            buckets.waiting_for_owner = sorted(waiting)
            buckets.deliberately_left_alone = sorted(left_alone)
            buckets.applied_housekeeping = []  # zero in report-only phase

            if waiting:
                fid = f"find-{rid}-memory"
                finding = self.store.create_finding(
                    {
                        "id": fid,
                        "dream_id": rid,
                        "snapshot_id": frozen.snapshot_id,
                        "class_key": "memory_signal",
                        "lane": "memory",
                        "summary": f"{len(waiting)} memory signal(s) need owner review",
                        "evidence_strength": (
                            "moderate" if len(waiting) >= 2 else "tentative"
                        ),
                        "support_counts_json": _canonical_json(
                            {"generation": len(waiting)}
                        ),
                        "counterevidence_json": _canonical_json(
                            frozen.counterevidence_ids
                        ),
                        "evidence_ids": waiting,
                        "run_id": rid,
                        "epoch": epoch,
                        "lease_key": self.lease_key,
                    }
                )
                if finding.get("outcome") not in {"created", "replayed"}:
                    raise RuntimeError(f"create_finding_failed:{finding!r}")
                finding_ids.append(fid)

                pid = f"prop-{rid}-memory"
                proposal = self.store.create_proposal(
                    {
                        "id": pid,
                        "kind": "memory_suggestion",
                        "title": "Memory signals awaiting owner",
                        "target_ref": f"dream:{rid}:memory",
                        "status_projection": "review_pending",
                        "scope": "local",
                        "risk_tier": "low",
                        "reversibility": "n/a",
                        "evidence_snapshot_id": frozen.snapshot_id,
                        "evidence_strength": (
                            "moderate" if len(waiting) >= 2 else "tentative"
                        ),
                        "dream_id": rid,
                        "finding_ids": [fid],
                        "evidence_summary_json": _canonical_json(
                            {"ids": waiting, "count": len(waiting)}
                        ),
                        "counterevidence_json": _canonical_json(
                            frozen.counterevidence_ids
                        ),
                        "sensitivity_max": frozen.sensitivity_max,
                        "run_id": rid,
                        "epoch": epoch,
                        "lease_key": self.lease_key,
                    }
                )
                if proposal.get("outcome") not in {"created", "replayed"}:
                    raise RuntimeError(f"create_proposal_failed:{proposal!r}")
                proposal_ids.append(pid)

            # Housekeeping digest finding (report only — no auto retention).
            hk_id = f"find-{rid}-housekeeping"
            hk = self.store.create_finding(
                {
                    "id": hk_id,
                    "dream_id": rid,
                    "snapshot_id": frozen.snapshot_id,
                    "class_key": "sensor_digest",
                    "lane": "housekeeping",
                    "summary": (
                        f"Reviewed {frozen.source_counts.get('total', 0)} evidence "
                        f"item(s); retention not applied (report_only)"
                    ),
                    "evidence_strength": "strong",
                    "support_counts_json": frozen.source_counts_json(),
                    "evidence_ids": list(frozen.generation_ids[:16]),
                    "run_id": rid,
                    "epoch": epoch,
                    "lease_key": self.lease_key,
                }
            )
            if hk.get("outcome") not in {"created", "replayed"}:
                raise RuntimeError(f"create_finding_failed:{hk!r}")
            finding_ids.append(hk_id)

            return {
                "output_digest": _digest_mapping(buckets.to_dict()),
                "finding_ids": list(finding_ids),
                "proposal_ids": list(proposal_ids),
            }

        def stage_compiling() -> dict[str, Any]:
            # Report-only: no patch artifacts, no quarantine writes.
            return {
                "output_digest": digest_text("report_only:no_compile"),
                "compiled": False,
            }

        def stage_validating() -> dict[str, Any]:
            assert frozen is not None
            packet = {
                "snapshot": frozen.analyzer_packet(),
                "buckets": buckets.to_dict(),
                "finding_ids": finding_ids,
                "proposal_ids": proposal_ids,
            }
            clean = redact_packet(packet)
            assert_no_intimate_fields(clean)
            return {"output_digest": _digest_mapping(clean), "privacy": "ok"}

        def stage_publishing() -> dict[str, Any]:
            nonlocal report
            assert frozen is not None
            report = {
                "processing_mode": self.processing_mode,
                "schema_version": MAINTENANCE_SCHEMA_VERSION,
                "taxonomy_version": TAXONOMY_VERSION,
                "redaction_policy_version": REDACTION_POLICY_VERSION,
                "run_id": rid,
                "snapshot_id": frozen.snapshot_id,
                "source_ids_digest": frozen.source_ids_digest,
                "source_counts": frozen.source_counts,
                "reviewed_count": frozen.source_counts.get("total", 0),
                "auto_applied_count": 0,
                "suppressed_candidate_count": len(buckets.deliberately_left_alone),
                "buckets": buckets.to_dict(),
                "finding_ids": list(finding_ids),
                "proposal_ids": list(proposal_ids),
                "holdout_count": len(frozen.holdout_ids),
                "eligible_exposure_count": len(frozen.eligible_exposure_ids),
                "excluded_late_count": len(frozen.excluded_late_ids),
                "excluded_revoked_count": len(frozen.excluded_revoked_ids),
                # Holdout ids listed only as count in public report body;
                # ids kept for local audit under separate key not sent external.
                "generation_ids": list(frozen.generation_ids),
                "counterevidence_ids": list(frozen.counterevidence_ids),
            }
            report = redact_packet(report)
            assert_no_intimate_fields(report)
            return {"output_digest": _digest_mapping(report)}

        def stage_completed() -> dict[str, Any]:
            owner = (
                "needs_review"
                if buckets.waiting_for_owner
                else "completed_clean"
            )
            return {"owner_status": owner}

        handlers: dict[str, Callable[[], dict[str, Any]]] = {
            "leased": stage_leased,
            "snapshotting": stage_snapshotting,
            "normalizing": stage_normalizing,
            "clustering": stage_clustering,
            "planning": stage_planning,
            "compiling": stage_compiling,
            "validating": stage_validating,
            "publishing": stage_publishing,
            "completed": stage_completed,
        }

        final_owner = "running"
        try:
            for stage in REPORT_ONLY_STAGES:
                # Heartbeat renew before each stage.
                renew = self.store.renew_maintenance_lease(
                    {
                        "key": self.lease_key,
                        "holder_id": self.holder_id,
                        "run_id": rid,
                        "epoch": epoch,
                        "ttl_seconds": self.ttl_seconds,
                    }
                )
                if renew.get("outcome") == "stale_epoch":
                    raise RuntimeError("lease_lost:stale_epoch_on_renew")

                work = handlers[stage]()
                owner_status = work.pop("owner_status", None)
                if owner_status is not None:
                    final_owner = owner_status
                output_digest = work.get("output_digest")
                input_digest = work.get("input_digest")

                # Prefer local checkpoint when replaying after crash mid-stage.
                ck = self._local_checkpoints.get(f"{rid}:{stage}")
                if ck is not None and output_digest is None:
                    output_digest = ck.get("output_digest")

                stage_payload: dict[str, Any] = {
                    "run_id": rid,
                    "epoch": epoch,
                    "lease_key": self.lease_key,
                    "stage": stage,
                    "attempt": 0,
                    "stage_key": stage_idempotency_key(
                        run_id=rid, stage=stage, attempt=0
                    ),
                    "input_digest": input_digest,
                    "output_digest": output_digest,
                }
                if owner_status is not None:
                    stage_payload["owner_status"] = owner_status
                elif stage == "leased":
                    stage_payload["owner_status"] = "running"
                    final_owner = "running"

                recorded = self.store.record_dream_stage(stage_payload)
                outcome = str(recorded.get("outcome") or "error")
                stage_outcomes[stage] = outcome
                if outcome == "replayed":
                    resumed = True
                if outcome not in {"recorded", "replayed"}:
                    # Illegal transition can happen if dream is already past this
                    # stage without receipts in the fake — treat as hard error.
                    raise RuntimeError(
                        f"record_dream_stage_failed:{stage}:{recorded!r}"
                    )

                self._local_checkpoints[f"{rid}:{stage}"] = {
                    "output_digest": output_digest,
                    "work": {k: v for k, v in work.items() if k != "output_digest"},
                    "finished_at": _now_iso(),
                }

                if stop_after_stage is not None and stage == stop_after_stage:
                    raise DreamRunCheckpoint(
                        run_id=rid,
                        epoch=epoch,
                        stage=stage,
                        stage_outcomes=dict(stage_outcomes),
                    )

            # If freeze never ran (should not happen), fail closed.
            if frozen is None:
                raise RuntimeError("snapshot_missing_after_pipeline")

            if not report:
                # Completing without publishing should not happen; rebuild.
                report = {
                    "processing_mode": self.processing_mode,
                    "buckets": buckets.to_dict(),
                    "run_id": rid,
                    "snapshot_id": frozen.snapshot_id,
                    "source_ids_digest": frozen.source_ids_digest,
                    "auto_applied_count": 0,
                }
                report = redact_packet(report)

            result = DreamRunResult(
                run_id=rid,
                epoch=epoch,
                stage="completed",
                owner_status=final_owner,
                processing_mode=self.processing_mode,
                snapshot_id=frozen.snapshot_id,
                source_ids_digest=frozen.source_ids_digest,
                report=report,
                stage_outcomes=stage_outcomes,
                finding_ids=finding_ids,
                proposal_ids=proposal_ids,
                resumed=resumed,
            )
            return result
        except DreamRunCheckpoint:
            # Intentional pause for crash-resume tests — do not mark failed.
            raise
        except Exception:
            # Best-effort fail stage when fence still holds.
            try:
                self.store.record_dream_stage(
                    {
                        "run_id": rid,
                        "epoch": epoch,
                        "lease_key": self.lease_key,
                        "stage": "failed",
                        "owner_status": "failed",
                        "error_class": "dream_runner_error",
                    }
                )
            except Exception:
                pass
            raise
        finally:
            # Keep the lease on intentional checkpoint so resume shares the epoch.
            if release_lease and not isinstance(sys.exc_info()[1], DreamRunCheckpoint):
                try:
                    self.store.release_maintenance_lease(
                        {
                            "key": self.lease_key,
                            "holder_id": self.holder_id,
                            "run_id": rid,
                            "epoch": epoch,
                        }
                    )
                except Exception:
                    pass


def run_report_only_dream(
    store: MaintenanceStoreProtocol,
    evidence: Iterable[Mapping[str, Any] | EvidenceItem],
    *,
    holder_id: str,
    harness_generation_id: str,
    cutoff_at: str,
    run_id: str | None = None,
    correlation_key: bytes | str | None = None,
    base_commit: str | None = None,
    holdout_ratio: float = 0.2,
    holdout_ids: Iterable[str] | None = None,
    graph_bookmark: str | None = None,
) -> DreamRunResult:
    """Convenience wrapper for CLI and tests."""
    runner = DreamRunner(
        store=store,
        holder_id=holder_id,
        harness_generation_id=harness_generation_id,
        correlation_key=correlation_key,
        base_commit=base_commit,
    )
    return runner.run(
        evidence,
        cutoff_at=cutoff_at,
        run_id=run_id,
        holdout_ratio=holdout_ratio,
        holdout_ids=holdout_ids,
        graph_bookmark=graph_bookmark,
    )
