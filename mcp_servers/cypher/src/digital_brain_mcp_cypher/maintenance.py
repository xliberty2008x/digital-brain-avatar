"""Durable maintenance workflow records and fenced coordination.

Lease acquire/renew/release, DreamRun stage receipts, snapshots, findings,
proposals, evaluation, and decision records. Called only by deterministic
coordinator code (HTTP control API) — never registered as FastMCP tools.

ActivationAuthority mint/consume stays off this surface and off model-facing MCP.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable, Mapping

_logger = logging.getLogger(__name__)

_QUARANTINE_FILENAMES = frozenset(
    {
        "artifact.md",
        "checksums.json",
        "evaluation.json",
        "intent.json",
        "manifest.json",
    }
)
_MAX_QUARANTINE_BYTES = 1_048_576


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest_core_without_patch(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in sorted(dict(manifest).items()) if k != "patch_sha256"}


def _compute_quarantine_patch_sha256(
    *,
    intent: Mapping[str, Any],
    artifact: bytes,
    evaluation: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> str:
    """Mirror the compiler's closed-bundle digest without importing the app."""
    core = _canonical_json(_manifest_core_without_patch(manifest)).encode("utf-8")
    return _digest_text(
        _canonical_json(
            {
                "artifact.md": _sha256_bytes(artifact),
                "evaluation.json": _sha256_bytes(
                    _canonical_json(dict(evaluation)).encode("utf-8")
                ),
                "intent.json": _sha256_bytes(
                    _canonical_json(dict(intent)).encode("utf-8")
                ),
                "manifest_core": _sha256_bytes(core),
            }
        )
    )


def _verify_quarantine_bundle(
    *,
    artifact_path: str,
    proposal_id: str,
    evidence_snapshot_id: str,
    base_commit: str,
    compiler_version: str,
    schema_version: str,
    patch_sha256: str,
) -> dict[str, Any]:
    """Read and verify the immutable quarantine bundle before graph publish.

    This implementation intentionally uses only the standard library because
    the standalone MCP image does not include the application package.
    """
    raw_path = Path(artifact_path)
    if not raw_path.is_absolute():
        raise ValueError("artifact_path_must_be_absolute")
    if raw_path.name != "artifact.md":
        raise ValueError("artifact_path_must_name_artifact_md")

    # Refuse symlinks anywhere in the supplied existing path before resolving.
    for candidate in (raw_path, *raw_path.parents):
        if candidate.exists() and candidate.is_symlink():
            raise ValueError(f"artifact_path_symlink_forbidden:{candidate}")

    try:
        path = raw_path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"artifact_path_unreadable:{exc}") from exc
    directory = path.parent
    if (
        directory.name != proposal_id
        or directory.parent.name == ""
        or directory.parent.parent.name != "quarantine"
        or directory.parent.parent.parent.name != "dreams"
    ):
        raise ValueError("artifact_path_must_match_quarantine_layout")
    if any(
        part in {"plugins", "active-overlays", "node_modules"}
        for part in path.parts
    ):
        raise ValueError("artifact_path_forbidden_segment")

    present = {p.name for p in directory.iterdir()}
    if present != _QUARANTINE_FILENAMES:
        missing = sorted(_QUARANTINE_FILENAMES - present)
        extra = sorted(present - _QUARANTINE_FILENAMES)
        raise ValueError(f"quarantine_bundle_not_closed:missing={missing}:extra={extra}")

    raw: dict[str, bytes] = {}
    total = 0
    for name in sorted(_QUARANTINE_FILENAMES):
        item = directory / name
        if item.is_symlink() or not item.is_file():
            raise ValueError(f"quarantine_bundle_file_invalid:{name}")
        data = item.read_bytes()
        total += len(data)
        if total > _MAX_QUARANTINE_BYTES:
            raise ValueError("quarantine_bundle_size_overflow")
        raw[name] = data

    try:
        intent = json.loads(raw["intent.json"])
        evaluation = json.loads(raw["evaluation.json"])
        manifest = json.loads(raw["manifest.json"])
        checksums = json.loads(raw["checksums.json"])
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"quarantine_bundle_json_invalid:{exc}") from exc
    if not all(
        isinstance(obj, dict) for obj in (intent, evaluation, manifest, checksums)
    ):
        raise ValueError("quarantine_bundle_json_must_be_objects")

    expected_checksum_names = _QUARANTINE_FILENAMES - {"checksums.json"}
    if set(checksums) != expected_checksum_names:
        raise ValueError("quarantine_checksums_not_closed")
    for name in sorted(expected_checksum_names):
        computed = _sha256_bytes(raw[name])
        if str(checksums.get(name) or "") != computed:
            raise ValueError(f"quarantine_checksum_mismatch:{name}")

    computed_patch = _compute_quarantine_patch_sha256(
        intent=intent,
        artifact=raw["artifact.md"],
        evaluation=evaluation,
        manifest=manifest,
    )
    bindings = {
        "proposal_id": proposal_id,
        "evidence_snapshot_id": evidence_snapshot_id,
        "base_commit": base_commit,
        "compiler_version": compiler_version,
        "schema_version": schema_version,
        "patch_sha256": patch_sha256,
    }
    for field, expected in bindings.items():
        actual = str(manifest.get(field) or "")
        if actual != expected:
            raise ValueError(
                f"quarantine_manifest_binding_mismatch:{field}:"
                f"expected={expected}:actual={actual}"
            )
    if computed_patch != patch_sha256:
        raise ValueError(
            f"quarantine_patch_digest_mismatch:"
            f"expected={patch_sha256}:computed={computed_patch}"
        )
    return {
        "artifact_path": str(path),
        "manifest": manifest,
        "patch_sha256": computed_patch,
    }

# Import pure transition validators from shared models when available; keep
# local mirrors so the MCP package does not hard-depend on the app package
# layout inside the container image. Prefer digital_brain when importable.
try:
    from digital_brain.maintenance.models import (  # type: ignore
        AUTHORITY_STATUSES,
        DECISION_VALUES,
        DREAM_OWNER_STATUSES,
        DREAM_STAGES,
        EVIDENCE_ROLES,
        EVIDENCE_STRENGTHS,
        EVALUATION_OUTCOMES,
        FINDING_LANES,
        FORBIDDEN_ABSORPTION_FIELDS,
        MAINTENANCE_SCHEMA_VERSION,
        PROPOSAL_KINDS,
        PROPOSAL_STATUS_PROJECTIONS,
        assert_legal_dream_stage_transition,
        assert_legal_owner_status_transition,
        assert_no_absorption_field,
        dream_stage_request_fingerprint,
        stage_idempotency_key,
    )
except ImportError:  # pragma: no cover - container without app package
    MAINTENANCE_SCHEMA_VERSION = "1"
    DREAM_STAGES = frozenset(
        {
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
            "failed",
            "aborted",
            "lease_lost",
        }
    )
    DREAM_PIPELINE = (
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
    DREAM_TERMINAL = frozenset({"completed", "failed", "aborted", "lease_lost"})
    DREAM_OWNER_STATUSES = frozenset(
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
    PROPOSAL_STATUS_PROJECTIONS = frozenset(
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
    PROPOSAL_KINDS = frozenset(
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
    EVIDENCE_STRENGTHS = frozenset({"tentative", "moderate", "strong"})
    FINDING_LANES = frozenset({"housekeeping", "memory", "behaviour", "engineering"})
    DECISION_VALUES = frozenset({"approved", "rejected", "deferred", "withdrawn"})
    EVALUATION_OUTCOMES = frozenset({"passed", "failed", "inconclusive"})
    AUTHORITY_STATUSES = frozenset({"minted", "consumed", "expired", "revoked"})
    EVIDENCE_ROLES = frozenset({"generation", "counterevidence", "holdout"})
    FORBIDDEN_ABSORPTION_FIELDS = frozenset(
        {"absorbed_by_dream_id", "absorbed_by_dream", "dream_absorption_id"}
    )

    def stage_idempotency_key(*, run_id: str, stage: str, attempt: int = 0) -> str:
        return f"{run_id}:{stage}:{int(attempt)}"

    def assert_no_absorption_field(payload: Mapping[str, Any]) -> None:
        for key in payload:
            if str(key) in FORBIDDEN_ABSORPTION_FIELDS:
                raise ValueError(f"forbidden field {key!r}")

    def assert_legal_dream_stage_transition(
        from_stage: str | None, to_stage: str, *, allow_replay: bool = True
    ) -> None:
        to_stage = str(to_stage or "").strip()
        if to_stage not in DREAM_STAGES:
            raise ValueError("illegal_dream_stage_transition")
        if from_stage is None or str(from_stage).strip() == "":
            if to_stage != "queued":
                raise ValueError("illegal_dream_stage_transition")
            return
        from_stage = str(from_stage).strip()
        if from_stage == to_stage and allow_replay:
            return
        if from_stage in DREAM_TERMINAL:
            raise ValueError("illegal_dream_stage_transition")
        if to_stage in {"failed", "aborted", "lease_lost"}:
            return
        if from_stage not in DREAM_PIPELINE or to_stage not in DREAM_PIPELINE:
            raise ValueError("illegal_dream_stage_transition")
        if DREAM_PIPELINE.index(to_stage) != DREAM_PIPELINE.index(from_stage) + 1:
            raise ValueError("illegal_dream_stage_transition")

    def assert_legal_owner_status_transition(
        from_status: str | None, to_status: str, *, allow_replay: bool = True
    ) -> None:
        to_status = str(to_status or "").strip()
        if to_status not in DREAM_OWNER_STATUSES:
            raise ValueError("illegal_owner_status_transition")
        if from_status is None or str(from_status).strip() == "":
            if to_status != "scheduled":
                raise ValueError("illegal_owner_status_transition")
            return
        from_status = str(from_status).strip()
        if from_status == to_status and allow_replay:
            return
        terminal = {
            "completed_clean",
            "completed_partial",
            "failed",
            "cancelled",
            "lease_lost",
        }
        if from_status in terminal:
            raise ValueError("illegal_owner_status_transition")
        allowed = {
            "scheduled": {"running", "cancelled", "failed", "lease_lost"},
            "running": {
                "needs_review",
                "completed_clean",
                "completed_partial",
                "failed",
                "cancelled",
                "lease_lost",
            },
            "needs_review": {
                "completed_clean",
                "completed_partial",
                "failed",
                "cancelled",
                "lease_lost",
            },
        }
        if to_status not in allowed.get(from_status, set()):
            raise ValueError("illegal_owner_status_transition")

    def dream_stage_request_fingerprint(**kwargs: Any) -> str:
        payload = {
            "attempt": int(kwargs.get("attempt") or 0),
            "input_digest": kwargs.get("input_digest"),
            "lease_epoch": int(kwargs["lease_epoch"]),
            "output_digest": kwargs.get("output_digest"),
            "run_id": kwargs["run_id"],
            "stage": kwargs["stage"],
            "stage_key": kwargs["stage_key"],
        }
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


try:
    from digital_brain.maintenance.evaluation import (  # type: ignore
        STATUSES_REQUIRING_EVALUATION,
        EvaluationGateError,
        assert_evaluation_present_for_transition,
    )
except ImportError:  # pragma: no cover - container without app package
    STATUSES_REQUIRING_EVALUATION = frozenset(
        {"validated", "review_pending", "approved"}
    )

    class EvaluationGateError(ValueError):
        """Raised when evaluation preconditions fail for a proposal transition."""

    def assert_evaluation_present_for_transition(
        *,
        target_status: str,
        evaluation_receipt: Mapping[str, Any] | None,
    ) -> None:
        if target_status not in STATUSES_REQUIRING_EVALUATION:
            return
        if evaluation_receipt is None:
            raise EvaluationGateError(
                f"evaluation_required_for_status:{target_status}"
            )
        outcome = str(evaluation_receipt.get("outcome") or "")
        inv = str(evaluation_receipt.get("invariant_result") or "")
        priv = str(evaluation_receipt.get("privacy_result") or "")
        if outcome not in EVALUATION_OUTCOMES:
            raise EvaluationGateError("evaluation_receipt_missing_outcome")
        if target_status in {"review_pending", "approved", "validated"}:
            if priv in {"failed", "fail"} or inv in {"failed", "fail"}:
                raise EvaluationGateError(
                    f"hard_gate_blocks_transition:{target_status}"
                    f":privacy={priv}:invariant={inv}"
                )
            if outcome == "failed":
                raise EvaluationGateError(
                    f"failed_evaluation_blocks_transition:{target_status}"
                )


DEFAULT_LEASE_KEY = "maintenance"
DEFAULT_LEASE_TTL_SECONDS = 300
MAX_ID_LEN = 128
MAX_SUMMARY_LEN = 512
MAX_JSON_FIELD_LEN = 16_384
MAX_TITLE_LEN = 256
MAX_REF_LEN = 256

MAINTENANCE_CONSTRAINTS = (
    """
    CREATE CONSTRAINT operational_dream_stage_receipt_id_unique IF NOT EXISTS
    FOR (n:DreamStageReceipt) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT operational_evidence_snapshot_id_unique IF NOT EXISTS
    FOR (n:EvidenceSnapshot) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT operational_finding_id_unique IF NOT EXISTS
    FOR (n:Finding) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT operational_proposal_id_unique IF NOT EXISTS
    FOR (n:Proposal) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT operational_evaluation_receipt_id_unique IF NOT EXISTS
    FOR (n:EvaluationReceipt) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT operational_decision_id_unique IF NOT EXISTS
    FOR (n:Decision) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT operational_activation_authority_id_unique IF NOT EXISTS
    FOR (n:ActivationAuthority) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT operational_deployment_id_unique IF NOT EXISTS
    FOR (n:Deployment) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT operational_exposure_window_id_unique IF NOT EXISTS
    FOR (n:ExposureWindow) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT operational_patch_artifact_id_unique IF NOT EXISTS
    FOR (n:PatchArtifact) REQUIRE n.id IS UNIQUE
    """,
)


def _consume(result: Any) -> None:
    if hasattr(result, "consume"):
        result.consume()


def _run_one(runner: Any, query: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    result = runner.run(query, params or {})
    record = result.single()
    if record is None:
        return None
    if hasattr(record, "data"):
        return record.data()
    return dict(record)


def _execute_write(session: Any, fn: Callable[[Any], Any]) -> Any:
    execute_write = getattr(session, "execute_write", None) or getattr(
        session, "write_transaction", None
    )
    if execute_write is None:
        return fn(session)
    return execute_write(fn)


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    value = value.strip()
    if len(value) > MAX_ID_LEN:
        raise ValueError(f"{field} exceeds max length {MAX_ID_LEN}")
    return value


def _require_enum(value: Any, allowed: frozenset[str], field: str) -> str:
    if not isinstance(value, str) or value.strip() not in allowed:
        raise ValueError(f"{field} must be one of {sorted(allowed)}")
    return value.strip()


def _optional_text(value: Any, field: str, max_len: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string or null")
    text = value.strip()
    if not text:
        return None
    if len(text) > max_len:
        raise ValueError(f"{field} exceeds max length {max_len}")
    return text


def _require_int(value: Any, field: str, *, min_value: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        # JSON may deliver numbers as int already; reject bools
        try:
            if isinstance(value, str) and value.strip().isdigit():
                value = int(value.strip())
            else:
                raise TypeError
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be an integer") from exc
    if min_value is not None and value < min_value:
        raise ValueError(f"{field} must be >= {min_value}")
    return int(value)


def _json_field(value: Any, field: str, *, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        encoded = _canonical_json(value)  # type: ignore[arg-type]
    elif isinstance(value, str):
        encoded = value
    else:
        raise TypeError(f"{field} must be a string, object, array, or null")
    if len(encoded) > MAX_JSON_FIELD_LEN:
        raise ValueError(f"{field} exceeds max length {MAX_JSON_FIELD_LEN}")
    return encoded


def _normalize_evaluation_receipt_view(
    row: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.get("id"),
        "outcome": row.get("outcome"),
        "privacy_result": row.get("privacy_result"),
        "invariant_result": row.get("invariant_result"),
        "proposal_id": row.get("proposal_id"),
        "evaluator_version": row.get("evaluator_version"),
        "fixture_snapshot": row.get("fixture_snapshot"),
        "holdout_ids": row.get("holdout_ids"),
        "fixture_digest": row.get("fixture_digest"),
    }


def _load_evaluation_receipt_for_gate(
    tx: Any,
    *,
    proposal_id: str | None = None,
    evaluation_receipt_id: str | None = None,
) -> dict[str, Any] | None:
    """Load the best EvaluationReceipt for a proposal status transition gate."""
    if evaluation_receipt_id:
        row = _run_one(
            tx,
            """
            MATCH (e:Operational:EvaluationReceipt {id: $id})
            RETURN e.id AS id,
                   e.outcome AS outcome,
                   e.privacy_result AS privacy_result,
                   e.invariant_result AS invariant_result,
                   e.proposal_id AS proposal_id,
                   e.evaluator_version AS evaluator_version,
                   e.fixture_snapshot AS fixture_snapshot
            LIMIT 1
            """,
            {"id": evaluation_receipt_id},
        )
        return _normalize_evaluation_receipt_view(row)

    if not proposal_id:
        return None

    # Prefer HAS_EVALUATION edge when present.
    row = _run_one(
        tx,
        """
        MATCH (p:Operational:Proposal {id: $proposal_id})
              -[:HAS_EVALUATION]->(e:Operational:EvaluationReceipt)
        RETURN e.id AS id,
               e.outcome AS outcome,
               e.privacy_result AS privacy_result,
               e.invariant_result AS invariant_result,
               e.proposal_id AS proposal_id,
               e.evaluator_version AS evaluator_version,
               e.fixture_snapshot AS fixture_snapshot
        ORDER BY e.created_at DESC
        LIMIT 1
        """,
        {"proposal_id": proposal_id},
    )
    if row is not None and row.get("id") is not None:
        return _normalize_evaluation_receipt_view(row)

    # Fall back to receipts keyed by proposal_id (pre-link create).
    row = _run_one(
        tx,
        """
        MATCH (e:Operational:EvaluationReceipt {proposal_id: $proposal_id})
        RETURN e.id AS id,
               e.outcome AS outcome,
               e.privacy_result AS privacy_result,
               e.invariant_result AS invariant_result,
               e.proposal_id AS proposal_id,
               e.evaluator_version AS evaluator_version,
               e.fixture_snapshot AS fixture_snapshot
        ORDER BY e.created_at DESC
        LIMIT 1
        """,
        {"proposal_id": proposal_id},
    )
    if row is None or row.get("id") is None:
        return None
    return _normalize_evaluation_receipt_view(row)


def _assert_evaluation_gate(
    *,
    target_status: str,
    evaluation_receipt: Mapping[str, Any] | None,
) -> None:
    """Raise EvaluationGateError when evaluation is missing or hard-failed."""
    assert_evaluation_present_for_transition(
        target_status=target_status,
        evaluation_receipt=evaluation_receipt,
    )


class MaintenanceStore:
    """Neo4j operations for fenced maintenance workflow records."""

    def __init__(self, driver_factory: Callable[[], Any], database: str):
        self._driver_factory = driver_factory
        self._database = database

    def _with_session(self, operation: Callable[[Any], Any]) -> Any:
        with self._driver_factory() as driver:
            with driver.session(database=self._database) as session:
                return operation(session)

    def ensure_constraints(self) -> None:
        def operation(session: Any) -> None:
            for query in MAINTENANCE_CONSTRAINTS:
                _consume(session.run(query))

        self._with_session(operation)

    # ------------------------------------------------------------------
    # Lease fencing
    # ------------------------------------------------------------------

    def acquire_maintenance_lease(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Acquire or take over an expired lease using database time.

        On first create epoch=1. After expiry, epoch is incremented (monotonic).
        Active hold by another holder/run returns ``held``.
        Same holder + run_id while active renews without epoch change.
        """
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")
        key = _require_id(payload.get("key") or DEFAULT_LEASE_KEY, "key")
        holder_id = _require_id(payload.get("holder_id"), "holder_id")
        run_id = _require_id(payload.get("run_id"), "run_id")
        ttl_seconds = payload.get("ttl_seconds", DEFAULT_LEASE_TTL_SECONDS)
        ttl_seconds = _require_int(ttl_seconds, "ttl_seconds", min_value=1)
        if ttl_seconds > 86_400:
            raise ValueError("ttl_seconds must be <= 86400")

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._acquire_lease_tx(
                    tx,
                    key=key,
                    holder_id=holder_id,
                    run_id=run_id,
                    ttl_seconds=ttl_seconds,
                ),
            )

        return self._with_session(operation)

    def _acquire_lease_tx(
        self,
        tx: Any,
        *,
        key: str,
        holder_id: str,
        run_id: str,
        ttl_seconds: int,
    ) -> dict[str, Any]:
        row = _run_one(
            tx,
            """
            MATCH (l:Operational:MaintenanceLease {key: $key})
            RETURN l.key AS key,
                   l.holder_id AS holder_id,
                   l.run_id AS run_id,
                   l.epoch AS epoch,
                   toString(l.lease_until) AS lease_until,
                   toString(l.heartbeat_at) AS heartbeat_at,
                   l.lease_until < datetime() AS expired,
                   toString(datetime()) AS db_now
            LIMIT 1
            """,
            {"key": key},
        )

        if row is None:
            created = _run_one(
                tx,
                """
                CREATE (l:Operational:MaintenanceLease)
                SET l.key = $key,
                    l.id = $key,
                    l.holder_id = $holder_id,
                    l.run_id = $run_id,
                    l.epoch = 1,
                    l.lease_until = datetime() + duration({seconds: $ttl}),
                    l.heartbeat_at = datetime()
                RETURN l.key AS key,
                       l.holder_id AS holder_id,
                       l.run_id AS run_id,
                       l.epoch AS epoch,
                       toString(l.lease_until) AS lease_until,
                       toString(l.heartbeat_at) AS heartbeat_at
                """,
                {
                    "key": key,
                    "holder_id": holder_id,
                    "run_id": run_id,
                    "ttl": ttl_seconds,
                },
            )
            if created is None:
                raise RuntimeError("lease create returned no row")
            return {
                "outcome": "acquired",
                "key": created["key"],
                "holder_id": created["holder_id"],
                "run_id": created["run_id"],
                "epoch": int(created["epoch"]),
                "lease_until": created["lease_until"],
                "heartbeat_at": created["heartbeat_at"],
            }

        expired = bool(row.get("expired"))
        same_holder = (
            row.get("holder_id") == holder_id and row.get("run_id") == run_id
        )

        if not expired and not same_holder:
            return {
                "outcome": "held",
                "reason": "lease_held_by_other",
                "key": row["key"],
                "holder_id": row["holder_id"],
                "run_id": row["run_id"],
                "epoch": int(row["epoch"]),
                "lease_until": row["lease_until"],
                "heartbeat_at": row["heartbeat_at"],
            }

        if not expired and same_holder:
            renewed = _run_one(
                tx,
                """
                MATCH (l:Operational:MaintenanceLease {key: $key})
                WHERE l.holder_id = $holder_id
                  AND l.run_id = $run_id
                  AND l.epoch = $epoch
                  AND l.lease_until >= datetime()
                SET l.lease_until = datetime() + duration({seconds: $ttl}),
                    l.heartbeat_at = datetime()
                RETURN l.key AS key,
                       l.holder_id AS holder_id,
                       l.run_id AS run_id,
                       l.epoch AS epoch,
                       toString(l.lease_until) AS lease_until,
                       toString(l.heartbeat_at) AS heartbeat_at
                """,
                {
                    "key": key,
                    "holder_id": holder_id,
                    "run_id": run_id,
                    "epoch": int(row["epoch"]),
                    "ttl": ttl_seconds,
                },
            )
            if renewed is None:
                return {
                    "outcome": "stale_epoch",
                    "reason": "lease_lost_during_renew",
                    "key": key,
                }
            return {
                "outcome": "renewed",
                "key": renewed["key"],
                "holder_id": renewed["holder_id"],
                "run_id": renewed["run_id"],
                "epoch": int(renewed["epoch"]),
                "lease_until": renewed["lease_until"],
                "heartbeat_at": renewed["heartbeat_at"],
            }

        # Expired → takeover with monotonic epoch bump (even for same holder).
        new_epoch = int(row["epoch"]) + 1
        taken = _run_one(
            tx,
            """
            MATCH (l:Operational:MaintenanceLease {key: $key})
            WHERE l.epoch = $old_epoch
              AND l.lease_until < datetime()
            SET l.holder_id = $holder_id,
                l.run_id = $run_id,
                l.epoch = $new_epoch,
                l.lease_until = datetime() + duration({seconds: $ttl}),
                l.heartbeat_at = datetime()
            RETURN l.key AS key,
                   l.holder_id AS holder_id,
                   l.run_id AS run_id,
                   l.epoch AS epoch,
                   toString(l.lease_until) AS lease_until,
                   toString(l.heartbeat_at) AS heartbeat_at
            """,
            {
                "key": key,
                "old_epoch": int(row["epoch"]),
                "new_epoch": new_epoch,
                "holder_id": holder_id,
                "run_id": run_id,
                "ttl": ttl_seconds,
            },
        )
        if taken is None:
            # Race: another worker acquired between read and write.
            current = self._read_lease(tx, key)
            return {
                "outcome": "held",
                "reason": "lease_race",
                **(current or {"key": key}),
            }
        return {
            "outcome": "acquired",
            "key": taken["key"],
            "holder_id": taken["holder_id"],
            "run_id": taken["run_id"],
            "epoch": int(taken["epoch"]),
            "lease_until": taken["lease_until"],
            "heartbeat_at": taken["heartbeat_at"],
            "previous_epoch": int(row["epoch"]),
        }

    def renew_maintenance_lease(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")
        key = _require_id(payload.get("key") or DEFAULT_LEASE_KEY, "key")
        holder_id = _require_id(payload.get("holder_id"), "holder_id")
        run_id = _require_id(payload.get("run_id"), "run_id")
        epoch = _require_int(payload.get("epoch"), "epoch", min_value=1)
        ttl_seconds = _require_int(
            payload.get("ttl_seconds", DEFAULT_LEASE_TTL_SECONDS),
            "ttl_seconds",
            min_value=1,
        )

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._renew_lease_tx(
                    tx,
                    key=key,
                    holder_id=holder_id,
                    run_id=run_id,
                    epoch=epoch,
                    ttl_seconds=ttl_seconds,
                ),
            )

        return self._with_session(operation)

    def _renew_lease_tx(
        self,
        tx: Any,
        *,
        key: str,
        holder_id: str,
        run_id: str,
        epoch: int,
        ttl_seconds: int,
    ) -> dict[str, Any]:
        renewed = _run_one(
            tx,
            """
            MATCH (l:Operational:MaintenanceLease {key: $key})
            WHERE l.holder_id = $holder_id
              AND l.run_id = $run_id
              AND l.epoch = $epoch
              AND l.lease_until >= datetime()
            SET l.lease_until = datetime() + duration({seconds: $ttl}),
                l.heartbeat_at = datetime()
            RETURN l.key AS key,
                   l.holder_id AS holder_id,
                   l.run_id AS run_id,
                   l.epoch AS epoch,
                   toString(l.lease_until) AS lease_until,
                   toString(l.heartbeat_at) AS heartbeat_at
            """,
            {
                "key": key,
                "holder_id": holder_id,
                "run_id": run_id,
                "epoch": epoch,
                "ttl": ttl_seconds,
            },
        )
        if renewed is not None:
            return {
                "outcome": "renewed",
                "key": renewed["key"],
                "holder_id": renewed["holder_id"],
                "run_id": renewed["run_id"],
                "epoch": int(renewed["epoch"]),
                "lease_until": renewed["lease_until"],
                "heartbeat_at": renewed["heartbeat_at"],
            }
        current = self._read_lease(tx, key)
        if current is None:
            return {"outcome": "not_found", "key": key}
        if int(current.get("epoch") or 0) != epoch:
            return {
                "outcome": "stale_epoch",
                "reason": "epoch_mismatch",
                "key": key,
                "current_epoch": current.get("epoch"),
                "requested_epoch": epoch,
            }
        if current.get("holder_id") != holder_id or current.get("run_id") != run_id:
            return {
                "outcome": "stale_epoch",
                "reason": "holder_mismatch",
                "key": key,
                **{k: current.get(k) for k in ("holder_id", "run_id", "epoch")},
            }
        return {
            "outcome": "stale_epoch",
            "reason": "lease_expired",
            "key": key,
            "epoch": epoch,
        }

    def release_maintenance_lease(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")
        key = _require_id(payload.get("key") or DEFAULT_LEASE_KEY, "key")
        holder_id = _require_id(payload.get("holder_id"), "holder_id")
        run_id = _require_id(payload.get("run_id"), "run_id")
        epoch = _require_int(payload.get("epoch"), "epoch", min_value=1)

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._release_lease_tx(
                    tx, key=key, holder_id=holder_id, run_id=run_id, epoch=epoch
                ),
            )

        return self._with_session(operation)

    def _release_lease_tx(
        self,
        tx: Any,
        *,
        key: str,
        holder_id: str,
        run_id: str,
        epoch: int,
    ) -> dict[str, Any]:
        released = _run_one(
            tx,
            """
            MATCH (l:Operational:MaintenanceLease {key: $key})
            WHERE l.holder_id = $holder_id
              AND l.run_id = $run_id
              AND l.epoch = $epoch
            SET l.lease_until = datetime() - duration({seconds: 1}),
                l.heartbeat_at = datetime()
            RETURN l.key AS key,
                   l.epoch AS epoch,
                   toString(l.lease_until) AS lease_until
            """,
            {
                "key": key,
                "holder_id": holder_id,
                "run_id": run_id,
                "epoch": epoch,
            },
        )
        if released is not None:
            return {
                "outcome": "released",
                "key": released["key"],
                "epoch": int(released["epoch"]),
                "lease_until": released["lease_until"],
            }
        current = self._read_lease(tx, key)
        if current is None:
            return {"outcome": "not_found", "key": key}
        return {
            "outcome": "stale_epoch",
            "reason": "cannot_release_foreign_or_stale_lease",
            "key": key,
            "current_epoch": current.get("epoch"),
            "requested_epoch": epoch,
        }

    @staticmethod
    def _read_lease(tx: Any, key: str) -> dict[str, Any] | None:
        return _run_one(
            tx,
            """
            MATCH (l:Operational:MaintenanceLease {key: $key})
            RETURN l.key AS key,
                   l.holder_id AS holder_id,
                   l.run_id AS run_id,
                   l.epoch AS epoch,
                   toString(l.lease_until) AS lease_until,
                   toString(l.heartbeat_at) AS heartbeat_at,
                   l.lease_until < datetime() AS expired
            LIMIT 1
            """,
            {"key": key},
        )

    def _assert_fence(
        self,
        tx: Any,
        *,
        lease_key: str,
        run_id: str,
        epoch: int,
    ) -> dict[str, Any] | None:
        """Return error dict when fence is invalid; None when current."""
        lease = self._read_lease(tx, lease_key)
        if lease is None:
            return {
                "outcome": "stale_epoch",
                "reason": "lease_not_found",
                "lease_key": lease_key,
                "run_id": run_id,
                "epoch": epoch,
            }
        if int(lease.get("epoch") or 0) != int(epoch):
            return {
                "outcome": "stale_epoch",
                "reason": "epoch_mismatch",
                "lease_key": lease_key,
                "run_id": run_id,
                "epoch": epoch,
                "current_epoch": lease.get("epoch"),
                "current_run_id": lease.get("run_id"),
            }
        if lease.get("run_id") != run_id:
            return {
                "outcome": "stale_epoch",
                "reason": "run_id_mismatch",
                "lease_key": lease_key,
                "run_id": run_id,
                "epoch": epoch,
                "current_run_id": lease.get("run_id"),
            }
        if lease.get("expired"):
            return {
                "outcome": "stale_epoch",
                "reason": "lease_expired",
                "lease_key": lease_key,
                "run_id": run_id,
                "epoch": epoch,
            }
        return None

    # ------------------------------------------------------------------
    # DreamRun + stages
    # ------------------------------------------------------------------

    def create_dream_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")
        assert_no_absorption_field(payload)

        run_id = _require_id(payload.get("id") or payload.get("run_id"), "id")
        harness_generation_id = _require_id(
            payload.get("harness_generation_id"), "harness_generation_id"
        )
        processing_mode = _optional_text(
            payload.get("processing_mode") or "report_only",
            "processing_mode",
            MAX_REF_LEN,
        ) or "report_only"
        lease_key = _require_id(
            payload.get("lease_key") or DEFAULT_LEASE_KEY, "lease_key"
        )
        # Fence is required so create is only done by the current lease holder.
        # DreamRun.id must equal the lease fence run_id — a mismatch would let a
        # holder mint a dream under a foreign identity while fencing as itself.
        fence_run_id = _require_id(payload.get("run_id") or run_id, "run_id")
        if run_id != fence_run_id:
            raise ValueError(
                "dream id must equal lease fence run_id "
                f"(id={run_id!r}, run_id={fence_run_id!r}); "
                "create_dream_run binds DreamRun.id to the holder lease fence"
            )
        epoch = _require_int(payload.get("epoch") or payload.get("lease_epoch"), "epoch", min_value=1)
        holder_id = _optional_text(payload.get("holder_id"), "holder_id", MAX_ID_LEN)
        base_commit = _optional_text(payload.get("base_commit"), "base_commit", MAX_REF_LEN)

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._create_dream_run_tx(
                    tx,
                    run_id=run_id,
                    fence_run_id=fence_run_id,
                    epoch=epoch,
                    lease_key=lease_key,
                    harness_generation_id=harness_generation_id,
                    processing_mode=processing_mode,
                    holder_id=holder_id,
                    base_commit=base_commit,
                ),
            )

        return self._with_session(operation)

    def _create_dream_run_tx(
        self,
        tx: Any,
        *,
        run_id: str,
        fence_run_id: str,
        epoch: int,
        lease_key: str,
        harness_generation_id: str,
        processing_mode: str,
        holder_id: str | None,
        base_commit: str | None,
    ) -> dict[str, Any]:
        fence_err = self._assert_fence(
            tx, lease_key=lease_key, run_id=fence_run_id, epoch=epoch
        )
        if fence_err is not None:
            return fence_err

        existing = _run_one(
            tx,
            """
            MATCH (d:Operational:DreamRun {id: $run_id})
            RETURN d.id AS id,
                   d.stage AS stage,
                   d.owner_status AS owner_status,
                   d.lease_epoch AS lease_epoch,
                   d.harness_generation_id AS harness_generation_id,
                   d.request_fingerprint AS request_fingerprint
            LIMIT 1
            """,
            {"run_id": run_id},
        )
        identity = {
            "base_commit": base_commit,
            "harness_generation_id": harness_generation_id,
            "id": run_id,
            "lease_epoch": epoch,
            "lease_key": lease_key,
            "processing_mode": processing_mode,
        }
        request_fingerprint = _digest_text(_canonical_json(identity))
        if existing is not None:
            if existing.get("request_fingerprint") == request_fingerprint:
                return {
                    "outcome": "replayed",
                    "run_id": existing["id"],
                    "stage": existing.get("stage"),
                    "owner_status": existing.get("owner_status"),
                    "lease_epoch": existing.get("lease_epoch"),
                    "request_fingerprint": existing.get("request_fingerprint"),
                }
            return {
                "outcome": "conflict",
                "reason": "dream_run_id_reused",
                "run_id": existing["id"],
                "request_fingerprint": existing.get("request_fingerprint"),
            }

        created = _run_one(
            tx,
            """
            CREATE (d:Operational:DreamRun)
            SET d.id = $id,
                d.owner_status = 'scheduled',
                d.stage = 'queued',
                d.attempt = 0,
                d.holder_id = $holder_id,
                d.lease_epoch = $epoch,
                d.lease_key = $lease_key,
                d.harness_generation_id = $harness_generation_id,
                d.processing_mode = $processing_mode,
                d.base_commit = $base_commit,
                d.schema_version = $schema_version,
                d.request_fingerprint = $request_fingerprint,
                d.reviewed_count = 0,
                d.auto_applied_count = 0,
                d.suppressed_candidate_count = 0,
                d.started_at = datetime(),
                d.created_at = datetime()
            RETURN d.id AS id,
                   d.stage AS stage,
                   d.owner_status AS owner_status,
                   d.lease_epoch AS lease_epoch,
                   d.request_fingerprint AS request_fingerprint,
                   toString(d.started_at) AS started_at
            """,
            {
                "id": run_id,
                "holder_id": holder_id,
                "epoch": epoch,
                "lease_key": lease_key,
                "harness_generation_id": harness_generation_id,
                "processing_mode": processing_mode,
                "base_commit": base_commit,
                "schema_version": MAINTENANCE_SCHEMA_VERSION,
                "request_fingerprint": request_fingerprint,
            },
        )
        if created is None:
            raise RuntimeError("DreamRun create returned no row")
        # Initial stage receipt (queued).
        stage_key = stage_idempotency_key(run_id=run_id, stage="queued", attempt=0)
        fp = dream_stage_request_fingerprint(
            run_id=run_id,
            stage="queued",
            stage_key=stage_key,
            lease_epoch=epoch,
            input_digest=None,
            output_digest=None,
            attempt=0,
        )
        _run_one(
            tx,
            """
            CREATE (s:Operational:DreamStageReceipt)
            SET s.id = $stage_key,
                s.run_id = $run_id,
                s.stage = 'queued',
                s.stage_key = $stage_key,
                s.attempt = 0,
                s.lease_epoch = $epoch,
                s.outcome = 'recorded',
                s.request_fingerprint = $fp,
                s.started_at = datetime(),
                s.finished_at = datetime()
            WITH s
            MATCH (d:Operational:DreamRun {id: $run_id})
            MERGE (d)-[:HAS_STAGE_RECEIPT]->(s)
            RETURN s.id AS id
            """,
            {
                "stage_key": stage_key,
                "run_id": run_id,
                "epoch": epoch,
                "fp": fp,
            },
        )
        return {
            "outcome": "created",
            "run_id": created["id"],
            "stage": created["stage"],
            "owner_status": created["owner_status"],
            "lease_epoch": int(created["lease_epoch"]),
            "request_fingerprint": created["request_fingerprint"],
            "started_at": created.get("started_at"),
        }

    def record_dream_stage(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Advance DreamRun stage under run_id + epoch fence; idempotent stage keys."""
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")
        assert_no_absorption_field(payload)

        run_id = _require_id(payload.get("run_id"), "run_id")
        epoch = _require_int(
            payload.get("epoch") if payload.get("epoch") is not None else payload.get("lease_epoch"),
            "epoch",
            min_value=1,
        )
        stage = _require_enum(payload.get("stage"), DREAM_STAGES, "stage")
        attempt = _require_int(payload.get("attempt", 0), "attempt", min_value=0)
        stage_key = _optional_text(payload.get("stage_key"), "stage_key", MAX_ID_LEN)
        if stage_key is None:
            stage_key = stage_idempotency_key(
                run_id=run_id, stage=stage, attempt=attempt
            )
        lease_key = _require_id(
            payload.get("lease_key") or DEFAULT_LEASE_KEY, "lease_key"
        )
        input_digest = _optional_text(
            payload.get("input_digest"), "input_digest", MAX_REF_LEN
        )
        output_digest = _optional_text(
            payload.get("output_digest"), "output_digest", MAX_REF_LEN
        )
        error_class = _optional_text(
            payload.get("error_class"), "error_class", MAX_REF_LEN
        )
        owner_status = payload.get("owner_status")
        if owner_status is not None:
            owner_status = _require_enum(
                owner_status, DREAM_OWNER_STATUSES, "owner_status"
            )

        request_fingerprint = dream_stage_request_fingerprint(
            run_id=run_id,
            stage=stage,
            stage_key=stage_key,
            lease_epoch=epoch,
            input_digest=input_digest,
            output_digest=output_digest,
            attempt=attempt,
        )

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._record_stage_tx(
                    tx,
                    run_id=run_id,
                    epoch=epoch,
                    lease_key=lease_key,
                    stage=stage,
                    stage_key=stage_key,
                    attempt=attempt,
                    input_digest=input_digest,
                    output_digest=output_digest,
                    error_class=error_class,
                    owner_status=owner_status,
                    request_fingerprint=request_fingerprint,
                ),
            )

        return self._with_session(operation)

    def _record_stage_tx(
        self,
        tx: Any,
        *,
        run_id: str,
        epoch: int,
        lease_key: str,
        stage: str,
        stage_key: str,
        attempt: int,
        input_digest: str | None,
        output_digest: str | None,
        error_class: str | None,
        owner_status: str | None,
        request_fingerprint: str,
    ) -> dict[str, Any]:
        fence_err = self._assert_fence(
            tx, lease_key=lease_key, run_id=run_id, epoch=epoch
        )
        if fence_err is not None:
            return fence_err

        existing_receipt = _run_one(
            tx,
            """
            MATCH (s:Operational:DreamStageReceipt {id: $stage_key})
            RETURN s.id AS id,
                   s.run_id AS run_id,
                   s.stage AS stage,
                   s.lease_epoch AS lease_epoch,
                   s.request_fingerprint AS request_fingerprint,
                   s.outcome AS outcome
            LIMIT 1
            """,
            {"stage_key": stage_key},
        )
        if existing_receipt is not None:
            if existing_receipt.get("request_fingerprint") == request_fingerprint:
                return {
                    "outcome": "replayed",
                    "run_id": run_id,
                    "stage": existing_receipt.get("stage"),
                    "stage_key": existing_receipt.get("id"),
                    "lease_epoch": existing_receipt.get("lease_epoch"),
                    "request_fingerprint": existing_receipt.get("request_fingerprint"),
                }
            return {
                "outcome": "conflict",
                "reason": "stage_key_reused",
                "stage_key": stage_key,
                "request_fingerprint": existing_receipt.get("request_fingerprint"),
            }

        dream = _run_one(
            tx,
            """
            MATCH (d:Operational:DreamRun {id: $run_id})
            RETURN d.id AS id,
                   d.stage AS stage,
                   d.owner_status AS owner_status,
                   d.lease_epoch AS lease_epoch,
                   d.lease_key AS lease_key
            LIMIT 1
            """,
            {"run_id": run_id},
        )
        if dream is None:
            return {"outcome": "not_found", "reason": "dream_run_not_found", "run_id": run_id}

        if int(dream.get("lease_epoch") or 0) != epoch:
            return {
                "outcome": "stale_epoch",
                "reason": "dream_lease_epoch_mismatch",
                "run_id": run_id,
                "epoch": epoch,
                "dream_lease_epoch": dream.get("lease_epoch"),
            }

        current_stage = dream.get("stage")
        try:
            assert_legal_dream_stage_transition(current_stage, stage)
        except Exception as exc:
            return {
                "outcome": "illegal_transition",
                "reason": str(getattr(exc, "reason", None) or exc),
                "from_stage": current_stage,
                "to_stage": stage,
                "run_id": run_id,
            }

        if owner_status is not None:
            try:
                assert_legal_owner_status_transition(
                    dream.get("owner_status"), owner_status
                )
            except Exception as exc:
                return {
                    "outcome": "illegal_transition",
                    "reason": str(getattr(exc, "reason", None) or exc),
                    "from_owner_status": dream.get("owner_status"),
                    "to_owner_status": owner_status,
                    "run_id": run_id,
                }

        created = _run_one(
            tx,
            """
            MATCH (d:Operational:DreamRun {id: $run_id})
            CREATE (s:Operational:DreamStageReceipt)
            SET s.id = $stage_key,
                s.run_id = $run_id,
                s.stage = $stage,
                s.stage_key = $stage_key,
                s.attempt = $attempt,
                s.lease_epoch = $epoch,
                s.input_digest = $input_digest,
                s.output_digest = $output_digest,
                s.error_class = $error_class,
                s.outcome = 'recorded',
                s.request_fingerprint = $fp,
                s.started_at = datetime(),
                s.finished_at = datetime()
            MERGE (d)-[:HAS_STAGE_RECEIPT]->(s)
            SET d.stage = $stage,
                d.attempt = $attempt,
                d.input_digest = coalesce($input_digest, d.input_digest),
                d.output_digest = coalesce($output_digest, d.output_digest),
                d.error_class = coalesce($error_class, d.error_class),
                d.owner_status = coalesce($owner_status, d.owner_status),
                d.finished_at = CASE
                    WHEN $stage IN ['completed', 'failed', 'aborted', 'lease_lost']
                    THEN datetime()
                    ELSE d.finished_at
                END
            RETURN s.id AS stage_key,
                   d.stage AS stage,
                   d.owner_status AS owner_status,
                   d.lease_epoch AS lease_epoch
            """,
            {
                "run_id": run_id,
                "stage_key": stage_key,
                "stage": stage,
                "attempt": attempt,
                "epoch": epoch,
                "input_digest": input_digest,
                "output_digest": output_digest,
                "error_class": error_class,
                "owner_status": owner_status,
                "fp": request_fingerprint,
            },
        )
        if created is None:
            raise RuntimeError("stage receipt create returned no row")
        return {
            "outcome": "recorded",
            "run_id": run_id,
            "stage": created["stage"],
            "stage_key": created["stage_key"],
            "owner_status": created.get("owner_status"),
            "lease_epoch": int(created["lease_epoch"]),
            "request_fingerprint": request_fingerprint,
        }

    # ------------------------------------------------------------------
    # Evidence snapshot / Finding / Proposal
    # ------------------------------------------------------------------

    def create_evidence_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")
        assert_no_absorption_field(payload)

        snapshot_id = _require_id(payload.get("id"), "id")
        dream_id = _require_id(payload.get("dream_id"), "dream_id")
        epoch = _require_int(
            payload.get("epoch") if payload.get("epoch") is not None else payload.get("lease_epoch"),
            "epoch",
            min_value=1,
        )
        run_id = _require_id(payload.get("run_id") or dream_id, "run_id")
        lease_key = _require_id(
            payload.get("lease_key") or DEFAULT_LEASE_KEY, "lease_key"
        )
        cutoff_at = _require_id(payload.get("cutoff_at"), "cutoff_at")
        source_ids_digest = _require_id(
            payload.get("source_ids_digest"), "source_ids_digest"
        )
        source_counts_json = _json_field(
            payload.get("source_counts_json") or payload.get("source_counts"),
            "source_counts_json",
            default="{}",
        )
        redaction_policy_version = _require_id(
            payload.get("redaction_policy_version") or "1",
            "redaction_policy_version",
        )
        sensitivity_max = _require_id(
            payload.get("sensitivity_max") or "public_ops", "sensitivity_max"
        )
        harness_generation_id = _require_id(
            payload.get("harness_generation_id"), "harness_generation_id"
        )
        taxonomy_version = _optional_text(
            payload.get("taxonomy_version") or "1", "taxonomy_version", MAX_REF_LEN
        ) or "1"
        graph_bookmark = _optional_text(
            payload.get("graph_bookmark"), "graph_bookmark", MAX_REF_LEN
        )
        base_commit = _optional_text(payload.get("base_commit"), "base_commit", MAX_REF_LEN)

        memberships = payload.get("memberships") or payload.get("evidence") or []
        if not isinstance(memberships, list):
            raise TypeError("memberships must be an array")
        normalized_memberships: list[dict[str, str]] = []
        for i, item in enumerate(memberships):
            if not isinstance(item, dict):
                raise TypeError(f"memberships[{i}] must be an object")
            assert_no_absorption_field(item)
            normalized_memberships.append(
                {
                    "evidence_id": _require_id(
                        item.get("evidence_id") or item.get("id"),
                        f"memberships[{i}].evidence_id",
                    ),
                    "evidence_label": _require_id(
                        item.get("evidence_label") or item.get("label") or "Feedback",
                        f"memberships[{i}].evidence_label",
                    ),
                    "role": _require_enum(
                        item.get("role") or "generation",
                        EVIDENCE_ROLES,
                        f"memberships[{i}].role",
                    ),
                    "evidence_hash": _require_id(
                        item.get("evidence_hash")
                        or item.get("hash")
                        or _digest_text(
                            str(item.get("evidence_id") or item.get("id"))
                        ),
                        f"memberships[{i}].evidence_hash",
                    ),
                }
            )

        identity = {
            "cutoff_at": cutoff_at,
            "dream_id": dream_id,
            "harness_generation_id": harness_generation_id,
            "id": snapshot_id,
            "lease_epoch": epoch,
            "membership_count": len(normalized_memberships),
            "redaction_policy_version": redaction_policy_version,
            "sensitivity_max": sensitivity_max,
            "source_ids_digest": source_ids_digest,
            "taxonomy_version": taxonomy_version,
        }
        request_fingerprint = _digest_text(_canonical_json(identity))

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._create_snapshot_tx(
                    tx,
                    snapshot_id=snapshot_id,
                    dream_id=dream_id,
                    run_id=run_id,
                    epoch=epoch,
                    lease_key=lease_key,
                    cutoff_at=cutoff_at,
                    source_ids_digest=source_ids_digest,
                    source_counts_json=source_counts_json,
                    redaction_policy_version=redaction_policy_version,
                    sensitivity_max=sensitivity_max,
                    harness_generation_id=harness_generation_id,
                    taxonomy_version=taxonomy_version,
                    graph_bookmark=graph_bookmark,
                    base_commit=base_commit,
                    memberships=normalized_memberships,
                    request_fingerprint=request_fingerprint,
                ),
            )

        return self._with_session(operation)

    def _create_snapshot_tx(
        self,
        tx: Any,
        *,
        snapshot_id: str,
        dream_id: str,
        run_id: str,
        epoch: int,
        lease_key: str,
        cutoff_at: str,
        source_ids_digest: str,
        source_counts_json: str,
        redaction_policy_version: str,
        sensitivity_max: str,
        harness_generation_id: str,
        taxonomy_version: str,
        graph_bookmark: str | None,
        base_commit: str | None,
        memberships: list[dict[str, str]],
        request_fingerprint: str,
    ) -> dict[str, Any]:
        fence_err = self._assert_fence(
            tx, lease_key=lease_key, run_id=run_id, epoch=epoch
        )
        if fence_err is not None:
            return fence_err

        existing = _run_one(
            tx,
            """
            MATCH (s:Operational:EvidenceSnapshot {id: $id})
            RETURN s.id AS id,
                   s.request_fingerprint AS request_fingerprint,
                   s.dream_id AS dream_id
            LIMIT 1
            """,
            {"id": snapshot_id},
        )
        if existing is not None:
            if existing.get("request_fingerprint") == request_fingerprint:
                return {
                    "outcome": "replayed",
                    "snapshot_id": existing["id"],
                    "dream_id": existing.get("dream_id"),
                    "request_fingerprint": existing.get("request_fingerprint"),
                }
            return {
                "outcome": "conflict",
                "reason": "snapshot_id_reused",
                "snapshot_id": existing["id"],
            }

        dream = _run_one(
            tx,
            "MATCH (d:Operational:DreamRun {id: $id}) RETURN d.id AS id LIMIT 1",
            {"id": dream_id},
        )
        if dream is None:
            return {
                "outcome": "not_found",
                "reason": "dream_run_not_found",
                "dream_id": dream_id,
            }

        created = _run_one(
            tx,
            """
            CREATE (s:Operational:EvidenceSnapshot)
            SET s.id = $id,
                s.dream_id = $dream_id,
                s.cutoff_at = $cutoff_at,
                s.source_ids_digest = $source_ids_digest,
                s.source_counts_json = $source_counts_json,
                s.redaction_policy_version = $redaction_policy_version,
                s.sensitivity_max = $sensitivity_max,
                s.harness_generation_id = $harness_generation_id,
                s.taxonomy_version = $taxonomy_version,
                s.graph_bookmark = $graph_bookmark,
                s.base_commit = $base_commit,
                s.lease_epoch = $epoch,
                s.request_fingerprint = $fp,
                s.created_at = datetime()
            WITH s
            MATCH (d:Operational:DreamRun {id: $dream_id})
            MERGE (d)-[:HAS_SNAPSHOT]->(s)
            RETURN s.id AS id, toString(s.created_at) AS created_at
            """,
            {
                "id": snapshot_id,
                "dream_id": dream_id,
                "cutoff_at": cutoff_at,
                "source_ids_digest": source_ids_digest,
                "source_counts_json": source_counts_json,
                "redaction_policy_version": redaction_policy_version,
                "sensitivity_max": sensitivity_max,
                "harness_generation_id": harness_generation_id,
                "taxonomy_version": taxonomy_version,
                "graph_bookmark": graph_bookmark,
                "base_commit": base_commit,
                "epoch": epoch,
                "fp": request_fingerprint,
            },
        )
        if created is None:
            raise RuntimeError("EvidenceSnapshot create returned no row")

        for member in memberships:
            # Soft link: create lightweight EvidenceRef stub if Feedback/RunEvent
            # node is absent so multi-use provenance works without requiring
            # sensors in the same transaction.
            _run_one(
                tx,
                """
                MATCH (s:Operational:EvidenceSnapshot {id: $snapshot_id})
                MERGE (e:Operational:EvidenceRef {id: $evidence_id})
                ON CREATE SET e.evidence_label = $evidence_label
                MERGE (s)-[r:INCLUDES_EVIDENCE]->(e)
                SET r.role = $role,
                    r.evidence_hash = $evidence_hash
                RETURN e.id AS id
                """,
                {
                    "snapshot_id": snapshot_id,
                    "evidence_id": member["evidence_id"],
                    "evidence_label": member["evidence_label"],
                    "role": member["role"],
                    "evidence_hash": member["evidence_hash"],
                },
            )

        return {
            "outcome": "created",
            "snapshot_id": created["id"],
            "dream_id": dream_id,
            "membership_count": len(memberships),
            "request_fingerprint": request_fingerprint,
            "created_at": created.get("created_at"),
            "lease_epoch": epoch,
        }

    def create_finding(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")
        assert_no_absorption_field(payload)

        finding_id = _require_id(payload.get("id"), "id")
        dream_id = _require_id(payload.get("dream_id"), "dream_id")
        snapshot_id = _require_id(payload.get("snapshot_id"), "snapshot_id")
        class_key = _require_id(payload.get("class_key"), "class_key")
        lane = _require_enum(payload.get("lane"), FINDING_LANES, "lane")
        summary = _require_id(payload.get("summary"), "summary")
        if len(summary) > MAX_SUMMARY_LEN:
            raise ValueError(f"summary exceeds max length {MAX_SUMMARY_LEN}")
        evidence_strength = _require_enum(
            payload.get("evidence_strength"), EVIDENCE_STRENGTHS, "evidence_strength"
        )
        support_counts_json = _json_field(
            payload.get("support_counts_json") or payload.get("support_counts"),
            "support_counts_json",
            default="{}",
        )
        counterevidence_json = _json_field(
            payload.get("counterevidence_json") or payload.get("counterevidence"),
            "counterevidence_json",
            default="[]",
        )
        evidence_ids = payload.get("evidence_ids") or []
        if not isinstance(evidence_ids, list):
            raise TypeError("evidence_ids must be an array")
        evidence_ids = [_require_id(eid, f"evidence_ids[{i}]") for i, eid in enumerate(evidence_ids)]

        # Lease fence is mandatory: stale holders after takeover cannot create findings.
        epoch = _require_int(
            payload.get("epoch")
            if payload.get("epoch") is not None
            else payload.get("lease_epoch"),
            "epoch",
            min_value=1,
        )
        run_id = _require_id(payload.get("run_id") or dream_id, "run_id")
        lease_key = _require_id(
            payload.get("lease_key") or DEFAULT_LEASE_KEY, "lease_key"
        )

        identity = {
            "class_key": class_key,
            "dream_id": dream_id,
            "evidence_strength": evidence_strength,
            "id": finding_id,
            "lane": lane,
            "snapshot_id": snapshot_id,
            "summary": summary,
        }
        request_fingerprint = _digest_text(_canonical_json(identity))

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._create_finding_tx(
                    tx,
                    finding_id=finding_id,
                    dream_id=dream_id,
                    snapshot_id=snapshot_id,
                    class_key=class_key,
                    lane=lane,
                    summary=summary,
                    evidence_strength=evidence_strength,
                    support_counts_json=support_counts_json,
                    counterevidence_json=counterevidence_json,
                    evidence_ids=evidence_ids,
                    request_fingerprint=request_fingerprint,
                    epoch=epoch,
                    run_id=run_id,
                    lease_key=lease_key,
                ),
            )

        return self._with_session(operation)

    def _create_finding_tx(
        self,
        tx: Any,
        *,
        finding_id: str,
        dream_id: str,
        snapshot_id: str,
        class_key: str,
        lane: str,
        summary: str,
        evidence_strength: str,
        support_counts_json: str,
        counterevidence_json: str,
        evidence_ids: list[str],
        request_fingerprint: str,
        epoch: int,
        run_id: str,
        lease_key: str,
    ) -> dict[str, Any]:
        fence_err = self._assert_fence(
            tx, lease_key=lease_key, run_id=run_id, epoch=epoch
        )
        if fence_err is not None:
            return fence_err

        existing = _run_one(
            tx,
            """
            MATCH (f:Operational:Finding {id: $id})
            RETURN f.id AS id, f.request_fingerprint AS request_fingerprint
            LIMIT 1
            """,
            {"id": finding_id},
        )
        if existing is not None:
            if existing.get("request_fingerprint") == request_fingerprint:
                return {
                    "outcome": "replayed",
                    "finding_id": existing["id"],
                    "request_fingerprint": existing.get("request_fingerprint"),
                }
            return {
                "outcome": "conflict",
                "reason": "finding_id_reused",
                "finding_id": existing["id"],
            }

        created = _run_one(
            tx,
            """
            CREATE (f:Operational:Finding)
            SET f.id = $id,
                f.dream_id = $dream_id,
                f.snapshot_id = $snapshot_id,
                f.class_key = $class_key,
                f.lane = $lane,
                f.summary = $summary,
                f.evidence_strength = $evidence_strength,
                f.support_counts_json = $support_counts_json,
                f.counterevidence_json = $counterevidence_json,
                f.request_fingerprint = $fp,
                f.created_at = datetime()
            WITH f
            OPTIONAL MATCH (s:Operational:EvidenceSnapshot {id: $snapshot_id})
            FOREACH (_ IN CASE WHEN s IS NULL THEN [] ELSE [1] END |
                MERGE (f)-[:FROM_SNAPSHOT]->(s)
            )
            OPTIONAL MATCH (d:Operational:DreamRun {id: $dream_id})
            FOREACH (_ IN CASE WHEN d IS NULL THEN [] ELSE [1] END |
                MERGE (d)-[:PRODUCED_FINDING]->(f)
            )
            RETURN f.id AS id, toString(f.created_at) AS created_at
            """,
            {
                "id": finding_id,
                "dream_id": dream_id,
                "snapshot_id": snapshot_id,
                "class_key": class_key,
                "lane": lane,
                "summary": summary,
                "evidence_strength": evidence_strength,
                "support_counts_json": support_counts_json,
                "counterevidence_json": counterevidence_json,
                "fp": request_fingerprint,
            },
        )
        if created is None:
            raise RuntimeError("Finding create returned no row")

        for eid in evidence_ids:
            _run_one(
                tx,
                """
                MATCH (f:Operational:Finding {id: $finding_id})
                MERGE (e:Operational:EvidenceRef {id: $evidence_id})
                MERGE (f)-[:USES_EVIDENCE]->(e)
                RETURN e.id AS id
                """,
                {"finding_id": finding_id, "evidence_id": eid},
            )

        return {
            "outcome": "created",
            "finding_id": created["id"],
            "dream_id": dream_id,
            "snapshot_id": snapshot_id,
            "evidence_ids": evidence_ids,
            "request_fingerprint": request_fingerprint,
            "created_at": created.get("created_at"),
        }

    def create_proposal(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")
        assert_no_absorption_field(payload)

        proposal_id = _require_id(payload.get("id"), "id")
        kind = _require_enum(payload.get("kind"), PROPOSAL_KINDS, "kind")
        title = _require_id(payload.get("title"), "title")
        if len(title) > MAX_TITLE_LEN:
            raise ValueError(f"title exceeds max length {MAX_TITLE_LEN}")
        status_projection = _require_enum(
            payload.get("status_projection") or "draft",
            PROPOSAL_STATUS_PROJECTIONS,
            "status_projection",
        )
        evaluation_receipt_id = _optional_text(
            payload.get("evaluation_receipt_id"),
            "evaluation_receipt_id",
            MAX_ID_LEN,
        )
        evaluation_receipt_embed = payload.get("evaluation_receipt") or payload.get(
            "evaluation"
        )
        if evaluation_receipt_embed is not None and not isinstance(
            evaluation_receipt_embed, Mapping
        ):
            raise TypeError("evaluation_receipt must be an object")
        # Advanced statuses require a non-failed EvaluationReceipt at create time.
        if status_projection in STATUSES_REQUIRING_EVALUATION:
            if evaluation_receipt_embed is not None:
                raise EvaluationGateError(
                    "embedded_evaluation_not_allowed_for_advanced_status"
                )
            elif evaluation_receipt_id is None:
                raise EvaluationGateError(
                    f"evaluation_required_for_status:{status_projection}"
                )
        target_ref = _require_id(payload.get("target_ref"), "target_ref")
        scope = _optional_text(payload.get("scope") or "local", "scope", MAX_REF_LEN) or "local"
        risk_tier = _optional_text(
            payload.get("risk_tier") or "low", "risk_tier", MAX_REF_LEN
        ) or "low"
        reversibility = _optional_text(
            payload.get("reversibility") or "reversible",
            "reversibility",
            MAX_REF_LEN,
        ) or "reversible"
        evidence_snapshot_id = _require_id(
            payload.get("evidence_snapshot_id"), "evidence_snapshot_id"
        )
        evidence_strength = _require_enum(
            payload.get("evidence_strength") or "tentative",
            EVIDENCE_STRENGTHS,
            "evidence_strength",
        )
        dream_id = _optional_text(payload.get("dream_id"), "dream_id", MAX_ID_LEN)
        finding_ids = payload.get("finding_ids") or []
        if not isinstance(finding_ids, list):
            raise TypeError("finding_ids must be an array")
        finding_ids = [
            _require_id(fid, f"finding_ids[{i}]") for i, fid in enumerate(finding_ids)
        ]
        evidence_summary_json = _json_field(
            payload.get("evidence_summary_json") or payload.get("evidence_summary"),
            "evidence_summary_json",
            default="{}",
        )
        counterevidence_json = _json_field(
            payload.get("counterevidence_json") or payload.get("counterevidence"),
            "counterevidence_json",
            default="[]",
        )
        sensitivity_max = _optional_text(
            payload.get("sensitivity_max") or "public_ops",
            "sensitivity_max",
            MAX_REF_LEN,
        ) or "public_ops"
        expected_outcome = _optional_text(
            payload.get("expected_outcome"), "expected_outcome", MAX_SUMMARY_LEN
        )
        before_fingerprint = _optional_text(
            payload.get("before_fingerprint"), "before_fingerprint", MAX_REF_LEN
        )
        proposed_effect_hash = _optional_text(
            payload.get("proposed_effect_hash"), "proposed_effect_hash", MAX_REF_LEN
        )
        artifact_ref = _optional_text(
            payload.get("artifact_ref"), "artifact_ref", MAX_REF_LEN
        )

        # Lease fence is mandatory: stale holders after takeover cannot create proposals.
        epoch = _require_int(
            payload.get("epoch")
            if payload.get("epoch") is not None
            else payload.get("lease_epoch"),
            "epoch",
            min_value=1,
        )
        run_id = _require_id(payload.get("run_id") or dream_id, "run_id")
        lease_key = _require_id(
            payload.get("lease_key") or DEFAULT_LEASE_KEY, "lease_key"
        )

        identity = {
            "dream_id": dream_id,
            "evidence_snapshot_id": evidence_snapshot_id,
            "id": proposal_id,
            "kind": kind,
            "target_ref": target_ref,
            "title": title,
        }
        request_fingerprint = _digest_text(_canonical_json(identity))

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._create_proposal_tx(
                    tx,
                    proposal_id=proposal_id,
                    kind=kind,
                    title=title,
                    status_projection=status_projection,
                    target_ref=target_ref,
                    scope=scope,
                    risk_tier=risk_tier,
                    reversibility=reversibility,
                    evidence_snapshot_id=evidence_snapshot_id,
                    evidence_strength=evidence_strength,
                    dream_id=dream_id,
                    finding_ids=finding_ids,
                    evidence_summary_json=evidence_summary_json,
                    counterevidence_json=counterevidence_json,
                    sensitivity_max=sensitivity_max,
                    expected_outcome=expected_outcome,
                    before_fingerprint=before_fingerprint,
                    proposed_effect_hash=proposed_effect_hash,
                    artifact_ref=artifact_ref,
                    request_fingerprint=request_fingerprint,
                    epoch=epoch,
                    run_id=run_id,
                    lease_key=lease_key,
                    evaluation_receipt_id=evaluation_receipt_id,
                    evaluation_receipt_embed=(
                        dict(evaluation_receipt_embed)
                        if evaluation_receipt_embed is not None
                        else None
                    ),
                ),
            )

        return self._with_session(operation)

    def _create_proposal_tx(
        self,
        tx: Any,
        *,
        proposal_id: str,
        kind: str,
        title: str,
        status_projection: str,
        target_ref: str,
        scope: str,
        risk_tier: str,
        reversibility: str,
        evidence_snapshot_id: str,
        evidence_strength: str,
        dream_id: str | None,
        finding_ids: list[str],
        evidence_summary_json: str,
        counterevidence_json: str,
        sensitivity_max: str,
        expected_outcome: str | None,
        before_fingerprint: str | None,
        proposed_effect_hash: str | None,
        artifact_ref: str | None,
        request_fingerprint: str,
        epoch: int,
        run_id: str,
        lease_key: str,
        evaluation_receipt_id: str | None = None,
        evaluation_receipt_embed: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fence_err = self._assert_fence(
            tx, lease_key=lease_key, run_id=run_id, epoch=epoch
        )
        if fence_err is not None:
            return fence_err

        # Re-check evaluation gate inside the transaction against durable receipts.
        if status_projection in STATUSES_REQUIRING_EVALUATION:
            receipt_view = None
            if evaluation_receipt_id:
                receipt_view = _load_evaluation_receipt_for_gate(
                    tx, evaluation_receipt_id=evaluation_receipt_id
                )
            if receipt_view is None and evaluation_receipt_embed is not None:
                receipt_view = {
                    "id": evaluation_receipt_embed.get("id"),
                    "outcome": evaluation_receipt_embed.get("outcome"),
                    "privacy_result": evaluation_receipt_embed.get("privacy_result"),
                    "invariant_result": evaluation_receipt_embed.get(
                        "invariant_result"
                    ),
                    "proposal_id": evaluation_receipt_embed.get("proposal_id")
                    or proposal_id,
                    "evaluator_version": evaluation_receipt_embed.get(
                        "evaluator_version"
                    ),
                    "fixture_snapshot": evaluation_receipt_embed.get(
                        "fixture_snapshot"
                    ),
                    "holdout_ids": evaluation_receipt_embed.get("holdout_ids"),
                    "fixture_digest": evaluation_receipt_embed.get("fixture_digest"),
                }
            if receipt_view is None:
                receipt_view = _load_evaluation_receipt_for_gate(
                    tx, proposal_id=proposal_id
                )
            _assert_evaluation_gate(
                target_status=status_projection,
                evaluation_receipt=receipt_view,
            )
            # If receipt is durable, require it to target this proposal (when set).
            if (
                receipt_view is not None
                and receipt_view.get("proposal_id")
                and str(receipt_view["proposal_id"]) != proposal_id
            ):
                raise EvaluationGateError(
                    f"evaluation_receipt_proposal_mismatch:"
                    f"{receipt_view.get('id')}:{proposal_id}"
                )

        existing = _run_one(
            tx,
            """
            MATCH (p:Operational:Proposal {id: $id})
            RETURN p.id AS id,
                   p.request_fingerprint AS request_fingerprint,
                   p.status_projection AS status_projection
            LIMIT 1
            """,
            {"id": proposal_id},
        )
        if existing is not None:
            if existing.get("request_fingerprint") == request_fingerprint:
                return {
                    "outcome": "replayed",
                    "proposal_id": existing["id"],
                    "status_projection": existing.get("status_projection"),
                    "request_fingerprint": existing.get("request_fingerprint"),
                }
            return {
                "outcome": "conflict",
                "reason": "proposal_id_reused",
                "proposal_id": existing["id"],
            }

        created = _run_one(
            tx,
            """
            CREATE (p:Operational:Proposal)
            SET p.id = $id,
                p.kind = $kind,
                p.title = $title,
                p.status_projection = $status_projection,
                p.target_ref = $target_ref,
                p.scope = $scope,
                p.risk_tier = $risk_tier,
                p.reversibility = $reversibility,
                p.evidence_snapshot_id = $evidence_snapshot_id,
                p.evidence_strength = $evidence_strength,
                p.dream_id = $dream_id,
                p.evidence_summary_json = $evidence_summary_json,
                p.counterevidence_json = $counterevidence_json,
                p.sensitivity_max = $sensitivity_max,
                p.expected_outcome = $expected_outcome,
                p.before_fingerprint = $before_fingerprint,
                p.proposed_effect_hash = $proposed_effect_hash,
                p.artifact_ref = $artifact_ref,
                p.request_fingerprint = $fp,
                p.created_at = datetime()
            WITH p
            OPTIONAL MATCH (s:Operational:EvidenceSnapshot {id: $evidence_snapshot_id})
            FOREACH (_ IN CASE WHEN s IS NULL THEN [] ELSE [1] END |
                MERGE (p)-[:FROM_SNAPSHOT]->(s)
            )
            RETURN p.id AS id,
                   p.status_projection AS status_projection,
                   toString(p.created_at) AS created_at
            """,
            {
                "id": proposal_id,
                "kind": kind,
                "title": title,
                "status_projection": status_projection,
                "target_ref": target_ref,
                "scope": scope,
                "risk_tier": risk_tier,
                "reversibility": reversibility,
                "evidence_snapshot_id": evidence_snapshot_id,
                "evidence_strength": evidence_strength,
                "dream_id": dream_id,
                "evidence_summary_json": evidence_summary_json,
                "counterevidence_json": counterevidence_json,
                "sensitivity_max": sensitivity_max,
                "expected_outcome": expected_outcome,
                "before_fingerprint": before_fingerprint,
                "proposed_effect_hash": proposed_effect_hash,
                "artifact_ref": artifact_ref,
                "fp": request_fingerprint,
            },
        )
        if created is None:
            raise RuntimeError("Proposal create returned no row")

        for fid in finding_ids:
            _run_one(
                tx,
                """
                MATCH (p:Operational:Proposal {id: $proposal_id})
                MERGE (f:Operational:Finding {id: $finding_id})
                ON CREATE SET f.placeholder = true
                MERGE (p)-[:SUPPORTED_BY]->(f)
                RETURN f.id AS id
                """,
                {"proposal_id": proposal_id, "finding_id": fid},
            )

        linked_eval_id: str | None = None
        if status_projection in STATUSES_REQUIRING_EVALUATION:
            linked_eval_id = self._link_or_create_evaluation_for_proposal(
                tx,
                proposal_id=proposal_id,
                evaluation_receipt_id=evaluation_receipt_id,
                evaluation_receipt_embed=evaluation_receipt_embed,
            )

        result = {
            "outcome": "created",
            "proposal_id": created["id"],
            "status_projection": created["status_projection"],
            "finding_ids": finding_ids,
            "request_fingerprint": request_fingerprint,
            "created_at": created.get("created_at"),
        }
        if linked_eval_id is not None:
            result["evaluation_receipt_id"] = linked_eval_id
        return result

    def _link_or_create_evaluation_for_proposal(
        self,
        tx: Any,
        *,
        proposal_id: str,
        evaluation_receipt_id: str | None,
        evaluation_receipt_embed: dict[str, Any] | None,
    ) -> str | None:
        """Ensure a durable EvaluationReceipt is linked via HAS_EVALUATION."""
        receipt_id = evaluation_receipt_id
        if receipt_id is None and evaluation_receipt_embed is not None:
            raw_id = evaluation_receipt_embed.get("id")
            if raw_id:
                receipt_id = _require_id(str(raw_id), "evaluation_receipt.id")

        if receipt_id:
            existing = _load_evaluation_receipt_for_gate(
                tx, evaluation_receipt_id=receipt_id
            )
            if existing is None and evaluation_receipt_embed is not None:
                # Materialize embedded receipt so later transitions stay gated.
                self._create_embedded_evaluation_receipt(
                    tx,
                    proposal_id=proposal_id,
                    receipt=evaluation_receipt_embed,
                    receipt_id=receipt_id,
                )
            elif existing is None:
                raise EvaluationGateError(
                    f"evaluation_receipt_not_found:{receipt_id}"
                )
            _run_one(
                tx,
                """
                MATCH (p:Operational:Proposal {id: $proposal_id})
                MATCH (e:Operational:EvaluationReceipt {id: $evaluation_id})
                MERGE (p)-[:HAS_EVALUATION]->(e)
                RETURN e.id AS id
                """,
                {"proposal_id": proposal_id, "evaluation_id": receipt_id},
            )
            return receipt_id

        if evaluation_receipt_embed is not None:
            receipt_id = _require_id(
                str(
                    evaluation_receipt_embed.get("id")
                    or f"eval-{_digest_text(proposal_id)[:16]}"
                ),
                "evaluation_receipt.id",
            )
            self._create_embedded_evaluation_receipt(
                tx,
                proposal_id=proposal_id,
                receipt=evaluation_receipt_embed,
                receipt_id=receipt_id,
            )
            _run_one(
                tx,
                """
                MATCH (p:Operational:Proposal {id: $proposal_id})
                MATCH (e:Operational:EvaluationReceipt {id: $evaluation_id})
                MERGE (p)-[:HAS_EVALUATION]->(e)
                RETURN e.id AS id
                """,
                {"proposal_id": proposal_id, "evaluation_id": receipt_id},
            )
            return receipt_id
        return None

    def _create_embedded_evaluation_receipt(
        self,
        tx: Any,
        *,
        proposal_id: str,
        receipt: Mapping[str, Any],
        receipt_id: str,
    ) -> None:
        # No silent defaults for hard results — self-attestation must include them.
        if receipt.get("privacy_result") in (None, ""):
            raise EvaluationGateError(
                "evaluation_receipt_missing_hard_results:privacy_result"
            )
        if receipt.get("invariant_result") in (None, ""):
            raise EvaluationGateError(
                "evaluation_receipt_missing_hard_results:invariant_result"
            )
        if receipt.get("evaluator_version") in (None, ""):
            raise EvaluationGateError(
                "evaluation_receipt_missing_evaluator_version"
            )
        outcome = _require_enum(
            receipt.get("outcome"), EVALUATION_OUTCOMES, "evaluation_receipt.outcome"
        )
        privacy_result = _require_id(
            str(receipt.get("privacy_result")),
            "evaluation_receipt.privacy_result",
        )
        invariant_result = _require_id(
            str(receipt.get("invariant_result")),
            "evaluation_receipt.invariant_result",
        )
        evaluator_version = _require_id(
            str(receipt.get("evaluator_version")),
            "evaluation_receipt.evaluator_version",
        )
        baseline_ref = _require_id(
            str(receipt.get("baseline_ref") or "baseline:embedded"),
            "evaluation_receipt.baseline_ref",
        )
        candidate_ref = _require_id(
            str(receipt.get("candidate_ref") or f"candidate:{proposal_id}"),
            "evaluation_receipt.candidate_ref",
        )
        # Holdout proof required: non-empty fixture_snapshot with holdout_ids or
        # explicit holdout_ids / fixture_digest on the embed.
        fixture_snapshot = _json_field(
            receipt.get("fixture_snapshot") or "{}",
            "evaluation_receipt.fixture_snapshot",
            default="{}",
        )
        holdout_ok = False
        try:
            parsed_fs = json.loads(fixture_snapshot) if fixture_snapshot else {}
            if isinstance(parsed_fs, dict):
                ids = parsed_fs.get("holdout_ids") or parsed_fs.get("ids")
                if isinstance(ids, list) and len(ids) > 0:
                    holdout_ok = True
        except (TypeError, json.JSONDecodeError):
            parsed_fs = {}
        if not holdout_ok:
            raw_h = receipt.get("holdout_ids")
            if isinstance(raw_h, (list, tuple)) and len(raw_h) > 0:
                holdout_ok = True
                fixture_snapshot = _json_field(
                    {
                        "holdout_ids": [str(x) for x in raw_h],
                        "evaluator_version": evaluator_version,
                    },
                    "evaluation_receipt.fixture_snapshot",
                    default="{}",
                )
            elif str(receipt.get("fixture_digest") or "").strip():
                holdout_ok = True
        if not holdout_ok:
            raise EvaluationGateError("evaluation_receipt_missing_holdout_proof")
        target_results = _json_field(
            receipt.get("target_results") or "{}",
            "evaluation_receipt.target_results",
            default="{}",
        )
        guardrail_results = _json_field(
            receipt.get("guardrail_results") or "{}",
            "evaluation_receipt.guardrail_results",
            default="{}",
        )
        identity = {
            "baseline_ref": baseline_ref,
            "candidate_ref": candidate_ref,
            "evaluator_version": evaluator_version,
            "id": receipt_id,
            "outcome": outcome,
            "proposal_id": proposal_id,
        }
        fp = _digest_text(_canonical_json(identity))
        created = _run_one(
            tx,
            """
            CREATE (e:Operational:EvaluationReceipt)
            SET e.id = $id,
                e.proposal_id = $proposal_id,
                e.evaluator_version = $evaluator_version,
                e.baseline_ref = $baseline_ref,
                e.candidate_ref = $candidate_ref,
                e.fixture_snapshot = $fixture_snapshot,
                e.target_results = $target_results,
                e.guardrail_results = $guardrail_results,
                e.privacy_result = $privacy_result,
                e.invariant_result = $invariant_result,
                e.outcome = $outcome,
                e.request_fingerprint = $fp,
                e.created_at = datetime()
            RETURN e.id AS id
            """,
            {
                "id": receipt_id,
                "proposal_id": proposal_id,
                "evaluator_version": evaluator_version,
                "baseline_ref": baseline_ref,
                "candidate_ref": candidate_ref,
                "fixture_snapshot": fixture_snapshot,
                "target_results": target_results,
                "guardrail_results": guardrail_results,
                "privacy_result": privacy_result,
                "invariant_result": invariant_result,
                "outcome": outcome,
                "fp": fp,
            },
        )
        if created is None:
            raise RuntimeError("embedded EvaluationReceipt create returned no row")

    # ------------------------------------------------------------------
    # Evaluation + Decision (separate records)
    # ------------------------------------------------------------------

    def record_evaluation(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")
        assert_no_absorption_field(payload)

        evaluation_id = _require_id(payload.get("id"), "id")
        proposal_id = _require_id(payload.get("proposal_id"), "proposal_id")
        evaluator_version = _require_id(
            payload.get("evaluator_version"), "evaluator_version"
        )
        baseline_ref = _require_id(payload.get("baseline_ref"), "baseline_ref")
        candidate_ref = _require_id(payload.get("candidate_ref"), "candidate_ref")
        if payload.get("privacy_result") in (None, ""):
            raise EvaluationGateError(
                "evaluation_receipt_missing_hard_results:privacy_result"
            )
        if payload.get("invariant_result") in (None, ""):
            raise EvaluationGateError(
                "evaluation_receipt_missing_hard_results:invariant_result"
            )
        fixture_snapshot = _json_field(
            payload.get("fixture_snapshot") or "{}",
            "fixture_snapshot",
            default="{}",
        )
        holdout_ids: list[str] = []
        try:
            parsed_fs = json.loads(fixture_snapshot) if fixture_snapshot else {}
            if isinstance(parsed_fs, dict):
                ids = parsed_fs.get("holdout_ids") or parsed_fs.get("ids")
                if isinstance(ids, list):
                    holdout_ids = [
                        str(item).strip() for item in ids if str(item).strip()
                    ]
        except (TypeError, json.JSONDecodeError):
            pass
        if not holdout_ids:
            raise EvaluationGateError("evaluation_receipt_missing_holdout_proof")
        target_results = _json_field(
            payload.get("target_results") or "{}", "target_results", default="{}"
        )
        guardrail_results = _json_field(
            payload.get("guardrail_results") or "{}",
            "guardrail_results",
            default="{}",
        )
        privacy_result = _require_id(
            str(payload.get("privacy_result")), "privacy_result"
        )
        invariant_result = _require_id(
            str(payload.get("invariant_result")), "invariant_result"
        )
        outcome = _require_enum(payload.get("outcome"), EVALUATION_OUTCOMES, "outcome")
        # Self-attested "passed" with hard failures is inconsistent — force failed.
        if outcome == "passed" and (
            privacy_result in {"failed", "fail"}
            or invariant_result in {"failed", "fail"}
        ):
            outcome = "failed"

        epoch = _require_int(
            payload.get("epoch")
            if payload.get("epoch") is not None
            else payload.get("lease_epoch"),
            "epoch",
            min_value=1,
        )
        run_id = _require_id(payload.get("run_id"), "run_id")
        lease_key = _require_id(
            payload.get("lease_key") or DEFAULT_LEASE_KEY, "lease_key"
        )

        identity = {
            "baseline_ref": baseline_ref,
            "candidate_ref": candidate_ref,
            "evaluator_version": evaluator_version,
            "fixture_snapshot": fixture_snapshot,
            "guardrail_results": guardrail_results,
            "id": evaluation_id,
            "invariant_result": invariant_result,
            "outcome": outcome,
            "privacy_result": privacy_result,
            "proposal_id": proposal_id,
            "target_results": target_results,
        }
        request_fingerprint = _digest_text(_canonical_json(identity))

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._record_evaluation_tx(
                    tx,
                    evaluation_id=evaluation_id,
                    proposal_id=proposal_id,
                    evaluator_version=evaluator_version,
                    baseline_ref=baseline_ref,
                    candidate_ref=candidate_ref,
                    fixture_snapshot=fixture_snapshot,
                    target_results=target_results,
                    guardrail_results=guardrail_results,
                    privacy_result=privacy_result,
                    invariant_result=invariant_result,
                    outcome=outcome,
                    holdout_ids=holdout_ids,
                    request_fingerprint=request_fingerprint,
                    epoch=epoch,
                    run_id=run_id,
                    lease_key=lease_key,
                ),
            )

        return self._with_session(operation)

    def _record_evaluation_tx(
        self,
        tx: Any,
        *,
        evaluation_id: str,
        proposal_id: str,
        evaluator_version: str,
        baseline_ref: str,
        candidate_ref: str,
        fixture_snapshot: str,
        target_results: str,
        guardrail_results: str,
        privacy_result: str,
        invariant_result: str,
        outcome: str,
        holdout_ids: list[str],
        request_fingerprint: str,
        epoch: int,
        run_id: str,
        lease_key: str,
    ) -> dict[str, Any]:
        fence_err = self._assert_fence(
            tx, lease_key=lease_key, run_id=run_id, epoch=epoch
        )
        if fence_err is not None:
            return fence_err

        provenance = _run_one(
            tx,
            """
            MATCH (p:Operational:Proposal {id: $proposal_id})
            OPTIONAL MATCH (s:Operational:EvidenceSnapshot {id: p.evidence_snapshot_id})
            OPTIONAL MATCH (s)-[r:INCLUDES_EVIDENCE]->(e:Operational:EvidenceRef)
            RETURN p.id AS proposal_id,
                   p.evidence_snapshot_id AS snapshot_id,
                   collect(CASE WHEN r.role = 'holdout' THEN e.id ELSE null END)
                       AS durable_holdout_ids,
                   collect(CASE WHEN r.role = 'generation' THEN e.id ELSE null END)
                       AS durable_generation_ids
            """,
            {"proposal_id": proposal_id},
        )
        if provenance is None:
            raise EvaluationGateError("evaluation_proposal_not_found")
        durable_holdout = {
            str(item)
            for item in (provenance.get("durable_holdout_ids") or [])
            if item is not None and str(item)
        }
        durable_generation = {
            str(item)
            for item in (provenance.get("durable_generation_ids") or [])
            if item is not None and str(item)
        }
        supplied_holdout = set(holdout_ids)
        if not durable_holdout:
            raise EvaluationGateError("evaluation_snapshot_has_no_holdout")
        if supplied_holdout != durable_holdout:
            raise EvaluationGateError("evaluation_holdout_snapshot_mismatch")
        if supplied_holdout & durable_generation:
            raise EvaluationGateError("evaluation_holdout_generation_overlap")

        existing = _run_one(
            tx,
            """
            MATCH (e:Operational:EvaluationReceipt {id: $id})
            RETURN e.id AS id,
                   e.request_fingerprint AS request_fingerprint,
                   e.outcome AS outcome
            LIMIT 1
            """,
            {"id": evaluation_id},
        )
        if existing is not None:
            if existing.get("request_fingerprint") == request_fingerprint:
                return {
                    "outcome": "replayed",
                    "evaluation_id": existing["id"],
                    "evaluation_outcome": existing.get("outcome"),
                    "request_fingerprint": existing.get("request_fingerprint"),
                }
            return {
                "outcome": "conflict",
                "reason": "evaluation_id_reused",
                "evaluation_id": existing["id"],
            }

        created = _run_one(
            tx,
            """
            CREATE (e:Operational:EvaluationReceipt)
            SET e.id = $id,
                e.proposal_id = $proposal_id,
                e.evaluator_version = $evaluator_version,
                e.baseline_ref = $baseline_ref,
                e.candidate_ref = $candidate_ref,
                e.fixture_snapshot = $fixture_snapshot,
                e.target_results = $target_results,
                e.guardrail_results = $guardrail_results,
                e.privacy_result = $privacy_result,
                e.invariant_result = $invariant_result,
                e.outcome = $outcome,
                e.request_fingerprint = $fp,
                e.created_at = datetime()
            WITH e
            OPTIONAL MATCH (p:Operational:Proposal {id: $proposal_id})
            FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END |
                MERGE (p)-[:HAS_EVALUATION]->(e)
                SET p.status_projection = CASE
                    WHEN $outcome = 'passed'
                         AND $privacy_result = 'passed'
                         AND $invariant_result = 'passed'
                         AND p.status_projection = 'draft'
                    THEN 'validated'
                    WHEN $outcome = 'failed'
                         OR $privacy_result = 'failed'
                         OR $invariant_result = 'failed'
                    THEN 'invalid'
                    ELSE p.status_projection
                END
            )
            RETURN e.id AS id, e.outcome AS outcome, toString(e.created_at) AS created_at
            """,
            {
                "id": evaluation_id,
                "proposal_id": proposal_id,
                "evaluator_version": evaluator_version,
                "baseline_ref": baseline_ref,
                "candidate_ref": candidate_ref,
                "fixture_snapshot": fixture_snapshot,
                "target_results": target_results,
                "guardrail_results": guardrail_results,
                "privacy_result": privacy_result,
                "invariant_result": invariant_result,
                "outcome": outcome,
                "fp": request_fingerprint,
            },
        )
        if created is None:
            raise RuntimeError("EvaluationReceipt create returned no row")
        return {
            "outcome": "created",
            "evaluation_id": created["id"],
            "proposal_id": proposal_id,
            "evaluation_outcome": created["outcome"],
            "request_fingerprint": request_fingerprint,
            "created_at": created.get("created_at"),
        }

    def record_decision(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")
        assert_no_absorption_field(payload)

        decision_id = _require_id(payload.get("id"), "id")
        proposal_id = _require_id(payload.get("proposal_id"), "proposal_id")
        decision = _require_enum(payload.get("decision"), DECISION_VALUES, "decision")
        proposal_hash = _require_id(payload.get("proposal_hash"), "proposal_hash")
        target_ref = _require_id(payload.get("target_ref"), "target_ref")
        before_fingerprint = _require_id(
            payload.get("before_fingerprint"), "before_fingerprint"
        )
        artifact_or_effect_hash = _require_id(
            payload.get("artifact_or_effect_hash"), "artifact_or_effect_hash"
        )
        decided_by = _require_id(payload.get("decided_by"), "decided_by")
        reason_code = _optional_text(payload.get("reason_code"), "reason_code", MAX_REF_LEN)
        expires_at = _optional_text(payload.get("expires_at"), "expires_at", MAX_REF_LEN)

        epoch = _require_int(
            payload.get("epoch")
            if payload.get("epoch") is not None
            else payload.get("lease_epoch"),
            "epoch",
            min_value=1,
        )
        run_id = _require_id(payload.get("run_id"), "run_id")
        lease_key = _require_id(
            payload.get("lease_key") or DEFAULT_LEASE_KEY, "lease_key"
        )

        identity = {
            "artifact_or_effect_hash": artifact_or_effect_hash,
            "before_fingerprint": before_fingerprint,
            "decision": decision,
            "id": decision_id,
            "proposal_hash": proposal_hash,
            "proposal_id": proposal_id,
            "target_ref": target_ref,
        }
        request_fingerprint = _digest_text(_canonical_json(identity))

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._record_decision_tx(
                    tx,
                    decision_id=decision_id,
                    proposal_id=proposal_id,
                    decision=decision,
                    proposal_hash=proposal_hash,
                    target_ref=target_ref,
                    before_fingerprint=before_fingerprint,
                    artifact_or_effect_hash=artifact_or_effect_hash,
                    decided_by=decided_by,
                    reason_code=reason_code,
                    expires_at=expires_at,
                    request_fingerprint=request_fingerprint,
                    epoch=epoch,
                    run_id=run_id,
                    lease_key=lease_key,
                ),
            )

        return self._with_session(operation)

    def _record_decision_tx(
        self,
        tx: Any,
        *,
        decision_id: str,
        proposal_id: str,
        decision: str,
        proposal_hash: str,
        target_ref: str,
        before_fingerprint: str,
        artifact_or_effect_hash: str,
        decided_by: str,
        reason_code: str | None,
        expires_at: str | None,
        request_fingerprint: str,
        epoch: int,
        run_id: str,
        lease_key: str,
    ) -> dict[str, Any]:
        fence_err = self._assert_fence(
            tx, lease_key=lease_key, run_id=run_id, epoch=epoch
        )
        if fence_err is not None:
            return fence_err

        existing = _run_one(
            tx,
            """
            MATCH (d:Operational:Decision {id: $id})
            RETURN d.id AS id,
                   d.request_fingerprint AS request_fingerprint,
                   d.decision AS decision
            LIMIT 1
            """,
            {"id": decision_id},
        )
        if existing is not None:
            if existing.get("request_fingerprint") == request_fingerprint:
                return {
                    "outcome": "replayed",
                    "decision_id": existing["id"],
                    "decision": existing.get("decision"),
                    "request_fingerprint": existing.get("request_fingerprint"),
                }
            return {
                "outcome": "conflict",
                "reason": "decision_id_reused",
                "decision_id": existing["id"],
            }

        status_map = {
            "approved": "approved",
            "rejected": "rejected",
            "deferred": "review_pending",
            "withdrawn": "withdrawn",
        }
        projection = status_map[decision]

        # Approval / defer-to-review cannot skip evaluation.
        if projection in STATUSES_REQUIRING_EVALUATION:
            receipt_view = _load_evaluation_receipt_for_gate(
                tx, proposal_id=proposal_id
            )
            _assert_evaluation_gate(
                target_status=projection,
                evaluation_receipt=receipt_view,
            )

        created = _run_one(
            tx,
            """
            CREATE (d:Operational:Decision)
            SET d.id = $id,
                d.proposal_id = $proposal_id,
                d.decision = $decision,
                d.proposal_hash = $proposal_hash,
                d.target_ref = $target_ref,
                d.before_fingerprint = $before_fingerprint,
                d.artifact_or_effect_hash = $artifact_or_effect_hash,
                d.decided_by = $decided_by,
                d.reason_code = $reason_code,
                d.expires_at = $expires_at,
                d.request_fingerprint = $fp,
                d.decided_at = datetime()
            WITH d
            OPTIONAL MATCH (p:Operational:Proposal {id: $proposal_id})
            FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END |
                MERGE (p)-[:HAS_DECISION]->(d)
                SET p.status_projection = $projection
            )
            RETURN d.id AS id, d.decision AS decision, toString(d.decided_at) AS decided_at
            """,
            {
                "id": decision_id,
                "proposal_id": proposal_id,
                "decision": decision,
                "proposal_hash": proposal_hash,
                "target_ref": target_ref,
                "before_fingerprint": before_fingerprint,
                "artifact_or_effect_hash": artifact_or_effect_hash,
                "decided_by": decided_by,
                "reason_code": reason_code,
                "expires_at": expires_at,
                "fp": request_fingerprint,
                "projection": projection,
            },
        )
        if created is None:
            raise RuntimeError("Decision create returned no row")
        return {
            "outcome": "created",
            "decision_id": created["id"],
            "proposal_id": proposal_id,
            "decision": created["decision"],
            "request_fingerprint": request_fingerprint,
            "decided_at": created.get("decided_at"),
            # Explicitly not an EffectReceipt / Deployment — application is separate.
            "application_status": "not_applied",
        }

    # ------------------------------------------------------------------
    # Retention effect (thin fenced receipt; full policy is Task 8)
    # ------------------------------------------------------------------

    def record_retention_effect(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Fenced retention EffectReceipt; may redact QualityPayload when action set.

        Thin path (no ``action`` / ``feedback_id``): durable receipt only —
        backward compatible with early fence tests.

        Full path (``action`` + ``feedback_id`` + ``config_digest``): policy-bound
        removal of removable ``QualityPayload`` raw text, lifecycle append, and
        verified EffectReceipt in one fenced transaction. Automatic apply is
        denied unless ``auto_apply_enabled`` is true; owner-initiated apply
        requires ``owner_initiated``.
        """
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")
        assert_no_absorption_field(payload)

        effect_id = _require_id(payload.get("id"), "id")
        effect_key = _require_id(
            payload.get("effect_key") or effect_id, "effect_key"
        )
        run_id = _require_id(payload.get("run_id"), "run_id")
        epoch = _require_int(
            payload.get("epoch")
            if payload.get("epoch") is not None
            else payload.get("lease_epoch"),
            "epoch",
            min_value=1,
        )
        lease_key = _require_id(
            payload.get("lease_key") or DEFAULT_LEASE_KEY, "lease_key"
        )
        actor = _require_id(payload.get("actor") or "maintenance", "actor")
        proposal_id = _optional_text(payload.get("proposal_id"), "proposal_id", MAX_ID_LEN)
        dream_id = _optional_text(
            payload.get("dream_id") or run_id, "dream_id", MAX_ID_LEN
        )
        target_ref = _optional_text(payload.get("target_ref"), "target_ref", MAX_REF_LEN)

        action_raw = payload.get("action")
        feedback_id = payload.get("feedback_id")
        config_digest = payload.get("config_digest")
        dry_run = bool(payload.get("dry_run", False))
        full_path = action_raw is not None or feedback_id is not None

        if full_path:
            action = _require_enum(
                action_raw,
                frozenset({"redact", "archive", "purge"}),
                "action",
            )
            feedback_id = _require_id(feedback_id, "feedback_id")
            config_digest = _require_id(config_digest, "config_digest")
            effect_type = _require_id(
                payload.get("effect_type")
                or {
                    "redact": "retention_redact",
                    "archive": "retention_archive",
                    "purge": "retention_purge",
                }[action],
                "effect_type",
            )
            automatic = bool(payload.get("automatic", False))
            owner_initiated = bool(payload.get("owner_initiated", False))
            auto_apply_enabled = bool(payload.get("auto_apply_enabled", False))
            if dry_run:
                # Fence still required so dry-run cannot be used to probe without lease.
                def dry_operation(session: Any) -> dict[str, Any]:
                    return _execute_write(
                        session,
                        lambda tx: self._retention_dry_run_tx(
                            tx,
                            lease_key=lease_key,
                            run_id=run_id,
                            epoch=epoch,
                            feedback_id=feedback_id,
                            action=action,
                            config_digest=config_digest,
                        ),
                    )

                return self._with_session(dry_operation)

            if automatic and not auto_apply_enabled:
                return {
                    "outcome": "denied",
                    "reason": "retention_auto_apply_disabled",
                    "feedback_id": feedback_id,
                    "action": action,
                    "config_digest": config_digest,
                }
            if not automatic and not owner_initiated:
                return {
                    "outcome": "denied",
                    "reason": "retention_apply_requires_owner_or_auto",
                    "feedback_id": feedback_id,
                    "action": action,
                }

            before_ref = _require_id(
                payload.get("before_ref") or f"Feedback:{feedback_id}:payload",
                "before_ref",
            )
            after_default = {
                "redact": f"Feedback:{feedback_id}:redacted",
                "archive": f"Feedback:{feedback_id}:archived",
                "purge": f"Feedback:{feedback_id}:purged",
            }[action]
            after_ref = _optional_text(
                payload.get("after_ref") or after_default, "after_ref", MAX_REF_LEN
            )
            target_ref = target_ref or f"Feedback:{feedback_id}"
            verification_status = _require_id(
                payload.get("verification_status") or "verified_absent",
                "verification_status",
            )
            effect_outcome = _require_id(
                payload.get("effect_outcome") or "applied", "effect_outcome"
            )
            identity = {
                "action": action,
                "before_ref": before_ref,
                "config_digest": config_digest,
                "effect_key": effect_key,
                "effect_type": effect_type,
                "feedback_id": feedback_id,
                "id": effect_id,
                "run_id": run_id,
                "target_ref": target_ref,
            }
            request_fingerprint = _digest_text(_canonical_json(identity))
            request_hash = _require_id(
                payload.get("request_hash") or request_fingerprint, "request_hash"
            )
            lifecycle_id = _optional_text(
                payload.get("lifecycle_id") or f"fle-ret-{effect_id}",
                "lifecycle_id",
                MAX_ID_LEN,
            )

            def full_operation(session: Any) -> dict[str, Any]:
                return _execute_write(
                    session,
                    lambda tx: self._apply_retention_effect_tx(
                        tx,
                        effect_id=effect_id,
                        effect_key=effect_key,
                        run_id=run_id,
                        epoch=epoch,
                        lease_key=lease_key,
                        effect_type=effect_type,
                        actor=actor,
                        before_ref=before_ref,
                        after_ref=after_ref,
                        proposal_id=proposal_id,
                        dream_id=dream_id,
                        target_ref=target_ref,
                        verification_status=verification_status,
                        effect_outcome=effect_outcome,
                        request_hash=request_hash,
                        request_fingerprint=request_fingerprint,
                        feedback_id=feedback_id,
                        action=action,
                        config_digest=config_digest,
                        lifecycle_id=lifecycle_id,
                    ),
                )

            return self._with_session(full_operation)

        # Thin receipt-only path (Task 5 fence tests).
        effect_type = _require_id(
            payload.get("effect_type") or "retention", "effect_type"
        )
        before_ref = _require_id(
            payload.get("before_ref") or "retention:none", "before_ref"
        )
        after_ref = _optional_text(payload.get("after_ref"), "after_ref", MAX_REF_LEN)
        verification_status = _require_id(
            payload.get("verification_status") or "recorded", "verification_status"
        )
        effect_outcome = _require_id(
            payload.get("effect_outcome") or "applied", "effect_outcome"
        )

        identity = {
            "before_ref": before_ref,
            "effect_key": effect_key,
            "effect_type": effect_type,
            "id": effect_id,
            "run_id": run_id,
            "target_ref": target_ref,
        }
        request_fingerprint = _digest_text(_canonical_json(identity))
        request_hash = _require_id(
            payload.get("request_hash") or request_fingerprint, "request_hash"
        )

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._record_retention_effect_tx(
                    tx,
                    effect_id=effect_id,
                    effect_key=effect_key,
                    run_id=run_id,
                    epoch=epoch,
                    lease_key=lease_key,
                    effect_type=effect_type,
                    actor=actor,
                    before_ref=before_ref,
                    after_ref=after_ref,
                    proposal_id=proposal_id,
                    dream_id=dream_id,
                    target_ref=target_ref,
                    verification_status=verification_status,
                    effect_outcome=effect_outcome,
                    request_hash=request_hash,
                    request_fingerprint=request_fingerprint,
                ),
            )

        return self._with_session(operation)

    def _retention_dry_run_tx(
        self,
        tx: Any,
        *,
        lease_key: str,
        run_id: str,
        epoch: int,
        feedback_id: str,
        action: str,
        config_digest: str,
    ) -> dict[str, Any]:
        fence_err = self._assert_fence(
            tx, lease_key=lease_key, run_id=run_id, epoch=epoch
        )
        if fence_err is not None:
            return fence_err
        fb = _run_one(
            tx,
            """
            MATCH (f:Operational:Feedback {id: $feedback_id})
            RETURN f.id AS id,
                   f.raw_payload_ref AS raw_payload_ref,
                   f.request_fingerprint AS request_fingerprint
            LIMIT 1
            """,
            {"feedback_id": feedback_id},
        )
        if fb is None:
            return {
                "outcome": "dry_run",
                "would_apply": False,
                "reason": "feedback_missing",
                "feedback_id": feedback_id,
                "action": action,
                "config_digest": config_digest,
                "counts": {"selected": 0},
            }
        ref = fb.get("raw_payload_ref")
        has_payload = False
        if ref:
            prow = _run_one(
                tx,
                """
                MATCH (p:Operational:QualityPayload {id: $payload_id})
                RETURN p.id AS id, p.payload_text AS payload_text
                LIMIT 1
                """,
                {"payload_id": ref},
            )
            has_payload = bool(
                prow and str(prow.get("payload_text") or "").strip()
            )
        return {
            "outcome": "dry_run",
            "would_apply": has_payload,
            "feedback_id": feedback_id,
            "action": action,
            "config_digest": config_digest,
            "request_fingerprint": fb.get("request_fingerprint"),
            "counts": {"selected": 1 if has_payload else 0},
        }

    def _apply_retention_effect_tx(
        self,
        tx: Any,
        *,
        effect_id: str,
        effect_key: str,
        run_id: str,
        epoch: int,
        lease_key: str,
        effect_type: str,
        actor: str,
        before_ref: str,
        after_ref: str | None,
        proposal_id: str | None,
        dream_id: str | None,
        target_ref: str | None,
        verification_status: str,
        effect_outcome: str,
        request_hash: str,
        request_fingerprint: str,
        feedback_id: str,
        action: str,
        config_digest: str,
        lifecycle_id: str | None,
    ) -> dict[str, Any]:
        fence_err = self._assert_fence(
            tx, lease_key=lease_key, run_id=run_id, epoch=epoch
        )
        if fence_err is not None:
            return fence_err

        # Receipt idempotency (same as thin path).
        existing = _run_one(
            tx,
            """
            MATCH (r:Operational:EffectReceipt {id: $id})
            RETURN r.id AS id,
                   r.effect_key AS effect_key,
                   r.request_fingerprint AS request_fingerprint,
                   r.request_hash AS request_hash,
                   r.outcome AS outcome,
                   r.verification_status AS verification_status,
                   r.fence_epoch AS fence_epoch
            LIMIT 1
            """,
            {"id": effect_id},
        )
        if existing is not None:
            if existing.get("request_fingerprint") == request_fingerprint:
                return {
                    "outcome": "replayed",
                    "effect_id": existing["id"],
                    "effect_key": existing.get("effect_key"),
                    "effect_outcome": existing.get("outcome"),
                    "request_fingerprint": existing.get("request_fingerprint"),
                    "fence_epoch": existing.get("fence_epoch"),
                    "feedback_id": feedback_id,
                    "action": action,
                }
            return {
                "outcome": "conflict",
                "reason": "effect_id_reused",
                "effect_id": existing["id"],
            }

        by_key = _run_one(
            tx,
            """
            MATCH (r:Operational:EffectReceipt {effect_key: $effect_key})
            RETURN r.id AS id,
                   r.request_fingerprint AS request_fingerprint,
                   r.outcome AS outcome,
                   r.effect_key AS effect_key,
                   r.fence_epoch AS fence_epoch
            LIMIT 1
            """,
            {"effect_key": effect_key},
        )
        if by_key is not None:
            if by_key.get("request_fingerprint") == request_fingerprint:
                return {
                    "outcome": "replayed",
                    "effect_id": by_key["id"],
                    "effect_key": by_key.get("effect_key"),
                    "effect_outcome": by_key.get("outcome"),
                    "request_fingerprint": by_key.get("request_fingerprint"),
                    "fence_epoch": by_key.get("fence_epoch"),
                    "feedback_id": feedback_id,
                    "action": action,
                }
            return {
                "outcome": "conflict",
                "reason": "effect_key_reused",
                "effect_id": by_key["id"],
                "effect_key": effect_key,
            }

        fb = _run_one(
            tx,
            """
            MATCH (f:Operational:Feedback {id: $feedback_id})
            RETURN f.id AS id,
                   f.request_fingerprint AS request_fingerprint,
                   f.raw_payload_ref AS raw_payload_ref
            LIMIT 1
            """,
            {"feedback_id": feedback_id},
        )
        if fb is None:
            return {
                "outcome": "not_found",
                "reason": "feedback_missing",
                "feedback_id": feedback_id,
                "effect_id": effect_id,
            }

        fp_before = fb.get("request_fingerprint")
        payload_ref = fb.get("raw_payload_ref")
        if payload_ref:
            _run_one(
                tx,
                """
                OPTIONAL MATCH (f:Operational:Feedback {id: $feedback_id})
                      -[rel:HAS_RAW_PAYLOAD]->(p:Operational:QualityPayload {id: $payload_id})
                FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END |
                    DELETE rel
                )
                WITH p
                FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END |
                    DETACH DELETE p
                )
                RETURN $payload_id AS deleted_id
                """,
                {"feedback_id": feedback_id, "payload_id": payload_ref},
            )
            # Fallback simple delete if OPTIONAL MATCH path not executed by fake.
            _run_one(
                tx,
                """
                MATCH (p:Operational:QualityPayload {id: $payload_id})
                DETACH DELETE p
                RETURN $payload_id AS deleted_id
                """,
                {"payload_id": payload_ref},
            )

        still = None
        if payload_ref:
            still = _run_one(
                tx,
                """
                MATCH (p:Operational:QualityPayload {id: $payload_id})
                RETURN p.id AS id, p.payload_text AS payload_text
                LIMIT 1
                """,
                {"payload_id": payload_ref},
            )
        verified = still is None or not str(
            (still or {}).get("payload_text") or ""
        ).strip()
        if not verified:
            return {
                "outcome": "failed",
                "reason": "payload_still_present",
                "feedback_id": feedback_id,
                "effect_id": effect_id,
            }

        fb_after = _run_one(
            tx,
            """
            MATCH (f:Operational:Feedback {id: $feedback_id})
            RETURN f.request_fingerprint AS request_fingerprint
            LIMIT 1
            """,
            {"feedback_id": feedback_id},
        )
        if fb_after is not None and fb_after.get("request_fingerprint") != fp_before:
            return {
                "outcome": "failed",
                "reason": "feedback_fingerprint_changed",
                "feedback_id": feedback_id,
            }

        lifecycle_event = {
            "redact": "redacted",
            "archive": "archived",
            "purge": "purged",
        }[action]
        lid = lifecycle_id or f"fle-ret-{effect_id}"
        _run_one(
            tx,
            """
            MATCH (f:Operational:Feedback {id: $feedback_id})
            MERGE (l:Operational:FeedbackLifecycleEvent {id: $id})
            ON CREATE SET
                l.feedback_id = $feedback_id,
                l.event = $event,
                l.actor = $actor,
                l.reason_code = $reason_code,
                l.config_digest = $config_digest,
                l.created_at = datetime()
            MERGE (f)-[:HAS_LIFECYCLE_EVENT]->(l)
            RETURN l.id AS id
            """,
            {
                "id": lid,
                "feedback_id": feedback_id,
                "event": lifecycle_event,
                "actor": actor,
                "reason_code": f"retention_{action}",
                "config_digest": config_digest,
            },
        )

        created = _run_one(
            tx,
            """
            CREATE (r:Operational:EffectReceipt)
            SET r.id = $id,
                r.effect_key = $effect_key,
                r.request_hash = $request_hash,
                r.request_fingerprint = $fp,
                r.proposal_id = $proposal_id,
                r.dream_id = $dream_id,
                r.effect_type = $effect_type,
                r.actor = $actor,
                r.before_ref = $before_ref,
                r.after_ref = $after_ref,
                r.target_ref = $target_ref,
                r.outcome = $effect_outcome,
                r.verification_status = $verification_status,
                r.config_digest = $config_digest,
                r.action = $action,
                r.feedback_id = $feedback_id,
                r.fence_epoch = $epoch,
                r.run_id = $run_id,
                r.applied_at = datetime(),
                r.created_at = datetime()
            RETURN r.id AS id,
                   r.effect_key AS effect_key,
                   r.outcome AS outcome,
                   r.fence_epoch AS fence_epoch,
                   r.verification_status AS verification_status,
                   toString(r.applied_at) AS applied_at
            """,
            {
                "id": effect_id,
                "effect_key": effect_key,
                "request_hash": request_hash,
                "fp": request_fingerprint,
                "proposal_id": proposal_id,
                "dream_id": dream_id,
                "effect_type": effect_type,
                "actor": actor,
                "before_ref": before_ref,
                "after_ref": after_ref,
                "target_ref": target_ref,
                "effect_outcome": effect_outcome,
                "verification_status": "verified_absent",
                "config_digest": config_digest,
                "action": action,
                "feedback_id": feedback_id,
                "epoch": epoch,
                "run_id": run_id,
            },
        )
        if created is None:
            raise RuntimeError("EffectReceipt create returned no row")
        return {
            "outcome": "created",
            "effect_id": created["id"],
            "effect_key": created["effect_key"],
            "effect_type": effect_type,
            "effect_outcome": created["outcome"],
            "verification_status": created.get("verification_status")
            or "verified_absent",
            "fence_epoch": int(created["fence_epoch"]),
            "run_id": run_id,
            "request_fingerprint": request_fingerprint,
            "applied_at": created.get("applied_at"),
            "feedback_id": feedback_id,
            "action": action,
            "config_digest": config_digest,
            "lifecycle_event": lifecycle_event,
            "lifecycle_event_id": lid,
            "feedback_request_fingerprint": fp_before,
            "payload_deleted": bool(payload_ref),
        }

    def _record_retention_effect_tx(
        self,
        tx: Any,
        *,
        effect_id: str,
        effect_key: str,
        run_id: str,
        epoch: int,
        lease_key: str,
        effect_type: str,
        actor: str,
        before_ref: str,
        after_ref: str | None,
        proposal_id: str | None,
        dream_id: str | None,
        target_ref: str | None,
        verification_status: str,
        effect_outcome: str,
        request_hash: str,
        request_fingerprint: str,
    ) -> dict[str, Any]:
        fence_err = self._assert_fence(
            tx, lease_key=lease_key, run_id=run_id, epoch=epoch
        )
        if fence_err is not None:
            return fence_err

        existing = _run_one(
            tx,
            """
            MATCH (r:Operational:EffectReceipt {id: $id})
            RETURN r.id AS id,
                   r.effect_key AS effect_key,
                   r.request_fingerprint AS request_fingerprint,
                   r.request_hash AS request_hash,
                   r.outcome AS outcome,
                   r.verification_status AS verification_status,
                   r.fence_epoch AS fence_epoch
            LIMIT 1
            """,
            {"id": effect_id},
        )
        if existing is not None:
            if existing.get("request_fingerprint") == request_fingerprint:
                return {
                    "outcome": "replayed",
                    "effect_id": existing["id"],
                    "effect_key": existing.get("effect_key"),
                    "effect_outcome": existing.get("outcome"),
                    "request_fingerprint": existing.get("request_fingerprint"),
                    "fence_epoch": existing.get("fence_epoch"),
                }
            return {
                "outcome": "conflict",
                "reason": "effect_id_reused",
                "effect_id": existing["id"],
            }

        # Also conflict if same effect_key maps to a different id.
        by_key = _run_one(
            tx,
            """
            MATCH (r:Operational:EffectReceipt {effect_key: $effect_key})
            RETURN r.id AS id,
                   r.request_fingerprint AS request_fingerprint,
                   r.outcome AS outcome,
                   r.effect_key AS effect_key,
                   r.fence_epoch AS fence_epoch
            LIMIT 1
            """,
            {"effect_key": effect_key},
        )
        if by_key is not None:
            if by_key.get("request_fingerprint") == request_fingerprint:
                return {
                    "outcome": "replayed",
                    "effect_id": by_key["id"],
                    "effect_key": by_key.get("effect_key"),
                    "effect_outcome": by_key.get("outcome"),
                    "request_fingerprint": by_key.get("request_fingerprint"),
                    "fence_epoch": by_key.get("fence_epoch"),
                }
            return {
                "outcome": "conflict",
                "reason": "effect_key_reused",
                "effect_id": by_key["id"],
                "effect_key": effect_key,
            }

        created = _run_one(
            tx,
            """
            CREATE (r:Operational:EffectReceipt)
            SET r.id = $id,
                r.effect_key = $effect_key,
                r.request_hash = $request_hash,
                r.request_fingerprint = $fp,
                r.proposal_id = $proposal_id,
                r.dream_id = $dream_id,
                r.effect_type = $effect_type,
                r.actor = $actor,
                r.before_ref = $before_ref,
                r.after_ref = $after_ref,
                r.target_ref = $target_ref,
                r.outcome = $effect_outcome,
                r.verification_status = $verification_status,
                r.fence_epoch = $epoch,
                r.run_id = $run_id,
                r.applied_at = datetime(),
                r.created_at = datetime()
            RETURN r.id AS id,
                   r.effect_key AS effect_key,
                   r.outcome AS outcome,
                   r.fence_epoch AS fence_epoch,
                   toString(r.applied_at) AS applied_at
            """,
            {
                "id": effect_id,
                "effect_key": effect_key,
                "request_hash": request_hash,
                "fp": request_fingerprint,
                "proposal_id": proposal_id,
                "dream_id": dream_id,
                "effect_type": effect_type,
                "actor": actor,
                "before_ref": before_ref,
                "after_ref": after_ref,
                "target_ref": target_ref,
                "effect_outcome": effect_outcome,
                "verification_status": verification_status,
                "epoch": epoch,
                "run_id": run_id,
            },
        )
        if created is None:
            raise RuntimeError("EffectReceipt create returned no row")
        return {
            "outcome": "created",
            "effect_id": created["id"],
            "effect_key": created["effect_key"],
            "effect_type": effect_type,
            "effect_outcome": created["outcome"],
            "verification_status": verification_status,
            "fence_epoch": int(created["fence_epoch"]),
            "run_id": run_id,
            "request_fingerprint": request_fingerprint,
            "applied_at": created.get("applied_at"),
        }

    # ------------------------------------------------------------------
    # PatchArtifact publish (control plane metadata only — no activation)
    # ------------------------------------------------------------------

    def publish_patch_artifact(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Coordinator-only: record a quarantined PatchArtifact after fence checks.

        Revalidates ``run_id + lease_epoch``, artifact digest, proposal/snapshot
        state, and base fingerprints. Never loads or activates the artifact;
        orphan quarantine files without a published record are ignored by review
        and runtime.
        """
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")
        assert_no_absorption_field(payload)

        artifact_id = _require_id(payload.get("id"), "id")
        proposal_id = _require_id(payload.get("proposal_id"), "proposal_id")
        evidence_snapshot_id = _require_id(
            payload.get("evidence_snapshot_id"), "evidence_snapshot_id"
        )
        base_commit = _require_id(payload.get("base_commit"), "base_commit")
        before_hashes_json = _json_field(
            payload.get("before_hashes_json") or payload.get("before_hashes") or "{}",
            "before_hashes_json",
            default="{}",
        )
        compiler_version = _require_id(
            payload.get("compiler_version") or "1", "compiler_version"
        )
        schema_version = _require_id(
            payload.get("schema_version") or "1", "schema_version"
        )
        target_path_allowlist_json = _json_field(
            payload.get("target_path_allowlist_json")
            or payload.get("target_path_allowlist")
            or "[]",
            "target_path_allowlist_json",
            default="[]",
        )
        patch_sha256 = _require_id(payload.get("patch_sha256"), "patch_sha256")
        # Paths may be longer than id fields (host state dir + quarantine layout).
        if not isinstance(payload.get("artifact_path"), str) or not str(
            payload.get("artifact_path")
        ).strip():
            raise ValueError("artifact_path must be a non-empty string")
        artifact_path = str(payload["artifact_path"]).strip()
        if len(artifact_path) > 1024:
            raise ValueError("artifact_path exceeds max length 1024")
        # Quarantine paths only — refuse plugin / active-overlay publish targets.
        norm_path = artifact_path.replace("\\", "/")
        if ".." in norm_path.split("/"):
            raise ValueError("artifact_path_traversal_forbidden")
        if "quarantine" not in norm_path:
            raise ValueError("artifact_path_must_be_quarantine")
        for forbidden in ("/plugins/", "active-overlays", "/SOUL", "node_modules"):
            if forbidden in norm_path:
                raise ValueError(f"artifact_path_forbidden_segment:{forbidden}")

        expected_plugin_generation = _optional_text(
            payload.get("expected_plugin_generation"),
            "expected_plugin_generation",
            MAX_REF_LEN,
        )
        rollback_ref = _optional_text(
            payload.get("rollback_ref"), "rollback_ref", MAX_REF_LEN
        )
        rule_id = _optional_text(payload.get("rule_id"), "rule_id", MAX_REF_LEN)
        extension_slot = _optional_text(
            payload.get("extension_slot"), "extension_slot", MAX_REF_LEN
        )
        target_skill = _optional_text(
            payload.get("target_skill"), "target_skill", MAX_REF_LEN
        )
        target_file = _optional_text(
            payload.get("target_file"), "target_file", MAX_REF_LEN
        )
        declared_base_commit = _optional_text(
            payload.get("declared_base_commit") or base_commit,
            "declared_base_commit",
            MAX_REF_LEN,
        )
        measured_base_commit = _optional_text(
            payload.get("measured_base_commit"), "measured_base_commit", MAX_REF_LEN
        )
        if (
            measured_base_commit is not None
            and declared_base_commit is not None
            and measured_base_commit != declared_base_commit
        ):
            return {
                "outcome": "stale",
                "reason": "base_commit_drift",
                "declared_base_commit": declared_base_commit,
                "measured_base_commit": measured_base_commit,
            }

        epoch = _require_int(
            payload.get("epoch")
            if payload.get("epoch") is not None
            else payload.get("lease_epoch"),
            "epoch",
            min_value=1,
        )
        run_id = _require_id(payload.get("run_id"), "run_id")
        lease_key = _require_id(
            payload.get("lease_key") or DEFAULT_LEASE_KEY, "lease_key"
        )

        identity = {
            "artifact_path": artifact_path,
            "base_commit": base_commit,
            "compiler_version": compiler_version,
            "evidence_snapshot_id": evidence_snapshot_id,
            "id": artifact_id,
            "patch_sha256": patch_sha256,
            "proposal_id": proposal_id,
            "schema_version": schema_version,
        }
        request_fingerprint = _digest_text(_canonical_json(identity))

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._publish_patch_artifact_tx(
                    tx,
                    artifact_id=artifact_id,
                    proposal_id=proposal_id,
                    evidence_snapshot_id=evidence_snapshot_id,
                    base_commit=base_commit,
                    before_hashes_json=before_hashes_json,
                    compiler_version=compiler_version,
                    schema_version=schema_version,
                    target_path_allowlist_json=target_path_allowlist_json,
                    patch_sha256=patch_sha256,
                    artifact_path=artifact_path,
                    expected_plugin_generation=expected_plugin_generation,
                    rollback_ref=rollback_ref,
                    rule_id=rule_id,
                    extension_slot=extension_slot,
                    target_skill=target_skill,
                    target_file=target_file,
                    request_fingerprint=request_fingerprint,
                    epoch=epoch,
                    run_id=run_id,
                    lease_key=lease_key,
                ),
            )

        return self._with_session(operation)

    def _publish_patch_artifact_tx(
        self,
        tx: Any,
        *,
        artifact_id: str,
        proposal_id: str,
        evidence_snapshot_id: str,
        base_commit: str,
        before_hashes_json: str,
        compiler_version: str,
        schema_version: str,
        target_path_allowlist_json: str,
        patch_sha256: str,
        artifact_path: str,
        expected_plugin_generation: str | None,
        rollback_ref: str | None,
        rule_id: str | None,
        extension_slot: str | None,
        target_skill: str | None,
        target_file: str | None,
        request_fingerprint: str,
        epoch: int,
        run_id: str,
        lease_key: str,
    ) -> dict[str, Any]:
        fence_err = self._assert_fence(
            tx, lease_key=lease_key, run_id=run_id, epoch=epoch
        )
        if fence_err is not None:
            return fence_err

        existing = _run_one(
            tx,
            """
            MATCH (a:Operational:PatchArtifact {id: $id})
            RETURN a.id AS id,
                   a.request_fingerprint AS request_fingerprint,
                   a.patch_sha256 AS patch_sha256,
                   a.proposal_id AS proposal_id
            LIMIT 1
            """,
            {"id": artifact_id},
        )
        if existing is not None:
            if existing.get("request_fingerprint") == request_fingerprint:
                return {
                    "outcome": "replayed",
                    "artifact_id": existing["id"],
                    "proposal_id": existing.get("proposal_id"),
                    "patch_sha256": existing.get("patch_sha256"),
                    "request_fingerprint": existing.get("request_fingerprint"),
                    "published": True,
                }
            return {
                "outcome": "conflict",
                "reason": "artifact_id_reused",
                "artifact_id": existing["id"],
            }

        # Same patch digest already published for this proposal → replay.
        by_digest = _run_one(
            tx,
            """
            MATCH (a:Operational:PatchArtifact {patch_sha256: $patch_sha256})
            WHERE a.proposal_id = $proposal_id
            RETURN a.id AS id,
                   a.request_fingerprint AS request_fingerprint,
                   a.patch_sha256 AS patch_sha256,
                   a.proposal_id AS proposal_id
            LIMIT 1
            """,
            {"patch_sha256": patch_sha256, "proposal_id": proposal_id},
        )
        if by_digest is not None:
            return {
                "outcome": "replayed",
                "artifact_id": by_digest["id"],
                "proposal_id": by_digest.get("proposal_id"),
                "patch_sha256": by_digest.get("patch_sha256"),
                "request_fingerprint": by_digest.get("request_fingerprint"),
                "published": True,
            }

        proposal = _run_one(
            tx,
            """
            MATCH (p:Operational:Proposal {id: $id})
            RETURN p.id AS id,
                   p.status_projection AS status_projection,
                   p.evidence_snapshot_id AS evidence_snapshot_id,
                   p.dream_id AS dream_id
            LIMIT 1
            """,
            {"id": proposal_id},
        )
        if proposal is None:
            return {
                "outcome": "not_found",
                "reason": "proposal_not_found",
                "proposal_id": proposal_id,
            }
        status = str(proposal.get("status_projection") or "")
        if status in {"stale", "invalid", "superseded", "withdrawn", "rejected"}:
            return {
                "outcome": "stale",
                "reason": f"proposal_status_{status}",
                "proposal_id": proposal_id,
                "status_projection": status,
            }
        prop_snap = proposal.get("evidence_snapshot_id")
        if prop_snap and str(prop_snap) != evidence_snapshot_id:
            return {
                "outcome": "conflict",
                "reason": "snapshot_proposal_mismatch",
                "proposal_id": proposal_id,
                "proposal_snapshot_id": prop_snap,
                "evidence_snapshot_id": evidence_snapshot_id,
            }

        snapshot = _run_one(
            tx,
            """
            MATCH (s:Operational:EvidenceSnapshot {id: $id})
            RETURN s.id AS id,
                   s.base_commit AS base_commit,
                   s.dream_id AS dream_id,
                   s.harness_generation_id AS harness_generation_id
            LIMIT 1
            """,
            {"id": evidence_snapshot_id},
        )
        if snapshot is None:
            return {
                "outcome": "not_found",
                "reason": "snapshot_not_found",
                "evidence_snapshot_id": evidence_snapshot_id,
            }
        snap_base = snapshot.get("base_commit")
        if snap_base and str(snap_base) != base_commit:
            return {
                "outcome": "stale",
                "reason": "base_commit_drift",
                "snapshot_base_commit": snap_base,
                "declared_base_commit": base_commit,
            }

        # The graph records evidence about the immutable bundle, not caller
        # assertions. Re-read the complete bundle and bind every identity field
        # immediately before the create so tampering cannot be published.
        verified = _verify_quarantine_bundle(
            artifact_path=artifact_path,
            proposal_id=proposal_id,
            evidence_snapshot_id=evidence_snapshot_id,
            base_commit=base_commit,
            compiler_version=compiler_version,
            schema_version=schema_version,
            patch_sha256=patch_sha256,
        )
        artifact_path = str(verified["artifact_path"])

        created = _run_one(
            tx,
            """
            CREATE (a:Operational:PatchArtifact)
            SET a.id = $id,
                a.proposal_id = $proposal_id,
                a.evidence_snapshot_id = $evidence_snapshot_id,
                a.base_commit = $base_commit,
                a.before_hashes_json = $before_hashes_json,
                a.compiler_version = $compiler_version,
                a.schema_version = $schema_version,
                a.target_path_allowlist_json = $target_path_allowlist_json,
                a.patch_sha256 = $patch_sha256,
                a.artifact_path = $artifact_path,
                a.expected_plugin_generation = $expected_plugin_generation,
                a.rollback_ref = $rollback_ref,
                a.rule_id = $rule_id,
                a.extension_slot = $extension_slot,
                a.target_skill = $target_skill,
                a.target_file = $target_file,
                a.lease_epoch = $epoch,
                a.run_id = $run_id,
                a.request_fingerprint = $fp,
                a.published = true,
                a.created_at = datetime()
            WITH a
            MATCH (p:Operational:Proposal {id: $proposal_id})
            SET p.artifact_ref = $artifact_id_ref
            MERGE (p)-[:HAS_ARTIFACT]->(a)
            RETURN a.id AS id,
                   a.patch_sha256 AS patch_sha256,
                   toString(a.created_at) AS created_at
            """,
            {
                "id": artifact_id,
                "proposal_id": proposal_id,
                "evidence_snapshot_id": evidence_snapshot_id,
                "base_commit": base_commit,
                "before_hashes_json": before_hashes_json,
                "compiler_version": compiler_version,
                "schema_version": schema_version,
                "target_path_allowlist_json": target_path_allowlist_json,
                "patch_sha256": patch_sha256,
                "artifact_path": artifact_path,
                "expected_plugin_generation": expected_plugin_generation,
                "rollback_ref": rollback_ref,
                "rule_id": rule_id,
                "extension_slot": extension_slot,
                "target_skill": target_skill,
                "target_file": target_file,
                "epoch": epoch,
                "run_id": run_id,
                "fp": request_fingerprint,
                "artifact_id_ref": artifact_id,
            },
        )
        if created is None:
            raise RuntimeError("PatchArtifact create returned no row")
        return {
            "outcome": "created",
            "artifact_id": created["id"],
            "proposal_id": proposal_id,
            "patch_sha256": created["patch_sha256"],
            "request_fingerprint": request_fingerprint,
            "published": True,
            "created_at": created.get("created_at"),
            # Explicit: publish records metadata only; no activation / load.
            "runtime_effect": "none",
        }

    def dispatch(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Route a coordinator operation name to the matching store method."""
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "acquire_maintenance_lease": self.acquire_maintenance_lease,
            "renew_maintenance_lease": self.renew_maintenance_lease,
            "release_maintenance_lease": self.release_maintenance_lease,
            "create_dream_run": self.create_dream_run,
            "record_dream_stage": self.record_dream_stage,
            "create_evidence_snapshot": self.create_evidence_snapshot,
            "create_finding": self.create_finding,
            "create_proposal": self.create_proposal,
            "record_evaluation": self.record_evaluation,
            "record_decision": self.record_decision,
            "record_retention_effect": self.record_retention_effect,
            "publish_patch_artifact": self.publish_patch_artifact,
        }
        handler = handlers.get(operation)
        if handler is None:
            return {
                "outcome": "not_implemented",
                "operation": operation,
                "reason": "unknown_maintenance_operation",
            }
        return handler(payload)
