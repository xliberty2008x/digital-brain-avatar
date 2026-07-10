"""Overlay trial activation: rejection gates, fail-closed load, pin, rollback.

TDD: rejection cases (wrong/expired/replayed nonce, changed hash/target/base,
stale proposal) are asserted before successful activation paths.
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

from digital_brain.maintenance.activation import (  # noqa: E402
    OVERLAY_EFFECT_TYPES,
    OverlayActivationBinding,
    OverlayEffectStore,
    PermanentDeployError,
    TrialPolicy,
    TrialPolicyError,
    activate_overlay_trial,
    assert_permanent_deploy_requirements,
    build_activation_request_hash,
    compute_overlay_before_fingerprint,
    expire_active_trials,
    rollback_overlay_trial,
    validate_authority_for_activation,
)
from digital_brain.maintenance.active_overlays import (  # noqa: E402
    ActiveManifest,
    ActiveOverlayEntry,
    ActiveOverlayError,
    ManifestMismatchError,
    atomic_replace_manifest,
    compute_manifest_digest,
    empty_active_manifest,
    load_session_active_overlays,
    load_validated_active_overlays,
    manifest_to_public_dict,
    pin_session_active_overlays,
    resolve_loadable_overlays,
    stage_overlay_content,
)
from digital_brain.maintenance.alias_effects import (  # noqa: E402
    AliasEffectStore,
)
from digital_brain.maintenance.models import (  # noqa: E402
    EMPTY_DIGEST,
    digest_text,
)


# ---------------------------------------------------------------------------
# Fake Neo4j for authority + effect receipts
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | dict[str, Any] | None):
        if rows is None:
            self._rows: list[dict[str, Any]] = []
        elif isinstance(rows, dict):
            self._rows = [rows]
        else:
            self._rows = list(rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def data(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def consume(self) -> None:
        return None


class _FakeSession:
    def __init__(self) -> None:
        self.authorities: dict[str, dict[str, Any]] = {}
        self.effects: dict[str, dict[str, Any]] = {}
        self.effects_by_key: dict[str, str] = {}
        self.effects_by_request: dict[str, str] = {}
        self.deployments: dict[str, dict[str, Any]] = {}
        self.exposure_windows: dict[str, dict[str, Any]] = {}
        self.proposals: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []

    def execute_write(self, fn):  # noqa: ANN001
        return fn(self)

    def write_transaction(self, fn):  # noqa: ANN001
        return fn(self)

    def run(self, query: str, params: dict[str, Any] | None = None) -> _Result:
        params = params or {}
        self.calls.append(query)
        q = " ".join(query.split())

        if "SET a.status = 'expired'" in q:
            a = self.authorities.get(params["id"])
            if a:
                a["status"] = "expired"
            return _Result({"id": params["id"]})

        if "SET a.status = 'consumed'" in q:
            a = self.authorities.get(params["id"])
            if a is None or a.get("status") != "minted":
                return _Result(None)
            a["status"] = "consumed"
            a["consumed_at"] = params.get("now")
            a["consumption_receipt_id"] = params.get("receipt_id")
            a["reconciliation_receipt_id"] = params.get("receipt_id")
            return _Result({"id": params["id"], "status": "consumed"})

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

        if "MATCH (a:Operational:ActivationAuthority {id: $id})" in q:
            a = self.authorities.get(params["id"])
            if a is None:
                return _Result(None)
            if "OPTIONAL MATCH (r:Operational:EffectReceipt" in q:
                receipt = self.effects.get(a.get("consumption_receipt_id") or "")
                return _Result(
                    {
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
                )
            return _Result(dict(a))

        if "MATCH (r:Operational:EffectReceipt {request_hash: $request_hash})" in q:
            rid = self.effects_by_request.get(params["request_hash"])
            if not rid:
                return _Result(None)
            r = self.effects[rid]
            return _Result(dict(r))

        if "MATCH (r:Operational:EffectReceipt {effect_key: $effect_key})" in q:
            rid = self.effects_by_key.get(params["effect_key"])
            if not rid:
                return _Result(None)
            r = self.effects[rid]
            return _Result(
                {
                    "id": r["id"],
                    "outcome": r.get("outcome"),
                    "request_hash": r.get("request_hash"),
                    "effect_type": r.get("effect_type"),
                }
            )

        if "MATCH (r:Operational:EffectReceipt {id: $id})" in q:
            r = self.effects.get(params["id"])
            if r is None:
                return _Result(None)
            return _Result(
                {
                    "id": r["id"],
                    "outcome": r.get("outcome"),
                    "effect_type": r.get("effect_type"),
                    "request_hash": r.get("request_hash"),
                }
            )

        if "CREATE (r:Operational:EffectReceipt)" in q:
            props = {
                "id": params["id"],
                "effect_key": params["effect_key"],
                "request_hash": params["request_hash"],
                "proposal_id": params["proposal_id"],
                "effect_type": params["effect_type"],
                "actor": params["actor"],
                "before_ref": params["before_ref"],
                "after_ref": params.get("after_ref"),
                "outcome": params["outcome"],
                "verification_status": params["verification_status"],
                "authority_digest": params.get("authority_digest"),
                "undo_ref": params.get("undo_ref"),
                "applied_at": params.get("applied_at"),
            }
            self.effects[props["id"]] = props
            self.effects_by_key[props["effect_key"]] = props["id"]
            self.effects_by_request[props["request_hash"]] = props["id"]
            return _Result({"id": props["id"], "outcome": props["outcome"]})

        if "CREATE (d:Operational:Deployment)" in q:
            props = {
                "id": params["id"],
                "proposal_id": params["proposal_id"],
                "generation_id": params["generation_id"],
                "status": params["status"],
                "activated_at": params.get("activated_at"),
                "retired_at": params.get("retired_at"),
                "rollback_ref": params.get("rollback_ref"),
            }
            self.deployments[props["id"]] = props
            return _Result({"id": props["id"], "status": props["status"]})

        if "MATCH (d:Operational:Deployment {id: $id})" in q and "SET" in q:
            d = self.deployments.get(params["id"])
            if d is None:
                return _Result(None)
            if params.get("status") is not None:
                d["status"] = params["status"]
            if params.get("retired_at") is not None:
                d["retired_at"] = params["retired_at"]
            if params.get("rollback_ref") is not None:
                d["rollback_ref"] = params["rollback_ref"]
            return _Result({"id": d["id"], "status": d.get("status")})

        if "CREATE (w:Operational:ExposureWindow)" in q:
            props = {
                "id": params["id"],
                "deployment_id": params["deployment_id"],
                "decision_point": params["decision_point"],
                "eligible_target": params["eligible_target"],
                "eligible_seen": params["eligible_seen"],
                "started_at": params["started_at"],
                "ends_at": params["ends_at"],
                "recurrence_count": params.get("recurrence_count", 0),
                "counterevidence_count": params.get("counterevidence_count", 0),
                "guardrail_json": params.get("guardrail_json", "{}"),
                "effectiveness_status": params.get("effectiveness_status", "observing"),
            }
            self.exposure_windows[props["id"]] = props
            return _Result({"id": props["id"]})

        return _Result(None)


def _store(session: _FakeSession) -> tuple[AliasEffectStore, OverlayEffectStore]:
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

    return AliasEffectStore(factory, "neo4j"), OverlayEffectStore(factory, "neo4j")


def _artifact() -> str:
    return (
        "<!-- OVERLAY_SLOT:fail_soft_language BEGIN -->\n"
        "### Rule `route-empty-guidance`\n"
        "Prefer fail-soft language when READ returns empty.\n"
        "<!-- OVERLAY_SLOT:fail_soft_language END -->\n"
    )


def _binding(**overrides: Any) -> OverlayActivationBinding:
    artifact = _artifact()
    digest = digest_text(artifact)
    base = dict(
        proposal_id="prop-ov-trial-1",
        proposal_hash="ph" * 32,
        artifact_hash=digest,
        target_ref="slot:fail_soft_language",
        base_commit="cafebabe",
        before_hashes={"skills/digital-brain-buddy-session/SKILL.md": "bb" * 32},
        rule_id="route-empty-guidance",
        extension_slot="fail_soft_language",
        target_skill="digital-brain-buddy-session",
        target_file="skills/digital-brain-buddy-session/SKILL.md",
    )
    base.update(overrides)
    return OverlayActivationBinding(**base)


def _trial_policy(**overrides: Any) -> TrialPolicy:
    base = dict(
        decision_point="route:READ:empty_or_fail",
        duration_seconds=7 * 24 * 3600,
        exposure_cap=50,
        target_recurrence=3,
        counterevidence_threshold=2,
        guardrail_rollback_thresholds={"privacy_gate_failure_count": 1, "guardrail_regression_rate": 0.1},
    )
    base.update(overrides)
    return TrialPolicy(**base)


def _mint(
    alias_store: AliasEffectStore,
    binding: OverlayActivationBinding,
    *,
    authority_id: str = "aa-ov-1",
    approver: str = "owner@test",
    expires_at: str | None = None,
    before_fp: str | None = None,
) -> dict[str, Any]:
    fp = before_fp or compute_overlay_before_fingerprint(
        target_ref=binding.target_ref,
        base_commit=binding.base_commit,
        before_hashes=binding.before_hashes,
        prior_manifest_digest=EMPTY_DIGEST,
    )
    payload: dict[str, Any] = {
        "id": authority_id,
        "proposal_id": binding.proposal_id,
        "proposal_hash": binding.proposal_hash,
        "target_ref": binding.target_ref,
        "before_fingerprint": fp,
        "artifact_or_effect_hash": binding.artifact_hash,
        "approver": approver,
        "scopes": ["overlay_trial"],
    }
    if expires_at:
        payload["expires_at"] = expires_at
        payload["minted_at"] = "2026-07-10T11:00:00Z"
    mint = alias_store.mint_activation_authority(payload)
    assert mint["outcome"] == "created", mint
    return mint


# ===========================================================================
# Rejection tests FIRST (task gate)
# ===========================================================================


def test_reject_wrong_nonce():
    binding = _binding()
    authority = {
        "id": "aa-1",
        "status": "minted",
        "nonce_digest": digest_text("correct-nonce"),
        "proposal_id": binding.proposal_id,
        "proposal_hash": binding.proposal_hash,
        "target_ref": binding.target_ref,
        "before_fingerprint": "bf" * 32,
        "artifact_or_effect_hash": binding.artifact_hash,
        "approver": "owner@test",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    result = validate_authority_for_activation(
        authority=authority,
        nonce="wrong-nonce",
        binding=binding,
        actor="owner@test",
        live_before_fingerprint=authority["before_fingerprint"],
    )
    assert result["outcome"] == "failed"
    assert result["reason"] == "authority_nonce_mismatch"


def test_reject_expired_nonce_authority():
    binding = _binding()
    authority = {
        "id": "aa-1",
        "status": "minted",
        "nonce_digest": digest_text("n1"),
        "proposal_id": binding.proposal_id,
        "proposal_hash": binding.proposal_hash,
        "target_ref": binding.target_ref,
        "before_fingerprint": "bf" * 32,
        "artifact_or_effect_hash": binding.artifact_hash,
        "approver": "owner@test",
        "expires_at": "2020-01-01T00:00:00Z",
    }
    result = validate_authority_for_activation(
        authority=authority,
        nonce="n1",
        binding=binding,
        actor="owner@test",
        live_before_fingerprint=authority["before_fingerprint"],
        now=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    assert result["outcome"] == "failed"
    assert result["reason"] == "authority_expired"


def test_reject_replayed_consumed_authority():
    binding = _binding()
    authority = {
        "id": "aa-1",
        "status": "consumed",
        "nonce_digest": digest_text("n1"),
        "proposal_id": binding.proposal_id,
        "proposal_hash": binding.proposal_hash,
        "target_ref": binding.target_ref,
        "before_fingerprint": "bf" * 32,
        "artifact_or_effect_hash": binding.artifact_hash,
        "approver": "owner@test",
        "expires_at": "2099-01-01T00:00:00Z",
        "consumption_receipt_id": "er-1",
    }
    result = validate_authority_for_activation(
        authority=authority,
        nonce="n1",
        binding=binding,
        actor="owner@test",
        live_before_fingerprint=authority["before_fingerprint"],
    )
    assert result["outcome"] == "replayed"
    assert result["reason"] == "authority_already_consumed"


def test_reject_changed_artifact_hash():
    binding = _binding(artifact_hash="aa" * 32)
    authority = {
        "id": "aa-1",
        "status": "minted",
        "nonce_digest": digest_text("n1"),
        "proposal_id": binding.proposal_id,
        "proposal_hash": binding.proposal_hash,
        "target_ref": binding.target_ref,
        "before_fingerprint": "bf" * 32,
        "artifact_or_effect_hash": "bb" * 32,  # different
        "approver": "owner@test",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    result = validate_authority_for_activation(
        authority=authority,
        nonce="n1",
        binding=binding,
        actor="owner@test",
        live_before_fingerprint=authority["before_fingerprint"],
    )
    assert result["outcome"] == "conflict"
    assert result["reason"] == "artifact_hash_mismatch"


def test_reject_changed_target_ref():
    binding = _binding(target_ref="slot:other_slot")
    authority = {
        "id": "aa-1",
        "status": "minted",
        "nonce_digest": digest_text("n1"),
        "proposal_id": binding.proposal_id,
        "proposal_hash": binding.proposal_hash,
        "target_ref": "slot:fail_soft_language",
        "before_fingerprint": "bf" * 32,
        "artifact_or_effect_hash": binding.artifact_hash,
        "approver": "owner@test",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    result = validate_authority_for_activation(
        authority=authority,
        nonce="n1",
        binding=binding,
        actor="owner@test",
        live_before_fingerprint=authority["before_fingerprint"],
    )
    assert result["outcome"] == "conflict"
    assert result["reason"] == "target_ref_mismatch"


def test_reject_changed_base_before_fingerprint():
    binding = _binding()
    authority = {
        "id": "aa-1",
        "status": "minted",
        "nonce_digest": digest_text("n1"),
        "proposal_id": binding.proposal_id,
        "proposal_hash": binding.proposal_hash,
        "target_ref": binding.target_ref,
        "before_fingerprint": "old" + ("0" * 61),
        "artifact_or_effect_hash": binding.artifact_hash,
        "approver": "owner@test",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    live = compute_overlay_before_fingerprint(
        target_ref=binding.target_ref,
        base_commit="CHANGED_BASE",
        before_hashes=binding.before_hashes,
        prior_manifest_digest=EMPTY_DIGEST,
    )
    result = validate_authority_for_activation(
        authority=authority,
        nonce="n1",
        binding=binding,
        actor="owner@test",
        live_before_fingerprint=live,
    )
    assert result["outcome"] == "stale"
    assert result["reason"] == "before_fingerprint_mismatch"


def test_reject_stale_proposal_id():
    binding = _binding(proposal_id="prop-new")
    authority = {
        "id": "aa-1",
        "status": "minted",
        "nonce_digest": digest_text("n1"),
        "proposal_id": "prop-old",
        "proposal_hash": binding.proposal_hash,
        "target_ref": binding.target_ref,
        "before_fingerprint": "bf" * 32,
        "artifact_or_effect_hash": binding.artifact_hash,
        "approver": "owner@test",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    result = validate_authority_for_activation(
        authority=authority,
        nonce="n1",
        binding=binding,
        actor="owner@test",
        live_before_fingerprint=authority["before_fingerprint"],
    )
    assert result["outcome"] == "stale"
    assert result["reason"] == "proposal_id_mismatch"


def test_reject_incomplete_trial_policy():
    with pytest.raises(TrialPolicyError, match="decision_point"):
        TrialPolicy(
            decision_point="",
            duration_seconds=100,
            exposure_cap=10,
            target_recurrence=1,
            counterevidence_threshold=1,
            guardrail_rollback_thresholds={"x": 1},
        )
    with pytest.raises(TrialPolicyError, match="guardrail"):
        TrialPolicy(
            decision_point="route:x",
            duration_seconds=100,
            exposure_cap=10,
            target_recurrence=1,
            counterevidence_threshold=1,
            guardrail_rollback_thresholds={},
        )


def test_reject_permanent_deploy_without_gates():
    with pytest.raises(PermanentDeployError):
        assert_permanent_deploy_requirements(
            reviewed_git_content=False,
            plugin_version_bumped=True,
            host_reloaded=True,
            generation_loaded_proof="hg-abc",
        )
    with pytest.raises(PermanentDeployError, match="generation_loaded_proof"):
        assert_permanent_deploy_requirements(
            reviewed_git_content=True,
            plugin_version_bumped=True,
            host_reloaded=True,
            generation_loaded_proof=None,
        )


# ===========================================================================
# Filesystem promotion + fail-closed + session pin
# ===========================================================================


def test_stage_only_under_active_overlays_not_quarantine(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    state.mkdir()
    content = _artifact()
    path, digest = stage_overlay_content(
        state_dir=state,
        proposal_id="prop-ov-trial-1",
        content=content,
    )
    assert path == state / "dreams" / "active-overlays" / "prop-ov-trial-1" / f"{digest}.md"
    assert path.is_file()
    assert digest == digest_text(content)
    # Quarantine presence must not be a load source
    q = state / "dreams" / "quarantine" / "dream-1" / "prop-ov-trial-1"
    q.mkdir(parents=True)
    (q / "artifact.md").write_text(content, encoding="utf-8")
    loaded = load_validated_active_overlays(state_dir=state)
    assert loaded.entries == ()
    assert loaded.fail_closed is False  # empty is valid known-good


def test_presence_alone_does_not_load_overlay_file(tmp_path: pathlib.Path):
    """Gate: presence of a file under active-overlays does not load it."""
    state = tmp_path / "state"
    content = _artifact()
    path, digest = stage_overlay_content(
        state_dir=state, proposal_id="prop-x", content=content
    )
    assert path.is_file()
    # No manifest entry → nothing loadable
    bodies = resolve_loadable_overlays(state_dir=state)
    assert bodies == []
    validated = load_validated_active_overlays(state_dir=state)
    assert validated.entries == ()


def test_manifest_mismatch_fails_closed_not_open(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    content = _artifact()
    path, digest = stage_overlay_content(
        state_dir=state, proposal_id="prop-ov-trial-1", content=content
    )
    entry = ActiveOverlayEntry(
        proposal_id="prop-ov-trial-1",
        digest=digest,
        rule_id="route-empty-guidance",
        extension_slot="fail_soft_language",
        target_skill="digital-brain-buddy-session",
        target_file="skills/digital-brain-buddy-session/SKILL.md",
        trial_expires_at="2099-01-01T00:00:00Z",
        exposure_budget=50,
        rollback_generation="hg-prior",
        status="trial_active",
        base_commit="cafebabe",
        artifact_hash=digest,
    )
    man = ActiveManifest(
        schema_version="1",
        entries=(entry,),
        prior_manifest_digest=EMPTY_DIGEST,
        rollback_generation="hg-prior",
        created_at="2026-07-10T12:00:00Z",
        generation_counter=1,
    )
    atomic_replace_manifest(state_dir=state, manifest=man)

    # Tamper file content → digest mismatch → fail closed to empty
    path.write_text(content + "\nTAMPER\n", encoding="utf-8")
    closed = load_validated_active_overlays(state_dir=state)
    assert closed.fail_closed is True
    assert closed.entries == ()
    assert closed.rollback_generation == "hg-prior"
    bodies = resolve_loadable_overlays(state_dir=state)
    assert bodies == []


def test_wrong_manifest_digest_entry_fails_closed(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    content = _artifact()
    _, digest = stage_overlay_content(
        state_dir=state, proposal_id="prop-ov-trial-1", content=content
    )
    entry = ActiveOverlayEntry(
        proposal_id="prop-ov-trial-1",
        digest="0" * 64,  # wrong
        rule_id="route-empty-guidance",
        extension_slot="fail_soft_language",
        target_skill="digital-brain-buddy-session",
        target_file="skills/digital-brain-buddy-session/SKILL.md",
        trial_expires_at="2099-01-01T00:00:00Z",
        exposure_budget=10,
        rollback_generation="hg-prior",
        status="trial_active",
        base_commit="cafebabe",
        artifact_hash=digest,
    )
    man = ActiveManifest(
        schema_version="1",
        entries=(entry,),
        prior_manifest_digest=EMPTY_DIGEST,
        rollback_generation="hg-prior",
        created_at="2026-07-10T12:00:00Z",
        generation_counter=1,
    )
    atomic_replace_manifest(state_dir=state, manifest=man)
    closed = load_validated_active_overlays(state_dir=state)
    assert closed.fail_closed is True
    assert closed.entries == ()


def test_session_pin_does_not_change_mid_session(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    content = _artifact()
    _, digest = stage_overlay_content(
        state_dir=state, proposal_id="prop-ov-trial-1", content=content
    )
    entry = ActiveOverlayEntry(
        proposal_id="prop-ov-trial-1",
        digest=digest,
        rule_id="route-empty-guidance",
        extension_slot="fail_soft_language",
        target_skill="digital-brain-buddy-session",
        target_file="skills/digital-brain-buddy-session/SKILL.md",
        trial_expires_at="2099-01-01T00:00:00Z",
        exposure_budget=50,
        rollback_generation="hg-prior",
        status="trial_active",
        base_commit="cafebabe",
        artifact_hash=digest,
    )
    man = ActiveManifest(
        schema_version="1",
        entries=(entry,),
        prior_manifest_digest=EMPTY_DIGEST,
        rollback_generation="hg-prior",
        created_at="2026-07-10T12:00:00Z",
        generation_counter=1,
    )
    atomic_replace_manifest(state_dir=state, manifest=man)

    pinned = pin_session_active_overlays(state_dir=state, session_id="sess-1")
    assert len(pinned.entries) == 1
    assert pinned.entries[0].digest == digest

    # Mid-session: clear live manifest / replace with empty
    atomic_replace_manifest(
        state_dir=state,
        manifest=empty_active_manifest(
            rollback_generation="hg-prior",
            created_at="2026-07-10T13:00:00Z",
        ),
    )
    reloaded = load_session_active_overlays(state_dir=state, session_id="sess-1")
    assert reloaded is not None
    assert len(reloaded.entries) == 1
    assert reloaded.entries[0].digest == digest


# ===========================================================================
# Successful activation + receipts + no silent promote + rollback
# ===========================================================================


def test_activate_trial_stages_manifest_and_records_separately(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    session = _FakeSession()
    alias_store, effect_store = _store(session)
    binding = _binding()
    content = _artifact()
    assert binding.artifact_hash == digest_text(content)
    mint = _mint(alias_store, binding)
    policy = _trial_policy()

    result = activate_overlay_trial(
        state_dir=state,
        binding=binding,
        artifact_md=content,
        trial_policy=policy,
        authority_id=mint["authority_id"],
        nonce=mint["nonce"],
        actor="owner@test",
        rollback_generation="hg-prior",
        alias_store=alias_store,
        effect_store=effect_store,
    )
    assert result["outcome"] == "applied"
    assert result["deployment"]["status"] == "trial_active"
    assert result["effect_receipt"]["effect_type"] == "activate_overlay_trial"
    assert result["exposure_window"]["decision_point"] == policy.decision_point
    assert "deployment_id" in result
    assert result["effect_receipt"]["id"] != result["deployment"]["id"]
    assert result["exposure_window"]["id"] != result["effect_receipt"]["id"]

    # File + manifest on disk
    validated = load_validated_active_overlays(state_dir=state)
    assert validated.fail_closed is False
    assert len(validated.entries) == 1
    assert validated.entries[0].proposal_id == binding.proposal_id
    assert validated.entries[0].digest == binding.artifact_hash
    assert validated.entries[0].status == "trial_active"
    bodies = resolve_loadable_overlays(state_dir=state)
    assert len(bodies) == 1
    assert bodies[0]["digest"] == binding.artifact_hash
    assert "fail-soft" in bodies[0]["body"]

    # Graph records
    assert len(session.effects) == 1
    assert len(session.deployments) == 1
    assert len(session.exposure_windows) == 1
    dep = next(iter(session.deployments.values()))
    assert dep["status"] == "trial_active"
    # Authority consumed
    auth = session.authorities[mint["authority_id"]]
    assert auth["status"] == "consumed"


def test_trials_expire_and_never_silently_promote(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    session = _FakeSession()
    alias_store, effect_store = _store(session)
    binding = _binding()
    content = _artifact()
    # Authority must still be valid at activation time; trial duration is separate.
    mint = _mint(
        alias_store,
        binding,
        expires_at="2099-01-01T00:00:00Z",
    )
    # Short trial duration so a slightly later clock expires it.
    policy = _trial_policy(duration_seconds=1)
    act_now = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    result = activate_overlay_trial(
        state_dir=state,
        binding=binding,
        artifact_md=content,
        trial_policy=policy,
        authority_id=mint["authority_id"],
        nonce=mint["nonce"],
        actor="owner@test",
        rollback_generation="hg-prior",
        alias_store=alias_store,
        effect_store=effect_store,
        now=act_now,
    )
    assert result["outcome"] == "applied", result

    expired = expire_active_trials(
        state_dir=state,
        effect_store=effect_store,
        actor="owner@test",
        now=act_now + timedelta(seconds=5),
    )
    assert expired["outcome"] == "applied"
    assert expired["expired_count"] >= 1
    validated = load_validated_active_overlays(state_dir=state)
    # Expired entries are disabled — not loadable, not promoted to deployed
    for e in validated.entries:
        assert e.status in {"expired", "disabled"}
        assert e.status != "deployed"
    bodies = resolve_loadable_overlays(state_dir=state)
    assert bodies == []
    # Deployment must not silently become deployed
    for d in session.deployments.values():
        assert d["status"] in {"trial_active", "expired", "rolled_back"}
        assert d["status"] != "deployed"


def test_rollback_is_compensating_and_artifact_specific(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    session = _FakeSession()
    alias_store, effect_store = _store(session)
    binding = _binding()
    content = _artifact()
    mint = _mint(alias_store, binding)
    act = activate_overlay_trial(
        state_dir=state,
        binding=binding,
        artifact_md=content,
        trial_policy=_trial_policy(),
        authority_id=mint["authority_id"],
        nonce=mint["nonce"],
        actor="owner@test",
        rollback_generation="hg-prior",
        alias_store=alias_store,
        effect_store=effect_store,
    )
    assert act["outcome"] == "applied"
    prior_digest = act["prior_manifest_digest"]
    after_digest = act["manifest_digest"]
    assert prior_digest == EMPTY_DIGEST or prior_digest != after_digest

    # Rollback requires the exact prior manifest digest (artifact-specific)
    with pytest.raises(ActiveOverlayError, match="prior_manifest"):
        rollback_overlay_trial(
            state_dir=state,
            proposal_id=binding.proposal_id,
            prior_manifest_digest="ff" * 32,  # wrong prior
            actor="owner@test",
            effect_store=effect_store,
            deployment_id=act["deployment"]["id"],
            reason="test",
        )

    rb = rollback_overlay_trial(
        state_dir=state,
        proposal_id=binding.proposal_id,
        prior_manifest_digest=prior_digest,
        actor="owner@test",
        effect_store=effect_store,
        deployment_id=act["deployment"]["id"],
        reason="guardrail_regression",
        prior_manifest=act["prior_manifest"],
    )
    assert rb["outcome"] == "applied"
    assert rb["effect_receipt"]["effect_type"] == "rollback_overlay_trial"
    # Prior empty restored
    validated = load_validated_active_overlays(state_dir=state)
    assert validated.entries == ()
    assert resolve_loadable_overlays(state_dir=state) == []
    # Audit history preserved (activation + rollback receipts)
    assert len(session.effects) >= 2
    dep = session.deployments[act["deployment"]["id"]]
    assert dep["status"] == "rolled_back"


def test_activation_not_in_overlay_effect_types_for_analyzer_confusion():
    # Model-facing surface must not include activation; effect types are operator-only.
    assert "activate_overlay_trial" in OVERLAY_EFFECT_TYPES
    assert "rollback_overlay_trial" in OVERLAY_EFFECT_TYPES
    # Permanent deploy is separate and gated
    assert "permanent_overlay_deploy" in OVERLAY_EFFECT_TYPES


def test_activate_rejects_bad_nonce_end_to_end(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    session = _FakeSession()
    alias_store, effect_store = _store(session)
    binding = _binding()
    mint = _mint(alias_store, binding)
    result = activate_overlay_trial(
        state_dir=state,
        binding=binding,
        artifact_md=_artifact(),
        trial_policy=_trial_policy(),
        authority_id=mint["authority_id"],
        nonce="not-the-nonce",
        actor="owner@test",
        rollback_generation="hg-prior",
        alias_store=alias_store,
        effect_store=effect_store,
    )
    assert result["outcome"] == "failed"
    assert result["reason"] == "authority_nonce_mismatch"
    assert load_validated_active_overlays(state_dir=state).entries == ()
    assert session.effects == {}


def test_manifest_lists_required_fields(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    session = _FakeSession()
    alias_store, effect_store = _store(session)
    binding = _binding()
    mint = _mint(alias_store, binding)
    act = activate_overlay_trial(
        state_dir=state,
        binding=binding,
        artifact_md=_artifact(),
        trial_policy=_trial_policy(),
        authority_id=mint["authority_id"],
        nonce=mint["nonce"],
        actor="owner@test",
        rollback_generation="hg-prior",
        alias_store=alias_store,
        effect_store=effect_store,
    )
    public = manifest_to_public_dict(
        load_validated_active_overlays(state_dir=state)
    )
    entry = public["entries"][0]
    for key in (
        "digest",
        "rule_id",
        "proposal_id",
        "trial_expires_at",
        "exposure_budget",
        "rollback_generation",
    ):
        assert key in entry
    assert public["rollback_generation"] == "hg-prior"
    assert act["manifest_digest"] == compute_manifest_digest(public)


def test_request_hash_stable():
    binding = _binding()
    a = build_activation_request_hash(
        effect_type="activate_overlay_trial",
        binding=binding,
        prior_manifest_digest=EMPTY_DIGEST,
        artifact_hash=binding.artifact_hash,
    )
    b = build_activation_request_hash(
        effect_type="activate_overlay_trial",
        binding=binding,
        prior_manifest_digest=EMPTY_DIGEST,
        artifact_hash=binding.artifact_hash,
    )
    assert a == b
    c = build_activation_request_hash(
        effect_type="activate_overlay_trial",
        binding=binding,
        prior_manifest_digest="11" * 32,
        artifact_hash=binding.artifact_hash,
    )
    assert c != a
