"""Alias audit, proposal, authority mint/consume, apply/revoke, protection tests.

Uses an in-memory fake session that implements the subset of Cypher emitted by
``AliasEffectStore``. Operator path only — no MCP registration.
"""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from digital_brain.maintenance.alias_effects import (  # noqa: E402
    DEFAULT_NAMESPACE,
    PINNED_IDENTITY_SCOPE,
    AliasEffectStore,
    alias_effect_payload,
    alias_lookup_key,
    audit_alias_rows,
    claim_false_may_mutate_life_memory,
    compute_alias_effect_hash,
    compute_before_fingerprint,
    is_generic_ack,
    normalize_alias_source,
    parse_apply_token,
)


# ---------------------------------------------------------------------------
# Fake Neo4j
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | dict[str, Any] | None):
        if rows is None:
            self._rows: list[dict[str, Any]] = []
        elif isinstance(rows, dict):
            self._rows = [rows]
        else:
            self._rows = list(rows)
        self._i = 0

    def single(self):
        if not self._rows:
            return None
        return self._rows[0]

    def data(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def consume(self) -> None:
        return None


class _FakeSession:
    def __init__(self) -> None:
        self.aliases: dict[str, dict[str, Any]] = {}
        self.entities: dict[str, dict[str, Any]] = {}
        self.proposals: dict[str, dict[str, Any]] = {}
        self.authorities: dict[str, dict[str, Any]] = {}
        self.effects: dict[str, dict[str, Any]] = {}
        self.effects_by_key: dict[str, str] = {}
        self.protections: list[dict[str, Any]] = []
        self.learning_logs: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.now = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)

    def execute_write(self, fn):  # noqa: ANN001
        return fn(self)

    def write_transaction(self, fn):  # noqa: ANN001
        return fn(self)

    def run(self, query: str, params: dict[str, Any] | None = None) -> _Result:
        params = params or {}
        self.calls.append(query)
        q = " ".join(query.split())

        # --- Alias audit / list ---
        if "MATCH (a:Alias)" in q and "RETURN a.id AS id" in q and "namespace" in q and "LIMIT" not in q:
            rows = []
            for a in self.aliases.values():
                rows.append(
                    {
                        "id": a.get("id"),
                        "namespace": a.get("namespace"),
                        "entity_type": a.get("entity_type"),
                        "normalized_from": a.get("normalized_from"),
                        "from_name": a.get("from_name"),
                        "canonical_id": a.get("canonical_id"),
                        "status": a.get("status"),
                        "revision": a.get("revision"),
                    }
                )
            return _Result(rows)

        # Active alias lookup for key
        if (
            "MATCH (a:Alias)" in q
            and "normalized_from = $normalized_from" in q
            and "ORDER BY" in q
        ):
            matches = []
            for a in self.aliases.values():
                if (a.get("status") or "active") != "active":
                    continue
                if (a.get("namespace") or params.get("namespace")) != params.get(
                    "namespace"
                ):
                    continue
                if a.get("entity_type") != params.get("entity_type"):
                    continue
                if a.get("normalized_from") != params.get("normalized_from"):
                    continue
                matches.append(a)
            matches.sort(
                key=lambda x: (-int(x.get("revision") or 0), str(x.get("id")))
            )
            if not matches:
                return _Result(None)
            top = matches[0]
            return _Result(
                {
                    "id": top["id"],
                    "revision": top.get("revision"),
                    "canonical_id": top.get("canonical_id"),
                    "canonical_name": top.get("canonical_name"),
                }
            )

        # Proposal status update (before generic proposal MATCH)
        if "SET p.status_projection = 'approved'" in q:
            p = self.proposals.get(params["id"])
            if p:
                p["status_projection"] = "approved"
            return _Result({"id": params["id"]})

        # Proposal create
        if "CREATE (p:Operational:Proposal)" in q:
            props = {
                "id": params["id"],
                "kind": params["kind"],
                "title": params["title"],
                "status_projection": "review_pending",
                "target_ref": params["target_ref"],
                "before_fingerprint": params["before_fingerprint"],
                "proposed_effect_hash": params["proposed_effect_hash"],
                "effect_json": params["effect_json"],
                "request_fingerprint": params["fp"],
            }
            self.proposals[params["id"]] = props
            return _Result(
                {"id": props["id"], "status_projection": props["status_projection"]}
            )

        # Proposal read
        if "MATCH (p:Operational:Proposal {id: $id})" in q and "RETURN p.id" in q:
            p = self.proposals.get(params["id"])
            if p is None:
                return _Result(None)
            return _Result(dict(p))

        # Authority expire / consume (must run before generic authority MATCH)
        if "SET a.status = 'expired'" in q:
            a = self.authorities.get(params["id"])
            if a:
                a["status"] = "expired"
            return _Result({"id": params["id"]})

        if "SET a.status = 'consumed'" in q:
            a = self.authorities.get(params["id"])
            if a:
                a["status"] = "consumed"
                a["consumed_at"] = params.get("now")
                a["consumption_receipt_id"] = params.get("receipt_id")
                a["reconciliation_receipt_id"] = params.get("receipt_id")
            return _Result({"id": params["id"]})

        # Authority create
        if "CREATE (a:Operational:ActivationAuthority)" in q:
            props = {
                "id": params["id"],
                "decision_id": params["decision_id"],
                "proposal_id": params["proposal_id"],
                "proposal_hash": params["proposal_hash"],
                "target_ref": params["target_ref"],
                "before_fingerprint": params["before_fingerprint"],
                "artifact_or_effect_hash": params["artifact_or_effect_hash"],
                "approver": params["approver"],
                "scopes_json": params["scopes_json"],
                "status": "minted",
                "nonce_digest": params["nonce_digest"],
                "minted_at": params["minted_at"],
                "expires_at": params["expires_at"],
                "request_fingerprint": params["fp"],
                "consumption_receipt_id": None,
                "reconciliation_receipt_id": None,
            }
            self.authorities[params["id"]] = props
            return _Result(
                {
                    "id": props["id"],
                    "status": props["status"],
                    "expires_at": props["expires_at"],
                }
            )

        # Authority read
        if "MATCH (a:Operational:ActivationAuthority {id: $id})" in q:
            a = self.authorities.get(params["id"])
            if a is None:
                return _Result(None)
            if "OPTIONAL MATCH (r:Operational:EffectReceipt" in q:
                receipt = self.effects.get(a.get("consumption_receipt_id") or "")
                row = {
                    "authority_id": a["id"],
                    "status": a.get("status"),
                    "proposal_id": a.get("proposal_id"),
                    "target_ref": a.get("target_ref"),
                    "before_fingerprint": a.get("before_fingerprint"),
                    "artifact_or_effect_hash": a.get("artifact_or_effect_hash"),
                    "approver": a.get("approver"),
                    "expires_at": a.get("expires_at"),
                    "consumed_at": a.get("consumed_at"),
                    "consumption_receipt_id": a.get("consumption_receipt_id"),
                    "reconciliation_receipt_id": a.get("reconciliation_receipt_id"),
                    "request_fingerprint": a.get("request_fingerprint"),
                    "receipt_id": None if not receipt else receipt.get("id"),
                    "receipt_outcome": None if not receipt else receipt.get("outcome"),
                    "receipt_effect_type": None
                    if not receipt
                    else receipt.get("effect_type"),
                    "receipt_request_hash": None
                    if not receipt
                    else receipt.get("request_hash"),
                }
                return _Result(row)
            return _Result(dict(a))

        # Effect receipt by id
        if "MATCH (r:Operational:EffectReceipt {id: $id})" in q:
            r = self.effects.get(params["id"])
            return _Result(None if r is None else dict(r))

        # Effect by key
        if "MATCH (r:Operational:EffectReceipt {effect_key: $effect_key})" in q:
            eid = self.effects_by_key.get(params["effect_key"])
            if not eid:
                return _Result(None)
            r = self.effects[eid]
            return _Result(
                {
                    "id": r["id"],
                    "request_hash": r.get("request_hash"),
                    "outcome": r.get("outcome"),
                }
            )

        # Effect create
        if "CREATE (r:Operational:EffectReceipt)" in q:
            props = {
                "id": params["id"],
                "effect_key": params["effect_key"],
                "request_hash": params["request_hash"],
                "proposal_id": params.get("proposal_id"),
                "effect_type": params["effect_type"],
                "actor": params["actor"],
                "before_ref": params.get("before_ref"),
                "after_ref": params.get("after_ref"),
                "outcome": params.get("outcome") or "applied",
                "verification_status": params.get("verification_status"),
                "authority_digest": params.get("authority_digest"),
                "applied_at": params.get("now"),
                "undo_ref": params.get("undo_ref"),
            }
            # outcome may be set as literal 'applied' in query
            if "outcome = 'applied'" in q or props["outcome"] is None:
                props["outcome"] = "applied"
            self.effects[props["id"]] = props
            self.effects_by_key[props["effect_key"]] = props["id"]
            return _Result({"id": props["id"], "outcome": props["outcome"]})

        # Target entity lookup
        if "MATCH (n {id: $id})" in q and "NOT n:Alias" in q:
            ent = self.entities.get(params["id"])
            if ent is None:
                return _Result(None)
            return _Result(
                {
                    "id": ent["id"],
                    "labels": ent.get("labels", []),
                    "name": ent.get("name"),
                }
            )

        if "MATCH (a:Alias {id: $id})" in q and "RETURN a.id AS id LIMIT 1" in q and "SET" not in q:
            a = self.aliases.get(params["id"])
            return _Result(None if a is None else {"id": a["id"]})

        # Revoke prior alias status (before Alias id MATCH reads)
        if "SET a.status = 'revoked'" in q and "MATCH (a:Alias {id: $id})" in q:
            a = self.aliases.get(params["id"])
            if a:
                a["status"] = "revoked"
                a["revoked_at"] = params.get("now")
            return _Result({"id": params["id"]})

        # Create Alias
        if "CREATE (a:Operational:Alias)" in q:
            props = {
                "id": params["id"],
                "namespace": params["namespace"],
                "entity_type": params["entity_type"],
                "normalized_from": params["normalized_from"],
                "display_from": params["display_from"],
                "from_name": params["display_from"],
                "to_name": params["canonical_name"],
                "canonical_id": params["canonical_id"],
                "canonical_name": params["canonical_name"],
                "revision": params["revision"],
                "status": "revoked" if "status = 'revoked'" in q else "active",
                "proposal_id": params.get("proposal_id"),
                "effect_receipt_id": params.get("effect_id"),
                "confirmed_by": params.get("actor"),
                "created_at": params.get("now"),
                "compensates_alias_id": params.get("prior_id"),
            }
            if "status = 'revoked'" in q:
                props["status"] = "revoked"
                props["revoked_at"] = params.get("now")
            self.aliases[props["id"]] = props
            return _Result({"id": props["id"], "revision": props["revision"]})

        # LearningLog
        if "CREATE (l:Operational:LearningLog)" in q:
            self.learning_logs.append(dict(params))
            return _Result({"id": params["id"]})

        # EntityProtection mutations before reads
        if "SET p.revoked_at = $now" in q and "EntityProtection" in q:
            for p in self.protections:
                if p.get("entity_id") == params["id"] and p.get("revoked_at") is None:
                    p["revoked_at"] = params.get("now")
                    if "revoked_by" in params:
                        p["revoked_by"] = params.get("actor")
            return _Result({"entity_id": params["id"]})

        if "CREATE (p:Operational:EntityProtection)" in q:
            props = {
                "entity_id": params["entity_id"],
                "protection_level": params["level"],
                "revision": params["revision"],
                "reason_code": params["reason_code"],
                "set_by": params["actor"],
                "created_at": params["now"],
                "effect_receipt_id": params["effect_id"],
                "revoked_at": None,
            }
            if "p.revoked_at = $now" in q:
                props["revoked_at"] = params["now"]
            self.protections.append(props)
            return _Result({"entity_id": props["entity_id"]})

        # Pinned protection on target
        if "MATCH (p:Operational:EntityProtection {entity_id: $id})" in q and "pinned" in q:
            for p in self.protections:
                if p.get("entity_id") == params["id"] and p.get("revoked_at") is None:
                    if p.get("protection_level") == "pinned":
                        return _Result(
                            {
                                "entity_id": p["entity_id"],
                                "revision": p.get("revision"),
                            }
                        )
            return _Result(None)

        # Source pin soft lookup — return none for simplicity unless set
        if "toLower(coalesce(n.name" in q:
            return _Result(None)

        # EntityProtection list active
        if (
            "MATCH (p:Operational:EntityProtection {entity_id: $id})" in q
            and "revoked_at IS NULL" in q
            and "ORDER BY" in q
        ):
            active = [
                p
                for p in self.protections
                if p.get("entity_id") == params["id"] and p.get("revoked_at") is None
            ]
            active.sort(key=lambda x: -int(x.get("revision") or 0))
            if not active:
                return _Result(None)
            p = active[0]
            return _Result(
                {
                    "entity_id": p["entity_id"],
                    "revision": p.get("revision"),
                    "protection_level": p.get("protection_level"),
                }
            )

        # Default empty
        return _Result(None)


class _FakeDriver:
    def __init__(self, session: _FakeSession):
        self._session = session

    def session(self, database: str = "neo4j"):  # noqa: ARG002
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):  # noqa: ANN001
        return False

    # session context manager
    def __call__(self):  # not used
        return self


def _store(session: _FakeSession) -> AliasEffectStore:
    def factory():
        class _Ctx:
            def __enter__(self_inner):
                class _Drv:
                    def session(self_drv, database="neo4j"):  # noqa: ARG002
                        class _SessCtx:
                            def __enter__(self_s):
                                return session

                            def __exit__(self_s, *a):  # noqa: ANN001
                                return False

                        return _SessCtx()

                return _Drv()

            def __exit__(self_inner, *a):  # noqa: ANN001
                return False

        return _Ctx()

    return AliasEffectStore(factory, "neo4j")


def _seed_person(session: _FakeSession, *, entity_id: str = "person-carid", name: str = "CarID"):
    session.entities[entity_id] = {
        "id": entity_id,
        "labels": ["Person"],
        "name": name,
    }


def _mint_and_apply(
    store: AliasEffectStore,
    session: _FakeSession,
    *,
    display_from: str = "CarPlace",
    entity_type: str = "Person",
    canonical_id: str = "person-carid",
    canonical_name: str = "CarID",
    scopes: list[str] | None = None,
    revision: int = 1,
    before_active: dict[str, Any] | None = None,
    expires_at: str | None = None,
    authority_id: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_alias_source(display_from)
    namespace = DEFAULT_NAMESPACE
    active_id = None if before_active is None else before_active.get("id")
    active_rev = None if before_active is None else before_active.get("revision")
    active_canon = None if before_active is None else before_active.get("canonical_id")
    before_fp = compute_before_fingerprint(
        namespace=namespace,
        entity_type=entity_type,
        normalized_from=normalized,
        active_alias_id=active_id,
        active_revision=active_rev,
        active_canonical_id=active_canon,
    )
    effect = alias_effect_payload(
        effect_type="apply_alias",
        namespace=namespace,
        entity_type=entity_type,
        normalized_from=normalized,
        display_from=display_from,
        canonical_id=canonical_id,
        canonical_name=canonical_name,
        revision=revision,
    )
    effect_hash = compute_alias_effect_hash(effect)
    target_ref = alias_lookup_key(
        namespace=namespace,
        entity_type=entity_type,
        normalized_from=normalized,
    )
    prop = store.create_alias_proposal(
        {
            "id": f"prop-{display_from}-{revision}",
            "namespace": namespace,
            "entity_type": entity_type,
            "display_from": display_from,
            "canonical_id": canonical_id,
            "canonical_name": canonical_name,
            "revision": revision,
            "before_fingerprint": before_fp,
        }
    )
    assert prop["outcome"] in {"created", "replayed"}
    # Force proposal effect hash to the computed one for this revision
    session.proposals[prop["proposal_id"]]["proposed_effect_hash"] = effect_hash
    session.proposals[prop["proposal_id"]]["effect_json"] = json.dumps(
        effect, sort_keys=True, separators=(",", ":")
    )
    session.proposals[prop["proposal_id"]]["before_fingerprint"] = before_fp

    mint_payload = {
        "id": authority_id or f"aa-{display_from}-{revision}",
        "proposal_id": prop["proposal_id"],
        "proposal_hash": effect_hash,
        "target_ref": target_ref,
        "before_fingerprint": before_fp,
        "artifact_or_effect_hash": effect_hash,
        "approver": "owner@test",
        "scopes": scopes or [],
    }
    if expires_at:
        mint_payload["expires_at"] = expires_at
        mint_payload["minted_at"] = "2026-07-10T11:00:00Z"
    mint = store.mint_activation_authority(mint_payload)
    assert mint["outcome"] == "created", mint
    applied = store.apply_alias(
        {
            "authority_id": mint["authority_id"],
            "nonce": mint["nonce"],
            "actor": "owner@test",
            "proposal_id": prop["proposal_id"],
            "namespace": namespace,
            "entity_type": entity_type,
            "display_from": display_from,
            "canonical_id": canonical_id,
            "canonical_name": canonical_name,
            "before_fingerprint": before_fp,
            "artifact_or_effect_hash": effect_hash,
        }
    )
    return {
        "proposal": prop,
        "mint": mint,
        "apply": applied,
        "effect_hash": effect_hash,
        "before_fp": before_fp,
        "target_ref": target_ref,
    }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_normalize_and_tokens():
    assert normalize_alias_source("  Car Place  ") == "car place"
    assert parse_apply_token("APPLY alias:prop-9") == "prop-9"
    assert parse_apply_token("please apply") is None
    assert is_generic_ack("YES")
    assert not is_generic_ack("APPLY alias:x")
    assert claim_false_may_mutate_life_memory() is False


def test_audit_unscoped_conflicting_cyclic():
    rows = [
        {"id": "a1", "from_name": "x", "canonical_id": "p1"},  # unscoped
        {
            "id": "a2",
            "namespace": "life",
            "entity_type": "Person",
            "normalized_from": "bob",
            "canonical_id": "p-bob",
            "status": "active",
        },
        {
            "id": "a3",
            "namespace": "life",
            "entity_type": "Person",
            "normalized_from": "bob",
            "canonical_id": "p-bob-2",
            "status": "active",
        },
        {
            "id": "a4",
            "namespace": "life",
            "entity_type": "Person",
            "normalized_from": "loop",
            "canonical_id": "a2",  # points at another alias id
            "status": "active",
        },
    ]
    report = audit_alias_rows(rows)
    kinds = {f["kind"] for f in report["findings"]}
    assert "unscoped" in kinds
    assert "conflicting" in kinds
    assert "cyclic" in kinds or "alias_target" in kinds
    assert report["review_required"] is True
    assert report["new_resolution_semantics_ready"] is False


def test_audit_clean_graph_ready():
    rows = [
        {
            "id": "a1",
            "namespace": "life",
            "entity_type": "Person",
            "normalized_from": "carplace",
            "canonical_id": "person-1",
            "status": "active",
            "revision": 1,
        }
    ]
    report = audit_alias_rows(rows)
    assert report["review_required"] is False
    assert report["new_resolution_semantics_ready"] is True


# ---------------------------------------------------------------------------
# Store behaviours
# ---------------------------------------------------------------------------


def test_claim_false_proposal_rejected():
    session = _FakeSession()
    store = _store(session)
    result = store.create_alias_proposal(
        {
            "entity_type": "Person",
            "display_from": "X",
            "canonical_id": "p1",
            "canonical_name": "X",
            "feedback_kind": "claim_false",
        }
    )
    assert result["outcome"] == "rejected"
    assert result["reason"] == "claim_false_propose_only"


def test_proposal_created_not_activated():
    session = _FakeSession()
    store = _store(session)
    result = store.create_alias_proposal(
        {
            "id": "prop-1",
            "entity_type": "Person",
            "display_from": "CarPlace",
            "canonical_id": "person-carid",
            "canonical_name": "CarID",
        }
    )
    assert result["outcome"] == "created"
    assert result["activation"] == "not_applied"
    assert result["status_projection"] == "review_pending"
    # Replay
    again = store.create_alias_proposal(
        {
            "id": "prop-1",
            "entity_type": "Person",
            "display_from": "CarPlace",
            "canonical_id": "person-carid",
            "canonical_name": "CarID",
        }
    )
    assert again["outcome"] == "replayed"
    # Changed payload conflict
    conflict = store.create_alias_proposal(
        {
            "id": "prop-1",
            "entity_type": "Person",
            "display_from": "Other",
            "canonical_id": "person-other",
            "canonical_name": "Other",
        }
    )
    assert conflict["outcome"] == "conflict"


def test_apply_happy_path_and_replay():
    session = _FakeSession()
    _seed_person(session)
    store = _store(session)
    out = _mint_and_apply(store, session)
    assert out["apply"]["outcome"] == "applied"
    assert out["apply"]["revision"] == 1
    assert any(a.get("status") == "active" for a in session.aliases.values())
    assert session.learning_logs
    # Authority consumed
    auth = session.authorities[out["mint"]["authority_id"]]
    assert auth["status"] == "consumed"
    # Replay via re-apply same authority → reconcile receipt
    replay = store.apply_alias(
        {
            "authority_id": out["mint"]["authority_id"],
            "nonce": out["mint"]["nonce"],
            "actor": "owner@test",
            "proposal_id": out["proposal"]["proposal_id"],
            "entity_type": "Person",
            "display_from": "CarPlace",
            "canonical_id": "person-carid",
            "canonical_name": "CarID",
            "before_fingerprint": out["before_fp"],
            "artifact_or_effect_hash": out["effect_hash"],
        }
    )
    assert replay["outcome"] == "replayed"
    assert replay["replacement_minted"] is False


def test_authority_receipt_reconcile_without_remint():
    session = _FakeSession()
    _seed_person(session)
    store = _store(session)
    out = _mint_and_apply(store, session)
    receipt = store.get_authority_receipt(out["mint"]["authority_id"])
    assert receipt["outcome"] == "found"
    assert receipt["status"] == "consumed"
    assert receipt["effect_receipt"] is not None
    assert receipt["replacement_minted"] is False
    # Re-mint with same id but a new nonce must NOT silently replace — conflict
    # (lost responses reconcile via get_authority_receipt only).
    mint2 = store.mint_activation_authority(
        {
            "id": out["mint"]["authority_id"],
            "proposal_id": out["proposal"]["proposal_id"],
            "proposal_hash": out["effect_hash"],
            "target_ref": out["target_ref"],
            "before_fingerprint": out["before_fp"],
            "artifact_or_effect_hash": out["effect_hash"],
            "approver": "owner@test",
        }
    )
    assert mint2["outcome"] == "conflict"
    assert mint2.get("nonce") is None
    assert mint2.get("reason") == "authority_id_reused"


def test_authority_expiry():
    session = _FakeSession()
    _seed_person(session)
    store = _store(session)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    out = _mint_and_apply(store, session, expires_at=past, authority_id="aa-expired")
    # apply inside _mint_and_apply already ran; re-mint fresh expired
    session2 = _FakeSession()
    _seed_person(session2)
    store2 = _store(session2)
    normalized = normalize_alias_source("CarPlace")
    effect = alias_effect_payload(
        effect_type="apply_alias",
        namespace="life",
        entity_type="Person",
        normalized_from=normalized,
        display_from="CarPlace",
        canonical_id="person-carid",
        canonical_name="CarID",
        revision=1,
    )
    effect_hash = compute_alias_effect_hash(effect)
    before_fp = compute_before_fingerprint(
        namespace="life",
        entity_type="Person",
        normalized_from=normalized,
        active_alias_id=None,
        active_revision=None,
        active_canonical_id=None,
    )
    target_ref = alias_lookup_key(
        namespace="life", entity_type="Person", normalized_from=normalized
    )
    mint = store2.mint_activation_authority(
        {
            "id": "aa-exp2",
            "proposal_id": "prop-exp",
            "proposal_hash": effect_hash,
            "target_ref": target_ref,
            "before_fingerprint": before_fp,
            "artifact_or_effect_hash": effect_hash,
            "approver": "owner@test",
            "expires_at": past,
            "minted_at": past,
        }
    )
    assert mint["outcome"] == "created"
    applied = store2.apply_alias(
        {
            "authority_id": "aa-exp2",
            "nonce": mint["nonce"],
            "actor": "owner@test",
            "entity_type": "Person",
            "display_from": "CarPlace",
            "canonical_id": "person-carid",
            "canonical_name": "CarID",
            "before_fingerprint": before_fp,
            "artifact_or_effect_hash": effect_hash,
        }
    )
    assert applied["outcome"] == "failed"
    assert applied["reason"] == "authority_expired"


def test_missing_target_wrong_type_alias_target():
    session = _FakeSession()
    store = _store(session)
    # missing
    out = _mint_and_apply(store, session, authority_id="aa-miss")
    assert out["apply"]["outcome"] == "failed"
    assert out["apply"]["reason"] == "missing_target"

    # wrong type
    session2 = _FakeSession()
    session2.entities["org-1"] = {
        "id": "org-1",
        "labels": ["Organization"],
        "name": "CarID",
    }
    store2 = _store(session2)
    out2 = _mint_and_apply(
        store2,
        session2,
        canonical_id="org-1",
        authority_id="aa-wrong",
    )
    assert out2["apply"]["outcome"] == "failed"
    assert out2["apply"]["reason"] == "wrong_type"

    # alias target
    session3 = _FakeSession()
    session3.aliases["alias-other"] = {
        "id": "alias-other",
        "status": "active",
        "canonical_id": "person-x",
    }
    store3 = _store(session3)
    out3 = _mint_and_apply(
        store3,
        session3,
        canonical_id="alias-other",
        authority_id="aa-alias-tgt",
    )
    assert out3["apply"]["outcome"] == "failed"
    assert out3["apply"]["reason"] == "alias_target_forbidden"


def test_stale_before_fingerprint():
    session = _FakeSession()
    _seed_person(session)
    store = _store(session)
    # Pre-seed an active alias so live before_fp differs from empty
    session.aliases["pre"] = {
        "id": "pre",
        "namespace": "life",
        "entity_type": "Person",
        "normalized_from": normalize_alias_source("CarPlace"),
        "canonical_id": "person-other",
        "canonical_name": "Other",
        "revision": 1,
        "status": "active",
    }
    # Mint with empty-before (stale relative to live)
    normalized = normalize_alias_source("CarPlace")
    effect = alias_effect_payload(
        effect_type="apply_alias",
        namespace="life",
        entity_type="Person",
        normalized_from=normalized,
        display_from="CarPlace",
        canonical_id="person-carid",
        canonical_name="CarID",
        revision=2,
    )
    effect_hash = compute_alias_effect_hash(effect)
    empty_before = compute_before_fingerprint(
        namespace="life",
        entity_type="Person",
        normalized_from=normalized,
        active_alias_id=None,
        active_revision=None,
        active_canonical_id=None,
    )
    target_ref = alias_lookup_key(
        namespace="life", entity_type="Person", normalized_from=normalized
    )
    mint = store.mint_activation_authority(
        {
            "id": "aa-stale",
            "proposal_id": "prop-stale",
            "proposal_hash": effect_hash,
            "target_ref": target_ref,
            "before_fingerprint": empty_before,
            "artifact_or_effect_hash": effect_hash,
            "approver": "owner@test",
        }
    )
    applied = store.apply_alias(
        {
            "authority_id": "aa-stale",
            "nonce": mint["nonce"],
            "actor": "owner@test",
            "entity_type": "Person",
            "display_from": "CarPlace",
            "canonical_id": "person-carid",
            "canonical_name": "CarID",
            "before_fingerprint": empty_before,
            "artifact_or_effect_hash": effect_hash,
        }
    )
    assert applied["outcome"] == "stale"


def test_changed_payload_conflict():
    session = _FakeSession()
    _seed_person(session)
    store = _store(session)
    normalized = normalize_alias_source("CarPlace")
    effect = alias_effect_payload(
        effect_type="apply_alias",
        namespace="life",
        entity_type="Person",
        normalized_from=normalized,
        display_from="CarPlace",
        canonical_id="person-carid",
        canonical_name="CarID",
        revision=1,
    )
    effect_hash = compute_alias_effect_hash(effect)
    before_fp = compute_before_fingerprint(
        namespace="life",
        entity_type="Person",
        normalized_from=normalized,
        active_alias_id=None,
        active_revision=None,
        active_canonical_id=None,
    )
    target_ref = alias_lookup_key(
        namespace="life", entity_type="Person", normalized_from=normalized
    )
    store.create_alias_proposal(
        {
            "id": "prop-change",
            "entity_type": "Person",
            "display_from": "CarPlace",
            "canonical_id": "person-carid",
            "canonical_name": "CarID",
            "revision": 1,
            "before_fingerprint": before_fp,
        }
    )
    session.proposals["prop-change"]["proposed_effect_hash"] = effect_hash
    session.proposals["prop-change"]["effect_json"] = json.dumps(
        effect, sort_keys=True, separators=(",", ":")
    )
    mint = store.mint_activation_authority(
        {
            "id": "aa-change",
            "proposal_id": "prop-change",
            "proposal_hash": effect_hash,
            "target_ref": target_ref,
            "before_fingerprint": before_fp,
            "artifact_or_effect_hash": effect_hash,
            "approver": "owner@test",
        }
    )
    # Apply with different canonical than proposal/authority
    applied = store.apply_alias(
        {
            "authority_id": "aa-change",
            "nonce": mint["nonce"],
            "actor": "owner@test",
            "proposal_id": "prop-change",
            "entity_type": "Person",
            "display_from": "CarPlace",
            "canonical_id": "person-OTHER",
            "canonical_name": "OTHER",
            "before_fingerprint": before_fp,
            "artifact_or_effect_hash": effect_hash,
        }
    )
    assert applied["outcome"] in {"conflict", "failed"}


def test_duplicate_active_alias_replayed():
    session = _FakeSession()
    _seed_person(session)
    store = _store(session)
    out = _mint_and_apply(store, session, authority_id="aa-dup1")
    assert out["apply"]["outcome"] == "applied"
    # Second apply same mapping with new authority should see already active
    active = next(a for a in session.aliases.values() if a["status"] == "active")
    out2 = _mint_and_apply(
        store,
        session,
        authority_id="aa-dup2",
        revision=1,
        before_active=None,  # intentional wrong before → may stale OR
    )
    # With empty before but live active exists → stale is also acceptable.
    # Force correct before for already-active path:
    normalized = normalize_alias_source("CarPlace")
    before_fp = compute_before_fingerprint(
        namespace="life",
        entity_type="Person",
        normalized_from=normalized,
        active_alias_id=active["id"],
        active_revision=active["revision"],
        active_canonical_id=active["canonical_id"],
    )
    effect = alias_effect_payload(
        effect_type="apply_alias",
        namespace="life",
        entity_type="Person",
        normalized_from=normalized,
        display_from="CarPlace",
        canonical_id="person-carid",
        canonical_name="CarID",
        revision=2,
    )
    effect_hash = compute_alias_effect_hash(effect)
    target_ref = alias_lookup_key(
        namespace="life", entity_type="Person", normalized_from=normalized
    )
    mint = store.mint_activation_authority(
        {
            "id": "aa-dup3",
            "proposal_id": "prop-dup3",
            "proposal_hash": effect_hash,
            "target_ref": target_ref,
            "before_fingerprint": before_fp,
            "artifact_or_effect_hash": effect_hash,
            "approver": "owner@test",
        }
    )
    applied = store.apply_alias(
        {
            "authority_id": "aa-dup3",
            "nonce": mint["nonce"],
            "actor": "owner@test",
            "entity_type": "Person",
            "display_from": "CarPlace",
            "canonical_id": "person-carid",
            "canonical_name": "CarID",
            "before_fingerprint": before_fp,
            "artifact_or_effect_hash": effect_hash,
        }
    )
    assert applied["outcome"] == "replayed"
    assert applied["reason"] == "alias_already_active"


def test_revoke_is_compensating_not_delete():
    session = _FakeSession()
    _seed_person(session)
    store = _store(session)
    out = _mint_and_apply(store, session, authority_id="aa-rev-setup")
    assert out["apply"]["outcome"] == "applied"
    active = next(a for a in session.aliases.values() if a["status"] == "active")
    prior_id = active["id"]

    normalized = normalize_alias_source("CarPlace")
    before_fp = compute_before_fingerprint(
        namespace="life",
        entity_type="Person",
        normalized_from=normalized,
        active_alias_id=active["id"],
        active_revision=active["revision"],
        active_canonical_id=active["canonical_id"],
    )
    effect = alias_effect_payload(
        effect_type="revoke_alias",
        namespace="life",
        entity_type="Person",
        normalized_from=normalized,
        display_from="CarPlace",
        canonical_id=active["canonical_id"],
        canonical_name=active["canonical_name"],
        revision=2,
    )
    effect_hash = compute_alias_effect_hash(effect)
    target_ref = alias_lookup_key(
        namespace="life", entity_type="Person", normalized_from=normalized
    )
    mint = store.mint_activation_authority(
        {
            "id": "aa-revoke",
            "proposal_id": "prop-revoke",
            "proposal_hash": effect_hash,
            "target_ref": target_ref,
            "before_fingerprint": before_fp,
            "artifact_or_effect_hash": effect_hash,
            "approver": "owner@test",
        }
    )
    revoked = store.revoke_alias(
        {
            "authority_id": "aa-revoke",
            "nonce": mint["nonce"],
            "actor": "owner@test",
            "entity_type": "Person",
            "display_from": "CarPlace",
            "before_fingerprint": before_fp,
            "artifact_or_effect_hash": effect_hash,
        }
    )
    assert revoked["outcome"] == "applied"
    # Prior node still present (revoked), plus compensating revision
    assert prior_id in session.aliases
    assert session.aliases[prior_id]["status"] == "revoked"
    assert any(
        a.get("status") == "revoked" and a.get("compensates_alias_id") == prior_id
        for a in session.aliases.values()
    )
    # No hard delete
    assert len(session.aliases) >= 2


def test_pinned_identity_requires_scope():
    session = _FakeSession()
    _seed_person(session)
    session.protections.append(
        {
            "entity_id": "person-carid",
            "protection_level": "pinned",
            "revision": 1,
            "revoked_at": None,
        }
    )
    store = _store(session)
    # Without scope
    out = _mint_and_apply(store, session, authority_id="aa-pin-no", scopes=[])
    assert out["apply"]["outcome"] == "failed"
    assert out["apply"]["reason"] == "pinned_identity_authority_required"

    # With scope
    session2 = _FakeSession()
    _seed_person(session2)
    session2.protections.append(
        {
            "entity_id": "person-carid",
            "protection_level": "pinned",
            "revision": 1,
            "revoked_at": None,
        }
    )
    store2 = _store(session2)
    out2 = _mint_and_apply(
        store2,
        session2,
        authority_id="aa-pin-yes",
        scopes=[PINNED_IDENTITY_SCOPE],
    )
    assert out2["apply"]["outcome"] == "applied"


def test_entity_protection_set_and_revoke():
    session = _FakeSession()
    _seed_person(session)
    store = _store(session)
    mint = store.mint_activation_authority(
        {
            "id": "aa-prot",
            "proposal_id": "prop-prot",
            "proposal_hash": "h1",
            "target_ref": "entity:person-carid",
            "before_fingerprint": "b1",
            "artifact_or_effect_hash": "h1",
            "approver": "owner@test",
            "scopes": [PINNED_IDENTITY_SCOPE],
        }
    )
    set_r = store.set_entity_protection(
        {
            "authority_id": "aa-prot",
            "nonce": mint["nonce"],
            "entity_id": "person-carid",
            "actor": "owner@test",
        }
    )
    assert set_r["outcome"] == "applied"
    assert any(
        p["entity_id"] == "person-carid" and p.get("revoked_at") is None
        for p in session.protections
    )

    mint2 = store.mint_activation_authority(
        {
            "id": "aa-prot2",
            "proposal_id": "prop-prot2",
            "proposal_hash": "h2",
            "target_ref": "entity:person-carid",
            "before_fingerprint": "b2",
            "artifact_or_effect_hash": "h2",
            "approver": "owner@test",
            "scopes": [PINNED_IDENTITY_SCOPE],
        }
    )
    rev = store.revoke_entity_protection(
        {
            "authority_id": "aa-prot2",
            "nonce": mint2["nonce"],
            "entity_id": "person-carid",
            "actor": "owner@test",
        }
    )
    assert rev["outcome"] == "applied"


def test_store_audit_aliases():
    session = _FakeSession()
    session.aliases["legacy"] = {
        "id": "legacy",
        "from_name": "x",
        "canonical_id": "p1",
    }
    store = _store(session)
    report = store.audit_aliases()
    assert report["review_required"] is True


def test_operator_script_rejects_yes_flag():
    from scripts.digital_brain_apply_proposal import main

    code = main(["--yes", "audit"])
    assert code == 2
    code = main(["audit", "--force"])
    assert code == 2


def test_activation_not_exported_as_mcp_tool_name():
    """Gate: activation surfaces stay off model-facing MCP tool list source."""
    api = (
        ROOT
        / "mcp_servers"
        / "cypher"
        / "src"
        / "digital_brain_mcp_cypher"
        / "quality_control_api.py"
    ).read_text(encoding="utf-8")
    # apply_alias is forbidden name, not a coordinator operation
    assert "apply_alias" in api
    assert '"apply_alias"' not in api.split("COORDINATOR_OPERATIONS")[1].split(
        "WORKFLOW_OPERATIONS"
    )[0]
