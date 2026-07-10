"""Evidence snapshot freeze: determinism, holdout, privacy, revocation."""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from digital_brain.maintenance.privacy import (  # noqa: E402
    IntimateFieldError,
    assert_no_intimate_fields,
    contains_intimate_fields,
    correlation_hmac,
    redact_evidence_record,
    redact_packet,
)
from digital_brain.maintenance.snapshot import (  # noqa: E402
    SnapshotPolicy,
    compute_source_ids_digest,
    freeze_snapshot,
    load_evidence_fixture,
)

GENERATION_ID = "hg-" + ("a" * 64)
CORRELATION_KEY = b"test-correlation-key-material"
FIXTURE = ROOT / "tests" / "fixtures" / "dreams" / "evidence" / "sample_ledger.json"
CUTOFF = "2026-07-10T12:00:00Z"


def _policy(**kwargs):
    base = dict(
        cutoff_at=CUTOFF,
        harness_generation_id=GENERATION_ID,
        correlation_key=CORRELATION_KEY,
        holdout_ratio=0.2,
        graph_bookmark="bm-1",
        base_commit="deadbeef",
    )
    base.update(kwargs)
    return SnapshotPolicy(**base)


def _ledger():
    return load_evidence_fixture(FIXTURE)


def test_late_event_excluded_from_snapshot():
    snap = freeze_snapshot(_ledger(), policy=_policy(), dream_id="dream-late")
    ids = {m.evidence_id for m in snap.memberships}
    assert "re-late-1" not in ids
    assert "re-late-1" in snap.excluded_late_ids
    assert snap.source_counts["excluded_late"] >= 1


def test_revoked_event_excluded_from_snapshot():
    snap = freeze_snapshot(_ledger(), policy=_policy(), dream_id="dream-rev")
    ids = {m.evidence_id for m in snap.memberships}
    assert "fb-revoked-1" not in ids
    assert "fb-revoked-1" in snap.excluded_revoked_ids
    assert snap.source_counts["excluded_revoked"] >= 1


def test_counterevidence_role_partition():
    snap = freeze_snapshot(_ledger(), policy=_policy(), dream_id="dream-ce")
    roles = snap.membership_role_map()
    assert roles.get("fb-praise-1") == "counterevidence"
    assert "fb-praise-1" in snap.counterevidence_ids
    assert "fb-praise-1" not in snap.generation_ids
    assert "fb-praise-1" not in snap.holdout_ids


def test_deterministic_digest_same_ledger_and_policy():
    a = freeze_snapshot(_ledger(), policy=_policy(), dream_id="dream-d1")
    b = freeze_snapshot(_ledger(), policy=_policy(), dream_id="dream-d2")
    assert a.source_ids_digest == b.source_ids_digest
    assert a.membership_role_map() == b.membership_role_map()
    assert a.generation_ids == b.generation_ids
    assert a.holdout_ids == b.holdout_ids
    assert a.counterevidence_ids == b.counterevidence_ids
    # Recompute digest independently.
    assert compute_source_ids_digest(a.memberships) == a.source_ids_digest


def test_digest_changes_when_membership_changes():
    snap = freeze_snapshot(_ledger(), policy=_policy(), dream_id="dream-chg")
    items = [x for x in _ledger() if x["id"] != "fb-entity-1"]
    other = freeze_snapshot(items, policy=_policy(), dream_id="dream-chg2")
    assert snap.source_ids_digest != other.source_ids_digest


def test_disjoint_holdout_and_generation():
    # Pin explicit holdout so the test is independent of ratio hashing.
    policy = _policy(holdout_ids=frozenset({"re-fail-2", "re-fail-3"}), holdout_ratio=0.0)
    snap = freeze_snapshot(_ledger(), policy=policy, dream_id="dream-ho")
    g, h, c = set(snap.generation_ids), set(snap.holdout_ids), set(snap.counterevidence_ids)
    assert g.isdisjoint(h)
    assert c.isdisjoint(h)
    assert "re-fail-2" in h
    assert "re-fail-3" in h
    assert "re-fail-2" not in g
    # Analyzer projection hides holdout membership entirely.
    packet = snap.analyzer_packet()
    assert "holdout_ids" not in packet
    packet_ids = {item["id"] for item in packet["items"]}
    assert "re-fail-2" not in packet_ids
    assert "re-fail-3" not in packet_ids
    # Generation/counterevidence remain visible.
    assert "fb-entity-1" in packet_ids or "fb-entity-1" in packet["generation_ids"]
    assert "fb-praise-1" in packet["counterevidence_ids"]


def test_holdout_ratio_is_deterministic():
    p = _policy(holdout_ratio=0.3, holdout_ids=None)
    a = freeze_snapshot(_ledger(), policy=p, dream_id="dream-r1")
    b = freeze_snapshot(_ledger(), policy=p, dream_id="dream-r2")
    assert a.holdout_ids == b.holdout_ids
    assert set(a.holdout_ids).isdisjoint(a.generation_ids)


def test_eligible_exposures_included_in_counts():
    snap = freeze_snapshot(_ledger(), policy=_policy(), dream_id="dream-ex")
    assert snap.source_counts["eligible_exposure"] == len(snap.eligible_exposure_ids)
    assert "fb-entity-1" in snap.eligible_exposure_ids
    # Holdout eligible exposures are excluded from the eligible list.
    for hid in snap.holdout_ids:
        assert hid not in snap.eligible_exposure_ids


def test_intimate_raw_fields_excluded_from_analyzer_and_redaction():
    snap = freeze_snapshot(_ledger(), policy=_policy(), dream_id="dream-priv")
    packet = snap.analyzer_packet()
    assert_no_intimate_fields(packet)
    # No raw intimate quote anywhere in the packet JSON surface.
    blob = str(packet)
    assert "never surface this intimate quote" not in blob
    assert "raw_payload" not in blob

    # Intimate item may still be a member (metadata only).
    if "fb-intimate-1" in snap.redacted_items:
        item = snap.redacted_items["fb-intimate-1"]
        assert "raw_payload" not in item
        # Free-form summary stripped for intimate sensitivity.
        assert "redacted_summary" not in item or item.get("sensitivity") != "intimate"


def test_correlation_uses_keyed_hmac_not_plain_id():
    plain = "fb-entity-1"
    mac = correlation_hmac(plain, key=CORRELATION_KEY)
    assert mac != plain
    assert len(mac) == 64
    # Different key → different MAC.
    other = correlation_hmac(plain, key=b"other-key-material-xx")
    assert other != mac

    snap = freeze_snapshot(_ledger(), policy=_policy(), dream_id="dream-hmac")
    member = next(m for m in snap.memberships if m.evidence_id == "fb-entity-1")
    assert member.evidence_hash == correlation_hmac("fb-entity-1", key=CORRELATION_KEY)


def test_redact_packet_fails_closed_on_intimate_leak():
    dirty = {"summary": "ok", "raw_payload": "secret intimate text"}
    # redact_packet walks and strips known intimate keys.
    clean = redact_packet(dirty)
    assert "raw_payload" not in clean
    assert_no_intimate_fields(clean)

    # Nested leak detection.
    nested = {"items": [{"id": "x", "payload_text": "nope"}]}
    clean2 = redact_packet(nested)
    assert contains_intimate_fields(clean2) == []


def test_assert_no_intimate_fields_raises():
    with pytest.raises(IntimateFieldError):
        assert_no_intimate_fields({"quote": "leaked"})


def test_snapshot_binds_cutoff_generation_bookmark_commit_redaction():
    snap = freeze_snapshot(_ledger(), policy=_policy(), dream_id="dream-bind")
    assert snap.cutoff_at == CUTOFF
    assert snap.harness_generation_id == GENERATION_ID
    assert snap.graph_bookmark == "bm-1"
    assert snap.base_commit == "deadbeef"
    assert snap.redaction_policy_version == "1"
    assert snap.taxonomy_version == "1"
    payload = snap.to_store_payload(run_id="dream-bind", epoch=1)
    assert payload["cutoff_at"] == CUTOFF
    assert payload["harness_generation_id"] == GENERATION_ID
    assert payload["graph_bookmark"] == "bm-1"
    assert payload["base_commit"] == "deadbeef"
    assert payload["source_ids_digest"] == snap.source_ids_digest
    assert isinstance(payload["memberships"], list)
    assert payload["memberships"]


def test_supporting_and_contradicting_evidence_present():
    """Generation (supporting) and counterevidence (contradicting) both freeze."""
    snap = freeze_snapshot(
        _ledger(),
        policy=_policy(holdout_ids=frozenset()),
        dream_id="dream-sc",
    )
    assert snap.generation_ids, "expected supporting generation evidence"
    assert snap.counterevidence_ids, "expected contradicting counterevidence"
    assert snap.source_counts["generation"] == len(snap.generation_ids)
    assert snap.source_counts["counterevidence"] == len(snap.counterevidence_ids)


def test_redact_evidence_record_strips_raw_keeps_id():
    rec = redact_evidence_record(
        {
            "id": "fb-1",
            "sensitivity": "intimate",
            "raw_payload": "private",
            "redacted_summary": "should drop",
            "kind": "entity_wrong",
        },
        correlation_key=CORRELATION_KEY,
    )
    assert rec["id"] == "fb-1"
    assert "raw_payload" not in rec
    assert "redacted_summary" not in rec
    assert "correlation_hmac" in rec


def test_revoked_feedback_excluded_and_unrelated_evidence_kept():
    """Regret: revoked evidence never re-enters freeze; peers stay eligible."""
    items = [
        {
            "id": "fb-revoked-regret",
            "label": "Feedback",
            "observed_at": "2026-07-09T10:00:00Z",
            "evidence_hash": "h-rev",
            "revoked": True,
            "sensitivity": "intimate",
            "raw_payload": "intimate raw must not freeze",
            "kind": "entity_wrong",
        },
        {
            "id": "fb-peer-ok",
            "label": "Feedback",
            "observed_at": "2026-07-09T11:00:00Z",
            "evidence_hash": "h-ok",
            "revoked": False,
            "sensitivity": "public_ops",
            "kind": "miss",
        },
    ]
    snap = freeze_snapshot(items, policy=_policy(holdout_ratio=0.0), dream_id="dream-regret")
    ids = {m.evidence_id for m in snap.memberships}
    assert "fb-revoked-regret" not in ids
    assert "fb-revoked-regret" in snap.excluded_revoked_ids
    assert "fb-peer-ok" in ids
    packet = snap.analyzer_packet()
    assert_no_intimate_fields(packet)
    assert "intimate raw must not freeze" not in str(packet)


def test_post_redaction_export_projection_has_no_raw_fields():
    """After retention redaction, analyzer packets carry metadata only."""
    items = [
        {
            "id": "fb-redacted-meta",
            "label": "Feedback",
            "observed_at": "2026-07-09T10:00:00Z",
            "evidence_hash": "h-meta",
            "sensitivity": "intimate",
            # raw already removed by retention; only metadata remains
            "raw_payload": None,
            "kind": "entity_wrong",
            "redacted_summary": "bounded ops summary",
        }
    ]
    snap = freeze_snapshot(
        items, policy=_policy(holdout_ratio=0.0), dream_id="dream-post-redact"
    )
    assert "fb-redacted-meta" in {m.evidence_id for m in snap.memberships}
    item = snap.redacted_items["fb-redacted-meta"]
    assert "raw_payload" not in item
    assert "payload_text" not in item
    packet = snap.analyzer_packet()
    assert_no_intimate_fields(packet)
