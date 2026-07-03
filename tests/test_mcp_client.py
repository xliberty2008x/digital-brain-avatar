from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from digital_brain.tools.mcp_client import execute_cypher


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
