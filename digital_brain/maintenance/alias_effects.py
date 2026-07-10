"""Operator-only Alias proposal/effect helpers and transactions.

ActivationAuthority mint/consume and Alias apply/revoke stay off model-facing
MCP and off maintainer/analyzer toolsets. The host operator script
``scripts/digital_brain_apply_proposal.py`` is the human path.

Unalias is a compensating revision + EffectReceipt, never a hard delete.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from digital_brain.maintenance.models import (
    AUTHORITY_STATUSES,
    assert_legal_authority_transition,
    compute_authority_request_fingerprint,
    digest_text,
)

DEFAULT_NAMESPACE = "life"
DEFAULT_AUTHORITY_TTL_SECONDS = 900
PINNED_IDENTITY_SCOPE = "pinned_identity"
ALIAS_EFFECT_TYPES = frozenset({"apply_alias", "revoke_alias"})
PROTECTION_EFFECT_TYPES = frozenset({"set_entity_protection", "revoke_entity_protection"})
PROTECTION_LEVELS = frozenset({"pinned"})

_APPLY_TOKEN_RE = re.compile(
    r"^\s*APPLY\s+alias:(?P<id>[A-Za-z0-9_.:\-]+)\s*$",
    re.IGNORECASE,
)
_GENERIC_ACK_RE = re.compile(
    r"^\s*(yes|y|ok|okay|sure|go\s*ahead|👍|👍🏻|👍🏼|👍🏽|👍🏾|👍🏿)\s*[.!]*\s*$",
    re.IGNORECASE,
)


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def normalize_alias_source(value: str) -> str:
    """Normalize display/source text for uniqueness keys."""
    if not isinstance(value, str):
        raise TypeError("alias source must be a string")
    # Collapse whitespace, strip, lower — stable across locales for ASCII names.
    collapsed = re.sub(r"\s+", " ", value.strip())
    if not collapsed:
        raise ValueError("alias source must be non-empty")
    return collapsed.casefold()


def is_generic_ack(text: str) -> bool:
    """True when prose is a generic acknowledgement (never activation)."""
    if not isinstance(text, str):
        return False
    return bool(_GENERIC_ACK_RE.match(text))


def parse_apply_token(text: str) -> str | None:
    """Return proposal_id from ``APPLY alias:<id>`` or None."""
    if not isinstance(text, str):
        return None
    match = _APPLY_TOKEN_RE.match(text)
    if not match:
        return None
    return match.group("id")


def claim_false_may_mutate_life_memory() -> bool:
    """claim_false stays propose-only until Claim provenance exists."""
    return False


def alias_effect_payload(
    *,
    effect_type: str,
    namespace: str,
    entity_type: str,
    normalized_from: str,
    display_from: str,
    canonical_id: str,
    canonical_name: str,
    revision: int,
) -> dict[str, Any]:
    return {
        "canonical_id": canonical_id,
        "canonical_name": canonical_name,
        "display_from": display_from,
        "effect_type": effect_type,
        "entity_type": entity_type,
        "namespace": namespace,
        "normalized_from": normalized_from,
        "revision": int(revision),
    }


def compute_alias_effect_hash(payload: Mapping[str, Any]) -> str:
    return digest_text(_canonical_json(dict(payload)))


def compute_before_fingerprint(
    *,
    namespace: str,
    entity_type: str,
    normalized_from: str,
    active_alias_id: str | None,
    active_revision: int | None,
    active_canonical_id: str | None,
) -> str:
    return digest_text(
        _canonical_json(
            {
                "active_alias_id": active_alias_id,
                "active_canonical_id": active_canonical_id,
                "active_revision": active_revision,
                "entity_type": entity_type,
                "namespace": namespace,
                "normalized_from": normalized_from,
            }
        )
    )


def compute_request_hash(payload: Mapping[str, Any]) -> str:
    return digest_text(_canonical_json(dict(payload)))


def alias_lookup_key(
    *,
    namespace: str,
    entity_type: str,
    normalized_from: str,
) -> str:
    return f"{namespace}|{entity_type}|{normalized_from}"


def entity_protection_target_ref(entity_id: str) -> str:
    """ActivationAuthority target_ref for EntityProtection effects."""
    return f"entity:{_require_str(entity_id, 'entity_id')}"


def protection_effect_binding(
    *,
    entity_id: str,
    effect_type: str,
) -> str:
    """Canonical artifact_or_effect_hash for protection mint/consume binding."""
    if effect_type not in PROTECTION_EFFECT_TYPES and effect_type not in {
        "set",
        "revoke",
    }:
        raise ValueError("invalid protection effect_type for binding")
    if effect_type == "set_entity_protection":
        action = "set"
    elif effect_type == "revoke_entity_protection":
        action = "revoke"
    else:
        action = effect_type
    return f"protect:{_require_str(entity_id, 'entity_id')}:{action}"


def compute_protection_before_fingerprint(
    *,
    entity_id: str,
    active_revision: int | None,
    protection_level: str | None,
) -> str:
    return digest_text(
        _canonical_json(
            {
                "active_revision": active_revision,
                "entity_id": entity_id,
                "protection_level": protection_level,
            }
        )
    )


@dataclass(frozen=True)
class AliasAuditFinding:
    kind: str  # unscoped | conflicting | cyclic | missing_target | alias_target
    alias_id: str | None
    detail: str
    severity: str = "review_required"


def audit_alias_rows(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Audit existing Alias nodes for migration review.

    New resolution semantics must not silently activate over unscoped,
    conflicting, or cyclic graphs — human review is required first.
    """
    findings: list[dict[str, Any]] = []
    active_by_key: dict[str, list[Mapping[str, Any]]] = {}
    by_id = {str(r.get("id")): r for r in rows if r.get("id") is not None}

    for row in rows:
        alias_id = str(row.get("id") or "") or None
        namespace = row.get("namespace")
        entity_type = row.get("entity_type")
        normalized = row.get("normalized_from")
        status = row.get("status") or "active"
        canonical_id = row.get("canonical_id")

        missing_scope = not (
            isinstance(namespace, str)
            and namespace.strip()
            and isinstance(entity_type, str)
            and entity_type.strip()
            and isinstance(normalized, str)
            and normalized.strip()
        )
        if missing_scope:
            findings.append(
                AliasAuditFinding(
                    kind="unscoped",
                    alias_id=alias_id,
                    detail="missing namespace, entity_type, and/or normalized_from",
                ).__dict__
            )

        if status == "active" and not missing_scope:
            key = alias_lookup_key(
                namespace=str(namespace),
                entity_type=str(entity_type),
                normalized_from=str(normalized),
            )
            active_by_key.setdefault(key, []).append(row)

        if isinstance(canonical_id, str) and canonical_id in by_id:
            findings.append(
                AliasAuditFinding(
                    kind="cyclic",
                    alias_id=alias_id,
                    detail=f"canonical_id {canonical_id} points at another Alias",
                ).__dict__
            )
            findings.append(
                AliasAuditFinding(
                    kind="alias_target",
                    alias_id=alias_id,
                    detail="Alias targets must be canonical entities, never Alias nodes",
                ).__dict__
            )

    for key, group in active_by_key.items():
        if len(group) > 1:
            ids = [str(g.get("id")) for g in group]
            findings.append(
                AliasAuditFinding(
                    kind="conflicting",
                    alias_id=ids[0],
                    detail=f"multiple active aliases for key {key}: {ids}",
                ).__dict__
            )

    review_required = any(f.get("severity") == "review_required" for f in findings)
    return {
        "outcome": "ok",
        "alias_count": len(rows),
        "finding_count": len(findings),
        "findings": findings,
        "review_required": review_required,
        "new_resolution_semantics_ready": not review_required,
        "message": (
            "Alias graph clean for scoped active resolution"
            if not review_required
            else "Human review required before new resolution semantics activate"
        ),
    }


def proposal_may_activate_from_prose() -> bool:
    return False


# ---------------------------------------------------------------------------
# Store (operator path; Neo4j quality credentials)
# ---------------------------------------------------------------------------


def _require_str(value: Any, field: str, *, max_len: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    text = value.strip()
    if len(text) > max_len:
        raise ValueError(f"{field} exceeds max length {max_len}")
    return text


def _require_enum(value: Any, allowed: frozenset[str], field: str) -> str:
    text = _require_str(value, field)
    if text not in allowed:
        raise ValueError(f"{field} must be one of {sorted(allowed)}")
    return text


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


def _run_all(runner: Any, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    result = runner.run(query, params or {})
    if hasattr(result, "data"):
        return list(result.data())
    rows: list[dict[str, Any]] = []
    for record in result:
        if hasattr(record, "data"):
            rows.append(record.data())
        else:
            rows.append(dict(record))
    return rows


class AliasEffectStore:
    """Operator-only Alias / ActivationAuthority / EntityProtection transactions.

    Never register these methods as FastMCP tools. Call only from the operator
    script or host control path with quality/admin credentials.
    """

    def __init__(self, driver_factory: Callable[[], Any], database: str = "neo4j"):
        self._driver_factory = driver_factory
        self._database = database

    def _with_session(self, operation: Callable[[Any], Any]) -> Any:
        with self._driver_factory() as driver:
            with driver.session(database=self._database) as session:
                return operation(session)

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def audit_aliases(self) -> dict[str, Any]:
        def operation(session: Any) -> dict[str, Any]:
            rows = _run_all(
                session,
                """
                MATCH (a:Alias)
                RETURN a.id AS id,
                       a.namespace AS namespace,
                       a.entity_type AS entity_type,
                       a.normalized_from AS normalized_from,
                       a.from_name AS from_name,
                       a.canonical_id AS canonical_id,
                       a.status AS status,
                       a.revision AS revision
                """,
            )
            return audit_alias_rows(rows)

        return self._with_session(operation)

    def get_active_alias(
        self,
        *,
        namespace: str,
        entity_type: str,
        normalized_from: str,
    ) -> dict[str, Any] | None:
        """Return the highest-revision active Alias for a scoped lookup key."""
        namespace = _require_str(namespace, "namespace")
        entity_type = _require_str(entity_type, "entity_type")
        normalized_from = normalize_alias_source(normalized_from)

        def operation(session: Any) -> dict[str, Any] | None:
            return _run_one(
                session,
                """
                MATCH (a:Alias)
                WHERE coalesce(a.status, 'active') = 'active'
                  AND coalesce(a.namespace, $namespace) = $namespace
                  AND a.entity_type = $entity_type
                  AND a.normalized_from = $normalized_from
                RETURN a.id AS id,
                       a.revision AS revision,
                       a.canonical_id AS canonical_id,
                       a.canonical_name AS canonical_name,
                       a.display_from AS display_from,
                       a.from_name AS from_name,
                       a.namespace AS namespace,
                       a.entity_type AS entity_type,
                       a.normalized_from AS normalized_from
                ORDER BY coalesce(a.revision, 0) DESC, a.id ASC
                LIMIT 1
                """,
                {
                    "namespace": namespace,
                    "entity_type": entity_type,
                    "normalized_from": normalized_from,
                },
            )

        return self._with_session(operation)

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        """Read a Proposal card for operator mint binding (effect_json, hashes)."""
        proposal_id = _require_str(proposal_id, "proposal_id")

        def operation(session: Any) -> dict[str, Any] | None:
            return _run_one(
                session,
                """
                MATCH (p:Operational:Proposal {id: $id})
                RETURN p.id AS id,
                       p.kind AS kind,
                       p.target_ref AS target_ref,
                       p.before_fingerprint AS before_fingerprint,
                       p.proposed_effect_hash AS proposed_effect_hash,
                       p.effect_json AS effect_json,
                       p.status_projection AS status_projection,
                       p.request_fingerprint AS request_fingerprint
                LIMIT 1
                """,
                {"id": proposal_id},
            )

        return self._with_session(operation)

    def get_active_protection(self, entity_id: str) -> dict[str, Any] | None:
        """Return the highest-revision non-revoked EntityProtection for entity_id."""
        entity_id = _require_str(entity_id, "entity_id")

        def operation(session: Any) -> dict[str, Any] | None:
            return _run_one(
                session,
                """
                MATCH (p:Operational:EntityProtection {entity_id: $id})
                WHERE p.revoked_at IS NULL
                RETURN p.entity_id AS entity_id,
                       p.revision AS revision,
                       p.protection_level AS protection_level
                ORDER BY coalesce(p.revision, 0) DESC
                LIMIT 1
                """,
                {"id": entity_id},
            )

        return self._with_session(operation)

    def live_alias_mint_binding(
        self,
        *,
        namespace: str,
        entity_type: str,
        normalized_from: str,
        display_from: str,
        effect_type: str,
        canonical_id: str | None = None,
        canonical_name: str | None = None,
        revision: int | None = None,
    ) -> dict[str, Any]:
        """Compute before_fingerprint + effect_hash from live graph state.

        Prefer this over provisional empty/pending defaults so mint bindings
        match store validation at apply/revoke time.
        """
        if effect_type not in ALIAS_EFFECT_TYPES:
            raise ValueError("effect_type must be apply_alias or revoke_alias")
        namespace = _require_str(namespace or DEFAULT_NAMESPACE, "namespace")
        entity_type = _require_str(entity_type, "entity_type")
        display_from = _require_str(display_from, "display_from")
        normalized_from = normalize_alias_source(normalized_from or display_from)
        active = self.get_active_alias(
            namespace=namespace,
            entity_type=entity_type,
            normalized_from=normalized_from,
        )
        before_fp = compute_before_fingerprint(
            namespace=namespace,
            entity_type=entity_type,
            normalized_from=normalized_from,
            active_alias_id=None if active is None else active.get("id"),
            active_revision=None if active is None else active.get("revision"),
            active_canonical_id=None if active is None else active.get("canonical_id"),
        )
        next_revision = (
            int(revision)
            if revision is not None
            else int((active or {}).get("revision") or 0) + 1
        )
        if effect_type == "revoke_alias":
            if active is None:
                return {
                    "outcome": "failed",
                    "reason": "no_active_alias_to_revoke",
                    "before_fingerprint": before_fp,
                    "active": None,
                }
            cid = str(canonical_id or active.get("canonical_id") or "")
            cname = str(
                canonical_name
                or active.get("canonical_name")
                or active.get("to_name")
                or cid
            )
        else:
            cid = _require_str(canonical_id, "canonical_id")
            cname = _require_str(canonical_name or cid, "canonical_name")
        effect = alias_effect_payload(
            effect_type=effect_type,
            namespace=namespace,
            entity_type=entity_type,
            normalized_from=normalized_from,
            display_from=display_from,
            canonical_id=cid,
            canonical_name=cname,
            revision=next_revision,
        )
        effect_hash = compute_alias_effect_hash(effect)
        target_ref = alias_lookup_key(
            namespace=namespace,
            entity_type=entity_type,
            normalized_from=normalized_from,
        )
        return {
            "outcome": "ok",
            "namespace": namespace,
            "entity_type": entity_type,
            "normalized_from": normalized_from,
            "display_from": display_from,
            "canonical_id": cid,
            "canonical_name": cname,
            "revision": next_revision,
            "before_fingerprint": before_fp,
            "effect_hash": effect_hash,
            "effect": effect,
            "target_ref": target_ref,
            "active": active,
        }

    # ------------------------------------------------------------------
    # Online / operator proposal (no dream lease; review_pending only)
    # ------------------------------------------------------------------

    def create_alias_proposal(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a typed Alias proposal card (propose-only; never activates)."""
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")

        proposal_id = _require_str(payload.get("id") or f"prop-{uuid.uuid4()}", "id")
        namespace = _require_str(payload.get("namespace") or DEFAULT_NAMESPACE, "namespace")
        entity_type = _require_str(payload.get("entity_type"), "entity_type")
        display_from = _require_str(
            payload.get("display_from") or payload.get("from_name"), "display_from"
        )
        normalized_from = normalize_alias_source(
            payload.get("normalized_from") or display_from
        )
        canonical_id = _require_str(payload.get("canonical_id"), "canonical_id")
        canonical_name = _require_str(
            payload.get("canonical_name") or canonical_id, "canonical_name"
        )
        feedback_id = payload.get("feedback_id")
        if feedback_id is not None:
            feedback_id = _require_str(feedback_id, "feedback_id")
        title = _require_str(
            payload.get("title")
            or f"Alias {display_from!r} → {canonical_name} ({entity_type})",
            "title",
            max_len=256,
        )
        kind = _require_str(payload.get("kind") or "alias", "kind")
        if kind not in {"alias", "revoke_alias"}:
            raise ValueError("kind must be alias or revoke_alias")
        if kind == "alias" and payload.get("feedback_kind") == "claim_false":
            # Explicit guard: claim_false cannot produce an activating alias proposal.
            if not claim_false_may_mutate_life_memory():
                return {
                    "outcome": "rejected",
                    "reason": "claim_false_propose_only",
                    "message": (
                        "claim_false remains propose-only until Claim provenance exists"
                    ),
                }

        effect = alias_effect_payload(
            effect_type="apply_alias" if kind == "alias" else "revoke_alias",
            namespace=namespace,
            entity_type=entity_type,
            normalized_from=normalized_from,
            display_from=display_from,
            canonical_id=canonical_id,
            canonical_name=canonical_name,
            revision=int(payload.get("revision") or 1),
        )
        proposed_effect_hash = compute_alias_effect_hash(effect)
        target_ref = alias_lookup_key(
            namespace=namespace,
            entity_type=entity_type,
            normalized_from=normalized_from,
        )
        before_fingerprint = _require_str(
            payload.get("before_fingerprint")
            or compute_before_fingerprint(
                namespace=namespace,
                entity_type=entity_type,
                normalized_from=normalized_from,
                active_alias_id=None,
                active_revision=None,
                active_canonical_id=None,
            ),
            "before_fingerprint",
            max_len=128,
        )

        identity = {
            "canonical_id": canonical_id,
            "id": proposal_id,
            "kind": kind,
            "normalized_from": normalized_from,
            "target_ref": target_ref,
        }
        request_fingerprint = digest_text(_canonical_json(identity))

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._create_alias_proposal_tx(
                    tx,
                    proposal_id=proposal_id,
                    kind=kind,
                    title=title,
                    target_ref=target_ref,
                    before_fingerprint=before_fingerprint,
                    proposed_effect_hash=proposed_effect_hash,
                    effect=effect,
                    feedback_id=feedback_id,
                    request_fingerprint=request_fingerprint,
                ),
            )

        return self._with_session(operation)

    def _create_alias_proposal_tx(
        self,
        tx: Any,
        *,
        proposal_id: str,
        kind: str,
        title: str,
        target_ref: str,
        before_fingerprint: str,
        proposed_effect_hash: str,
        effect: dict[str, Any],
        feedback_id: str | None,
        request_fingerprint: str,
    ) -> dict[str, Any]:
        existing = _run_one(
            tx,
            """
            MATCH (p:Operational:Proposal {id: $id})
            RETURN p.id AS id,
                   p.request_fingerprint AS request_fingerprint,
                   p.status_projection AS status_projection,
                   p.proposed_effect_hash AS proposed_effect_hash
            LIMIT 1
            """,
            {"id": proposal_id},
        )
        if existing is not None:
            if existing.get("request_fingerprint") == request_fingerprint:
                return {
                    "outcome": "replayed",
                    "proposal_id": existing["id"],
                    "status_projection": existing.get("status_projection"),
                    "proposed_effect_hash": existing.get("proposed_effect_hash"),
                    "request_fingerprint": request_fingerprint,
                    "activation": "not_applied",
                }
            return {
                "outcome": "conflict",
                "reason": "proposal_id_reused",
                "proposal_id": existing["id"],
            }

        created = _run_one(
            tx,
            """
            CREATE (p:Operational:Proposal)
            SET p.id = $id,
                p.kind = $kind,
                p.title = $title,
                p.status_projection = 'review_pending',
                p.target_ref = $target_ref,
                p.scope = 'identity',
                p.risk_tier = 'high',
                p.reversibility = 'reversible',
                p.evidence_snapshot_id = coalesce($feedback_id, 'online-feedback'),
                p.evidence_strength = 'moderate',
                p.before_fingerprint = $before_fingerprint,
                p.proposed_effect_hash = $proposed_effect_hash,
                p.effect_json = $effect_json,
                p.request_fingerprint = $fp,
                p.created_at = datetime()
            RETURN p.id AS id, p.status_projection AS status_projection
            """,
            {
                "id": proposal_id,
                "kind": kind,
                "title": title,
                "target_ref": target_ref,
                "feedback_id": feedback_id,
                "before_fingerprint": before_fingerprint,
                "proposed_effect_hash": proposed_effect_hash,
                "effect_json": _canonical_json(effect),
                "fp": request_fingerprint,
            },
        )
        if created is None:
            raise RuntimeError("Proposal create returned no row")
        return {
            "outcome": "created",
            "proposal_id": created["id"],
            "status_projection": created["status_projection"],
            "proposed_effect_hash": proposed_effect_hash,
            "before_fingerprint": before_fingerprint,
            "target_ref": target_ref,
            "request_fingerprint": request_fingerprint,
            # Explicit: proposal is never activation.
            "activation": "not_applied",
        }

    # ------------------------------------------------------------------
    # ActivationAuthority: mint / receipt-read / atomic consume+effect
    # ------------------------------------------------------------------

    def mint_activation_authority(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")

        authority_id = _require_str(payload.get("id") or f"aa-{uuid.uuid4()}", "id")
        decision_id = _require_str(payload.get("decision_id") or f"dec-{uuid.uuid4()}", "decision_id")
        proposal_id = _require_str(payload.get("proposal_id"), "proposal_id")
        proposal_hash = _require_str(payload.get("proposal_hash"), "proposal_hash")
        target_ref = _require_str(payload.get("target_ref"), "target_ref")
        before_fingerprint = _require_str(
            payload.get("before_fingerprint"), "before_fingerprint"
        )
        artifact_or_effect_hash = _require_str(
            payload.get("artifact_or_effect_hash"), "artifact_or_effect_hash"
        )
        approver = _require_str(payload.get("approver"), "approver")
        scopes = payload.get("scopes") or []
        if not isinstance(scopes, list):
            raise TypeError("scopes must be an array")
        scopes_norm = sorted({str(s).strip() for s in scopes if str(s).strip()})
        ttl = int(payload.get("ttl_seconds") or DEFAULT_AUTHORITY_TTL_SECONDS)
        if ttl < 30 or ttl > 86_400:
            raise ValueError("ttl_seconds out of range")

        nonce = secrets.token_hex(16)
        nonce_digest = digest_text(nonce)
        minted_at = payload.get("minted_at") or _now_iso()
        expires_at = payload.get("expires_at")
        if not expires_at:
            expires_at = (
                datetime.fromisoformat(str(minted_at).replace("Z", "+00:00"))
                + timedelta(seconds=ttl)
            ).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
                "+00:00", "Z"
            )

        identity = {
            "artifact_or_effect_hash": artifact_or_effect_hash,
            "before_fingerprint": before_fingerprint,
            "decision_id": decision_id,
            "nonce_digest": nonce_digest,
            "proposal_hash": proposal_hash,
            "proposal_id": proposal_id,
            "target_ref": target_ref,
        }
        request_fingerprint = digest_text(_canonical_json(identity))

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._mint_authority_tx(
                    tx,
                    authority_id=authority_id,
                    decision_id=decision_id,
                    proposal_id=proposal_id,
                    proposal_hash=proposal_hash,
                    target_ref=target_ref,
                    before_fingerprint=before_fingerprint,
                    artifact_or_effect_hash=artifact_or_effect_hash,
                    approver=approver,
                    scopes_json=_canonical_json(scopes_norm),
                    nonce_digest=nonce_digest,
                    minted_at=str(minted_at),
                    expires_at=str(expires_at),
                    request_fingerprint=request_fingerprint,
                ),
            )

        result = self._with_session(operation)
        # Nonce returned once at mint only (operator holds it; not re-readable).
        if result.get("outcome") == "created":
            result = dict(result)
            result["nonce"] = nonce
        return result

    def _mint_authority_tx(
        self,
        tx: Any,
        *,
        authority_id: str,
        decision_id: str,
        proposal_id: str,
        proposal_hash: str,
        target_ref: str,
        before_fingerprint: str,
        artifact_or_effect_hash: str,
        approver: str,
        scopes_json: str,
        nonce_digest: str,
        minted_at: str,
        expires_at: str,
        request_fingerprint: str,
    ) -> dict[str, Any]:
        existing = _run_one(
            tx,
            """
            MATCH (a:Operational:ActivationAuthority {id: $id})
            RETURN a.id AS id,
                   a.request_fingerprint AS request_fingerprint,
                   a.status AS status,
                   a.consumption_receipt_id AS consumption_receipt_id,
                   a.reconciliation_receipt_id AS reconciliation_receipt_id
            LIMIT 1
            """,
            {"id": authority_id},
        )
        if existing is not None:
            if existing.get("request_fingerprint") == request_fingerprint:
                # Never re-issue a replacement nonce implicitly.
                return {
                    "outcome": "replayed",
                    "authority_id": existing["id"],
                    "status": existing.get("status"),
                    "request_fingerprint": request_fingerprint,
                    "consumption_receipt_id": existing.get("consumption_receipt_id"),
                    "reconciliation_receipt_id": existing.get(
                        "reconciliation_receipt_id"
                    ),
                    "nonce_reissued": False,
                }
            return {
                "outcome": "conflict",
                "reason": "authority_id_reused",
                "authority_id": existing["id"],
            }

        assert_legal_authority_transition(None, "minted")
        created = _run_one(
            tx,
            """
            CREATE (a:Operational:ActivationAuthority)
            SET a.id = $id,
                a.decision_id = $decision_id,
                a.proposal_id = $proposal_id,
                a.proposal_hash = $proposal_hash,
                a.target_ref = $target_ref,
                a.before_fingerprint = $before_fingerprint,
                a.artifact_or_effect_hash = $artifact_or_effect_hash,
                a.approver = $approver,
                a.scopes_json = $scopes_json,
                a.status = 'minted',
                a.nonce_digest = $nonce_digest,
                a.minted_at = $minted_at,
                a.expires_at = $expires_at,
                a.request_fingerprint = $fp
            RETURN a.id AS id, a.status AS status, a.expires_at AS expires_at
            """,
            {
                "id": authority_id,
                "decision_id": decision_id,
                "proposal_id": proposal_id,
                "proposal_hash": proposal_hash,
                "target_ref": target_ref,
                "before_fingerprint": before_fingerprint,
                "artifact_or_effect_hash": artifact_or_effect_hash,
                "approver": approver,
                "scopes_json": scopes_json,
                "nonce_digest": nonce_digest,
                "minted_at": minted_at,
                "expires_at": expires_at,
                "fp": request_fingerprint,
            },
        )
        if created is None:
            raise RuntimeError("ActivationAuthority create returned no row")
        return {
            "outcome": "created",
            "authority_id": created["id"],
            "status": created["status"],
            "expires_at": created.get("expires_at"),
            "request_fingerprint": request_fingerprint,
            "nonce_reissued": False,
        }

    def get_authority_receipt(self, authority_id: str) -> dict[str, Any]:
        """Read authority + linked EffectReceipt for lost-response reconciliation.

        Never mints a replacement authority.
        """
        authority_id = _require_str(authority_id, "authority_id")

        def operation(session: Any) -> dict[str, Any]:
            row = _run_one(
                session,
                """
                MATCH (a:Operational:ActivationAuthority {id: $id})
                OPTIONAL MATCH (r:Operational:EffectReceipt {id: a.consumption_receipt_id})
                RETURN a.id AS authority_id,
                       a.status AS status,
                       a.proposal_id AS proposal_id,
                       a.target_ref AS target_ref,
                       a.before_fingerprint AS before_fingerprint,
                       a.artifact_or_effect_hash AS artifact_or_effect_hash,
                       a.approver AS approver,
                       a.expires_at AS expires_at,
                       a.consumed_at AS consumed_at,
                       a.consumption_receipt_id AS consumption_receipt_id,
                       a.reconciliation_receipt_id AS reconciliation_receipt_id,
                       a.request_fingerprint AS request_fingerprint,
                       r.id AS receipt_id,
                       r.outcome AS receipt_outcome,
                       r.effect_type AS receipt_effect_type,
                       r.request_hash AS receipt_request_hash
                LIMIT 1
                """,
                {"id": authority_id},
            )
            if row is None:
                return {"outcome": "not_found", "authority_id": authority_id}
            return {
                "outcome": "found",
                "authority_id": row["authority_id"],
                "status": row.get("status"),
                "proposal_id": row.get("proposal_id"),
                "target_ref": row.get("target_ref"),
                "before_fingerprint": row.get("before_fingerprint"),
                "artifact_or_effect_hash": row.get("artifact_or_effect_hash"),
                "approver": row.get("approver"),
                "expires_at": row.get("expires_at"),
                "consumed_at": row.get("consumed_at"),
                "consumption_receipt_id": row.get("consumption_receipt_id"),
                "reconciliation_receipt_id": row.get("reconciliation_receipt_id"),
                "request_fingerprint": row.get("request_fingerprint"),
                "effect_receipt": (
                    None
                    if not row.get("receipt_id")
                    else {
                        "id": row.get("receipt_id"),
                        "outcome": row.get("receipt_outcome"),
                        "effect_type": row.get("receipt_effect_type"),
                        "request_hash": row.get("receipt_request_hash"),
                    }
                ),
                "replacement_minted": False,
            }

        return self._with_session(operation)

    def apply_alias(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Atomically validate, consume authority, and apply Alias revision."""
        return self._apply_or_revoke_alias(payload, effect_type="apply_alias")

    def revoke_alias(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Compensating revoke revision + receipt (never hard-delete)."""
        return self._apply_or_revoke_alias(payload, effect_type="revoke_alias")

    def _apply_or_revoke_alias(
        self, payload: dict[str, Any], *, effect_type: str
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")
        if effect_type not in ALIAS_EFFECT_TYPES:
            raise ValueError("invalid effect_type")

        authority_id = _require_str(payload.get("authority_id"), "authority_id")
        nonce = _require_str(payload.get("nonce"), "nonce")
        actor = _require_str(payload.get("actor") or payload.get("approver"), "actor")
        request_hash = payload.get("request_hash")

        # Effect body (may be loaded from proposal in-tx when proposal_id given).
        proposal_id = payload.get("proposal_id")
        if proposal_id is not None:
            proposal_id = _require_str(proposal_id, "proposal_id")

        namespace = payload.get("namespace") or DEFAULT_NAMESPACE
        entity_type = payload.get("entity_type")
        display_from = payload.get("display_from") or payload.get("from_name")
        normalized_from = payload.get("normalized_from")
        canonical_id = payload.get("canonical_id")
        canonical_name = payload.get("canonical_name")
        expected_before = payload.get("before_fingerprint")
        expected_effect_hash = payload.get("artifact_or_effect_hash") or payload.get(
            "proposed_effect_hash"
        )

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._consume_with_alias_effect_tx(
                    tx,
                    effect_type=effect_type,
                    authority_id=authority_id,
                    nonce=nonce,
                    actor=actor,
                    proposal_id=proposal_id,
                    namespace=namespace,
                    entity_type=entity_type,
                    display_from=display_from,
                    normalized_from=normalized_from,
                    canonical_id=canonical_id,
                    canonical_name=canonical_name,
                    expected_before=expected_before,
                    expected_effect_hash=expected_effect_hash,
                    request_hash=request_hash,
                ),
            )

        return self._with_session(operation)

    def _consume_with_alias_effect_tx(
        self,
        tx: Any,
        *,
        effect_type: str,
        authority_id: str,
        nonce: str,
        actor: str,
        proposal_id: str | None,
        namespace: Any,
        entity_type: Any,
        display_from: Any,
        normalized_from: Any,
        canonical_id: Any,
        canonical_name: Any,
        expected_before: Any,
        expected_effect_hash: Any,
        request_hash: Any,
    ) -> dict[str, Any]:
        auth = _run_one(
            tx,
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
                   a.scopes_json AS scopes_json,
                   a.expires_at AS expires_at,
                   a.consumption_receipt_id AS consumption_receipt_id,
                   a.reconciliation_receipt_id AS reconciliation_receipt_id,
                   a.request_fingerprint AS request_fingerprint
            LIMIT 1
            """,
            {"id": authority_id},
        )
        if auth is None:
            return {"outcome": "failed", "reason": "authority_not_found"}

        # Lost-response reconciliation: already consumed → return linked receipt.
        if auth.get("status") == "consumed":
            receipt_id = auth.get("consumption_receipt_id")
            receipt = None
            if receipt_id:
                receipt = _run_one(
                    tx,
                    """
                    MATCH (r:Operational:EffectReceipt {id: $id})
                    RETURN r.id AS id, r.outcome AS outcome,
                           r.effect_type AS effect_type,
                           r.request_hash AS request_hash
                    LIMIT 1
                    """,
                    {"id": receipt_id},
                )
            return {
                "outcome": "replayed",
                "reason": "authority_already_consumed",
                "authority_id": authority_id,
                "effect_receipt": receipt,
                "replacement_minted": False,
            }

        if auth.get("status") in {"expired", "revoked"}:
            return {
                "outcome": "failed",
                "reason": f"authority_{auth.get('status')}",
                "authority_id": authority_id,
            }

        if auth.get("status") != "minted":
            return {
                "outcome": "failed",
                "reason": "authority_not_minted",
                "authority_id": authority_id,
            }

        # Approver binding: actor must match mint-time approver (constant-time).
        expected_approver = str(auth.get("approver") or "")
        if not expected_approver or not secrets.compare_digest(
            expected_approver, str(actor)
        ):
            return {
                "outcome": "failed",
                "reason": "authority_approver_mismatch",
                "authority_id": authority_id,
            }

        # Expiry check
        expires_at = auth.get("expires_at")
        if expires_at:
            try:
                exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > exp:
                    _run_one(
                        tx,
                        """
                        MATCH (a:Operational:ActivationAuthority {id: $id})
                        SET a.status = 'expired'
                        RETURN a.id AS id
                        """,
                        {"id": authority_id},
                    )
                    return {
                        "outcome": "failed",
                        "reason": "authority_expired",
                        "authority_id": authority_id,
                    }
            except ValueError:
                pass

        nonce_digest = digest_text(nonce)
        if not secrets.compare_digest(str(auth.get("nonce_digest") or ""), nonce_digest):
            return {
                "outcome": "failed",
                "reason": "authority_nonce_mismatch",
                "authority_id": authority_id,
            }

        # Load proposal effect when present
        prop_row = None
        if proposal_id or auth.get("proposal_id"):
            pid = proposal_id or auth.get("proposal_id")
            prop_row = _run_one(
                tx,
                """
                MATCH (p:Operational:Proposal {id: $id})
                RETURN p.id AS id,
                       p.kind AS kind,
                       p.target_ref AS target_ref,
                       p.before_fingerprint AS before_fingerprint,
                       p.proposed_effect_hash AS proposed_effect_hash,
                       p.effect_json AS effect_json,
                       p.status_projection AS status_projection
                LIMIT 1
                """,
                {"id": pid},
            )

        effect_body: dict[str, Any] = {}
        if prop_row and prop_row.get("effect_json"):
            try:
                effect_body = json.loads(str(prop_row["effect_json"]))
            except json.JSONDecodeError:
                return {"outcome": "failed", "reason": "proposal_effect_json_invalid"}

        namespace = str(
            namespace or effect_body.get("namespace") or DEFAULT_NAMESPACE
        ).strip()
        entity_type = str(entity_type or effect_body.get("entity_type") or "").strip()
        display_from = str(
            display_from or effect_body.get("display_from") or ""
        ).strip()
        if normalized_from:
            normalized_from = normalize_alias_source(str(normalized_from))
        elif effect_body.get("normalized_from"):
            normalized_from = str(effect_body["normalized_from"])
        elif display_from:
            normalized_from = normalize_alias_source(display_from)
        else:
            return {"outcome": "failed", "reason": "missing_normalized_from"}

        if not entity_type:
            return {"outcome": "failed", "reason": "missing_entity_type"}

        canonical_id = str(
            canonical_id or effect_body.get("canonical_id") or ""
        ).strip()
        canonical_name = str(
            canonical_name or effect_body.get("canonical_name") or canonical_id
        ).strip()

        if effect_type == "apply_alias" and not canonical_id:
            return {"outcome": "failed", "reason": "missing_canonical_id"}

        target_ref = alias_lookup_key(
            namespace=namespace,
            entity_type=entity_type,
            normalized_from=normalized_from,
        )
        if auth.get("target_ref") and auth["target_ref"] != target_ref:
            return {
                "outcome": "conflict",
                "reason": "target_ref_mismatch",
                "authority_target": auth.get("target_ref"),
                "request_target": target_ref,
            }

        # Active alias state for before_fingerprint
        active = _run_one(
            tx,
            """
            MATCH (a:Alias)
            WHERE coalesce(a.status, 'active') = 'active'
              AND coalesce(a.namespace, $namespace) = $namespace
              AND a.entity_type = $entity_type
              AND a.normalized_from = $normalized_from
            RETURN a.id AS id,
                   a.revision AS revision,
                   a.canonical_id AS canonical_id,
                   a.canonical_name AS canonical_name
            ORDER BY coalesce(a.revision, 0) DESC, a.id ASC
            LIMIT 1
            """,
            {
                "namespace": namespace,
                "entity_type": entity_type,
                "normalized_from": normalized_from,
            },
        )
        live_before = compute_before_fingerprint(
            namespace=namespace,
            entity_type=entity_type,
            normalized_from=normalized_from,
            active_alias_id=None if active is None else active.get("id"),
            active_revision=None if active is None else active.get("revision"),
            active_canonical_id=None if active is None else active.get("canonical_id"),
        )
        bound_before = str(
            expected_before
            or (prop_row or {}).get("before_fingerprint")
            or auth.get("before_fingerprint")
            or ""
        )
        if bound_before and bound_before != live_before:
            return {
                "outcome": "stale",
                "reason": "before_fingerprint_mismatch",
                "expected": bound_before,
                "live": live_before,
            }
        if auth.get("before_fingerprint") and auth["before_fingerprint"] != live_before:
            return {
                "outcome": "stale",
                "reason": "authority_before_fingerprint_stale",
                "expected": auth.get("before_fingerprint"),
                "live": live_before,
            }

        next_revision = int((active or {}).get("revision") or 0) + 1
        if effect_type == "apply_alias":
            effect = alias_effect_payload(
                effect_type=effect_type,
                namespace=namespace,
                entity_type=entity_type,
                normalized_from=normalized_from,
                display_from=display_from or normalized_from,
                canonical_id=canonical_id,
                canonical_name=canonical_name,
                revision=next_revision,
            )
        else:
            # Revoke compensates the active mapping
            if active is None:
                return {"outcome": "failed", "reason": "no_active_alias_to_revoke"}
            effect = alias_effect_payload(
                effect_type=effect_type,
                namespace=namespace,
                entity_type=entity_type,
                normalized_from=normalized_from,
                display_from=display_from or normalized_from,
                canonical_id=str(active.get("canonical_id") or ""),
                canonical_name=str(active.get("canonical_name") or ""),
                revision=next_revision,
            )
            canonical_id = effect["canonical_id"]
            canonical_name = effect["canonical_name"]

        effect_hash = compute_alias_effect_hash(effect)
        bound_hash = str(
            expected_effect_hash
            or (prop_row or {}).get("proposed_effect_hash")
            or auth.get("artifact_or_effect_hash")
            or ""
        )
        # For apply, if proposal locked a hash for a specific revision body,
        # recompute with proposal revision if present.
        if bound_hash and bound_hash != effect_hash:
            # Allow proposal hash that used provisional revision from proposal.
            if prop_row and prop_row.get("effect_json"):
                try:
                    proposed = json.loads(str(prop_row["effect_json"]))
                    proposed_hash = compute_alias_effect_hash(proposed)
                    if bound_hash == proposed_hash:
                        # Changed payload vs live next revision → conflict if
                        # identity fields diverge.
                        for key in (
                            "namespace",
                            "entity_type",
                            "normalized_from",
                            "canonical_id",
                        ):
                            if str(proposed.get(key)) != str(effect.get(key)):
                                return {
                                    "outcome": "conflict",
                                    "reason": "changed_payload",
                                    "field": key,
                                }
                        # Align revision to proposed when it matches next or is explicit.
                        if int(proposed.get("revision") or next_revision) != next_revision:
                            # If proposed revision is not the live next, treat as stale.
                            if int(proposed.get("revision") or 0) != next_revision:
                                return {
                                    "outcome": "stale",
                                    "reason": "revision_mismatch",
                                    "expected": proposed.get("revision"),
                                    "live_next": next_revision,
                                }
                        effect = dict(effect)
                        effect["revision"] = int(proposed.get("revision") or next_revision)
                        effect_hash = compute_alias_effect_hash(effect)
                    else:
                        return {
                            "outcome": "conflict",
                            "reason": "effect_hash_mismatch",
                            "expected": bound_hash,
                            "computed": effect_hash,
                        }
                except json.JSONDecodeError:
                    return {
                        "outcome": "conflict",
                        "reason": "effect_hash_mismatch",
                        "expected": bound_hash,
                        "computed": effect_hash,
                    }
            else:
                return {
                    "outcome": "conflict",
                    "reason": "effect_hash_mismatch",
                    "expected": bound_hash,
                    "computed": effect_hash,
                }

        if auth.get("artifact_or_effect_hash") and auth[
            "artifact_or_effect_hash"
        ] != effect_hash:
            # Accept if authority was bound to proposal's proposed hash and we
            # already reconciled above.
            if auth["artifact_or_effect_hash"] != bound_hash:
                return {
                    "outcome": "conflict",
                    "reason": "authority_effect_hash_mismatch",
                    "expected": auth.get("artifact_or_effect_hash"),
                    "computed": effect_hash,
                }

        # Target validation for apply
        if effect_type == "apply_alias":
            target = _run_one(
                tx,
                """
                MATCH (n {id: $id})
                WHERE NOT n:Alias AND NOT n:Operational
                RETURN n.id AS id, labels(n) AS labels,
                       coalesce(n.name, n.type) AS name
                LIMIT 1
                """,
                {"id": canonical_id},
            )
            if target is None:
                # Distinguish missing vs Alias-target
                alias_tgt = _run_one(
                    tx,
                    "MATCH (a:Alias {id: $id}) RETURN a.id AS id LIMIT 1",
                    {"id": canonical_id},
                )
                if alias_tgt is not None:
                    return {
                        "outcome": "failed",
                        "reason": "alias_target_forbidden",
                        "canonical_id": canonical_id,
                    }
                return {
                    "outcome": "failed",
                    "reason": "missing_target",
                    "canonical_id": canonical_id,
                }
            labels = target.get("labels") or []
            if isinstance(labels, str):
                labels = [labels]
            if entity_type not in labels:
                return {
                    "outcome": "failed",
                    "reason": "wrong_type",
                    "expected_type": entity_type,
                    "labels": list(labels),
                }

            # Pinned identity check
            pinned = _run_one(
                tx,
                """
                MATCH (p:Operational:EntityProtection {entity_id: $id})
                WHERE p.protection_level = 'pinned'
                  AND p.revoked_at IS NULL
                RETURN p.entity_id AS entity_id, p.revision AS revision
                LIMIT 1
                """,
                {"id": canonical_id},
            )
            source_pinned = _run_one(
                tx,
                """
                MATCH (n)
                WHERE NOT n:Alias AND NOT n:Operational
                  AND (
                    toLower(coalesce(n.name, '')) = toLower($display)
                    OR any(x IN CASE WHEN n.name IS :: LIST<STRING>
                      THEN n.name ELSE [] END
                      WHERE toLower(x) = toLower($display))
                  )
                OPTIONAL MATCH (p:Operational:EntityProtection {entity_id: n.id})
                WHERE p.protection_level = 'pinned' AND p.revoked_at IS NULL
                RETURN n.id AS id, p.entity_id AS pinned_id
                LIMIT 1
                """,
                {"display": display_from or normalized_from},
            )
            needs_pinned_scope = pinned is not None or (
                source_pinned is not None and source_pinned.get("pinned_id")
            )
            if needs_pinned_scope:
                scopes_raw = auth.get("scopes_json") or "[]"
                try:
                    scopes = json.loads(str(scopes_raw))
                except json.JSONDecodeError:
                    scopes = []
                if PINNED_IDENTITY_SCOPE not in scopes:
                    return {
                        "outcome": "failed",
                        "reason": "pinned_identity_authority_required",
                        "required_scope": PINNED_IDENTITY_SCOPE,
                    }

            # Duplicate active alias (different id, same key) already handled by
            # active single-row lookup; if active exists with different canonical
            # and we apply, we revoke old then create new revision.
            already_active = (
                active is not None and str(active.get("canonical_id")) == canonical_id
            )
        else:
            already_active = False

        # Build request hash for receipt
        req_identity = {
            "authority_id": authority_id,
            "effect": effect,
            "effect_type": effect_type,
        }
        computed_request_hash = compute_request_hash(req_identity)
        if request_hash and str(request_hash) != computed_request_hash:
            return {
                "outcome": "conflict",
                "reason": "request_hash_mismatch",
                "expected": request_hash,
                "computed": computed_request_hash,
            }
        request_hash = computed_request_hash
        effect_id = f"eff-{uuid.uuid4()}"
        effect_key = f"alias:{target_ref}:{effect_type}:r{effect['revision']}"

        # Existing effect by key?
        by_key = _run_one(
            tx,
            """
            MATCH (r:Operational:EffectReceipt {effect_key: $effect_key})
            RETURN r.id AS id, r.request_hash AS request_hash,
                   r.outcome AS outcome
            LIMIT 1
            """,
            {"effect_key": effect_key},
        )
        if by_key is not None:
            if by_key.get("request_hash") == request_hash:
                # Still consume authority if still minted (CAS) so single-use holds.
                cas = self._cas_consume_authority(
                    tx,
                    authority_id=authority_id,
                    receipt_id=str(by_key["id"]),
                )
                if cas.get("outcome") == "stale_consume":
                    return {
                        "outcome": "replayed",
                        "reason": "authority_already_consumed",
                        "authority_id": authority_id,
                        "effect_id": by_key["id"],
                        "replacement_minted": False,
                    }
                return {
                    "outcome": "replayed",
                    "effect_id": by_key["id"],
                    "effect_key": effect_key,
                    "effect_outcome": by_key.get("outcome"),
                    "request_hash": request_hash,
                    "authority_status": "consumed",
                }
            return {
                "outcome": "conflict",
                "reason": "effect_key_reused",
                "effect_id": by_key["id"],
            }

        alias_id = f"alias-{uuid.uuid4()}"
        now = _now_iso()

        if effect_type == "apply_alias" and already_active:
            # No-op mapping already present: still single-use consume authority
            # with a verified receipt so the nonce cannot be reused.
            cas = self._cas_consume_authority(
                tx,
                authority_id=authority_id,
                receipt_id=effect_id,
            )
            if cas.get("outcome") == "stale_consume":
                return {
                    "outcome": "replayed",
                    "reason": "authority_already_consumed",
                    "authority_id": authority_id,
                    "replacement_minted": False,
                }
            _run_one(
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
                    r.outcome = 'noop_already_active',
                    r.verification_status = 'verified',
                    r.authority_digest = $authority_digest,
                    r.applied_at = $now,
                    r.undo_ref = $undo_ref
                RETURN r.id AS id
                """,
                {
                    "id": effect_id,
                    "effect_key": effect_key,
                    "request_hash": request_hash,
                    "proposal_id": proposal_id or auth.get("proposal_id"),
                    "effect_type": effect_type,
                    "actor": actor,
                    "before_ref": f"before:{live_before}",
                    "after_ref": f"alias:{active.get('id')}:already_active",
                    "authority_digest": auth.get("request_fingerprint"),
                    "now": now,
                    "undo_ref": f"revoke_alias:{target_ref}",
                },
            )
            return {
                "outcome": "replayed",
                "reason": "alias_already_active",
                "alias_id": active.get("id"),
                "canonical_id": canonical_id,
                "revision": active.get("revision"),
                "effect_id": effect_id,
                "authority_id": authority_id,
                "authority_status": "consumed",
                "request_hash": request_hash,
            }

        if effect_type == "apply_alias":
            # Revoke prior active (compensating status, not delete)
            if active is not None:
                _run_one(
                    tx,
                    """
                    MATCH (a:Alias {id: $id})
                    SET a.status = 'revoked', a.revoked_at = $now
                    RETURN a.id AS id
                    """,
                    {"id": active["id"], "now": now},
                )
            created_alias = _run_one(
                tx,
                """
                CREATE (a:Operational:Alias)
                SET a.id = $id,
                    a.namespace = $namespace,
                    a.entity_type = $entity_type,
                    a.normalized_from = $normalized_from,
                    a.display_from = $display_from,
                    a.from_name = $display_from,
                    a.to_name = $canonical_name,
                    a.canonical_id = $canonical_id,
                    a.canonical_name = $canonical_name,
                    a.revision = $revision,
                    a.status = 'active',
                    a.proposal_id = $proposal_id,
                    a.effect_receipt_id = $effect_id,
                    a.confirmed_by = $actor,
                    a.created_at = $now
                RETURN a.id AS id, a.revision AS revision
                """,
                {
                    "id": alias_id,
                    "namespace": namespace,
                    "entity_type": entity_type,
                    "normalized_from": normalized_from,
                    "display_from": display_from or normalized_from,
                    "canonical_id": canonical_id,
                    "canonical_name": canonical_name,
                    "revision": effect["revision"],
                    "proposal_id": proposal_id or auth.get("proposal_id"),
                    "effect_id": effect_id,
                    "actor": actor,
                    "now": now,
                },
            )
            after_ref = f"alias:{created_alias['id']}:r{created_alias['revision']}"
        else:
            # revoke_alias: mark active revoked; write compensating revision row
            _run_one(
                tx,
                """
                MATCH (a:Alias {id: $id})
                SET a.status = 'revoked', a.revoked_at = $now
                RETURN a.id AS id
                """,
                {"id": active["id"], "now": now},
            )
            created_alias = _run_one(
                tx,
                """
                CREATE (a:Operational:Alias)
                SET a.id = $id,
                    a.namespace = $namespace,
                    a.entity_type = $entity_type,
                    a.normalized_from = $normalized_from,
                    a.display_from = $display_from,
                    a.from_name = $display_from,
                    a.to_name = $canonical_name,
                    a.canonical_id = $canonical_id,
                    a.canonical_name = $canonical_name,
                    a.revision = $revision,
                    a.status = 'revoked',
                    a.proposal_id = $proposal_id,
                    a.effect_receipt_id = $effect_id,
                    a.confirmed_by = $actor,
                    a.created_at = $now,
                    a.revoked_at = $now,
                    a.compensates_alias_id = $prior_id
                RETURN a.id AS id, a.revision AS revision
                """,
                {
                    "id": alias_id,
                    "namespace": namespace,
                    "entity_type": entity_type,
                    "normalized_from": normalized_from,
                    "display_from": display_from or normalized_from,
                    "canonical_id": canonical_id,
                    "canonical_name": canonical_name,
                    "revision": effect["revision"],
                    "proposal_id": proposal_id or auth.get("proposal_id"),
                    "effect_id": effect_id,
                    "actor": actor,
                    "now": now,
                    "prior_id": active["id"],
                },
            )
            after_ref = f"alias:{created_alias['id']}:revoked:r{created_alias['revision']}"

        # LearningLog
        _run_one(
            tx,
            """
            CREATE (l:Operational:LearningLog)
            SET l.id = $id,
                l.type = $effect_type,
                l.entity = $canonical_id,
                l.alias_id = $alias_id,
                l.timestamp = $now,
                l.actor = $actor
            RETURN l.id AS id
            """,
            {
                "id": f"ll-{uuid.uuid4()}",
                "effect_type": effect_type,
                "canonical_id": canonical_id,
                "alias_id": alias_id,
                "now": now,
                "actor": actor,
            },
        )

        # EffectReceipt
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
                r.outcome = 'applied',
                r.verification_status = 'verified',
                r.authority_digest = $authority_digest,
                r.applied_at = $now,
                r.undo_ref = $undo_ref
            RETURN r.id AS id, r.outcome AS outcome
            """,
            {
                "id": effect_id,
                "effect_key": effect_key,
                "request_hash": request_hash,
                "proposal_id": proposal_id or auth.get("proposal_id"),
                "effect_type": effect_type,
                "actor": actor,
                "before_ref": f"before:{live_before}",
                "after_ref": after_ref,
                "authority_digest": auth.get("request_fingerprint"),
                "now": now,
                "undo_ref": (
                    f"revoke_alias:{target_ref}"
                    if effect_type == "apply_alias"
                    else f"reapply_alias:{target_ref}"
                ),
            },
        )

        # Consume authority with compare-and-set (single-use under concurrency).
        cas = self._cas_consume_authority(
            tx,
            authority_id=authority_id,
            receipt_id=effect_id if receipt is None else str(receipt["id"]),
        )
        if cas.get("outcome") == "stale_consume":
            return {
                "outcome": "replayed",
                "reason": "authority_already_consumed",
                "authority_id": authority_id,
                "replacement_minted": False,
            }

        if prop_row is not None:
            _run_one(
                tx,
                """
                MATCH (p:Operational:Proposal {id: $id})
                SET p.status_projection = 'approved'
                RETURN p.id AS id
                """,
                {"id": prop_row["id"]},
            )

        return {
            "outcome": "applied",
            "effect_id": receipt["id"] if receipt else effect_id,
            "effect_key": effect_key,
            "effect_type": effect_type,
            "alias_id": alias_id,
            "revision": effect["revision"],
            "canonical_id": canonical_id,
            "canonical_name": canonical_name,
            "target_ref": target_ref,
            "request_hash": request_hash,
            "authority_id": authority_id,
            "authority_status": "consumed",
            "before_fingerprint": live_before,
            "effect_hash": effect_hash,
            "undo_ref": (
                f"revoke_alias:{target_ref}"
                if effect_type == "apply_alias"
                else f"reapply_alias:{target_ref}"
            ),
        }

    def _cas_consume_authority(
        self,
        tx: Any,
        *,
        authority_id: str,
        receipt_id: str,
    ) -> dict[str, Any]:
        """Atomically transition minted → consumed; fail closed on races."""
        assert_legal_authority_transition("minted", "consumed")
        now = _now_iso()
        row = _run_one(
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
        if row is None:
            return {"outcome": "stale_consume", "authority_id": authority_id}
        return {
            "outcome": "consumed",
            "authority_id": authority_id,
            "status": row.get("status"),
        }

    # ------------------------------------------------------------------
    # EntityProtection (revisioned; operator-only)
    # ------------------------------------------------------------------

    def set_entity_protection(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._protection_effect(payload, effect_type="set_entity_protection")

    def revoke_entity_protection(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._protection_effect(payload, effect_type="revoke_entity_protection")

    def _protection_effect(
        self, payload: dict[str, Any], *, effect_type: str
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")
        if effect_type not in PROTECTION_EFFECT_TYPES:
            raise ValueError("invalid protection effect_type")

        authority_id = _require_str(payload.get("authority_id"), "authority_id")
        nonce = _require_str(payload.get("nonce"), "nonce")
        entity_id = _require_str(payload.get("entity_id"), "entity_id")
        actor = _require_str(payload.get("actor") or payload.get("approver"), "actor")
        reason_code = _require_str(
            payload.get("reason_code") or "operator_pin", "reason_code"
        )
        protection_level = _require_enum(
            payload.get("protection_level") or "pinned",
            PROTECTION_LEVELS,
            "protection_level",
        )

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._protection_tx(
                    tx,
                    effect_type=effect_type,
                    authority_id=authority_id,
                    nonce=nonce,
                    entity_id=entity_id,
                    actor=actor,
                    reason_code=reason_code,
                    protection_level=protection_level,
                ),
            )

        return self._with_session(operation)

    def _protection_tx(
        self,
        tx: Any,
        *,
        effect_type: str,
        authority_id: str,
        nonce: str,
        entity_id: str,
        actor: str,
        reason_code: str,
        protection_level: str,
    ) -> dict[str, Any]:
        auth = _run_one(
            tx,
            """
            MATCH (a:Operational:ActivationAuthority {id: $id})
            RETURN a.id AS id, a.status AS status,
                   a.nonce_digest AS nonce_digest,
                   a.scopes_json AS scopes_json,
                   a.expires_at AS expires_at,
                   a.consumption_receipt_id AS consumption_receipt_id,
                   a.target_ref AS target_ref,
                   a.artifact_or_effect_hash AS artifact_or_effect_hash,
                   a.before_fingerprint AS before_fingerprint,
                   a.approver AS approver
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
                "consumption_receipt_id": auth.get("consumption_receipt_id"),
                "replacement_minted": False,
            }
        if auth.get("status") in {"expired", "revoked"}:
            return {
                "outcome": "failed",
                "reason": f"authority_{auth.get('status')}",
                "authority_id": authority_id,
            }
        if auth.get("status") != "minted":
            return {
                "outcome": "failed",
                "reason": f"authority_{auth.get('status')}",
            }

        expected_approver = str(auth.get("approver") or "")
        if not expected_approver or not secrets.compare_digest(
            expected_approver, str(actor)
        ):
            return {
                "outcome": "failed",
                "reason": "authority_approver_mismatch",
                "authority_id": authority_id,
            }

        # Expiry check (reject past expires_at; mark expired for reconciliation).
        expires_at = auth.get("expires_at")
        if expires_at:
            try:
                exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > exp:
                    _run_one(
                        tx,
                        """
                        MATCH (a:Operational:ActivationAuthority {id: $id})
                        SET a.status = 'expired'
                        RETURN a.id AS id
                        """,
                        {"id": authority_id},
                    )
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

        if not secrets.compare_digest(
            str(auth.get("nonce_digest") or ""), digest_text(nonce)
        ):
            return {"outcome": "failed", "reason": "authority_nonce_mismatch"}

        # target_ref binding: authority must be minted for this entity only.
        expected_target = entity_protection_target_ref(entity_id)
        auth_target = str(auth.get("target_ref") or "").strip()
        if not auth_target:
            return {
                "outcome": "failed",
                "reason": "authority_target_ref_missing",
                "expected_target": expected_target,
            }
        if auth_target != expected_target:
            return {
                "outcome": "failed",
                "reason": "target_ref_mismatch",
                "authority_target": auth_target,
                "request_target": expected_target,
            }

        # Effect-hash binding (canonical protect:<entity_id>:<set|revoke>).
        expected_effect_hash = protection_effect_binding(
            entity_id=entity_id, effect_type=effect_type
        )
        auth_effect_hash = str(auth.get("artifact_or_effect_hash") or "").strip()
        if not auth_effect_hash:
            return {
                "outcome": "failed",
                "reason": "authority_effect_hash_missing",
                "expected": expected_effect_hash,
            }
        if auth_effect_hash != expected_effect_hash:
            return {
                "outcome": "failed",
                "reason": "effect_hash_mismatch",
                "expected": expected_effect_hash,
                "authority": auth_effect_hash,
            }

        scopes_raw = auth.get("scopes_json") or "[]"
        try:
            scopes = json.loads(str(scopes_raw))
        except json.JSONDecodeError:
            scopes = []
        if PINNED_IDENTITY_SCOPE not in scopes:
            return {
                "outcome": "failed",
                "reason": "pinned_identity_authority_required",
            }

        active = _run_one(
            tx,
            """
            MATCH (p:Operational:EntityProtection {entity_id: $id})
            WHERE p.revoked_at IS NULL
            RETURN p.entity_id AS entity_id, p.revision AS revision,
                   p.protection_level AS protection_level
            ORDER BY coalesce(p.revision, 0) DESC
            LIMIT 1
            """,
            {"id": entity_id},
        )
        next_revision = int((active or {}).get("revision") or 0) + 1
        now = _now_iso()
        effect_id = f"eff-{uuid.uuid4()}"

        if effect_type == "set_entity_protection":
            if active is not None:
                _run_one(
                    tx,
                    """
                    MATCH (p:Operational:EntityProtection {entity_id: $id})
                    WHERE p.revoked_at IS NULL
                    SET p.revoked_at = $now
                    RETURN p.entity_id AS entity_id
                    """,
                    {"id": entity_id, "now": now},
                )
            _run_one(
                tx,
                """
                CREATE (p:Operational:EntityProtection)
                SET p.entity_id = $entity_id,
                    p.protection_level = $level,
                    p.revision = $revision,
                    p.reason_code = $reason_code,
                    p.set_by = $actor,
                    p.created_at = $now,
                    p.effect_receipt_id = $effect_id
                RETURN p.entity_id AS entity_id
                """,
                {
                    "entity_id": entity_id,
                    "level": protection_level,
                    "revision": next_revision,
                    "reason_code": reason_code,
                    "actor": actor,
                    "now": now,
                    "effect_id": effect_id,
                },
            )
        else:
            if active is None:
                return {"outcome": "failed", "reason": "no_active_protection"}
            _run_one(
                tx,
                """
                MATCH (p:Operational:EntityProtection {entity_id: $id})
                WHERE p.revoked_at IS NULL
                SET p.revoked_at = $now, p.revoked_by = $actor
                RETURN p.entity_id AS entity_id
                """,
                {"id": entity_id, "now": now, "actor": actor},
            )
            # Compensating revision record
            _run_one(
                tx,
                """
                CREATE (p:Operational:EntityProtection)
                SET p.entity_id = $entity_id,
                    p.protection_level = $level,
                    p.revision = $revision,
                    p.reason_code = $reason_code,
                    p.set_by = $actor,
                    p.created_at = $now,
                    p.revoked_at = $now,
                    p.effect_receipt_id = $effect_id
                RETURN p.entity_id AS entity_id
                """,
                {
                    "entity_id": entity_id,
                    "level": protection_level,
                    "revision": next_revision,
                    "reason_code": reason_code,
                    "actor": actor,
                    "now": now,
                    "effect_id": effect_id,
                },
            )

        _run_one(
            tx,
            """
            CREATE (r:Operational:EffectReceipt)
            SET r.id = $id,
                r.effect_key = $effect_key,
                r.request_hash = $request_hash,
                r.effect_type = $effect_type,
                r.actor = $actor,
                r.before_ref = $before_ref,
                r.after_ref = $after_ref,
                r.outcome = 'applied',
                r.verification_status = 'verified',
                r.applied_at = $now
            RETURN r.id AS id
            """,
            {
                "id": effect_id,
                "effect_key": f"protect:{entity_id}:{effect_type}:r{next_revision}",
                "request_hash": digest_text(
                    _canonical_json(
                        {
                            "authority_id": authority_id,
                            "effect_type": effect_type,
                            "entity_id": entity_id,
                            "revision": next_revision,
                        }
                    )
                ),
                "effect_type": effect_type,
                "actor": actor,
                "before_ref": f"protect:{entity_id}:r{(active or {}).get('revision')}",
                "after_ref": f"protect:{entity_id}:r{next_revision}",
                "now": now,
            },
        )
        cas = self._cas_consume_authority(
            tx,
            authority_id=authority_id,
            receipt_id=effect_id,
        )
        if cas.get("outcome") == "stale_consume":
            return {
                "outcome": "replayed",
                "reason": "authority_already_consumed",
                "authority_id": authority_id,
                "replacement_minted": False,
            }
        return {
            "outcome": "applied",
            "effect_id": effect_id,
            "effect_type": effect_type,
            "entity_id": entity_id,
            "revision": next_revision,
            "protection_level": protection_level,
            "authority_status": "consumed",
        }


# Re-export for tests / scripts
__all__ = [
    "DEFAULT_NAMESPACE",
    "PINNED_IDENTITY_SCOPE",
    "AliasAuditFinding",
    "AliasEffectStore",
    "alias_effect_payload",
    "alias_lookup_key",
    "audit_alias_rows",
    "claim_false_may_mutate_life_memory",
    "compute_alias_effect_hash",
    "compute_before_fingerprint",
    "compute_protection_before_fingerprint",
    "compute_request_hash",
    "entity_protection_target_ref",
    "is_generic_ack",
    "normalize_alias_source",
    "parse_apply_token",
    "proposal_may_activate_from_prose",
    "protection_effect_binding",
]
