"""FastMCP server exposing Neo4j Cypher tools for Digital Brain."""

from __future__ import annotations

import json
import os
from typing import Any

from fastmcp.server import FastMCP
from mcp.types import ToolAnnotations
from neo4j import GraphDatabase
from pydantic import Field

from .embeddings import embed_text as generate_embedding
from .query_tools import (
    assert_read_only,
    serialize_records,
    validate_embedding_usage,
    with_embedding_param,
)


mcp = FastMCP("digital-brain-mcp-cypher")


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
    query: str = Field(..., description="Write Cypher query"),
    params: dict[str, Any] | None = Field(default=None, description="Cypher parameters"),
    embed_text: str | None = Field(default=None, description="Text to embed into `$embedding`"),
) -> str:
    validate_embedding_usage(query, embed_text)
    embedding = generate_embedding(embed_text)
    rows = _run_cypher(query, with_embedding_param(params, embedding), write=True)
    return json.dumps(rows, ensure_ascii=False, default=str)


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
