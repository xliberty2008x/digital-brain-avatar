"""Policy-bound quality evidence retention and regret handling."""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))

from digital_brain.maintenance.privacy import assert_no_intimate_fields  # noqa: E402
from digital_brain.maintenance.retention import (  # noqa: E402
    BACKUP_RETENTION_LIMITATION,
    RETENTION_SCHEMA_VERSION,
    RetentionConfig,
    assert_apply_permitted,
    compute_retention_config_digest,
    default_demo_config,
    effect_key_for,
    load_retention_config,
    select_retention_candidates,
)
from digital_brain.maintenance.snapshot import (  # noqa: E402
    SnapshotPolicy,
    freeze_snapshot,
)
from digital_brain_mcp_cypher.quality import QualityStore  # noqa: E402


GENERATION_ID = "hg-" + ("c" * 64)
NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fake Neo4j session for QualityStore retention / revoke tests
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, row: dict[str, Any] | list[dict[str, Any]] | None):
        if isinstance(row, list):
            self._rows = row
            self.row = row[0] if row else None
        else:
            self.row = row
            self._rows = [] if row is None else [row]

    def single(self):
        return self.row

    def data(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def consume(self) -> None:
        return None


class _FakeSession:
    def __init__(self) -> None:
        self.feedback: dict[str, dict[str, Any]] = {}
        self.payloads: dict[str, dict[str, Any]] = {}
        self.lifecycle: dict[str, dict[str, Any]] = {}
        self.effects: dict[str, dict[str, Any]] = {}
        self.proposals: dict[str, dict[str, Any]] = {}
        self.findings: dict[str, dict[str, Any]] = {}
        self.evidence_refs: dict[str, dict[str, Any]] = {}
        # finding_id -> set(evidence_id)
        self.finding_evidence: dict[str, set[str]] = {}
        # proposal_id -> set(finding_id)
        self.proposal_findings: dict[str, set[str]] = {}
        self.leases: dict[str, dict[str, Any]] = {}
        self.journal_touched: bool = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute_write(self, fn):  # noqa: ANN001
        return fn(self)

    def write_transaction(self, fn):  # noqa: ANN001
        return fn(self)

    def run(self, query: str, params: dict[str, Any] | None = None) -> _Result:
        params = params or {}
        self.calls.append((query, params))
        q = " ".join(query.split())

        if "RETURN toString(datetime())" in q:
            return _Result({"ts": "2026-07-10T12:00:00Z"})

        if "MATCH (l:Operational:MaintenanceLease {key:" in q:
            key = params.get("key")
            node = self.leases.get(key)  # type: ignore[arg-type]
            if node is None:
                return _Result(None)
            return _Result(
                {
                    "run_id": node.get("run_id"),
                    "epoch": node.get("epoch"),
                    "lease_until": node.get("lease_until"),
                    "expired": bool(node.get("expired", False)),
                }
            )

        if "MATCH (r:Operational:EffectReceipt {id:" in q:
            rid = params.get("id") or params.get("receipt_id")
            node = self.effects.get(rid)  # type: ignore[arg-type]
            return _Result(None if node is None else dict(node))

        if "MATCH (r:Operational:EffectReceipt {effect_key:" in q:
            key = params.get("effect_key")
            for node in self.effects.values():
                if node.get("effect_key") == key:
                    return _Result(dict(node))
            return _Result(None)

        if "CREATE (r:Operational:EffectReceipt)" in q:
            props = {
                "id": params["id"],
                "effect_key": params.get("effect_key"),
                "request_hash": params.get("request_hash"),
                "request_fingerprint": params.get("fp"),
                "effect_type": params.get("effect_type"),
                "actor": params.get("actor"),
                "before_ref": params.get("before_ref"),
                "after_ref": params.get("after_ref"),
                "target_ref": params.get("target_ref"),
                "outcome": params.get("effect_outcome"),
                "verification_status": params.get("verification_status"),
                "config_digest": params.get("config_digest"),
                "action": params.get("action"),
                "feedback_id": params.get("feedback_id"),
                "fence_epoch": params.get("epoch"),
                "run_id": params.get("run_id"),
            }
            self.effects[props["id"]] = props
            return _Result(
                {
                    "id": props["id"],
                    "effect_key": props["effect_key"],
                    "outcome": props["outcome"],
                    "verification_status": props["verification_status"],
                    "fence_epoch": props.get("fence_epoch"),
                }
            )

        if "MATCH (f:Operational:Feedback {id:" in q and "RETURN f.id AS id" in q:
            fid = params.get("feedback_id")
            node = self.feedback.get(fid)  # type: ignore[arg-type]
            return _Result(None if node is None else dict(node))

        if "MATCH (p:Operational:QualityPayload {id:" in q and "DETACH DELETE" in q:
            pid = params.get("payload_id")
            if pid in self.payloads:
                del self.payloads[pid]
                return _Result({"deleted_id": pid})
            return _Result(None)

        if (
            "MATCH (f:Operational:Feedback {id:" in q
            and "HAS_RAW_PAYLOAD" in q
            and "DELETE" in q
        ):
            pid = params.get("payload_id")
            if pid in self.payloads:
                del self.payloads[pid]
                return _Result({"deleted_id": pid})
            return _Result(None)

        if "MATCH (p:Operational:QualityPayload {id:" in q and "RETURN p.id AS id" in q:
            pid = params.get("payload_id")
            node = self.payloads.get(pid)  # type: ignore[arg-type]
            if node is None:
                return _Result(None)
            return _Result(
                {
                    "id": node["id"],
                    "owner_evidence_id": node.get("owner_evidence_id"),
                    "payload_text": node.get("payload_text"),
                    "sensitivity": node.get("sensitivity"),
                    "created_at": node.get("created_at"),
                }
            )

        if "CREATE (l:Operational:FeedbackLifecycleEvent)" in q:
            if "props" in params:
                props = dict(params["props"])
            else:
                props = {
                    "id": params.get("id"),
                    "feedback_id": params.get("feedback_id"),
                    "event": params.get("event"),
                    "actor": params.get("actor"),
                    "reason_code": params.get("reason_code"),
                    "request_fingerprint": params.get("fp"),
                    "config_digest": params.get("config_digest"),
                    "created_at": params.get("created_at") or "2026-07-10T12:00:00Z",
                }
            self.lifecycle[props["id"]] = props
            return _Result(
                {
                    "id": props["id"],
                    "feedback_id": props["feedback_id"],
                    "event": props["event"],
                    "actor": props["actor"],
                    "request_fingerprint": props.get("request_fingerprint"),
                    "created_at": props.get("created_at"),
                }
            )

        if "MERGE (l:Operational:FeedbackLifecycleEvent {id:" in q:
            lid = params.get("id")
            if lid not in self.lifecycle:
                self.lifecycle[lid] = {  # type: ignore[index]
                    "id": lid,
                    "feedback_id": params.get("feedback_id"),
                    "event": params.get("event"),
                    "actor": params.get("actor"),
                    "reason_code": params.get("reason_code"),
                    "config_digest": params.get("config_digest"),
                    "created_at": "2026-07-10T12:00:00Z",
                }
            return _Result({"id": lid})

        if "MATCH (l:Operational:FeedbackLifecycleEvent {id:" in q:
            lid = params.get("lifecycle_id") or params.get("id")
            node = self.lifecycle.get(lid)  # type: ignore[arg-type]
            return _Result(None if node is None else dict(node))

        if "CREATE (f:Operational:Feedback)" in q:
            props = dict(params["props"])
            self.feedback[props["id"]] = props
            return _Result(
                {
                    "id": props["id"],
                    "request_fingerprint": props["request_fingerprint"],
                    "kind": props["kind"],
                    "sensitivity": props["sensitivity"],
                    "harness_generation_id": props["harness_generation_id"],
                    "raw_payload_ref": props.get("raw_payload_ref"),
                    "created_at": props.get("created_at"),
                }
            )

        if "CREATE (p:Operational:QualityPayload)" in q:
            payload_props = dict(params["payload_props"])
            self.payloads[payload_props["id"]] = payload_props
            return _Result(None)

        # Stale proposal selection.
        if "USES_EVIDENCE" in q and "SUPPORTED_BY" in q and "RETURN DISTINCT" in q:
            fid = params.get("feedback_id")
            pending = set(params.get("pending") or [])
            rows: list[dict[str, Any]] = []
            for pid, prop in self.proposals.items():
                if prop.get("status_projection") not in pending:
                    continue
                for find_id in self.proposal_findings.get(pid, set()):
                    if fid in self.finding_evidence.get(find_id, set()):
                        rows.append(
                            {
                                "id": pid,
                                "status_projection": prop.get("status_projection"),
                            }
                        )
                        break
            return _Result(rows)

        if "SET p.status_projection = 'stale'" in q:
            pid = params.get("proposal_id")
            prop = self.proposals.get(pid)  # type: ignore[arg-type]
            if prop is None:
                return _Result(None)
            pending = set(params.get("pending") or [])
            if prop.get("status_projection") not in pending:
                return _Result(None)
            prop["status_projection"] = "stale"
            prop["stale_reason"] = "evidence_revoked"
            prop["stale_evidence_id"] = params.get("feedback_id")
            return _Result({"id": pid})

        if "JournalEntry" in q or "append_journal" in q:
            self.journal_touched = True

        return _Result(None)


def _store_with(session: _FakeSession) -> QualityStore:
    class _Driver:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def session(self, database=None):  # noqa: ANN001
            return session

    return QualityStore(driver_factory=lambda: _Driver(), database="neo4j")


def _feedback(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "fb-ret-1",
        "kind": "entity_wrong",
        "sensitivity": "intimate",
        "harness_generation_id": GENERATION_ID,
        "redacted_summary": "ops summary only",
        "raw_payload": "never surface this intimate quote in exports",
        "created_at": "2026-07-01T00:00:00Z",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Config: schema / version / digest
# ---------------------------------------------------------------------------


def test_retention_config_digest_stable_and_notes_excluded(tmp_path: pathlib.Path):
    cfg_a = default_demo_config(auto_apply=False)
    cfg_b = RetentionConfig.from_mapping(
        {
            **cfg_a.identity_payload(),
            "notes": "different notes must not change digest",
        }
    )
    assert cfg_a.digest() == cfg_b.digest()
    assert cfg_a.schema_version == RETENTION_SCHEMA_VERSION
    assert len(cfg_a.digest()) == 64

    # Version bump changes digest.
    bumped = RetentionConfig.from_mapping(
        {**cfg_a.identity_payload(), "version": "1.0.1-test"}
    )
    assert bumped.digest() != cfg_a.digest()
    assert compute_retention_config_digest(cfg_a) == cfg_a.digest()

    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg_a.identity_payload()), encoding="utf-8")
    loaded = load_retention_config(path)
    assert loaded.digest() == cfg_a.digest()
    assert BACKUP_RETENTION_LIMITATION in loaded.to_public_dict()["backup_limitation"]


def test_retention_config_rejects_model_expansion():
    with pytest.raises(ValueError, match="unknown action"):
        RetentionConfig.from_mapping(
            {
                "version": "1",
                "sensitivity_allowlist": ["intimate"],
                "ttl_seconds_by_sensitivity": {"intimate": 0},
                "actions_by_sensitivity": {"intimate": "shred_everything"},
            }
        )
    with pytest.raises(ValueError, match="unknown sensitivity"):
        RetentionConfig.from_mapping(
            {
                "version": "1",
                "sensitivity_allowlist": ["super_secret"],
                "ttl_seconds_by_sensitivity": {"super_secret": 0},
                "actions_by_sensitivity": {"super_secret": "purge"},
            }
        )


# ---------------------------------------------------------------------------
# Dry-run selection
# ---------------------------------------------------------------------------


def test_dry_run_counts_before_any_effect():
    cfg = default_demo_config(
        auto_apply=False,
        intimate_ttl_seconds=0,
        personal_ttl_seconds=0,
        public_ttl_seconds=10_000_000,
    )
    inventory = [
        {
            "feedback_id": "fb-int",
            "sensitivity": "intimate",
            "created_at": "2026-07-01T00:00:00Z",
            "has_payload": True,
            "payload_id": "qp-fb-int",
        },
        {
            "feedback_id": "fb-pub",
            "sensitivity": "public_ops",
            "created_at": "2026-07-09T00:00:00Z",
            "has_payload": True,
            "payload_id": "qp-fb-pub",
        },
        {
            "feedback_id": "fb-empty",
            "sensitivity": "intimate",
            "created_at": "2026-07-01T00:00:00Z",
            "has_payload": False,
        },
        {
            "feedback_id": "fb-done",
            "sensitivity": "intimate",
            "created_at": "2026-07-01T00:00:00Z",
            "has_payload": True,
            "already_lifecycle": "purged",
        },
    ]
    plan = select_retention_candidates(inventory, cfg, now=NOW)
    assert plan.counts["selected"] == 1
    assert plan.counts["skipped_no_payload"] == 1
    assert plan.counts["skipped_ttl"] == 1  # public_ops not aged enough
    assert plan.counts["skipped_already_done"] == 1
    assert plan.selected[0].feedback_id == "fb-int"
    assert plan.selected[0].action == "purge"
    assert plan.auto_apply is False
    public = plan.to_public_dict()
    assert "payload_text" not in str(public)
    assert public["counts"]["selected"] == 1


def test_auto_apply_denied_without_opt_in():
    cfg = default_demo_config(auto_apply=False)
    with pytest.raises(PermissionError, match="retention_auto_apply_disabled"):
        assert_apply_permitted(cfg, automatic=True)
    # Owner-initiated still ok without auto_apply.
    assert_apply_permitted(cfg, automatic=False, owner_initiated=True)
    cfg_on = default_demo_config(auto_apply=True)
    assert_apply_permitted(cfg_on, automatic=True)


# ---------------------------------------------------------------------------
# Dedicated quality transaction
# ---------------------------------------------------------------------------


def _retention_fence_payload(**extra: Any) -> dict[str, Any]:
    """Mutative retention requires run_id + epoch + lease_key (Task 5 fence)."""
    base = {
        "run_id": "dream-ret-1",
        "epoch": 1,
        "lease_key": "maintenance",
    }
    base.update(extra)
    return base


def test_apply_retention_redacts_payload_keeps_fingerprint_and_receipt():
    session = _FakeSession()
    store = _store_with(session)
    session.leases["maintenance"] = {
        "run_id": "dream-ret-1",
        "epoch": 1,
        "lease_until": "2099-01-01T00:00:00Z",
        "expired": False,
    }
    created = store.create_feedback(_feedback())
    assert created["outcome"] == "created"
    ref = created["raw_payload_ref"]
    assert ref in session.payloads
    assert "intimate quote" in session.payloads[ref]["payload_text"]
    fp_before = session.feedback["fb-ret-1"]["request_fingerprint"]

    cfg = default_demo_config(auto_apply=False)
    denied = store.apply_retention_effect(
        _retention_fence_payload(
            id="eff-ret-1",
            effect_key=effect_key_for(
                action="purge", feedback_id="fb-ret-1", config_digest=cfg.digest()
            ),
            feedback_id="fb-ret-1",
            action="purge",
            config_digest=cfg.digest(),
            automatic=True,
            auto_apply_enabled=False,
        )
    )
    assert denied["outcome"] == "denied"
    assert denied["reason"] == "retention_auto_apply_disabled"
    assert ref in session.payloads  # no mutation

    dry = store.apply_retention_effect(
        {
            "id": "eff-ret-1",
            "effect_key": effect_key_for(
                action="purge", feedback_id="fb-ret-1", config_digest=cfg.digest()
            ),
            "feedback_id": "fb-ret-1",
            "action": "purge",
            "config_digest": cfg.digest(),
            "dry_run": True,
        }
    )
    assert dry["outcome"] == "dry_run"
    assert dry["would_apply"] is True
    assert dry["counts"]["selected"] == 1
    assert ref in session.payloads

    with pytest.raises(ValueError, match="run_id"):
        store.apply_retention_effect(
            {
                "id": "eff-ret-unfenced",
                "effect_key": effect_key_for(
                    action="purge",
                    feedback_id="fb-ret-1",
                    config_digest=cfg.digest(),
                ),
                "feedback_id": "fb-ret-1",
                "action": "purge",
                "config_digest": cfg.digest(),
                "owner_initiated": True,
            }
        )

    applied = store.apply_retention_effect(
        _retention_fence_payload(
            id="eff-ret-1",
            effect_key=effect_key_for(
                action="purge", feedback_id="fb-ret-1", config_digest=cfg.digest()
            ),
            feedback_id="fb-ret-1",
            action="purge",
            config_digest=cfg.digest(),
            owner_initiated=True,
        )
    )
    assert applied["outcome"] == "created"
    assert applied["verification_status"] == "verified_absent"
    assert applied["feedback_request_fingerprint"] == fp_before
    assert ref not in session.payloads
    assert session.feedback["fb-ret-1"]["request_fingerprint"] == fp_before
    assert "eff-ret-1" in session.effects
    assert session.effects["eff-ret-1"]["verification_status"] == "verified_absent"

    # Intimate raw gone from privileged + public reads.
    gone = store.get_quality_payload(ref)
    assert gone["outcome"] == "not_found"
    public = store.export_feedback_public("fb-ret-1")
    assert public["outcome"] == "ok"
    assert public["payload_text"] is None
    assert public["raw_payload"] is None
    assert public["request_fingerprint"] == fp_before
    assert_no_intimate_fields(
        {k: v for k, v in public.items() if k not in {"payload_text", "raw_payload"}}
    )

    # Replay
    replayed = store.apply_retention_effect(
        _retention_fence_payload(
            id="eff-ret-1",
            effect_key=effect_key_for(
                action="purge", feedback_id="fb-ret-1", config_digest=cfg.digest()
            ),
            feedback_id="fb-ret-1",
            action="purge",
            config_digest=cfg.digest(),
            owner_initiated=True,
        )
    )
    assert replayed["outcome"] == "replayed"


def test_retention_apply_rejects_stale_lease_epoch():
    session = _FakeSession()
    store = _store_with(session)
    session.leases["maintenance"] = {
        "run_id": "dream-ret-1",
        "epoch": 2,
        "lease_until": "2099-01-01T00:00:00Z",
        "expired": False,
    }
    store.create_feedback(_feedback())
    cfg = default_demo_config(auto_apply=False)
    out = store.apply_retention_effect(
        _retention_fence_payload(
            id="eff-stale",
            effect_key=effect_key_for(
                action="redact", feedback_id="fb-ret-1", config_digest=cfg.digest()
            ),
            feedback_id="fb-ret-1",
            action="redact",
            config_digest=cfg.digest(),
            owner_initiated=True,
            epoch=1,  # stale vs lease epoch 2
        )
    )
    assert out["outcome"] == "stale_epoch"


def test_revoke_marks_only_directly_derived_pending_proposals_stale():
    session = _FakeSession()
    store = _store_with(session)
    store.create_feedback(_feedback(id="fb-rev-1", raw_payload="secret"))

    # Directly derived pending proposal via Finding USES_EVIDENCE + SUPPORTED_BY.
    session.evidence_refs["fb-rev-1"] = {"id": "fb-rev-1"}
    session.findings["find-1"] = {"id": "find-1"}
    session.finding_evidence["find-1"] = {"fb-rev-1"}
    session.proposals["prop-derived"] = {
        "id": "prop-derived",
        "status_projection": "review_pending",
    }
    session.proposal_findings["prop-derived"] = {"find-1"}

    # Unrelated pending proposal (different evidence).
    session.evidence_refs["fb-other"] = {"id": "fb-other"}
    session.findings["find-2"] = {"id": "find-2"}
    session.finding_evidence["find-2"] = {"fb-other"}
    session.proposals["prop-unrelated"] = {
        "id": "prop-unrelated",
        "status_projection": "draft",
    }
    session.proposal_findings["prop-unrelated"] = {"find-2"}

    # Already rejected — must not become stale.
    session.proposals["prop-rejected"] = {
        "id": "prop-rejected",
        "status_projection": "rejected",
    }
    session.proposal_findings["prop-rejected"] = {"find-1"}

    rev = store.revoke_feedback(
        {
            "id": "fle-rev-1",
            "feedback_id": "fb-rev-1",
            "actor": "user",
            "reason_code": "user_request",
        }
    )
    assert rev["outcome"] == "created"
    assert "prop-derived" in rev["stale_proposal_ids"]
    assert session.proposals["prop-derived"]["status_projection"] == "stale"
    assert session.proposals["prop-unrelated"]["status_projection"] == "draft"
    assert session.proposals["prop-rejected"]["status_projection"] == "rejected"
    assert session.journal_touched is False

    # Future snapshots exclude revoked evidence.
    items = [
        {
            "id": "fb-rev-1",
            "label": "Feedback",
            "observed_at": "2026-07-09T00:00:00Z",
            "evidence_hash": "h1",
            "revoked": True,
            "sensitivity": "intimate",
            "raw_payload": "should not appear",
        },
        {
            "id": "fb-ok",
            "label": "Feedback",
            "observed_at": "2026-07-09T00:00:00Z",
            "evidence_hash": "h2",
            "revoked": False,
            "sensitivity": "public_ops",
        },
    ]
    snap = freeze_snapshot(
        items,
        policy=SnapshotPolicy(
            cutoff_at="2026-07-10T12:00:00Z",
            harness_generation_id=GENERATION_ID,
            holdout_ratio=0.0,
        ),
        dream_id="dream-rev-ret",
    )
    ids = {m.evidence_id for m in snap.memberships}
    assert "fb-rev-1" not in ids
    assert "fb-rev-1" in snap.excluded_revoked_ids
    assert "fb-ok" in ids
    packet = snap.analyzer_packet()
    assert "should not appear" not in str(packet)


def test_backup_limitation_documented():
    assert "predate" in BACKUP_RETENTION_LIMITATION.lower() or "before" in (
        BACKUP_RETENTION_LIMITATION.lower()
    )
    assert "backup" in BACKUP_RETENTION_LIMITATION.lower()
    cfg = default_demo_config()
    assert BACKUP_RETENTION_LIMITATION == cfg.to_public_dict()["backup_limitation"]
