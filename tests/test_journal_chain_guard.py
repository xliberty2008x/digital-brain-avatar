from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from digital_brain.callbacks.combined_before_tool_callbacks import combined_before_tool_callback
from digital_brain.callbacks.journal_chain_guard import (
    is_raw_journal_mutation,
    journal_chain_guard_after_tool_callback,
    journal_chain_guard_before_tool_callback,
)
from digital_brain.callbacks.query_sanitizer import query_sanitizer_callback


def _tool():
    return SimpleNamespace(name="write_neo4j_cypher")


def _ctx():
    return SimpleNamespace(state={})


class JournalChainGuardTests(IsolatedAsyncioTestCase):
    async def test_rejects_raw_journal_create(self) -> None:
        args = {
            "query": "CREATE (j:JournalEntry {id: 'j-1', embedding: $embedding}) RETURN j",
        }
        result = await journal_chain_guard_before_tool_callback(_tool(), args, _ctx())
        self.assertIsNotNone(result)
        self.assertTrue(result["isError"])
        self.assertIn("append_journal_entry", result["content"][0]["text"])

    async def test_rejects_anonymous_journal_merge(self) -> None:
        args = {"query": "MERGE (:JournalEntry {id: $id})"}
        result = await journal_chain_guard_before_tool_callback(_tool(), args, _ctx())
        self.assertIsNotNone(result)
        self.assertTrue(result["isError"])

    async def test_rejects_generic_follows_mutation(self) -> None:
        args = {
            "query": "MATCH (a), (b) MERGE (a)-[:FOLLOWS]->(b)",
        }
        result = await journal_chain_guard_before_tool_callback(_tool(), args, _ctx())
        self.assertIsNotNone(result)
        self.assertTrue(result["isError"])

    async def test_allows_idempotent_post_append_link(self) -> None:
        args = {
            "query": "MATCH (j:JournalEntry {id: $journal_id}), (p:Person {id: $person_id}) "
            "MERGE (j)-[:MENTIONS]->(p)",
        }
        result = await journal_chain_guard_before_tool_callback(_tool(), args, _ctx())
        self.assertIsNone(result)

    async def test_after_callback_is_noop(self) -> None:
        ctx = _ctx()
        result = await journal_chain_guard_after_tool_callback(_tool(), {}, ctx, {"content": []})
        self.assertIsNone(result)
        self.assertEqual(ctx.state, {})

    async def test_combined_before_still_blocks_missing_id_detach_delete(self) -> None:
        args = {"query": 'MATCH (remove {id: "MISSING"}) DETACH DELETE remove'}
        sanitizer = await query_sanitizer_callback(_tool(), args, _ctx())
        self.assertIsNotNone(sanitizer)
        combined = await combined_before_tool_callback(_tool(), args, _ctx())
        self.assertIsNotNone(combined)
        self.assertTrue(combined["isError"])


def test_detection_ignores_vector_index_ddl() -> None:
    assert not is_raw_journal_mutation(
        "CREATE VECTOR INDEX journal_entry_embedding_index IF NOT EXISTS "
        "FOR (j:JournalEntry) ON (j.embedding)"
    )
