"""Crash-recovery and adversarial chaos gates for Dreams (in-process fakes).

Chaos points:
  - after every DreamRun checkpoint (stop_after_stage + resume)
  - after Alias mutation before response (authority consumed, re-apply replayed)
  - after manifest rename before graph receipt (post_manifest reconcile)
  - lease takeover (stale epoch rejection)
  - concurrent/double activation (flock + request_hash replay)
  - stale base (before_fingerprint mismatch)
  - symlink substitution (secure state dir + state refuse)
  - external packet containing an intimate field
"""

from __future__ import annotations

import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))

from digital_brain.maintenance.activation import (  # noqa: E402
    OverlayActivationBinding,
    TrialPolicy,
    activate_overlay_trial,
    compute_overlay_before_fingerprint,
    validate_authority_for_activation,
    write_activation_pending,
)
from digital_brain.maintenance.active_overlays import (  # noqa: E402
    load_validated_active_overlays,
    resolve_loadable_overlays,
)
from digital_brain.maintenance.artifacts import (  # noqa: E402
    SecureStateDirError,
    resolve_secure_state_dir,
)
from digital_brain.maintenance.models import EMPTY_DIGEST, digest_text  # noqa: E402
from digital_brain.maintenance.privacy import (  # noqa: E402
    IntimateFieldError,
    assert_no_intimate_fields,
    redact_packet,
)
from digital_brain.maintenance.reconcile import (  # noqa: E402
    reconcile_overlay_activation,
    same_request_replays_without_duplicate,
)
from digital_brain.maintenance.runner import (  # noqa: E402
    DreamRunCheckpoint,
    DreamRunner,
)
from digital_brain.maintenance.snapshot import load_evidence_fixture  # noqa: E402
from tests.test_alias_effects import (  # noqa: E402
    _FakeSession as _AliasFakeSession,
    _mint_and_apply,
    _seed_person,
    _store as _alias_store,
)
from tests.test_mcp_cypher_maintenance import (  # noqa: E402
    GENERATION_ID,
    _FakeMaintSession,
    _store_with as _maint_store_with,
)
from tests.test_overlay_activation import (  # noqa: E402
    _FakeSession as _OverlayFakeSession,
    _store as _overlay_store,
)

FIXTURE = ROOT / "tests" / "fixtures" / "dreams" / "evidence" / "sample_ledger.json"
CUTOFF = "2026-07-10T12:00:00Z"
CORRELATION_KEY = b"dream-chaos-correlation-key"
TARGET_FILE = "skills/digital-brain-buddy-session/SKILL.md"


def _artifact() -> str:
    return (
        "<!-- OVERLAY_SLOT:fail_soft_language BEGIN -->\n"
        "### Rule `route-empty-guidance`\n"
        "Prefer fail-soft language when READ returns empty.\n"
        "<!-- OVERLAY_SLOT:fail_soft_language END -->\n"
    )


def _binding(**overrides: Any) -> OverlayActivationBinding:
    content = _artifact()
    digest = digest_text(content)
    base = dict(
        proposal_id="prop-chaos-1",
        proposal_hash="ph" * 32,
        artifact_hash=digest,
        target_ref="slot:fail_soft_language",
        base_commit="cafebabe",
        before_hashes={TARGET_FILE: "bb" * 32},
        rule_id="route-empty-guidance",
        extension_slot="fail_soft_language",
        target_skill="digital-brain-buddy-session",
        target_file=TARGET_FILE,
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
        guardrail_rollback_thresholds={
            "privacy_gate_failure_count": 1,
            "guardrail_regression_rate": 0.1,
        },
    )
    base.update(overrides)
    return TrialPolicy(**base)


def _mint(
    alias_store: Any,
    binding: OverlayActivationBinding,
    *,
    authority_id: str = "aa-chaos-1",
) -> dict[str, Any]:
    fp = compute_overlay_before_fingerprint(
        target_ref=binding.target_ref,
        base_commit=binding.base_commit,
        before_hashes=binding.before_hashes,
        prior_manifest_digest=EMPTY_DIGEST,
    )
    mint = alias_store.mint_activation_authority(
        {
            "id": authority_id,
            "proposal_id": binding.proposal_id,
            "proposal_hash": binding.proposal_hash,
            "target_ref": binding.target_ref,
            "before_fingerprint": fp,
            "artifact_or_effect_hash": binding.artifact_hash,
            "approver": "owner@chaos",
            "scopes": ["overlay_trial"],
        }
    )
    assert mint["outcome"] == "created", mint
    return mint


def _runner(session: _FakeMaintSession | None = None) -> tuple[DreamRunner, _FakeMaintSession]:
    session = session or _FakeMaintSession()
    store = _maint_store_with(session)
    return (
        DreamRunner(
            store=store,
            holder_id="host-chaos",
            harness_generation_id=GENERATION_ID,
            correlation_key=CORRELATION_KEY,
            base_commit="abc1234",
        ),
        session,
    )


# ---------------------------------------------------------------------------
# DreamRun checkpoint chaos (every stage)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stage",
    [
        "leased",
        "snapshotting",
        "normalizing",
        "clustering",
        "planning",
        "compiling",
        "validating",
        "publishing",
    ],
)
def test_crash_after_checkpoint_resumes_without_duplicate_effects(stage: str):
    """After every DreamRun checkpoint, resume replays receipts without dupes."""
    runner, session = _runner()
    run_id = f"dream-crash-{stage}"
    evidence = load_evidence_fixture(FIXTURE)

    with pytest.raises(DreamRunCheckpoint) as cp:
        runner.run(
            evidence,
            cutoff_at=CUTOFF,
            run_id=run_id,
            holdout_ids=["re-fail-2"],
            release_lease=False,
            stop_after_stage=stage,
        )
    assert cp.value.stage == stage
    assert cp.value.run_id == run_id
    findings_after = dict(session.findings)
    proposals_after = dict(session.proposals)
    snapshots_after = set(session.snapshots)
    stages_after = set(session.stages)

    result = runner.run(
        evidence,
        cutoff_at=CUTOFF,
        run_id=run_id,
        holdout_ids=["re-fail-2"],
        release_lease=True,
    )
    assert result.stage == "completed"
    assert result.resumed is True
    assert result.stage_outcomes.get(stage) == "replayed"
    # Early checkpoints may not have findings yet; later ones must not grow on resume.
    if findings_after:
        assert set(session.findings) == set(findings_after)
    if proposals_after:
        assert set(session.proposals) == set(proposals_after)
    # Stage receipts from the crash are preserved (replayed, not re-keyed).
    assert stages_after <= set(session.stages)
    # Snapshot for this run remains single.
    assert sum(1 for k in session.snapshots if str(k).startswith("snap-")) == 1
    # Completed pipeline recorded once.
    assert f"{run_id}:completed:0" in session.stages
    # Findings/proposals stable after a completed resume (no extra keys if
    # planning already ran before the crash).
    if stage in {"planning", "compiling", "validating", "publishing"}:
        assert findings_after
        assert set(session.findings) == set(findings_after)


# ---------------------------------------------------------------------------
# Alias mutation before response
# ---------------------------------------------------------------------------


def test_alias_apply_crash_before_response_replays_without_duplicate():
    """Authority consumed; lost response re-apply is replayed, not reminted."""
    session = _AliasFakeSession()
    _seed_person(session)
    store = _alias_store(session)
    out = _mint_and_apply(store, session)
    assert out["apply"]["outcome"] == "applied"
    auth_id = out["mint"]["authority_id"]
    assert session.authorities[auth_id]["status"] == "consumed"
    n_aliases = len(session.aliases)
    n_effects = len(session.effects)

    # Client lost the response → re-submit same apply.
    replay = store.apply_alias(
        {
            "authority_id": auth_id,
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
    assert replay.get("replacement_minted") is False
    assert len(session.aliases) == n_aliases
    assert len(session.effects) == n_effects
    receipt = store.get_authority_receipt(auth_id)
    assert receipt["outcome"] == "found"
    assert receipt["status"] == "consumed"
    assert receipt["effect_receipt"] is not None


# ---------------------------------------------------------------------------
# Manifest advanced, graph receipt missing
# ---------------------------------------------------------------------------


def test_post_manifest_crash_reconciles_graph_receipt_once(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    session = _OverlayFakeSession()
    alias_store, effect_store = _overlay_store(session)
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
        actor="owner@chaos",
        rollback_generation="hg-prior",
        alias_store=alias_store,
        effect_store=effect_store,
    )
    assert act["outcome"] == "applied"
    req = act["request_hash"]

    # Simulate lost graph after FS write.
    session.effects.clear()
    session.effects_by_key.clear()
    session.effects_by_request.clear()
    session.deployments.clear()
    session.exposure_windows.clear()
    write_activation_pending(
        state,
        {
            "request_hash": req,
            "effect_key": f"overlay-trial:{binding.proposal_id}:{binding.artifact_hash}",
            "proposal_id": binding.proposal_id,
            "manifest_digest": act["manifest_digest"],
            "prior_manifest_digest": act["prior_manifest_digest"],
            "prior_manifest": act["prior_manifest"],
            "authority_id": mint["authority_id"],
            "receipt_id": "er-chaos-1",
            "deployment_id": "dep-chaos-1",
            "window_id": "ew-chaos-1",
            "digest": binding.artifact_hash,
            "phase": "post_manifest",
        },
    )

    rec = reconcile_overlay_activation(
        state_dir=state,
        effect_store=effect_store,
        request_hash=req,
        actor="owner@chaos",
    )
    assert rec["outcome"] in {"applied", "replayed"}
    assert rec.get("duplicate_activation") is False
    assert effect_store.get_effect_by_request_hash(req) is not None

    rec2 = reconcile_overlay_activation(
        state_dir=state,
        effect_store=effect_store,
        request_hash=req,
    )
    assert rec2["outcome"] == "replayed"
    assert len(session.effects) == 1

    check = same_request_replays_without_duplicate(
        state_dir=state,
        effect_store=effect_store,
        request_hash=req,
    )
    assert check["duplicate_activation"] is False


def test_pre_manifest_crash_abandons_without_activation(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    session = _OverlayFakeSession()
    _alias_store, effect_store = _overlay_store(session)
    req = "ab" * 32
    write_activation_pending(
        state,
        {
            "request_hash": req,
            "phase": "pre_manifest",
            "proposal_id": "prop-pre",
            "prior_manifest_digest": EMPTY_DIGEST,
        },
    )
    rec = reconcile_overlay_activation(
        state_dir=state,
        effect_store=effect_store,
        request_hash=req,
    )
    assert rec["outcome"] == "idle"
    assert rec["reason"] == "pre_manifest_abandoned"
    assert effect_store.get_effect_by_request_hash(req) is None
    assert load_validated_active_overlays(state_dir=state).entries == ()


# ---------------------------------------------------------------------------
# Lease takeover
# ---------------------------------------------------------------------------


def test_lease_takeover_rejects_stale_holder_mutations():
    session = _FakeMaintSession()
    store = _maint_store_with(session)

    first = store.acquire_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-a",
            "run_id": "run-a",
            "ttl_seconds": 10,
        }
    )
    assert first["outcome"] == "acquired"
    assert first["epoch"] == 1
    store.create_dream_run(
        {
            "id": "run-a",
            "run_id": "run-a",
            "epoch": 1,
            "holder_id": "host-a",
            "harness_generation_id": GENERATION_ID,
        }
    )
    store.record_dream_stage({"run_id": "run-a", "epoch": 1, "stage": "leased"})

    session.advance(11)
    takeover = store.acquire_maintenance_lease(
        {
            "key": "maintenance",
            "holder_id": "host-b",
            "run_id": "run-b",
            "ttl_seconds": 30,
        }
    )
    assert takeover["outcome"] == "acquired"
    assert takeover["epoch"] == 2
    assert takeover["previous_epoch"] == 1

    for op_name, call in (
        (
            "renew",
            lambda: store.renew_maintenance_lease(
                {
                    "key": "maintenance",
                    "holder_id": "host-a",
                    "run_id": "run-a",
                    "epoch": 1,
                    "ttl_seconds": 30,
                }
            ),
        ),
        (
            "stage",
            lambda: store.record_dream_stage(
                {"run_id": "run-a", "epoch": 1, "stage": "snapshotting"}
            ),
        ),
        (
            "finding",
            lambda: store.create_finding(
                {
                    "id": "find-stale",
                    "dream_id": "run-a",
                    "snapshot_id": "snap-x",
                    "class_key": "x",
                    "lane": "memory",
                    "summary": "stale",
                    "evidence_strength": "tentative",
                    "run_id": "run-a",
                    "epoch": 1,
                }
            ),
        ),
    ):
        result = call()
        assert result["outcome"] == "stale_epoch", f"{op_name}: {result}"


# ---------------------------------------------------------------------------
# Concurrent / double activation
# ---------------------------------------------------------------------------


def test_double_activation_same_request_is_idempotent(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    session = _OverlayFakeSession()
    alias_store, effect_store = _overlay_store(session)
    binding = _binding()
    content = _artifact()
    mint = _mint(alias_store, binding)

    first = activate_overlay_trial(
        state_dir=state,
        binding=binding,
        artifact_md=content,
        trial_policy=_trial_policy(),
        authority_id=mint["authority_id"],
        nonce=mint["nonce"],
        actor="owner@chaos",
        rollback_generation="hg-prior",
        alias_store=alias_store,
        effect_store=effect_store,
    )
    assert first["outcome"] == "applied"
    req = first["request_hash"]
    n_effects = len(session.effects)

    second = activate_overlay_trial(
        state_dir=state,
        binding=binding,
        artifact_md=content,
        trial_policy=_trial_policy(),
        authority_id=mint["authority_id"],
        nonce=mint["nonce"],
        actor="owner@chaos",
        rollback_generation="hg-prior",
        alias_store=alias_store,
        effect_store=effect_store,
        request_hash=req,
    )
    # Replayed or rejected as already consumed — never a second live effect.
    assert second["outcome"] in {"replayed", "applied", "failed"}
    if second["outcome"] == "applied":
        # Same request_hash must not create a second effect row.
        assert len(session.effects) == n_effects
    elif second["outcome"] == "replayed":
        assert len(session.effects) == n_effects
    bodies = resolve_loadable_overlays(state_dir=state)
    assert len(bodies) == 1


def test_concurrent_activation_lock_serializes(tmp_path: pathlib.Path):
    """Two threads racing activate_overlay_trial: only one applied effect."""
    state = tmp_path / "state"
    session = _OverlayFakeSession()
    alias_store, effect_store = _overlay_store(session)
    binding = _binding()
    content = _artifact()
    mint = _mint(alias_store, binding, authority_id="aa-race-1")

    def _once() -> dict[str, Any]:
        return activate_overlay_trial(
            state_dir=state,
            binding=binding,
            artifact_md=content,
            trial_policy=_trial_policy(),
            authority_id=mint["authority_id"],
            nonce=mint["nonce"],
            actor="owner@chaos",
            rollback_generation="hg-prior",
            alias_store=alias_store,
            effect_store=effect_store,
        )

    outcomes: list[str] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(_once), pool.submit(_once)]
        for fut in as_completed(futs):
            try:
                outcomes.append(fut.result()["outcome"])
            except Exception as exc:  # lock contention may raise
                outcomes.append(f"error:{type(exc).__name__}")

    # At most one applied; the other is replayed/failed/error — never two effects.
    assert outcomes.count("applied") <= 1 or len(session.effects) <= 1
    assert len(session.effects) <= 1
    bodies = resolve_loadable_overlays(state_dir=state)
    assert len(bodies) <= 1


# ---------------------------------------------------------------------------
# Stale base
# ---------------------------------------------------------------------------


def test_stale_base_fingerprint_rejects_activation():
    binding = _binding()
    live = compute_overlay_before_fingerprint(
        target_ref=binding.target_ref,
        base_commit="CHANGED",
        before_hashes=binding.before_hashes,
        prior_manifest_digest=EMPTY_DIGEST,
    )
    result = validate_authority_for_activation(
        authority={
            "id": "aa-stale",
            "status": "minted",
            "nonce_digest": digest_text("n1"),
            "proposal_id": binding.proposal_id,
            "proposal_hash": binding.proposal_hash,
            "target_ref": binding.target_ref,
            "before_fingerprint": "old" + ("0" * 61),
            "artifact_or_effect_hash": binding.artifact_hash,
            "approver": "owner@chaos",
            "expires_at": "2099-01-01T00:00:00Z",
        },
        nonce="n1",
        binding=binding,
        actor="owner@chaos",
        live_before_fingerprint=live,
    )
    assert result["outcome"] == "stale"
    assert result["reason"] == "before_fingerprint_mismatch"


# ---------------------------------------------------------------------------
# Symlink substitution
# ---------------------------------------------------------------------------


def test_secure_state_dir_refuses_symlink_substitution(tmp_path: pathlib.Path):
    real = tmp_path / "real-state"
    real.mkdir()
    link = tmp_path / "link-state"
    link.symlink_to(real)
    with pytest.raises(SecureStateDirError, match="symlink"):
        resolve_secure_state_dir(link, repo_root=ROOT, create=False)


def test_secure_state_dir_refuses_repo_path_as_state():
    inside = ROOT / ".pytest_dreams_chaos_state_should_fail"
    try:
        with pytest.raises(SecureStateDirError, match="inside_repo"):
            resolve_secure_state_dir(inside, repo_root=ROOT, create=True)
    finally:
        if inside.exists():
            if inside.is_dir():
                inside.rmdir()
            else:
                inside.unlink()


# ---------------------------------------------------------------------------
# Intimate field in external packet
# ---------------------------------------------------------------------------


def test_external_packet_with_intimate_field_fails_closed():
    packet = {
        "proposal_id": "prop-x",
        "items": [{"id": "fb-1", "raw_payload": "intimate secret quote"}],
        "meta": {"note": "ok"},
    }
    with pytest.raises(IntimateFieldError):
        assert_no_intimate_fields(packet)
    redacted = redact_packet(packet, correlation_key=CORRELATION_KEY)
    assert_no_intimate_fields(redacted)
    assert "intimate secret quote" not in str(redacted)
    assert "raw_payload" not in str(redacted)


def test_expired_authority_rejected_at_activation(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    session = _OverlayFakeSession()
    alias_store, effect_store = _overlay_store(session)
    binding = _binding()
    content = _artifact()
    fp = compute_overlay_before_fingerprint(
        target_ref=binding.target_ref,
        base_commit=binding.base_commit,
        before_hashes=binding.before_hashes,
        prior_manifest_digest=EMPTY_DIGEST,
    )
    mint = alias_store.mint_activation_authority(
        {
            "id": "aa-expired-chaos",
            "proposal_id": binding.proposal_id,
            "proposal_hash": binding.proposal_hash,
            "target_ref": binding.target_ref,
            "before_fingerprint": fp,
            "artifact_or_effect_hash": binding.artifact_hash,
            "approver": "owner@chaos",
            "scopes": ["overlay_trial"],
            "expires_at": "2020-01-01T00:00:00Z",
            "minted_at": "2019-12-01T00:00:00Z",
        }
    )
    assert mint["outcome"] == "created"
    result = activate_overlay_trial(
        state_dir=state,
        binding=binding,
        artifact_md=content,
        trial_policy=_trial_policy(),
        authority_id=mint["authority_id"],
        nonce=mint["nonce"],
        actor="owner@chaos",
        rollback_generation="hg-prior",
        alias_store=alias_store,
        effect_store=effect_store,
        now=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    assert result["outcome"] in {"failed", "stale"}
    assert load_validated_active_overlays(state_dir=state).entries == ()
    assert session.effects == {}
