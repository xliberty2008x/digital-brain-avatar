"""Pure helpers for Cypher MCP validation and parameter handling."""

from __future__ import annotations

import re
from typing import Any


READ_FORBIDDEN_RE = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|DROP|REMOVE|CALL\s+dbms|LOAD\s+CSV|CREATE\s+INDEX|DROP\s+INDEX)\b",
    re.IGNORECASE,
)
# Keywords that end a CREATE/MERGE node-pattern span. Includes DDL tokens so
# `CREATE VECTOR INDEX ... FOR (j:JournalEntry)` is not treated as a journal write.
_CLAUSE_KEYWORDS_RE = (
    r"CREATE|MERGE|MATCH|WITH|RETURN|WHERE|SET|DELETE|DETACH|REMOVE|UNWIND|CALL|"
    r"FOREACH|FOR|INDEX|CONSTRAINT|UNIQUE|VECTOR|FULLTEXT|LOOKUP|RANGE|POINT|TEXT"
)
# Matches a node labeled :JournalEntry inside a CREATE/MERGE clause, including
# relationship-chained patterns like `MERGE (p)-[:WROTE]->(j:JournalEntry {...})`.
# Requires an opening `(` before the label so schema DDL is excluded.
JOURNAL_WRITE_RE = re.compile(
    rf"\b(?:CREATE|MERGE)\b(?:(?!\b(?:{_CLAUSE_KEYWORDS_RE})\b).)*?\(\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*JournalEntry\b",
    re.IGNORECASE | re.DOTALL,
)
EMBEDDING_PARAM_RE = re.compile(r"\$embedding\b")


def assert_read_only(query: str) -> None:
    """Reject mutating Cypher in read tools."""
    if READ_FORBIDDEN_RE.search(query or ""):
        raise ValueError("read_neo4j_cypher only accepts read-only Cypher")


def normalize_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """Return a mutable params dict."""
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise TypeError("params must be an object")
    return dict(params)


def with_embedding_param(params: dict[str, Any] | None, embedding: list[float] | None) -> dict[str, Any]:
    """Copy params and add `$embedding` when an embedding was generated."""
    normalized = normalize_params(params)
    if embedding is not None:
        normalized["embedding"] = embedding
    return normalized


def validate_embedding_usage(query: str, embed_text: str | None) -> None:
    """Require JournalEntry writes to always pass embed_text and consume `$embedding`."""
    query = query or ""
    if not JOURNAL_WRITE_RE.search(query):
        return
    if embed_text is None or not str(embed_text).strip():
        raise ValueError(
            "JournalEntry writes must pass embed_text so the entry gets an embedding"
        )
    if not EMBEDDING_PARAM_RE.search(query):
        raise ValueError(
            "JournalEntry writes that pass embed_text must set an embedding property with `$embedding`"
        )


def serialize_records(records: list[Any]) -> list[dict[str, Any]]:
    """Convert Neo4j Record objects to plain dictionaries."""
    result: list[dict[str, Any]] = []
    for record in records:
        if hasattr(record, "data"):
            result.append(record.data())
        elif isinstance(record, dict):
            result.append(record)
        else:
            result.append(dict(record))
    return result
