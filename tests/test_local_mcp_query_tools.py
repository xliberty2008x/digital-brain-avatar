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


def test_general_write_rejects_full_node_replacement_and_multi_label_journal_mint():
    for query in (
        "MATCH (j:JournalEntry {id: $id}) SET j = {content: 'hack', embedding: null}",
        "MATCH (j:JournalEntry {id: $id}) SET j = $props",
        "CREATE (n {id: $id}) SET n:Person:JournalEntry",
        "CREATE (n {key: 'primary'}) SET n:Foo:JournalChain",
        "MATCH (n) SET n:`Person`:`JournalEntry`",
    ):
        try:
            assert_general_write_allowed(query)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected rejection for {query}")


def test_general_write_rejects_destructive_and_unlabeled_chain_bypasses():
    for query in (
        "MATCH (n {id: $id}) DETACH DELETE n",
        "MATCH (n) WHERE n.append_key = $k DETACH DELETE n",
        "MATCH ()-[r]->() WHERE type(r) = 'FOLLOWS' DELETE r",
        "MATCH ()-[r]->() WHERE type(r) = 'HEAD' DELETE r",
        "MATCH (n) WHERE n.append_key IS NOT NULL SET n.content = 'x'",
        "MATCH (j:JournalEntry) REMOVE j:JournalEntry",
        "MATCH (n) WHERE n.key = 'primary' SET n.version = 999",
        "MATCH (j:JournalEntry {id: $id}) SET j['append_key'] = $k",
    ):
        try:
            assert_general_write_allowed(query)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected rejection for {query}")


def test_general_write_rejects_map_merge_and_alias_full_node_replacement():
    for query in (
        "MATCH (j:JournalEntry {id: $id}) SET j += {content: 'hack', embedding: null}",
        "MATCH (j:JournalEntry {id: $id}) SET j += {`append_key`: 'x'}",
        "MATCH (j:JournalEntry {id: $id}) SET j += $props",
        "MATCH (j:JournalEntry {id: $id}), (m) SET j = m",
        "MATCH (j:JournalEntry {id: $id}) SET j = properties(m)",
        "MATCH (n) SET n.version = 999",
        "DROP CONSTRAINT journal_entry_append_key_unique IF EXISTS",
        "DROP INDEX journal_entry_embedding_index",
        "MATCH ()-[r]->() WHERE type(r) IN $types DELETE r",
    ):
        try:
            assert_general_write_allowed(query)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected rejection for {query}")


def test_general_write_allows_idempotent_post_append_links_and_non_reserved_sets():
    assert_general_write_allowed(
        "MATCH (j:JournalEntry {id: $journal_id}) "
        "MERGE (e:Event {id: $append_key + '-event-1'}) "
        "MERGE (j)-[:DOCUMENTS]->(e)"
    )
    assert_general_write_allowed(
        "MATCH (j:JournalEntry {id: $journal_id}) SET j.summary = 'ok'"
    )
    assert_general_write_allowed(
        "MATCH (j:JournalEntry {id: $journal_id}) SET j += {summary: 'ok', source: 'buddy'}"
    )
    assert_general_write_allowed(
        "CREATE VECTOR INDEX journal_entry_embedding_index IF NOT EXISTS "
        "FOR (j:JournalEntry) ON (j.embedding)"
    )


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


# ---------------------------------------------------------------------------
# Operational / quality control write guards (Task 2)
# ---------------------------------------------------------------------------

PROTECTED_QUALITY_WRITE_CASES = (
    # CREATE / MERGE protected labels
    "CREATE (n:Operational {id: $id})",
    "CREATE (n:Operational:Feedback {id: $id})",
    "CREATE (f:Feedback {id: $id})",
    "MERGE (a:Alias {from_name: $name})",
    "CREATE (l:LearningLog {id: $id})",
    "CREATE (r:EffectReceipt {id: $id})",
    "CREATE (p:AgentPolicyRevision {id: $id})",
    "CREATE (s:PolicySlot {slot: 'active'})",
    "CREATE (m:MaintenanceLease {id: $id})",
    "CREATE (d:DreamRun {id: $id})",
    "CREATE (h:HarnessGeneration {id: $id})",
    "CREATE (e:RunEvent {id: $id})",
    "CREATE (x:EvaluationReceipt {id: $id})",
    "CREATE (y:ActivationAuthority {id: $id})",
    "CREATE (z:Deployment {id: $id})",
    # SET label
    "MATCH (n {id: $id}) SET n:Operational",
    "MATCH (n) SET n:Alias",
    "MATCH (n) SET n:Person:LearningLog",
    "MATCH (n) SET n:`Operational`",
    "CREATE (n {id: $id}) SET n:Feedback:Operational",
    # Dynamic label / property smuggling
    "MATCH (n) SET n[$label] = true",
    "MATCH (n) SET n['id'] = $id",
    "MATCH (n) SET n:$($label)",
    "MATCH (n) SET n:$(label)",
    "MATCH (n) SET n:$label",
    "MATCH (n) SET n:Person:$($extra)",
    "MATCH (n) SET n : $label",
    # Full node replacement of control records
    "MATCH (a:Alias {id: $id}) SET a = $props",
    "MATCH (o:Operational) SET o = {summary: 'x'}",
    "MATCH (a:Alias) SET a += $props",
    # Relationship-based access to protected control records
    "MATCH (p:Person)-[r]->(a:Alias) SET a.to_name = $name",
    "MATCH (p:Person)-[r]->(o:Operational) SET o.stage = 'hack'",
    "MATCH (j:JournalEntry), (a:Alias) MERGE (j)-[:USES_ALIAS]->(a)",
    "MATCH (n)-[r:SUPPORTED_BY]->(f:Finding) SET f.summary = 'x'",
    "MATCH ()-[r]->(e:EffectReceipt) SET e.status = 'forged'",
    # Escaped / whitespace variants
    "CREATE (a : `Alias` {from_name: $name})",
    "MERGE (l:`LearningLog` {id: $id})",
)


def test_general_write_rejects_protected_quality_control_mutations():
    for query in PROTECTED_QUALITY_WRITE_CASES:
        try:
            assert_general_write_allowed(query)
        except ValueError as exc:
            message = str(exc).lower()
            assert any(
                token in message
                for token in (
                    "operational",
                    "quality",
                    "control",
                    "alias",
                    "learninglog",
                    "protected",
                    "receipt",
                    "policy",
                    "dynamic",
                    "full node",
                    "delete",
                    "map",
                )
            ), f"unexpected message for {query!r}: {exc}"
        else:
            raise AssertionError(f"expected protected quality rejection for {query}")


def test_general_write_still_allows_life_graph_post_append_links():
    assert_general_write_allowed(
        "MATCH (j:JournalEntry {id: $journal_id}) "
        "MERGE (p:Person {id: $person_id}) "
        "ON CREATE SET p.name = $name "
        "MERGE (j)-[:MENTIONS]->(p)"
    )
    assert_general_write_allowed(
        "MATCH (j:JournalEntry {id: $journal_id}) "
        "MERGE (e:Event {id: $event_id}) "
        "MERGE (j)-[:DOCUMENTS]->(e)"
    )
    assert_general_write_allowed(
        "MATCH (j:JournalEntry {id: $journal_id}) SET j.summary = 'ok'"
    )
