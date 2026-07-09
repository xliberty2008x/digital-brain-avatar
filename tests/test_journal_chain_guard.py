from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from digital_brain.callbacks.journal_chain_guard import (
    journal_chain_guard_after_tool_callback,
    journal_chain_guard_before_tool_callback,
)
from digital_brain.callbacks.query_sanitizer import query_sanitizer_callback
from digital_brain.callbacks.combined_before_tool_callbacks import combined_before_tool_callback


def _tool():
    return SimpleNamespace(name="write_neo4j_cypher")


def _ctx(prev_id=None):
    state = {}
    if prev_id is not None:
        state["last_journal_entry_id"] = prev_id
    return SimpleNamespace(state=state)


class JournalChainGuardTests(IsolatedAsyncioTestCase):
    async def test_allows_literal_id_with_follows_chain(self) -> None:
        args = {
            "query": """
                MATCH (prev:JournalEntry {id: "prev-123"})
                CREATE (j:JournalEntry {
                    id: "j-1",
                    content: "ok",
                    embedding: $embedding
                })
                MERGE (j)-[:FOLLOWS]->(prev)
                RETURN j.id
            """,
            "params": {},
        }
        ctx = _ctx("prev-123")
        result = await journal_chain_guard_before_tool_callback(_tool(), args, ctx)
        self.assertIsNone(result)
        self.assertEqual(ctx.state["_pending_journal_entry_id"], "j-1")
        # Chain head not advanced until after successful write
        self.assertEqual(ctx.state["last_journal_entry_id"], "prev-123")

        await journal_chain_guard_after_tool_callback(
            _tool(), args, ctx, {"content": [{"type": "text", "text": '[{"id":"j-1"}]'}]}
        )
        self.assertEqual(ctx.state["last_journal_entry_id"], "j-1")
        self.assertNotIn("_pending_journal_entry_id", ctx.state)

    async def test_rejects_random_uuid_function_id(self) -> None:
        args = {
            "query": """
                MATCH (prev:JournalEntry {id: "prev-123"})
                CREATE (j:JournalEntry {id: randomUUID(), content: "x", embedding: $embedding})
                MERGE (j)-[:FOLLOWS]->(prev)
            """,
        }
        result = await journal_chain_guard_before_tool_callback(_tool(), args, _ctx("prev-123"))
        self.assertIsNotNone(result)
        self.assertTrue(result["isError"])
        self.assertIn("explicit `id`", result["content"][0]["text"])

    async def test_rejects_missing_chain_when_prev_known(self) -> None:
        args = {
            "query": """
                CREATE (j:JournalEntry {id: "j-2", content: "x", embedding: $embedding})
                RETURN j.id
            """,
        }
        result = await journal_chain_guard_before_tool_callback(_tool(), args, _ctx("prev-123"))
        self.assertIsNotNone(result)
        self.assertTrue(result["isError"])
        self.assertIn("chain link", result["content"][0]["text"])

    async def test_allows_first_entry_without_prev(self) -> None:
        args = {
            "query": """
                CREATE (j:JournalEntry {id: $journal_id, content: $content, embedding: $embedding})
                RETURN j.id
            """,
            "params": {"journal_id": "j-first", "content": "hello"},
        }
        ctx = _ctx(None)
        result = await journal_chain_guard_before_tool_callback(_tool(), args, ctx)
        self.assertIsNone(result)
        self.assertEqual(ctx.state["_pending_journal_entry_id"], "j-first")

    async def test_detects_relationship_chained_journal_create(self) -> None:
        args = {
            "query": """
                MATCH (p:Person {id: $pid})
                MATCH (prev:JournalEntry {id: $prev_id})
                MERGE (p)-[:WROTE]->(j:JournalEntry {id: $journal_id, content: $content, embedding: $embedding})
                MERGE (j)-[:FOLLOWS]->(prev)
            """,
            "params": {
                "pid": "person-1",
                "prev_id": "prev-123",
                "journal_id": "j-3",
                "content": "linked",
            },
        }
        result = await journal_chain_guard_before_tool_callback(_tool(), args, _ctx("prev-123"))
        self.assertIsNone(result)

    async def test_does_not_advance_chain_on_failed_write(self) -> None:
        args = {
            "query": """
                MATCH (prev:JournalEntry {id: "prev-123"})
                CREATE (j:JournalEntry {id: "j-fail", content: "x", embedding: $embedding})
                MERGE (j)-[:FOLLOWS]->(prev)
            """,
        }
        ctx = _ctx("prev-123")
        await journal_chain_guard_before_tool_callback(_tool(), args, ctx)
        await journal_chain_guard_after_tool_callback(
            _tool(),
            args,
            ctx,
            {
                "isError": True,
                "content": [{"type": "text", "text": "JournalEntry writes must pass embed_text"}],
            },
        )
        self.assertEqual(ctx.state["last_journal_entry_id"], "prev-123")

    async def test_combined_before_still_blocks_missing_id_detach_delete(self) -> None:
        args = {
            "query": 'MATCH (remove {id: "MISSING"}) DETACH DELETE remove',
        }
        # Sanitizer alone
        san = await query_sanitizer_callback(_tool(), args, _ctx())
        self.assertIsNotNone(san)
        self.assertTrue(san["isError"])
        # Combined path
        combined = await combined_before_tool_callback(_tool(), args, _ctx())
        self.assertIsNotNone(combined)
        self.assertTrue(combined["isError"])
        self.assertIn("MISSING", combined["content"][0]["text"])
