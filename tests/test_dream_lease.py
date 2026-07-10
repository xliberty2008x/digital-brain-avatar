"""Fenced MaintenanceLease: DB-time acquire, epoch takeover, stale rejection."""

from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))

from digital_brain_mcp_cypher.maintenance import MaintenanceStore  # noqa: E402

# Reuse fake session from maintenance tests.
from tests.test_mcp_cypher_maintenance import (  # noqa: E402
    GENERATION_ID,
    _FakeMaintSession,
    _store_with,
)


def test_acquire_uses_monotonic_epoch_after_expiry():
    session = _FakeMaintSession()
    store = _store_with(session)

    first = store.acquire_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-a",
            "run_id": "run-a",
            "ttl_seconds": 30,
        }
    )
    assert first["outcome"] == "acquired"
    assert first["epoch"] == 1

    # Still held by A — B cannot acquire.
    held = store.acquire_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-b",
            "run_id": "run-b",
            "ttl_seconds": 30,
        }
    )
    assert held["outcome"] == "held"
    assert held["epoch"] == 1

    # Expire lease (database-time comparison in fake).
    session.advance(31)

    takeover = store.acquire_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-b",
            "run_id": "run-b",
            "ttl_seconds": 30,
        }
    )
    assert takeover["outcome"] == "acquired"
    assert takeover["epoch"] == 2
    assert takeover["previous_epoch"] == 1
    assert takeover["holder_id"] == "host-b"
    assert takeover["run_id"] == "run-b"


def test_expired_holder_cannot_commit_after_new_epoch():
    session = _FakeMaintSession()
    store = _store_with(session)

    a = store.acquire_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-a",
            "run_id": "run-a",
            "ttl_seconds": 10,
        }
    )
    assert a["epoch"] == 1

    dream = store.create_dream_run(
        {
            "id": "run-a",
            "run_id": "run-a",
            "epoch": 1,
            "holder_id": "host-a",
            "harness_generation_id": GENERATION_ID,
        }
    )
    assert dream["outcome"] == "created"

    # A advances one stage under epoch 1.
    stage = store.record_dream_stage(
        {"run_id": "run-a", "epoch": 1, "stage": "leased"}
    )
    assert stage["outcome"] == "recorded"

    # Lease expires; B takes over with epoch 2.
    session.advance(11)
    b = store.acquire_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-b",
            "run_id": "run-b",
            "ttl_seconds": 30,
        }
    )
    assert b["epoch"] == 2

    # Stale A cannot renew, release, stage, or snapshot with epoch 1.
    renew = store.renew_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-a",
            "run_id": "run-a",
            "epoch": 1,
            "ttl_seconds": 30,
        }
    )
    assert renew["outcome"] == "stale_epoch"

    release = store.release_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-a",
            "run_id": "run-a",
            "epoch": 1,
        }
    )
    assert release["outcome"] == "stale_epoch"

    stale_stage = store.record_dream_stage(
        {"run_id": "run-a", "epoch": 1, "stage": "snapshotting"}
    )
    assert stale_stage["outcome"] == "stale_epoch"
    assert session.dreams["run-a"]["stage"] == "leased"

    stale_snap = store.create_evidence_snapshot(
        {
            "id": "snap-stale",
            "dream_id": "run-a",
            "run_id": "run-a",
            "epoch": 1,
            "cutoff_at": "2026-07-10T00:00:00Z",
            "source_ids_digest": "d",
            "harness_generation_id": GENERATION_ID,
        }
    )
    assert stale_snap["outcome"] == "stale_epoch"
    assert "snap-stale" not in session.snapshots

    # Stale A cannot create findings/proposals or record eval/decision/retention.
    assert store.create_finding(
        {
            "id": "find-stale",
            "dream_id": "run-a",
            "snapshot_id": "snap-x",
            "class_key": "x",
            "lane": "memory",
            "summary": "stale",
            "evidence_strength": "tentative",
            "run_id": "run-a",
            "epoch": 1,
        }
    )["outcome"] == "stale_epoch"
    assert "find-stale" not in session.findings

    assert store.create_proposal(
        {
            "id": "prop-stale",
            "kind": "alias",
            "title": "stale",
            "target_ref": "entity:x",
            "evidence_snapshot_id": "snap-x",
            "dream_id": "run-a",
            "run_id": "run-a",
            "epoch": 1,
        }
    )["outcome"] == "stale_epoch"
    assert "prop-stale" not in session.proposals

    assert store.record_evaluation(
        {
            "id": "eval-stale",
            "proposal_id": "prop-x",
            "evaluator_version": "ev-1",
            "baseline_ref": "b",
            "candidate_ref": "c",
            "outcome": "passed",
            "run_id": "run-a",
            "epoch": 1,
        }
    )["outcome"] == "stale_epoch"
    assert "eval-stale" not in session.evaluations

    assert store.record_decision(
        {
            "id": "dec-stale",
            "proposal_id": "prop-x",
            "decision": "approved",
            "proposal_hash": "ph",
            "target_ref": "t",
            "before_fingerprint": "bf",
            "artifact_or_effect_hash": "eh",
            "decided_by": "owner",
            "run_id": "run-a",
            "epoch": 1,
        }
    )["outcome"] == "stale_epoch"
    assert "dec-stale" not in session.decisions

    assert store.record_retention_effect(
        {
            "id": "eff-stale",
            "effect_key": "ret:stale",
            "run_id": "run-a",
            "epoch": 1,
            "target_ref": "Feedback:x",
        }
    )["outcome"] == "stale_epoch"
    assert "eff-stale" not in session.effects


def test_renew_requires_matching_run_id_and_epoch():
    session = _FakeMaintSession()
    store = _store_with(session)
    lease = store.acquire_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-a",
            "run_id": "run-a",
            "ttl_seconds": 60,
        }
    )
    ok = store.renew_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-a",
            "run_id": "run-a",
            "epoch": lease["epoch"],
            "ttl_seconds": 120,
        }
    )
    assert ok["outcome"] == "renewed"

    wrong_run = store.renew_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-a",
            "run_id": "run-other",
            "epoch": lease["epoch"],
            "ttl_seconds": 120,
        }
    )
    assert wrong_run["outcome"] == "stale_epoch"


def test_release_then_reacquire_increments_epoch():
    session = _FakeMaintSession()
    store = _store_with(session)
    first = store.acquire_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-a",
            "run_id": "run-a",
            "ttl_seconds": 60,
        }
    )
    released = store.release_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-a",
            "run_id": "run-a",
            "epoch": first["epoch"],
        }
    )
    assert released["outcome"] == "released"
    # Released lease is past-due; next acquire bumps epoch.
    second = store.acquire_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-a",
            "run_id": "run-a2",
            "ttl_seconds": 60,
        }
    )
    assert second["outcome"] == "acquired"
    assert second["epoch"] == first["epoch"] + 1


def test_same_holder_active_acquire_renews_without_epoch_bump():
    session = _FakeMaintSession()
    store = _store_with(session)
    first = store.acquire_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-a",
            "run_id": "run-a",
            "ttl_seconds": 60,
        }
    )
    again = store.acquire_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-a",
            "run_id": "run-a",
            "ttl_seconds": 60,
        }
    )
    assert again["outcome"] == "renewed"
    assert again["epoch"] == first["epoch"] == 1


def test_stage_and_retention_hooks_require_run_id_plus_epoch():
    """Stage transitions and retention effects need the fence pair."""
    session = _FakeMaintSession()
    store = _store_with(session)
    lease = store.acquire_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-a",
            "run_id": "run-fence",
            "ttl_seconds": 60,
        }
    )
    store.create_dream_run(
        {
            "id": "run-fence",
            "run_id": "run-fence",
            "epoch": lease["epoch"],
            "harness_generation_id": GENERATION_ID,
        }
    )

    with pytest.raises(ValueError):
        store.record_dream_stage({"run_id": "run-fence", "stage": "leased"})

    with pytest.raises(ValueError):
        store.record_dream_stage({"epoch": lease["epoch"], "stage": "leased"})

    ok = store.record_dream_stage(
        {
            "run_id": "run-fence",
            "epoch": lease["epoch"],
            "stage": "leased",
        }
    )
    assert ok["outcome"] == "recorded"

    with pytest.raises(ValueError):
        store.record_retention_effect(
            {"id": "eff-fence", "effect_key": "ret:x", "run_id": "run-fence"}
        )
    with pytest.raises(ValueError):
        store.record_retention_effect(
            {"id": "eff-fence", "effect_key": "ret:x", "epoch": lease["epoch"]}
        )

    ret = store.record_retention_effect(
        {
            "id": "eff-fence",
            "effect_key": "ret:x",
            "run_id": "run-fence",
            "epoch": lease["epoch"],
            "target_ref": "Feedback:x",
        }
    )
    assert ret["outcome"] == "created"
