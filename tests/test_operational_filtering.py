"""Operational boundary: retrieval exclusions and shared filter helpers."""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))

from digital_brain_mcp_cypher.quality import (  # noqa: E402
    OPERATIONAL_EXCLUSION_CYPHER,
    LEGACY_CONTROL_EXCLUSION_CYPHER,
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


def test_core_entity_service_query_excludes_operational():
    from digital_brain.services import core_entity_service

    source = pathlib.Path(core_entity_service.__file__).read_text(encoding="utf-8")
    assert "Operational" in source
    assert "NOT 'Operational' IN labels(n)" in source or "NOT n:Operational" in source
    # Legacy exclusions retained until migration.
    assert "Alias" in source
    assert "LearningLog" in source


def test_recent_entries_linked_entities_exclude_operational():
    from digital_brain.services import recent_entries_service

    source = pathlib.Path(recent_entries_service.__file__).read_text(encoding="utf-8")
    assert "Operational" in source
