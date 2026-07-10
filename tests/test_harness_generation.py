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
    collect_harness_generation,
    get_or_pin_session_generation,
    load_session_pin,
    pin_session_generation,
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
from digital_brain_mcp_cypher.quality import QualityStore  # noqa: E402


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

    pin_path = pin_session_generation(
        gen, state_dir=tmp_path / "state", session_id="s1", export_env=False
    )
    pin_text = pin_path.read_text(encoding="utf-8")
    assert secret not in pin_text
    assert "PRIVATE SOUL" not in pin_text
    assert gen.soul_sha in pin_text


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


class _FakeSession:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

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

    conflict_params = dict(params)
    conflict_params["request_fingerprint"] = "different-fingerprint"
    conflict = store.record_harness_generation(conflict_params)
    assert conflict["outcome"] == "conflict"
    assert conflict["reason"] == "generation_id_reused"


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


def test_pin_script_public_summary_has_no_soul_body(tmp_path: pathlib.Path, monkeypatch):
    """scripts/pin_harness_generation.py summary path (offline, skip-record)."""
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "version.json").write_text('"0.2.0"\n', encoding="utf-8")
    soul = tmp_path / "SOUL.MD"
    secret = "SECRET_SOUL_BODY_TEXT_XYZ"
    soul.write_text(secret, encoding="utf-8")
    state = tmp_path / "state"

    monkeypatch.setenv("DIGITAL_BRAIN_STATE_DIR", str(state))
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
    # Override git-less fixed collect via env not needed — pin script uses collect.
    from scripts import pin_harness_generation as pin_mod

    # Force deterministic commit inputs by monkeypatching collect
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
    # force-new path uses pin_session_generation with collect result
    assert pin is not None or session_pin_path(state, "unit").is_file()
    pin_file = session_pin_path(state, "unit")
    if pin_file.is_file():
        assert secret not in pin_file.read_text(encoding="utf-8")
