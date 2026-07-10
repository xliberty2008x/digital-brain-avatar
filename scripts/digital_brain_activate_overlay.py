#!/usr/bin/env python3
"""Operator-only reviewed overlay trial activation / rollback / expire / reconcile.

Uses quality/admin Neo4j credentials. Never mount this script or its secrets
into maintainer/analyzer toolsets. There is **no unattended ``--yes`` path** —
every mutating action requires an interactive confirmation token.

Permanent deployment is intentionally not available here: it requires reviewed
Git content, a plugin version bump, host reload, and generation load proof
(Task 12 / release path).

Examples:

  # Activate a quarantine-compiled artifact as a bounded trial
  uv run python scripts/digital_brain_activate_overlay.py activate \\
      --proposal-id prop-1 --artifact /path/to/artifact.md \\
      --target-ref slot:fail_soft_language --extension-slot fail_soft_language \\
      --rule-id route-empty-guidance --base-commit abc123 \\
      --approver owner@local --decision-point route:READ:empty_or_fail

  # Roll back to the prior known-good manifest (artifact-specific)
  uv run python scripts/digital_brain_activate_overlay.py rollback \\
      --proposal-id prop-1 --prior-manifest-digest <hex> --approver owner@local

  # Expire due trials
  uv run python scripts/digital_brain_activate_overlay.py expire --approver owner@local

  # Reconcile lost response after crash
  uv run python scripts/digital_brain_activate_overlay.py reconcile --request-hash <hex>
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

from digital_brain.maintenance.activation import (  # noqa: E402
    OverlayActivationBinding,
    OverlayEffectStore,
    PermanentDeployError,
    TrialPolicy,
    activate_overlay_trial,
    assert_permanent_deploy_requirements,
    compute_overlay_before_fingerprint,
    expire_active_trials,
    rollback_overlay_trial,
)
from digital_brain.maintenance.active_overlays import (  # noqa: E402
    load_validated_active_overlays,
    manifest_to_public_dict,
    prior_digest_for,
    resolve_loadable_overlays,
)
from digital_brain.maintenance.alias_effects import AliasEffectStore  # noqa: E402
from digital_brain.maintenance.artifacts import (  # noqa: E402
    ArtifactError,
    IsolatedValidationError,
    validate_quarantine_isolated,
)
from digital_brain.maintenance.models import EMPTY_DIGEST, digest_text  # noqa: E402
from digital_brain.maintenance.reconcile import (  # noqa: E402
    reconcile_overlay_activation,
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
    if os.getenv("NEO4J_RUNTIME_PASSWORD") and password == os.getenv(
        "NEO4J_RUNTIME_PASSWORD"
    ):
        if not (
            os.getenv("NEO4J_QUALITY_PASSWORD") or os.getenv("NEO4J_ADMIN_PASSWORD")
        ):
            raise SystemExit(
                "Refusing to activate with runtime-only credentials; "
                "set NEO4J_QUALITY_PASSWORD or NEO4J_ADMIN_PASSWORD"
            )
    return uri, user, password, database


def _driver_factory():
    from neo4j import GraphDatabase

    uri, user, password, _database = _auth()

    def factory():
        return GraphDatabase.driver(uri, auth=(user, password))

    return factory, _auth()[3]


def _stores() -> tuple[AliasEffectStore, OverlayEffectStore]:
    factory, database = _driver_factory()
    return AliasEffectStore(factory, database), OverlayEffectStore(factory, database)


def _confirm(prompt: str, *, expected: str) -> None:
    """Interactive confirm — no ``--yes`` bypass exists by design."""
    print(prompt)
    print(f"Type exactly: {expected}")
    try:
        got = input("> ").strip()
    except EOFError as exc:
        raise SystemExit(
            "interactive confirmation required (no unattended path)"
        ) from exc
    if got != expected:
        raise SystemExit("confirmation mismatch; aborted")


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _state_dir(args: argparse.Namespace) -> Path:
    if args.state_dir:
        return Path(args.state_dir).expanduser().resolve()
    env = (os.getenv("DIGITAL_BRAIN_STATE_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    raise SystemExit("DIGITAL_BRAIN_STATE_DIR or --state-dir is required")


def _parse_before_hashes(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SystemExit("--before-hashes must be a JSON object")
    return {str(k): str(v) for k, v in data.items()}


def cmd_status(args: argparse.Namespace) -> int:
    state = _state_dir(args)
    man = load_validated_active_overlays(state_dir=state)
    bodies = resolve_loadable_overlays(state_dir=state, manifest=man)
    _print(
        {
            "fail_closed": man.fail_closed,
            "fail_reason": man.fail_reason,
            "manifest": manifest_to_public_dict(man),
            "loadable_count": len(bodies),
            "loadable_digests": [b["digest"] for b in bodies],
        }
    )
    return 0 if not man.fail_closed else 2


def cmd_activate(args: argparse.Namespace) -> int:
    state = _state_dir(args)
    alias_store, effect_store = _stores()
    artifact_path = Path(args.artifact).expanduser().resolve()
    if not artifact_path.is_file():
        raise SystemExit(f"artifact not found: {artifact_path}")
    quarantine_root = (state / "dreams" / "quarantine").resolve()
    try:
        artifact_path.relative_to(quarantine_root)
    except ValueError as exc:
        raise SystemExit(
            f"activation requires reviewed quarantine artifact under {quarantine_root}"
        ) from exc
    if artifact_path.name != "artifact.md":
        raise SystemExit("activation artifact must be the bundle artifact.md")
    try:
        validate_quarantine_isolated(
            artifact_path.parent,
            state_dir=state,
            repo_root=ROOT,
        )
        manifest = json.loads(
            (artifact_path.parent / "manifest.json").read_text(encoding="utf-8")
        )
    except (ArtifactError, IsolatedValidationError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"quarantine validation failed: {exc}") from exc

    expected_bindings = {
        "proposal_id": args.proposal_id,
        "base_commit": args.base_commit,
        "extension_slot": args.extension_slot,
        "rule_id": args.rule_id,
    }
    if args.target_skill:
        expected_bindings["target_skill"] = args.target_skill
    if args.target_file:
        expected_bindings["target_file"] = args.target_file
    for field, expected in expected_bindings.items():
        actual = str(manifest.get(field) or "")
        if actual != str(expected):
            raise SystemExit(
                f"quarantine manifest {field} mismatch: expected {expected}, got {actual}"
            )
    content = artifact_path.read_text(encoding="utf-8")
    digest = digest_text(content)
    manifest_before_hashes = {
        str(k): str(v) for k, v in dict(manifest.get("before_hashes") or {}).items()
    }
    before_hashes = (
        _parse_before_hashes(args.before_hashes)
        if args.before_hashes
        else manifest_before_hashes
    )
    if before_hashes != manifest_before_hashes:
        raise SystemExit("--before-hashes does not match quarantine manifest")
    binding = OverlayActivationBinding(
        proposal_id=args.proposal_id,
        proposal_hash=args.proposal_hash or digest,
        artifact_hash=args.artifact_hash or digest,
        target_ref=args.target_ref,
        base_commit=args.base_commit,
        before_hashes=before_hashes,
        rule_id=args.rule_id,
        extension_slot=args.extension_slot,
        target_skill=args.target_skill
        or str(manifest.get("target_skill") or "digital-brain-buddy-session"),
        target_file=args.target_file
        or str(
            manifest.get("target_file")
            or "skills/digital-brain-buddy-session/SKILL.md"
        ),
    )
    if binding.artifact_hash != digest:
        raise SystemExit(
            f"artifact_hash mismatch: expected {binding.artifact_hash}, got {digest}"
        )

    policy = TrialPolicy(
        decision_point=args.decision_point,
        duration_seconds=int(args.duration_seconds),
        exposure_cap=int(args.exposure_cap),
        target_recurrence=int(args.target_recurrence),
        counterevidence_threshold=int(args.counterevidence_threshold),
        guardrail_rollback_thresholds=json.loads(args.guardrail_thresholds),
    )

    prior = load_validated_active_overlays(state_dir=state)
    prior_digest = prior_digest_for(prior)
    before_fp = compute_overlay_before_fingerprint(
        target_ref=binding.target_ref,
        base_commit=binding.base_commit,
        before_hashes=binding.before_hashes,
        prior_manifest_digest=prior_digest,
    )

    print("About to mint single-use ActivationAuthority and activate overlay trial.")
    print(f"  proposal_id={binding.proposal_id}")
    print(f"  target_ref={binding.target_ref}")
    print(f"  artifact_hash={binding.artifact_hash}")
    print(f"  base_commit={binding.base_commit}")
    print(f"  before_fingerprint={before_fp}")
    print(f"  decision_point={policy.decision_point}")
    print(f"  duration_seconds={policy.duration_seconds}")
    print(f"  exposure_cap={policy.exposure_cap}")
    print(f"  state_dir={state}")
    _confirm(
        "Confirm mint (this does not activate yet).",
        expected=f"MINT {binding.target_ref}",
    )

    mint = alias_store.mint_activation_authority(
        {
            "proposal_id": binding.proposal_id,
            "proposal_hash": binding.proposal_hash,
            "target_ref": binding.target_ref,
            "before_fingerprint": before_fp,
            "artifact_or_effect_hash": binding.artifact_hash,
            "approver": args.approver,
            "scopes": ["overlay_trial"],
            "ttl_seconds": args.ttl_seconds,
        }
    )
    _print({"mint": mint})
    if mint.get("outcome") not in {"created", "replayed"}:
        return 1
    if mint.get("outcome") == "replayed" and not mint.get("nonce"):
        print(
            "Authority already exists; use `reconcile` — nonce is not re-issued.",
            file=sys.stderr,
        )
        return 2

    _confirm(
        "Confirm activate (consumes authority; stages active-overlays + manifest).",
        expected=f"ACTIVATE {mint['authority_id']}",
    )

    result = activate_overlay_trial(
        state_dir=state,
        binding=binding,
        artifact_md=content,
        trial_policy=policy,
        authority_id=mint["authority_id"],
        nonce=mint["nonce"],
        actor=args.approver,
        rollback_generation=args.rollback_generation or EMPTY_DIGEST,
        alias_store=alias_store,
        effect_store=effect_store,
    )
    _print(result)
    return 0 if result.get("outcome") in {"applied", "replayed"} else 1


def cmd_rollback(args: argparse.Namespace) -> int:
    state = _state_dir(args)
    _alias_store, effect_store = _stores()
    prior_path = args.prior_manifest_json
    prior_manifest = None
    if prior_path:
        prior_manifest = json.loads(
            Path(prior_path).expanduser().read_text(encoding="utf-8")
        )
    print("About to roll back overlay trial (compensating effect).")
    print(f"  proposal_id={args.proposal_id}")
    print(f"  prior_manifest_digest={args.prior_manifest_digest}")
    print(f"  deployment_id={args.deployment_id}")
    print(f"  reason={args.reason}")
    _confirm(
        "Confirm rollback (restores exact prior manifest).",
        expected=f"ROLLBACK {args.proposal_id}",
    )
    result = rollback_overlay_trial(
        state_dir=state,
        proposal_id=args.proposal_id,
        prior_manifest_digest=args.prior_manifest_digest,
        actor=args.approver,
        effect_store=effect_store,
        deployment_id=args.deployment_id,
        reason=args.reason,
        prior_manifest=prior_manifest,
    )
    _print(result)
    return 0 if result.get("outcome") in {"applied", "replayed"} else 1


def cmd_expire(args: argparse.Namespace) -> int:
    state = _state_dir(args)
    _alias_store, effect_store = _stores()
    print("About to expire due overlay trials (never promotes).")
    _confirm("Confirm expire.", expected="EXPIRE overlays")
    result = expire_active_trials(
        state_dir=state,
        effect_store=effect_store,
        actor=args.approver,
    )
    _print(result)
    return 0 if result.get("outcome") in {"applied", "replayed"} else 1


def cmd_reconcile(args: argparse.Namespace) -> int:
    state = _state_dir(args)
    _alias_store, effect_store = _stores()
    result = reconcile_overlay_activation(
        state_dir=state,
        effect_store=effect_store,
        request_hash=args.request_hash,
        actor=args.approver or "reconcile",
    )
    _print(result)
    return 0 if result.get("outcome") in {
        "applied",
        "replayed",
        "idle",
        "restored",
    } else 1


def cmd_permanent_gate(args: argparse.Namespace) -> int:
    """Show / enforce permanent deploy gates (never silent promote from trial)."""
    try:
        assert_permanent_deploy_requirements(
            reviewed_git_content=bool(args.reviewed_git),
            plugin_version_bumped=bool(args.plugin_version_bumped),
            host_reloaded=bool(args.host_reloaded),
            generation_loaded_proof=args.generation_loaded_proof,
        )
    except PermanentDeployError as exc:
        _print({"outcome": "rejected", "reason": str(exc)})
        return 1
    _print(
        {
            "outcome": "gates_satisfied",
            "message": (
                "Gates ok — permanent deploy still requires the reviewed Git "
                "release path (plugin version bump + host reload proof); "
                "this script does not write permanent plugin content."
            ),
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Intentionally no --yes / --force / --non-interactive flags.
    parser.add_argument("--state-dir", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Show validated active overlay manifest")
    p_status.set_defaults(func=cmd_status)

    p_act = sub.add_parser("activate", help="Mint authority + activate trial (interactive)")
    p_act.add_argument("--proposal-id", required=True)
    p_act.add_argument("--artifact", required=True, help="Path to reviewed artifact.md")
    p_act.add_argument("--artifact-hash", default=None)
    p_act.add_argument("--proposal-hash", default=None)
    p_act.add_argument("--target-ref", required=True)
    p_act.add_argument("--extension-slot", required=True)
    p_act.add_argument("--rule-id", required=True)
    p_act.add_argument("--base-commit", required=True)
    p_act.add_argument("--before-hashes", default=None, help="JSON object of path→hash")
    p_act.add_argument("--target-skill", default=None)
    p_act.add_argument("--target-file", default=None)
    p_act.add_argument("--approver", required=True)
    p_act.add_argument("--decision-point", required=True)
    p_act.add_argument("--duration-seconds", type=int, default=7 * 24 * 3600)
    p_act.add_argument("--exposure-cap", type=int, default=50)
    p_act.add_argument("--target-recurrence", type=int, default=3)
    p_act.add_argument("--counterevidence-threshold", type=int, default=2)
    p_act.add_argument(
        "--guardrail-thresholds",
        default='{"privacy_gate_failure_count": 1, "guardrail_regression_rate": 0.1}',
    )
    p_act.add_argument("--rollback-generation", default=None)
    p_act.add_argument("--ttl-seconds", type=int, default=900)
    p_act.set_defaults(func=cmd_activate)

    p_rb = sub.add_parser("rollback", help="Compensating restore of prior manifest")
    p_rb.add_argument("--proposal-id", required=True)
    p_rb.add_argument("--prior-manifest-digest", required=True)
    p_rb.add_argument("--prior-manifest-json", default=None)
    p_rb.add_argument("--deployment-id", default=None)
    p_rb.add_argument("--reason", default="operator_rollback")
    p_rb.add_argument("--approver", required=True)
    p_rb.set_defaults(func=cmd_rollback)

    p_ex = sub.add_parser("expire", help="Expire due trials (never promote)")
    p_ex.add_argument("--approver", required=True)
    p_ex.set_defaults(func=cmd_expire)

    p_rec = sub.add_parser("reconcile", help="Reconcile FS/graph after crash")
    p_rec.add_argument("--request-hash", default=None)
    p_rec.add_argument("--approver", default="reconcile")
    p_rec.set_defaults(func=cmd_reconcile)

    p_perm = sub.add_parser(
        "permanent-gate",
        help="Check permanent deploy gates (does not deploy)",
    )
    p_perm.add_argument("--reviewed-git", action="store_true")
    p_perm.add_argument("--plugin-version-bumped", action="store_true")
    p_perm.add_argument("--host-reloaded", action="store_true")
    p_perm.add_argument("--generation-loaded-proof", default=None)
    p_perm.set_defaults(func=cmd_permanent_gate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Refuse --yes if ever smuggled via unknown future flags.
    if any(a in {"--yes", "-y", "--force", "--non-interactive"} for a in (argv or sys.argv[1:])):
        raise SystemExit("unattended flags are not supported")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
