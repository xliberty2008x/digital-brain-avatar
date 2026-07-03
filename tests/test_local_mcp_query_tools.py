import pathlib
import sys
from unittest import TestCase

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))

from digital_brain_mcp_cypher.query_tools import (  # noqa: E402
    assert_read_only,
    validate_embedding_usage,
    with_embedding_param,
)


class LocalMcpQueryToolsTests(TestCase):
    def test_with_embedding_param_injects_embedding_without_mutating_original(self) -> None:
        params = {"id": "j-1"}
        merged = with_embedding_param(params, [0.1, 0.2])

        self.assertEqual(merged, {"id": "j-1", "embedding": [0.1, 0.2]})
        self.assertEqual(params, {"id": "j-1"})

    def test_validate_embedding_usage_rejects_journal_write_without_embedding_param(self) -> None:
        query = "CREATE (j:JournalEntry {id: $id, content: $content}) RETURN j"

        with self.assertRaisesRegex(ValueError, r"\$embedding"):
            validate_embedding_usage(query, "content")

    def test_validate_embedding_usage_allows_journal_write_with_embedding_param(self) -> None:
        query = "CREATE (j:JournalEntry {id: $id, content: $content, embedding: $embedding}) RETURN j"

        validate_embedding_usage(query, "content")

    def test_read_only_rejects_mutating_cypher(self) -> None:
        with self.assertRaises(ValueError):
            assert_read_only("MATCH (n) SET n.name = 'x' RETURN n")
