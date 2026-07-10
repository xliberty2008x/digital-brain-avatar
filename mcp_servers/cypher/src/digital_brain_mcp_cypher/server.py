"""FastMCP server exposing Neo4j Cypher tools for Digital Brain."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any

from fastmcp.server import FastMCP
from mcp.types import ToolAnnotations
from neo4j import GraphDatabase
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from .embeddings import (
    EmbeddingConfig,
    EmbeddingRequestError,
    embed_text as generate_embedding,
)
from .journal import (
    PRIMARY_JOURNAL_CHAIN_KEY,
    JournalStore,
    build_append_request,
    replay_or_key_conflict,
)
from .query_tools import (
    assert_general_write_allowed,
    assert_read_only,
    serialize_records,
    validate_embedding_usage,
    with_embedding_param,
)


mcp = FastMCP("digital-brain-mcp-cypher")

_JOURNAL_SCHEMA_LOCK = threading.Lock()
_journal_schema_ready = False
JOURNAL_EMBEDDING_DIMENSIONS = 1024


def _neo4j_uri() -> str:
    return os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL") or "bolt://neo4j:7687"


def _neo4j_auth() -> tuple[str, str]:
    return (
        os.getenv("NEO4J_USERNAME", "neo4j"),
        os.getenv("NEO4J_PASSWORD", "password"),
    )


def _neo4j_database() -> str:
    return os.getenv("NEO4J_DATABASE", "neo4j")


def _driver():
    return GraphDatabase.driver(_neo4j_uri(), auth=_neo4j_auth())


def _run_cypher(query: str, params: dict[str, Any] | None, write: bool) -> list[dict[str, Any]]:
    with _driver() as driver:
        with driver.session(database=_neo4j_database()) as session:
            result = session.run(query, params or {})
            records = serialize_records(list(result))
            summary = result.consume()
            if write and not records:
                counters = summary.counters
                return [
                    {
                        "_contains_updates": bool(summary.counters.contains_updates),
                        "nodes_created": counters.nodes_created,
                        "nodes_deleted": counters.nodes_deleted,
                        "relationships_created": counters.relationships_created,
                        "relationships_deleted": counters.relationships_deleted,
                        "properties_set": counters.properties_set,
                        "labels_added": counters.labels_added,
                        "labels_removed": counters.labels_removed,
                        "indexes_added": counters.indexes_added,
                        "indexes_removed": counters.indexes_removed,
                        "constraints_added": counters.constraints_added,
                        "constraints_removed": counters.constraints_removed,
                    }
                ]
            return records


def _journal_store() -> JournalStore:
    return JournalStore(_driver, _neo4j_database())


def _ensure_journal_schema() -> None:
    """Create only the new safe uniqueness constraints once per process."""
    global _journal_schema_ready
    if _journal_schema_ready:
        return
    with _JOURNAL_SCHEMA_LOCK:
        if _journal_schema_ready:
            return
        _journal_store().ensure_constraints()
        _journal_schema_ready = True


def _readiness() -> tuple[bool, dict[str, str]]:
    """Check both required dependencies without exposing upstream diagnostics."""
    try:
        rows = _run_cypher("RETURN 1 AS ok", None, write=False)
        if not rows or rows[0].get("ok") != 1:
            return False, {"status": "not_ready", "reason": "neo4j_unavailable"}
    except Exception:  # Neo4j errors can include credentials and host details.
        return False, {"status": "not_ready", "reason": "neo4j_unavailable"}

    try:
        embedding = generate_embedding("digital-brain readiness probe")
        config = EmbeddingConfig.from_env()
        if (
            config.dimensions != JOURNAL_EMBEDDING_DIMENSIONS
            or embedding is None
            or len(embedding) != JOURNAL_EMBEDDING_DIMENSIONS
        ):
            return False, {"status": "not_ready", "reason": "embedding_invalid"}
    except EmbeddingRequestError as exc:
        reason = {
            "timeout": "embedding_timeout",
            "oom": "embedding_oom",
            "network": "embedding_unavailable",
            "http_error": "embedding_unavailable",
            "response_error": "embedding_unavailable",
            "invalid_response": "embedding_invalid",
        }.get(exc.reason, "embedding_unavailable")
        return False, {"status": "not_ready", "reason": reason}
    except (TimeoutError, ValueError):
        return False, {"status": "not_ready", "reason": "embedding_invalid"}
    except Exception:
        return False, {"status": "not_ready", "reason": "embedding_unavailable"}

    return True, {"status": "ready"}


@mcp.custom_route("/livez", methods=["GET"], include_in_schema=False)
async def livez(_: Request) -> JSONResponse:
    """Return success once the MCP process itself has started."""
    return JSONResponse({"status": "live"})


@mcp.custom_route("/readyz", methods=["GET"], include_in_schema=False)
async def readyz(_: Request) -> JSONResponse:
    """Return success only when Neo4j and the configured embedder are usable."""
    # Both the Neo4j driver and urllib-based Ollama client are synchronous.
    # Keep their bounded (20s by default) probe off FastMCP's event loop.
    ready, payload = await asyncio.to_thread(_readiness)
    return JSONResponse(payload, status_code=200 if ready else 503)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Neo4j Schema",
        description="Inspect labels, relationship types, and property keys.",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def get_neo4j_schema(sample_size: int | None = Field(default=100, ge=1, le=1000)) -> str:
    sample_size = sample_size or 100
    query = """
    CALL apoc.meta.schema({sample: $sample_size})
    YIELD value
    RETURN value
    """
    rows = _run_cypher(query, {"sample_size": sample_size}, write=False)
    if rows and "value" in rows[0]:
        return json.dumps(rows[0]["value"], ensure_ascii=False, default=str)
    return "{}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="Read Neo4j Cypher",
        description="Execute a read-only Cypher query.",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,
    )
)
def read_neo4j_cypher(
    query: str = Field(..., description="Read-only Cypher query"),
    params: dict[str, Any] | None = Field(default=None, description="Cypher parameters"),
    embed_text: str | None = Field(default=None, description="Text to embed into `$embedding`"),
) -> str:
    assert_read_only(query)
    embedding = generate_embedding(embed_text)
    rows = _run_cypher(query, with_embedding_param(params, embedding), write=False)
    return json.dumps(rows, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Write Neo4j Cypher",
        description="Execute a write Cypher query.",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
    )
)
def write_neo4j_cypher(
    query: str = Field(
        ...,
        description=(
            "Write Cypher for post-append graph links only (MATCH/MERGE). "
            "Cannot create JournalEntry/FOLLOWS/HEAD/JournalChain or mutate "
            "protected journal fields; use append_journal_entry for the chain."
        ),
    ),
    params: dict[str, Any] | None = Field(default=None, description="Cypher parameters"),
    embed_text: str | None = Field(default=None, description="Text to embed into `$embedding`"),
) -> str:
    assert_general_write_allowed(query)
    validate_embedding_usage(query, embed_text)
    embedding = generate_embedding(embed_text)
    rows = _run_cypher(query, with_embedding_param(params, embedding), write=True)
    return json.dumps(rows, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Journal Chain Head",
        description="Read the current primary JournalChain version and head metadata.",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def get_journal_chain_head() -> str:
    """Return the current chain version without exposing journal content."""
    payload = _journal_store().get_chain_head(PRIMARY_JOURNAL_CHAIN_KEY)
    return json.dumps(payload, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Journal Append Receipt",
        description="Look up a journal append by its UUID idempotency key.",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def get_journal_append_receipt(
    append_key: str = Field(..., description="UUID generated once for the append operation"),
) -> str:
    """Return a receipt that callers can use after an ambiguous timeout."""
    payload = _journal_store().get_receipt(append_key)
    return json.dumps(payload, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Bootstrap Journal Chain",
        description="Initialize the protected primary JournalChain from a reviewed legacy head or an empty graph.",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def bootstrap_journal_chain(
    head_element_id: str | None = Field(
        default=None,
        description="Reviewed legacy JournalEntry elementId; required unless empty=True",
    ),
    empty: bool = Field(
        default=False,
        description="Only for a graph that contains no JournalEntry nodes",
    ),
) -> str:
    """Bootstrap the chain through a dedicated, non-generic mutation path."""
    _ensure_journal_schema()
    payload = _journal_store().bootstrap(
        head_element_id=head_element_id,
        empty=empty,
        chain_key=PRIMARY_JOURNAL_CHAIN_KEY,
    )
    return json.dumps(payload, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Append Journal Entry",
        description="Atomically append one embedded JournalEntry to the primary chain.",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def append_journal_entry(
    append_key: str = Field(..., description="UUID generated once before the first attempt"),
    content: str = Field(..., description="Non-empty journal entry content to embed"),
    timestamp: str = Field(..., description="Entry timestamp"),
    expected_version: int = Field(..., ge=0, description="Version returned by get_journal_chain_head"),
    mood: str | None = Field(default=None, description="Optional entry mood"),
    properties: dict[str, Any] | None = Field(
        default=None,
        description="Optional additional flat Neo4j properties; reserved journal fields are rejected",
    ),
) -> str:
    """Append with compare-and-swap and idempotency semantics.

    A receipt lookup happens before embedding, and the embedding happens before
    entering the Neo4j write transaction. This keeps retry reconciliation fast
    and never holds a JournalChain lock while Ollama is running.
    """
    request = build_append_request(
        append_key=append_key,
        content=content,
        timestamp=timestamp,
        mood=mood,
        expected_version=expected_version,
        properties=properties,
    )
    store = _journal_store()

    existing_receipt = store.find_receipt(request.append_key)
    if existing_receipt is not None:
        return json.dumps(
            replay_or_key_conflict(existing_receipt, request),
            ensure_ascii=False,
            default=str,
        )

    chain = store.get_chain_head(PRIMARY_JOURNAL_CHAIN_KEY)
    if chain["outcome"] != "ok":
        chain["append_key"] = request.append_key
        return json.dumps(chain, ensure_ascii=False, default=str)

    _ensure_journal_schema()
    embedding = generate_embedding(request.content)
    if embedding is None:
        # build_append_request already rejects blank content; this protects the
        # invariant if an embedding provider is replaced in-process.
        raise RuntimeError("Journal append did not receive an embedding")
    payload = store.append(request.with_embedding(embedding), PRIMARY_JOURNAL_CHAIN_KEY)
    return json.dumps(payload, ensure_ascii=False, default=str)


def main() -> None:
    transport = os.getenv("NEO4J_TRANSPORT", "streamable-http")
    if transport == "stdio":
        mcp.run()
        return
    mcp.run(
        transport=transport,
        host=os.getenv("NEO4J_MCP_SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("NEO4J_MCP_SERVER_PORT", "8000")),
        path=os.getenv("NEO4J_MCP_SERVER_PATH", "/api/mcp/"),
    )


if __name__ == "__main__":
    main()
