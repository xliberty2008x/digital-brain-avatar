"""Quality/control plane: Operational boundary, constraints, and typed store.

Task 2 establishes the boundary, roles, and idempotent schema bootstrap.
Full Feedback/RunEvent sensor transactions land in Task 4; this module owns
the shared labels, exclusion fragments, quality driver store, and receipt
read surface used by model-facing tools.
"""

from __future__ import annotations

from typing import Any, Callable

# Every quality/control node carries Operational in addition to its specific
# label. Generic retrieval excludes Operational centrally.
OPERATIONAL_LABEL = "Operational"

# Specific control-plane labels protected from generic model-facing Cypher.
# Keep in sync with scripts/init-quality-roles.cypher DENY lists.
PROTECTED_QUALITY_LABELS: frozenset[str] = frozenset(
    {
        "Operational",
        "Alias",
        "LearningLog",
        "Feedback",
        "FeedbackLifecycleEvent",
        "QualityPayload",
        "EvidenceSnapshot",
        "Finding",
        "RunEvent",
        "DreamRun",
        "HarnessGeneration",
        "Deployment",
        "ExposureWindow",
        "Proposal",
        "EvaluationReceipt",
        "Decision",
        "EffectReceipt",
        "ActivationAuthority",
        "MaintenanceLease",
        "EntityProtection",
        "AgentPolicyRevision",
        "PolicySlot",
        "ChangeIntent",
        "PatchArtifact",
    }
)

# Cypher WHERE fragment: exclude Operational from BOOTSTRAP/heavy-node paths.
OPERATIONAL_EXCLUSION_CYPHER = "NOT n:Operational"

# Temporary legacy exclusions until Alias/LearningLog are fully backfilled with
# Operational via the reviewed migration (scripts/migrate_operational_labels.cypher).
LEGACY_CONTROL_EXCLUSION_CYPHER = (
    "NOT n:Alias AND NOT n:LearningLog AND NOT n:JournalEntry"
)

QUALITY_CONSTRAINTS = (
    """
    CREATE CONSTRAINT operational_feedback_id_unique IF NOT EXISTS
    FOR (n:Feedback) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT operational_run_event_id_unique IF NOT EXISTS
    FOR (n:RunEvent) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT operational_effect_receipt_id_unique IF NOT EXISTS
    FOR (n:EffectReceipt) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT operational_dream_run_id_unique IF NOT EXISTS
    FOR (n:DreamRun) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT operational_harness_generation_id_unique IF NOT EXISTS
    FOR (n:HarnessGeneration) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT operational_maintenance_lease_id_unique IF NOT EXISTS
    FOR (n:MaintenanceLease) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT operational_alias_id_unique IF NOT EXISTS
    FOR (n:Alias) REQUIRE n.id IS UNIQUE
    """,
)


def protected_quality_labels() -> frozenset[str]:
    """Return the canonical set of labels generic writers must not mutate."""
    return PROTECTED_QUALITY_LABELS


def is_operational_label(label: str) -> bool:
    return str(label).lower() == OPERATIONAL_LABEL.lower()


def heavy_node_exclusion_predicate(var: str = "n") -> str:
    """Shared heavy-node / BOOTSTRAP exclusion (Operational + legacy)."""
    return (
        f"NOT {var}:Operational "
        f"AND NOT {var}:Alias "
        f"AND NOT {var}:LearningLog "
        f"AND NOT {var}:JournalEntry"
    )


def labels_exclusion_list_predicate(var: str = "n") -> str:
    """Alternative form using labels() for older query styles."""
    return (
        f"NOT '{OPERATIONAL_LABEL}' IN labels({var}) "
        f"AND NOT 'Alias' IN labels({var}) "
        f"AND NOT 'LearningLog' IN labels({var}) "
        f"AND NOT 'JournalEntry' IN labels({var})"
    )


def _consume(result: Any) -> None:
    if hasattr(result, "consume"):
        result.consume()


def _run_one(runner: Any, query: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    result = runner.run(query, params or {})
    record = result.single()
    if record is None:
        return None
    if hasattr(record, "data"):
        return record.data()
    return dict(record)


class QualityStore:
    """Neo4j operations for the quality/control plane.

    Uses a dedicated quality credential (not the model-facing runtime role).
    ``driver_factory`` matches the server's ``_quality_driver`` helper.
    """

    def __init__(self, driver_factory: Callable[[], Any], database: str):
        self._driver_factory = driver_factory
        self._database = database

    def _with_session(self, operation: Callable[[Any], Any]) -> Any:
        with self._driver_factory() as driver:
            with driver.session(database=self._database) as session:
                return operation(session)

    def ensure_constraints(self) -> None:
        """Idempotent uniqueness constraints for quality/control records."""

        def operation(session: Any) -> None:
            for query in QUALITY_CONSTRAINTS:
                _consume(session.run(query))

        self._with_session(operation)

    def get_receipt(self, receipt_id: str) -> dict[str, Any]:
        """Look up a quality EffectReceipt (or stub) by stable id."""
        receipt_id = str(receipt_id or "").strip()
        if not receipt_id:
            raise ValueError("receipt_id must be a non-empty string")

        def operation(session: Any) -> dict[str, Any]:
            row = _run_one(
                session,
                """
                MATCH (r:Operational:EffectReceipt {id: $receipt_id})
                RETURN r.id AS receipt_id,
                       r.request_fingerprint AS request_fingerprint,
                       r.status AS status,
                       r.created_at AS created_at
                LIMIT 1
                """,
                {"receipt_id": receipt_id},
            )
            if row is None:
                return {"outcome": "not_found", "receipt_id": receipt_id}
            return {
                "outcome": "ok",
                "receipt_id": row.get("receipt_id"),
                "request_fingerprint": row.get("request_fingerprint"),
                "status": row.get("status"),
                "created_at": row.get("created_at"),
            }

        return self._with_session(operation)

    def ping(self) -> dict[str, Any]:
        """Health probe for the quality credential."""

        def operation(session: Any) -> dict[str, Any]:
            row = _run_one(session, "RETURN 1 AS ok")
            return {"outcome": "ok", "ok": bool(row and row.get("ok") == 1)}

        return self._with_session(operation)
