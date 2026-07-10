from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import aiohttp

from digital_brain.tools.mcp_client import (
    append_journal_entry,
    call_mcp_tool,
    execute_cypher,
    get_journal_append_receipt,
    get_journal_chain_head,
    McpWriteOutcomeUnknown,
)


class _FailingRequest:
    async def __aenter__(self):
        raise aiohttp.ClientConnectionError("offline")

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FailingSession:
    def __init__(self, *args, **kwargs):
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        self.calls += 1
        return _FailingRequest()


class McpClientTests(IsolatedAsyncioTestCase):
    async def test_execute_cypher_passes_embed_text_to_read_tool(self) -> None:
        with patch(
            "digital_brain.tools.mcp_client.call_mcp_tool",
            new=AsyncMock(return_value={"content": [{"type": "text", "text": '[{"id": "j-1"}]'}]}),
        ) as mocked_call:
            rows = await execute_cypher(
                "CALL db.index.vector.queryNodes('journal_entry_embedding_index', $limit, $embedding) "
                "YIELD node RETURN node.id AS id",
                {"limit": 5},
                embed_text="semantic query",
            )

        self.assertEqual(rows, [{"id": "j-1"}])
        mocked_call.assert_awaited_once_with(
            "read_neo4j_cypher",
            {
                "query": "CALL db.index.vector.queryNodes('journal_entry_embedding_index', $limit, $embedding) "
                "YIELD node RETURN node.id AS id",
                "params": {"limit": 5},
                "embed_text": "semantic query",
            },
        )

    async def test_append_uses_one_stable_payload_and_decodes_receipt(self) -> None:
        response = {
            "content": [
                {
                    "type": "text",
                    "text": '{"outcome":"created","append_key":"key-1","journal_id":"journal-key-1","version":2}',
                }
            ]
        }
        with patch(
            "digital_brain.tools.mcp_client.call_mcp_tool",
            new=AsyncMock(return_value=response),
        ) as mocked_call:
            receipt = await append_journal_entry(
                append_key="key-1",
                content="memory",
                timestamp="2026-07-09T00:00:00Z",
                mood="calm",
                expected_version=1,
                properties={"source": "test"},
            )

        self.assertEqual(receipt["outcome"], "created")
        mocked_call.assert_awaited_once_with(
            "append_journal_entry",
            {
                "append_key": "key-1",
                "content": "memory",
                "timestamp": "2026-07-09T00:00:00Z",
                "mood": "calm",
                "expected_version": 1,
                "properties": {"source": "test"},
            },
        )

    async def test_chain_reads_and_receipt_are_decoded(self) -> None:
        responses = [
            {"content": [{"type": "text", "text": '{"outcome":"ready","version":4}'}]},
            {"content": [{"type": "text", "text": '{"outcome":"replayed","append_key":"key-1"}'}]},
        ]
        with patch(
            "digital_brain.tools.mcp_client.call_mcp_tool",
            new=AsyncMock(side_effect=responses),
        ) as mocked_call:
            self.assertEqual((await get_journal_chain_head())["version"], 4)
            self.assertEqual((await get_journal_append_receipt("key-1"))["outcome"], "replayed")

        self.assertEqual(
            mocked_call.await_args_list[0].args,
            ("get_journal_chain_head", {}),
        )
        self.assertEqual(
            mocked_call.await_args_list[1].args,
            ("get_journal_append_receipt", {"append_key": "key-1"}),
        )

    async def test_transport_failure_on_generic_write_is_outcome_unknown_without_retry(self) -> None:
        session = _FailingSession()
        with patch(
            "digital_brain.tools.mcp_client.aiohttp.ClientSession",
            return_value=session,
        ):
            with self.assertRaises(McpWriteOutcomeUnknown):
                await call_mcp_tool("write_neo4j_cypher", {"query": "CREATE (n:Topic {id: 'x'})"})

        self.assertEqual(session.calls, 1)
