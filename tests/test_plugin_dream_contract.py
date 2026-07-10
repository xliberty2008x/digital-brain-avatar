"""Static contract tests for /digital-brain-dream UX and report shape.

Locks:
- command subcommands present with phase gates on try/apply
- no scheduled run / heartbeat / shared-session private proposal queue
- reports: counts, ids, processing_mode — not raw quotes
- approval / application / deployment / effectiveness stay separate
- public DreamRunResult shape from the runner
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "digital-brain-buddy"
DREAM_CMD = PLUGIN / "commands" / "digital-brain-dream.md"
MAINT_SKILL = (
    PLUGIN / "skills" / "digital-brain-buddy-maintenance" / "SKILL.md"
)
QC_CONTRACT = (
    PLUGIN
    / "skills"
    / "digital-brain-buddy-maintenance"
    / "references"
    / "quality-control-contract.md"
)
MAINTAINER_AGENT = PLUGIN / "agents" / "digital-brain-maintainer.md"
DREAM_CLI = ROOT / "scripts" / "digital_brain_dream.py"


def _read(path: pathlib.Path) -> str:
    assert path.is_file(), f"missing required file: {path}"
    return path.read_text(encoding="utf-8")


REQUIRED_SUBCOMMANDS = (
    "run",
    "status",
    "review",
    "show",
    "try",
    "apply",
    "defer",
    "reject",
    "undo",
    "history",
    "privacy",
)


def test_dream_command_declares_all_subcommands():
    text = _read(DREAM_CMD)
    for name in REQUIRED_SUBCOMMANDS:
        assert re.search(rf"\b{name}\b", text), f"missing subcommand {name}"


def test_try_apply_are_phase_gated_operator_only():
    dream = _read(DREAM_CMD)
    skill = _read(MAINT_SKILL)
    combined = dream + "\n" + skill
    assert re.search(r"Phase-gated|phase gate|phase-gated", combined, re.I)
    assert "digital_brain_apply_proposal.py" in combined
    assert "digital_brain_activate_overlay.py" in combined
    assert re.search(
        r"If gates are missing|only expose|operator",
        combined,
        re.I,
    )
    # Must not present try/apply as model MCP tools
    assert not re.search(
        r"(?i)call\s+`?apply_alias`?\s+MCP|mcp\.tool.*apply_alias",
        combined,
    )


def test_no_schedule_no_heartbeat_no_shared_queue():
    combined = "\n".join(
        [
            _read(DREAM_CMD),
            _read(MAINT_SKILL),
            _read(QC_CONTRACT),
            _read(MAINTAINER_AGENT),
        ]
    )
    assert re.search(
        r"no scheduled|No scheduled|not schedule",
        combined,
        re.I,
    )
    assert re.search(r"no heartbeat|No heartbeat|heartbeat.*off", combined, re.I)
    assert re.search(
        r"shared|non-owner",
        combined,
        re.I,
    )
    assert re.search(
        r"private proposal queue|proposal queues",
        combined,
        re.I,
    )


def test_reports_require_counts_ids_processing_mode_not_raw_quotes():
    skill = _read(MAINT_SKILL)
    contract = _read(QC_CONTRACT)
    agent = _read(MAINTAINER_AGENT)
    dream = _read(DREAM_CMD)
    combined = "\n".join([skill, contract, agent, dream])

    assert "processing_mode" in combined
    for bucket in (
        "applied_housekeeping",
        "waiting_for_owner",
        "deliberately_left_alone",
    ):
        assert bucket in combined

    assert re.search(r"counts?.*ids?|ids?.*counts?", combined, re.I)
    assert re.search(
        r"not raw|never.*quote|no quotes|not.*intimate quotes|not paste raw",
        combined,
        re.I,
    )
    assert re.search(r"progressive disclosure", combined, re.I)


def test_lifecycle_messages_kept_separate():
    combined = _read(MAINT_SKILL) + "\n" + _read(DREAM_CMD) + "\n" + _read(
        QC_CONTRACT
    )
    for word in ("Approval", "application", "deployment", "effectiveness"):
        assert re.search(word, combined, re.I), f"missing lifecycle word {word}"
    assert re.search(
        r"separate (durable )?facts|separate (user-facing )?messages|Separate owner messages",
        combined,
        re.I,
    )


def test_dream_cli_exists_and_is_report_only():
    src = _read(DREAM_CLI)
    assert "report-only" in src.lower() or "REPORT_ONLY" in src or "report_only" in src
    assert "maintainer_tool_profile" in src
    assert "assert_no_activation_capability" in src
    assert "processing_mode" in src
    # CLI must print bucket counts/ids, not dump raw evidence
    assert "waiting_for_owner" in src
    assert "deliberately_left_alone" in src


def test_public_report_shape_from_runner():
    from digital_brain.maintenance.runner import (
        DreamRunResult,
        ReportBuckets,
        maintainer_tool_profile,
        assert_no_activation_capability,
    )

    buckets = ReportBuckets(
        applied_housekeeping=[],
        waiting_for_owner=["prop-1"],
        deliberately_left_alone=["amb-1"],
    )
    result = DreamRunResult(
        run_id="dream-test",
        epoch=1,
        stage="completed",
        owner_status="waiting_for_owner",
        processing_mode="report_only",
        snapshot_id="snap-1",
        source_ids_digest="abc",
        report={
            "reviewed_count": 3,
            "auto_applied_count": 0,
            "buckets": buckets.to_dict(),
        },
        finding_ids=["f-1"],
        proposal_ids=["prop-1"],
    )
    public = result.to_public_dict()
    assert public["processing_mode"] == "report_only"
    assert public["run_id"] == "dream-test"
    assert public["proposal_ids"] == ["prop-1"]
    report = public["report"]
    assert report["auto_applied_count"] == 0
    assert report["buckets"]["waiting_for_owner"]["count"] == 1
    assert report["buckets"]["waiting_for_owner"]["ids"] == ["prop-1"]
    # Public packet must not invent raw quote fields
    blob = str(public)
    assert "raw_text" not in blob
    assert "journal_body" not in blob

    assert_no_activation_capability(maintainer_tool_profile(all_tools=True))


def test_default_mode_is_manual_report_only():
    skill = _read(MAINT_SKILL)
    assert re.search(r"report-only", skill, re.I)
    assert re.search(r"manual", skill, re.I)
    assert re.search(r"local-only|local only", skill, re.I)


def test_maintainer_skill_points_at_cli():
    skill = _read(MAINT_SKILL)
    dream = _read(DREAM_CMD)
    assert "digital_brain_dream.py" in skill
    assert "digital_brain_dream.py" in dream
