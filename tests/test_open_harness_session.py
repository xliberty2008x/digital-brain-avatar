"""Host-agnostic open_harness_session + resolve_handle_for_chat contracts."""

from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from digital_brain.maintenance.generation import (  # noqa: E402
    SESSION_ENV_GENERATION_ID,
    SESSION_ENV_PIN_PATH,
    write_active_harness_pin,
)
from digital_brain.maintenance.models import EMPTY_DIGEST  # noqa: E402
from digital_brain.maintenance.session import (  # noqa: E402
    SESSION_ENV_SESSION_ID,
    SESSION_HANDLE_SCHEMA_VERSION,
    SessionHandle,
    assert_no_soul_in_handle_payload,
    handle_from_public_dict,
    load_active_pin_meta,
    new_host_session_id,
    normalize_host,
    open_harness_session,
    resolve_handle_for_chat,
)


@pytest.fixture(autouse=True)
def _clean_session_env():
    """Clear session env without monkeypatch restore (raw os.environ sets)."""
    keys = (
        SESSION_ENV_SESSION_ID,
        SESSION_ENV_GENERATION_ID,
        SESSION_ENV_PIN_PATH,
        "DIGITAL_BRAIN_HOST",
    )
    for key in keys:
        os.environ.pop(key, None)
    yield
    for key in keys:
        os.environ.pop(key, None)


def _plugin(tmp_path: pathlib.Path) -> pathlib.Path:
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "version.json").write_text('"0.3.0"\n', encoding="utf-8")
    return plugin


def _collect_kwargs(tmp_path: pathlib.Path, plugin: pathlib.Path) -> dict:
    soul = tmp_path / "SOUL.MD"
    soul.write_text("voice test — private", encoding="utf-8")
    return dict(
        repo_root=tmp_path,
        plugin_root=plugin,
        soul_path=soul,
        collect_kwargs=dict(
            mcp_version="0.1.0",
            model_id=None,
            core_commit="c1",
            core_tree_digest="t1",
            dirty_state_digest=EMPTY_DIGEST,
            overlay_manifest_digest=EMPTY_DIGEST,
            policy_digest=EMPTY_DIGEST,
        ),
    )


def test_normalize_host():
    assert normalize_host("Grok") == "grok"
    assert normalize_host(None) == "unknown"
    assert normalize_host("claude code") == "claude-code"


def test_new_host_session_id_prefixed():
    sid = new_host_session_id("grok")
    assert sid.startswith("grok-")
    assert "local-" not in sid


def test_open_creates_session_pin_and_handle(tmp_path: pathlib.Path, monkeypatch):
    monkeypatch.delenv(SESSION_ENV_SESSION_ID, raising=False)
    monkeypatch.delenv(SESSION_ENV_GENERATION_ID, raising=False)
    state = tmp_path / "state"
    plugin = _plugin(tmp_path)
    kwargs = _collect_kwargs(tmp_path, plugin)

    handle = open_harness_session(
        session_id="grok-chat-1",
        host="grok",
        force_new=True,
        state_dir=state,
        skip_record=True,
        **kwargs,
    )
    assert handle.schema_version == SESSION_HANDLE_SCHEMA_VERSION
    assert handle.session_id == "grok-chat-1"
    assert handle.harness_generation_id.startswith("hg-")
    assert handle.mode == "opened"
    assert handle.host == "grok"
    assert handle.force_new is True
    assert handle.record_outcome == "skipped"
    assert pathlib.Path(handle.pin_path).is_file()
    pin = json.loads(pathlib.Path(handle.pin_path).read_text(encoding="utf-8"))
    assert pin["id"] == handle.harness_generation_id
    assert "soul_content" not in pin
    assert "PRIVATE" not in pathlib.Path(handle.pin_path).read_text(encoding="utf-8")
    # active breadcrumb refreshed with this session_id
    active = load_active_pin_meta(state)
    assert active is not None
    assert active["id"] == handle.harness_generation_id
    assert active["session_id"] == "grok-chat-1"
    # env exported
    assert os.environ[SESSION_ENV_SESSION_ID] == "grok-chat-1"
    assert os.environ[SESSION_ENV_GENERATION_ID] == handle.harness_generation_id


def test_resume_same_session_keeps_generation_id(tmp_path: pathlib.Path, monkeypatch):
    monkeypatch.delenv(SESSION_ENV_SESSION_ID, raising=False)
    state = tmp_path / "state"
    plugin = _plugin(tmp_path)
    kwargs = _collect_kwargs(tmp_path, plugin)

    first = open_harness_session(
        session_id="sess-resume",
        host="codex",
        force_new=True,
        state_dir=state,
        **kwargs,
    )
    # Mid-session SOUL change must not alter pin on resume
    soul = tmp_path / "SOUL.MD"
    soul.write_text("completely different voice", encoding="utf-8")

    second = open_harness_session(
        session_id="sess-resume",
        host="codex",
        force_new=False,
        state_dir=state,
        **kwargs,
    )
    assert second.harness_generation_id == first.harness_generation_id
    assert second.mode == "resumed"
    assert second.force_new is False


def test_force_new_recollects(tmp_path: pathlib.Path, monkeypatch):
    monkeypatch.delenv(SESSION_ENV_SESSION_ID, raising=False)
    state = tmp_path / "state"
    plugin = _plugin(tmp_path)
    kwargs = _collect_kwargs(tmp_path, plugin)

    first = open_harness_session(
        session_id="sess-recollect",
        host="claude",
        force_new=True,
        state_dir=state,
        **kwargs,
    )
    soul = tmp_path / "SOUL.MD"
    soul.write_text("voice after clear", encoding="utf-8")
    second = open_harness_session(
        session_id="sess-recollect",
        host="claude",
        force_new=True,
        state_dir=state,
        **kwargs,
    )
    assert second.mode == "recollected"
    assert second.harness_generation_id != first.harness_generation_id


def test_ephemeral_open_without_session_id(tmp_path: pathlib.Path, monkeypatch):
    monkeypatch.delenv(SESSION_ENV_SESSION_ID, raising=False)
    monkeypatch.delenv(SESSION_ENV_GENERATION_ID, raising=False)
    state = tmp_path / "state"
    plugin = _plugin(tmp_path)
    kwargs = _collect_kwargs(tmp_path, plugin)

    handle = open_harness_session(
        host="grok",
        state_dir=state,
        **kwargs,
    )
    assert handle.session_id.startswith("grok-")
    assert handle.mode == "opened"
    assert pathlib.Path(handle.pin_path).is_file()


def test_resolve_never_uses_active_alone(tmp_path: pathlib.Path, monkeypatch):
    """Leftover verify active/ pin is not a chat ticket."""
    monkeypatch.delenv(SESSION_ENV_SESSION_ID, raising=False)
    monkeypatch.delenv(SESSION_ENV_GENERATION_ID, raising=False)
    monkeypatch.delenv(SESSION_ENV_PIN_PATH, raising=False)
    state = tmp_path / "state"
    # Poison active/ as if from milestone-b-verify
    write_active_harness_pin(
        "hg-verify-only-deadbeef",
        state_dir=state,
        session_id="milestone-b-verify-1783677850",
    )
    meta = load_active_pin_meta(state)
    assert meta is not None
    assert meta["id"] == "hg-verify-only-deadbeef"

    resolved = resolve_handle_for_chat(state_dir=state, open_if_missing=False)
    assert resolved is None

    # With open_if_missing we mint OUR session, not the verify one
    plugin = _plugin(tmp_path)
    kwargs = _collect_kwargs(tmp_path, plugin)
    opened = resolve_handle_for_chat(
        state_dir=state,
        host="grok",
        open_if_missing=True,
        force_new=True,
        **kwargs,
    )
    assert opened is not None
    assert opened.session_id != "milestone-b-verify-1783677850"
    assert opened.harness_generation_id != "hg-verify-only-deadbeef"
    assert opened.session_id.startswith("grok-")


def test_resolve_loads_session_pin(tmp_path: pathlib.Path, monkeypatch):
    monkeypatch.delenv(SESSION_ENV_GENERATION_ID, raising=False)
    state = tmp_path / "state"
    plugin = _plugin(tmp_path)
    kwargs = _collect_kwargs(tmp_path, plugin)
    opened = open_harness_session(
        session_id="known-sess",
        host="grok",
        force_new=True,
        state_dir=state,
        export_env=False,
        **kwargs,
    )
    monkeypatch.delenv(SESSION_ENV_SESSION_ID, raising=False)
    monkeypatch.delenv(SESSION_ENV_GENERATION_ID, raising=False)

    resolved = resolve_handle_for_chat(
        session_id="known-sess",
        state_dir=state,
        open_if_missing=False,
    )
    assert resolved is not None
    assert resolved.harness_generation_id == opened.harness_generation_id
    assert resolved.mode == "resumed"


def test_handle_public_dict_no_soul_and_legacy_alias(tmp_path: pathlib.Path, monkeypatch):
    monkeypatch.delenv(SESSION_ENV_SESSION_ID, raising=False)
    state = tmp_path / "state"
    plugin = _plugin(tmp_path)
    kwargs = _collect_kwargs(tmp_path, plugin)
    handle = open_harness_session(
        session_id="json-sess",
        host="grok",
        force_new=True,
        state_dir=state,
        **kwargs,
    )
    public = handle.to_public_dict()
    assert_no_soul_in_handle_payload(public)
    assert public["harness_generation_id"] == handle.harness_generation_id
    assert public["generation_id"] == handle.harness_generation_id
    assert "soul_content" not in public
    roundtrip = handle_from_public_dict(public)
    assert roundtrip.harness_generation_id == handle.harness_generation_id
    assert roundtrip.session_id == handle.session_id


def test_two_hosts_two_sessions_distinct(tmp_path: pathlib.Path, monkeypatch):
    monkeypatch.delenv(SESSION_ENV_SESSION_ID, raising=False)
    state = tmp_path / "state"
    plugin = _plugin(tmp_path)
    kwargs = _collect_kwargs(tmp_path, plugin)
    a = open_harness_session(
        session_id="claude-uuid-1",
        host="claude",
        force_new=True,
        state_dir=state,
        **kwargs,
    )
    b = open_harness_session(
        session_id="grok-chat-9",
        host="grok",
        force_new=True,
        state_dir=state,
        **kwargs,
    )
    assert a.session_id != b.session_id
    # Same inputs → same generation id is OK; sessions are still distinct pins
    assert pathlib.Path(a.pin_path) != pathlib.Path(b.pin_path)
    assert pathlib.Path(a.pin_path).is_file()
    assert pathlib.Path(b.pin_path).is_file()


def test_session_env_file_includes_session_id(tmp_path: pathlib.Path, monkeypatch):
    monkeypatch.delenv(SESSION_ENV_SESSION_ID, raising=False)
    state = tmp_path / "state"
    plugin = _plugin(tmp_path)
    kwargs = _collect_kwargs(tmp_path, plugin)
    handle = open_harness_session(
        session_id="env-file-sess",
        host="codex",
        force_new=True,
        state_dir=state,
        **kwargs,
    )
    env_path = pathlib.Path(handle.pin_path).parent / "harness_generation.env"
    text = env_path.read_text(encoding="utf-8")
    assert f"{SESSION_ENV_SESSION_ID}=env-file-sess" in text
    assert SESSION_ENV_GENERATION_ID in text
