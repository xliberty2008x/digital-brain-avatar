"""Legal/illegal workflow transitions, provenance, and MaintenanceStore contracts.

State-transition rules are tested first (pure models). Store tests use an
in-memory fake session that implements the subset of Cypher the store emits.
"""

from __future__ import annotations

import ast
import asyncio
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))

from digital_brain.maintenance.models import (  # noqa: E402
    ActivationAuthority,
    DREAM_PIPELINE_STAGES,
    Decision,
    EvaluationReceipt,
    EffectReceipt,
    ExposureWindow,
    IllegalTransitionError,
    assert_legal_authority_transition,
    assert_legal_dream_stage_transition,
    assert_legal_owner_status_transition,
    assert_no_absorption_field,
    is_legal_dream_stage_transition,
    stage_idempotency_key,
)
from digital_brain_mcp_cypher.maintenance import (  # noqa: E402
    MaintenanceStore,
)
from digital_brain_mcp_cypher.quality import PROTECTED_QUALITY_LABELS  # noqa: E402
from digital_brain_mcp_cypher.quality_control_api import (  # noqa: E402
    COORDINATOR_FORBIDDEN_MCP_TOOL_NAMES,
    WORKFLOW_OPERATIONS,
)


GENERATION_ID = "hg-" + ("b" * 64)


# ---------------------------------------------------------------------------
# Pure transition rules (must pass before store behaviour is trusted)
# ---------------------------------------------------------------------------


def test_dream_pipeline_legal_forward_and_replay():
    assert is_legal_dream_stage_transition(None, "queued")
    for i in range(len(DREAM_PIPELINE_STAGES) - 1):
        assert is_legal_dream_stage_transition(
            DREAM_PIPELINE_STAGES[i], DREAM_PIPELINE_STAGES[i + 1]
        )
        # Idempotent re-record of the same stage (crash/replay).
        assert is_legal_dream_stage_transition(
            DREAM_PIPELINE_STAGES[i], DREAM_PIPELINE_STAGES[i]
        )


def test_dream_pipeline_illegal_skips_and_backwards():
    assert not is_legal_dream_stage_transition(None, "leased")
    assert not is_legal_dream_stage_transition("queued", "snapshotting")
    assert not is_legal_dream_stage_transition("snapshotting", "queued")
    assert not is_legal_dream_stage_transition("completed", "failed")
    assert not is_legal_dream_stage_transition("failed", "queued")
    with pytest.raises(IllegalTransitionError):
        assert_legal_dream_stage_transition("planning", "publishing")


def test_dream_terminal_failure_paths_from_any_nonterminal():
    for stage in ("queued", "leased", "clustering", "publishing"):
        for terminal in ("failed", "aborted", "lease_lost"):
            assert is_legal_dream_stage_transition(stage, terminal)


def test_owner_status_and_authority_transitions():
    assert_legal_owner_status_transition(None, "scheduled")
    assert_legal_owner_status_transition("scheduled", "running")
    assert_legal_owner_status_transition("running", "needs_review")
    assert_legal_owner_status_transition("needs_review", "completed_clean")
    with pytest.raises(IllegalTransitionError):
        assert_legal_owner_status_transition("completed_clean", "running")

    assert_legal_authority_transition(None, "minted")
    assert_legal_authority_transition("minted", "consumed")
    assert_legal_authority_transition("minted", "revoked")
    with pytest.raises(IllegalTransitionError):
        assert_legal_authority_transition("consumed", "minted")


def test_observation_proposal_decision_application_are_separate_types():
    # Distinct dataclasses — never collapse into one mutable workflow row.
    assert EvaluationReceipt.__name__ != Decision.__name__
    assert Decision.__name__ != EffectReceipt.__name__
    assert EffectReceipt.__name__ != ExposureWindow.__name__
    assert ActivationAuthority.__dataclass_fields__  # type: ignore[attr-defined]
    required_authority = {
        "nonce_digest",
        "proposal_hash",
        "before_fingerprint",
        "artifact_or_effect_hash",
        "target_ref",
        "approver",
        "status",
        "expires_at",
        "request_fingerprint",
        "consumption_receipt_id",
        "reconciliation_receipt_id",
    }
    assert required_authority <= set(ActivationAuthority.__dataclass_fields__)


def test_forbidden_absorbed_by_dream_id_field():
    with pytest.raises(ValueError, match="absorbed_by_dream_id"):
        assert_no_absorption_field({"id": "x", "absorbed_by_dream_id": "dream-1"})


def test_stage_idempotency_key_stable():
    a = stage_idempotency_key(run_id="run-1", stage="snapshotting", attempt=0)
    b = stage_idempotency_key(run_id="run-1", stage="snapshotting", attempt=0)
    assert a == b
    assert a != stage_idempotency_key(run_id="run-1", stage="normalizing", attempt=0)


def test_workflow_labels_protected_and_tools_forbidden():
    labels = {x.lower() for x in PROTECTED_QUALITY_LABELS}
    for required in (
        "dreamrun",
        "dreamstagereceipt",
        "evidencesnapshot",
        "finding",
        "proposal",
        "evaluationreceipt",
        "decision",
        "activationauthority",
        "maintenancelease",
        "evidenceref",
    ):
        assert required in labels
    for name in WORKFLOW_OPERATIONS:
        assert name in COORDINATOR_FORBIDDEN_MCP_TOOL_NAMES


# ---------------------------------------------------------------------------
# Fake Neo4j for MaintenanceStore
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row

    def single(self):
        return self.row

    def consume(self) -> None:
        return None


class _FakeMaintSession:
    """Minimal Cypher interpreter for maintenance store unit tests."""

    def __init__(self) -> None:
        self.now = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
        self.leases: dict[str, dict[str, Any]] = {}
        self.dreams: dict[str, dict[str, Any]] = {}
        self.stages: dict[str, dict[str, Any]] = {}
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.findings: dict[str, dict[str, Any]] = {}
        self.proposals: dict[str, dict[str, Any]] = {}
        self.evaluations: dict[str, dict[str, Any]] = {}
        self.decisions: dict[str, dict[str, Any]] = {}
        self.evidence_refs: dict[str, dict[str, Any]] = {}
        # (snapshot_id, evidence_id) -> rel props
        self.includes: dict[tuple[str, str], dict[str, Any]] = {}
        # (finding_id, evidence_id)
        self.uses_evidence: set[tuple[str, str]] = set()
        # (proposal_id, finding_id)
        self.supported_by: set[tuple[str, str]] = set()
        self.calls: list[str] = []

    def execute_write(self, fn):  # noqa: ANN001
        return fn(self)

    def write_transaction(self, fn):  # noqa: ANN001
        return fn(self)

    def _ts(self) -> str:
        return self.now.isoformat().replace("+00:00", "Z")

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)

    def _lease_view(self, node: dict[str, Any]) -> dict[str, Any]:
        return {
            "key": node["key"],
            "holder_id": node["holder_id"],
            "run_id": node["run_id"],
            "epoch": node["epoch"],
            "lease_until": node["lease_until_dt"].isoformat(),
            "heartbeat_at": node["heartbeat_at_dt"].isoformat(),
            "expired": node["lease_until_dt"] < self.now,
            "db_now": self._ts(),
        }

    def run(self, query: str, params: dict[str, Any] | None = None) -> _Result:
        params = params or {}
        self.calls.append(" ".join(query.split())[:120])
        q = " ".join(query.split())

        if "CREATE CONSTRAINT" in q:
            return _Result(None)

        # ---- Mutations first (must not fall through to generic MATCH) ----

        if "CREATE (l:Operational:MaintenanceLease)" in q:
            key = params["key"]
            until = self.now + timedelta(seconds=int(params["ttl"]))
            node = {
                "key": key,
                "id": key,
                "holder_id": params["holder_id"],
                "run_id": params["run_id"],
                "epoch": 1,
                "lease_until_dt": until,
                "heartbeat_at_dt": self.now,
            }
            self.leases[key] = node
            return _Result(self._lease_view(node))

        if (
            "MATCH (l:Operational:MaintenanceLease {key:" in q
            and "SET l.lease_until = datetime() + duration" in q
        ):
            # Renew: requires holder/run/epoch + not expired.
            key = params["key"]
            node = self.leases.get(key)
            if (
                node
                and node["holder_id"] == params.get("holder_id")
                and node["run_id"] == params.get("run_id")
                and int(node["epoch"]) == int(params.get("epoch", -1))
                and node["lease_until_dt"] >= self.now
            ):
                node["lease_until_dt"] = self.now + timedelta(seconds=int(params["ttl"]))
                node["heartbeat_at_dt"] = self.now
                return _Result(self._lease_view(node))
            return _Result(None)

        if (
            "MATCH (l:Operational:MaintenanceLease {key:" in q
            and "$old_epoch" in q
            and "SET l.holder_id" in q
        ):
            # Takeover after expiry with monotonic epoch.
            key = params["key"]
            node = self.leases.get(key)
            if (
                node
                and int(node["epoch"]) == int(params["old_epoch"])
                and node["lease_until_dt"] < self.now
            ):
                node["holder_id"] = params["holder_id"]
                node["run_id"] = params["run_id"]
                node["epoch"] = int(params["new_epoch"])
                node["lease_until_dt"] = self.now + timedelta(seconds=int(params["ttl"]))
                node["heartbeat_at_dt"] = self.now
                return _Result(self._lease_view(node))
            return _Result(None)

        if (
            "MATCH (l:Operational:MaintenanceLease {key:" in q
            and "SET l.lease_until = datetime() - duration" in q
        ):
            key = params["key"]
            node = self.leases.get(key)
            if (
                node
                and node["holder_id"] == params["holder_id"]
                and node["run_id"] == params["run_id"]
                and int(node["epoch"]) == int(params["epoch"])
            ):
                node["lease_until_dt"] = self.now - timedelta(seconds=1)
                node["heartbeat_at_dt"] = self.now
                return _Result(
                    {
                        "key": key,
                        "epoch": node["epoch"],
                        "lease_until": node["lease_until_dt"].isoformat(),
                    }
                )
            return _Result(None)

        if "CREATE (d:Operational:DreamRun)" in q:
            rid = params["id"]
            node = {
                "id": rid,
                "stage": "queued",
                "owner_status": "scheduled",
                "lease_epoch": params["epoch"],
                "lease_key": params["lease_key"],
                "harness_generation_id": params["harness_generation_id"],
                "request_fingerprint": params["request_fingerprint"],
                "holder_id": params.get("holder_id"),
                "started_at": self._ts(),
            }
            self.dreams[rid] = node
            return _Result(
                {
                    "id": rid,
                    "stage": "queued",
                    "owner_status": "scheduled",
                    "lease_epoch": params["epoch"],
                    "request_fingerprint": params["request_fingerprint"],
                    "started_at": self._ts(),
                }
            )

        if "CREATE (s:Operational:DreamStageReceipt)" in q and "SET d.stage = $stage" in q:
            sk = params["stage_key"]
            rid = params["run_id"]
            dream = self.dreams[rid]
            self.stages[sk] = {
                "id": sk,
                "run_id": rid,
                "stage": params["stage"],
                "lease_epoch": params["epoch"],
                "request_fingerprint": params["fp"],
                "outcome": "recorded",
            }
            dream["stage"] = params["stage"]
            dream["attempt"] = params["attempt"]
            if params.get("owner_status") is not None:
                dream["owner_status"] = params["owner_status"]
            return _Result(
                {
                    "stage_key": sk,
                    "stage": dream["stage"],
                    "owner_status": dream["owner_status"],
                    "lease_epoch": dream["lease_epoch"],
                }
            )

        if "CREATE (s:Operational:DreamStageReceipt)" in q:
            sk = params["stage_key"]
            self.stages[sk] = {
                "id": sk,
                "run_id": params["run_id"],
                "stage": "queued",
                "lease_epoch": params["epoch"],
                "request_fingerprint": params["fp"],
                "outcome": "recorded",
            }
            return _Result({"id": sk})

        if "CREATE (s:Operational:EvidenceSnapshot)" in q:
            sid = params["id"]
            self.snapshots[sid] = {
                "id": sid,
                "dream_id": params["dream_id"],
                "request_fingerprint": params["fp"],
                "created_at": self._ts(),
            }
            return _Result({"id": sid, "created_at": self._ts()})

        if "INCLUDES_EVIDENCE" in q:
            eid = params["evidence_id"]
            self.evidence_refs.setdefault(
                eid, {"id": eid, "evidence_label": params.get("evidence_label")}
            )
            self.includes[(params["snapshot_id"], eid)] = {
                "role": params["role"],
                "evidence_hash": params["evidence_hash"],
            }
            return _Result({"id": eid})

        if "CREATE (f:Operational:Finding)" in q:
            fid = params["id"]
            self.findings[fid] = {
                "id": fid,
                "dream_id": params["dream_id"],
                "snapshot_id": params["snapshot_id"],
                "request_fingerprint": params["fp"],
                "created_at": self._ts(),
            }
            return _Result({"id": fid, "created_at": self._ts()})

        if "USES_EVIDENCE" in q:
            self.evidence_refs.setdefault(
                params["evidence_id"], {"id": params["evidence_id"]}
            )
            self.uses_evidence.add((params["finding_id"], params["evidence_id"]))
            return _Result({"id": params["evidence_id"]})

        if "CREATE (p:Operational:Proposal)" in q:
            pid = params["id"]
            self.proposals[pid] = {
                "id": pid,
                "status_projection": params["status_projection"],
                "request_fingerprint": params["fp"],
                "created_at": self._ts(),
            }
            return _Result(
                {
                    "id": pid,
                    "status_projection": params["status_projection"],
                    "created_at": self._ts(),
                }
            )

        if "SUPPORTED_BY" in q:
            self.findings.setdefault(
                params["finding_id"],
                {"id": params["finding_id"], "placeholder": True},
            )
            self.supported_by.add((params["proposal_id"], params["finding_id"]))
            return _Result({"id": params["finding_id"]})

        if "CREATE (e:Operational:EvaluationReceipt)" in q:
            eid = params["id"]
            self.evaluations[eid] = {
                "id": eid,
                "outcome": params["outcome"],
                "request_fingerprint": params["fp"],
            }
            prop = self.proposals.get(params["proposal_id"])
            if prop is not None:
                if params["outcome"] == "passed" and prop.get("status_projection") == "draft":
                    prop["status_projection"] = "validated"
                elif params["outcome"] == "failed":
                    prop["status_projection"] = "invalid"
            return _Result(
                {"id": eid, "outcome": params["outcome"], "created_at": self._ts()}
            )

        if "CREATE (d:Operational:Decision)" in q:
            did = params["id"]
            self.decisions[did] = {
                "id": did,
                "decision": params["decision"],
                "request_fingerprint": params["fp"],
            }
            prop = self.proposals.get(params["proposal_id"])
            if prop is not None:
                prop["status_projection"] = params["projection"]
            return _Result(
                {
                    "id": did,
                    "decision": params["decision"],
                    "decided_at": self._ts(),
                }
            )

        # ---- Reads ----

        if "MATCH (l:Operational:MaintenanceLease {key:" in q:
            key = params["key"]
            node = self.leases.get(key)
            if node is None:
                return _Result(None)
            return _Result(self._lease_view(node))

        if "MATCH (d:Operational:DreamRun {id:" in q:
            rid = params.get("run_id") or params.get("id")
            node = self.dreams.get(rid)  # type: ignore[arg-type]
            if node is None:
                return _Result(None)
            return _Result(dict(node))

        if "MATCH (s:Operational:DreamStageReceipt {id:" in q:
            sk = params.get("stage_key")
            node = self.stages.get(sk)  # type: ignore[arg-type]
            return _Result(None if node is None else dict(node))

        if "MATCH (s:Operational:EvidenceSnapshot {id:" in q:
            sid = params.get("id") or params.get("snapshot_id")
            node = self.snapshots.get(sid)  # type: ignore[arg-type]
            return _Result(None if node is None else dict(node))

        if "MATCH (f:Operational:Finding {id:" in q:
            node = self.findings.get(params["id"])
            return _Result(None if node is None else dict(node))

        if "MATCH (p:Operational:Proposal {id:" in q:
            node = self.proposals.get(params["id"])
            return _Result(None if node is None else dict(node))

        if "MATCH (e:Operational:EvaluationReceipt {id:" in q:
            node = self.evaluations.get(params["id"])
            return _Result(None if node is None else dict(node))

        if "MATCH (d:Operational:Decision {id:" in q:
            node = self.decisions.get(params["id"])
            return _Result(None if node is None else dict(node))

        raise AssertionError(f"unexpected maintenance query: {q[:200]}")


class _SessionCtx:
    def __init__(self, session: _FakeMaintSession):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *args):
        return False


def _store_with(session: _FakeMaintSession) -> MaintenanceStore:
    def factory():
        class _Wrapped:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def session(self_inner, database: str = "neo4j"):
                return _SessionCtx(session)

        return _Wrapped()

    return MaintenanceStore(factory, "neo4j")


def _acquire(store: MaintenanceStore, **kw: Any) -> dict[str, Any]:
    base = {
        "key": "maintenance",
        "holder_id": "host-a",
        "run_id": "run-1",
        "ttl_seconds": 60,
    }
    base.update(kw)
    return store.acquire_maintenance_lease(base)


# ---------------------------------------------------------------------------
# Store: dream stages + provenance
# ---------------------------------------------------------------------------


def test_create_dream_and_stage_pipeline_with_replay():
    session = _FakeMaintSession()
    store = _store_with(session)
    lease = _acquire(store)
    assert lease["outcome"] == "acquired"
    epoch = lease["epoch"]

    created = store.create_dream_run(
        {
            "id": "run-1",
            "run_id": "run-1",
            "epoch": epoch,
            "holder_id": "host-a",
            "harness_generation_id": GENERATION_ID,
        }
    )
    assert created["outcome"] == "created"
    assert created["stage"] == "queued"

    # Illegal skip
    bad = store.record_dream_stage(
        {
            "run_id": "run-1",
            "epoch": epoch,
            "stage": "snapshotting",
        }
    )
    assert bad["outcome"] == "illegal_transition"

    # Legal advance + crash/replay on same stage key
    for stage in ("leased", "snapshotting", "normalizing"):
        r1 = store.record_dream_stage(
            {"run_id": "run-1", "epoch": epoch, "stage": stage}
        )
        assert r1["outcome"] == "recorded"
        r2 = store.record_dream_stage(
            {"run_id": "run-1", "epoch": epoch, "stage": stage}
        )
        assert r2["outcome"] == "replayed"


def test_evidence_supports_multiple_findings_and_proposals_no_absorption():
    session = _FakeMaintSession()
    store = _store_with(session)
    lease = _acquire(store, run_id="run-multi")
    epoch = lease["epoch"]
    store.create_dream_run(
        {
            "id": "run-multi",
            "run_id": "run-multi",
            "epoch": epoch,
            "harness_generation_id": GENERATION_ID,
        }
    )
    snap = store.create_evidence_snapshot(
        {
            "id": "snap-1",
            "dream_id": "run-multi",
            "run_id": "run-multi",
            "epoch": epoch,
            "cutoff_at": "2026-07-10T00:00:00Z",
            "source_ids_digest": "digest-1",
            "harness_generation_id": GENERATION_ID,
            "memberships": [
                {
                    "evidence_id": "fb-1",
                    "evidence_label": "Feedback",
                    "role": "generation",
                    "evidence_hash": "h1",
                },
                {
                    "evidence_id": "re-1",
                    "evidence_label": "RunEvent",
                    "role": "counterevidence",
                    "evidence_hash": "h2",
                },
            ],
        }
    )
    assert snap["outcome"] == "created"
    assert snap["membership_count"] == 2

    f1 = store.create_finding(
        {
            "id": "find-1",
            "dream_id": "run-multi",
            "snapshot_id": "snap-1",
            "class_key": "alias_confusion",
            "lane": "memory",
            "summary": "entity wrong",
            "evidence_strength": "moderate",
            "evidence_ids": ["fb-1"],
            "run_id": "run-multi",
            "epoch": epoch,
        }
    )
    f2 = store.create_finding(
        {
            "id": "find-2",
            "dream_id": "run-multi",
            "snapshot_id": "snap-1",
            "class_key": "empty_read",
            "lane": "housekeeping",
            "summary": "read empty",
            "evidence_strength": "tentative",
            "evidence_ids": ["fb-1", "re-1"],  # same fb-1 supports both
            "run_id": "run-multi",
            "epoch": epoch,
        }
    )
    assert f1["outcome"] == "created"
    assert f2["outcome"] == "created"
    assert ("find-1", "fb-1") in session.uses_evidence
    assert ("find-2", "fb-1") in session.uses_evidence

    p1 = store.create_proposal(
        {
            "id": "prop-1",
            "kind": "alias",
            "title": "alias fix",
            "target_ref": "entity:alice",
            "evidence_snapshot_id": "snap-1",
            "finding_ids": ["find-1"],
            "dream_id": "run-multi",
            "run_id": "run-multi",
            "epoch": epoch,
        }
    )
    p2 = store.create_proposal(
        {
            "id": "prop-2",
            "kind": "housekeeping_report",
            "title": "report",
            "target_ref": "report:weekly",
            "evidence_snapshot_id": "snap-1",
            "finding_ids": ["find-1", "find-2"],
            "dream_id": "run-multi",
            "run_id": "run-multi",
            "epoch": epoch,
        }
    )
    assert p1["outcome"] == "created"
    assert p2["outcome"] == "created"
    assert ("prop-1", "find-1") in session.supported_by
    assert ("prop-2", "find-1") in session.supported_by

    with pytest.raises(ValueError, match="absorbed_by_dream_id"):
        store.create_finding(
            {
                "id": "find-bad",
                "dream_id": "run-multi",
                "snapshot_id": "snap-1",
                "class_key": "x",
                "lane": "memory",
                "summary": "nope",
                "evidence_strength": "tentative",
                "absorbed_by_dream_id": "run-multi",
            }
        )


def test_evaluation_and_decision_are_separate_from_application():
    session = _FakeMaintSession()
    store = _store_with(session)
    # Seed a draft proposal without full dream fence for decision path.
    session.proposals["prop-x"] = {
        "id": "prop-x",
        "status_projection": "draft",
        "request_fingerprint": "seed",
    }

    ev = store.record_evaluation(
        {
            "id": "eval-1",
            "proposal_id": "prop-x",
            "evaluator_version": "ev-1",
            "baseline_ref": "base",
            "candidate_ref": "cand",
            "outcome": "passed",
        }
    )
    assert ev["outcome"] == "created"
    assert session.proposals["prop-x"]["status_projection"] == "validated"
    # Replay
    assert store.record_evaluation(
        {
            "id": "eval-1",
            "proposal_id": "prop-x",
            "evaluator_version": "ev-1",
            "baseline_ref": "base",
            "candidate_ref": "cand",
            "outcome": "passed",
        }
    )["outcome"] == "replayed"

    dec = store.record_decision(
        {
            "id": "dec-1",
            "proposal_id": "prop-x",
            "decision": "approved",
            "proposal_hash": "ph",
            "target_ref": "t",
            "before_fingerprint": "bf",
            "artifact_or_effect_hash": "eh",
            "decided_by": "owner",
        }
    )
    assert dec["outcome"] == "created"
    assert dec["application_status"] == "not_applied"
    assert session.proposals["prop-x"]["status_projection"] == "approved"
    # No EffectReceipt or Deployment created by decision alone.
    assert not any("EffectReceipt" in c for c in session.calls)
    assert not any("Deployment" in c for c in session.calls)


def test_maintenance_module_not_registered_as_fastmcp_tools():
    pytest.importorskip("fastmcp")
    from digital_brain_mcp_cypher import server

    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    for op in WORKFLOW_OPERATIONS:
        assert op not in names
    for forbidden in (
        "mint_activation_authority",
        "activate_alias",
        "record_effect",
        "publish_deployment",
    ):
        assert forbidden not in names


def test_maintenance_module_does_not_import_embeddings_or_journal():
    path = (
        ROOT
        / "mcp_servers"
        / "cypher"
        / "src"
        / "digital_brain_mcp_cypher"
        / "maintenance.py"
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "embeddings" not in imported
    assert "from .embeddings" not in source
    assert "from .journal" not in source
    assert "JournalEntry" not in source
