"""Pure unit coverage for the server-owned JournalChain append protocol."""

from __future__ import annotations

import pathlib
import sys
import uuid
from collections import defaultdict
from typing import Any

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))

from digital_brain_mcp_cypher.journal import (  # noqa: E402
    JOURNAL_CONSTRAINTS,
    JournalStore,
    build_append_request,
)


class _Result:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row

    def single(self):
        return self.row

    def consume(self) -> None:
        return None


class _Transaction:
    def __init__(self, responses: dict[str, list[dict[str, Any] | None]]):
        self.responses = defaultdict(list, responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, params: dict[str, Any] | None = None) -> _Result:
        name = self._name(query)
        self.calls.append((name, params or {}))
        rows = self.responses[name]
        if not rows:
            raise AssertionError(f"Unexpected query {name}: {query}")
        return _Result(rows.pop(0))

    @staticmethod
    def _name(query: str) -> str:
        if "SET chain._journal_append_lock" in query:
            return "lock"
        if "REMOVE chain._journal_append_lock" in query:
            return "unlock"
        if "MATCH (entry:JournalEntry {append_key" in query:
            return "receipt"
        if "RETURN count(entry) AS entry_count" in query:
            return "id_collision"
        if "collect({" in query:
            return "chain_state"
        if "WHERE NOT (chain)-[:HEAD]->()" in query:
            return "create_first"
        if "CREATE (entry:JournalEntry)" in query:
            return "create_next"
        if "CREATE CONSTRAINT" in query:
            return "constraint"
        if "OPTIONAL MATCH (entry:JournalEntry)" in query:
            return "bootstrap_empty"
        if "MATCH (candidate:JournalEntry)" in query:
            return "bootstrap_head"
        raise AssertionError(f"No test name for query: {query}")


class _Session:
    def __init__(self, transaction: _Transaction):
        self.transaction = transaction

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def run(self, query: str, params: dict[str, Any] | None = None) -> _Result:
        return self.transaction.run(query, params)

    def execute_write(self, callback):
        return callback(self.transaction)


class _Driver:
    def __init__(self, transaction: _Transaction):
        self.transaction = transaction

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def session(self, *, database: str) -> _Session:
        assert database == "neo4j"
        return _Session(self.transaction)


def _store(responses: dict[str, list[dict[str, Any] | None]]) -> tuple[JournalStore, _Transaction]:
    transaction = _Transaction(responses)
    return JournalStore(lambda: _Driver(transaction), "neo4j"), transaction


def _request(*, expected_version: int = 0, mood: str | None = "calm"):
    return build_append_request(
        append_key="00000000-0000-4000-8000-000000000001",
        content="Journal content",
        timestamp="2026-07-09T00:00:00Z",
        mood=mood,
        expected_version=expected_version,
        properties={"source": "unit-test"},
    ).with_embedding([0.1, 0.2])


def _head(version: int = 0, journal_id: str | None = "legacy-head") -> dict[str, Any]:
    heads: list[dict[str, Any]] = []
    if journal_id is not None:
        heads.append(
            {
                "journal_id": journal_id,
                "append_key": None,
                "timestamp": "2026-07-08T00:00:00Z",
                "mood": None,
                "journal_version": version,
                "previous_journal_id": "older",
                "element_id": "legacy-element",
            }
        )
    return {"version": version, "heads": heads}


def _created(version: int, previous_journal_id: str | None) -> dict[str, Any]:
    request = _request()
    return {
        "journal_id": request.journal_id,
        "append_key": request.append_key,
        "timestamp": request.timestamp,
        "mood": request.mood,
        "journal_version": version,
        "previous_journal_id": previous_journal_id,
        "element_id": "new-element",
    }


def test_request_has_a_stable_uuid_based_id_and_canonical_fingerprint() -> None:
    key = str(uuid.UUID("00000000-0000-4000-8000-000000000001")).upper()
    first = build_append_request(
        append_key=key,
        content="Journal content",
        timestamp="2026-07-09T00:00:00Z",
        mood=None,
        expected_version=0,
        properties={"a": 1, "b": "two"},
    )
    second = build_append_request(
        append_key=key.lower(),
        content="Journal content",
        timestamp="2026-07-09T00:00:00Z",
        mood=None,
        expected_version=9,
        properties={"b": "two", "a": 1},
    )

    assert first.append_key == key.lower()
    assert first.journal_id == f"journal-{key.lower()}"
    assert first.request_fingerprint == second.request_fingerprint
    assert first.mood is None


def test_request_rejects_non_uuid_and_reserved_properties() -> None:
    with pytest.raises(ValueError, match="UUID"):
        build_append_request(
            append_key="not-a-uuid",
            content="content",
            timestamp="now",
            mood=None,
            expected_version=0,
            properties=None,
        )
    with pytest.raises(ValueError, match="reserved"):
        build_append_request(
            append_key="00000000-0000-4000-8000-000000000001",
            content="content",
            timestamp="now",
            mood=None,
            expected_version=0,
            properties={"append_key": "override"},
        )


def test_empty_chain_is_ready_for_a_first_append() -> None:
    store, _ = _store({"chain_state": [_head(journal_id=None)]})

    assert store.get_chain_head() == {
        "outcome": "ok",
        "chain_key": "primary",
        "version": 0,
        "journal_id": None,
        "append_key": None,
        "timestamp": None,
        "mood": None,
        "previous_journal_id": None,
        "element_id": None,
    }


def test_first_append_creates_head_without_follows() -> None:
    request = _request()
    store, transaction = _store(
        {
            "lock": [{"chain_element_id": "chain"}],
            "receipt": [None],
            "id_collision": [{"entry_count": 0}],
            "chain_state": [_head(journal_id=None)],
            "create_first": [_created(1, None)],
            "unlock": [None],
        }
    )

    payload = store.append(request)

    assert payload["outcome"] == "created"
    assert payload["version"] == 1
    assert payload["previous_journal_id"] is None
    assert [name for name, _ in transaction.calls] == [
        "lock",
        "receipt",
        "id_collision",
        "chain_state",
        "create_first",
        "unlock",
    ]


def test_following_append_creates_one_follows_link_from_current_head() -> None:
    request = _request(expected_version=3)
    store, transaction = _store(
        {
            "lock": [{"chain_element_id": "chain"}],
            "receipt": [None],
            "id_collision": [{"entry_count": 0}],
            "chain_state": [_head(version=3)],
            "create_next": [_created(4, "legacy-head")],
            "unlock": [None],
        }
    )

    payload = store.append(request)

    assert payload == {
        "outcome": "created",
        "append_key": request.append_key,
        "journal_id": request.journal_id,
        "version": 4,
        "previous_journal_id": "legacy-head",
        "timestamp": request.timestamp,
        "mood": request.mood,
        "element_id": "new-element",
    }
    assert "create_next" in [name for name, _ in transaction.calls]


def test_same_key_and_fingerprint_replays_before_version_check() -> None:
    request = _request(expected_version=999)
    receipt = _created(2, "legacy-head")
    receipt["request_fingerprint"] = request.request_fingerprint
    store, transaction = _store(
        {
            "lock": [{"chain_element_id": "chain"}],
            "receipt": [receipt],
            "unlock": [None],
        }
    )

    payload = store.append(request)

    assert payload["outcome"] == "replayed"
    assert payload["version"] == 2
    assert [name for name, _ in transaction.calls] == ["lock", "receipt", "unlock"]


def test_stale_version_conflicts_without_creating_a_node() -> None:
    request = _request(expected_version=1)
    store, transaction = _store(
        {
            "lock": [{"chain_element_id": "chain"}],
            "receipt": [None],
            "id_collision": [{"entry_count": 0}],
            "chain_state": [_head(version=2)],
            "unlock": [None],
        }
    )

    payload = store.append(request)

    assert payload == {
        "outcome": "conflict",
        "reason": "stale_version",
        "append_key": request.append_key,
        "journal_id": "legacy-head",
        "version": 2,
        "previous_journal_id": "legacy-head",
    }
    assert "create_first" not in [name for name, _ in transaction.calls]
    assert "create_next" not in [name for name, _ in transaction.calls]


def test_receipt_reports_not_found_without_journal_content() -> None:
    store, _ = _store({"receipt": [None]})

    assert store.get_receipt("00000000-0000-4000-8000-000000000001") == {
        "outcome": "not_found",
        "append_key": "00000000-0000-4000-8000-000000000001",
        "journal_id": None,
        "version": None,
        "previous_journal_id": None,
    }


def test_constraints_are_limited_to_safe_new_keys() -> None:
    store, transaction = _store({"constraint": [None, None]})

    store.ensure_constraints()

    assert len(JOURNAL_CONSTRAINTS) == 2
    assert [name for name, _ in transaction.calls] == ["constraint", "constraint"]
    assert all("append_key" in query or "JournalChain" in query for query in JOURNAL_CONSTRAINTS)


def test_bootstrap_uses_a_dedicated_transaction_and_preserves_legacy_head() -> None:
    store, transaction = _store(
        {
            "bootstrap_head": [
                {
                    "chain_key": "primary",
                    "version": 0,
                    "journal_id": "legacy-head",
                    "head_element_id": "legacy-element",
                }
            ]
        }
    )

    payload = store.bootstrap(head_element_id="legacy-element", empty=False)

    assert payload == {
        "outcome": "bootstrapped",
        "chain_key": "primary",
        "version": 0,
        "journal_id": "legacy-head",
        "head_element_id": "legacy-element",
    }
    assert [name for name, _ in transaction.calls] == ["bootstrap_head"]


def test_empty_bootstrap_is_distinct_and_returns_no_legacy_head() -> None:
    store, transaction = _store(
        {
            "bootstrap_empty": [
                {
                    "chain_key": "primary",
                    "version": 0,
                    "journal_id": None,
                    "head_element_id": None,
                }
            ]
        }
    )

    payload = store.bootstrap(head_element_id=None, empty=True)

    assert payload["outcome"] == "bootstrapped"
    assert payload["journal_id"] is None
    assert [name for name, _ in transaction.calls] == ["bootstrap_empty"]
