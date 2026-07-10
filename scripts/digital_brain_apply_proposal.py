#!/usr/bin/env python3
"""Operator-only Alias proposal apply / revoke / protect / audit.

Uses quality/admin Neo4j credentials. Never mount this script or its secrets
into maintainer/analyzer toolsets. There is **no unattended ``--yes`` path** —
every mutating action requires an interactive confirmation token.

Examples:

  # Audit legacy Alias nodes (review before new resolution semantics)
  uv run python scripts/digital_brain_apply_proposal.py audit

  # Create a propose-only Alias proposal from FEEDBACK evidence
  uv run python scripts/digital_brain_apply_proposal.py propose \\
      --entity-type Person --from CarPlace --canonical-id id-carid \\
      --canonical-name CarID --feedback-id fb-1

  # Mint authority + apply (two interactive confirms)
  uv run python scripts/digital_brain_apply_proposal.py apply \\
      --proposal-id prop-1 --approver owner@local

  # Compensating unalias (revision/receipt, not delete)
  uv run python scripts/digital_brain_apply_proposal.py revoke \\
      --entity-type Person --from CarPlace --approver owner@local

  # Reconcile a lost apply response without reminting
  uv run python scripts/digital_brain_apply_proposal.py receipt --authority-id aa-1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from digital_brain.maintenance.alias_effects import (  # noqa: E402
    DEFAULT_NAMESPACE,
    PINNED_IDENTITY_SCOPE,
    AliasEffectStore,
    alias_effect_payload,
    alias_lookup_key,
    compute_alias_effect_hash,
    compute_before_fingerprint,
    normalize_alias_source,
    parse_apply_token,
)


def _auth() -> tuple[str, str, str, str]:
    uri = os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL") or "bolt://localhost:7687"
    user = (
        os.getenv("NEO4J_QUALITY_USERNAME")
        or os.getenv("NEO4J_ADMIN_USERNAME")
        or os.getenv("NEO4J_USERNAME")
        or "neo4j"
    )
    password = (
        os.getenv("NEO4J_QUALITY_PASSWORD")
        or os.getenv("NEO4J_ADMIN_PASSWORD")
        or os.getenv("NEO4J_PASSWORD")
    )
    database = os.getenv("NEO4J_DATABASE") or "neo4j"
    if not password:
        raise SystemExit(
            "Neo4j quality/admin password env is required "
            "(NEO4J_QUALITY_PASSWORD or NEO4J_ADMIN_PASSWORD)"
        )
    # Refuse runtime-only credentials for mutation path when explicitly flagged.
    if os.getenv("NEO4J_RUNTIME_PASSWORD") and password == os.getenv(
        "NEO4J_RUNTIME_PASSWORD"
    ):
        if not (
            os.getenv("NEO4J_QUALITY_PASSWORD") or os.getenv("NEO4J_ADMIN_PASSWORD")
        ):
            raise SystemExit(
                "Refusing to apply with runtime-only credentials; "
                "set NEO4J_QUALITY_PASSWORD or NEO4J_ADMIN_PASSWORD"
            )
    return uri, user, password, database


def _store() -> AliasEffectStore:
    from neo4j import GraphDatabase

    uri, user, password, database = _auth()

    def factory():
        return GraphDatabase.driver(uri, auth=(user, password))

    return AliasEffectStore(factory, database)


def _confirm(prompt: str, *, expected: str) -> None:
    """Interactive confirm — no ``--yes`` bypass exists by design."""
    print(prompt)
    print(f"Type exactly: {expected}")
    try:
        got = input("> ").strip()
    except EOFError as exc:
        raise SystemExit("interactive confirmation required (no unattended path)") from exc
    if got != expected:
        raise SystemExit("confirmation mismatch; aborted")


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def cmd_audit(_args: argparse.Namespace) -> int:
    result = _store().audit_aliases()
    _print(result)
    if result.get("review_required"):
        print(
            "\n# Human review required before new resolution semantics activate.",
            file=sys.stderr,
        )
        return 2
    return 0


def cmd_propose(args: argparse.Namespace) -> int:
    if args.feedback_kind == "claim_false":
        _print(
            {
                "outcome": "rejected",
                "reason": "claim_false_propose_only",
                "message": (
                    "claim_false cannot produce an activating Alias proposal"
                ),
            }
        )
        return 1
    store = _store()
    payload = {
        "id": args.proposal_id,
        "namespace": args.namespace,
        "entity_type": args.entity_type,
        "display_from": args.from_name,
        "canonical_id": args.canonical_id,
        "canonical_name": args.canonical_name,
        "feedback_id": args.feedback_id,
        "kind": "alias",
        "feedback_kind": args.feedback_kind,
    }
    result = store.create_alias_proposal(payload)
    _print(result)
    return 0 if result.get("outcome") in {"created", "replayed"} else 1


def cmd_receipt(args: argparse.Namespace) -> int:
    result = _store().get_authority_receipt(args.authority_id)
    _print(result)
    return 0 if result.get("outcome") in {"found", "not_found"} else 1


def cmd_apply(args: argparse.Namespace) -> int:
    store = _store()
    proposal_id = args.proposal_id
    if args.apply_token:
        token_id = parse_apply_token(args.apply_token)
        if not token_id:
            raise SystemExit("invalid APPLY token; expected 'APPLY alias:<proposal_id>'")
        proposal_id = proposal_id or token_id

    if not proposal_id and not (args.entity_type and args.from_name and args.canonical_id):
        raise SystemExit("provide --proposal-id or full effect fields")

    # Resolve effect fields from args (proposal body is authoritative in-store).
    namespace = args.namespace or DEFAULT_NAMESPACE
    display_from = args.from_name
    entity_type = args.entity_type
    canonical_id = args.canonical_id
    canonical_name = args.canonical_name
    normalized = (
        normalize_alias_source(display_from)
        if display_from
        else None
    )

    if proposal_id and not all([entity_type, display_from, canonical_id]):
        # Mint/apply will load effect_json from the proposal inside the store.
        effect_hash = args.effect_hash
        before_fp = args.before_fingerprint
        target_ref = args.target_ref
        if not (effect_hash and before_fp and target_ref):
            raise SystemExit(
                "When applying by proposal-id without inline effect fields, "
                "pass --effect-hash, --before-fingerprint, and --target-ref "
                "from the proposal review card (or pass full effect fields)."
            )
    else:
        assert entity_type and display_from and canonical_id and normalized
        # Live before fingerprint requires graph read via audit of single key —
        # provisional empty-active fingerprint when --assume-empty-before.
        before_fp = args.before_fingerprint or compute_before_fingerprint(
            namespace=namespace,
            entity_type=entity_type,
            normalized_from=normalized,
            active_alias_id=None,
            active_revision=None,
            active_canonical_id=None,
        )
        effect = alias_effect_payload(
            effect_type="apply_alias",
            namespace=namespace,
            entity_type=entity_type,
            normalized_from=normalized,
            display_from=display_from,
            canonical_id=canonical_id,
            canonical_name=canonical_name or canonical_id,
            revision=int(args.revision or 1),
        )
        effect_hash = args.effect_hash or compute_alias_effect_hash(effect)
        target_ref = args.target_ref or alias_lookup_key(
            namespace=namespace,
            entity_type=entity_type,
            normalized_from=normalized,
        )

    scopes = []
    if args.pinned_identity:
        scopes.append(PINNED_IDENTITY_SCOPE)

    print("About to mint single-use ActivationAuthority and apply Alias.")
    print(f"  proposal_id={proposal_id}")
    print(f"  target_ref={target_ref}")
    print(f"  effect_hash={effect_hash}")
    print(f"  before_fingerprint={before_fp}")
    print(f"  approver={args.approver}")
    print(f"  scopes={scopes}")
    _confirm(
        "Confirm mint (this does not apply yet).",
        expected=f"MINT {target_ref}",
    )

    mint = store.mint_activation_authority(
        {
            "proposal_id": proposal_id or f"inline-{target_ref}",
            "proposal_hash": effect_hash,
            "target_ref": target_ref,
            "before_fingerprint": before_fp,
            "artifact_or_effect_hash": effect_hash,
            "approver": args.approver,
            "scopes": scopes,
            "ttl_seconds": args.ttl_seconds,
        }
    )
    _print({"mint": mint})
    if mint.get("outcome") not in {"created", "replayed"}:
        return 1
    if mint.get("outcome") == "replayed" and not mint.get("nonce"):
        print(
            "Authority already exists; use `receipt` to reconcile — "
            "nonce is not re-issued.",
            file=sys.stderr,
        )
        return 2

    nonce = mint.get("nonce")
    authority_id = mint.get("authority_id")
    _confirm(
        "Confirm apply (consumes authority atomically).",
        expected=f"APPLY {authority_id}",
    )

    apply_payload: dict[str, Any] = {
        "authority_id": authority_id,
        "nonce": nonce,
        "actor": args.approver,
        "proposal_id": proposal_id,
        "namespace": namespace,
        "entity_type": entity_type,
        "display_from": display_from,
        "canonical_id": canonical_id,
        "canonical_name": canonical_name,
        "before_fingerprint": before_fp,
        "artifact_or_effect_hash": effect_hash,
    }
    result = store.apply_alias(apply_payload)
    _print(result)
    return 0 if result.get("outcome") in {"applied", "replayed"} else 1


def cmd_revoke(args: argparse.Namespace) -> int:
    store = _store()
    namespace = args.namespace or DEFAULT_NAMESPACE
    display_from = args.from_name
    entity_type = args.entity_type
    if not display_from or not entity_type:
        raise SystemExit("--from and --entity-type required for revoke")
    normalized = normalize_alias_source(display_from)
    target_ref = alias_lookup_key(
        namespace=namespace,
        entity_type=entity_type,
        normalized_from=normalized,
    )
    # Provisional effect hash for revoke (store recomputes from live active).
    effect = alias_effect_payload(
        effect_type="revoke_alias",
        namespace=namespace,
        entity_type=entity_type,
        normalized_from=normalized,
        display_from=display_from,
        canonical_id=args.canonical_id or "pending",
        canonical_name=args.canonical_name or "pending",
        revision=int(args.revision or 1),
    )
    effect_hash = args.effect_hash or compute_alias_effect_hash(effect)
    before_fp = args.before_fingerprint or compute_before_fingerprint(
        namespace=namespace,
        entity_type=entity_type,
        normalized_from=normalized,
        active_alias_id=None,
        active_revision=None,
        active_canonical_id=None,
    )

    print("About to mint authority and revoke Alias (compensating revision).")
    print(f"  target_ref={target_ref}")
    _confirm("Confirm mint for revoke.", expected=f"MINT {target_ref}")

    mint = store.mint_activation_authority(
        {
            "proposal_id": args.proposal_id or f"revoke-{target_ref}",
            "proposal_hash": effect_hash,
            "target_ref": target_ref,
            "before_fingerprint": before_fp,
            "artifact_or_effect_hash": effect_hash,
            "approver": args.approver,
            "scopes": [PINNED_IDENTITY_SCOPE] if args.pinned_identity else [],
            "ttl_seconds": args.ttl_seconds,
        }
    )
    _print({"mint": mint})
    if mint.get("outcome") != "created":
        return 1

    _confirm(
        "Confirm revoke (consumes authority; never hard-deletes).",
        expected=f"REVOKE {mint['authority_id']}",
    )
    result = store.revoke_alias(
        {
            "authority_id": mint["authority_id"],
            "nonce": mint["nonce"],
            "actor": args.approver,
            "proposal_id": args.proposal_id,
            "namespace": namespace,
            "entity_type": entity_type,
            "display_from": display_from,
            "before_fingerprint": before_fp,
            "artifact_or_effect_hash": effect_hash,
        }
    )
    _print(result)
    return 0 if result.get("outcome") in {"applied", "replayed"} else 1


def cmd_protect(args: argparse.Namespace) -> int:
    store = _store()
    target_ref = f"entity:{args.entity_id}"
    effect_hash = args.effect_hash or f"protect:{args.entity_id}:{args.action}"
    before_fp = args.before_fingerprint or f"protect-before:{args.entity_id}"

    print(f"About to {args.action} EntityProtection on {args.entity_id}")
    _confirm("Confirm mint for protection.", expected=f"MINT {target_ref}")
    mint = store.mint_activation_authority(
        {
            "proposal_id": args.proposal_id or f"protect-{args.entity_id}",
            "proposal_hash": effect_hash,
            "target_ref": target_ref,
            "before_fingerprint": before_fp,
            "artifact_or_effect_hash": effect_hash,
            "approver": args.approver,
            "scopes": [PINNED_IDENTITY_SCOPE],
            "ttl_seconds": args.ttl_seconds,
        }
    )
    _print({"mint": mint})
    if mint.get("outcome") != "created":
        return 1
    _confirm(
        "Confirm protection effect.",
        expected=f"PROTECT {mint['authority_id']}",
    )
    payload = {
        "authority_id": mint["authority_id"],
        "nonce": mint["nonce"],
        "entity_id": args.entity_id,
        "actor": args.approver,
        "reason_code": args.reason_code,
    }
    if args.action == "set":
        result = store.set_entity_protection(payload)
    else:
        result = store.revoke_entity_protection(payload)
    _print(result)
    return 0 if result.get("outcome") in {"applied", "replayed"} else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Intentionally no --yes / --force / --non-interactive flags.
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit", help="Audit unscoped/conflicting/cyclic Alias nodes")
    p_audit.set_defaults(func=cmd_audit)

    p_prop = sub.add_parser("propose", help="Create propose-only Alias proposal")
    p_prop.add_argument("--proposal-id", default=None)
    p_prop.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    p_prop.add_argument("--entity-type", required=True)
    p_prop.add_argument("--from", dest="from_name", required=True)
    p_prop.add_argument("--canonical-id", required=True)
    p_prop.add_argument("--canonical-name", required=True)
    p_prop.add_argument("--feedback-id", default=None)
    p_prop.add_argument("--feedback-kind", default=None)
    p_prop.set_defaults(func=cmd_propose)

    p_receipt = sub.add_parser(
        "receipt", help="Read authority + EffectReceipt (no remint)"
    )
    p_receipt.add_argument("--authority-id", required=True)
    p_receipt.set_defaults(func=cmd_receipt)

    p_apply = sub.add_parser("apply", help="Mint authority and apply Alias (interactive)")
    p_apply.add_argument("--proposal-id", default=None)
    p_apply.add_argument("--apply-token", default=None, help="APPLY alias:<proposal_id>")
    p_apply.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    p_apply.add_argument("--entity-type", default=None)
    p_apply.add_argument("--from", dest="from_name", default=None)
    p_apply.add_argument("--canonical-id", default=None)
    p_apply.add_argument("--canonical-name", default=None)
    p_apply.add_argument("--revision", type=int, default=None)
    p_apply.add_argument("--effect-hash", default=None)
    p_apply.add_argument("--before-fingerprint", default=None)
    p_apply.add_argument("--target-ref", default=None)
    p_apply.add_argument("--approver", required=True)
    p_apply.add_argument("--pinned-identity", action="store_true")
    p_apply.add_argument("--ttl-seconds", type=int, default=900)
    p_apply.set_defaults(func=cmd_apply)

    p_rev = sub.add_parser("revoke", help="Compensating unalias (not delete)")
    p_rev.add_argument("--proposal-id", default=None)
    p_rev.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    p_rev.add_argument("--entity-type", required=True)
    p_rev.add_argument("--from", dest="from_name", required=True)
    p_rev.add_argument("--canonical-id", default=None)
    p_rev.add_argument("--canonical-name", default=None)
    p_rev.add_argument("--revision", type=int, default=None)
    p_rev.add_argument("--effect-hash", default=None)
    p_rev.add_argument("--before-fingerprint", default=None)
    p_rev.add_argument("--approver", required=True)
    p_rev.add_argument("--pinned-identity", action="store_true")
    p_rev.add_argument("--ttl-seconds", type=int, default=900)
    p_rev.set_defaults(func=cmd_revoke)

    p_prot = sub.add_parser("protect", help="Set/revoke EntityProtection")
    p_prot.add_argument("--action", choices=("set", "revoke"), required=True)
    p_prot.add_argument("--entity-id", required=True)
    p_prot.add_argument("--approver", required=True)
    p_prot.add_argument("--reason-code", default="operator_pin")
    p_prot.add_argument("--proposal-id", default=None)
    p_prot.add_argument("--effect-hash", default=None)
    p_prot.add_argument("--before-fingerprint", default=None)
    p_prot.add_argument("--ttl-seconds", type=int, default=900)
    p_prot.set_defaults(func=cmd_protect)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Guard: reject any attempt to smuggle unattended yes flags.
    raw = list(argv) if argv is not None else sys.argv[1:]
    for forbidden in ("--yes", "-y", "--force", "--non-interactive", "--assume-yes"):
        if forbidden in raw:
            print(
                f"Refusing {forbidden}: no unattended apply path exists.",
                file=sys.stderr,
            )
            return 2
    parser = build_parser()
    args = parser.parse_args(raw)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
