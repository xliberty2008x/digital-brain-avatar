"""Query sanitizer: keep DELETE/MISSING rejection; never coach identity merges."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from digital_brain.callbacks.query_sanitizer import query_sanitizer_callback


def _tool(name: str = "write_neo4j_cypher"):
    return SimpleNamespace(name=name)


def _ctx():
    return SimpleNamespace(state={})


class QuerySanitizerTests(IsolatedAsyncioTestCase):
    async def test_blocks_detach_delete_with_missing_id(self) -> None:
        """Characterization: MCP-facing sanitizer still rejects MISSING DETACH DELETE."""
        args = {"query": 'MATCH (remove {id: "MISSING"}) DETACH DELETE remove'}
        result = await query_sanitizer_callback(_tool(), args, _ctx())
        self.assertIsNotNone(result)
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("MISSING", text.upper())
        self.assertIn("BLOCKED", text.upper())

    async def test_blocked_message_does_not_recommend_apoc_merge_or_detach_delete(self) -> None:
        args = {"query": 'MATCH (remove {id: "MISSING"}) DETACH DELETE remove'}
        result = await query_sanitizer_callback(_tool(), args, _ctx())
        self.assertIsNotNone(result)
        text = result["content"][0]["text"]
        upper = text.upper()
        self.assertNotIn("APOC", upper)
        self.assertNotIn("DETACH DELETE REMOVE", upper)
        # Must not teach a "corrected" identity-merge pattern
        self.assertNotIn("TRANSFER RELATIONSHIPS", upper)
        self.assertNotIn("apoc.create.relationship", text)

    async def test_allows_non_detach_queries_unchanged(self) -> None:
        args = {"query": "MATCH (p:Person {id: $id}) SET p.name = $name"}
        result = await query_sanitizer_callback(_tool(), args, _ctx())
        self.assertIsNone(result)

    async def test_detach_without_missing_id_not_rewritten_by_sanitizer(self) -> None:
        # Sanitizer only blocks MISSING-id patterns; generic DELETE rejection
        # lives in MCP query_tools (see test_local_mcp_query_tools).
        args = {"query": "MATCH (n {id: $id}) DETACH DELETE n"}
        result = await query_sanitizer_callback(_tool(), args, _ctx())
        self.assertIsNone(result)

    async def test_ignores_non_write_tools(self) -> None:
        args = {"query": 'MATCH (remove {id: "MISSING"}) DETACH DELETE remove'}
        result = await query_sanitizer_callback(_tool("read_neo4j_cypher"), args, _ctx())
        self.assertIsNone(result)
