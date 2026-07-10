"""Typed Feedback / RunEvent quality sensors: validation, replay/conflict, isolation."""

from __future__ import annotations

import ast
import json
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))

from digital_brain_mcp_cypher.quality import (  # noqa: E402
    DETERMINISTIC_OUTCOME_SOURCES,
    FEEDBACK_KINDS,
    MAX_RAW_PAYLOAD_LEN,
    MAX_REF_COUNT,
    MAX_SUMMARY_LEN,
    QualityStore,
    build_tool_outcome_run_event,
    compute_raw_hmac,
    compute_sensor_request_fingerprint,
    feedback_identity_payload,
    mint_tool_outcome_event_id,
    resolve_session_harness_generation_id,
    try_record_tool_outcome_run_event,
)


GENERATION_ID = "hg-" + ("a" * 64)


# ---------------------------------------------------------------------------
# Fake Neo4j session for QualityStore unit tests
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row

    def single(self):
        return self.row

    def consume(self) -> None:
        return None


class _ConstraintError(Exception):
    def __init__(self, message: str = "ConstraintValidationFailed: already exists"):
        super().__init__(message)
        self.code = "Neo.ClientError.Schema.ConstraintValidationFailed"


class _FakeSession:
    def __init__(self) -> None:
        self.feedback: dict[str, dict[str, Any]] = {}
        self.payloads: dict[str, dict[str, Any]] = {}
        self.lifecycle: dict[str, dict[str, Any]] = {}
        self.run_events: dict[str, dict[str, Any]] = {}
        self.effects: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_next_create: bool = False
        self.created_journal: bool = False
        self.embedding_calls: int = 0

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

        if "MATCH (r:Operational:EffectReceipt {id:" in q:
            rid = params.get("receipt_id")
            node = self.effects.get(rid)  # type: ignore[arg-type]
            return _Result(None if node is None else dict(node))

        if "MATCH (f:Operational:Feedback {id:" in q and "RETURN f.id AS receipt_id" in q:
            rid = params.get("receipt_id")
            node = self.feedback.get(rid)  # type: ignore[arg-type]
            if node is None:
                return _Result(None)
            return _Result(
                {
                    "receipt_id": node["id"],
                    "request_fingerprint": node["request_fingerprint"],
                    "kind": node.get("kind"),
                    "sensitivity": node.get("sensitivity"),
                    "harness_generation_id": node.get("harness_generation_id"),
                    "raw_payload_ref": node.get("raw_payload_ref"),
                    "created_at": node.get("created_at"),
                }
            )

        if "MATCH (e:Operational:RunEvent {id:" in q and "RETURN e.id AS receipt_id" in q:
            rid = params.get("receipt_id")
            node = self.run_events.get(rid)  # type: ignore[arg-type]
            if node is None:
                return _Result(None)
            return _Result(
                {
                    "receipt_id": node["id"],
                    "request_fingerprint": node["request_fingerprint"],
                    "route": node.get("route"),
                    "tool": node.get("tool"),
                    "tool_outcome": node.get("tool_outcome"),
                    "outcome_source": node.get("outcome_source"),
                    "harness_generation_id": node.get("harness_generation_id"),
                    "observed_at": node.get("observed_at"),
                    "ingested_at": node.get("ingested_at"),
                }
            )

        if "MATCH (l:Operational:FeedbackLifecycleEvent {id:" in q and "receipt_id" in q:
            rid = params.get("receipt_id")
            node = self.lifecycle.get(rid)  # type: ignore[arg-type]
            if node is None:
                return _Result(None)
            return _Result(
                {
                    "receipt_id": node["id"],
                    "request_fingerprint": node["request_fingerprint"],
                    "feedback_id": node.get("feedback_id"),
                    "event": node.get("event"),
                    "actor": node.get("actor"),
                    "created_at": node.get("created_at"),
                }
            )

        if "MATCH (f:Operational:Feedback {id:" in q and "RETURN f.id AS id" in q:
            fid = params.get("feedback_id")
            node = self.feedback.get(fid)  # type: ignore[arg-type]
            return _Result(None if node is None else dict(node))

        if "MATCH (l:Operational:FeedbackLifecycleEvent {id:" in q and "lifecycle_id" in str(
            params
        ):
            lid = params.get("lifecycle_id")
            node = self.lifecycle.get(lid)  # type: ignore[arg-type]
            return _Result(None if node is None else dict(node))

        if "MATCH (e:Operational:RunEvent {id:" in q and "event_id" in params:
            eid = params.get("event_id")
            node = self.run_events.get(eid)  # type: ignore[arg-type]
            return _Result(None if node is None else dict(node))

        if "CREATE (f:Operational:Feedback)" in q:
            props = dict(params["props"])
            if self.fail_next_create or props["id"] in self.feedback:
                self.fail_next_create = False
                if props["id"] not in self.feedback:
                    self.feedback[props["id"]] = props
                raise _ConstraintError()
            self.feedback[props["id"]] = props
            return _Result(
                {
                    "id": props["id"],
                    "request_fingerprint": props["request_fingerprint"],
                    "kind": props["kind"],
                    "sensitivity": props["sensitivity"],
                    "harness_generation_id": props["harness_generation_id"],
                    "raw_payload_ref": props.get("raw_payload_ref"),
                    "created_at": props.get("created_at"),
                }
            )

        if "CREATE (p:Operational:QualityPayload)" in q:
            payload_props = dict(params["payload_props"])
            self.payloads[payload_props["id"]] = payload_props
            return _Result(None)

        if "CREATE (l:Operational:FeedbackLifecycleEvent)" in q:
            props = dict(params["props"])
            if self.fail_next_create or props["id"] in self.lifecycle:
                self.fail_next_create = False
                if props["id"] not in self.lifecycle:
                    self.lifecycle[props["id"]] = props
                raise _ConstraintError()
            if params.get("feedback_id") not in self.feedback:
                return _Result(None)
            self.lifecycle[props["id"]] = props
            return _Result(
                {
                    "id": props["id"],
                    "feedback_id": props["feedback_id"],
                    "event": props["event"],
                    "actor": props["actor"],
                    "request_fingerprint": props["request_fingerprint"],
                    "created_at": props.get("created_at"),
                }
            )

        if "CREATE (e:Operational:RunEvent)" in q:
            props = dict(params["props"])
            if self.fail_next_create or props["id"] in self.run_events:
                self.fail_next_create = False
                if props["id"] not in self.run_events:
                    self.run_events[props["id"]] = props
                raise _ConstraintError()
            self.run_events[props["id"]] = props
            return _Result(
                {
                    "id": props["id"],
                    "request_fingerprint": props["request_fingerprint"],
                    "route": props.get("route"),
                    "tool": props.get("tool"),
                    "tool_outcome": props.get("tool_outcome"),
                    "outcome_source": props.get("outcome_source"),
                    "harness_generation_id": props.get("harness_generation_id"),
                    "observed_at": props.get("observed_at"),
                    "ingested_at": props.get("ingested_at"),
                }
            )

        if "JournalEntry" in q or "embedding" in q.lower():
            self.created_journal = True
            raise AssertionError("sensor path must not touch journal/embeddings")

        raise AssertionError(f"unexpected query: {query}")


class _SessionCtx:
    def __init__(self, session: _FakeSession):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *args):
        return False


def _store_with(session: _FakeSession) -> QualityStore:
    def factory():
        class _Wrapped:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def session(self_inner, database: str = "neo4j"):
                return _SessionCtx(session)

        return _Wrapped()

    return QualityStore(factory, "neo4j")


def _feedback(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "fb-test-1",
        "kind": "entity_wrong",
        "sensitivity": "personal",
        "harness_generation_id": GENERATION_ID,
        "redacted_summary": "not CarPlace",
        "source_turn_ref": "turn-1",
    }
    base.update(overrides)
    return base


def _run_event(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "re-test-1",
        "harness_generation_id": GENERATION_ID,
        "route": "READ",
        "tool": "read_neo4j_cypher",
        "tool_outcome": "empty",
        "outcome_source": "mcp",
        "error_class": "no_hits",
        "sensitivity": "public_ops",
        "entity_refs": ["person:alice"],
        "observed_at": "2026-07-10T12:00:00Z",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Module isolation: no embeddings / journal imports
# ---------------------------------------------------------------------------


def test_quality_module_does_not_import_embeddings_or_journal():
    path = (
        ROOT
        / "mcp_servers"
        / "cypher"
        / "src"
        / "digital_brain_mcp_cypher"
        / "quality.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
                if node.module.startswith("."):
                    for alias in node.names:
                        imported.add(alias.name)
    assert "embeddings" not in imported
    assert "journal" not in imported
    # Relative imports of sibling modules
    source = path.read_text(encoding="utf-8")
    assert "from .embeddings" not in source
    assert "from .journal" not in source
    assert "generate_embedding" not in source
    assert "JournalEntry" not in source or "never" in source.lower()


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------


def test_create_feedback_created_replay_conflict():
    session = _FakeSession()
    store = _store_with(session)
    payload = _feedback(raw_payload="secret correction text")

    created = store.create_feedback(payload)
    assert created["outcome"] == "created"
    assert created["feedback_id"] == "fb-test-1"
    assert created["raw_payload_ref"] == "qp-fb-test-1"
    assert created["harness_generation_id"] == GENERATION_ID
    assert "secret correction text" not in created
    assert "qp-fb-test-1" in session.payloads
    assert session.payloads["qp-fb-test-1"]["payload_text"] == "secret correction text"
    # Immutable Feedback node has no raw text property.
    assert "payload_text" not in session.feedback["fb-test-1"]
    assert session.feedback["fb-test-1"]["raw_hmac"] == compute_raw_hmac(
        "secret correction text"
    )

    replayed = store.create_feedback(payload)
    assert replayed["outcome"] == "replayed"
    assert replayed["feedback_id"] == "fb-test-1"

    session.feedback["fb-test-1"]["request_fingerprint"] = "0" * 64
    conflict = store.create_feedback(payload)
    assert conflict["outcome"] == "conflict"
    assert conflict["reason"] == "feedback_id_reused"


def test_create_feedback_validation_errors():
    session = _FakeSession()
    store = _store_with(session)

    with pytest.raises(ValueError, match="kind"):
        store.create_feedback(_feedback(kind="not_a_kind"))

    with pytest.raises(ValueError, match="sensitivity"):
        store.create_feedback(_feedback(sensitivity="top_secret"))

    with pytest.raises(ValueError, match="harness_generation_id"):
        store.create_feedback(_feedback(harness_generation_id=""))

    with pytest.raises(ValueError, match="redacted_summary exceeds"):
        store.create_feedback(_feedback(redacted_summary="x" * (MAX_SUMMARY_LEN + 1)))

    with pytest.raises(ValueError, match="raw_payload exceeds"):
        store.create_feedback(_feedback(raw_payload="y" * (MAX_RAW_PAYLOAD_LEN + 1)))


def test_feedback_fingerprint_excludes_raw_text_includes_hmac():
    identity = feedback_identity_payload(
        kind="praise",
        sensitivity="public_ops",
        source_turn_ref=None,
        redacted_summary="👍",
        harness_generation_id=GENERATION_ID,
        schema_version="1",
        taxonomy_version="1",
        raw_hmac=compute_raw_hmac("raw"),
    )
    assert "raw" not in identity.values()
    assert identity["raw_hmac"] == compute_raw_hmac("raw")
    fp1 = compute_sensor_request_fingerprint(identity)
    identity2 = dict(identity)
    identity2["raw_hmac"] = compute_raw_hmac("other")
    assert compute_sensor_request_fingerprint(identity2) != fp1


def test_revoke_feedback_created_replay_and_missing_parent():
    session = _FakeSession()
    store = _store_with(session)
    store.create_feedback(_feedback())

    rev = {
        "id": "fle-1",
        "feedback_id": "fb-test-1",
        "actor": "user",
        "reason_code": "user_request",
    }
    created = store.revoke_feedback(rev)
    assert created["outcome"] == "created"
    assert created["event"] == "revoked"

    replayed = store.revoke_feedback(rev)
    assert replayed["outcome"] == "replayed"

    session.lifecycle["fle-1"]["request_fingerprint"] = "1" * 64
    conflict = store.revoke_feedback(rev)
    assert conflict["outcome"] == "conflict"

    missing = store.revoke_feedback(
        {"id": "fle-missing-parent", "feedback_id": "fb-nope", "actor": "user"}
    )
    assert missing["outcome"] == "not_found"
    assert missing["reason"] == "feedback_missing"


def test_redaction_can_drop_payload_while_feedback_fingerprint_remains():
    session = _FakeSession()
    store = _store_with(session)
    store.create_feedback(_feedback(raw_payload="intimate detail"))
    fb = session.feedback["fb-test-1"]
    fp_before = fb["request_fingerprint"]
    ref = fb["raw_payload_ref"]
    # Simulate retention redaction: remove payload node only.
    del session.payloads[ref]
    assert store.get_receipt("fb-test-1")["request_fingerprint"] == fp_before
    assert store.get_receipt("fb-test-1")["raw_payload_ref"] == ref
    assert ref not in session.payloads


# ---------------------------------------------------------------------------
# RunEvent
# ---------------------------------------------------------------------------


def test_record_run_event_created_replay_conflict():
    session = _FakeSession()
    store = _store_with(session)
    event = _run_event()

    created = store.record_run_event(event)
    assert created["outcome"] == "created"
    assert created["outcome_source"] == "mcp"
    assert created["harness_generation_id"] == GENERATION_ID

    replayed = store.record_run_event(event)
    assert replayed["outcome"] == "replayed"

    session.run_events["re-test-1"]["request_fingerprint"] = "f" * 64
    conflict = store.record_run_event(event)
    assert conflict["outcome"] == "conflict"
    assert conflict["reason"] == "run_event_id_reused"


def test_model_facing_force_outcome_source_model_advisory():
    session = _FakeSession()
    store = _store_with(session)
    # Caller claims mcp authority — model-facing force overrides.
    created = store.record_run_event(
        _run_event(outcome_source="mcp", id="re-adv-1"),
        force_outcome_source="model_advisory",
    )
    assert created["outcome"] == "created"
    assert created["outcome_source"] == "model_advisory"
    assert session.run_events["re-adv-1"]["outcome_source"] == "model_advisory"


def test_deterministic_recorder_rejects_model_advisory():
    session = _FakeSession()
    store = _store_with(session)
    with pytest.raises(ValueError, match="deterministic"):
        store.record_deterministic_run_event(
            _run_event(outcome_source="model_advisory")
        )
    for source in DETERMINISTIC_OUTCOME_SOURCES:
        out = store.record_deterministic_run_event(
            _run_event(id=f"re-det-{source}", outcome_source=source)
        )
        assert out["outcome"] == "created"
        assert out["outcome_source"] == source


def test_run_event_validation_errors():
    session = _FakeSession()
    store = _store_with(session)

    with pytest.raises(ValueError, match="route"):
        store.record_run_event(_run_event(route="DREAM"))

    with pytest.raises(ValueError, match="tool_outcome"):
        store.record_run_event(_run_event(tool_outcome="maybe"))

    with pytest.raises(ValueError, match="sensitivity"):
        store.record_run_event(_run_event(sensitivity="classified"))

    with pytest.raises(ValueError, match="entity_refs exceeds"):
        store.record_run_event(
            _run_event(entity_refs=[f"e{i}" for i in range(MAX_REF_COUNT + 1)])
        )

    with pytest.raises(ValueError, match="harness_generation_id"):
        store.record_run_event(_run_event(harness_generation_id=None))

    with pytest.raises(ValueError, match="redacted_summary exceeds"):
        store.record_run_event(
            _run_event(redacted_summary="z" * (MAX_SUMMARY_LEN + 1))
        )


def test_instrumented_read_empty_and_write_conflict_timeout_sources():
    session = _FakeSession()
    store = _store_with(session)

    cases = [
        ("re-read-empty", "READ", "read_neo4j_cypher", "empty", "mcp", "no_hits"),
        ("re-read-fail", "READ", "read_neo4j_cypher", "fail", "mcp", "query_error"),
        (
            "re-write-conflict",
            "WRITE",
            "append_journal_entry",
            "conflict",
            "host",
            "chain_conflict",
        ),
        (
            "re-write-timeout",
            "WRITE",
            "append_journal_entry",
            "timeout",
            "host",
            "mcp_timeout",
        ),
    ]
    for event_id, route, tool, outcome, source, error_class in cases:
        payload = build_tool_outcome_run_event(
            event_id=event_id,
            harness_generation_id=GENERATION_ID,
            tool=tool,
            tool_outcome=outcome,
            route=route,
            outcome_source=source,
            error_class=error_class,
        )
        # Pin is unchanged across all instrumented paths.
        assert payload["harness_generation_id"] == GENERATION_ID
        result = store.record_deterministic_run_event(payload)
        assert result["outcome"] == "created"
        assert result["outcome_source"] == source
        assert result["tool_outcome"] == outcome
        assert result["harness_generation_id"] == GENERATION_ID
        assert session.run_events[event_id]["harness_generation_id"] == GENERATION_ID


def test_deterministic_independent_of_model_prose():
    """Host records tool outcome without any model-authored success claim."""
    session = _FakeSession()
    store = _store_with(session)
    payload = build_tool_outcome_run_event(
        event_id="re-host-only",
        harness_generation_id=GENERATION_ID,
        tool="append_journal_entry",
        tool_outcome="timeout",
        route="WRITE",
        outcome_source="host",
        error_class="mcp_timeout",
        redacted_summary=None,  # no model prose
    )
    assert "success" not in str(payload).lower() or payload["tool_outcome"] == "timeout"
    out = store.record_deterministic_run_event(payload)
    assert out["outcome_source"] == "host"
    assert out["tool_outcome"] == "timeout"


def test_try_record_tool_outcome_best_effort_uses_session_pin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    session = _FakeSession()
    store = _store_with(session)
    monkeypatch.setenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID", GENERATION_ID)
    # Avoid host active pin leaking into the "missing pin" assertion.
    monkeypatch.setenv("DIGITAL_BRAIN_STATE_DIR", str(tmp_path / "empty-state"))
    monkeypatch.delenv("DIGITAL_BRAIN_HARNESS_PIN_PATH", raising=False)

    out = try_record_tool_outcome_run_event(
        store.record_deterministic_run_event,
        tool="read_neo4j_cypher",
        tool_outcome="empty",
        route="READ",
        outcome_source="mcp",
        error_class="no_hits",
        event_id="re-try-record-empty",
    )
    assert out is not None
    assert out["outcome"] == "created"
    assert out["harness_generation_id"] == GENERATION_ID
    assert session.run_events["re-try-record-empty"]["outcome_source"] == "mcp"

    # Missing pin → skip (do not raise).
    monkeypatch.delenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID", raising=False)
    skipped = try_record_tool_outcome_run_event(
        store.record_deterministic_run_event,
        tool="read_neo4j_cypher",
        tool_outcome="empty",
        route="READ",
        outcome_source="mcp",
    )
    assert skipped is None

    # Recorder failure → swallow.
    def boom(_event: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("store down")

    monkeypatch.setenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID", GENERATION_ID)
    assert (
        try_record_tool_outcome_run_event(
            boom,
            tool="write_neo4j_cypher",
            tool_outcome="fail",
            route="WRITE",
            outcome_source="mcp",
        )
        is None
    )


def test_resolve_and_mint_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    assert mint_tool_outcome_event_id("read_neo4j_cypher", "empty").startswith(
        "re-read_neo4j_cypher-empty-"
    )
    assert resolve_session_harness_generation_id(GENERATION_ID) == GENERATION_ID
    # Isolate from the developer's real state dir / active pin.
    monkeypatch.setenv("DIGITAL_BRAIN_STATE_DIR", str(tmp_path / "empty-state"))
    monkeypatch.delenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID", raising=False)
    monkeypatch.delenv("DIGITAL_BRAIN_HARNESS_PIN_PATH", raising=False)
    assert resolve_session_harness_generation_id(None) is None
    monkeypatch.setenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID", GENERATION_ID)
    assert resolve_session_harness_generation_id(None) == GENERATION_ID


def test_resolve_reads_active_pin_file_without_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """MCP dual-process path: active pin under state dir, no env injection."""
    monkeypatch.delenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID", raising=False)
    monkeypatch.delenv("DIGITAL_BRAIN_HARNESS_PIN_PATH", raising=False)
    state = tmp_path / "state"
    active = state / "active"
    active.mkdir(parents=True)
    gid = "hg-" + ("b" * 64)
    (active / "harness_generation.id").write_text(gid + "\n", encoding="utf-8")
    monkeypatch.setenv("DIGITAL_BRAIN_STATE_DIR", str(state))

    assert resolve_session_harness_generation_id(None) == gid


def test_resolve_reads_pin_path_json_and_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.delenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID", raising=False)
    monkeypatch.setenv("DIGITAL_BRAIN_STATE_DIR", str(tmp_path / "no-active"))
    gid = "hg-" + ("c" * 64)

    pin_json = tmp_path / "harness_generation.json"
    pin_json.write_text(
        json.dumps({"id": gid, "plugin_version": "0.2.0"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DIGITAL_BRAIN_HARNESS_PIN_PATH", str(pin_json))
    assert resolve_session_harness_generation_id(None) == gid

    env_file = tmp_path / "harness_generation.env"
    env_file.write_text(
        f"DIGITAL_BRAIN_HARNESS_GENERATION_ID={gid}\n"
        f"DIGITAL_BRAIN_HARNESS_PIN_PATH={pin_json}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DIGITAL_BRAIN_HARNESS_PIN_PATH", str(env_file))
    assert resolve_session_harness_generation_id(None) == gid


def test_try_record_uses_active_pin_file_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Instrumentation records when only the well-known active pin is present."""
    monkeypatch.delenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID", raising=False)
    monkeypatch.delenv("DIGITAL_BRAIN_HARNESS_PIN_PATH", raising=False)
    state = tmp_path / "state"
    active = state / "active"
    active.mkdir(parents=True)
    (active / "harness_generation.json").write_text(
        json.dumps({"id": GENERATION_ID, "session_id": "s1"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DIGITAL_BRAIN_STATE_DIR", str(state))

    session = _FakeSession()
    store = _store_with(session)
    out = try_record_tool_outcome_run_event(
        store.record_deterministic_run_event,
        tool="read_neo4j_cypher",
        tool_outcome="empty",
        route="READ",
        outcome_source="mcp",
        error_class="no_hits",
        event_id="re-active-pin-only",
    )
    assert out is not None
    assert out["outcome"] == "created"
    assert out["harness_generation_id"] == GENERATION_ID
    assert session.run_events["re-active-pin-only"]["outcome_source"] == "mcp"


def test_get_receipt_finds_feedback_and_run_event():
    session = _FakeSession()
    store = _store_with(session)
    store.create_feedback(_feedback())
    store.record_deterministic_run_event(_run_event())

    fb = store.get_receipt("fb-test-1")
    assert fb["outcome"] == "ok"
    assert fb["record_type"] == "Feedback"
    assert fb["kind"] == "entity_wrong"

    re = store.get_receipt("re-test-1")
    assert re["outcome"] == "ok"
    assert re["record_type"] == "RunEvent"
    assert re["tool_outcome"] == "empty"

    missing = store.get_receipt("missing-id")
    assert missing["outcome"] == "not_found"


def test_uniqueness_race_maps_to_replay_for_sensors():
    session = _FakeSession()
    store = _store_with(session)
    session.fail_next_create = True
    # Pre-seed as if concurrent writer won.
    store.create_feedback(_feedback(id="fb-race"))
    session.fail_next_create = True
    # Second call with race flag: node already present from first create.
    out = store.create_feedback(_feedback(id="fb-race"))
    assert out["outcome"] == "replayed"


def test_sensor_writes_never_touch_journal_in_fake_session():
    session = _FakeSession()
    store = _store_with(session)
    store.create_feedback(_feedback(raw_payload="x"))
    store.record_deterministic_run_event(_run_event())
    assert session.created_journal is False
    for query, _params in session.calls:
        assert "JournalEntry" not in query
        assert "embedding" not in query.lower()


def test_feedback_kinds_cover_design_table():
    assert FEEDBACK_KINDS == {
        "entity_wrong",
        "claim_false",
        "miss",
        "invent",
        "praise",
    }
