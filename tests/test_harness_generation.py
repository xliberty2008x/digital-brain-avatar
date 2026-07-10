"""Harness generation digests, session pin stability, and record receipts."""

from __future__ import annotations

import json
import pathlib
import sys
from collections import defaultdict
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))

from digital_brain.maintenance.generation import (  # noqa: E402
    SESSION_ENV_GENERATION_ID,
    SESSION_ENV_PIN_PATH,
    collect_harness_generation,
    export_pin_to_claude_env_file,
    get_or_pin_session_generation,
    load_session_pin,
    pin_session_generation,
    resolve_session_binding,
    session_pin_path,
)
from digital_brain.maintenance.models import (  # noqa: E402
    EMPTY_DIGEST,
    HARNESS_SCHEMA_VERSION,
    TAXONOMY_VERSION,
    HarnessGeneration,
    build_harness_generation,
    compute_generation_id,
    generation_request_fingerprint,
)
from digital_brain_mcp_cypher.quality import (  # noqa: E402
    QualityStore,
    compute_harness_request_fingerprint,
    harness_identity_payload,
)


# ---------------------------------------------------------------------------
# Deterministic serialization / digests
# ---------------------------------------------------------------------------


def _sample(**overrides: Any) -> HarnessGeneration:
    base = dict(
        core_commit="abc123",
        core_tree_digest="tree456",
        dirty_state_digest=EMPTY_DIGEST,
        plugin_version="0.2.0",
        soul_sha="souldeadbeef",
        overlay_manifest_digest=EMPTY_DIGEST,
        policy_digest=EMPTY_DIGEST,
        mcp_version="0.1.0",
        model_id="test-model",
        schema_version=HARNESS_SCHEMA_VERSION,
        taxonomy_version=TAXONOMY_VERSION,
    )
    base.update(overrides)
    return build_harness_generation(**base)


def test_generation_id_is_deterministic_and_stable():
    a = _sample()
    b = _sample()
    assert a.id == b.id
    assert a.id.startswith("hg-")
    assert compute_generation_id(a) == a.id
    assert generation_request_fingerprint(a) == generation_request_fingerprint(b)
    # created_at must not affect identity
    with_ts = _sample(created_at="2026-07-10T00:00:00Z")
    assert with_ts.id == a.id


def test_canonical_public_dict_has_no_soul_content():
    gen = _sample()
    public = gen.to_public_dict()
    assert "soul_sha" in public
    assert "soul_content" not in public
    assert "soul_text" not in public
    assert "soul" not in public
    assert public["soul_sha"] == "souldeadbeef"
    # fingerprint payload also never includes content
    payload = gen.identity_payload()
    assert set(payload.keys()) == {
        "core_commit",
        "core_tree_digest",
        "dirty_state_digest",
        "mcp_version",
        "model_id",
        "overlay_manifest_digest",
        "plugin_version",
        "policy_digest",
        "schema_version",
        "soul_sha",
        "taxonomy_version",
    }


def test_each_meaningful_input_changes_generation_id():
    base = _sample()
    fields = {
        "core_commit": "other-commit",
        "core_tree_digest": "other-tree",
        "dirty_state_digest": "d" * 64,
        "plugin_version": "0.3.0",
        "soul_sha": "e" * 64,
        "overlay_manifest_digest": "f" * 64,
        "policy_digest": "a" * 64,
        "mcp_version": "9.9.9",
        "model_id": "other-model",
        "schema_version": "2",
        "taxonomy_version": "2",
    }
    for key, value in fields.items():
        mutated = _sample(**{key: value})
        assert mutated.id != base.id, f"{key} should change generation id"


def test_null_model_id_differs_from_set_model_id():
    with_model = _sample(model_id="m1")
    without = _sample(model_id=None)
    assert with_model.id != without.id


def test_soul_content_never_stored_only_digest(tmp_path: pathlib.Path):
    soul = tmp_path / "SOUL.MD"
    secret = "PRIVATE SOUL VOICE — never leave this machine as free text"
    soul.write_text(secret, encoding="utf-8")
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "version.json").write_text('"0.2.0"\n', encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()

    gen = collect_harness_generation(
        repo_root=repo,
        plugin_root=plugin,
        soul_path=soul,
        state_dir=tmp_path / "state",
        mcp_version="0.1.0",
        model_id=None,
        core_commit="deadbeef",
        core_tree_digest="tree",
        dirty_state_digest=EMPTY_DIGEST,
    )
    public = gen.to_public_dict()
    serialized = json.dumps(public)
    assert secret not in serialized
    assert "PRIVATE SOUL" not in serialized
    assert gen.soul_sha != EMPTY_DIGEST
    assert len(gen.soul_sha) == 64

    state_dir = tmp_path / "state"
    pin_path = pin_session_generation(
        gen, state_dir=state_dir, session_id="s1", export_env=False
    )
    pin_text = pin_path.read_text(encoding="utf-8")
    assert secret not in pin_text
    assert "PRIVATE SOUL" not in pin_text
    assert gen.soul_sha in pin_text
    # Active pin is id-only (no SOUL body) for dual-process MCP share.
    active_id = state_dir / "active" / "harness_generation.id"
    active_json = state_dir / "active" / "harness_generation.json"
    assert active_id.is_file()
    assert active_id.read_text(encoding="utf-8").strip() == gen.id
    active_payload = json.loads(active_json.read_text(encoding="utf-8"))
    assert active_payload["id"] == gen.id
    assert secret not in active_json.read_text(encoding="utf-8")
    assert "PRIVATE SOUL" not in active_json.read_text(encoding="utf-8")


def test_collect_is_deterministic_for_fixed_inputs(tmp_path: pathlib.Path):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "version.json").write_text('"0.2.0"\n', encoding="utf-8")
    kwargs = dict(
        repo_root=tmp_path,
        plugin_root=plugin,
        soul_path=tmp_path / "missing-soul",
        state_dir=tmp_path / "state",
        mcp_version="0.1.0",
        model_id="x",
        core_commit="c1",
        core_tree_digest="t1",
        dirty_state_digest=EMPTY_DIGEST,
        overlay_manifest_digest=EMPTY_DIGEST,
        policy_digest=EMPTY_DIGEST,
    )
    a = collect_harness_generation(**kwargs)
    b = collect_harness_generation(**kwargs)
    assert a.id == b.id
    assert a.soul_sha == EMPTY_DIGEST


# ---------------------------------------------------------------------------
# Session pin stability
# ---------------------------------------------------------------------------


def test_mid_session_file_change_does_not_change_pinned_id(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "version.json").write_text('"0.2.0"\n', encoding="utf-8")
    soul = tmp_path / "SOUL.MD"
    soul.write_text("voice v1", encoding="utf-8")

    first = get_or_pin_session_generation(
        state_dir=state,
        session_id="sess-a",
        repo_root=tmp_path,
        plugin_root=plugin,
        soul_path=soul,
        mcp_version="0.1.0",
        model_id=None,
        core_commit="c1",
        core_tree_digest="t1",
        dirty_state_digest=EMPTY_DIGEST,
    )
    pinned_id = first.id

    # Mid-session changes: SOUL, plugin version, policy, overlay.
    soul.write_text("voice v2 COMPLETELY DIFFERENT", encoding="utf-8")
    (plugin / "version.json").write_text('"0.9.9"\n', encoding="utf-8")
    policy = state / "dreams" / "active-policy" / "policy.json"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text('{"knob": 1}\n', encoding="utf-8")
    manifest = state / "dreams" / "active-overlays" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('{"overlays": ["x"]}\n', encoding="utf-8")

    second = get_or_pin_session_generation(
        state_dir=state,
        session_id="sess-a",
        repo_root=tmp_path,
        plugin_root=plugin,
        soul_path=soul,
        mcp_version="9.9.9",
        model_id="new-model",
        core_commit="CHANGED",
        core_tree_digest="CHANGED",
        dirty_state_digest="CHANGED",
    )
    assert second.id == pinned_id
    loaded = load_session_pin(state_dir=state, session_id="sess-a")
    assert loaded is not None
    assert loaded.id == pinned_id


def test_new_session_receives_new_generation_after_change(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "version.json").write_text('"0.2.0"\n', encoding="utf-8")
    soul = tmp_path / "SOUL.MD"
    soul.write_text("voice v1", encoding="utf-8")

    s1 = get_or_pin_session_generation(
        state_dir=state,
        session_id="session-1",
        repo_root=tmp_path,
        plugin_root=plugin,
        soul_path=soul,
        mcp_version="0.1.0",
        model_id=None,
        core_commit="c1",
        core_tree_digest="t1",
        dirty_state_digest=EMPTY_DIGEST,
    )

    soul.write_text("voice v2", encoding="utf-8")
    s2 = get_or_pin_session_generation(
        state_dir=state,
        session_id="session-2",
        force_new=True,
        repo_root=tmp_path,
        plugin_root=plugin,
        soul_path=soul,
        mcp_version="0.1.0",
        model_id=None,
        core_commit="c1",
        core_tree_digest="t1",
        dirty_state_digest=EMPTY_DIGEST,
    )
    assert s2.id != s1.id
    # Original session still pinned to old id
    still = load_session_pin(state_dir=state, session_id="session-1")
    assert still is not None
    assert still.id == s1.id


def test_session_pin_path_is_under_state_dir(tmp_path: pathlib.Path):
    path = session_pin_path(tmp_path, "abc")
    assert path == tmp_path / "sessions" / "abc" / "harness_generation.json"


# ---------------------------------------------------------------------------
# Quality-plane record: replay / conflict / readback (mocked Neo4j session)
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row

    def single(self):
        return self.row

    def consume(self) -> None:
        return None


class _ConstraintError(Exception):
    """Mimic neo4j.exceptions.ConstraintError for uniqueness races."""

    def __init__(self, message: str = "ConstraintValidationFailed: already exists"):
        super().__init__(message)
        self.code = "Neo.ClientError.Schema.ConstraintValidationFailed"


class _FakeSession:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_next_create: bool = False

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
        if "MATCH (g:Operational:HarnessGeneration {id:" in q and "RETURN g.id AS id" in q:
            gid = params.get("generation_id")
            node = self.nodes.get(gid)  # type: ignore[arg-type]
            return _Result(None if node is None else dict(node))
        if q.strip().startswith("CREATE (g:Operational:HarnessGeneration)"):
            props = dict(params["props"])
            if self.fail_next_create or props["id"] in self.nodes:
                self.fail_next_create = False
                # Simulate concurrent create winning the uniqueness race.
                if props["id"] not in self.nodes:
                    self.nodes[props["id"]] = props
                raise _ConstraintError()
            self.nodes[props["id"]] = props
            return _Result(
                {
                    "id": props["id"],
                    "request_fingerprint": props["request_fingerprint"],
                    "created_at": props.get("created_at"),
                    "soul_sha": props["soul_sha"],
                    "plugin_version": props["plugin_version"],
                    "core_commit": props["core_commit"],
                    "schema_version": props["schema_version"],
                    "taxonomy_version": props["taxonomy_version"],
                }
            )
        if "MATCH (r:Operational:EffectReceipt" in q:
            return _Result(None)
        if "RETURN 1 AS ok" in q:
            return _Result({"ok": 1})
        raise AssertionError(f"unexpected query: {query}")


class _FakeDriver:
    def __init__(self, session: _FakeSession):
        self._session = session

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def session(self, database: str = "neo4j"):
        return self


class _SessionCtx:
    def __init__(self, session: _FakeSession):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *args):
        return False


def _store_with(session: _FakeSession) -> QualityStore:
    def factory():
        driver = _FakeDriver(session)

        class _Wrapped:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def session(self_inner, database: str = "neo4j"):
                return _SessionCtx(session)

        return _Wrapped()

    return QualityStore(factory, "neo4j")


def test_server_fingerprint_matches_client_models():
    gen = _sample()
    client_fp = generation_request_fingerprint(gen)
    server_fp = compute_harness_request_fingerprint(
        harness_identity_payload(
            core_commit=gen.core_commit,
            core_tree_digest=gen.core_tree_digest,
            dirty_state_digest=gen.dirty_state_digest,
            plugin_version=gen.plugin_version,
            soul_sha=gen.soul_sha,
            overlay_manifest_digest=gen.overlay_manifest_digest,
            policy_digest=gen.policy_digest,
            mcp_version=gen.mcp_version,
            model_id=gen.model_id,
            schema_version=gen.schema_version,
            taxonomy_version=gen.taxonomy_version,
        )
    )
    assert client_fp == server_fp
    assert gen.id == f"hg-{server_fp}"


def test_record_harness_generation_created_replay_conflict():
    session = _FakeSession()
    store = _store_with(session)
    gen = _sample()
    params = gen.to_record_params()
    params["created_at"] = None

    created = store.record_harness_generation(params)
    assert created["outcome"] == "created"
    assert created["generation_id"] == gen.id
    assert "soul_content" not in created
    assert created["soul_sha"] == gen.soul_sha

    replayed = store.record_harness_generation(params)
    assert replayed["outcome"] == "replayed"
    assert replayed["generation_id"] == gen.id

    # Corrupt stored fingerprint to exercise conflict (integrity rejects client
    # mismatches before write; conflict is for pre-existing divergent nodes).
    session.nodes[gen.id]["request_fingerprint"] = "different-fingerprint"
    conflict = store.record_harness_generation(params)
    assert conflict["outcome"] == "conflict"
    assert conflict["reason"] == "generation_id_reused"


def test_record_rejects_mismatched_fingerprint_or_id():
    session = _FakeSession()
    store = _store_with(session)
    gen = _sample()
    params = gen.to_record_params()

    bad_fp = dict(params)
    bad_fp["request_fingerprint"] = "0" * 64
    with pytest.raises(ValueError, match="request_fingerprint does not match"):
        store.record_harness_generation(bad_fp)

    bad_id = dict(params)
    bad_id["id"] = "hg-" + ("0" * 64)
    with pytest.raises(ValueError, match="generation.id must equal"):
        store.record_harness_generation(bad_id)


def test_record_uniqueness_race_maps_to_replay():
    session = _FakeSession()
    store = _store_with(session)
    gen = _sample()
    params = gen.to_record_params()
    params["created_at"] = None
    session.fail_next_create = True
    outcome = store.record_harness_generation(params)
    assert outcome["outcome"] == "replayed"
    assert outcome["generation_id"] == gen.id


def test_record_rejects_soul_content_fields():
    session = _FakeSession()
    store = _store_with(session)
    gen = _sample()
    params = gen.to_record_params()
    params["soul_content"] = "LEAK"
    with pytest.raises(ValueError, match="SOUL content"):
        store.record_harness_generation(params)


def test_get_harness_generation_readback():
    session = _FakeSession()
    store = _store_with(session)
    gen = _sample()
    params = gen.to_record_params()
    store.record_harness_generation(params)

    found = store.get_harness_generation(gen.id)
    assert found["outcome"] == "ok"
    assert found["id"] == gen.id
    assert found["soul_sha"] == gen.soul_sha
    assert "soul_content" not in found

    missing = store.get_harness_generation("hg-missing")
    assert missing["outcome"] == "not_found"


def test_mcp_tools_registered_for_harness_generation():
    pytest.importorskip("fastmcp")
    pytest.importorskip("neo4j")
    from digital_brain_mcp_cypher import server

    import asyncio

    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert "record_harness_generation" in names
    assert "get_harness_generation" in names


def test_mcp_client_rejects_soul_content():
    import asyncio
    from digital_brain.tools.mcp_client import record_harness_generation

    gen = _sample().to_record_params()
    gen["soul_content"] = "nope"

    async def _run():
        with pytest.raises(ValueError, match="SOUL content"):
            await record_harness_generation(gen)

    asyncio.run(_run())


def test_resolve_session_binding_sources():
    sid, force = resolve_session_binding(
        env_session_id="env-sess",
        hook_session_id="hook-sess",
        hook_source="startup",
    )
    assert sid == "env-sess"
    assert force is True

    sid, force = resolve_session_binding(
        env_session_id=None,
        hook_session_id="hook-uuid-1234",
        hook_source="resume",
    )
    assert sid == "hook-uuid-1234"
    assert force is False

    sid, force = resolve_session_binding(
        env_session_id=None,
        hook_session_id="hook-uuid-1234",
        hook_source="clear",
    )
    assert force is True

    sid, force = resolve_session_binding(
        env_session_id=None,
        hook_session_id=None,
        hook_source=None,
    )
    assert sid.startswith("local-")
    assert force is True  # ephemeral


def test_export_pin_to_claude_env_file(tmp_path: pathlib.Path):
    env_file = tmp_path / "claude.env"
    pin = tmp_path / "sessions" / "s1" / "harness_generation.json"
    export_pin_to_claude_env_file("hg-abc", pin, env_file=env_file)
    text = env_file.read_text(encoding="utf-8")
    assert f"export {SESSION_ENV_GENERATION_ID}='hg-abc'" in text
    assert f"export {SESSION_ENV_PIN_PATH}='{pin}'" in text


def test_overlay_manifest_digest_changes_generation_and_session_pin_stable(
    tmp_path: pathlib.Path,
):
    """Active-overlay manifest is part of harness identity; session pin holds."""
    from digital_brain.maintenance.active_overlays import (
        ActiveManifest,
        ActiveOverlayEntry,
        atomic_replace_manifest,
        pin_session_active_overlays,
        stage_overlay_content,
    )
    from digital_brain.maintenance.models import digest_text

    state = tmp_path / "state"
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "version.json").write_text('"0.2.0"\n', encoding="utf-8")
    soul = tmp_path / "SOUL.MD"
    soul.write_text("voice v1", encoding="utf-8")

    g1 = get_or_pin_session_generation(
        state_dir=state,
        session_id="sess-ov",
        repo_root=tmp_path,
        plugin_root=plugin,
        soul_path=soul,
        mcp_version="0.1.0",
        model_id=None,
        core_commit="c1",
        core_tree_digest="t1",
        dirty_state_digest=EMPTY_DIGEST,
    )
    assert g1.overlay_manifest_digest == EMPTY_DIGEST

    content = (
        "<!-- OVERLAY_SLOT:fail_soft_language BEGIN -->\n"
        "### Rule `r1`\n"
        "<!-- OVERLAY_SLOT:fail_soft_language END -->\n"
    )
    _path, digest = stage_overlay_content(
        state_dir=state, proposal_id="prop-1", content=content
    )
    entry = ActiveOverlayEntry(
        proposal_id="prop-1",
        digest=digest,
        rule_id="r1",
        extension_slot="fail_soft_language",
        target_skill="digital-brain-buddy-session",
        target_file="skills/digital-brain-buddy-session/SKILL.md",
        trial_expires_at="2099-01-01T00:00:00Z",
        exposure_budget=10,
        rollback_generation="hg-prior",
        status="trial_active",
        base_commit="c1",
        artifact_hash=digest,
    )
    atomic_replace_manifest(
        state_dir=state,
        manifest=ActiveManifest(
            schema_version="1",
            entries=(entry,),
            prior_manifest_digest=EMPTY_DIGEST,
            rollback_generation="hg-prior",
            created_at="2026-07-10T12:00:00Z",
            generation_counter=1,
        ),
    )

    # Existing session pin unchanged after mid-session overlay activation.
    still = get_or_pin_session_generation(
        state_dir=state,
        session_id="sess-ov",
        repo_root=tmp_path,
        plugin_root=plugin,
        soul_path=soul,
        mcp_version="0.1.0",
        model_id=None,
        core_commit="c1",
        core_tree_digest="t1",
        dirty_state_digest=EMPTY_DIGEST,
    )
    assert still.id == g1.id

    # Overlay session pin is independent and stable.
    ov_pin = pin_session_active_overlays(state_dir=state, session_id="sess-ov")
    assert len(ov_pin.entries) == 1
    assert ov_pin.entries[0].digest == digest

    # New session recollects and includes new overlay_manifest_digest.
    g2 = get_or_pin_session_generation(
        state_dir=state,
        session_id="sess-ov-2",
        force_new=True,
        repo_root=tmp_path,
        plugin_root=plugin,
        soul_path=soul,
        mcp_version="0.1.0",
        model_id=None,
        core_commit="c1",
        core_tree_digest="t1",
        dirty_state_digest=EMPTY_DIGEST,
    )
    assert g2.id != g1.id
    assert g2.overlay_manifest_digest != EMPTY_DIGEST
    from digital_brain.maintenance.models import digest_bytes

    manifest_bytes = (
        state / "dreams" / "active-overlays" / "manifest.json"
    ).read_bytes()
    assert g2.overlay_manifest_digest == digest_bytes(manifest_bytes)


def test_fail_closed_overlay_does_not_open_on_mismatch(tmp_path: pathlib.Path):
    from digital_brain.maintenance.active_overlays import (
        ActiveManifest,
        ActiveOverlayEntry,
        atomic_replace_manifest,
        load_validated_active_overlays,
        resolve_loadable_overlays,
        stage_overlay_content,
    )

    state = tmp_path / "state"
    content = "### Rule `r1`\n"
    path, digest = stage_overlay_content(
        state_dir=state, proposal_id="prop-1", content=content
    )
    atomic_replace_manifest(
        state_dir=state,
        manifest=ActiveManifest(
            schema_version="1",
            entries=(
                ActiveOverlayEntry(
                    proposal_id="prop-1",
                    digest=digest,
                    rule_id="r1",
                    extension_slot="fail_soft_language",
                    target_skill="digital-brain-buddy-session",
                    target_file="skills/digital-brain-buddy-session/SKILL.md",
                    trial_expires_at="2099-01-01T00:00:00Z",
                    exposure_budget=5,
                    rollback_generation="hg-prior",
                    status="trial_active",
                    artifact_hash=digest,
                ),
            ),
            prior_manifest_digest=EMPTY_DIGEST,
            rollback_generation="hg-prior",
            created_at="2026-07-10T12:00:00Z",
            generation_counter=1,
        ),
    )
    path.write_text(content + "TAMPER", encoding="utf-8")
    closed = load_validated_active_overlays(state_dir=state)
    assert closed.fail_closed is True
    assert resolve_loadable_overlays(state_dir=state) == []


def test_pin_script_public_summary_has_no_soul_body(tmp_path: pathlib.Path, monkeypatch):
    """scripts/pin_harness_generation.py summary path (offline, skip-record)."""
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "version.json").write_text('"0.2.0"\n', encoding="utf-8")
    soul = tmp_path / "SOUL.MD"
    secret = "SECRET_SOUL_BODY_TEXT_XYZ"
    soul.write_text(secret, encoding="utf-8")
    state = tmp_path / "state"
    claude_env = tmp_path / "claude_env_file.sh"

    monkeypatch.setenv("DIGITAL_BRAIN_STATE_DIR", str(state))
    monkeypatch.setenv("CLAUDE_ENV_FILE", str(claude_env))
    monkeypatch.delenv("DIGITAL_BRAIN_SESSION_ID", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pin_harness_generation.py",
            "--repo-root",
            str(tmp_path),
            "--plugin-root",
            str(plugin),
            "--soul-path",
            str(soul),
            "--state-dir",
            str(state),
            "--session-id",
            "unit",
            "--skip-record",
            "--json",
            "--force-new",
        ],
    )
    from scripts import pin_harness_generation as pin_mod

    fixed = _sample(soul_sha="ab" * 32)
    monkeypatch.setattr(
        pin_mod,
        "collect_harness_generation",
        lambda **kwargs: fixed,
    )
    monkeypatch.setattr(
        pin_mod,
        "get_or_pin_session_generation",
        lambda **kwargs: fixed,
    )

    code = pin_mod.main()
    assert code == 0

    pin = load_session_pin(state_dir=state, session_id="unit")
    assert pin is not None
    assert pin.id == fixed.id

    pin_file = session_pin_path(state, "unit")
    assert pin_file.is_file()
    pin_text = pin_file.read_text(encoding="utf-8")
    assert secret not in pin_text
    assert fixed.soul_sha in pin_text

    env_file = pin_file.parent / "harness_generation.env"
    assert env_file.is_file()
    env_text = env_file.read_text(encoding="utf-8")
    assert f"{SESSION_ENV_GENERATION_ID}={fixed.id}" in env_text
    assert f"{SESSION_ENV_PIN_PATH}={pin_file}" in env_text

    claude_text = claude_env.read_text(encoding="utf-8")
    assert f"export {SESSION_ENV_GENERATION_ID}='{fixed.id}'" in claude_text
    assert f"export {SESSION_ENV_PIN_PATH}='{pin_file}'" in claude_text


def test_pin_script_force_new_and_new_session_get_distinct_ids(
    tmp_path: pathlib.Path, monkeypatch
):
    """Production-like session ids: force-new recollects; new session is distinct."""
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "version.json").write_text('"0.2.0"\n', encoding="utf-8")
    soul = tmp_path / "SOUL.MD"
    soul.write_text("voice-a", encoding="utf-8")
    state = tmp_path / "state"

    from scripts import pin_harness_generation as pin_mod

    call_n = {"n": 0}

    def _collect(**kwargs: Any) -> HarnessGeneration:
        call_n["n"] += 1
        # Distinct soul digest per collect so force-new / new session change id.
        return _sample(soul_sha=f"{call_n['n']:02d}" * 32)

    monkeypatch.setattr(pin_mod, "collect_harness_generation", _collect)
    monkeypatch.setenv("DIGITAL_BRAIN_STATE_DIR", str(state))
    monkeypatch.delenv("CLAUDE_ENV_FILE", raising=False)

    def _run(session_id: str, *extra: str) -> dict[str, Any]:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "pin_harness_generation.py",
                "--repo-root",
                str(tmp_path),
                "--plugin-root",
                str(plugin),
                "--soul-path",
                str(soul),
                "--state-dir",
                str(state),
                "--session-id",
                session_id,
                "--skip-record",
                "--json",
                *extra,
            ],
        )
        # Capture stdout JSON
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            assert pin_mod.main() == 0
        return json.loads(buf.getvalue())

    first = _run("prod-session-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    second_same = _run("prod-session-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert first["generation_id"] == second_same["generation_id"]
    assert first["session_id"] == "prod-session-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    forced = _run(
        "prod-session-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "--force-new",
        "--hook-source",
        "startup",
    )
    assert forced["generation_id"] != first["generation_id"]

    other = _run("prod-session-ffffffffffff-1111-2222-3333-444444444444")
    assert other["generation_id"] != forced["generation_id"]
    assert other["session_id"] == "prod-session-ffffffffffff-1111-2222-3333-444444444444"

    # Original session pin file still holds the force-new id (overwrite).
    loaded = load_session_pin(
        state_dir=state,
        session_id="prod-session-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )
    assert loaded is not None
    assert loaded.id == forced["generation_id"]
