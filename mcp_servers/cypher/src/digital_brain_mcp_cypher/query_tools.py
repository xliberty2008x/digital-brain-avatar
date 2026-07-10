"""Pure helpers for Cypher MCP validation and parameter handling."""

from __future__ import annotations

import re
from typing import Any


READ_FORBIDDEN_RE = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|DROP|REMOVE|CALL\s+dbms|LOAD\s+CSV|CREATE\s+INDEX|DROP\s+INDEX)\b",
    re.IGNORECASE,
)
CALL_RE = re.compile(r"\bCALL\b", re.IGNORECASE)
# A vector lookup is the only stored procedure the read tool needs. All other
# CALL forms (including CALL { ... } and APOC) can hide writes, so reject them.
SAFE_VECTOR_CALL_RE = re.compile(
    r"\bCALL\s+db\s*\.\s*index\s*\.\s*vector\s*\.\s*queryNodes\s*\(",
    re.IGNORECASE,
)
# Keywords that end a CREATE/MERGE node-pattern span. Includes DDL tokens so
# `CREATE VECTOR INDEX ... FOR (j:JournalEntry)` is not treated as a journal write.
_CLAUSE_KEYWORDS_RE = (
    r"CREATE|MERGE|MATCH|WITH|RETURN|WHERE|SET|DELETE|DETACH|REMOVE|UNWIND|CALL|"
    r"FOREACH|FOR|INDEX|CONSTRAINT|UNIQUE|VECTOR|FULLTEXT|LOOKUP|RANGE|POINT|TEXT"
)
_CYPHER_IDENTIFIER = r"(?:[A-Za-z_][A-Za-z0-9_]*|`(?:``|[^`])+`)"
_JOURNAL_LABEL = r"(?:JournalEntry|`JournalEntry`)"
_CHAIN_LABEL = r"(?:JournalChain|`JournalChain`)"
_FOLLOWS_TYPE = r"(?:FOLLOWS|`FOLLOWS`)"
_HEAD_TYPE = r"(?:HEAD|`HEAD`)"
_PROTECTED_LABEL_NAMES = frozenset({"journalentry", "journalchain"})


def _node_label_pattern(label: str) -> str:
    """Match a labeled node pattern, including anonymous/backticked nodes."""
    return (
        rf"\(\s*(?:{_CYPHER_IDENTIFIER}\s*)?"
        rf"(?::\s*{_CYPHER_IDENTIFIER}\s*)*:\s*{label}(?![A-Za-z0-9_`])"
    )


# Matches a node labeled :JournalEntry inside a CREATE/MERGE clause, including
# anonymous nodes and relationship-chained patterns such as
# `MERGE (p)-[:WROTE]->(j:JournalEntry {...})`. Requires an opening `(` before
# the label so schema DDL is excluded.
JOURNAL_WRITE_RE = re.compile(
    rf"\b(?:CREATE|MERGE)\b(?:(?!\b(?:{_CLAUSE_KEYWORDS_RE})\b).)*?{_node_label_pattern(_JOURNAL_LABEL)}",
    re.IGNORECASE | re.DOTALL,
)
# Matches a :FOLLOWS relationship created or merged in a write clause. Keep
# the clause boundary guard aligned with JOURNAL_WRITE_RE so a read pattern
# later in a query does not get mistaken for a new relationship.
FOLLOWS_WRITE_RE = re.compile(
    rf"\b(?:CREATE|MERGE)\b(?:(?!\b(?:{_CLAUSE_KEYWORDS_RE})\b).)*?-\s*\[\s*(?:{_CYPHER_IDENTIFIER}\s*)?:\s*{_FOLLOWS_TYPE}(?![A-Za-z0-9_`])",
    re.IGNORECASE | re.DOTALL,
)
JOURNAL_CHAIN_NODE_RE = re.compile(_node_label_pattern(_CHAIN_LABEL), re.IGNORECASE)
PROTECTED_RELATIONSHIP_RE = re.compile(
    rf":\s*(?:{_FOLLOWS_TYPE}|{_HEAD_TYPE})(?![A-Za-z0-9_`])",
    re.IGNORECASE,
)
# Captures every label in `SET n:A:B` / `SET n : A : B`, not only the first.
SET_LABEL_LIST_RE = re.compile(
    rf"(?:\bSET\b|,)\s*{_CYPHER_IDENTIFIER}((?:\s*:\s*{_CYPHER_IDENTIFIER})+)",
    re.IGNORECASE,
)
# Full node replacement (`SET n = {...}` / `SET n = $map`), not `SET n.prop =`.
FULL_NODE_SET_RE = re.compile(
    rf"(?:\bSET\b|,)\s*{_CYPHER_IDENTIFIER}\s*=\s*(?:\{{|\$)",
    re.IGNORECASE,
)
# Post-append path is MERGE-only; destructive clauses bypass label guards easily.
DELETE_DETACH_REMOVE_RE = re.compile(r"\b(?:DELETE|DETACH|REMOVE)\b", re.IGNORECASE)
# `type(r) = 'FOLLOWS'` severs the chain without a `:FOLLOWS` literal.
PROTECTED_TYPE_PREDICATE_RE = re.compile(
    rf"\btype\s*\(\s*{_CYPHER_IDENTIFIER}\s*\)\s*(?:=\s*['\"](?:FOLLOWS|HEAD)['\"]|"
    rf"IN\s*\([^)]*(?:FOLLOWS|HEAD)[^)]*\))",
    re.IGNORECASE,
)
JOURNAL_ENTRY_NODE_RE = re.compile(_node_label_pattern(_JOURNAL_LABEL), re.IGNORECASE)
DYNAMIC_PROPERTY_REFERENCE_RE = re.compile(
    rf"\b{_CYPHER_IDENTIFIER}\s*\[\s*(?:\$|['\"])",
    re.IGNORECASE,
)
_PROTECTED_JOURNAL_PROPERTIES = (
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
    "_journal_append_lock",
)
PROTECTED_JOURNAL_PROPERTY_RE = re.compile(
    rf"\.\s*(?:{'|'.join(_PROTECTED_JOURNAL_PROPERTIES)}|"
    rf"`(?:{'|'.join(_PROTECTED_JOURNAL_PROPERTIES)})`)(?![A-Za-z0-9_`])",
    re.IGNORECASE,
)
# Unlabeled chain CAS mutation: SET version while addressing key='primary'.
CHAIN_VERSION_MUTATION_RE = re.compile(
    r"(?=.*\bSET\b)(?=.*\.\s*`?version`?\b)(?=.*(?:['\"]primary['\"]|\.\s*`?key`?\b))",
    re.IGNORECASE | re.DOTALL,
)
EMBEDDING_PARAM_RE = re.compile(r"\$embedding\b")
SET_CLAUSE_RE = re.compile(r"\bSET\b", re.IGNORECASE)


def _normalize_label_token(token: str) -> str:
    token = token.strip()
    if token.startswith("`") and token.endswith("`"):
        token = token[1:-1].replace("``", "`")
    return token.lower()


def _set_adds_protected_label(query: str) -> bool:
    for match in SET_LABEL_LIST_RE.finditer(query):
        for label in re.findall(rf":\s*({_CYPHER_IDENTIFIER})", match.group(1), re.IGNORECASE):
            if _normalize_label_token(label) in _PROTECTED_LABEL_NAMES:
                return True
    return False


def assert_read_only(query: str) -> None:
    """Reject mutating Cypher in read tools."""
    query = query or ""
    if READ_FORBIDDEN_RE.search(query):
        raise ValueError("read_neo4j_cypher only accepts read-only Cypher")
    for call in CALL_RE.finditer(query):
        if SAFE_VECTOR_CALL_RE.match(query, call.start()) is None:
            raise ValueError(
                "read_neo4j_cypher only allows CALL db.index.vector.queryNodes(...)"
            )


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
    """Legacy helper for JournalEntry CREATE+embed_text patterns.

    Generic ``write_neo4j_cypher`` now rejects JournalEntry CREATE/MERGE first,
    so this path is unreachable there. Kept for unit tests and any pre-check
    tooling that still validates historical query shapes.
    """
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


def assert_general_write_allowed(query: str) -> None:
    """Reserve journal-chain mutations for ``append_journal_entry``.

    The generic writer remains available for ordinary post-append graph links
    (MATCH + MERGE). It must not bypass the chain protocol by creating,
    labeling, deleting, fully replacing, or rewiring journal-chain state.
    """
    query = query or ""
    if CALL_RE.search(query):
        raise ValueError(
            "write_neo4j_cypher does not allow CALL/APOC; use a dedicated MCP tool"
        )
    if JOURNAL_WRITE_RE.search(query) or FOLLOWS_WRITE_RE.search(query):
        raise ValueError(
            "write_neo4j_cypher cannot create or merge JournalEntry nodes or "
            "FOLLOWS relationships; use append_journal_entry instead"
        )
    if JOURNAL_CHAIN_NODE_RE.search(query) or PROTECTED_RELATIONSHIP_RE.search(query):
        raise ValueError(
            "write_neo4j_cypher cannot access JournalChain, HEAD, or FOLLOWS; "
            "use the dedicated journal MCP tools"
        )
    if _set_adds_protected_label(query):
        raise ValueError(
            "write_neo4j_cypher cannot add JournalEntry or JournalChain labels; "
            "use the dedicated journal MCP tools"
        )
    if DELETE_DETACH_REMOVE_RE.search(query):
        raise ValueError(
            "write_neo4j_cypher does not allow DELETE/DETACH/REMOVE; "
            "post-append mutations must be idempotent MATCH/MERGE only"
        )
    if FULL_NODE_SET_RE.search(query):
        raise ValueError(
            "write_neo4j_cypher does not allow full node replacement (`SET n = {...}`); "
            "set explicit non-reserved properties only"
        )
    if PROTECTED_TYPE_PREDICATE_RE.search(query):
        raise ValueError(
            "write_neo4j_cypher cannot target FOLLOWS or HEAD via type(); "
            "use the dedicated journal MCP tools"
        )
    if SET_CLAUSE_RE.search(query) and PROTECTED_JOURNAL_PROPERTY_RE.search(query):
        raise ValueError(
            "write_neo4j_cypher cannot mutate protected JournalEntry/chain fields; "
            "use append_journal_entry instead"
        )
    if SET_CLAUSE_RE.search(query) and DYNAMIC_PROPERTY_REFERENCE_RE.search(query):
        raise ValueError(
            "write_neo4j_cypher cannot use dynamic property writes that could "
            "target protected journal fields; set explicit properties only"
        )
    if CHAIN_VERSION_MUTATION_RE.search(query):
        raise ValueError(
            "write_neo4j_cypher cannot mutate JournalChain version/CAS state; "
            "use the dedicated journal MCP tools"
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
