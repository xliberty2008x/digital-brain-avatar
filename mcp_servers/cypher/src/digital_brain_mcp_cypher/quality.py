"""Quality/control plane: Operational boundary, constraints, and typed store.

Owns shared labels, exclusion fragments, the quality driver store,
HarnessGeneration receipts, and typed Feedback/RunEvent sensor transactions.

Sensors never call embeddings or journal-chain code. Raw Feedback text lives
in a separate removable ``QualityPayload`` node so redaction can drop it
without rewriting immutable observation metadata or the request fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from typing import Any, Callable, Mapping

_logger = logging.getLogger(__name__)

# Must match digital_brain.maintenance.models.GENERATION_ID_PREFIX / algorithm.
_GENERATION_ID_PREFIX = "hg-"

# Sensor schema / taxonomy versions (independent of HarnessGeneration schema).
FEEDBACK_SCHEMA_VERSION = "1"
RUN_EVENT_SCHEMA_VERSION = "1"
SENSOR_TAXONOMY_VERSION = "1"

# Tight enums and length caps for sensor validation.
FEEDBACK_KINDS: frozenset[str] = frozenset(
    {"entity_wrong", "claim_false", "miss", "invent", "praise"}
)
SENSITIVITIES: frozenset[str] = frozenset({"public_ops", "personal", "intimate"})
ROUTES: frozenset[str] = frozenset(
    {"SKIP", "READ", "WRITE", "FEEDBACK", "MAINTAIN"}
)
TOOL_OUTCOMES: frozenset[str] = frozenset(
    {"success", "fail", "empty", "conflict", "timeout"}
)
TASK_OUTCOMES: frozenset[str] = frozenset(
    {"success", "fail", "corrected", "unknown"}
)
OUTCOME_SOURCES: frozenset[str] = frozenset(
    {"mcp", "host", "user", "model_advisory"}
)
DETERMINISTIC_OUTCOME_SOURCES: frozenset[str] = frozenset({"mcp", "host", "user"})
LIFECYCLE_EVENTS: frozenset[str] = frozenset(
    {"triaged", "closed", "dismissed", "revoked", "redacted", "archived", "purged"}
)

MAX_SENSOR_ID_LEN = 128
MAX_SUMMARY_LEN = 512
MAX_RAW_PAYLOAD_LEN = 8_192
MAX_REF_COUNT = 16
MAX_REF_ITEM_LEN = 256
MAX_APPROACH_LEN = 64
MAX_TOOL_LEN = 128
MAX_ERROR_CLASS_LEN = 128
MAX_SOURCE_TURN_REF_LEN = 256
MAX_ACTOR_LEN = 128
MAX_REASON_CODE_LEN = 64
MAX_TRACE_LEN = 128
MAX_SESSION_REF_LEN = 128
MAX_HOST_LEN = 128
MAX_RECURRENCE_KEY_LEN = 128
RAW_HMAC_KEY_VERSION = "sha256-v1"

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
        "DreamStageReceipt",
        "HarnessGeneration",
        "Deployment",
        "ExposureWindow",
        "Proposal",
        "EvaluationReceipt",
        "Decision",
        "EffectReceipt",
        "ActivationAuthority",
        "MaintenanceLease",
        "EvidenceRef",
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
    CREATE CONSTRAINT operational_feedback_lifecycle_id_unique IF NOT EXISTS
    FOR (n:FeedbackLifecycleEvent) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT operational_quality_payload_id_unique IF NOT EXISTS
    FOR (n:QualityPayload) REQUIRE n.id IS UNIQUE
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


def _run_all(
    runner: Any, query: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    result = runner.run(query, params or {})
    # Prefer .data() when available (neo4j Result); fall back to iteration.
    if hasattr(result, "data") and callable(result.data):
        try:
            rows = result.data()
            if isinstance(rows, list):
                return [dict(r) for r in rows]
        except Exception:  # noqa: BLE001 — fall through to iteration
            pass
    rows_out: list[dict[str, Any]] = []
    if hasattr(result, "__iter__") and not hasattr(result, "single"):
        # Already a list-like from fakes.
        for record in result:
            if hasattr(record, "data"):
                rows_out.append(record.data())
            elif isinstance(record, dict):
                rows_out.append(dict(record))
        return rows_out
    # Neo4j Result: iterate records.
    try:
        for record in result:
            if hasattr(record, "data"):
                rows_out.append(record.data())
            elif isinstance(record, dict):
                rows_out.append(dict(record))
    except TypeError:
        pass
    return rows_out


def _canonical_json(payload: Mapping[str, Any]) -> str:
    """Canonical JSON — must match digital_brain.maintenance.models._canonical_json."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def harness_identity_payload(
    *,
    core_commit: str,
    core_tree_digest: str,
    dirty_state_digest: str,
    plugin_version: str,
    soul_sha: str,
    overlay_manifest_digest: str,
    policy_digest: str,
    mcp_version: str,
    model_id: str | None,
    schema_version: str,
    taxonomy_version: str,
) -> dict[str, Any]:
    """Identity fields that define generation id / request fingerprint."""
    return {
        "core_commit": core_commit,
        "core_tree_digest": core_tree_digest,
        "dirty_state_digest": dirty_state_digest,
        "mcp_version": mcp_version,
        "model_id": model_id,
        "overlay_manifest_digest": overlay_manifest_digest,
        "plugin_version": plugin_version,
        "policy_digest": policy_digest,
        "schema_version": schema_version,
        "soul_sha": soul_sha,
        "taxonomy_version": taxonomy_version,
    }


def compute_harness_request_fingerprint(identity: Mapping[str, Any]) -> str:
    """Server-side fingerprint — same algorithm as client models."""
    return hashlib.sha256(
        _canonical_json(identity).encode("utf-8")
    ).hexdigest()


def _is_uniqueness_constraint_error(exc: BaseException) -> bool:
    """Map Neo4j uniqueness races (and fakes) without hard-importing neo4j."""
    name = type(exc).__name__
    code = str(getattr(exc, "code", "") or "")
    msg = str(exc).lower()
    if name in {"ConstraintError", "ClientError"}:
        if "constraint" in msg or "already exists" in msg or "uniqueness" in msg:
            return True
        if "ConstraintValidationFailed" in code or "constraint" in code.lower():
            return True
    if "constraintvalidationfailed" in msg or "already exists" in msg:
        return True
    if "uniqueness" in msg and "constraint" in msg:
        return True
    return False


def _execute_write(session: Any, fn: Callable[[Any], Any]) -> Any:
    execute_write = getattr(session, "execute_write", None) or getattr(
        session, "write_transaction", None
    )
    if execute_write is None:
        # Test fakes / minimal runners: run inline.
        return fn(session)
    return execute_write(fn)


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
        """Look up a quality record by stable id for write reconciliation.

        Checks EffectReceipt, Feedback, RunEvent, then FeedbackLifecycleEvent.
        Timeouts must call this instead of blindly retrying a write.
        """
        receipt_id = _require_sensor_id(receipt_id, "receipt_id")

        def operation(session: Any) -> dict[str, Any]:
            effect = _run_one(
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
            if effect is not None:
                return {
                    "outcome": "ok",
                    "receipt_id": effect.get("receipt_id"),
                    "record_type": "EffectReceipt",
                    "request_fingerprint": effect.get("request_fingerprint"),
                    "status": effect.get("status"),
                    "created_at": effect.get("created_at"),
                }

            feedback = _run_one(
                session,
                """
                MATCH (f:Operational:Feedback {id: $receipt_id})
                RETURN f.id AS receipt_id,
                       f.request_fingerprint AS request_fingerprint,
                       f.kind AS kind,
                       f.sensitivity AS sensitivity,
                       f.harness_generation_id AS harness_generation_id,
                       f.raw_payload_ref AS raw_payload_ref,
                       f.created_at AS created_at
                LIMIT 1
                """,
                {"receipt_id": receipt_id},
            )
            if feedback is not None:
                return {
                    "outcome": "ok",
                    "receipt_id": feedback.get("receipt_id"),
                    "record_type": "Feedback",
                    "request_fingerprint": feedback.get("request_fingerprint"),
                    "kind": feedback.get("kind"),
                    "sensitivity": feedback.get("sensitivity"),
                    "harness_generation_id": feedback.get("harness_generation_id"),
                    "raw_payload_ref": feedback.get("raw_payload_ref"),
                    "created_at": feedback.get("created_at"),
                }

            run_event = _run_one(
                session,
                """
                MATCH (e:Operational:RunEvent {id: $receipt_id})
                RETURN e.id AS receipt_id,
                       e.request_fingerprint AS request_fingerprint,
                       e.route AS route,
                       e.tool AS tool,
                       e.tool_outcome AS tool_outcome,
                       e.outcome_source AS outcome_source,
                       e.harness_generation_id AS harness_generation_id,
                       e.observed_at AS observed_at,
                       e.ingested_at AS ingested_at
                LIMIT 1
                """,
                {"receipt_id": receipt_id},
            )
            if run_event is not None:
                return {
                    "outcome": "ok",
                    "receipt_id": run_event.get("receipt_id"),
                    "record_type": "RunEvent",
                    "request_fingerprint": run_event.get("request_fingerprint"),
                    "route": run_event.get("route"),
                    "tool": run_event.get("tool"),
                    "tool_outcome": run_event.get("tool_outcome"),
                    "outcome_source": run_event.get("outcome_source"),
                    "harness_generation_id": run_event.get("harness_generation_id"),
                    "observed_at": run_event.get("observed_at"),
                    "created_at": run_event.get("ingested_at"),
                }

            lifecycle = _run_one(
                session,
                """
                MATCH (l:Operational:FeedbackLifecycleEvent {id: $receipt_id})
                RETURN l.id AS receipt_id,
                       l.request_fingerprint AS request_fingerprint,
                       l.feedback_id AS feedback_id,
                       l.event AS event,
                       l.actor AS actor,
                       l.created_at AS created_at
                LIMIT 1
                """,
                {"receipt_id": receipt_id},
            )
            if lifecycle is not None:
                return {
                    "outcome": "ok",
                    "receipt_id": lifecycle.get("receipt_id"),
                    "record_type": "FeedbackLifecycleEvent",
                    "request_fingerprint": lifecycle.get("request_fingerprint"),
                    "feedback_id": lifecycle.get("feedback_id"),
                    "event": lifecycle.get("event"),
                    "actor": lifecycle.get("actor"),
                    "created_at": lifecycle.get("created_at"),
                }

            return {"outcome": "not_found", "receipt_id": receipt_id}

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

        Server recomputes the fingerprint from identity fields (same canonical
        algorithm as client models) and rejects clients that supply a mismatched
        fingerprint or ``id != "hg-" + fingerprint``.

        Writes run in a single ``execute_write`` transaction; uniqueness races
        are mapped to replay/conflict via re-read.

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

        client_fingerprint = str(
            generation.get("request_fingerprint") or ""
        ).strip()
        if not client_fingerprint:
            raise ValueError("generation.request_fingerprint must be a non-empty string")

        model_id = generation.get("model_id")
        if model_id is not None:
            model_id = str(model_id)
            if not model_id.strip():
                model_id = None

        created_at = generation.get("created_at")
        if created_at is not None:
            created_at = str(created_at)

        identity = harness_identity_payload(
            core_commit=str(generation["core_commit"]),
            core_tree_digest=str(generation["core_tree_digest"]),
            dirty_state_digest=str(generation["dirty_state_digest"]),
            plugin_version=str(generation["plugin_version"]),
            soul_sha=str(generation["soul_sha"]),
            overlay_manifest_digest=str(generation["overlay_manifest_digest"]),
            policy_digest=str(generation["policy_digest"]),
            mcp_version=str(generation["mcp_version"]),
            model_id=model_id,
            schema_version=str(generation["schema_version"]),
            taxonomy_version=str(generation["taxonomy_version"]),
        )
        request_fingerprint = compute_harness_request_fingerprint(identity)
        expected_id = f"{_GENERATION_ID_PREFIX}{request_fingerprint}"

        if client_fingerprint != request_fingerprint:
            raise ValueError(
                "generation.request_fingerprint does not match identity fields "
                f"(server={request_fingerprint[:16]}… client={client_fingerprint[:16]}…)"
            )
        if generation_id != expected_id:
            raise ValueError(
                f"generation.id must equal '{_GENERATION_ID_PREFIX}' + fingerprint "
                f"(expected {expected_id[:19]}…, got {generation_id[:19]}…)"
            )

        props = {
            "id": generation_id,
            "core_commit": identity["core_commit"],
            "core_tree_digest": identity["core_tree_digest"],
            "dirty_state_digest": identity["dirty_state_digest"],
            "plugin_version": identity["plugin_version"],
            "soul_sha": identity["soul_sha"],
            "overlay_manifest_digest": identity["overlay_manifest_digest"],
            "policy_digest": identity["policy_digest"],
            "mcp_version": identity["mcp_version"],
            "model_id": model_id,
            "schema_version": identity["schema_version"],
            "taxonomy_version": identity["taxonomy_version"],
            "request_fingerprint": request_fingerprint,
            "created_at": created_at,
        }

        def operation(session: Any) -> dict[str, Any]:
            try:
                return _execute_write(
                    session,
                    lambda tx: self._record_harness_generation_tx(
                        tx,
                        props=props,
                        generation_id=generation_id,
                        request_fingerprint=request_fingerprint,
                    ),
                )
            except Exception as exc:
                # Constraint failures abort the write tx — re-read in a new one.
                if not _is_uniqueness_constraint_error(exc):
                    raise
                raced = _execute_write(
                    session,
                    lambda tx: self._read_harness_generation_row(tx, generation_id),
                )
                if raced is None:
                    raise RuntimeError(
                        "HarnessGeneration uniqueness race but node not found on re-read"
                    ) from exc
                return self._replay_or_conflict(
                    raced, request_fingerprint=request_fingerprint
                )

        return self._with_session(operation)

    def _record_harness_generation_tx(
        self,
        tx: Any,
        *,
        props: dict[str, Any],
        generation_id: str,
        request_fingerprint: str,
    ) -> dict[str, Any]:
        existing = self._read_harness_generation_row(tx, generation_id)
        if existing is not None:
            return self._replay_or_conflict(
                existing, request_fingerprint=request_fingerprint
            )

        write_props = dict(props)
        if not write_props.get("created_at"):
            row_ts = _run_one(tx, "RETURN toString(datetime()) AS ts")
            write_props["created_at"] = (row_ts or {}).get("ts") or "unknown"

        created = _run_one(
            tx,
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

    @staticmethod
    def _read_harness_generation_row(
        runner: Any, generation_id: str
    ) -> dict[str, Any] | None:
        return _run_one(
            runner,
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

    @staticmethod
    def _replay_or_conflict(
        existing: dict[str, Any], *, request_fingerprint: str
    ) -> dict[str, Any]:
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

    # ------------------------------------------------------------------
    # Feedback sensors
    # ------------------------------------------------------------------

    def create_feedback(self, feedback: dict[str, Any]) -> dict[str, Any]:
        """Idempotent create of Operational:Feedback (+ optional QualityPayload).

        Same id + same request_fingerprint → ``replayed``.
        Same id + different fingerprint → ``conflict``.
        Raw text is stored only on a separate ``QualityPayload`` node.
        """
        if not isinstance(feedback, dict):
            raise TypeError("feedback must be an object")

        feedback_id = _require_sensor_id(feedback.get("id"), "feedback.id")
        kind = _require_enum(feedback.get("kind"), FEEDBACK_KINDS, "feedback.kind")
        sensitivity = _require_enum(
            feedback.get("sensitivity"), SENSITIVITIES, "feedback.sensitivity"
        )
        harness_generation_id = _require_generation_id(
            feedback.get("harness_generation_id")
        )
        schema_version = str(
            feedback.get("schema_version") or FEEDBACK_SCHEMA_VERSION
        ).strip()
        taxonomy_version = str(
            feedback.get("taxonomy_version") or SENSOR_TAXONOMY_VERSION
        ).strip()
        source_turn_ref = _optional_bounded_text(
            feedback.get("source_turn_ref"),
            "feedback.source_turn_ref",
            MAX_SOURCE_TURN_REF_LEN,
        )
        redacted_summary = _optional_bounded_text(
            feedback.get("redacted_summary"),
            "feedback.redacted_summary",
            MAX_SUMMARY_LEN,
        )
        raw_payload = feedback.get("raw_payload")
        if raw_payload is not None and not isinstance(raw_payload, str):
            raise TypeError("feedback.raw_payload must be a string or null")
        if isinstance(raw_payload, str) and len(raw_payload) > MAX_RAW_PAYLOAD_LEN:
            raise ValueError(
                f"feedback.raw_payload exceeds max length {MAX_RAW_PAYLOAD_LEN}"
            )
        if isinstance(raw_payload, str) and not raw_payload:
            raw_payload = None

        raw_hmac: str | None = None
        raw_payload_ref: str | None = None
        hmac_key_version: str | None = None
        if raw_payload is not None:
            raw_hmac = compute_raw_hmac(raw_payload)
            hmac_key_version = RAW_HMAC_KEY_VERSION
            raw_payload_ref = f"qp-{feedback_id}"

        identity = feedback_identity_payload(
            kind=kind,
            sensitivity=sensitivity,
            source_turn_ref=source_turn_ref,
            redacted_summary=redacted_summary,
            harness_generation_id=harness_generation_id,
            schema_version=schema_version,
            taxonomy_version=taxonomy_version,
            raw_hmac=raw_hmac,
        )
        request_fingerprint = compute_sensor_request_fingerprint(identity)
        _assert_client_fingerprint(
            feedback.get("request_fingerprint"),
            request_fingerprint,
            "feedback.request_fingerprint",
        )

        created_at = feedback.get("created_at")
        if created_at is not None:
            created_at = str(created_at)

        props = {
            "id": feedback_id,
            "kind": kind,
            "sensitivity": sensitivity,
            "source_turn_ref": source_turn_ref,
            "redacted_summary": redacted_summary,
            "harness_generation_id": harness_generation_id,
            "schema_version": schema_version,
            "taxonomy_version": taxonomy_version,
            "raw_payload_ref": raw_payload_ref,
            "raw_hmac": raw_hmac,
            "hmac_key_version": hmac_key_version,
            "request_fingerprint": request_fingerprint,
            "created_at": created_at,
        }

        def operation(session: Any) -> dict[str, Any]:
            try:
                return _execute_write(
                    session,
                    lambda tx: self._create_feedback_tx(
                        tx,
                        props=props,
                        feedback_id=feedback_id,
                        request_fingerprint=request_fingerprint,
                        raw_payload=raw_payload,
                        raw_payload_ref=raw_payload_ref,
                    ),
                )
            except Exception as exc:
                if not _is_uniqueness_constraint_error(exc):
                    raise
                raced = _execute_write(
                    session,
                    lambda tx: self._read_feedback_row(tx, feedback_id),
                )
                if raced is None:
                    raise RuntimeError(
                        "Feedback uniqueness race but node not found on re-read"
                    ) from exc
                return self._sensor_replay_or_conflict(
                    raced,
                    request_fingerprint=request_fingerprint,
                    id_key="feedback_id",
                    reused_reason="feedback_id_reused",
                )

        return self._with_session(operation)

    def _create_feedback_tx(
        self,
        tx: Any,
        *,
        props: dict[str, Any],
        feedback_id: str,
        request_fingerprint: str,
        raw_payload: str | None,
        raw_payload_ref: str | None,
    ) -> dict[str, Any]:
        existing = self._read_feedback_row(tx, feedback_id)
        if existing is not None:
            return self._sensor_replay_or_conflict(
                existing,
                request_fingerprint=request_fingerprint,
                id_key="feedback_id",
                reused_reason="feedback_id_reused",
            )

        write_props = dict(props)
        if not write_props.get("created_at"):
            write_props["created_at"] = _now_iso(tx)

        created = _run_one(
            tx,
            """
            CREATE (f:Operational:Feedback)
            SET f += $props
            RETURN f.id AS id,
                   f.request_fingerprint AS request_fingerprint,
                   f.kind AS kind,
                   f.sensitivity AS sensitivity,
                   f.harness_generation_id AS harness_generation_id,
                   f.raw_payload_ref AS raw_payload_ref,
                   f.created_at AS created_at
            """,
            {"props": write_props},
        )
        if created is None:
            raise RuntimeError("Feedback create returned no row")

        if raw_payload is not None and raw_payload_ref is not None:
            payload_props = {
                "id": raw_payload_ref,
                "owner_evidence_id": feedback_id,
                "payload_text": raw_payload,
                "sensitivity": write_props["sensitivity"],
                "created_at": write_props["created_at"],
            }
            _consume(
                tx.run(
                    """
                    MATCH (f:Operational:Feedback {id: $feedback_id})
                    CREATE (p:Operational:QualityPayload)
                    SET p += $payload_props
                    CREATE (f)-[:HAS_RAW_PAYLOAD]->(p)
                    """,
                    {
                        "feedback_id": feedback_id,
                        "payload_props": payload_props,
                    },
                )
            )

        return {
            "outcome": "created",
            "feedback_id": created.get("id"),
            "request_fingerprint": created.get("request_fingerprint"),
            "kind": created.get("kind"),
            "sensitivity": created.get("sensitivity"),
            "harness_generation_id": created.get("harness_generation_id"),
            "raw_payload_ref": created.get("raw_payload_ref"),
            "created_at": created.get("created_at"),
        }

    def revoke_feedback(self, revocation: dict[str, Any]) -> dict[str, Any]:
        """Append a revoked FeedbackLifecycleEvent (idempotent by lifecycle id)."""
        if not isinstance(revocation, dict):
            raise TypeError("revocation must be an object")

        lifecycle_id = _require_sensor_id(revocation.get("id"), "revocation.id")
        feedback_id = _require_sensor_id(
            revocation.get("feedback_id"), "revocation.feedback_id"
        )
        actor = _require_bounded_text(
            revocation.get("actor"), "revocation.actor", MAX_ACTOR_LEN
        )
        reason_code = _optional_bounded_text(
            revocation.get("reason_code"),
            "revocation.reason_code",
            MAX_REASON_CODE_LEN,
        )
        event = "revoked"
        identity = lifecycle_identity_payload(
            feedback_id=feedback_id,
            event=event,
            actor=actor,
            reason_code=reason_code,
        )
        request_fingerprint = compute_sensor_request_fingerprint(identity)
        _assert_client_fingerprint(
            revocation.get("request_fingerprint"),
            request_fingerprint,
            "revocation.request_fingerprint",
        )

        created_at = revocation.get("created_at")
        if created_at is not None:
            created_at = str(created_at)

        props = {
            "id": lifecycle_id,
            "feedback_id": feedback_id,
            "event": event,
            "actor": actor,
            "reason_code": reason_code,
            "request_fingerprint": request_fingerprint,
            "created_at": created_at,
        }

        def operation(session: Any) -> dict[str, Any]:
            try:
                return _execute_write(
                    session,
                    lambda tx: self._revoke_feedback_tx(
                        tx,
                        props=props,
                        lifecycle_id=lifecycle_id,
                        feedback_id=feedback_id,
                        request_fingerprint=request_fingerprint,
                    ),
                )
            except Exception as exc:
                if not _is_uniqueness_constraint_error(exc):
                    raise
                raced = _execute_write(
                    session,
                    lambda tx: self._read_lifecycle_row(tx, lifecycle_id),
                )
                if raced is None:
                    raise RuntimeError(
                        "FeedbackLifecycleEvent uniqueness race but node not found"
                    ) from exc
                return self._sensor_replay_or_conflict(
                    raced,
                    request_fingerprint=request_fingerprint,
                    id_key="lifecycle_event_id",
                    reused_reason="lifecycle_event_id_reused",
                )

        return self._with_session(operation)

    def _revoke_feedback_tx(
        self,
        tx: Any,
        *,
        props: dict[str, Any],
        lifecycle_id: str,
        feedback_id: str,
        request_fingerprint: str,
    ) -> dict[str, Any]:
        existing = self._read_lifecycle_row(tx, lifecycle_id)
        if existing is not None:
            return self._sensor_replay_or_conflict(
                existing,
                request_fingerprint=request_fingerprint,
                id_key="lifecycle_event_id",
                reused_reason="lifecycle_event_id_reused",
            )

        parent = self._read_feedback_row(tx, feedback_id)
        if parent is None:
            return {
                "outcome": "not_found",
                "reason": "feedback_missing",
                "feedback_id": feedback_id,
                "lifecycle_event_id": lifecycle_id,
            }

        write_props = dict(props)
        if not write_props.get("created_at"):
            write_props["created_at"] = _now_iso(tx)

        created = _run_one(
            tx,
            """
            MATCH (f:Operational:Feedback {id: $feedback_id})
            CREATE (l:Operational:FeedbackLifecycleEvent)
            SET l += $props
            CREATE (f)-[:HAS_LIFECYCLE_EVENT]->(l)
            RETURN l.id AS id,
                   l.feedback_id AS feedback_id,
                   l.event AS event,
                   l.actor AS actor,
                   l.request_fingerprint AS request_fingerprint,
                   l.created_at AS created_at
            """,
            {"feedback_id": feedback_id, "props": write_props},
        )
        if created is None:
            raise RuntimeError("FeedbackLifecycleEvent create returned no row")

        stale_ids = self._mark_derived_pending_proposals_stale(
            tx, feedback_id=feedback_id
        )
        return {
            "outcome": "created",
            "lifecycle_event_id": created.get("id"),
            "feedback_id": created.get("feedback_id"),
            "event": created.get("event"),
            "actor": created.get("actor"),
            "request_fingerprint": created.get("request_fingerprint"),
            "created_at": created.get("created_at"),
            "stale_proposal_ids": stale_ids,
        }

    def _mark_derived_pending_proposals_stale(
        self,
        tx: Any,
        *,
        feedback_id: str,
    ) -> list[str]:
        """Mark only directly derived pending proposals stale.

        Provenance path:
        ``(:Proposal)-[:SUPPORTED_BY]->(:Finding)-[:USES_EVIDENCE]->
        (:EvidenceRef {id: feedback_id})``.

        Does not rewrite journals or co-snapshot-only proposals.
        """
        pending = sorted(
            {
                "draft",
                "validated",
                "review_pending",
            }
        )
        # Collect matching proposals then set status (two steps for fake-session tests).
        rows = _run_all(
            tx,
            """
            MATCH (ref:Operational:EvidenceRef {id: $feedback_id})
                  <-[:USES_EVIDENCE]-(f:Operational:Finding)
                  <-[:SUPPORTED_BY]-(p:Operational:Proposal)
            WHERE p.status_projection IN $pending
            RETURN DISTINCT p.id AS id, p.status_projection AS status_projection
            """,
            {"feedback_id": feedback_id, "pending": pending},
        )
        stale_ids: list[str] = []
        for row in rows:
            pid = row.get("id")
            if not pid:
                continue
            updated = _run_one(
                tx,
                """
                MATCH (p:Operational:Proposal {id: $proposal_id})
                WHERE p.status_projection IN $pending
                SET p.status_projection = 'stale',
                    p.stale_reason = 'evidence_revoked',
                    p.stale_evidence_id = $feedback_id
                RETURN p.id AS id
                """,
                {
                    "proposal_id": pid,
                    "pending": pending,
                    "feedback_id": feedback_id,
                },
            )
            if updated is not None and updated.get("id"):
                stale_ids.append(str(updated["id"]))
        return sorted(stale_ids)

    # ------------------------------------------------------------------
    # Retention (policy-bound QualityPayload redaction)
    # ------------------------------------------------------------------

    def get_quality_payload(self, payload_id: str) -> dict[str, Any]:
        """Privileged read of a QualityPayload row (tests / owner tools only).

        Normal exports must not use this. After retention apply, outcome is
        ``not_found`` when the node was deleted.
        """
        payload_id = _require_sensor_id(payload_id, "payload_id")

        def operation(session: Any) -> dict[str, Any]:
            row = _run_one(
                session,
                """
                MATCH (p:Operational:QualityPayload {id: $payload_id})
                RETURN p.id AS id,
                       p.owner_evidence_id AS owner_evidence_id,
                       p.payload_text AS payload_text,
                       p.sensitivity AS sensitivity,
                       p.created_at AS created_at
                LIMIT 1
                """,
                {"payload_id": payload_id},
            )
            if row is None:
                return {"outcome": "not_found", "payload_id": payload_id}
            return {
                "outcome": "ok",
                "payload_id": row.get("id"),
                "owner_evidence_id": row.get("owner_evidence_id"),
                "payload_text": row.get("payload_text"),
                "sensitivity": row.get("sensitivity"),
                "created_at": row.get("created_at"),
            }

        return self._with_session(operation)

    def export_feedback_public(self, feedback_id: str) -> dict[str, Any]:
        """Normal read/export projection — never includes payload_text."""
        feedback_id = _require_sensor_id(feedback_id, "feedback_id")

        def operation(session: Any) -> dict[str, Any]:
            row = self._read_feedback_row(session, feedback_id)
            if row is None:
                return {"outcome": "not_found", "feedback_id": feedback_id}
            # Intentionally omit raw payload body even when the node still exists.
            return {
                "outcome": "ok",
                "feedback_id": row.get("id"),
                "kind": row.get("kind"),
                "sensitivity": row.get("sensitivity"),
                "harness_generation_id": row.get("harness_generation_id"),
                "request_fingerprint": row.get("request_fingerprint"),
                "raw_payload_ref": row.get("raw_payload_ref"),
                "created_at": row.get("created_at"),
                # Explicit absence for exporters / tests.
                "payload_text": None,
                "raw_payload": None,
            }

        return self._with_session(operation)

    def apply_retention_effect(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Policy-bound redaction/archive/purge of removable QualityPayload.

        Only this dedicated transaction (and the MaintenanceStore retention
        path that mirrors it) may remove raw payload. Generic Cypher DELETE
        remains blocked. Every apply is receipted via EffectReceipt + lifecycle.

        Required fields: id, effect_key, feedback_id, action, config_digest,
        and (for mutative apply) run_id + epoch + lease_key fence.
        ``dry_run: true`` returns a plan-style count without mutation and
        does not require a fence.
        Automatic apply must set ``automatic: true`` and pass
        ``auto_apply_enabled: true`` from the reviewed config; otherwise denied.
        Owner-initiated apply sets ``owner_initiated: true``.
        """
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")

        effect_id = _require_sensor_id(payload.get("id"), "id")
        effect_key = _require_sensor_id(
            payload.get("effect_key") or effect_id, "effect_key"
        )
        feedback_id = _require_sensor_id(
            payload.get("feedback_id"), "feedback_id"
        )
        action = _require_enum(
            payload.get("action"),
            frozenset({"redact", "archive", "purge"}),
            "action",
        )
        config_digest = _require_sensor_id(
            payload.get("config_digest"), "config_digest"
        )
        actor = _require_bounded_text(
            payload.get("actor") or "maintenance", "actor", MAX_ACTOR_LEN
        )
        dry_run = bool(payload.get("dry_run", False))
        automatic = bool(payload.get("automatic", False))
        owner_initiated = bool(payload.get("owner_initiated", False))
        auto_apply_enabled = bool(payload.get("auto_apply_enabled", False))

        if dry_run:
            return self._retention_dry_run_one(
                feedback_id=feedback_id,
                action=action,
                config_digest=config_digest,
            )

        if automatic and not auto_apply_enabled:
            return {
                "outcome": "denied",
                "reason": "retention_auto_apply_disabled",
                "feedback_id": feedback_id,
                "action": action,
                "config_digest": config_digest,
            }
        if not automatic and not owner_initiated:
            return {
                "outcome": "denied",
                "reason": "retention_apply_requires_owner_or_auto",
                "feedback_id": feedback_id,
                "action": action,
            }

        effect_type = {
            "redact": "retention_redact",
            "archive": "retention_archive",
            "purge": "retention_purge",
        }[action]
        lifecycle_event = {
            "redact": "redacted",
            "archive": "archived",
            "purge": "purged",
        }[action]
        before_ref = str(
            payload.get("before_ref") or f"Feedback:{feedback_id}:payload"
        )
        after_ref = str(
            payload.get("after_ref") or f"Feedback:{feedback_id}:{action}ed"
        )
        if action == "purge":
            after_ref = str(
                payload.get("after_ref") or f"Feedback:{feedback_id}:purged"
            )
        elif action == "redact":
            after_ref = str(
                payload.get("after_ref") or f"Feedback:{feedback_id}:redacted"
            )
        elif action == "archive":
            after_ref = str(
                payload.get("after_ref") or f"Feedback:{feedback_id}:archived"
            )

        # Mutative retention is always fenced (Task 5 / Milestone B gate).
        run_id = _require_sensor_id(payload.get("run_id"), "run_id")
        epoch = payload.get("epoch")
        if epoch is None:
            epoch = payload.get("lease_epoch")
        if epoch is None:
            raise ValueError("epoch is required for retention apply")
        try:
            epoch_i = int(epoch)
        except (TypeError, ValueError) as exc:
            raise ValueError("epoch must be an integer") from exc
        if epoch_i < 1:
            raise ValueError("epoch must be >= 1")
        lease_key = _require_sensor_id(
            payload.get("lease_key") or "maintenance", "lease_key"
        )
        fence_required = True

        identity = {
            "action": action,
            "after_ref": after_ref,
            "before_ref": before_ref,
            "config_digest": config_digest,
            "effect_id": effect_id,
            "effect_key": effect_key,
            "effect_type": effect_type,
            "feedback_id": feedback_id,
        }
        request_fingerprint = compute_sensor_request_fingerprint(identity)
        request_hash = str(payload.get("request_hash") or request_fingerprint)
        lifecycle_id = str(
            payload.get("lifecycle_id") or f"fle-ret-{effect_id}"
        )[:MAX_SENSOR_ID_LEN]

        def operation(session: Any) -> dict[str, Any]:
            return _execute_write(
                session,
                lambda tx: self._apply_retention_effect_tx(
                    tx,
                    effect_id=effect_id,
                    effect_key=effect_key,
                    feedback_id=feedback_id,
                    action=action,
                    effect_type=effect_type,
                    lifecycle_event=lifecycle_event,
                    lifecycle_id=lifecycle_id,
                    config_digest=config_digest,
                    actor=actor,
                    before_ref=before_ref,
                    after_ref=after_ref,
                    request_fingerprint=request_fingerprint,
                    request_hash=request_hash,
                    run_id=run_id,
                    epoch=epoch_i,
                    lease_key=lease_key,
                    fence_required=fence_required,
                ),
            )

        return self._with_session(operation)

    def _retention_dry_run_one(
        self,
        *,
        feedback_id: str,
        action: str,
        config_digest: str,
    ) -> dict[str, Any]:
        def operation(session: Any) -> dict[str, Any]:
            fb = self._read_feedback_row(session, feedback_id)
            if fb is None:
                return {
                    "outcome": "dry_run",
                    "would_apply": False,
                    "reason": "feedback_missing",
                    "feedback_id": feedback_id,
                    "action": action,
                    "config_digest": config_digest,
                    "counts": {"selected": 0},
                }
            ref = fb.get("raw_payload_ref")
            has_payload = False
            if ref:
                payload_row = _run_one(
                    session,
                    """
                    MATCH (p:Operational:QualityPayload {id: $payload_id})
                    RETURN p.id AS id, p.payload_text AS payload_text
                    LIMIT 1
                    """,
                    {"payload_id": ref},
                )
                has_payload = bool(
                    payload_row
                    and str(payload_row.get("payload_text") or "").strip()
                )
            return {
                "outcome": "dry_run",
                "would_apply": has_payload,
                "feedback_id": feedback_id,
                "action": action,
                "config_digest": config_digest,
                "request_fingerprint": fb.get("request_fingerprint"),
                "raw_payload_ref": ref,
                "counts": {"selected": 1 if has_payload else 0},
            }

        return self._with_session(operation)

    def _apply_retention_effect_tx(
        self,
        tx: Any,
        *,
        effect_id: str,
        effect_key: str,
        feedback_id: str,
        action: str,
        effect_type: str,
        lifecycle_event: str,
        lifecycle_id: str,
        config_digest: str,
        actor: str,
        before_ref: str,
        after_ref: str,
        request_fingerprint: str,
        request_hash: str,
        run_id: str,
        epoch: int,
        lease_key: str,
        fence_required: bool,
    ) -> dict[str, Any]:
        if fence_required:
            fence_err = self._assert_retention_fence(
                tx, lease_key=lease_key, run_id=run_id, epoch=epoch
            )
            if fence_err is not None:
                return fence_err

        # Idempotent receipt first.
        existing = _run_one(
            tx,
            """
            MATCH (r:Operational:EffectReceipt {id: $id})
            RETURN r.id AS id,
                   r.effect_key AS effect_key,
                   r.request_fingerprint AS request_fingerprint,
                   r.outcome AS outcome,
                   r.verification_status AS verification_status,
                   r.fence_epoch AS fence_epoch
            LIMIT 1
            """,
            {"id": effect_id},
        )
        if existing is not None:
            if existing.get("request_fingerprint") == request_fingerprint:
                return {
                    "outcome": "replayed",
                    "effect_id": existing["id"],
                    "effect_key": existing.get("effect_key"),
                    "effect_outcome": existing.get("outcome"),
                    "verification_status": existing.get("verification_status"),
                    "request_fingerprint": existing.get("request_fingerprint"),
                    "fence_epoch": existing.get("fence_epoch"),
                    "feedback_id": feedback_id,
                    "action": action,
                }
            return {
                "outcome": "conflict",
                "reason": "effect_id_reused",
                "effect_id": existing["id"],
            }

        by_key = _run_one(
            tx,
            """
            MATCH (r:Operational:EffectReceipt {effect_key: $effect_key})
            RETURN r.id AS id,
                   r.request_fingerprint AS request_fingerprint,
                   r.outcome AS outcome,
                   r.effect_key AS effect_key,
                   r.verification_status AS verification_status,
                   r.fence_epoch AS fence_epoch
            LIMIT 1
            """,
            {"effect_key": effect_key},
        )
        if by_key is not None:
            if by_key.get("request_fingerprint") == request_fingerprint:
                return {
                    "outcome": "replayed",
                    "effect_id": by_key["id"],
                    "effect_key": by_key.get("effect_key"),
                    "effect_outcome": by_key.get("outcome"),
                    "verification_status": by_key.get("verification_status"),
                    "request_fingerprint": by_key.get("request_fingerprint"),
                    "fence_epoch": by_key.get("fence_epoch"),
                    "feedback_id": feedback_id,
                    "action": action,
                }
            return {
                "outcome": "conflict",
                "reason": "effect_key_reused",
                "effect_id": by_key["id"],
                "effect_key": effect_key,
            }

        fb = self._read_feedback_row(tx, feedback_id)
        if fb is None:
            return {
                "outcome": "not_found",
                "reason": "feedback_missing",
                "feedback_id": feedback_id,
                "effect_id": effect_id,
            }

        request_fingerprint_fb = fb.get("request_fingerprint")
        payload_ref = fb.get("raw_payload_ref")
        deleted_payload = False
        if payload_ref:
            deleted = _run_one(
                tx,
                """
                MATCH (f:Operational:Feedback {id: $feedback_id})
                      -[r:HAS_RAW_PAYLOAD]->(p:Operational:QualityPayload {id: $payload_id})
                DELETE r, p
                RETURN $payload_id AS deleted_id
                """,
                {"feedback_id": feedback_id, "payload_id": payload_ref},
            )
            deleted_payload = deleted is not None
            if not deleted_payload:
                # Payload already gone — still receipt as verified absent.
                orphan = _run_one(
                    tx,
                    """
                    MATCH (p:Operational:QualityPayload {id: $payload_id})
                    DETACH DELETE p
                    RETURN $payload_id AS deleted_id
                    """,
                    {"payload_id": payload_ref},
                )
                deleted_payload = orphan is not None

        # Verify absence.
        still = None
        if payload_ref:
            still = _run_one(
                tx,
                """
                MATCH (p:Operational:QualityPayload {id: $payload_id})
                RETURN p.id AS id, p.payload_text AS payload_text
                LIMIT 1
                """,
                {"payload_id": payload_ref},
            )
        verification_status = (
            "verified_absent"
            if still is None
            or not str((still or {}).get("payload_text") or "").strip()
            else "verification_failed"
        )
        if verification_status == "verification_failed":
            return {
                "outcome": "failed",
                "reason": "payload_still_present",
                "feedback_id": feedback_id,
                "effect_id": effect_id,
            }

        # Immutable Feedback fingerprint must remain.
        fb_after = self._read_feedback_row(tx, feedback_id)
        if (
            fb_after is not None
            and fb_after.get("request_fingerprint") != request_fingerprint_fb
        ):
            return {
                "outcome": "failed",
                "reason": "feedback_fingerprint_changed",
                "feedback_id": feedback_id,
            }

        now = _now_iso(tx)
        # Lifecycle append (idempotent by lifecycle id).
        existing_life = self._read_lifecycle_row(tx, lifecycle_id)
        if existing_life is None:
            life_identity = lifecycle_identity_payload(
                feedback_id=feedback_id,
                event=lifecycle_event,
                actor=actor,
                reason_code=f"retention_{action}",
            )
            life_fp = compute_sensor_request_fingerprint(life_identity)
            _consume(
                tx.run(
                    """
                    MATCH (f:Operational:Feedback {id: $feedback_id})
                    CREATE (l:Operational:FeedbackLifecycleEvent)
                    SET l.id = $id,
                        l.feedback_id = $feedback_id,
                        l.event = $event,
                        l.actor = $actor,
                        l.reason_code = $reason_code,
                        l.request_fingerprint = $fp,
                        l.config_digest = $config_digest,
                        l.created_at = $created_at
                    CREATE (f)-[:HAS_LIFECYCLE_EVENT]->(l)
                    """,
                    {
                        "id": lifecycle_id,
                        "feedback_id": feedback_id,
                        "event": lifecycle_event,
                        "actor": actor,
                        "reason_code": f"retention_{action}",
                        "fp": life_fp,
                        "config_digest": config_digest,
                        "created_at": now,
                    },
                )
            )

        created = _run_one(
            tx,
            """
            CREATE (r:Operational:EffectReceipt)
            SET r.id = $id,
                r.effect_key = $effect_key,
                r.request_hash = $request_hash,
                r.request_fingerprint = $fp,
                r.effect_type = $effect_type,
                r.actor = $actor,
                r.before_ref = $before_ref,
                r.after_ref = $after_ref,
                r.target_ref = $target_ref,
                r.outcome = $effect_outcome,
                r.verification_status = $verification_status,
                r.config_digest = $config_digest,
                r.action = $action,
                r.feedback_id = $feedback_id,
                r.fence_epoch = $epoch,
                r.run_id = $run_id,
                r.applied_at = datetime(),
                r.created_at = datetime()
            RETURN r.id AS id,
                   r.effect_key AS effect_key,
                   r.outcome AS outcome,
                   r.verification_status AS verification_status,
                   r.fence_epoch AS fence_epoch
            """,
            {
                "id": effect_id,
                "effect_key": effect_key,
                "request_hash": request_hash,
                "fp": request_fingerprint,
                "effect_type": effect_type,
                "actor": actor,
                "before_ref": before_ref,
                "after_ref": after_ref,
                "target_ref": f"Feedback:{feedback_id}",
                "effect_outcome": "applied",
                "verification_status": verification_status,
                "config_digest": config_digest,
                "action": action,
                "feedback_id": feedback_id,
                "epoch": epoch,
                "run_id": run_id,
            },
        )
        if created is None:
            raise RuntimeError("EffectReceipt create returned no row")

        return {
            "outcome": "created",
            "effect_id": created["id"],
            "effect_key": created["effect_key"],
            "effect_type": effect_type,
            "effect_outcome": created["outcome"],
            "verification_status": created.get("verification_status"),
            "fence_epoch": created.get("fence_epoch"),
            "run_id": run_id,
            "request_fingerprint": request_fingerprint,
            "feedback_id": feedback_id,
            "action": action,
            "lifecycle_event": lifecycle_event,
            "lifecycle_event_id": lifecycle_id,
            "config_digest": config_digest,
            "payload_deleted": deleted_payload or payload_ref is not None,
            "feedback_request_fingerprint": request_fingerprint_fb,
        }

    def _assert_retention_fence(
        self,
        tx: Any,
        *,
        lease_key: str,
        run_id: str,
        epoch: int,
    ) -> dict[str, Any] | None:
        """Optional lease fence for maintenance-driven retention."""
        row = _run_one(
            tx,
            """
            MATCH (l:Operational:MaintenanceLease {key: $key})
            RETURN l.run_id AS run_id,
                   l.epoch AS epoch,
                   l.lease_until AS lease_until,
                   CASE
                     WHEN l.lease_until IS NULL THEN true
                     WHEN l.lease_until < datetime() THEN true
                     ELSE false
                   END AS expired
            LIMIT 1
            """,
            {"key": lease_key},
        )
        if row is None:
            return {
                "outcome": "stale_epoch",
                "reason": "lease_missing",
                "lease_key": lease_key,
            }
        if bool(row.get("expired")):
            return {
                "outcome": "stale_epoch",
                "reason": "lease_expired",
                "lease_key": lease_key,
            }
        if str(row.get("run_id") or "") != run_id:
            return {
                "outcome": "stale_epoch",
                "reason": "run_id_mismatch",
                "lease_key": lease_key,
            }
        try:
            current_epoch = int(row.get("epoch"))
        except (TypeError, ValueError):
            current_epoch = -1
        if current_epoch != int(epoch):
            return {
                "outcome": "stale_epoch",
                "reason": "epoch_mismatch",
                "lease_key": lease_key,
                "expected_epoch": int(epoch),
                "current_epoch": current_epoch,
            }
        return None

    # ------------------------------------------------------------------
    # RunEvent sensors
    # ------------------------------------------------------------------

    def record_run_event(
        self,
        event: dict[str, Any],
        *,
        force_outcome_source: str | None = None,
        allowed_outcome_sources: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """Idempotent create of Operational:RunEvent with replay/conflict.

        Model-facing callers pass ``force_outcome_source='model_advisory'``.
        Trusted host/MCP recorders use :meth:`record_deterministic_run_event`.
        """
        if not isinstance(event, dict):
            raise TypeError("event must be an object")

        event_id = _require_sensor_id(event.get("id"), "event.id")
        harness_generation_id = _require_generation_id(
            event.get("harness_generation_id")
        )
        route = _require_enum(event.get("route"), ROUTES, "event.route")
        tool_outcome = _require_enum(
            event.get("tool_outcome"), TOOL_OUTCOMES, "event.tool_outcome"
        )
        sensitivity = _require_enum(
            event.get("sensitivity") or "public_ops",
            SENSITIVITIES,
            "event.sensitivity",
        )

        if force_outcome_source is not None:
            outcome_source = _require_enum(
                force_outcome_source, OUTCOME_SOURCES, "outcome_source"
            )
        else:
            outcome_source = _require_enum(
                event.get("outcome_source"),
                OUTCOME_SOURCES,
                "event.outcome_source",
            )
        allowed = allowed_outcome_sources or OUTCOME_SOURCES
        if outcome_source not in allowed:
            raise ValueError(
                f"event.outcome_source {outcome_source!r} not allowed "
                f"(allowed={sorted(allowed)})"
            )

        task_outcome = event.get("task_outcome")
        if task_outcome is not None:
            task_outcome = _require_enum(
                task_outcome, TASK_OUTCOMES, "event.task_outcome"
            )

        schema_version = str(
            event.get("schema_version") or RUN_EVENT_SCHEMA_VERSION
        ).strip()
        taxonomy_version = str(
            event.get("taxonomy_version") or SENSOR_TAXONOMY_VERSION
        ).strip()
        tool = _optional_bounded_text(event.get("tool"), "event.tool", MAX_TOOL_LEN)
        approach = _optional_bounded_text(
            event.get("approach"), "event.approach", MAX_APPROACH_LEN
        )
        error_class = _optional_bounded_text(
            event.get("error_class"), "event.error_class", MAX_ERROR_CLASS_LEN
        )
        decision_point = _optional_bounded_text(
            event.get("decision_point"), "event.decision_point", MAX_APPROACH_LEN
        )
        redacted_summary = _optional_bounded_text(
            event.get("redacted_summary"), "event.redacted_summary", MAX_SUMMARY_LEN
        )
        trace_id = _optional_bounded_text(
            event.get("trace_id"), "event.trace_id", MAX_TRACE_LEN
        )
        attempt_id = _optional_bounded_text(
            event.get("attempt_id"), "event.attempt_id", MAX_TRACE_LEN
        )
        session_ref = _optional_bounded_text(
            event.get("session_ref"), "event.session_ref", MAX_SESSION_REF_LEN
        )
        host = _optional_bounded_text(event.get("host"), "event.host", MAX_HOST_LEN)
        recurrence_key = _optional_bounded_text(
            event.get("recurrence_key"), "event.recurrence_key", MAX_RECURRENCE_KEY_LEN
        )
        entity_refs = _normalize_ref_list(event.get("entity_refs"), "event.entity_refs")
        journal_refs = _normalize_ref_list(
            event.get("journal_refs"), "event.journal_refs"
        )
        latency_ms = _optional_non_negative_int(event.get("latency_ms"), "event.latency_ms")
        eligible_exposure = event.get("eligible_exposure")
        if eligible_exposure is not None and not isinstance(eligible_exposure, bool):
            raise TypeError("event.eligible_exposure must be a bool or null")

        observed_at = event.get("observed_at")
        if observed_at is not None:
            observed_at = str(observed_at)
        ingested_at = event.get("ingested_at")
        if ingested_at is not None:
            ingested_at = str(ingested_at)

        # Optional denormalized diagnostics (not part of identity fingerprint).
        plugin_version = _optional_bounded_text(
            event.get("plugin_version"), "event.plugin_version", MAX_TOOL_LEN
        )
        policy_digest = _optional_bounded_text(
            event.get("policy_digest"), "event.policy_digest", 128
        )
        mcp_version = _optional_bounded_text(
            event.get("mcp_version"), "event.mcp_version", MAX_TOOL_LEN
        )
        model_id = _optional_bounded_text(
            event.get("model_id"), "event.model_id", MAX_TOOL_LEN
        )

        identity = run_event_identity_payload(
            schema_version=schema_version,
            taxonomy_version=taxonomy_version,
            harness_generation_id=harness_generation_id,
            route=route,
            tool=tool,
            tool_outcome=tool_outcome,
            task_outcome=task_outcome,
            outcome_source=outcome_source,
            approach=approach,
            error_class=error_class,
            decision_point=decision_point,
            eligible_exposure=eligible_exposure,
            entity_refs=entity_refs,
            journal_refs=journal_refs,
            redacted_summary=redacted_summary,
            sensitivity=sensitivity,
            recurrence_key=recurrence_key,
            session_ref=session_ref,
            host=host,
            trace_id=trace_id,
            attempt_id=attempt_id,
            latency_ms=latency_ms,
            observed_at=observed_at,
        )
        request_fingerprint = compute_sensor_request_fingerprint(identity)
        _assert_client_fingerprint(
            event.get("request_fingerprint"),
            request_fingerprint,
            "event.request_fingerprint",
        )

        props = {
            "id": event_id,
            "schema_version": schema_version,
            "taxonomy_version": taxonomy_version,
            "harness_generation_id": harness_generation_id,
            "route": route,
            "tool": tool,
            "tool_outcome": tool_outcome,
            "task_outcome": task_outcome,
            "outcome_source": outcome_source,
            "approach": approach,
            "error_class": error_class,
            "decision_point": decision_point,
            "eligible_exposure": eligible_exposure,
            "entity_refs": entity_refs,
            "journal_refs": journal_refs,
            "redacted_summary": redacted_summary,
            "sensitivity": sensitivity,
            "recurrence_key": recurrence_key,
            "session_ref": session_ref,
            "host": host,
            "trace_id": trace_id,
            "attempt_id": attempt_id,
            "latency_ms": latency_ms,
            "observed_at": observed_at,
            "ingested_at": ingested_at,
            "plugin_version": plugin_version,
            "policy_digest": policy_digest,
            "mcp_version": mcp_version,
            "model_id": model_id,
            "request_fingerprint": request_fingerprint,
        }

        def operation(session: Any) -> dict[str, Any]:
            try:
                return _execute_write(
                    session,
                    lambda tx: self._record_run_event_tx(
                        tx,
                        props=props,
                        event_id=event_id,
                        request_fingerprint=request_fingerprint,
                    ),
                )
            except Exception as exc:
                if not _is_uniqueness_constraint_error(exc):
                    raise
                raced = _execute_write(
                    session,
                    lambda tx: self._read_run_event_row(tx, event_id),
                )
                if raced is None:
                    raise RuntimeError(
                        "RunEvent uniqueness race but node not found on re-read"
                    ) from exc
                return self._sensor_replay_or_conflict(
                    raced,
                    request_fingerprint=request_fingerprint,
                    id_key="run_event_id",
                    reused_reason="run_event_id_reused",
                )

        return self._with_session(operation)

    def record_deterministic_run_event(
        self, event: dict[str, Any]
    ) -> dict[str, Any]:
        """Trusted internal recorder for MCP/host/user tool outcomes.

        Not model-facing: rejects ``model_advisory`` and requires an explicit
        deterministic ``outcome_source`` (``mcp`` | ``host`` | ``user``).
        """
        if not isinstance(event, dict):
            raise TypeError("event must be an object")
        source = event.get("outcome_source")
        if source is None or str(source).strip() == "":
            raise ValueError(
                "deterministic RunEvent requires outcome_source in "
                f"{sorted(DETERMINISTIC_OUTCOME_SOURCES)}"
            )
        source = str(source).strip()
        if source not in DETERMINISTIC_OUTCOME_SOURCES:
            raise ValueError(
                "deterministic RunEvent outcome_source must be one of "
                f"{sorted(DETERMINISTIC_OUTCOME_SOURCES)}; got {source!r}"
            )
        return self.record_run_event(
            event,
            allowed_outcome_sources=DETERMINISTIC_OUTCOME_SOURCES,
        )

    def _record_run_event_tx(
        self,
        tx: Any,
        *,
        props: dict[str, Any],
        event_id: str,
        request_fingerprint: str,
    ) -> dict[str, Any]:
        existing = self._read_run_event_row(tx, event_id)
        if existing is not None:
            return self._sensor_replay_or_conflict(
                existing,
                request_fingerprint=request_fingerprint,
                id_key="run_event_id",
                reused_reason="run_event_id_reused",
            )

        write_props = dict(props)
        if not write_props.get("ingested_at"):
            write_props["ingested_at"] = _now_iso(tx)
        if not write_props.get("observed_at"):
            write_props["observed_at"] = write_props["ingested_at"]

        created = _run_one(
            tx,
            """
            CREATE (e:Operational:RunEvent)
            SET e += $props
            RETURN e.id AS id,
                   e.request_fingerprint AS request_fingerprint,
                   e.route AS route,
                   e.tool AS tool,
                   e.tool_outcome AS tool_outcome,
                   e.outcome_source AS outcome_source,
                   e.harness_generation_id AS harness_generation_id,
                   e.observed_at AS observed_at,
                   e.ingested_at AS ingested_at
            """,
            {"props": write_props},
        )
        if created is None:
            raise RuntimeError("RunEvent create returned no row")
        return {
            "outcome": "created",
            "run_event_id": created.get("id"),
            "request_fingerprint": created.get("request_fingerprint"),
            "route": created.get("route"),
            "tool": created.get("tool"),
            "tool_outcome": created.get("tool_outcome"),
            "outcome_source": created.get("outcome_source"),
            "harness_generation_id": created.get("harness_generation_id"),
            "observed_at": created.get("observed_at"),
            "created_at": created.get("ingested_at"),
        }

    @staticmethod
    def _read_feedback_row(runner: Any, feedback_id: str) -> dict[str, Any] | None:
        row = _run_one(
            runner,
            """
            MATCH (f:Operational:Feedback {id: $feedback_id})
            RETURN f.id AS id,
                   f.request_fingerprint AS request_fingerprint,
                   f.kind AS kind,
                   f.sensitivity AS sensitivity,
                   f.harness_generation_id AS harness_generation_id,
                   f.raw_payload_ref AS raw_payload_ref,
                   f.created_at AS created_at
            LIMIT 1
            """,
            {"feedback_id": feedback_id},
        )
        if row is None:
            return None
        return {
            "id": row.get("id"),
            "request_fingerprint": row.get("request_fingerprint"),
            "kind": row.get("kind"),
            "sensitivity": row.get("sensitivity"),
            "harness_generation_id": row.get("harness_generation_id"),
            "raw_payload_ref": row.get("raw_payload_ref"),
            "created_at": row.get("created_at"),
        }

    @staticmethod
    def _read_lifecycle_row(runner: Any, lifecycle_id: str) -> dict[str, Any] | None:
        row = _run_one(
            runner,
            """
            MATCH (l:Operational:FeedbackLifecycleEvent {id: $lifecycle_id})
            RETURN l.id AS id,
                   l.feedback_id AS feedback_id,
                   l.event AS event,
                   l.actor AS actor,
                   l.request_fingerprint AS request_fingerprint,
                   l.created_at AS created_at
            LIMIT 1
            """,
            {"lifecycle_id": lifecycle_id},
        )
        if row is None:
            return None
        return {
            "id": row.get("id"),
            "feedback_id": row.get("feedback_id"),
            "event": row.get("event"),
            "actor": row.get("actor"),
            "request_fingerprint": row.get("request_fingerprint"),
            "created_at": row.get("created_at"),
        }

    @staticmethod
    def _read_run_event_row(runner: Any, event_id: str) -> dict[str, Any] | None:
        row = _run_one(
            runner,
            """
            MATCH (e:Operational:RunEvent {id: $event_id})
            RETURN e.id AS id,
                   e.request_fingerprint AS request_fingerprint,
                   e.route AS route,
                   e.tool AS tool,
                   e.tool_outcome AS tool_outcome,
                   e.outcome_source AS outcome_source,
                   e.harness_generation_id AS harness_generation_id,
                   e.observed_at AS observed_at,
                   e.ingested_at AS ingested_at
            LIMIT 1
            """,
            {"event_id": event_id},
        )
        if row is None:
            return None
        return {
            "id": row.get("id"),
            "request_fingerprint": row.get("request_fingerprint"),
            "route": row.get("route"),
            "tool": row.get("tool"),
            "tool_outcome": row.get("tool_outcome"),
            "outcome_source": row.get("outcome_source"),
            "harness_generation_id": row.get("harness_generation_id"),
            "observed_at": row.get("observed_at"),
            "created_at": row.get("ingested_at"),
        }

    @staticmethod
    def _sensor_replay_or_conflict(
        existing: dict[str, Any],
        *,
        request_fingerprint: str,
        id_key: str,
        reused_reason: str,
    ) -> dict[str, Any]:
        record_id = existing.get("id")
        if existing.get("request_fingerprint") == request_fingerprint:
            payload: dict[str, Any] = {
                "outcome": "replayed",
                id_key: record_id,
                "request_fingerprint": existing.get("request_fingerprint"),
                "created_at": existing.get("created_at"),
            }
            for key in (
                "kind",
                "sensitivity",
                "harness_generation_id",
                "raw_payload_ref",
                "route",
                "tool",
                "tool_outcome",
                "outcome_source",
                "observed_at",
                "feedback_id",
                "event",
                "actor",
            ):
                if key in existing and existing[key] is not None:
                    payload[key] = existing[key]
            return payload
        return {
            "outcome": "conflict",
            "reason": reused_reason,
            id_key: record_id,
            "request_fingerprint": existing.get("request_fingerprint"),
            "created_at": existing.get("created_at"),
        }


# ---------------------------------------------------------------------------
# Sensor validation + fingerprint helpers (no embeddings / journal imports)
# ---------------------------------------------------------------------------


def compute_raw_hmac(payload_text: str, *, key_version: str = RAW_HMAC_KEY_VERSION) -> str:
    """Content-binding digest for removable raw payload (not a secret MAC)."""
    return hashlib.sha256(
        f"{key_version}\0{payload_text}".encode("utf-8")
    ).hexdigest()


def compute_sensor_request_fingerprint(identity: Mapping[str, Any]) -> str:
    """Canonical request fingerprint for sensor replay-vs-conflict."""
    return hashlib.sha256(
        _canonical_json(identity).encode("utf-8")
    ).hexdigest()


def feedback_identity_payload(
    *,
    kind: str,
    sensitivity: str,
    source_turn_ref: str | None,
    redacted_summary: str | None,
    harness_generation_id: str,
    schema_version: str,
    taxonomy_version: str,
    raw_hmac: str | None,
) -> dict[str, Any]:
    """Immutable Feedback fields that define the request fingerprint.

    Raw payload text is intentionally excluded so redaction of QualityPayload
    cannot change the observation fingerprint.
    """
    return {
        "harness_generation_id": harness_generation_id,
        "kind": kind,
        "raw_hmac": raw_hmac,
        "redacted_summary": redacted_summary,
        "schema_version": schema_version,
        "sensitivity": sensitivity,
        "source_turn_ref": source_turn_ref,
        "taxonomy_version": taxonomy_version,
    }


def lifecycle_identity_payload(
    *,
    feedback_id: str,
    event: str,
    actor: str,
    reason_code: str | None,
) -> dict[str, Any]:
    return {
        "actor": actor,
        "event": event,
        "feedback_id": feedback_id,
        "reason_code": reason_code,
    }


def run_event_identity_payload(
    *,
    schema_version: str,
    taxonomy_version: str,
    harness_generation_id: str,
    route: str,
    tool: str | None,
    tool_outcome: str,
    task_outcome: str | None,
    outcome_source: str,
    approach: str | None,
    error_class: str | None,
    decision_point: str | None,
    eligible_exposure: bool | None,
    entity_refs: list[str],
    journal_refs: list[str],
    redacted_summary: str | None,
    sensitivity: str,
    recurrence_key: str | None,
    session_ref: str | None,
    host: str | None,
    trace_id: str | None,
    attempt_id: str | None,
    latency_ms: int | None,
    observed_at: str | None,
) -> dict[str, Any]:
    """Identity fields for RunEvent (ingested_at is bookkeeping only)."""
    return {
        "approach": approach,
        "attempt_id": attempt_id,
        "decision_point": decision_point,
        "eligible_exposure": eligible_exposure,
        "entity_refs": entity_refs,
        "error_class": error_class,
        "harness_generation_id": harness_generation_id,
        "host": host,
        "journal_refs": journal_refs,
        "latency_ms": latency_ms,
        "observed_at": observed_at,
        "outcome_source": outcome_source,
        "recurrence_key": recurrence_key,
        "redacted_summary": redacted_summary,
        "route": route,
        "schema_version": schema_version,
        "sensitivity": sensitivity,
        "session_ref": session_ref,
        "task_outcome": task_outcome,
        "taxonomy_version": taxonomy_version,
        "tool": tool,
        "tool_outcome": tool_outcome,
        "trace_id": trace_id,
    }


def build_tool_outcome_run_event(
    *,
    event_id: str,
    harness_generation_id: str,
    tool: str,
    tool_outcome: str,
    route: str,
    outcome_source: str,
    error_class: str | None = None,
    approach: str | None = None,
    redacted_summary: str | None = None,
    latency_ms: int | None = None,
    entity_refs: list[str] | None = None,
    journal_refs: list[str] | None = None,
    sensitivity: str = "public_ops",
    session_ref: str | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic tool-outcome RunEvent payload (host/MCP use).

    Used to instrument meaningful READ empty/fail and WRITE conflict/timeout
    paths without relying on model prose.
    """
    return {
        "id": event_id,
        "harness_generation_id": harness_generation_id,
        "route": route,
        "tool": tool,
        "tool_outcome": tool_outcome,
        "outcome_source": outcome_source,
        "error_class": error_class,
        "approach": approach,
        "redacted_summary": redacted_summary,
        "latency_ms": latency_ms,
        "entity_refs": entity_refs or [],
        "journal_refs": journal_refs or [],
        "sensitivity": sensitivity,
        "session_ref": session_ref,
        "observed_at": observed_at,
        "schema_version": RUN_EVENT_SCHEMA_VERSION,
        "taxonomy_version": SENSOR_TAXONOMY_VERSION,
    }


def _read_generation_id_from_pin_file(path: str) -> str | None:
    """Extract a harness generation id from a pin JSON, env-file, or id file.

    Never returns SOUL content — only a short id string.
    """
    raw_path = (path or "").strip()
    if not raw_path:
        return None
    try:
        from pathlib import Path

        pin = Path(raw_path).expanduser()
        if not pin.is_file():
            return None
        text = pin.read_text(encoding="utf-8")
        name = pin.name
        suffix = pin.suffix
    except (OSError, UnicodeError):
        return None
    if not text or not text.strip():
        return None

    stripped = text.strip()
    # Env-file style: KEY=VALUE lines.
    looks_like_env = (
        "DIGITAL_BRAIN_HARNESS_GENERATION_ID=" in stripped
        or suffix == ".env"
        or name.endswith(".env")
    )
    if looks_like_env:
        for line in stripped.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if line.startswith("DIGITAL_BRAIN_HARNESS_GENERATION_ID="):
                value = line.split("=", 1)[1].strip().strip("'\"")
                return value or None

    # JSON pin (session pin or active/harness_generation.json).
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            for key in ("id", "generation_id", "harness_generation_id"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    # Plain id file (active/harness_generation.id) — single token, no SOUL body.
    first = stripped.splitlines()[0].strip()
    if first and len(first) <= 200 and " " not in first and "\t" not in first:
        return first
    return None


def _resolve_state_dir_for_pin() -> str | None:
    """Best-effort state dir for the well-known active pin (MCP + host)."""
    env = (os.getenv("DIGITAL_BRAIN_STATE_DIR") or "").strip()
    if env:
        return env
    xdg = (os.getenv("XDG_STATE_HOME") or "").strip()
    if xdg:
        from pathlib import Path

        return str(Path(xdg).expanduser() / "digital-brain")
    home = (os.getenv("HOME") or "").strip()
    if home:
        from pathlib import Path

        return str(Path(home).expanduser() / ".local" / "state" / "digital-brain")
    return None


def resolve_session_harness_generation_id(
    explicit: str | None = None,
) -> str | None:
    """Resolve the session-pinned harness generation id.

    Order:
    1. Explicit argument
    2. ``DIGITAL_BRAIN_HARNESS_GENERATION_ID`` env
    3. ``DIGITAL_BRAIN_HARNESS_PIN_PATH`` (JSON pin, env-file, or id file)
    4. Well-known active pin under ``DIGITAL_BRAIN_STATE_DIR``:
       ``active/harness_generation.id`` or ``active/harness_generation.json``

    Returns ``None`` when no pin is available (instrumentation must skip, not
    fail the primary tool path). Never reads SOUL body content.
    """
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()

    pinned = (os.getenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID") or "").strip()
    if pinned:
        return pinned

    pin_path = (os.getenv("DIGITAL_BRAIN_HARNESS_PIN_PATH") or "").strip()
    if pin_path:
        from_path = _read_generation_id_from_pin_file(pin_path)
        if from_path:
            return from_path

    state_dir = _resolve_state_dir_for_pin()
    if state_dir:
        from pathlib import Path

        base = Path(state_dir).expanduser()
        for candidate in (
            base / "active" / "harness_generation.id",
            base / "active" / "harness_generation.json",
        ):
            from_active = _read_generation_id_from_pin_file(str(candidate))
            if from_active:
                return from_active

    return None


def mint_tool_outcome_event_id(tool: str, tool_outcome: str) -> str:
    """Stable-prefix event id for instrumented tool outcomes (unique per emit)."""
    safe_tool = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in tool)[
        :48
    ]
    safe_outcome = "".join(
        ch if ch.isalnum() or ch in "-_" else "-" for ch in tool_outcome
    )[:24]
    return f"re-{safe_tool}-{safe_outcome}-{uuid.uuid4().hex[:16]}"


def try_record_tool_outcome_run_event(
    record_fn: Callable[[dict[str, Any]], dict[str, Any]] | None,
    *,
    tool: str,
    tool_outcome: str,
    route: str,
    outcome_source: str,
    harness_generation_id: str | None = None,
    error_class: str | None = None,
    approach: str | None = None,
    redacted_summary: str | None = None,
    latency_ms: int | None = None,
    session_ref: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any] | None:
    """Best-effort deterministic tool-outcome RunEvent recording.

    Never raises: instrumentation must not break the primary tool path.
    Skips when ``record_fn`` is missing or no session harness pin is available.
    Uses :func:`build_tool_outcome_run_event` + trusted ``record_fn``
    (typically ``QualityStore.record_deterministic_run_event``).
    """
    if record_fn is None:
        return None
    generation_id = resolve_session_harness_generation_id(harness_generation_id)
    if generation_id is None:
        _logger.debug(
            "skip tool-outcome RunEvent (%s/%s): no harness_generation_id pin",
            tool,
            tool_outcome,
        )
        return None
    try:
        payload = build_tool_outcome_run_event(
            event_id=event_id
            or mint_tool_outcome_event_id(tool, tool_outcome),
            harness_generation_id=generation_id,
            tool=tool,
            tool_outcome=tool_outcome,
            route=route,
            outcome_source=outcome_source,
            error_class=error_class,
            approach=approach,
            redacted_summary=redacted_summary,
            latency_ms=latency_ms,
            session_ref=session_ref,
        )
        return record_fn(payload)
    except Exception as exc:  # noqa: BLE001 — best-effort instrumentation
        _logger.warning(
            "tool-outcome RunEvent instrumentation failed (%s/%s): %s",
            tool,
            tool_outcome,
            exc,
        )
        return None


def _now_iso(runner: Any) -> str:
    row = _run_one(runner, "RETURN toString(datetime()) AS ts")
    return (row or {}).get("ts") or "unknown"


def _require_sensor_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    cleaned = value.strip()
    if len(cleaned) > MAX_SENSOR_ID_LEN:
        raise ValueError(f"{field_name} exceeds max length {MAX_SENSOR_ID_LEN}")
    return cleaned


def _require_generation_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "harness_generation_id is required (session pin "
            "DIGITAL_BRAIN_HARNESS_GENERATION_ID)"
        )
    cleaned = value.strip()
    if len(cleaned) > MAX_SENSOR_ID_LEN:
        raise ValueError(
            f"harness_generation_id exceeds max length {MAX_SENSOR_ID_LEN}"
        )
    return cleaned


def _require_enum(value: Any, allowed: frozenset[str], field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be one of {sorted(allowed)}")
    cleaned = value.strip()
    if cleaned not in allowed:
        raise ValueError(f"{field_name} must be one of {sorted(allowed)}; got {cleaned!r}")
    return cleaned


def _require_bounded_text(value: Any, field_name: str, max_len: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    cleaned = value.strip()
    if len(cleaned) > max_len:
        raise ValueError(f"{field_name} exceeds max length {max_len}")
    return cleaned


def _optional_bounded_text(
    value: Any, field_name: str, max_len: int
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or null")
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > max_len:
        raise ValueError(f"{field_name} exceeds max length {max_len}")
    return cleaned


def _normalize_ref_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list of strings")
    if len(value) > MAX_REF_COUNT:
        raise ValueError(f"{field_name} exceeds max count {MAX_REF_COUNT}")
    out: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name}[{idx}] must be a non-empty string")
        cleaned = item.strip()
        if len(cleaned) > MAX_REF_ITEM_LEN:
            raise ValueError(
                f"{field_name}[{idx}] exceeds max length {MAX_REF_ITEM_LEN}"
            )
        out.append(cleaned)
    # Stable order for fingerprinting without losing multiset semantics.
    return sorted(out)


def _optional_non_negative_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer or null")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _assert_client_fingerprint(
    client_value: Any, server_value: str, field_name: str
) -> None:
    if client_value is None:
        return
    client = str(client_value).strip()
    if not client:
        return
    if client != server_value:
        raise ValueError(
            f"{field_name} does not match identity fields "
            f"(server={server_value[:16]}… client={client[:16]}…)"
        )
