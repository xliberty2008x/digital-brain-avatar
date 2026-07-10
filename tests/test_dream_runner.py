"""Report-only DreamRun coordinator: stages, resume, buckets, capability."""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))

from digital_brain.maintenance.privacy import assert_no_intimate_fields  # noqa: E402
from digital_brain.maintenance.runner import (  # noqa: E402
    MAINTAINER_ALLOWED_OPERATIONS,
    MAINTAINER_FORBIDDEN_OPERATIONS,
    DreamRunCheckpoint,
    DreamRunner,
    assert_no_activation_capability,
    maintainer_tool_profile,
)
from digital_brain.maintenance.snapshot import load_evidence_fixture  # noqa: E402
from tests.test_mcp_cypher_maintenance import (  # noqa: E402
    _FakeMaintSession,
    _store_with,
)

GENERATION_ID = "hg-" + ("c" * 64)
CORRELATION_KEY = b"runner-test-correlation-key"
FIXTURE = ROOT / "tests" / "fixtures" / "dreams" / "evidence" / "sample_ledger.json"
CUTOFF = "2026-07-10T12:00:00Z"


def _evidence():
    return load_evidence_fixture(FIXTURE)


def _runner(session: _FakeMaintSession | None = None, **kwargs) -> tuple[DreamRunner, _FakeMaintSession]:
    session = session or _FakeMaintSession()
    store = _store_with(session)
    defaults = dict(
        store=store,
        holder_id="host-runner",
        harness_generation_id=GENERATION_ID,
        correlation_key=CORRELATION_KEY,
        base_commit="abc1234",
    )
    defaults.update(kwargs)
    return DreamRunner(**defaults), session


def test_all_tools_maintainer_profile_has_no_activation_capability():
    tools = maintainer_tool_profile(all_tools=True)
    assert_no_activation_capability(tools)
    assert tools == MAINTAINER_ALLOWED_OPERATIONS
    for forbidden in (
        "activate_alias",
        "apply_alias",
        "mint_activation_authority",
        "activate_overlay",
        "publish_deployment",
        "record_retention_effect",
        "compile_patch",
    ):
        assert forbidden not in tools
        assert forbidden in MAINTAINER_FORBIDDEN_OPERATIONS

    runner, _ = _runner()
    profile = runner.tool_profile(all_tools=True)
    assert_no_activation_capability(profile)

    denied = runner.dispatch("apply_alias", {"proposal_id": "x"})
    assert denied["outcome"] == "forbidden"
    denied2 = runner.dispatch("mint_activation_authority", {})
    assert denied2["outcome"] == "forbidden"
    denied3 = runner.dispatch("record_retention_effect", {})
    assert denied3["outcome"] == "forbidden"


def test_report_only_run_produces_three_buckets_and_zero_housekeeping():
    runner, session = _runner()
    result = runner.run(
        _evidence(),
        cutoff_at=CUTOFF,
        run_id="dream-buckets",
        holdout_ids=["re-fail-2"],
        graph_bookmark="bm-run",
    )
    assert result.stage == "completed"
    assert result.processing_mode == "report_only"
    assert result.snapshot_id == "snap-dream-buckets"
    assert result.source_ids_digest

    report = result.report
    assert report["auto_applied_count"] == 0
    buckets = report["buckets"]
    assert buckets["applied_housekeeping"]["count"] == 0
    assert buckets["applied_housekeeping"]["ids"] == []
    assert "waiting_for_owner" in buckets
    assert "deliberately_left_alone" in buckets
    # entity_wrong / miss (non-intimate) wait for owner.
    waiting = set(buckets["waiting_for_owner"]["ids"])
    assert "fb-entity-1" in waiting
    assert "fb-miss-1" in waiting
    # Intimate entity_wrong is left alone (no intimate text in proposals).
    left = set(buckets["deliberately_left_alone"]["ids"])
    assert "fb-intimate-1" in left or "fb-intimate-1" in result.report.get(
        "generation_ids", []
    )
    # Holdout never waits for owner.
    assert "re-fail-2" not in waiting

    public = result.to_public_dict()
    assert_no_intimate_fields(public)
    assert "never surface this intimate quote" not in str(public)

    # Snapshot + findings persisted via store.
    assert "snap-dream-buckets" in session.snapshots
    assert any(k.startswith("find-dream-buckets") for k in session.findings)
    # Pipeline finished.
    assert session.dreams["dream-buckets"]["stage"] == "completed"
    assert result.owner_status in {"needs_review", "completed_clean"}
    if waiting:
        assert result.owner_status == "needs_review"


def test_stage_receipts_cover_full_pipeline():
    runner, session = _runner()
    result = runner.run(
        _evidence(),
        cutoff_at=CUTOFF,
        run_id="dream-stages",
        holdout_ratio=0.0,
    )
    expected = [
        "leased",
        "snapshotting",
        "normalizing",
        "clustering",
        "planning",
        "compiling",
        "validating",
        "publishing",
        "completed",
    ]
    for stage in expected:
        assert result.stage_outcomes.get(stage) == "recorded"
        key = f"dream-stages:{stage}:0"
        assert key in session.stages


def test_crash_resume_replays_stage_receipts_without_duplicate_effects():
    runner, session = _runner()
    run_id = "dream-resume"

    with pytest.raises(DreamRunCheckpoint) as cp:
        runner.run(
            _evidence(),
            cutoff_at=CUTOFF,
            run_id=run_id,
            holdout_ids=["re-fail-3"],
            release_lease=False,
            stop_after_stage="planning",
        )
    assert cp.value.stage == "planning"
    assert cp.value.run_id == run_id
    # Partial receipts exist.
    assert f"{run_id}:planning:0" in session.stages
    assert f"{run_id}:completed:0" not in session.stages
    findings_after_crash = dict(session.findings)
    proposals_after_crash = dict(session.proposals)

    # Resume: same holder/run while lease still active → renewed + stage replay.
    result = runner.run(
        _evidence(),
        cutoff_at=CUTOFF,
        run_id=run_id,
        holdout_ids=["re-fail-3"],
        release_lease=True,
    )
    assert result.stage == "completed"
    assert result.resumed is True
    # Early stages replayed.
    assert result.stage_outcomes["leased"] == "replayed"
    assert result.stage_outcomes["snapshotting"] == "replayed"
    assert result.stage_outcomes["planning"] == "replayed"
    # Later stages newly recorded.
    assert result.stage_outcomes["publishing"] == "recorded"
    assert result.stage_outcomes["completed"] == "recorded"

    # Findings/proposals not duplicated.
    assert set(session.findings) == set(findings_after_crash)
    assert set(session.proposals) == set(proposals_after_crash)
    # Snapshot still single.
    assert sum(1 for k in session.snapshots if k.startswith("snap-")) == 1


def test_same_ledger_policy_same_snapshot_digest_across_runs():
    runner_a, _ = _runner()
    runner_b, session_b = _runner()
    # Different run ids, same evidence/policy → same source_ids_digest.
    r1 = runner_a.run(
        _evidence(),
        cutoff_at=CUTOFF,
        run_id="dream-det-a",
        holdout_ids=["re-fail-2", "re-fail-3"],
        graph_bookmark="bm-x",
    )
    r2 = runner_b.run(
        _evidence(),
        cutoff_at=CUTOFF,
        run_id="dream-det-b",
        holdout_ids=["re-fail-2", "re-fail-3"],
        graph_bookmark="bm-x",
    )
    assert r1.source_ids_digest == r2.source_ids_digest
    assert r1.report["source_counts"]["total"] == r2.report["source_counts"]["total"]


def test_report_contains_only_counts_and_ids():
    runner, _ = _runner()
    result = runner.run(
        _evidence(),
        cutoff_at=CUTOFF,
        run_id="dream-surface",
        holdout_ratio=0.25,
    )
    report = result.report
    assert "buckets" in report
    assert isinstance(report["reviewed_count"], int)
    assert report["auto_applied_count"] == 0
    # No intimate raw fields.
    assert_no_intimate_fields(report)
    assert "never surface this intimate quote" not in str(report)


def test_dispatch_allows_workflow_ops():
    runner, session = _runner()
    lease = runner.dispatch(
        "acquire_maintenance_lease",
        {
            "key": "maintenance",
            "holder_id": "host-runner",
            "run_id": "dream-dispatch",
            "ttl_seconds": 60,
        },
    )
    assert lease["outcome"] == "acquired"
    assert "dream-dispatch" == lease["run_id"]
