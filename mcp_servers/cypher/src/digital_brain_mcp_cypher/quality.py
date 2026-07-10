"""Quality/control plane: Operational boundary, constraints, and typed store.

Task 2 establishes the boundary, roles, and idempotent schema bootstrap.
HarnessGeneration pin/record lands in Task 3; full Feedback/RunEvent sensor
transactions land in Task 4. This module owns the shared labels, exclusion
fragments, quality driver store, harness generation receipts, and receipt
read surface used by model-facing tools.
"""

from __future__ import annotations

from typing import Any, Callable

# Every quality/control node carries Operational in addition to its specific
# label. Generic retrieval excludes Operational centrally.
OPERATIONAL_LABEL = "Operational"

# Specific control-plane labels protected from generic model-facing Cypher.
# Single source of truth for Neo4j DENY coverage: scripts/init_quality_roles.py
# imports this set and generates CREATE/DELETE/SET PROPERTY/SET LABEL denies
# for every label (regenerate scripts/init-quality-roles.cypher via --write-cypher).
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

    def get_harness_generation(self, generation_id: str) -> dict[str, Any]:
        """Read back a HarnessGeneration for pin reconciliation."""
        generation_id = str(generation_id or "").strip()
        if not generation_id:
            raise ValueError("generation_id must be a non-empty string")

        def operation(session: Any) -> dict[str, Any]:
            row = _run_one(
                session,
                """
                MATCH (g:Operational:HarnessGeneration {id: $generation_id})
                RETURN g.id AS id,
                       g.core_commit AS core_commit,
                       g.core_tree_digest AS core_tree_digest,
                       g.dirty_state_digest AS dirty_state_digest,
                       g.plugin_version AS plugin_version,
                       g.soul_sha AS soul_sha,
                       g.overlay_manifest_digest AS overlay_manifest_digest,
                       g.policy_digest AS policy_digest,
                       g.mcp_version AS mcp_version,
                       g.model_id AS model_id,
                       g.schema_version AS schema_version,
                       g.taxonomy_version AS taxonomy_version,
                       g.request_fingerprint AS request_fingerprint,
                       g.created_at AS created_at
                LIMIT 1
                """,
                {"generation_id": generation_id},
            )
            if row is None:
                return {"outcome": "not_found", "generation_id": generation_id}
            return {
                "outcome": "ok",
                "generation_id": row.get("id"),
                "id": row.get("id"),
                "core_commit": row.get("core_commit"),
                "core_tree_digest": row.get("core_tree_digest"),
                "dirty_state_digest": row.get("dirty_state_digest"),
                "plugin_version": row.get("plugin_version"),
                "soul_sha": row.get("soul_sha"),
                "overlay_manifest_digest": row.get("overlay_manifest_digest"),
                "policy_digest": row.get("policy_digest"),
                "mcp_version": row.get("mcp_version"),
                "model_id": row.get("model_id"),
                "schema_version": row.get("schema_version"),
                "taxonomy_version": row.get("taxonomy_version"),
                "request_fingerprint": row.get("request_fingerprint"),
                "created_at": row.get("created_at"),
            }

        return self._with_session(operation)

    def record_harness_generation(self, generation: dict[str, Any]) -> dict[str, Any]:
        """Idempotent create of Operational:HarnessGeneration with replay/conflict.

        Same id + same request_fingerprint → ``replayed``.
        Same id + different fingerprint → ``conflict``.
        New id → ``created``.

        SOUL body fields are rejected; only ``soul_sha`` is stored.
        """
        if not isinstance(generation, dict):
            raise TypeError("generation must be an object")
        for forbidden in ("soul_content", "soul_text", "soul", "SOUL"):
            if forbidden in generation:
                raise ValueError(
                    f"SOUL content must not be recorded; only soul_sha is allowed ({forbidden})"
                )

        generation_id = str(generation.get("id") or "").strip()
        if not generation_id:
            raise ValueError("generation.id must be a non-empty string")

        required = (
            "core_commit",
            "core_tree_digest",
            "dirty_state_digest",
            "plugin_version",
            "soul_sha",
            "overlay_manifest_digest",
            "policy_digest",
            "mcp_version",
            "schema_version",
            "taxonomy_version",
        )
        missing = [key for key in required if not str(generation.get(key) or "").strip()]
        if missing:
            raise ValueError(f"generation missing required fields: {missing}")

        request_fingerprint = str(
            generation.get("request_fingerprint") or ""
        ).strip()
        if not request_fingerprint:
            raise ValueError("generation.request_fingerprint must be a non-empty string")

        model_id = generation.get("model_id")
        if model_id is not None:
            model_id = str(model_id)
            if not model_id.strip():
                model_id = None

        created_at = generation.get("created_at")
        if created_at is not None:
            created_at = str(created_at)

        props = {
            "id": generation_id,
            "core_commit": str(generation["core_commit"]),
            "core_tree_digest": str(generation["core_tree_digest"]),
            "dirty_state_digest": str(generation["dirty_state_digest"]),
            "plugin_version": str(generation["plugin_version"]),
            "soul_sha": str(generation["soul_sha"]),
            "overlay_manifest_digest": str(generation["overlay_manifest_digest"]),
            "policy_digest": str(generation["policy_digest"]),
            "mcp_version": str(generation["mcp_version"]),
            "model_id": model_id,
            "schema_version": str(generation["schema_version"]),
            "taxonomy_version": str(generation["taxonomy_version"]),
            "request_fingerprint": request_fingerprint,
            "created_at": created_at,
        }

        def operation(session: Any) -> dict[str, Any]:
            existing = _run_one(
                session,
                """
                MATCH (g:Operational:HarnessGeneration {id: $generation_id})
                RETURN g.id AS id,
                       g.request_fingerprint AS request_fingerprint,
                       g.created_at AS created_at,
                       g.soul_sha AS soul_sha,
                       g.plugin_version AS plugin_version,
                       g.core_commit AS core_commit,
                       g.schema_version AS schema_version,
                       g.taxonomy_version AS taxonomy_version
                LIMIT 1
                """,
                {"generation_id": generation_id},
            )
            if existing is not None:
                if existing.get("request_fingerprint") == request_fingerprint:
                    return {
                        "outcome": "replayed",
                        "generation_id": existing.get("id"),
                        "request_fingerprint": existing.get("request_fingerprint"),
                        "created_at": existing.get("created_at"),
                        "soul_sha": existing.get("soul_sha"),
                        "plugin_version": existing.get("plugin_version"),
                        "core_commit": existing.get("core_commit"),
                        "schema_version": existing.get("schema_version"),
                        "taxonomy_version": existing.get("taxonomy_version"),
                    }
                return {
                    "outcome": "conflict",
                    "reason": "generation_id_reused",
                    "generation_id": existing.get("id"),
                    "request_fingerprint": existing.get("request_fingerprint"),
                    "created_at": existing.get("created_at"),
                }

            # Ensure created_at is set on first write.
            write_props = dict(props)
            if not write_props.get("created_at"):
                row_ts = _run_one(session, "RETURN toString(datetime()) AS ts")
                write_props["created_at"] = (
                    (row_ts or {}).get("ts") or "unknown"
                )

            created = _run_one(
                session,
                """
                CREATE (g:Operational:HarnessGeneration)
                SET g += $props
                RETURN g.id AS id,
                       g.request_fingerprint AS request_fingerprint,
                       g.created_at AS created_at,
                       g.soul_sha AS soul_sha,
                       g.plugin_version AS plugin_version,
                       g.core_commit AS core_commit,
                       g.schema_version AS schema_version,
                       g.taxonomy_version AS taxonomy_version
                """,
                {"props": write_props},
            )
            if created is None:
                raise RuntimeError("HarnessGeneration create returned no row")
            return {
                "outcome": "created",
                "generation_id": created.get("id"),
                "request_fingerprint": created.get("request_fingerprint"),
                "created_at": created.get("created_at"),
                "soul_sha": created.get("soul_sha"),
                "plugin_version": created.get("plugin_version"),
                "core_commit": created.get("core_commit"),
                "schema_version": created.get("schema_version"),
                "taxonomy_version": created.get("taxonomy_version"),
            }

        return self._with_session(operation)
