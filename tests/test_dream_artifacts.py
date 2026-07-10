"""Quarantine layout, secure state dir, immutability, and zero runtime effect."""

from __future__ import annotations

import json
import os
import pathlib
import stat
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from digital_brain.maintenance.artifacts import (  # noqa: E402
    ARTIFACT_FILENAME,
    CHECKSUMS_FILENAME,
    DEFAULT_ISOLATED_VALIDATION_COMMANDS,
    INTENT_FILENAME,
    ISOLATED_VALIDATION_COMMANDS,
    MANIFEST_FILENAME,
    ArtifactError,
    ImmutableArtifactError,
    IsolatedValidationError,
    SecureStateDirError,
    ValidationCommandError,
    assert_validation_commands_allowed,
    build_manifest,
    compute_patch_sha256,
    quarantine_proposal_dir,
    resolve_secure_state_dir,
    validate_quarantine_isolated,
    write_quarantine_bundle,
)
from digital_brain.maintenance.generation import (  # noqa: E402
    ACTIVE_OVERLAY_MANIFEST_REL,
    collect_harness_generation,
    resolve_state_dir,
)
from digital_brain.maintenance.models import EMPTY_DIGEST  # noqa: E402

PLUGIN = ROOT / "plugins" / "digital-brain-buddy"
SESSION_SKILL = PLUGIN / "skills" / "digital-brain-buddy-session" / "SKILL.md"
READ_SKILL = PLUGIN / "skills" / "digital-brain-buddy-read-memory" / "SKILL.md"
WRITE_SKILL = PLUGIN / "skills" / "digital-brain-buddy-write-memory" / "SKILL.md"
GRAPH_SKILL = PLUGIN / "skills" / "digital-brain-buddy-graph-mcp" / "SKILL.md"
IDENTITY_SKILL = (
    PLUGIN / "skills" / "digital-brain-buddy-identity-bootstrap" / "SKILL.md"
)
GENERATION_PY = ROOT / "digital_brain" / "maintenance" / "generation.py"
MCP_CLIENT = ROOT / "digital_brain" / "tools" / "mcp_client.py"


def _sample_intent(**overrides):
    base = {
        "id": "intent-1",
        "dream_id": "dream-art-1",
        "snapshot_id": "snap-art-1",
        "lane": "behaviour",
        "effect_type": "overlay_rule",
        "operation": "add_rule",
        "rule_id": "route-empty-guidance",
        "summary": "Fail soft on empty READ",
        "expected_outcome": "owner_trial_only",
        "extension_slot": "fail_soft_language",
        "target_skill": "digital-brain-buddy-session",
    }
    base.update(overrides)
    return base


def _write_bundle(state: pathlib.Path, **kwargs):
    intent = kwargs.pop("intent", _sample_intent())
    artifact_md = kwargs.pop(
        "artifact_md",
        "<!-- OVERLAY_SLOT:fail_soft_language BEGIN -->\n### Rule `route-empty-guidance`\n<!-- OVERLAY_SLOT:fail_soft_language END -->\n",
    )
    manifest = kwargs.pop(
        "manifest",
        build_manifest(
            proposal_id="prop-art-1",
            dream_id="dream-art-1",
            evidence_snapshot_id="snap-art-1",
            target_skill="digital-brain-buddy-session",
            extension_slot="fail_soft_language",
            rule_id="route-empty-guidance",
            base_commit="abc123",
            before_hashes={
                "skills/digital-brain-buddy-session/SKILL.md": "deadbeef"
            },
            target_file="skills/digital-brain-buddy-session/SKILL.md",
            compiler_version="1",
            schema_version="1",
            patch_sha256="pending",
            artifact_relpath="dreams/quarantine/dream-art-1/prop-art-1/artifact.md",
        ),
    )
    return write_quarantine_bundle(
        state_dir=state,
        dream_id=intent["dream_id"],
        proposal_id=kwargs.pop("proposal_id", "prop-art-1"),
        intent=intent,
        artifact_md=artifact_md,
        manifest=manifest,
        evaluation=kwargs.pop("evaluation", {"outcome": "passed"}),
        repo_root=kwargs.pop("repo_root", ROOT),
        **kwargs,
    )


def test_secure_state_dir_creates_0700(tmp_path, monkeypatch):
    state = tmp_path / "db-state"
    monkeypatch.delenv("DIGITAL_BRAIN_STATE_DIR", raising=False)
    resolved = resolve_secure_state_dir(state, repo_root=ROOT, create=True)
    assert resolved.is_dir()
    mode = resolved.stat().st_mode & 0o777
    assert mode == 0o700
    assert not resolved.is_symlink()


def test_secure_state_dir_refuses_symlink(tmp_path):
    real = tmp_path / "real-state"
    real.mkdir()
    link = tmp_path / "link-state"
    link.symlink_to(real)
    with pytest.raises(SecureStateDirError, match="symlink"):
        resolve_secure_state_dir(link, repo_root=ROOT, create=False)


def test_secure_state_dir_refuses_inside_repo(tmp_path):
    # Point state at a path under the worktree root.
    inside = ROOT / ".pytest_quarantine_should_fail"
    try:
        with pytest.raises(SecureStateDirError, match="inside_repo"):
            resolve_secure_state_dir(inside, repo_root=ROOT, create=True)
    finally:
        if inside.exists():
            if inside.is_dir():
                inside.rmdir()
            else:
                inside.unlink()


def test_secure_state_dir_refuses_world_writable(tmp_path):
    state = tmp_path / "world"
    state.mkdir()
    os.chmod(state, 0o777)
    with pytest.raises(SecureStateDirError, match="world_writable|insecure_mode"):
        resolve_secure_state_dir(state, repo_root=ROOT, create=False)


def test_quarantine_layout_and_checksums(tmp_path):
    state = tmp_path / "state"
    bundle = _write_bundle(state)
    d = bundle.directory
    assert d.is_dir()
    for name in (
        INTENT_FILENAME,
        ARTIFACT_FILENAME,
        MANIFEST_FILENAME,
        "evaluation.json",
        CHECKSUMS_FILENAME,
    ):
        path = d / name
        assert path.is_file()
        mode = path.stat().st_mode
        assert not (mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
        assert mode & 0o777 == 0o600

    checksums = json.loads((d / CHECKSUMS_FILENAME).read_text(encoding="utf-8"))
    assert set(checksums) >= {
        INTENT_FILENAME,
        ARTIFACT_FILENAME,
        MANIFEST_FILENAME,
        "evaluation.json",
    }
    manifest = json.loads((d / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest["proposal_id"] == "prop-art-1"
    assert manifest["rule_id"] == "route-empty-guidance"
    assert manifest["base_commit"] == "abc123"
    assert manifest["extension_slot"] == "fail_soft_language"
    assert manifest["patch_sha256"]
    assert bundle.patch_sha256 == manifest["patch_sha256"]


def test_quarantine_immutable_replay_and_drift(tmp_path):
    state = tmp_path / "state"
    b1 = _write_bundle(state)
    b2 = _write_bundle(state)
    assert b1.patch_sha256 == b2.patch_sha256
    assert b1.directory == b2.directory

    with pytest.raises(ImmutableArtifactError, match="immutable"):
        _write_bundle(
            state,
            artifact_md="<!-- OVERLAY_SLOT:fail_soft_language BEGIN -->\nchanged\n<!-- OVERLAY_SLOT:fail_soft_language END -->\n",
        )


def test_path_traversal_ids_rejected(tmp_path):
    state = tmp_path / "state"
    resolve_secure_state_dir(state, repo_root=ROOT, create=True)
    with pytest.raises(ArtifactError, match="unsafe_path_segment|path_traversal"):
        quarantine_proposal_dir(state, "../escape", "prop-1")
    with pytest.raises(ArtifactError, match="unsafe_path_segment|path_traversal"):
        quarantine_proposal_dir(state, "dream-1", "prop/../../etc")


def test_quarantine_has_zero_runtime_effect_on_harness(tmp_path, monkeypatch):
    """Writing quarantine must not change overlay_manifest_digest or load paths."""
    state = tmp_path / "state"
    monkeypatch.setenv("DIGITAL_BRAIN_STATE_DIR", str(state))
    resolve_secure_state_dir(state, repo_root=ROOT, create=True)

    before = collect_harness_generation(
        repo_root=ROOT,
        plugin_root=PLUGIN,
        state_dir=state,
        core_commit="c0",
        core_tree_digest="t0",
        dirty_state_digest=EMPTY_DIGEST,
        plugin_version="0.0-test",
        soul_sha=EMPTY_DIGEST,
        mcp_version="0.0-test",
        model_id="test",
    )
    assert before.overlay_manifest_digest == EMPTY_DIGEST

    _write_bundle(state)

    after = collect_harness_generation(
        repo_root=ROOT,
        plugin_root=PLUGIN,
        state_dir=state,
        core_commit="c0",
        core_tree_digest="t0",
        dirty_state_digest=EMPTY_DIGEST,
        plugin_version="0.0-test",
        soul_sha=EMPTY_DIGEST,
        mcp_version="0.0-test",
        model_id="test",
    )
    assert after.overlay_manifest_digest == before.overlay_manifest_digest == EMPTY_DIGEST
    assert after.id == before.id
    # Active overlay path still absent.
    assert not (state / ACTIVE_OVERLAY_MANIFEST_REL).exists()
    # Quarantine exists but is not the active path.
    q = state / "dreams" / "quarantine"
    assert q.is_dir()
    assert q != state / "dreams" / "active-overlays"


def test_runtime_loaders_and_session_skills_never_mention_quarantine():
    """Static gate: runtime loaders / session skills must not reference quarantine."""
    runtime_paths = [
        SESSION_SKILL,
        READ_SKILL,
        WRITE_SKILL,
        GRAPH_SKILL,
        IDENTITY_SKILL,
        GENERATION_PY,
        MCP_CLIENT,
        ROOT / "plugins" / "digital-brain-buddy" / "scripts" / "compose-up.sh",
        ROOT / "plugins" / "digital-brain-buddy" / "hooks" / "hooks.json",
    ]
    # generation.py may mention quarantine only in a negative comment; the load
    # path must still not open dreams/quarantine.
    for path in runtime_paths:
        assert path.is_file(), f"missing {path}"
        text = path.read_text(encoding="utf-8")
        if path == GENERATION_PY:
            # Allowed: explicit "not quarantine" comment on active overlay digest.
            assert "ACTIVE_OVERLAY_MANIFEST_REL" in text
            assert "dreams/quarantine" not in text
            assert 'Path("dreams") / "quarantine"' not in text
            # The word may appear in a docstring warning — ensure no load path.
            assert "quarantine/" not in text
            continue
        assert "quarantine" not in text.lower(), f"quarantine mentioned in {path}"


def test_resolve_state_dir_still_works_for_non_secure_helpers(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGITAL_BRAIN_STATE_DIR", str(tmp_path / "plain"))
    p = resolve_state_dir()
    assert p == (tmp_path / "plain").resolve()


def test_compute_patch_sha256_stable_and_matches_bundle(tmp_path):
    state = tmp_path / "state"
    bundle = _write_bundle(state)
    recomputed = compute_patch_sha256(
        intent=bundle.intent,
        artifact_md=bundle.artifact_md,
        evaluation=bundle.evaluation,
        manifest=bundle.manifest,
    )
    assert recomputed == bundle.patch_sha256 == bundle.manifest["patch_sha256"]
    on_disk = json.loads(
        (bundle.directory / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert on_disk["patch_sha256"] == recomputed


def test_isolated_validation_success(tmp_path):
    state = tmp_path / "state"
    bundle = _write_bundle(state)
    result = validate_quarantine_isolated(
        bundle.directory,
        commands=DEFAULT_ISOLATED_VALIDATION_COMMANDS,
        state_dir=state,
        repo_root=ROOT,
    )
    assert result.ok is True
    assert result.errors == ()
    assert set(result.commands_run) == set(DEFAULT_ISOLATED_VALIDATION_COMMANDS)
    assert result.results["recompute_checksums"]["ok"] is True
    assert result.results["patch_digest_check"]["patch_sha256"] == bundle.patch_sha256
    # Worktree is ephemeral (cleaned up by default) and not under the plugin.
    assert "plugins" not in result.work_dir
    # Validation work root exists under secure state; per-run dir is removed.
    work_root = state / "dreams" / "validation-work"
    assert work_root.is_dir()
    assert list(work_root.iterdir()) == []


def test_isolated_validation_rejects_disallowed_command(tmp_path):
    state = tmp_path / "state"
    bundle = _write_bundle(state)
    with pytest.raises(ValidationCommandError, match="disallowed_validation_command"):
        validate_quarantine_isolated(
            bundle.directory,
            commands=["rm -rf /", "recompute_checksums"],
            state_dir=state,
            repo_root=ROOT,
        )
    with pytest.raises(ValidationCommandError, match="disallowed_validation_command"):
        validate_quarantine_isolated(
            bundle.directory,
            commands=["python -c 'import os; os.system(\"id\")'"],
            state_dir=state,
            repo_root=ROOT,
        )
    with pytest.raises(ValidationCommandError, match="disallowed_validation_command"):
        assert_validation_commands_allowed(["shell:echo hi"])
    # Allowlist is a closed repository-owned set.
    assert "recompute_checksums" in ISOLATED_VALIDATION_COMMANDS
    assert "bash" not in ISOLATED_VALIDATION_COMMANDS


def test_isolated_validation_detects_checksum_tamper(tmp_path):
    state = tmp_path / "state"
    bundle = _write_bundle(state)
    # Tamper after write by rewriting a private copy we validate against.
    # (Immutable bundle on the original path is not mutated.)
    import shutil

    tampered = tmp_path / "tampered-bundle"
    shutil.copytree(bundle.directory, tampered)
    (tampered / ARTIFACT_FILENAME).write_text(
        "<!-- OVERLAY_SLOT:fail_soft_language BEGIN -->\ntampered\n"
        "<!-- OVERLAY_SLOT:fail_soft_language END -->\n",
        encoding="utf-8",
    )
    with pytest.raises(IsolatedValidationError, match="checksum_mismatch|isolated_validation_failed"):
        validate_quarantine_isolated(
            tampered,
            commands=("recompute_checksums",),
            state_dir=state,
            repo_root=ROOT,
        )
