import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))

from digital_brain_mcp_cypher.query_tools import (  # noqa: E402
    assert_read_only,
    validate_embedding_usage,
    with_embedding_param,
)
from digital_brain_mcp_cypher.embeddings import _validate_dimensions  # noqa: E402


def test_with_embedding_param_injects_embedding_without_mutating_original():
    params = {"id": "j-1"}
    merged = with_embedding_param(params, [0.1, 0.2])

    assert merged == {"id": "j-1", "embedding": [0.1, 0.2]}
    assert params == {"id": "j-1"}


def test_validate_embedding_usage_rejects_journal_write_without_embedding_param():
    query = "CREATE (j:JournalEntry {id: $id, content: $content}) RETURN j"

    try:
        validate_embedding_usage(query, "content")
    except ValueError as exc:
        assert "$embedding" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_validate_embedding_usage_allows_journal_write_with_embedding_param():
    query = "CREATE (j:JournalEntry {id: $id, content: $content, embedding: $embedding}) RETURN j"

    validate_embedding_usage(query, "content")


def test_read_only_rejects_mutating_cypher():
    try:
        assert_read_only("MATCH (n) SET n.name = 'x' RETURN n")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_validate_dimensions_rejects_mismatched_length():
    try:
        _validate_dimensions([1, 2, 3, 4], 2)
    except ValueError as exc:
        assert "dimension mismatch" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    assert _validate_dimensions([1, 2], 2) == [1.0, 2.0]


def test_validate_embedding_usage_rejects_journal_write_missing_embed_text():
    query = "CREATE (j:JournalEntry {id: $id, content: $content}) RETURN j"

    try:
        validate_embedding_usage(query, None)
    except ValueError as exc:
        assert "embed_text" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_validate_embedding_usage_allows_non_journal_write_without_embed_text():
    query = "CREATE (p:Person {id: $id, name: $name}) RETURN p"

    validate_embedding_usage(query, None)


def test_validate_embedding_usage_rejects_relationship_chained_journal_write_missing_embed_text():
    query = (
        "MATCH (p:Person {id: $pid}) "
        "MERGE (p)-[:WROTE]->(j:JournalEntry {id: $id, content: $content})"
    )

    try:
        validate_embedding_usage(query, None)
    except ValueError as exc:
        assert "embed_text" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_validate_embedding_usage_rejects_whitespace_before_label_journal_write_missing_embed_text():
    query = "CREATE (j : JournalEntry {id: $id, content: $content}) RETURN j"

    try:
        validate_embedding_usage(query, None)
    except ValueError as exc:
        assert "embed_text" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_validate_embedding_usage_allows_unrelated_journal_read_without_embed_text():
    query = "MATCH (j:JournalEntry) RETURN j"

    validate_embedding_usage(query, None)
