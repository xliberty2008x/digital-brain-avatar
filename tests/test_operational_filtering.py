"""Operational boundary: retrieval exclusions and shared filter helpers."""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from digital_brain_mcp_cypher.quality import (  # noqa: E402
    OPERATIONAL_EXCLUSION_CYPHER,
    LEGACY_CONTROL_EXCLUSION_CYPHER,
    PROTECTED_QUALITY_LABELS,
    heavy_node_exclusion_predicate,
    is_operational_label,
    protected_quality_labels,
)


def test_operational_exclusion_fragment_is_central_and_stable():
    assert "Operational" in OPERATIONAL_EXCLUSION_CYPHER
    assert "NOT" in OPERATIONAL_EXCLUSION_CYPHER
    # Prefer label predicate form usable in WHERE clauses.
    assert (
        "n:Operational" in OPERATIONAL_EXCLUSION_CYPHER
        or "'Operational' IN labels(n)" in OPERATIONAL_EXCLUSION_CYPHER
    )


def test_legacy_exclusions_kept_until_migration_complete():
    legacy = LEGACY_CONTROL_EXCLUSION_CYPHER
    assert "Alias" in legacy
    assert "LearningLog" in legacy


def test_heavy_node_predicate_combines_operational_and_legacy():
    predicate = heavy_node_exclusion_predicate("n")
    assert "Operational" in predicate
    assert "Alias" in predicate
    assert "LearningLog" in predicate
    assert "JournalEntry" in predicate


def test_protected_quality_labels_cover_control_surface():
    labels = {label.lower() for label in protected_quality_labels()}
    for required in (
        "operational",
        "alias",
        "learninglog",
        "feedback",
        "qualitypayload",
        "decision",
        "entityprotection",
        "runevent",
        "effectreceipt",
        "dreamrun",
        "maintenancelease",
        "harnessgeneration",
        "agentpolicyrevision",
        "policyslot",
        "activationauthority",
        "deployment",
        "evaluationreceipt",
    ):
        assert required in labels


def test_is_operational_label_helper():
    assert is_operational_label("Operational") is True
    assert is_operational_label("operational") is True
    assert is_operational_label("Person") is False


def test_core_entity_service_uses_shared_exclusion_predicate():
    from digital_brain.services.core_entity_service import (
        HEAVY_NODE_EXCLUSION_PREDICATE,
        get_all_core_entities,
    )

    # digital_brain constant must match MCP quality helper (no circular import).
    assert HEAVY_NODE_EXCLUSION_PREDICATE == heavy_node_exclusion_predicate("n")
    source = pathlib.Path(get_all_core_entities.__code__.co_filename).read_text(
        encoding="utf-8"
    )
    assert "HEAVY_NODE_EXCLUSION_PREDICATE" in source
    assert "Operational" in source
    assert "Alias" in source
    assert "LearningLog" in source


def test_recent_entries_linked_entities_exclude_operational():
    from digital_brain.services import recent_entries_service

    source = pathlib.Path(recent_entries_service.__file__).read_text(encoding="utf-8")
    assert "Operational" in source


def test_init_quality_roles_deny_covers_all_protected_labels():
    """PROTECTED_QUALITY_LABELS ⊆ full DENY surface (CREATE/DELETE/SET PROPERTY/SET LABEL)."""
    import init_quality_roles as roles  # noqa: E402

    assert frozenset(roles.PROTECTED_LABELS) == PROTECTED_QUALITY_LABELS
    assert set(roles.DENY_PRIVILEGES) == {
        "CREATE",
        "DELETE",
        "SET PROPERTY",
        "SET LABEL",
    }

    cfg = {
        "database": "neo4j",
        "runtime_user": "digital_brain_runtime",
        "runtime_password": "x",
        "quality_user": "digital_brain_quality",
        "quality_password": "x",
        "uri": "bolt://localhost:7687",
    }
    statements = roles.build_statements(cfg)
    joined = "\n".join(statements)

    for label in PROTECTED_QUALITY_LABELS:
        assert (
            f"DENY CREATE ON GRAPH neo4j NODE {label} TO digital_brain_runtime"
            in joined
        ), f"missing CREATE DENY for {label}"
        assert (
            f"DENY DELETE ON GRAPH neo4j NODE {label} TO digital_brain_runtime"
            in joined
        ), f"missing DELETE DENY for {label}"
        assert (
            f"DENY SET PROPERTY {{*}} ON GRAPH neo4j NODE {label} "
            "TO digital_brain_runtime"
            in joined
        ), f"missing SET PROPERTY DENY for {label}"
        assert (
            f"DENY SET LABEL {label} ON GRAPH neo4j TO digital_brain_runtime"
            in joined
        ), f"missing SET LABEL DENY for {label}"

    # Previously incomplete surface — explicit regression pins.
    for label in ("QualityPayload", "Decision", "EntityProtection"):
        assert f"SET LABEL {label}" in joined
        assert f"NODE {label}" in joined


def test_init_quality_roles_cypher_file_matches_generator():
    """Companion .cypher file must be regenerable from the same label set."""
    import init_quality_roles as roles  # noqa: E402

    cypher_path = ROOT / "scripts" / "init-quality-roles.cypher"
    on_disk = cypher_path.read_text(encoding="utf-8")
    expected = roles.render_cypher_file()
    assert on_disk == expected, (
        "scripts/init-quality-roles.cypher is stale; run "
        "`uv run --group dev python scripts/init_quality_roles.py --write-cypher`"
    )

    # Every protected label appears under each privilege class in the file.
    for label in PROTECTED_QUALITY_LABELS:
        assert re.search(
            rf"DENY CREATE ON GRAPH \$database NODE {re.escape(label)} ",
            on_disk,
        )
        assert re.search(
            rf"DENY DELETE ON GRAPH \$database NODE {re.escape(label)} ",
            on_disk,
        )
        assert re.search(
            rf"DENY SET PROPERTY \{{\*\}} ON GRAPH \$database NODE "
            rf"{re.escape(label)} ",
            on_disk,
        )
        assert re.search(
            rf"DENY SET LABEL {re.escape(label)} ON GRAPH \$database ",
            on_disk,
        )
