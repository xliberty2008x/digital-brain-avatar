"""Crash reconciliation between FS overlay manifest and graph EffectReceipt."""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from digital_brain.maintenance.activation import (  # noqa: E402
    OverlayActivationBinding,
    OverlayEffectStore,
    TrialPolicy,
    activate_overlay_trial,
    write_activation_pending,
)
from digital_brain.maintenance.active_overlays import (  # noqa: E402
    load_validated_active_overlays,
    prior_digest_for,
    resolve_loadable_overlays,
)
from digital_brain.maintenance.alias_effects import AliasEffectStore  # noqa: E402
from digital_brain.maintenance.models import EMPTY_DIGEST, digest_text  # noqa: E402
from digital_brain.maintenance.reconcile import (  # noqa: E402
    reconcile_overlay_activation,
    same_request_replays_without_duplicate,
)

# Reuse fake session from activation tests.
from tests.test_overlay_activation import (  # noqa: E402
    _FakeSession,
    _artifact,
    _binding,
    _mint,
    _store,
    _trial_policy,
)


def test_same_request_hash_replays_without_duplicate_activation(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    session = _FakeSession()
    alias_store, effect_store = _store(session)
    binding = _binding()
    content = _artifact()
    mint = _mint(alias_store, binding)

    first = activate_overlay_trial(
        state_dir=state,
        binding=binding,
        artifact_md=content,
        trial_policy=_trial_policy(),
        authority_id=mint["authority_id"],
        nonce=mint["nonce"],
        actor="owner@test",
        rollback_generation="hg-prior",
        alias_store=alias_store,
        effect_store=effect_store,
    )
    assert first["outcome"] == "applied"
    req = first["request_hash"]
    n_effects = len(session.effects)
    n_deps = len(session.deployments)

    # Reconcile same request → replayed, no new nodes.
    rec = reconcile_overlay_activation(
        state_dir=state,
        effect_store=effect_store,
        request_hash=req,
    )
    assert rec["outcome"] == "replayed"
    assert rec["duplicate_activation"] is False
    assert len(session.effects) == n_effects
    assert len(session.deployments) == n_deps

    check = same_request_replays_without_duplicate(
        state_dir=state,
        effect_store=effect_store,
        request_hash=req,
    )
    assert check["duplicate_activation"] is False
    assert check["both_replayed_or_idle"] is True


def test_post_manifest_crash_completes_graph_receipt(tmp_path: pathlib.Path):
    """FS advanced, graph missing → reconcile writes receipt once."""
    state = tmp_path / "state"
    session = _FakeSession()
    alias_store, effect_store = _store(session)
    binding = _binding()
    content = _artifact()
    mint = _mint(alias_store, binding)

    # Partial: stage + manifest via activate, but simulate lost graph by
    # removing effects after a normal activate is too late. Instead: stage
    # manually through activate then delete graph rows and restore pending.
    act = activate_overlay_trial(
        state_dir=state,
        binding=binding,
        artifact_md=content,
        trial_policy=_trial_policy(),
        authority_id=mint["authority_id"],
        nonce=mint["nonce"],
        actor="owner@test",
        rollback_generation="hg-prior",
        alias_store=alias_store,
        effect_store=effect_store,
    )
    assert act["outcome"] == "applied"
    req = act["request_hash"]

    # Simulate lost graph after FS write: wipe effects but keep FS + re-pending.
    wiped = dict(session.effects)
    session.effects.clear()
    session.effects_by_key.clear()
    session.effects_by_request.clear()
    # Keep deployment/windows empty too
    session.deployments.clear()
    session.exposure_windows.clear()
    # Authority stays consumed — reconcile still completes receipt
    write_activation_pending(
        state,
        {
            "request_hash": req,
            "effect_key": f"overlay-trial:{binding.proposal_id}:{binding.artifact_hash}",
            "proposal_id": binding.proposal_id,
            "manifest_digest": act["manifest_digest"],
            "prior_manifest_digest": act["prior_manifest_digest"],
            "prior_manifest": act["prior_manifest"],
            "authority_id": mint["authority_id"],
            "receipt_id": "er-reconcile-1",
            "deployment_id": "dep-reconcile-1",
            "window_id": "ew-reconcile-1",
            "digest": binding.artifact_hash,
            "phase": "post_manifest",
        },
    )

    rec = reconcile_overlay_activation(
        state_dir=state,
        effect_store=effect_store,
        request_hash=req,
        actor="owner@test",
    )
    assert rec["outcome"] in {"applied", "replayed"}
    assert rec.get("duplicate_activation") is False
    assert effect_store.get_effect_by_request_hash(req) is not None
    # Second reconcile is pure replay
    rec2 = reconcile_overlay_activation(
        state_dir=state,
        effect_store=effect_store,
        request_hash=req,
    )
    assert rec2["outcome"] == "replayed"
    assert len(session.effects) == 1
    assert wiped  # we did wipe something originally


def test_pre_manifest_pending_is_abandoned_without_activation(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    session = _FakeSession()
    _alias_store, effect_store = _store(session)
    req = "ab" * 32
    write_activation_pending(
        state,
        {
            "request_hash": req,
            "phase": "pre_manifest",
            "proposal_id": "prop-x",
            "prior_manifest_digest": EMPTY_DIGEST,
        },
    )
    rec = reconcile_overlay_activation(
        state_dir=state,
        effect_store=effect_store,
        request_hash=req,
    )
    assert rec["outcome"] == "idle"
    assert rec["reason"] == "pre_manifest_abandoned"
    assert effect_store.get_effect_by_request_hash(req) is None
    assert load_validated_active_overlays(state_dir=state).entries == ()


def test_fs_mismatch_restores_prior(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    session = _FakeSession()
    _alias_store, effect_store = _store(session)
    # Write empty prior as live
    from digital_brain.maintenance.active_overlays import (
        atomic_replace_manifest,
        empty_active_manifest,
    )

    empty = empty_active_manifest(rollback_generation="hg-prior")
    atomic_replace_manifest(state_dir=state, manifest=empty)

    req = "cd" * 32
    write_activation_pending(
        state,
        {
            "request_hash": req,
            "phase": "post_manifest",
            "proposal_id": "prop-x",
            "manifest_digest": "ff" * 32,  # does not match live empty
            "prior_manifest_digest": EMPTY_DIGEST,
            "prior_manifest": {
                "schema_version": "1",
                "entries": [],
                "prior_manifest_digest": EMPTY_DIGEST,
                "rollback_generation": "hg-prior",
                "created_at": "2026-07-10T12:00:00Z",
                "generation_counter": 0,
            },
        },
    )
    rec = reconcile_overlay_activation(
        state_dir=state,
        effect_store=effect_store,
        request_hash=req,
    )
    assert rec["outcome"] == "restored"
    assert load_validated_active_overlays(state_dir=state).entries == ()
    assert resolve_loadable_overlays(state_dir=state) == []
