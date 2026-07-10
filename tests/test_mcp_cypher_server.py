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
