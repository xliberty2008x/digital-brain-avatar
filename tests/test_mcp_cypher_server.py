"""Server-only behavior tests (run when the Cypher MCP dependencies are installed)."""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from typing import Any

import pytest


pytest.importorskip("fastmcp")
pytest.importorskip("neo4j")

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))

from digital_brain_mcp_cypher import server  # noqa: E402
from digital_brain_mcp_cypher.embeddings import EmbeddingRequestError  # noqa: E402


def _tool_function(tool):
    """FastMCP 2 wraps tools while FastMCP 3 leaves the function callable."""
    return getattr(tool, "fn", tool)


def test_livez_never_checks_dependencies() -> None:
    response = asyncio.run(server.livez(None))

    assert response.status_code == 200
    assert json.loads(response.body) == {"status": "live"}


def test_readyz_returns_503_with_a_safe_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        server,
        "_readiness",
        lambda: (False, {"status": "not_ready", "reason": "embedding_oom"}),
    )

    response = asyncio.run(server.readyz(None))

    assert response.status_code == 503
    assert json.loads(response.body) == {"status": "not_ready", "reason": "embedding_oom"}


def test_readiness_requires_neo4j_and_a_real_dimensioned_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "_run_cypher", lambda *_args, **_kwargs: [{"ok": 1}])
    monkeypatch.setattr(server, "generate_embedding", lambda _text: [0.0] * 1024)

    ready, payload = server._readiness()

    assert ready is True
    assert payload == {"status": "ready"}


def test_readiness_hides_ollama_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_run_cypher", lambda *_args, **_kwargs: [{"ok": 1}])

    def fail_embedding(_text: str):
        raise EmbeddingRequestError("private upstream stack trace", reason="oom")

    monkeypatch.setattr(server, "generate_embedding", fail_embedding)

    ready, payload = server._readiness()

    assert ready is False
    assert payload == {"status": "not_ready", "reason": "embedding_oom"}
    assert "private" not in json.dumps(payload)


def test_raw_journal_writes_are_rejected_before_any_embedding_or_driver_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server,
        "generate_embedding",
        lambda _text: (_ for _ in ()).throw(AssertionError("must not embed")),
    )

    with pytest.raises(ValueError, match="append_journal_entry"):
        _tool_function(server.write_neo4j_cypher)("CREATE (:JournalEntry {id: $id})")


def test_append_generates_embedding_only_after_receipt_and_head_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Store:
        def find_receipt(self, _append_key: str):
            events.append("receipt")
            return None

        def get_chain_head(self, _chain_key: str):
            events.append("head")
            return {"outcome": "ok", "version": 0, "journal_id": None}

        def append(self, request, _chain_key: str):
            events.append("append")
            assert request.embedding == [0.0, 0.0]
            return {
                "outcome": "created",
                "append_key": request.append_key,
                "journal_id": request.journal_id,
                "version": 1,
                "previous_journal_id": None,
            }

    monkeypatch.setattr(server, "_journal_store", lambda: Store())
    monkeypatch.setattr(server, "_ensure_journal_schema", lambda: events.append("schema"))
    monkeypatch.setattr(server, "generate_embedding", lambda _text: events.append("embedding") or [0.0, 0.0])

    payload = json.loads(
        _tool_function(server.append_journal_entry)(
            append_key="00000000-0000-4000-8000-000000000001",
            content="journal",
            timestamp="2026-07-09T00:00:00Z",
            expected_version=0,
            mood=None,
            properties=None,
        )
    )

    assert payload["outcome"] == "created"
    assert events == ["receipt", "head", "schema", "embedding", "append"]


def test_existing_receipt_skips_embedding_on_replay_and_key_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Store:
        def __init__(self, receipt: dict[str, Any]):
            self.receipt = receipt

        def find_receipt(self, _append_key: str):
            events.append("receipt")
            return self.receipt

        def get_chain_head(self, _chain_key: str):
            events.append("head")
            raise AssertionError("must not read head after receipt hit")

        def append(self, _request, _chain_key: str):
            events.append("append")
            raise AssertionError("must not append after receipt hit")

    monkeypatch.setattr(
        server,
        "generate_embedding",
        lambda _text: events.append("embedding") or [0.0, 0.0],
    )
    monkeypatch.setattr(server, "_ensure_journal_schema", lambda: events.append("schema"))

    matching = {
        "journal_id": "journal-00000000-0000-4000-8000-000000000001",
        "append_key": "00000000-0000-4000-8000-000000000001",
        "request_fingerprint": None,  # filled below after build
        "timestamp": "2026-07-09T00:00:00Z",
        "mood": None,
        "journal_version": 2,
        "previous_journal_id": None,
        "element_id": "el-1",
    }
    from digital_brain_mcp_cypher.journal import build_append_request

    request = build_append_request(
        append_key="00000000-0000-4000-8000-000000000001",
        content="journal",
        timestamp="2026-07-09T00:00:00Z",
        mood=None,
        expected_version=0,
        properties=None,
    )
    matching["request_fingerprint"] = request.request_fingerprint
    monkeypatch.setattr(server, "_journal_store", lambda: Store(matching))

    replayed = json.loads(
        _tool_function(server.append_journal_entry)(
            append_key="00000000-0000-4000-8000-000000000001",
            content="journal",
            timestamp="2026-07-09T00:00:00Z",
            expected_version=0,
            mood=None,
            properties=None,
        )
    )
    assert replayed["outcome"] == "replayed"
    assert events == ["receipt"]

    events.clear()
    mismatched = dict(matching)
    mismatched["request_fingerprint"] = "other-fingerprint"
    monkeypatch.setattr(server, "_journal_store", lambda: Store(mismatched))

    conflicted = json.loads(
        _tool_function(server.append_journal_entry)(
            append_key="00000000-0000-4000-8000-000000000001",
            content="journal",
            timestamp="2026-07-09T00:00:00Z",
            expected_version=0,
            mood=None,
            properties=None,
        )
    )
    assert conflicted["outcome"] == "conflict"
    assert conflicted["reason"] == "append_key_reused"
    assert events == ["receipt"]


def test_chain_uninitialized_short_circuits_before_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Store:
        def find_receipt(self, _append_key: str):
            events.append("receipt")
            return None

        def get_chain_head(self, _chain_key: str):
            events.append("head")
            return {
                "outcome": "chain_uninitialized",
                "chain_key": "primary",
                "version": None,
                "journal_id": None,
            }

        def append(self, _request, _chain_key: str):
            events.append("append")
            raise AssertionError("must not append")

    monkeypatch.setattr(server, "_journal_store", lambda: Store())
    monkeypatch.setattr(server, "_ensure_journal_schema", lambda: events.append("schema"))
    monkeypatch.setattr(
        server,
        "generate_embedding",
        lambda _text: events.append("embedding") or [0.0, 0.0],
    )

    payload = json.loads(
        _tool_function(server.append_journal_entry)(
            append_key="00000000-0000-4000-8000-000000000001",
            content="journal",
            timestamp="2026-07-09T00:00:00Z",
            expected_version=0,
            mood=None,
            properties=None,
        )
    )

    assert payload["outcome"] == "chain_uninitialized"
    assert events == ["receipt", "head"]


def test_bootstrap_uses_the_dedicated_store_path(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class Store:
        def bootstrap(self, **kwargs):
            events.append(f"bootstrap:{kwargs}")
            return {
                "outcome": "bootstrapped",
                "chain_key": "primary",
                "version": 0,
                "journal_id": "legacy-id",
                "head_element_id": "legacy-element",
            }

    monkeypatch.setattr(server, "_journal_store", lambda: Store())
    monkeypatch.setattr(server, "_ensure_journal_schema", lambda: events.append("schema"))

    payload = json.loads(
        _tool_function(server.bootstrap_journal_chain)(head_element_id="legacy-element", empty=False)
    )

    assert payload["outcome"] == "bootstrapped"
    assert events == [
        "schema",
        "bootstrap:{'head_element_id': 'legacy-element', 'empty': False, 'chain_key': 'primary'}",
    ]


def test_quality_sensor_tools_are_registered() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    for expected in (
        "create_feedback",
        "revoke_feedback",
        "record_run_event",
        "get_quality_receipt",
        "record_harness_generation",
        "get_harness_generation",
    ):
        assert expected in names


def test_record_run_event_tool_forces_model_advisory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Store:
        def record_run_event(self, event, *, force_outcome_source=None, **_kwargs):
            captured["event"] = event
            captured["force_outcome_source"] = force_outcome_source
            return {
                "outcome": "created",
                "run_event_id": event["id"],
                "outcome_source": force_outcome_source,
                "harness_generation_id": event["harness_generation_id"],
            }

    monkeypatch.setattr(server, "_quality_store", lambda: Store())
    monkeypatch.setattr(server, "_ensure_quality_schema", lambda: None)

    payload = json.loads(
        _tool_function(server.record_run_event)(
            id="re-model-1",
            harness_generation_id="hg-" + ("b" * 64),
            route="READ",
            tool_outcome="success",
            outcome_source="mcp",  # caller claim must be ignored
            tool="read_neo4j_cypher",
        )
    )
    assert payload["outcome"] == "created"
    assert captured["force_outcome_source"] == "model_advisory"
    assert payload["outcome_source"] == "model_advisory"


def test_create_feedback_tool_does_not_call_embeddings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server,
        "generate_embedding",
        lambda _text: (_ for _ in ()).throw(AssertionError("must not embed")),
    )

    class Store:
        def create_feedback(self, feedback):
            assert feedback["id"] == "fb-1"
            assert "embedding" not in feedback
            return {
                "outcome": "created",
                "feedback_id": feedback["id"],
                "harness_generation_id": feedback["harness_generation_id"],
            }

    monkeypatch.setattr(server, "_quality_store", lambda: Store())
    monkeypatch.setattr(server, "_ensure_quality_schema", lambda: None)

    payload = json.loads(
        _tool_function(server.create_feedback)(
            id="fb-1",
            kind="miss",
            sensitivity="public_ops",
            harness_generation_id="hg-" + ("c" * 64),
            raw_payload="forgot EPAM Dec",
        )
    )
    assert payload["outcome"] == "created"


def _quality_store_factory_that_must_not_write():
    """QualityStore driver factory whose session refuses any write."""
    from digital_brain_mcp_cypher.quality import QualityStore

    class _FakeSession:
        def execute_write(self, fn):  # noqa: ANN001
            return fn(self)

        def write_transaction(self, fn):  # noqa: ANN001
            return fn(self)

        def run(self, *_a, **_k):
            raise AssertionError("should not write on bad kwargs")

    def factory():
        class _Wrapped:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def session(self_inner, database: str = "neo4j"):
                class _Ctx:
                    def __enter__(self_ctx):
                        return _FakeSession()

                    def __exit__(self_ctx, *a):
                        return False

                return _Ctx()

        return _Wrapped()

    return QualityStore(factory, "neo4j")


def test_create_feedback_tool_wrong_alias_kwargs_are_agent_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP tool path must surface contract hint for summary/detail (not silent drop)."""
    monkeypatch.setattr(
        server, "_quality_store", _quality_store_factory_that_must_not_write
    )
    monkeypatch.setattr(server, "_ensure_quality_schema", lambda: None)

    with pytest.raises(ValueError) as excinfo:
        _tool_function(server.create_feedback)(
            kind="miss",
            harness_generation_id="hg-" + ("c" * 64),
            summary="invented field",
            detail="also wrong",
        )
    err = str(excinfo.value)
    assert "missing required" in err
    assert "id" in err and "sensitivity" in err
    assert "summary" in err and "detail" in err
    assert "redacted_summary" in err or "raw_payload" in err
    for token in ("entity_wrong", "miss", "public_ops", "intimate"):
        assert token in err


def test_create_feedback_tool_missing_required_is_agent_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing required fields alone (no aliases) still name the full contract."""
    monkeypatch.setattr(
        server, "_quality_store", _quality_store_factory_that_must_not_write
    )
    monkeypatch.setattr(server, "_ensure_quality_schema", lambda: None)

    with pytest.raises(ValueError) as excinfo:
        _tool_function(server.create_feedback)(kind="miss")
    err = str(excinfo.value)
    assert "missing required" in err
    for field in ("id", "sensitivity", "harness_generation_id"):
        assert field in err
    assert "create_feedback requires" in err
    assert "public_ops" in err or "intimate" in err


def test_create_feedback_tool_description_documents_contract() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    create = next(t for t in tools if t.name == "create_feedback")
    tool_desc = create.description or ""
    assert "harness_generation_id" in tool_desc
    assert "redacted_summary" in tool_desc
    # Description must warn about aliases / exact required fields.
    assert "summary" in tool_desc or "alias" in tool_desc.lower()
    for kind in ("miss", "invent", "entity_wrong"):
        assert kind in tool_desc
    for field in ("id", "kind", "sensitivity"):
        assert field in tool_desc


def test_create_feedback_tool_schema_http_is_session_less() -> None:
    """GET /tool-schemas/create_feedback needs no MCP session headers."""
    response = asyncio.run(server.create_feedback_tool_schema(None))
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["name"] == "create_feedback"
    assert body["required"] == [
        "id",
        "kind",
        "sensitivity",
        "harness_generation_id",
    ]
    assert "miss" in body["kind"]
    assert "intimate" in body["sensitivity"]
    assert body["forbidden_aliases"]["summary"] == "redacted_summary"
    assert "redacted_summary" in body["contract_hint"] or "raw_payload" in body[
        "contract_hint"
    ]
    assert "digital_brain.tools.mcp_client.create_feedback" in body["prefer"]


def test_revoke_feedback_tool_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    class Store:
        def revoke_feedback(self, revocation):
            return {
                "outcome": "created",
                "lifecycle_event_id": revocation["id"],
                "feedback_id": revocation["feedback_id"],
                "event": "revoked",
            }

    monkeypatch.setattr(server, "_quality_store", lambda: Store())
    monkeypatch.setattr(server, "_ensure_quality_schema", lambda: None)
    payload = json.loads(
        _tool_function(server.revoke_feedback)(
            id="fle-1",
            feedback_id="fb-1",
            actor="owner",
        )
    )
    assert payload["event"] == "revoked"


def test_get_quality_receipt_description_mentions_sensor_ids() -> None:
    """Tool surface documents Feedback/RunEvent/lifecycle reconciliation."""
    tools = asyncio.run(server.mcp.list_tools())
    receipt = next(t for t in tools if t.name == "get_quality_receipt")
    # Collect every text surface FastMCP may expose for the tool.
    blobs: list[str] = [receipt.description or ""]
    ann = getattr(receipt, "annotations", None)
    if ann is not None:
        blobs.append(str(getattr(ann, "description", "") or ann))
    schema = getattr(receipt, "inputSchema", None) or getattr(
        receipt, "parameters", None
    )
    if schema is not None:
        blobs.append(json.dumps(schema))
    # Source contract: annotations description on the registered tool.
    src = pathlib.Path(server.__file__).read_text(encoding="utf-8")
    assert "Feedback, RunEvent, FeedbackLifecycleEvent" in src
    combined = " ".join(blobs).lower()
    # Live list_tools should surface at least one of these when descriptions propagate.
    if combined.strip():
        assert (
            "feedback" in combined
            or "runevent" in combined.replace(" ", "")
            or "run event" in combined
            or "lifecycle" in combined
            or "receipt" in combined
        )


def test_read_empty_path_emits_mcp_run_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """Call site: empty READ results fire deterministic mcp-sourced RunEvent."""
    recorded: list[dict[str, Any]] = []
    generation_id = "hg-" + ("d" * 64)

    class Store:
        def ensure_constraints(self) -> None:
            return None

        def record_deterministic_run_event(self, event: dict[str, Any]) -> dict[str, Any]:
            recorded.append(event)
            return {
                "outcome": "created",
                "run_event_id": event["id"],
                "outcome_source": event["outcome_source"],
                "tool_outcome": event["tool_outcome"],
                "harness_generation_id": event["harness_generation_id"],
            }

    monkeypatch.setenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID", generation_id)
    monkeypatch.setattr(server, "_quality_store", lambda: Store())
    monkeypatch.setattr(server, "_ensure_quality_schema", lambda: None)
    monkeypatch.setattr(server, "generate_embedding", lambda _text: None)
    monkeypatch.setattr(server, "_run_cypher", lambda *_a, **_k: [])

    payload = json.loads(
        _tool_function(server.read_neo4j_cypher)(
            query="MATCH (n:Missing) RETURN n",
            params=None,
            embed_text=None,
        )
    )
    assert payload == []
    assert len(recorded) == 1
    event = recorded[0]
    assert event["tool"] == "read_neo4j_cypher"
    assert event["tool_outcome"] == "empty"
    assert event["route"] == "READ"
    assert event["outcome_source"] == "mcp"
    assert event["error_class"] == "no_hits"
    assert event["harness_generation_id"] == generation_id


def test_read_fail_path_emits_mcp_run_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """Call site: READ query failure fires deterministic mcp-sourced RunEvent."""
    recorded: list[dict[str, Any]] = []
    generation_id = "hg-" + ("e" * 64)

    class Store:
        def ensure_constraints(self) -> None:
            return None

        def record_deterministic_run_event(self, event: dict[str, Any]) -> dict[str, Any]:
            recorded.append(event)
            return {"outcome": "created", "run_event_id": event["id"]}

    monkeypatch.setenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID", generation_id)
    monkeypatch.setattr(server, "_quality_store", lambda: Store())
    monkeypatch.setattr(server, "_ensure_quality_schema", lambda: None)
    monkeypatch.setattr(server, "generate_embedding", lambda _text: None)

    def boom(*_a, **_k):
        raise RuntimeError("neo4j unavailable")

    monkeypatch.setattr(server, "_run_cypher", boom)

    with pytest.raises(RuntimeError, match="neo4j unavailable"):
        _tool_function(server.read_neo4j_cypher)(
            query="MATCH (n) RETURN n",
            params=None,
            embed_text=None,
        )

    assert len(recorded) == 1
    event = recorded[0]
    assert event["tool"] == "read_neo4j_cypher"
    assert event["tool_outcome"] == "fail"
    assert event["route"] == "READ"
    assert event["outcome_source"] == "mcp"
    assert event["harness_generation_id"] == generation_id


def test_read_timeout_path_emits_timeout_outcome_not_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP query timeouts must use tool_outcome=timeout (aligned with host transport)."""
    recorded: list[dict[str, Any]] = []
    generation_id = "hg-" + ("e1" * 32)

    class Store:
        def ensure_constraints(self) -> None:
            return None

        def record_deterministic_run_event(self, event: dict[str, Any]) -> dict[str, Any]:
            recorded.append(event)
            return {"outcome": "created", "run_event_id": event["id"]}

    monkeypatch.setenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID", generation_id)
    monkeypatch.setattr(server, "_quality_store", lambda: Store())
    monkeypatch.setattr(server, "_ensure_quality_schema", lambda: None)
    monkeypatch.setattr(server, "generate_embedding", lambda _text: None)

    def boom(*_a, **_k):
        raise TimeoutError("query timed out after 30s")

    monkeypatch.setattr(server, "_run_cypher", boom)

    with pytest.raises(TimeoutError, match="timed out"):
        _tool_function(server.read_neo4j_cypher)(
            query="MATCH (n) RETURN n",
            params=None,
            embed_text=None,
        )

    assert len(recorded) == 1
    event = recorded[0]
    assert event["tool"] == "read_neo4j_cypher"
    assert event["tool_outcome"] == "timeout"
    assert event["error_class"] == "query_timeout"
    assert event["route"] == "READ"
    assert event["outcome_source"] == "mcp"
    assert event["harness_generation_id"] == generation_id


def test_write_timeout_path_emits_timeout_outcome_not_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[dict[str, Any]] = []
    generation_id = "hg-" + ("e2" * 32)

    class Store:
        def ensure_constraints(self) -> None:
            return None

        def record_deterministic_run_event(self, event: dict[str, Any]) -> dict[str, Any]:
            recorded.append(event)
            return {"outcome": "created", "run_event_id": event["id"]}

    monkeypatch.setenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID", generation_id)
    monkeypatch.setattr(server, "_quality_store", lambda: Store())
    monkeypatch.setattr(server, "_ensure_quality_schema", lambda: None)
    monkeypatch.setattr(server, "generate_embedding", lambda _text: None)

    def boom(*_a, **_k):
        raise RuntimeError("SocketTimeout: connection timed out")

    monkeypatch.setattr(server, "_run_cypher", boom)

    with pytest.raises(RuntimeError, match="timed out"):
        _tool_function(server.write_neo4j_cypher)(
            query="MERGE (t:Topic {id: $id})",
            params={"id": "t-1"},
            embed_text=None,
        )

    assert len(recorded) == 1
    event = recorded[0]
    assert event["tool"] == "write_neo4j_cypher"
    assert event["tool_outcome"] == "timeout"
    assert event["error_class"] == "query_timeout"
    assert event["outcome_source"] == "mcp"


def test_read_empty_instrumentation_failure_does_not_break_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Instrumentation is best-effort: primary empty result still returns."""
    generation_id = "hg-" + ("f" * 64)

    class Store:
        def ensure_constraints(self) -> None:
            return None

        def record_deterministic_run_event(self, event: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("quality plane down")

    monkeypatch.setenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID", generation_id)
    monkeypatch.setattr(server, "_quality_store", lambda: Store())
    monkeypatch.setattr(server, "_ensure_quality_schema", lambda: None)
    monkeypatch.setattr(server, "generate_embedding", lambda _text: None)
    monkeypatch.setattr(server, "_run_cypher", lambda *_a, **_k: [])

    payload = json.loads(
        _tool_function(server.read_neo4j_cypher)(
            query="MATCH (n:Missing) RETURN n",
            params=None,
            embed_text=None,
        )
    )
    assert payload == []


def test_write_fail_path_emits_mcp_run_event(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[dict[str, Any]] = []
    generation_id = "hg-" + ("g" * 64)

    class Store:
        def ensure_constraints(self) -> None:
            return None

        def record_deterministic_run_event(self, event: dict[str, Any]) -> dict[str, Any]:
            recorded.append(event)
            return {"outcome": "created", "run_event_id": event["id"]}

    monkeypatch.setenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID", generation_id)
    monkeypatch.setattr(server, "_quality_store", lambda: Store())
    monkeypatch.setattr(server, "_ensure_quality_schema", lambda: None)
    monkeypatch.setattr(server, "generate_embedding", lambda _text: None)

    def boom(*_a, **_k):
        raise RuntimeError("write failed")

    monkeypatch.setattr(server, "_run_cypher", boom)

    with pytest.raises(RuntimeError, match="write failed"):
        _tool_function(server.write_neo4j_cypher)(
            query="MERGE (t:Topic {id: $id})",
            params={"id": "t-1"},
            embed_text=None,
        )

    assert len(recorded) == 1
    event = recorded[0]
    assert event["tool"] == "write_neo4j_cypher"
    assert event["tool_outcome"] == "fail"
    assert event["route"] == "WRITE"
    assert event["outcome_source"] == "mcp"
    assert event["harness_generation_id"] == generation_id


def test_append_conflict_path_emits_mcp_run_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Call site: journal append conflict fires deterministic mcp RunEvent."""
    recorded: list[dict[str, Any]] = []
    generation_id = "hg-" + ("h" * 64)

    class Store:
        def ensure_constraints(self) -> None:
            return None

        def record_deterministic_run_event(self, event: dict[str, Any]) -> dict[str, Any]:
            recorded.append(event)
            return {"outcome": "created", "run_event_id": event["id"]}

    class Journal:
        def find_receipt(self, append_key: str):
            return {
                "append_key": append_key,
                "content_sha256": "deadbeef",
                "timestamp": "2026-07-09T00:00:00Z",
                "mood": None,
                "expected_version": 0,
                "entry_id": "old-entry",
            }

        def get_chain_head(self, *_a, **_k):
            raise AssertionError("must not reach chain head on key conflict")

        def append(self, *_a, **_k):
            raise AssertionError("must not append on key conflict")

    monkeypatch.setenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID", generation_id)
    monkeypatch.setattr(server, "_quality_store", lambda: Store())
    monkeypatch.setattr(server, "_ensure_quality_schema", lambda: None)
    monkeypatch.setattr(server, "_journal_store", lambda: Journal())

    # Different content than receipt → conflict (not replay).
    payload = json.loads(
        _tool_function(server.append_journal_entry)(
            append_key="00000000-0000-4000-8000-000000000099",
            content="different content for conflict",
            timestamp="2026-07-09T00:00:00Z",
            expected_version=0,
            mood=None,
            properties=None,
        )
    )
    assert payload["outcome"] == "conflict"
    assert len(recorded) == 1
    event = recorded[0]
    assert event["tool"] == "append_journal_entry"
    assert event["tool_outcome"] == "conflict"
    assert event["route"] == "WRITE"
    assert event["outcome_source"] == "mcp"
    assert event["error_class"] == "append_key_conflict"
    assert event["harness_generation_id"] == generation_id
