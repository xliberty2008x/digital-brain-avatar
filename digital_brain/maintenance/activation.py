"""Operator-only overlay trial activation, expiry, and rollback.

Activation requires a single-use ActivationAuthority (minted off model-facing
MCP). There is no unattended ``--yes`` path. Trials never silently promote to
permanent deployment.

Records EffectReceipt, Deployment, and ExposureWindow as separate nodes.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from digital_brain.maintenance.active_overlays import (
    ActiveManifest,
    ActiveOverlayEntry,
    ActiveOverlayError,
    atomic_replace_manifest,
    build_manifest_with_entry,
    compute_manifest_digest,
    empty_active_manifest,
    entry_to_dict,
    load_validated_active_overlays,
    manifest_from_mapping,
    manifest_to_public_dict,
    prior_digest_for,
    restore_prior_manifest,
    stage_overlay_content,
)
from digital_brain.maintenance.alias_effects import AliasEffectStore
from digital_brain.maintenance.models import (
    EMPTY_DIGEST,
    assert_legal_authority_transition,
    digest_text,
)

OVERLAY_EFFECT_TYPES: frozenset[str] = frozenset(
    {
        "activate_overlay_trial",
        "rollback_overlay_trial",
        "expire_overlay_trial",
        "permanent_overlay_deploy",
    }
)

DEFAULT_AUTHORITY_TTL_SECONDS = 900
PENDING_ACTIVATION_FILENAME = "activation-pending.json"


class TrialPolicyError(ValueError):
    """Raised when trial eligibility / rollback thresholds are incomplete."""


class PermanentDeployError(ValueError):
    """Raised when permanent deploy gates are not all satisfied."""


class ActivationError(ValueError):
    """Raised for structural activation failures."""


def _now_iso(now: datetime | None = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _require_str(value: Any, field: str, *, max_len: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    text = value.strip()
    if len(text) > max_len:
        raise ValueError(f"{field} exceeds max length {max_len}")
    return text


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


@dataclass(frozen=True)
class TrialPolicy:
    """Fixed eligibility and rollback thresholds required before trial activation."""

    decision_point: str
    duration_seconds: int
    exposure_cap: int
    target_recurrence: int
    counterevidence_threshold: int
    guardrail_rollback_thresholds: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.decision_point, str) or not self.decision_point.strip():
            raise TrialPolicyError("decision_point is required")
        if int(self.duration_seconds) <= 0:
            raise TrialPolicyError("duration_seconds must be positive")
        if int(self.exposure_cap) <= 0:
            raise TrialPolicyError("exposure_cap must be positive")
        if int(self.target_recurrence) < 0:
            raise TrialPolicyError("target_recurrence must be non-negative")
        if int(self.counterevidence_threshold) < 0:
            raise TrialPolicyError("counterevidence_threshold must be non-negative")
        if not isinstance(self.guardrail_rollback_thresholds, Mapping):
            raise TrialPolicyError("guardrail_rollback_thresholds must be a mapping")
        if not dict(self.guardrail_rollback_thresholds):
            raise TrialPolicyError("guardrail_rollback_thresholds must be non-empty")

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "counterevidence_threshold": int(self.counterevidence_threshold),
            "decision_point": self.decision_point,
            "duration_seconds": int(self.duration_seconds),
            "exposure_cap": int(self.exposure_cap),
            "guardrail_rollback_thresholds": dict(self.guardrail_rollback_thresholds),
            "target_recurrence": int(self.target_recurrence),
        }


@dataclass(frozen=True)
class OverlayActivationBinding:
    """Exact activation scope bound into authority + effect hash."""

    proposal_id: str
    proposal_hash: str
    artifact_hash: str
    target_ref: str
    base_commit: str
    before_hashes: Mapping[str, str]
    rule_id: str
    extension_slot: str
    target_skill: str
    target_file: str


def compute_overlay_before_fingerprint(
    *,
    target_ref: str,
    base_commit: str,
    before_hashes: Mapping[str, str],
    prior_manifest_digest: str,
) -> str:
    return digest_text(
        _canonical_json(
            {
                "base_commit": base_commit,
                "before_hashes": {str(k): str(v) for k, v in sorted(before_hashes.items())},
                "prior_manifest_digest": prior_manifest_digest,
                "target_ref": target_ref,
            }
        )
    )


def build_activation_request_hash(
    *,
    effect_type: str,
    binding: OverlayActivationBinding,
    prior_manifest_digest: str,
    artifact_hash: str,
) -> str:
    if effect_type not in OVERLAY_EFFECT_TYPES:
        raise ActivationError(f"invalid_effect_type:{effect_type}")
    return digest_text(
        _canonical_json(
            {
                "artifact_hash": artifact_hash,
                "base_commit": binding.base_commit,
                "before_hashes": {
                    str(k): str(v) for k, v in sorted(binding.before_hashes.items())
                },
                "effect_type": effect_type,
                "extension_slot": binding.extension_slot,
                "prior_manifest_digest": prior_manifest_digest,
                "proposal_hash": binding.proposal_hash,
                "proposal_id": binding.proposal_id,
                "rule_id": binding.rule_id,
                "target_ref": binding.target_ref,
            }
        )
    )


def validate_authority_for_activation(
    *,
    authority: Mapping[str, Any],
    nonce: str,
    binding: OverlayActivationBinding,
    actor: str,
    live_before_fingerprint: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Pure authority + binding checks (no graph / filesystem side effects)."""
    if not isinstance(authority, Mapping):
        return {"outcome": "failed", "reason": "authority_not_found"}

    status = str(authority.get("status") or "")
    authority_id = authority.get("id")

    if status == "consumed":
        return {
            "outcome": "replayed",
            "reason": "authority_already_consumed",
            "authority_id": authority_id,
            "consumption_receipt_id": authority.get("consumption_receipt_id"),
        }
    if status in {"expired", "revoked"}:
        return {
            "outcome": "failed",
            "reason": f"authority_{status}",
            "authority_id": authority_id,
        }
    if status != "minted":
        return {
            "outcome": "failed",
            "reason": "authority_not_minted",
            "authority_id": authority_id,
        }

    expected_approver = str(authority.get("approver") or "")
    if not expected_approver or not secrets.compare_digest(
        expected_approver, str(actor)
    ):
        return {
            "outcome": "failed",
            "reason": "authority_approver_mismatch",
            "authority_id": authority_id,
        }

    expires_at = authority.get("expires_at")
    if expires_at:
        try:
            exp = _parse_iso(str(expires_at))
            current = now or datetime.now(timezone.utc)
            if current > exp:
                return {
                    "outcome": "failed",
                    "reason": "authority_expired",
                    "authority_id": authority_id,
                }
        except ValueError:
            return {
                "outcome": "failed",
                "reason": "authority_expires_at_invalid",
                "authority_id": authority_id,
            }

    nonce_digest = digest_text(str(nonce))
    if not secrets.compare_digest(
        str(authority.get("nonce_digest") or ""), nonce_digest
    ):
        return {
            "outcome": "failed",
            "reason": "authority_nonce_mismatch",
            "authority_id": authority_id,
        }

    if str(authority.get("proposal_id") or "") != binding.proposal_id:
        return {
            "outcome": "stale",
            "reason": "proposal_id_mismatch",
            "authority_id": authority_id,
        }
    if str(authority.get("proposal_hash") or "") != binding.proposal_hash:
        return {
            "outcome": "stale",
            "reason": "proposal_hash_mismatch",
            "authority_id": authority_id,
        }
    if str(authority.get("target_ref") or "") != binding.target_ref:
        return {
            "outcome": "conflict",
            "reason": "target_ref_mismatch",
            "authority_id": authority_id,
            "authority_target": authority.get("target_ref"),
            "request_target": binding.target_ref,
        }
    if str(authority.get("artifact_or_effect_hash") or "") != binding.artifact_hash:
        return {
            "outcome": "conflict",
            "reason": "artifact_hash_mismatch",
            "authority_id": authority_id,
            "expected": authority.get("artifact_or_effect_hash"),
            "computed": binding.artifact_hash,
        }

    auth_before = str(authority.get("before_fingerprint") or "")
    if auth_before and auth_before != live_before_fingerprint:
        return {
            "outcome": "stale",
            "reason": "before_fingerprint_mismatch",
            "authority_id": authority_id,
            "expected": auth_before,
            "live": live_before_fingerprint,
        }

    return {
        "outcome": "ok",
        "authority_id": authority_id,
        "authority_digest": digest_text(
            _canonical_json(
                {
                    "artifact_or_effect_hash": authority.get("artifact_or_effect_hash"),
                    "before_fingerprint": authority.get("before_fingerprint"),
                    "id": authority.get("id"),
                    "proposal_id": authority.get("proposal_id"),
                    "target_ref": authority.get("target_ref"),
                }
            )
        ),
    }


def assert_permanent_deploy_requirements(
    *,
    reviewed_git_content: bool,
    plugin_version_bumped: bool,
    host_reloaded: bool,
    generation_loaded_proof: str | None,
) -> None:
    """Permanent deploy requires Git review + version bump + reload + proof."""
    if not reviewed_git_content:
        raise PermanentDeployError("reviewed_git_content_required")
    if not plugin_version_bumped:
        raise PermanentDeployError("plugin_version_bump_required")
    if not host_reloaded:
        raise PermanentDeployError("host_reload_required")
    if not generation_loaded_proof or not str(generation_loaded_proof).strip():
        raise PermanentDeployError("generation_loaded_proof_required")


def _pending_path(state_dir: str | Path | None) -> Path:
    from digital_brain.maintenance.active_overlays import active_overlays_root

    return active_overlays_root(state_dir) / PENDING_ACTIVATION_FILENAME


def write_activation_pending(
    state_dir: str | Path | None,
    payload: Mapping[str, Any],
) -> Path:
    from digital_brain.maintenance.active_overlays import _write_private_file_fsync

    path = _pending_path(state_dir)
    data = (_canonical_json(dict(payload)) + "\n").encode("utf-8")
    _write_private_file_fsync(path, data)
    return path


def clear_activation_pending(state_dir: str | Path | None) -> None:
    path = _pending_path(state_dir)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def read_activation_pending(
    state_dir: str | Path | None,
) -> dict[str, Any] | None:
    path = _pending_path(state_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# OverlayEffectStore — EffectReceipt / Deployment / ExposureWindow
# ---------------------------------------------------------------------------


def _execute_write(session: Any, fn: Callable[[Any], Any]) -> Any:
    execute_write = getattr(session, "execute_write", None) or getattr(
        session, "write_transaction", None
    )
    if execute_write is None:
        return fn(session)
    return execute_write(fn)


def _run_one(runner: Any, query: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    result = runner.run(query, params or {})
    record = result.single()
    if record is None:
        return None
    if hasattr(record, "data"):
        return record.data()
    return dict(record)


class OverlayEffectStore:
    """Operator-only overlay effect / deployment / exposure receipts.

    Never register as FastMCP tools. Pair with quality/admin credentials.
    """

    def __init__(self, driver_factory: Callable[[], Any], database: str = "neo4j"):
        self._driver_factory = driver_factory
        self._database = database

    def _with_session(self, operation: Callable[[Any], Any]) -> Any:
        with self._driver_factory() as driver:
            with driver.session(database=self._database) as session:
                return operation(session)

    def get_effect_by_request_hash(self, request_hash: str) -> dict[str, Any] | None:
        request_hash = _require_str(request_hash, "request_hash")

        def operation(session: Any) -> dict[str, Any] | None:
            return _run_one(
                session,
                """
                MATCH (r:Operational:EffectReceipt {request_hash: $request_hash})
                RETURN r.id AS id,
                       r.outcome AS outcome,
                       r.effect_type AS effect_type,
                       r.request_hash AS request_hash,
                       r.effect_key AS effect_key,
                       r.proposal_id AS proposal_id,
                       r.before_ref AS before_ref,
                       r.after_ref AS after_ref,
                       r.undo_ref AS undo_ref,
                       r.applied_at AS applied_at
                LIMIT 1
                """,
                {"request_hash": request_hash},
            )

        return self._with_session(operation)

    def get_effect_by_key(self, effect_key: str) -> dict[str, Any] | None:
        effect_key = _require_str(effect_key, "effect_key")

        def operation(session: Any) -> dict[str, Any] | None:
            return _run_one(
                session,
                """
                MATCH (r:Operational:EffectReceipt {effect_key: $effect_key})
                RETURN r.id AS id,
                       r.outcome AS outcome,
                       r.request_hash AS request_hash,
                       r.effect_type AS effect_type
                LIMIT 1
                """,
                {"effect_key": effect_key},
            )

        return self._with_session(operation)

    def record_activation_bundle(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create EffectReceipt + Deployment + ExposureWindow (or replay).

        Same request_hash → replayed without duplicates.
        Same effect_key + different request_hash → conflict.
        """
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")

        request_hash = _require_str(payload.get("request_hash"), "request_hash")
        effect_key = _require_str(payload.get("effect_key"), "effect_key")
        effect_type = _require_str(payload.get("effect_type"), "effect_type")
        if effect_type not in OVERLAY_EFFECT_TYPES:
            raise ValueError(f"invalid effect_type: {effect_type}")
        proposal_id = _require_str(payload.get("proposal_id"), "proposal_id")
        actor = _require_str(payload.get("actor"), "actor")
        before_ref = _require_str(payload.get("before_ref"), "before_ref")
        after_ref = payload.get("after_ref")
        if after_ref is not None:
            after_ref = _require_str(after_ref, "after_ref")
        authority_digest = payload.get("authority_digest")
        undo_ref = payload.get("undo_ref")
        generation_id = _require_str(
            payload.get("generation_id") or payload.get("rollback_generation") or EMPTY_DIGEST,
            "generation_id",
        )
        deployment_status = _require_str(
            payload.get("deployment_status") or "trial_active", "deployment_status"
        )
        decision_point = _require_str(
            payload.get("decision_point") or "unspecified", "decision_point"
        )
        eligible_target = int(payload.get("eligible_target") or payload.get("exposure_cap") or 0)
        started_at = str(payload.get("started_at") or _now_iso())
        ends_at = str(payload.get("ends_at") or started_at)
        guardrail_json = payload.get("guardrail_json") or "{}"
        if isinstance(guardrail_json, Mapping):
            guardrail_json = _canonical_json(dict(guardrail_json))
        receipt_id = _require_str(payload.get("receipt_id") or f"er-{uuid.uuid4()}", "receipt_id")
        deployment_id = _require_str(
            payload.get("deployment_id") or f"dep-{uuid.uuid4()}", "deployment_id"
        )
        window_id = _require_str(
            payload.get("window_id") or f"ew-{uuid.uuid4()}", "window_id"
        )
        applied_at = str(payload.get("applied_at") or _now_iso())

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._record_bundle_tx(
                    tx,
                    request_hash=request_hash,
                    effect_key=effect_key,
                    effect_type=effect_type,
                    proposal_id=proposal_id,
                    actor=actor,
                    before_ref=before_ref,
                    after_ref=after_ref,
                    authority_digest=authority_digest,
                    undo_ref=undo_ref,
                    generation_id=generation_id,
                    deployment_status=deployment_status,
                    decision_point=decision_point,
                    eligible_target=eligible_target,
                    started_at=started_at,
                    ends_at=ends_at,
                    guardrail_json=str(guardrail_json),
                    receipt_id=receipt_id,
                    deployment_id=deployment_id,
                    window_id=window_id,
                    applied_at=applied_at,
                    create_window=bool(payload.get("create_window", True)),
                ),
            )

        return self._with_session(operation)

    def _record_bundle_tx(
        self,
        tx: Any,
        *,
        request_hash: str,
        effect_key: str,
        effect_type: str,
        proposal_id: str,
        actor: str,
        before_ref: str,
        after_ref: str | None,
        authority_digest: Any,
        undo_ref: Any,
        generation_id: str,
        deployment_status: str,
        decision_point: str,
        eligible_target: int,
        started_at: str,
        ends_at: str,
        guardrail_json: str,
        receipt_id: str,
        deployment_id: str,
        window_id: str,
        applied_at: str,
        create_window: bool,
    ) -> dict[str, Any]:
        existing_req = _run_one(
            tx,
            """
            MATCH (r:Operational:EffectReceipt {request_hash: $request_hash})
            RETURN r.id AS id,
                   r.outcome AS outcome,
                   r.effect_type AS effect_type,
                   r.request_hash AS request_hash,
                   r.effect_key AS effect_key,
                   r.proposal_id AS proposal_id,
                   r.before_ref AS before_ref,
                   r.after_ref AS after_ref,
                   r.undo_ref AS undo_ref,
                   r.applied_at AS applied_at
            LIMIT 1
            """,
            {"request_hash": request_hash},
        )
        if existing_req is not None:
            return {
                "outcome": "replayed",
                "effect_receipt": existing_req,
                "deployment": None,
                "exposure_window": None,
            }

        existing_key = _run_one(
            tx,
            """
            MATCH (r:Operational:EffectReceipt {effect_key: $effect_key})
            RETURN r.id AS id,
                   r.outcome AS outcome,
                   r.request_hash AS request_hash,
                   r.effect_type AS effect_type
            LIMIT 1
            """,
            {"effect_key": effect_key},
        )
        if existing_key is not None:
            if existing_key.get("request_hash") != request_hash:
                return {
                    "outcome": "conflict",
                    "reason": "effect_key_reused",
                    "existing": existing_key,
                }
            return {
                "outcome": "replayed",
                "effect_receipt": existing_key,
                "deployment": None,
                "exposure_window": None,
            }

        receipt = _run_one(
            tx,
            """
            CREATE (r:Operational:EffectReceipt)
            SET r.id = $id,
                r.effect_key = $effect_key,
                r.request_hash = $request_hash,
                r.proposal_id = $proposal_id,
                r.effect_type = $effect_type,
                r.actor = $actor,
                r.before_ref = $before_ref,
                r.after_ref = $after_ref,
                r.outcome = $outcome,
                r.verification_status = $verification_status,
                r.authority_digest = $authority_digest,
                r.undo_ref = $undo_ref,
                r.applied_at = $applied_at
            RETURN r.id AS id, r.outcome AS outcome
            """,
            {
                "id": receipt_id,
                "effect_key": effect_key,
                "request_hash": request_hash,
                "proposal_id": proposal_id,
                "effect_type": effect_type,
                "actor": actor,
                "before_ref": before_ref,
                "after_ref": after_ref,
                "outcome": "applied",
                "verification_status": "verified",
                "authority_digest": authority_digest,
                "undo_ref": undo_ref,
                "applied_at": applied_at,
            },
        )
        if receipt is None:
            raise RuntimeError("EffectReceipt create returned no row")

        deployment = _run_one(
            tx,
            """
            CREATE (d:Operational:Deployment)
            SET d.id = $id,
                d.proposal_id = $proposal_id,
                d.generation_id = $generation_id,
                d.status = $status,
                d.activated_at = $activated_at,
                d.retired_at = $retired_at,
                d.rollback_ref = $rollback_ref
            RETURN d.id AS id, d.status AS status
            """,
            {
                "id": deployment_id,
                "proposal_id": proposal_id,
                "generation_id": generation_id,
                "status": deployment_status,
                "activated_at": applied_at if deployment_status == "trial_active" else None,
                "retired_at": applied_at
                if deployment_status in {"expired", "rolled_back"}
                else None,
                "rollback_ref": undo_ref,
            },
        )
        if deployment is None:
            raise RuntimeError("Deployment create returned no row")

        window = None
        if create_window and deployment_status == "trial_active":
            window = _run_one(
                tx,
                """
                CREATE (w:Operational:ExposureWindow)
                SET w.id = $id,
                    w.deployment_id = $deployment_id,
                    w.decision_point = $decision_point,
                    w.eligible_target = $eligible_target,
                    w.eligible_seen = $eligible_seen,
                    w.started_at = $started_at,
                    w.ends_at = $ends_at,
                    w.recurrence_count = $recurrence_count,
                    w.counterevidence_count = $counterevidence_count,
                    w.guardrail_json = $guardrail_json,
                    w.effectiveness_status = $effectiveness_status
                RETURN w.id AS id
                """,
                {
                    "id": window_id,
                    "deployment_id": deployment_id,
                    "decision_point": decision_point,
                    "eligible_target": eligible_target,
                    "eligible_seen": 0,
                    "started_at": started_at,
                    "ends_at": ends_at,
                    "recurrence_count": 0,
                    "counterevidence_count": 0,
                    "guardrail_json": guardrail_json,
                    "effectiveness_status": "observing",
                },
            )
            if window is None:
                raise RuntimeError("ExposureWindow create returned no row")

        return {
            "outcome": "applied",
            "effect_receipt": {
                "id": receipt["id"],
                "outcome": receipt["outcome"],
                "effect_type": effect_type,
                "request_hash": request_hash,
                "effect_key": effect_key,
            },
            "deployment": {
                "id": deployment["id"],
                "status": deployment["status"],
                "proposal_id": proposal_id,
                "generation_id": generation_id,
            },
            "exposure_window": (
                None
                if window is None
                else {
                    "id": window["id"],
                    "deployment_id": deployment_id,
                    "decision_point": decision_point,
                    "eligible_target": eligible_target,
                    "started_at": started_at,
                    "ends_at": ends_at,
                }
            ),
        }

    def mark_deployment_status(
        self,
        *,
        deployment_id: str,
        status: str,
        retired_at: str | None = None,
        rollback_ref: str | None = None,
    ) -> dict[str, Any]:
        deployment_id = _require_str(deployment_id, "deployment_id")
        status = _require_str(status, "status")

        def operation(session: Any) -> dict[str, Any]:
            row = _run_one(
                session,
                """
                MATCH (d:Operational:Deployment {id: $id})
                SET d.status = $status,
                    d.retired_at = coalesce($retired_at, d.retired_at),
                    d.rollback_ref = coalesce($rollback_ref, d.rollback_ref)
                RETURN d.id AS id, d.status AS status
                """,
                {
                    "id": deployment_id,
                    "status": status,
                    "retired_at": retired_at,
                    "rollback_ref": rollback_ref,
                },
            )
            if row is None:
                return {"outcome": "not_found", "deployment_id": deployment_id}
            return {"outcome": "ok", "deployment_id": row["id"], "status": row["status"]}

        return self._with_session(operation)

    def consume_authority(
        self,
        *,
        authority_id: str,
        receipt_id: str,
        now: str | None = None,
    ) -> dict[str, Any]:
        """CAS mint→consumed. Caller must already have validated nonce/bindings."""
        authority_id = _require_str(authority_id, "authority_id")
        receipt_id = _require_str(receipt_id, "receipt_id")
        now = now or _now_iso()

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._consume_authority_tx(
                    tx,
                    authority_id=authority_id,
                    receipt_id=receipt_id,
                    now=now,
                ),
            )

        return self._with_session(operation)

    def _consume_authority_tx(
        self,
        tx: Any,
        *,
        authority_id: str,
        receipt_id: str,
        now: str,
    ) -> dict[str, Any]:
        auth = _run_one(
            tx,
            """
            MATCH (a:Operational:ActivationAuthority {id: $id})
            RETURN a.id AS id,
                   a.status AS status,
                   a.consumption_receipt_id AS consumption_receipt_id
            LIMIT 1
            """,
            {"id": authority_id},
        )
        if auth is None:
            return {"outcome": "failed", "reason": "authority_not_found"}
        if auth.get("status") == "consumed":
            return {
                "outcome": "replayed",
                "reason": "authority_already_consumed",
                "authority_id": authority_id,
                "consumption_receipt_id": auth.get("consumption_receipt_id"),
            }
        if auth.get("status") != "minted":
            return {
                "outcome": "failed",
                "reason": f"authority_{auth.get('status')}",
                "authority_id": authority_id,
            }
        assert_legal_authority_transition("minted", "consumed")
        updated = _run_one(
            tx,
            """
            MATCH (a:Operational:ActivationAuthority {id: $id})
            WHERE a.status = 'minted'
            SET a.status = 'consumed',
                a.consumed_at = $now,
                a.consumption_receipt_id = $receipt_id,
                a.reconciliation_receipt_id = $receipt_id
            RETURN a.id AS id, a.status AS status
            """,
            {"id": authority_id, "now": now, "receipt_id": receipt_id},
        )
        if updated is None:
            return {
                "outcome": "replayed",
                "reason": "authority_already_consumed",
                "authority_id": authority_id,
            }
        return {
            "outcome": "consumed",
            "authority_id": updated["id"],
            "status": updated["status"],
            "consumption_receipt_id": receipt_id,
        }


def activate_overlay_trial(
    *,
    state_dir: str | Path | None,
    binding: OverlayActivationBinding,
    artifact_md: str,
    trial_policy: TrialPolicy,
    authority_id: str,
    nonce: str,
    actor: str,
    rollback_generation: str,
    alias_store: AliasEffectStore,
    effect_store: OverlayEffectStore,
    now: datetime | None = None,
    request_hash: str | None = None,
) -> dict[str, Any]:
    """Stage overlay + atomic manifest replace + graph receipts under authority.

    Order (crash-safe with reconcile):
    1. Validate trial policy + authority bindings
    2. Stage overlay file (idempotent by digest)
    3. Write activation-pending marker
    4. Atomic manifest replace
    5. Record EffectReceipt + Deployment + ExposureWindow
    6. Consume authority
    7. Clear pending marker
    """
    # TrialPolicy.__post_init__ already validates completeness.
    if not isinstance(trial_policy, TrialPolicy):
        raise TrialPolicyError("trial_policy required")

    # Idempotent replay by request_hash when provided/computed after prior known.
    prior = load_validated_active_overlays(state_dir)
    if prior.fail_closed:
        # Refuse activation on a fail-closed live tree until operator restores.
        return {
            "outcome": "failed",
            "reason": "active_manifest_fail_closed",
            "fail_reason": prior.fail_reason,
        }
    prior_public = manifest_to_public_dict(prior)
    prior_digest = prior_digest_for(prior)

    live_before = compute_overlay_before_fingerprint(
        target_ref=binding.target_ref,
        base_commit=binding.base_commit,
        before_hashes=binding.before_hashes,
        prior_manifest_digest=prior_digest,
    )

    auth_row = alias_store.get_authority_receipt(authority_id)
    if auth_row.get("outcome") != "found":
        return {"outcome": "failed", "reason": "authority_not_found"}

    # Rebuild authority mapping for pure validator.
    authority = {
        "id": auth_row["authority_id"],
        "status": auth_row.get("status"),
        "proposal_id": auth_row.get("proposal_id"),
        "target_ref": auth_row.get("target_ref"),
        "before_fingerprint": auth_row.get("before_fingerprint"),
        "artifact_or_effect_hash": auth_row.get("artifact_or_effect_hash"),
        "approver": auth_row.get("approver"),
        "expires_at": auth_row.get("expires_at"),
        "consumption_receipt_id": auth_row.get("consumption_receipt_id"),
        "proposal_hash": None,  # filled from full match below
        "nonce_digest": None,
    }
    # Need full authority row including nonce_digest + proposal_hash.
    # get_authority_receipt omits nonce_digest intentionally — re-read via apply path.
    # Use AliasEffectStore internal by minting pattern: fetch via apply-style.
    # We re-query through a lightweight path: store the mint fields by reading
    # authority via private-style MATCH in effect_store... Use alias store's
    # apply which expects nonce. For validate we need nonce_digest from graph.

    # Direct session read via alias_store driver:
    def _load_auth(session: Any) -> dict[str, Any] | None:
        return _run_one(
            session,
            """
            MATCH (a:Operational:ActivationAuthority {id: $id})
            RETURN a.id AS id,
                   a.status AS status,
                   a.nonce_digest AS nonce_digest,
                   a.proposal_id AS proposal_id,
                   a.proposal_hash AS proposal_hash,
                   a.target_ref AS target_ref,
                   a.before_fingerprint AS before_fingerprint,
                   a.artifact_or_effect_hash AS artifact_or_effect_hash,
                   a.approver AS approver,
                   a.expires_at AS expires_at,
                   a.consumption_receipt_id AS consumption_receipt_id
            LIMIT 1
            """,
            {"id": authority_id},
        )

    authority_full = alias_store._with_session(_load_auth)  # noqa: SLF001
    if authority_full is None:
        return {"outcome": "failed", "reason": "authority_not_found"}

    # Replayed consume path
    if authority_full.get("status") == "consumed":
        existing = None
        if request_hash:
            existing = effect_store.get_effect_by_request_hash(request_hash)
        receipt = auth_row.get("effect_receipt")
        return {
            "outcome": "replayed",
            "reason": "authority_already_consumed",
            "authority_id": authority_id,
            "effect_receipt": receipt or existing,
            "replacement_minted": False,
        }

    check = validate_authority_for_activation(
        authority=authority_full,
        nonce=nonce,
        binding=binding,
        actor=actor,
        live_before_fingerprint=live_before,
        now=now,
    )
    if check["outcome"] != "ok":
        return check

    # Stage file first (same filesystem as manifest).
    path, digest = stage_overlay_content(
        state_dir=state_dir,
        proposal_id=binding.proposal_id,
        content=artifact_md,
    )
    if digest != binding.artifact_hash:
        return {
            "outcome": "conflict",
            "reason": "artifact_content_hash_mismatch",
            "expected": binding.artifact_hash,
            "computed": digest,
        }

    current = now or datetime.now(timezone.utc)
    expires = current + timedelta(seconds=int(trial_policy.duration_seconds))
    expires_iso = _now_iso(expires)
    started_iso = _now_iso(current)

    entry = ActiveOverlayEntry(
        proposal_id=binding.proposal_id,
        digest=digest,
        rule_id=binding.rule_id,
        extension_slot=binding.extension_slot,
        target_skill=binding.target_skill,
        target_file=binding.target_file,
        trial_expires_at=expires_iso,
        exposure_budget=int(trial_policy.exposure_cap),
        rollback_generation=rollback_generation,
        status="trial_active",
        exposure_used=0,
        base_commit=binding.base_commit,
        artifact_hash=digest,
    )
    new_manifest = build_manifest_with_entry(
        prior=prior,
        entry=entry,
        rollback_generation=rollback_generation,
        created_at=started_iso,
    )
    new_digest = compute_manifest_digest(new_manifest)

    req_hash = request_hash or build_activation_request_hash(
        effect_type="activate_overlay_trial",
        binding=binding,
        prior_manifest_digest=prior_digest,
        artifact_hash=digest,
    )

    # Idempotent: if receipt already exists for this request, do not re-stage.
    existing_effect = effect_store.get_effect_by_request_hash(req_hash)
    if existing_effect is not None:
        return {
            "outcome": "replayed",
            "reason": "request_hash_replay",
            "effect_receipt": existing_effect,
            "manifest_digest": new_digest,
            "prior_manifest_digest": prior_digest,
            "replacement_minted": False,
        }

    receipt_id = f"er-{uuid.uuid4()}"
    deployment_id = f"dep-{uuid.uuid4()}"
    window_id = f"ew-{uuid.uuid4()}"
    effect_key = f"overlay-trial:{binding.proposal_id}:{digest}"

    # Bind deployment_id into entry before write.
    entry = ActiveOverlayEntry(**{**entry_to_dict(entry), "deployment_id": deployment_id})
    new_manifest = build_manifest_with_entry(
        prior=prior,
        entry=entry,
        rollback_generation=rollback_generation,
        created_at=started_iso,
    )
    new_digest = compute_manifest_digest(new_manifest)

    write_activation_pending(
        state_dir,
        {
            "request_hash": req_hash,
            "effect_key": effect_key,
            "proposal_id": binding.proposal_id,
            "manifest_digest": new_digest,
            "prior_manifest_digest": prior_digest,
            "prior_manifest": prior_public,
            "authority_id": authority_id,
            "receipt_id": receipt_id,
            "deployment_id": deployment_id,
            "window_id": window_id,
            "digest": digest,
            "phase": "pre_manifest",
        },
    )

    atomic_replace_manifest(state_dir=state_dir, manifest=new_manifest)

    write_activation_pending(
        state_dir,
        {
            "request_hash": req_hash,
            "effect_key": effect_key,
            "proposal_id": binding.proposal_id,
            "manifest_digest": new_digest,
            "prior_manifest_digest": prior_digest,
            "prior_manifest": prior_public,
            "authority_id": authority_id,
            "receipt_id": receipt_id,
            "deployment_id": deployment_id,
            "window_id": window_id,
            "digest": digest,
            "phase": "post_manifest",
        },
    )

    bundle = effect_store.record_activation_bundle(
        {
            "request_hash": req_hash,
            "effect_key": effect_key,
            "effect_type": "activate_overlay_trial",
            "proposal_id": binding.proposal_id,
            "actor": actor,
            "before_ref": prior_digest,
            "after_ref": new_digest,
            "authority_digest": check.get("authority_digest"),
            "undo_ref": prior_digest,
            "generation_id": rollback_generation,
            "deployment_status": "trial_active",
            "decision_point": trial_policy.decision_point,
            "eligible_target": trial_policy.exposure_cap,
            "started_at": started_iso,
            "ends_at": expires_iso,
            "guardrail_json": trial_policy.guardrail_rollback_thresholds,
            "receipt_id": receipt_id,
            "deployment_id": deployment_id,
            "window_id": window_id,
            "applied_at": started_iso,
            "create_window": True,
        }
    )
    if bundle["outcome"] == "conflict":
        # Fail closed: restore prior manifest.
        restore_prior_manifest(
            state_dir=state_dir,
            prior_manifest=prior,
            expected_prior_digest=prior_digest,
        )
        clear_activation_pending(state_dir)
        return bundle

    consume = effect_store.consume_authority(
        authority_id=authority_id,
        receipt_id=receipt_id,
        now=started_iso,
    )
    clear_activation_pending(state_dir)

    return {
        "outcome": bundle["outcome"],
        "effect_receipt": bundle.get("effect_receipt"),
        "deployment": bundle.get("deployment"),
        "exposure_window": bundle.get("exposure_window"),
        "manifest_digest": new_digest,
        "prior_manifest_digest": prior_digest,
        "prior_manifest": prior_public,
        "overlay_path": str(path),
        "authority_consume": consume,
        "trial_policy": trial_policy.to_public_dict(),
        "deployment_id": deployment_id,
        "request_hash": req_hash,
    }


def rollback_overlay_trial(
    *,
    state_dir: str | Path | None,
    proposal_id: str,
    prior_manifest_digest: str,
    actor: str,
    effect_store: OverlayEffectStore,
    deployment_id: str | None = None,
    reason: str = "operator_rollback",
    prior_manifest: ActiveManifest | Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compensating effect: restore the exact prior manifest; preserve history.

    Rollback is artifact-specific: ``prior_manifest`` must digest to
    ``prior_manifest_digest``. Artifact-agnostic rollback is rejected.
    """
    proposal_id = _require_str(proposal_id, "proposal_id")
    prior_manifest_digest = _require_str(prior_manifest_digest, "prior_manifest_digest")
    actor = _require_str(actor, "actor")
    current = now or datetime.now(timezone.utc)
    applied_at = _now_iso(current)

    live = load_validated_active_overlays(state_dir)
    if live.fail_closed and prior_manifest is None:
        return {
            "outcome": "failed",
            "reason": "fail_closed_requires_prior_manifest",
        }

    live_public = manifest_to_public_dict(live)
    live_digest = prior_digest_for(live) if not live.entries else compute_manifest_digest(
        live_public
    )
    # Always use full digest for non-empty live; EMPTY for empty.
    if live.entries:
        live_digest = compute_manifest_digest(live_public)
    else:
        live_digest = EMPTY_DIGEST

    if prior_manifest is None:
        # Only allow empty restore when prior digest is the empty sentinel.
        prior_manifest = empty_active_manifest(
            rollback_generation=live.rollback_generation,
            created_at=applied_at,
        )
    if isinstance(prior_manifest, ActiveManifest):
        prior_public = manifest_to_public_dict(prior_manifest)
        prior_obj = prior_manifest
    else:
        prior_obj = manifest_from_mapping(prior_manifest)
        prior_public = manifest_to_public_dict(prior_obj)

    if prior_obj.entries:
        computed_prior = compute_manifest_digest(prior_public)
        if computed_prior != prior_manifest_digest:
            raise ActiveOverlayError(
                f"prior_manifest_digest_mismatch:expected={prior_manifest_digest}"
                f":computed={computed_prior}"
            )
    else:
        computed_prior = EMPTY_DIGEST
        if prior_manifest_digest not in {
            EMPTY_DIGEST,
            compute_manifest_digest(prior_public),
        }:
            raise ActiveOverlayError(
                f"prior_manifest_digest_mismatch:expected={prior_manifest_digest}"
                f":computed={EMPTY_DIGEST}"
            )

    req_hash = digest_text(
        _canonical_json(
            {
                "effect_type": "rollback_overlay_trial",
                "live_manifest_digest": live_digest,
                "prior_manifest_digest": prior_manifest_digest,
                "proposal_id": proposal_id,
                "reason": reason,
            }
        )
    )
    existing = effect_store.get_effect_by_request_hash(req_hash)
    if existing is not None:
        return {
            "outcome": "replayed",
            "effect_receipt": existing,
            "replacement_minted": False,
        }

    # Filesystem restore first (artifact-specific prior digest).
    restored_digest = restore_prior_manifest(
        state_dir=state_dir,
        prior_manifest=prior_obj,
        expected_prior_digest=prior_manifest_digest
        if prior_manifest_digest == EMPTY_DIGEST or not prior_obj.entries
        else computed_prior,
    )

    receipt_id = f"er-{uuid.uuid4()}"
    dep_id = deployment_id or f"dep-rb-{uuid.uuid4()}"
    effect_key = f"overlay-rollback:{proposal_id}:{live_digest}:{prior_manifest_digest}"

    bundle = effect_store.record_activation_bundle(
        {
            "request_hash": req_hash,
            "effect_key": effect_key,
            "effect_type": "rollback_overlay_trial",
            "proposal_id": proposal_id,
            "actor": actor,
            "before_ref": live_digest,
            "after_ref": restored_digest,
            "undo_ref": prior_manifest_digest,
            "generation_id": prior_obj.rollback_generation or EMPTY_DIGEST,
            "deployment_status": "rolled_back",
            "decision_point": "rollback",
            "eligible_target": 0,
            "started_at": applied_at,
            "ends_at": applied_at,
            "guardrail_json": {"reason": reason},
            "receipt_id": receipt_id,
            "deployment_id": dep_id,
            "window_id": f"ew-rb-{uuid.uuid4()}",
            "applied_at": applied_at,
            "create_window": False,
        }
    )

    if deployment_id:
        effect_store.mark_deployment_status(
            deployment_id=deployment_id,
            status="rolled_back",
            retired_at=applied_at,
            rollback_ref=prior_manifest_digest,
        )

    return {
        "outcome": bundle["outcome"],
        "effect_receipt": bundle.get("effect_receipt"),
        "deployment": bundle.get("deployment"),
        "restored_manifest_digest": restored_digest,
        "prior_manifest_digest": prior_manifest_digest,
        "reason": reason,
        "request_hash": req_hash,
    }


def expire_active_trials(
    *,
    state_dir: str | Path | None,
    effect_store: OverlayEffectStore,
    actor: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Expire trials past trial_expires_at or exposure budget. Never promote."""
    current = now or datetime.now(timezone.utc)
    applied_at = _now_iso(current)
    live = load_validated_active_overlays(state_dir)
    if live.fail_closed:
        return {
            "outcome": "failed",
            "reason": "active_manifest_fail_closed",
            "expired_count": 0,
        }

    expired_entries: list[ActiveOverlayEntry] = []
    kept: list[ActiveOverlayEntry] = []
    for entry in live.entries:
        should_expire = False
        if entry.status == "trial_active":
            try:
                if current > _parse_iso(entry.trial_expires_at):
                    should_expire = True
            except ValueError:
                should_expire = True
            if entry.exposure_used >= entry.exposure_budget:
                should_expire = True
        if should_expire:
            expired_entries.append(
                ActiveOverlayEntry(**{**entry_to_dict(entry), "status": "expired"})
            )
        else:
            kept.append(entry)

    if not expired_entries:
        return {"outcome": "applied", "expired_count": 0, "changed": False}

    prior_public = manifest_to_public_dict(live)
    prior_digest = compute_manifest_digest(prior_public)
    new_manifest = ActiveManifest(
        schema_version=live.schema_version,
        entries=tuple(kept + expired_entries),
        prior_manifest_digest=prior_digest,
        rollback_generation=live.rollback_generation,
        created_at=applied_at,
        generation_counter=live.generation_counter + 1,
    )
    # Expired entries remain listed but not loadable; optional strip of loadable-only.
    # For load safety, rewrite without loadable expired: keep as expired status.
    new_digest = atomic_replace_manifest(state_dir=state_dir, manifest=new_manifest)

    receipts = []
    for entry in expired_entries:
        req_hash = digest_text(
            _canonical_json(
                {
                    "digest": entry.digest,
                    "effect_type": "expire_overlay_trial",
                    "proposal_id": entry.proposal_id,
                    "at": applied_at,
                }
            )
        )
        # New Deployment row for the expire compensating event; also mark the
        # original trial Deployment expired (never silent promote).
        expire_dep_id = f"dep-ex-{uuid.uuid4()}"
        bundle = effect_store.record_activation_bundle(
            {
                "request_hash": req_hash,
                "effect_key": f"overlay-expire:{entry.proposal_id}:{entry.digest}",
                "effect_type": "expire_overlay_trial",
                "proposal_id": entry.proposal_id,
                "actor": actor,
                "before_ref": prior_digest,
                "after_ref": new_digest,
                "undo_ref": prior_digest,
                "generation_id": entry.rollback_generation,
                "deployment_status": "expired",
                "decision_point": "expiry",
                "eligible_target": 0,
                "started_at": applied_at,
                "ends_at": applied_at,
                "guardrail_json": {"reason": "trial_expired"},
                "deployment_id": expire_dep_id,
                "create_window": False,
            }
        )
        if entry.deployment_id:
            effect_store.mark_deployment_status(
                deployment_id=entry.deployment_id,
                status="expired",
                retired_at=applied_at,
            )
        receipts.append(bundle)

    return {
        "outcome": "applied",
        "expired_count": len(expired_entries),
        "changed": True,
        "manifest_digest": new_digest,
        "receipts": receipts,
        # Explicit: expiry never promotes to permanent deploy.
        "promoted": False,
    }
