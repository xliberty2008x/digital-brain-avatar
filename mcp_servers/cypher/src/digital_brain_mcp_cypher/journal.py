"""Atomic JournalEntry append support for the Cypher MCP server.

The generic Cypher writer intentionally cannot create journal entries.  This
module owns the small, constrained mutation protocol that provides a stable
idempotency key and a single linear journal chain.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping


PRIMARY_JOURNAL_CHAIN_KEY = "primary"
_RESERVED_ENTRY_PROPERTIES = frozenset(
    {
        "id",
        "append_key",
        "request_fingerprint",
        "content",
        "timestamp",
        "mood",
        "embedding",
        "chain_version",
        "previous_journal_id",
        "previous_element_id",
    }
)

JOURNAL_CONSTRAINTS = (
    """
    CREATE CONSTRAINT journal_chain_key_unique IF NOT EXISTS
    FOR (chain:JournalChain) REQUIRE chain.key IS UNIQUE
    """,
    """
    CREATE CONSTRAINT journal_entry_append_key_unique IF NOT EXISTS
    FOR (entry:JournalEntry) REQUIRE entry.append_key IS UNIQUE
    """,
)

_LOCK_CHAIN_QUERY = """
MATCH (chain:JournalChain {key: $chain_key})
SET chain._journal_append_lock = $lock_token
RETURN elementId(chain) AS chain_element_id
"""

_UNLOCK_CHAIN_QUERY = """
MATCH (chain:JournalChain {key: $chain_key})
WHERE chain._journal_append_lock = $lock_token
REMOVE chain._journal_append_lock
"""

_CHAIN_STATE_QUERY = """
MATCH (chain:JournalChain {key: $chain_key})
OPTIONAL MATCH (chain)-[:HEAD]->(head:JournalEntry)
RETURN chain.version AS version,
       collect({
           journal_id: head.id,
           append_key: head.append_key,
           timestamp: head.timestamp,
           mood: head.mood,
           journal_version: head.chain_version,
           previous_journal_id: head.previous_journal_id,
           element_id: elementId(head)
       }) AS heads
"""

_RECEIPT_QUERY = """
MATCH (entry:JournalEntry {append_key: $append_key})
OPTIONAL MATCH (entry)-[:FOLLOWS]->(previous:JournalEntry)
RETURN entry.id AS journal_id,
       entry.append_key AS append_key,
       entry.request_fingerprint AS request_fingerprint,
       entry.timestamp AS timestamp,
       entry.mood AS mood,
       entry.chain_version AS journal_version,
       coalesce(entry.previous_journal_id, previous.id) AS previous_journal_id,
       elementId(entry) AS element_id
LIMIT 2
"""

_ENTRY_ID_COLLISION_QUERY = """
MATCH (entry:JournalEntry {id: $journal_id})
RETURN count(entry) AS entry_count
"""

_CREATE_APPEND_QUERY = """
MATCH (chain:JournalChain {key: $chain_key})-[old_head:HEAD]->(previous:JournalEntry)
CREATE (entry:JournalEntry)
SET entry += $entry_properties
CREATE (entry)-[:FOLLOWS]->(previous)
DELETE old_head
CREATE (chain)-[:HEAD]->(entry)
SET chain.version = $new_version
RETURN entry.id AS journal_id,
       entry.append_key AS append_key,
       entry.timestamp AS timestamp,
       entry.mood AS mood,
       entry.chain_version AS journal_version,
       entry.previous_journal_id AS previous_journal_id,
       elementId(entry) AS element_id
"""

_CREATE_FIRST_APPEND_QUERY = """
MATCH (chain:JournalChain {key: $chain_key})
WHERE NOT (chain)-[:HEAD]->()
CREATE (entry:JournalEntry)
SET entry += $entry_properties
CREATE (chain)-[:HEAD]->(entry)
SET chain.version = $new_version
RETURN entry.id AS journal_id,
       entry.append_key AS append_key,
       entry.timestamp AS timestamp,
       entry.mood AS mood,
       entry.chain_version AS journal_version,
       entry.previous_journal_id AS previous_journal_id,
       elementId(entry) AS element_id
"""

_BOOTSTRAP_EMPTY_QUERY = """
OPTIONAL MATCH (entry:JournalEntry)
WITH count(entry) AS entry_count
WHERE entry_count = 0
MERGE (chain:JournalChain {key: $chain_key})
  ON CREATE SET chain.version = 0, chain.created_at = datetime()
  ON MATCH SET chain.version = coalesce(chain.version, 0)
WITH chain
OPTIONAL MATCH (chain)-[:HEAD]->(head:JournalEntry)
WITH chain, collect(elementId(head)) AS head_element_ids
WHERE size(head_element_ids) = 0
RETURN chain.key AS chain_key,
       chain.version AS version,
       null AS journal_id,
       null AS head_element_id
"""

_BOOTSTRAP_HEAD_QUERY = """
MATCH (candidate:JournalEntry)
WHERE elementId(candidate) = $head_element_id
MERGE (chain:JournalChain {key: $chain_key})
  ON CREATE SET chain.version = 0, chain.created_at = datetime()
  ON MATCH SET chain.version = coalesce(chain.version, 0)
WITH chain, candidate
OPTIONAL MATCH (chain)-[:HEAD]->(existing_head:JournalEntry)
WITH chain, candidate, collect(elementId(existing_head)) AS head_element_ids
WHERE size(head_element_ids) = 0
   OR (size(head_element_ids) = 1 AND head_element_ids[0] = elementId(candidate))
MERGE (chain)-[:HEAD]->(candidate)
RETURN chain.key AS chain_key,
       chain.version AS version,
       candidate.id AS journal_id,
       elementId(candidate) AS head_element_id
"""


@dataclass(frozen=True)
class JournalAppendRequest:
    """Validated data for a single journal append operation."""

    append_key: str
    journal_id: str
    content: str
    timestamp: str
    mood: str | None
    expected_version: int
    properties: dict[str, Any]
    request_fingerprint: str
    embedding: list[float] | None = None

    def with_embedding(self, embedding: list[float]) -> "JournalAppendRequest":
        return replace(self, embedding=embedding)


def canonical_append_key(value: str) -> str:
    """Validate and canonicalize a UUID idempotency key."""
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("append_key must be a UUID") from exc


def build_append_request(
    *,
    append_key: str,
    content: str,
    timestamp: str,
    mood: str | None,
    expected_version: int,
    properties: dict[str, Any] | None,
) -> JournalAppendRequest:
    """Validate public tool inputs and create their stable fingerprint."""
    canonical_key = canonical_append_key(append_key)
    content = _required_text(content, "content")
    timestamp = _required_text(timestamp, "timestamp")
    mood = _optional_text(mood, "mood")
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise ValueError("expected_version must be a non-negative integer")
    if expected_version < 0:
        raise ValueError("expected_version must be a non-negative integer")

    normalized_properties = _normalize_properties(properties)
    fingerprint_payload = {
        "content": content,
        "timestamp": timestamp,
        "mood": mood,
        "properties": normalized_properties,
    }
    request_fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return JournalAppendRequest(
        append_key=canonical_key,
        journal_id=f"journal-{canonical_key}",
        content=content,
        timestamp=timestamp,
        mood=mood,
        expected_version=expected_version,
        properties=normalized_properties,
        request_fingerprint=request_fingerprint,
    )


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null")
    return value if value.strip() else None


def _normalize_properties(properties: dict[str, Any] | None) -> dict[str, Any]:
    if properties is None:
        return {}
    if not isinstance(properties, dict):
        raise TypeError("properties must be an object")

    normalized: dict[str, Any] = {}
    for key, value in properties.items():
        if not isinstance(key, str) or not key:
            raise ValueError("properties keys must be non-empty strings")
        if key in _RESERVED_ENTRY_PROPERTIES:
            raise ValueError(f"properties cannot override reserved field `{key}`")
        normalized[key] = _normalize_property_value(value, key)
    return normalized


def _normalize_property_value(value: Any, key: str) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"properties.{key} must be finite")
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        values = [_normalize_property_value(item, key) for item in value]
        if values:
            value_type = type(values[0])
            if any(type(item) is not value_type for item in values):
                raise ValueError(f"properties.{key} arrays must use one scalar type")
        return values
    raise ValueError(
        f"properties.{key} must be a Neo4j scalar or an array of one scalar type"
    )


class JournalStore:
    """Neo4j operations for the JournalChain append protocol.

    ``driver_factory`` intentionally matches the server's existing ``_driver``
    helper, which makes this class easy to unit-test with a small fake driver.
    """

    def __init__(self, driver_factory: Callable[[], Any], database: str):
        self._driver_factory = driver_factory
        self._database = database

    def ensure_constraints(self) -> None:
        def operation(session: Any) -> None:
            for query in JOURNAL_CONSTRAINTS:
                _consume(session.run(query))

        self._with_session(operation)

    def bootstrap(
        self,
        *,
        head_element_id: str | None,
        empty: bool,
        chain_key: str = PRIMARY_JOURNAL_CHAIN_KEY,
    ) -> dict[str, Any]:
        """Create the protected primary chain without touching legacy FOLLOWS.

        Existing graphs must name one reviewed legacy head. ``empty=True`` is
        intentionally allowed only when no JournalEntry nodes exist at all.
        """
        if empty == bool(head_element_id):
            raise ValueError("provide exactly one of head_element_id or empty=True")

        query = _BOOTSTRAP_EMPTY_QUERY if empty else _BOOTSTRAP_HEAD_QUERY
        params: dict[str, Any] = {"chain_key": chain_key}
        if head_element_id is not None:
            params["head_element_id"] = head_element_id

        def operation(session: Any) -> dict[str, Any] | None:
            execute_write = getattr(session, "execute_write", None) or getattr(
                session, "write_transaction"
            )
            return execute_write(lambda transaction: _run_one(transaction, query, params))

        row = self._with_session(operation)
        if row is None:
            if empty:
                raise ValueError(
                    "cannot bootstrap an empty chain when JournalEntry nodes or an existing HEAD are present"
                )
            raise ValueError(
                "cannot bootstrap primary chain: reviewed head is missing or another HEAD already exists"
            )
        return {
            "outcome": "bootstrapped",
            "chain_key": row.get("chain_key"),
            "version": row.get("version"),
            "journal_id": row.get("journal_id"),
            "head_element_id": row.get("head_element_id"),
        }

    def get_chain_head(self, chain_key: str = PRIMARY_JOURNAL_CHAIN_KEY) -> dict[str, Any]:
        state = self._with_session(
            lambda session: _run_one(session, _CHAIN_STATE_QUERY, {"chain_key": chain_key})
        )
        return _chain_head_payload(state, chain_key)

    def get_receipt(self, append_key: str) -> dict[str, Any]:
        canonical_key = canonical_append_key(append_key)
        receipt = self.find_receipt(canonical_key)
        if receipt is None:
            return {
                "outcome": "not_found",
                "append_key": canonical_key,
                "journal_id": None,
                "version": None,
                "previous_journal_id": None,
            }
        return _receipt_payload(receipt, outcome="found")

    def find_receipt(self, append_key: str) -> dict[str, Any] | None:
        return self._with_session(
            lambda session: _run_one(session, _RECEIPT_QUERY, {"append_key": append_key})
        )

    def append(
        self,
        request: JournalAppendRequest,
        chain_key: str = PRIMARY_JOURNAL_CHAIN_KEY,
    ) -> dict[str, Any]:
        if request.embedding is None:
            raise ValueError("append requires a generated embedding")

        def operation(session: Any) -> dict[str, Any]:
            execute_write = getattr(session, "execute_write", None) or getattr(
                session, "write_transaction"
            )
            return execute_write(
                lambda transaction: self._append_in_transaction(transaction, request, chain_key)
            )

        return self._with_session(operation)

    def _append_in_transaction(
        self,
        transaction: Any,
        request: JournalAppendRequest,
        chain_key: str,
    ) -> dict[str, Any]:
        lock_token = str(uuid.uuid4())
        locked = _run_one(
            transaction,
            _LOCK_CHAIN_QUERY,
            {"chain_key": chain_key, "lock_token": lock_token},
        )
        if locked is None:
            return _chain_uninitialized_payload(request.append_key, chain_key)

        try:
            # Re-check after acquiring the chain write lock. A concurrent call
            # with the same append key may have committed while this one waited.
            receipt = _run_one(
                transaction,
                _RECEIPT_QUERY,
                {"append_key": request.append_key},
            )
            if receipt is not None:
                return replay_or_key_conflict(receipt, request)

            collision = _run_one(
                transaction,
                _ENTRY_ID_COLLISION_QUERY,
                {"journal_id": request.journal_id},
            )
            if collision and int(collision.get("entry_count") or 0) > 0:
                return {
                    "outcome": "conflict",
                    "reason": "journal_id_already_exists",
                    "append_key": request.append_key,
                    "journal_id": request.journal_id,
                    "version": None,
                    "previous_journal_id": None,
                }

            state = _run_one(
                transaction,
                _CHAIN_STATE_QUERY,
                {"chain_key": chain_key},
            )
            chain_payload = _chain_head_payload(state, chain_key)
            if chain_payload["outcome"] != "ok":
                chain_payload["append_key"] = request.append_key
                return chain_payload

            actual_version = int(chain_payload["version"])
            if actual_version != request.expected_version:
                return {
                    "outcome": "conflict",
                    "reason": "stale_version",
                    "append_key": request.append_key,
                    "journal_id": chain_payload["journal_id"],
                    "version": actual_version,
                    "previous_journal_id": chain_payload["journal_id"],
                }

            entry_properties = dict(request.properties)
            entry_properties.update(
                {
                    "id": request.journal_id,
                    "append_key": request.append_key,
                    "request_fingerprint": request.request_fingerprint,
                    "content": request.content,
                    "timestamp": request.timestamp,
                    "embedding": request.embedding,
                    "chain_version": actual_version + 1,
                    "previous_journal_id": chain_payload["journal_id"],
                    "previous_element_id": chain_payload.get("element_id"),
                }
            )
            if request.mood is not None:
                entry_properties["mood"] = request.mood
            create_query = (
                _CREATE_FIRST_APPEND_QUERY
                if chain_payload["journal_id"] is None
                else _CREATE_APPEND_QUERY
            )
            created = _run_one(
                transaction,
                create_query,
                {
                    "chain_key": chain_key,
                    "entry_properties": entry_properties,
                    "new_version": actual_version + 1,
                },
            )
            if created is None:
                return {
                    "outcome": "conflict",
                    "reason": "chain_changed",
                    "append_key": request.append_key,
                    "journal_id": chain_payload["journal_id"],
                    "version": actual_version,
                    "previous_journal_id": chain_payload["journal_id"],
                }
            return _receipt_payload(created, outcome="created")
        finally:
            _run_one(
                transaction,
                _UNLOCK_CHAIN_QUERY,
                {"chain_key": chain_key, "lock_token": lock_token},
            )

    def _with_session(self, operation: Callable[[Any], Any]) -> Any:
        with self._driver_factory() as driver:
            with driver.session(database=self._database) as session:
                return operation(session)


def _chain_head_payload(state: Mapping[str, Any] | None, chain_key: str) -> dict[str, Any]:
    if state is None:
        return _chain_uninitialized_payload(None, chain_key)
    try:
        version = int(state.get("version"))
    except (TypeError, ValueError):
        return {
            "outcome": "chain_invalid",
            "reason": "missing_version",
            "chain_key": chain_key,
            "version": None,
            "journal_id": None,
            "append_key": None,
            "previous_journal_id": None,
        }

    raw_heads = state.get("heads") or []
    heads = [head for head in raw_heads if isinstance(head, dict) and head.get("element_id")]
    if not heads:
        # `bootstrap_journal_chain --empty` deliberately creates a versioned
        # chain before its first entry. The first append creates HEAD but has
        # no predecessor (and therefore no FOLLOWS relationship).
        return {
            "outcome": "ok",
            "chain_key": chain_key,
            "version": version,
            "journal_id": None,
            "append_key": None,
            "timestamp": None,
            "mood": None,
            "previous_journal_id": None,
            "element_id": None,
        }
    if len(heads) != 1:
        return {
            "outcome": "chain_invalid",
            "reason": "multiple_heads",
            "chain_key": chain_key,
            "version": version,
            "journal_id": None,
            "append_key": None,
            "previous_journal_id": None,
        }

    head = heads[0]
    return {
        "outcome": "ok",
        "chain_key": chain_key,
        "version": version,
        "journal_id": head.get("journal_id"),
        "append_key": head.get("append_key"),
        "timestamp": head.get("timestamp"),
        "mood": head.get("mood"),
        "previous_journal_id": head.get("previous_journal_id"),
        "element_id": head.get("element_id"),
    }


def _chain_uninitialized_payload(
    append_key: str | None,
    chain_key: str,
) -> dict[str, Any]:
    return {
        "outcome": "chain_uninitialized",
        "chain_key": chain_key,
        "append_key": append_key,
        "journal_id": None,
        "version": None,
        "previous_journal_id": None,
    }


def replay_or_key_conflict(
    receipt: Mapping[str, Any], request: JournalAppendRequest
) -> dict[str, Any]:
    if receipt.get("request_fingerprint") == request.request_fingerprint:
        return _receipt_payload(receipt, outcome="replayed")
    return {
        "outcome": "conflict",
        "reason": "append_key_reused",
        "append_key": request.append_key,
        "journal_id": receipt.get("journal_id"),
        "version": receipt.get("journal_version"),
        "previous_journal_id": receipt.get("previous_journal_id"),
    }


def _receipt_payload(receipt: Mapping[str, Any], *, outcome: str) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "append_key": receipt.get("append_key"),
        "journal_id": receipt.get("journal_id"),
        "version": receipt.get("journal_version"),
        "previous_journal_id": receipt.get("previous_journal_id"),
        "timestamp": receipt.get("timestamp"),
        "mood": receipt.get("mood"),
        "element_id": receipt.get("element_id"),
    }


def _run_one(runner: Any, query: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    result = runner.run(query, params or {})
    record = result.single() if hasattr(result, "single") else next(iter(result), None)
    _consume(result)
    if record is None:
        return None
    if hasattr(record, "data"):
        return dict(record.data())
    return dict(record)


def _consume(result: Any) -> None:
    consume = getattr(result, "consume", None)
    if consume is not None:
        consume()
