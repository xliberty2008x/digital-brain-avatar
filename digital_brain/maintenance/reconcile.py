"""Reconcile filesystem overlay activation with graph EffectReceipts.

Crashes between atomic manifest replacement and graph receipt creation are
healed without duplicate activation. Same request_hash always replays.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from digital_brain.maintenance.activation import (
    OverlayEffectStore,
    clear_activation_pending,
    read_activation_pending,
)
from digital_brain.maintenance.active_overlays import (
    acquire_activation_lock,
    compute_manifest_digest,
    empty_active_manifest,
    load_raw_manifest,
    load_validated_active_overlays,
    manifest_from_mapping,
    manifest_to_public_dict,
    restore_prior_manifest,
)
from digital_brain.maintenance.models import EMPTY_DIGEST


def reconcile_overlay_activation(
    *,
    state_dir: str | Path | None,
    effect_store: OverlayEffectStore,
    request_hash: str | None = None,
    actor: str = "reconcile",
) -> dict[str, Any]:
    """Serialize reconciliation with activation, rollback, and expiry."""
    lock_handle = acquire_activation_lock(state_dir)
    try:
        return _reconcile_overlay_activation_locked(
            state_dir=state_dir,
            effect_store=effect_store,
            request_hash=request_hash,
            actor=actor,
        )
    finally:
        try:
            lock_handle.close()
        except OSError:
            pass


def _reconcile_overlay_activation_locked(
    *,
    state_dir: str | Path | None,
    effect_store: OverlayEffectStore,
    request_hash: str | None = None,
    actor: str = "reconcile",
) -> dict[str, Any]:
    """Reconcile pending activation between FS manifest and graph receipt.

    Outcomes:
    - ``replayed``: EffectReceipt already exists for the request_hash
    - ``applied``: completed missing graph side for a post_manifest pending
    - ``restored``: fail-closed restore of prior when graph conflict / missing FS
    - ``idle``: nothing pending
    - ``failed``: unrecoverable without operator input
    """
    pending = read_activation_pending(state_dir)
    if pending is None and not request_hash:
        return {"outcome": "idle", "reason": "no_pending_activation"}

    req = request_hash or (pending or {}).get("request_hash")
    if not req:
        return {"outcome": "failed", "reason": "missing_request_hash"}

    operation = str((pending or {}).get("operation") or "activate_overlay_trial")
    existing = effect_store.get_effect_by_request_hash(str(req))
    if existing is not None and operation == "activate_overlay_trial":
        # Graph already durable — clear stale pending marker if any.
        if pending and str(pending.get("request_hash")) == str(req):
            clear_activation_pending(state_dir)
        return {
            "outcome": "replayed",
            "reason": "request_hash_already_receipted",
            "effect_receipt": existing,
            "replacement_minted": False,
            "duplicate_activation": False,
        }

    if pending is None:
        return {
            "outcome": "idle",
            "reason": "no_pending_and_no_receipt",
            "request_hash": req,
        }

    phase = str(pending.get("phase") or "")
    expected_manifest_digest = pending.get("manifest_digest")
    prior_digest = str(pending.get("prior_manifest_digest") or EMPTY_DIGEST)
    prior_manifest = pending.get("prior_manifest")

    raw = load_raw_manifest(state_dir)
    live_digest = None
    if raw is not None:
        try:
            live_digest = compute_manifest_digest(raw)
        except Exception:
            live_digest = None

    if operation in {"rollback_overlay_trial", "expire_overlay_trial"}:
        payloads_raw = pending.get("bundle_payloads")
        if payloads_raw is None and isinstance(pending.get("bundle_payload"), Mapping):
            payloads_raw = [pending["bundle_payload"]]
        payloads = list(payloads_raw or [])
        if not payloads or not all(isinstance(p, Mapping) for p in payloads):
            return {"outcome": "failed", "reason": "pending_effect_payload_missing"}

        if expected_manifest_digest and live_digest == expected_manifest_digest:
            bundles: list[dict[str, Any]] = []
            for raw_payload in payloads:
                payload = dict(raw_payload)
                payload["actor"] = payload.get("actor") or actor
                existing_effect = effect_store.get_effect_by_request_hash(
                    str(payload["request_hash"])
                )
                bundle = (
                    {"outcome": "replayed", "effect_receipt": existing_effect}
                    if existing_effect is not None
                    else effect_store.record_activation_bundle(payload)
                )
                if bundle["outcome"] not in {"applied", "replayed"}:
                    return {
                        "outcome": "failed",
                        "reason": "pending_effect_receipt_conflict",
                        "bundle": bundle,
                    }
                bundles.append(bundle)
            for update in pending.get("deployment_status_updates") or []:
                if isinstance(update, Mapping) and update.get("deployment_id"):
                    effect_store.mark_deployment_status(**dict(update))
            clear_activation_pending(state_dir)
            return {
                "outcome": "applied",
                "reason": f"completed_{operation}_receipts",
                "bundles": bundles,
                "duplicate_activation": False,
            }

        if phase == "pre_manifest" and (
            live_digest is None or live_digest == prior_digest
        ):
            clear_activation_pending(state_dir)
            return {
                "outcome": "idle",
                "reason": f"pre_manifest_{operation}_abandoned",
            }

        if prior_manifest is not None and live_digest is not None:
            try:
                restore_prior_manifest(
                    state_dir=state_dir,
                    prior_manifest=prior_manifest,
                    expected_prior_digest=prior_digest,
                    expected_live_digest=live_digest,
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "outcome": "failed",
                    "reason": f"{operation}_restore_failed",
                    "error": str(exc),
                }
            clear_activation_pending(state_dir)
            return {
                "outcome": "restored",
                "reason": f"{operation}_fs_mismatch_restored_prior",
            }
        return {"outcome": "failed", "reason": f"{operation}_unrecoverable"}

    # post_manifest: FS advanced; graph missing → complete graph side.
    if phase == "post_manifest" and expected_manifest_digest:
        if live_digest == expected_manifest_digest:
            bundle = effect_store.record_activation_bundle(
                {
                    "request_hash": str(req),
                    "effect_key": pending.get("effect_key")
                    or f"overlay-reconcile:{pending.get('proposal_id')}",
                    "effect_type": "activate_overlay_trial",
                    "proposal_id": pending.get("proposal_id") or "unknown",
                    "actor": actor,
                    "before_ref": prior_digest,
                    "after_ref": expected_manifest_digest,
                    "undo_ref": prior_digest,
                    "generation_id": (
                        (prior_manifest or {}).get("rollback_generation")
                        if isinstance(prior_manifest, Mapping)
                        else EMPTY_DIGEST
                    )
                    or EMPTY_DIGEST,
                    "deployment_status": "trial_active",
                    "decision_point": "reconcile",
                    "eligible_target": 0,
                    "receipt_id": pending.get("receipt_id"),
                    "deployment_id": pending.get("deployment_id"),
                    "window_id": pending.get("window_id"),
                    "create_window": True,
                    "guardrail_json": {"reconciled": True},
                }
            )
            if bundle["outcome"] in {"applied", "replayed"}:
                # Consume authority if still minted.
                auth_id = pending.get("authority_id")
                if auth_id and bundle.get("effect_receipt"):
                    effect_store.consume_authority(
                        authority_id=str(auth_id),
                        receipt_id=str(bundle["effect_receipt"]["id"]),
                    )
                clear_activation_pending(state_dir)
                return {
                    "outcome": bundle["outcome"],
                    "reason": "completed_graph_receipt",
                    "effect_receipt": bundle.get("effect_receipt"),
                    "deployment": bundle.get("deployment"),
                    "exposure_window": bundle.get("exposure_window"),
                    "duplicate_activation": False,
                }
            # Graph conflict → fail closed to prior.
            if prior_manifest is not None:
                restore_prior_manifest(
                    state_dir=state_dir,
                    prior_manifest=prior_manifest
                    if isinstance(prior_manifest, Mapping)
                    else empty_active_manifest(),
                    expected_prior_digest=prior_digest
                    if prior_digest != EMPTY_DIGEST
                    else compute_manifest_digest(
                        prior_manifest
                        if isinstance(prior_manifest, Mapping)
                        else manifest_to_public_dict(empty_active_manifest())
                    ),
                )
            clear_activation_pending(state_dir)
            return {
                "outcome": "restored",
                "reason": "graph_conflict_restored_prior",
                "bundle": bundle,
            }

        # FS does not match expected post-manifest digest → restore prior.
        if prior_manifest is not None:
            try:
                restore_prior_manifest(
                    state_dir=state_dir,
                    prior_manifest=prior_manifest
                    if isinstance(prior_manifest, Mapping)
                    else empty_active_manifest(),
                    expected_prior_digest=(
                        prior_digest
                        if prior_digest != EMPTY_DIGEST
                        else compute_manifest_digest(
                            prior_manifest
                            if isinstance(prior_manifest, Mapping)
                            else manifest_to_public_dict(empty_active_manifest())
                        )
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "outcome": "failed",
                    "reason": "restore_failed",
                    "error": str(exc),
                }
        clear_activation_pending(state_dir)
        return {
            "outcome": "restored",
            "reason": "fs_mismatch_restored_prior",
            "live_digest": live_digest,
            "expected_manifest_digest": expected_manifest_digest,
        }

    # pre_manifest: crash window includes "manifest replaced but post_manifest
    # marker not written". If live FS already matches expected new digest,
    # complete the graph side (treat as post_manifest). Otherwise abandon only
    # when FS is still prior; restore on unexpected mismatch.
    if phase == "pre_manifest":
        if expected_manifest_digest and live_digest == expected_manifest_digest:
            # Fall through by re-running post_manifest logic inline.
            bundle = effect_store.record_activation_bundle(
                {
                    "request_hash": str(req),
                    "effect_key": pending.get("effect_key")
                    or f"overlay-reconcile:{pending.get('proposal_id')}",
                    "effect_type": "activate_overlay_trial",
                    "proposal_id": pending.get("proposal_id") or "unknown",
                    "actor": actor,
                    "before_ref": prior_digest,
                    "after_ref": expected_manifest_digest,
                    "undo_ref": prior_digest,
                    "generation_id": (
                        (prior_manifest or {}).get("rollback_generation")
                        if isinstance(prior_manifest, Mapping)
                        else EMPTY_DIGEST
                    )
                    or EMPTY_DIGEST,
                    "deployment_status": "trial_active",
                    "decision_point": "reconcile",
                    "eligible_target": 0,
                    "receipt_id": pending.get("receipt_id"),
                    "deployment_id": pending.get("deployment_id"),
                    "window_id": pending.get("window_id"),
                    "create_window": True,
                    "guardrail_json": {
                        "reconciled": True,
                        "recovered_from": "pre_manifest_crash_window",
                    },
                }
            )
            if bundle["outcome"] in {"applied", "replayed"}:
                auth_id = pending.get("authority_id")
                if auth_id and bundle.get("effect_receipt"):
                    effect_store.consume_authority(
                        authority_id=str(auth_id),
                        receipt_id=str(bundle["effect_receipt"]["id"]),
                    )
                clear_activation_pending(state_dir)
                return {
                    "outcome": bundle["outcome"],
                    "reason": "completed_graph_receipt_from_pre_manifest",
                    "effect_receipt": bundle.get("effect_receipt"),
                    "deployment": bundle.get("deployment"),
                    "exposure_window": bundle.get("exposure_window"),
                    "duplicate_activation": False,
                }
            if prior_manifest is not None:
                restore_prior_manifest(
                    state_dir=state_dir,
                    prior_manifest=prior_manifest
                    if isinstance(prior_manifest, Mapping)
                    else empty_active_manifest(),
                    expected_prior_digest=prior_digest
                    if prior_digest != EMPTY_DIGEST
                    else compute_manifest_digest(
                        prior_manifest
                        if isinstance(prior_manifest, Mapping)
                        else manifest_to_public_dict(empty_active_manifest())
                    ),
                )
            clear_activation_pending(state_dir)
            return {
                "outcome": "restored",
                "reason": "pre_manifest_graph_conflict_restored_prior",
                "bundle": bundle,
            }
        # Live FS still prior (or empty) → safe abandon without restore needed.
        if live_digest is None or live_digest == prior_digest:
            clear_activation_pending(state_dir)
            return {
                "outcome": "idle",
                "reason": "pre_manifest_abandoned",
                "request_hash": req,
                "duplicate_activation": False,
            }
        # Unexpected live digest: restore prior if available.
        if prior_manifest is not None:
            try:
                restore_prior_manifest(
                    state_dir=state_dir,
                    prior_manifest=prior_manifest
                    if isinstance(prior_manifest, Mapping)
                    else empty_active_manifest(),
                    expected_prior_digest=prior_digest
                    if prior_digest != EMPTY_DIGEST
                    else compute_manifest_digest(
                        prior_manifest
                        if isinstance(prior_manifest, Mapping)
                        else manifest_to_public_dict(empty_active_manifest())
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "outcome": "failed",
                    "reason": "pre_manifest_restore_failed",
                    "error": str(exc),
                }
        clear_activation_pending(state_dir)
        return {
            "outcome": "restored",
            "reason": "pre_manifest_unexpected_fs_restored_prior",
            "live_digest": live_digest,
            "expected_manifest_digest": expected_manifest_digest,
        }

    return {
        "outcome": "failed",
        "reason": "unrecognized_pending_phase",
        "phase": phase,
        "request_hash": req,
    }


def same_request_replays_without_duplicate(
    *,
    state_dir: str | Path | None,
    effect_store: OverlayEffectStore,
    request_hash: str,
) -> dict[str, Any]:
    """Convenience: replaying the same request_hash never double-activates."""
    first = reconcile_overlay_activation(
        state_dir=state_dir,
        effect_store=effect_store,
        request_hash=request_hash,
    )
    second = reconcile_overlay_activation(
        state_dir=state_dir,
        effect_store=effect_store,
        request_hash=request_hash,
    )
    return {
        "first": first,
        "second": second,
        "duplicate_activation": False,
        "both_replayed_or_idle": (
            first.get("outcome") in {"replayed", "idle", "applied"}
            and second.get("outcome") in {"replayed", "idle"}
        ),
    }
