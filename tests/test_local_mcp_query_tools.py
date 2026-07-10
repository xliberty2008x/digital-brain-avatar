import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))

from digital_brain_mcp_cypher.query_tools import (  # noqa: E402
    assert_general_write_allowed,
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


def test_validate_embedding_usage_allows_vector_index_ddl_without_embed_text():
    query = """
    CREATE VECTOR INDEX journal_entry_embedding_index IF NOT EXISTS
    FOR (j:JournalEntry) ON (j.embedding)
    OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}}
    """
    validate_embedding_usage(query, None)


def test_validate_embedding_usage_rejects_whitespace_only_embed_text():
    query = "CREATE (j:JournalEntry {id: $id, content: $content, embedding: $embedding}) RETURN j"
    try:
        validate_embedding_usage(query, "   ")
    except ValueError as exc:
        assert "embed_text" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_general_write_rejects_anonymous_journal_entry_creation():
    try:
        assert_general_write_allowed("CREATE (:JournalEntry {id: $id})")
    except ValueError as exc:
        assert "append_journal_entry" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_general_write_rejects_relationship_chained_journal_merge():
    try:
        assert_general_write_allowed(
            "MATCH (p:Person {id: $person_id}) "
            "MERGE (p)-[:WROTE]->(j:JournalEntry {id: $id})"
        )
    except ValueError as exc:
        assert "JournalEntry" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_general_write_rejects_raw_follows_create_and_merge():
    for query in (
        "CREATE (new:JournalEntry)-[:FOLLOWS]->(old:JournalEntry)",
        "MATCH (new), (old) MERGE (new)-[r : FOLLOWS]->(old)",
    ):
        try:
            assert_general_write_allowed(query)
        except ValueError as exc:
            assert "FOLLOWS" in str(exc)
        else:
            raise AssertionError(f"expected ValueError for {query}")


def test_general_write_allows_journal_reads_and_vector_index_ddl():
    assert_general_write_allowed("MATCH (j:JournalEntry) RETURN j.id")
    assert_general_write_allowed(
        "CREATE VECTOR INDEX journal_entry_embedding_index IF NOT EXISTS "
        "FOR (j:JournalEntry) ON (j.embedding)"
    )


def test_general_write_rejects_escaped_labels_and_chain_protocol_mutations():
    for query in (
        "CREATE (j:`JournalEntry` {id: $id})",
        "CREATE (a)-[:`FOLLOWS`]->(b)",
        "CREATE (chain:`JournalChain` {key: 'primary'})",
        "MATCH (j) SET j:`JournalEntry`",
        "MATCH (j:JournalEntry {id: $id}) SET j.append_key = $append_key",
        "MATCH (chain:JournalChain {key: 'primary'}), (j:JournalEntry) "
        "CREATE (chain)-[:HEAD]->(j)",
    ):
        try:
            assert_general_write_allowed(query)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected protected-write rejection for {query}")


def test_read_only_rejects_dynamic_procedure_escape_hatch_but_allows_vector_search():
    try:
        assert_read_only("CALL apoc.cypher.run($query, {}) YIELD value RETURN value")
    except ValueError as exc:
        assert "only allows CALL db.index.vector.queryNodes" in str(exc)
    else:
        raise AssertionError("expected dynamic procedure rejection")

    assert_read_only(
        "CALL db.index.vector.queryNodes('journal_entry_embedding_index', $limit, $embedding) "
        "YIELD node RETURN node.id"
    )
