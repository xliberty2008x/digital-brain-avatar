"""FastMCP server exposing Neo4j Cypher tools for Digital Brain."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any

from fastmcp.server import FastMCP
from mcp.types import ToolAnnotations
from neo4j import GraphDatabase
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from .embeddings import (
    EmbeddingConfig,
    EmbeddingRequestError,
    embed_text as generate_embedding,
)
from .journal import (
    PRIMARY_JOURNAL_CHAIN_KEY,
    JournalStore,
    build_append_request,
    replay_or_key_conflict,
)
from .maintenance import MaintenanceStore
from .quality import QualityStore, try_record_tool_outcome_run_event
from .quality_control_api import handle_quality_control
from .query_tools import (
    assert_general_write_allowed,
    assert_read_only,
    serialize_records,
    validate_embedding_usage,
    with_embedding_param,
)

_logger = logging.getLogger(__name__)


mcp = FastMCP("digital-brain-mcp-cypher")

_JOURNAL_SCHEMA_LOCK = threading.Lock()
_journal_schema_ready = False
_QUALITY_SCHEMA_LOCK = threading.Lock()
_quality_schema_ready = False
JOURNAL_EMBEDDING_DIMENSIONS = 1024


def _neo4j_uri() -> str:
    return os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL") or "bolt://neo4j:7687"


def _neo4j_auth() -> tuple[str, str]:
    """Model-facing runtime credential (life-graph read/write).

    Prefer dedicated runtime role only when *both* username and password are
    set (avoids mixed auth: runtime user + admin password). Falls back to
    NEO4J_USERNAME/PASSWORD for local single-user stacks that have not yet
    applied role bootstrap (see .env.example + DIGITAL_BRAIN_APPLY_QUALITY_ROLES).
    """
    runtime_user = (os.getenv("NEO4J_RUNTIME_USERNAME") or "").strip()
    runtime_password = (os.getenv("NEO4J_RUNTIME_PASSWORD") or "").strip()
    if runtime_user and runtime_password:
        return (runtime_user, runtime_password)
    username = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "password")
    return (username, password)


def _quality_neo4j_auth() -> tuple[str, str]:
    """Quality/control credential for typed quality transactions.

    Separate from the model-facing runtime role. Falls back to runtime auth
    only when quality credentials are unset (local bootstrap convenience);
    production compose should always set NEO4J_QUALITY_*.
    """
    username = (os.getenv("NEO4J_QUALITY_USERNAME") or "").strip()
    password = (os.getenv("NEO4J_QUALITY_PASSWORD") or "").strip()
    if username and password:
        return (username, password)
    return _neo4j_auth()


def _neo4j_database() -> str:
    return os.getenv("NEO4J_DATABASE", "neo4j")


def _driver():
    return GraphDatabase.driver(_neo4j_uri(), auth=_neo4j_auth())


def _quality_driver():
    return GraphDatabase.driver(_neo4j_uri(), auth=_quality_neo4j_auth())


def _run_cypher(query: str, params: dict[str, Any] | None, write: bool) -> list[dict[str, Any]]:
    with _driver() as driver:
        with driver.session(database=_neo4j_database()) as session:
            result = session.run(query, params or {})
            records = serialize_records(list(result))
            summary = result.consume()
            if write and not records:
                counters = summary.counters
                return [
                    {
                        "_contains_updates": bool(summary.counters.contains_updates),
                        "nodes_created": counters.nodes_created,
                        "nodes_deleted": counters.nodes_deleted,
                        "relationships_created": counters.relationships_created,
                        "relationships_deleted": counters.relationships_deleted,
                        "properties_set": counters.properties_set,
                        "labels_added": counters.labels_added,
                        "labels_removed": counters.labels_removed,
                        "indexes_added": counters.indexes_added,
                        "indexes_removed": counters.indexes_removed,
                        "constraints_added": counters.constraints_added,
                        "constraints_removed": counters.constraints_removed,
                    }
                ]
            return records


def _journal_store() -> JournalStore:
    return JournalStore(_driver, _neo4j_database())


def _quality_store() -> QualityStore:
    return QualityStore(_quality_driver, _neo4j_database())


def _maintenance_store() -> MaintenanceStore:
    return MaintenanceStore(_quality_driver, _neo4j_database())


def _ensure_journal_schema() -> None:
    """Create only the new safe uniqueness constraints once per process."""
    global _journal_schema_ready
    if _journal_schema_ready:
        return
    with _JOURNAL_SCHEMA_LOCK:
        if _journal_schema_ready:
            return
        _journal_store().ensure_constraints()
        _journal_schema_ready = True


def _ensure_quality_schema() -> None:
    """Idempotent quality/control uniqueness constraints (JournalStore pattern)."""
    global _quality_schema_ready
    if _quality_schema_ready:
        return
    with _QUALITY_SCHEMA_LOCK:
        if _quality_schema_ready:
            return
        _quality_store().ensure_constraints()
        _maintenance_store().ensure_constraints()
        _quality_schema_ready = True


def _record_deterministic_run_event(event: dict[str, Any]) -> dict[str, Any]:
    """Trusted MCP-side recorder for instrumented tool outcomes."""
    try:
        _ensure_quality_schema()
    except Exception as exc:  # noqa: BLE001 — best-effort schema
        _logger.debug("quality schema ensure during instrumentation: %s", exc)
    return _quality_store().record_deterministic_run_event(event)


def _instrument_mcp_tool_outcome(
    *,
    tool: str,
    tool_outcome: str,
    route: str,
    error_class: str | None = None,
    redacted_summary: str | None = None,
) -> None:
    """Best-effort MCP-attributed RunEvent; never raises into the tool path."""
    try_record_tool_outcome_run_event(
        _record_deterministic_run_event,
        tool=tool,
        tool_outcome=tool_outcome,
        route=route,
        outcome_source="mcp",
        error_class=error_class,
        redacted_summary=redacted_summary,
    )


def _error_class_for_exception(exc: BaseException) -> str:
    name = type(exc).__name__
    message = str(exc).lower()
    if "timeout" in name.lower() or "timeout" in message:
        return "query_timeout"
    if isinstance(exc, ValueError):
        return "validation_error"
    return "query_error"


def _tool_outcome_for_exception(exc: BaseException) -> str:
    """Map exceptions to tool_outcome enums shared with host instrumentation.

    Timeouts use ``timeout`` (not ``fail``) so MCP query timeouts and host
    transport timeouts share the same outcome class; ``error_class`` still
    distinguishes ``query_timeout`` vs ``mcp_timeout`` / transport classes.
    """
    if _error_class_for_exception(exc) == "query_timeout":
        return "timeout"
    return "fail"


def _readiness() -> tuple[bool, dict[str, str]]:
    """Check both required dependencies without exposing upstream diagnostics."""
    try:
        rows = _run_cypher("RETURN 1 AS ok", None, write=False)
        if not rows or rows[0].get("ok") != 1:
            return False, {"status": "not_ready", "reason": "neo4j_unavailable"}
    except Exception:  # Neo4j errors can include credentials and host details.
        return False, {"status": "not_ready", "reason": "neo4j_unavailable"}

    try:
        embedding = generate_embedding("digital-brain readiness probe")
        config = EmbeddingConfig.from_env()
        if (
            config.dimensions != JOURNAL_EMBEDDING_DIMENSIONS
            or embedding is None
            or len(embedding) != JOURNAL_EMBEDDING_DIMENSIONS
        ):
            return False, {"status": "not_ready", "reason": "embedding_invalid"}
    except EmbeddingRequestError as exc:
        reason = {
            "timeout": "embedding_timeout",
            "oom": "embedding_oom",
            "network": "embedding_unavailable",
            "http_error": "embedding_unavailable",
            "response_error": "embedding_unavailable",
            "invalid_response": "embedding_invalid",
        }.get(exc.reason, "embedding_unavailable")
        return False, {"status": "not_ready", "reason": reason}
    except (TimeoutError, ValueError):
        return False, {"status": "not_ready", "reason": "embedding_invalid"}
    except Exception:
        return False, {"status": "not_ready", "reason": "embedding_unavailable"}

    return True, {"status": "ready"}


@mcp.custom_route("/livez", methods=["GET"], include_in_schema=False)
async def livez(_: Request) -> JSONResponse:
    """Return success once the MCP process itself has started."""
    return JSONResponse({"status": "live"})


@mcp.custom_route("/readyz", methods=["GET"], include_in_schema=False)
async def readyz(_: Request) -> JSONResponse:
    """Return success only when Neo4j and the configured embedder are usable."""
    # Both the Neo4j driver and urllib-based Ollama client are synchronous.
    # Keep their bounded (20s by default) probe off FastMCP's event loop.
    ready, payload = await asyncio.to_thread(_readiness)
    return JSONResponse(payload, status_code=200 if ready else 503)


@mcp.custom_route("/internal/quality-control", methods=["POST"], include_in_schema=False)
async def quality_control(request: Request) -> JSONResponse:
    """Authenticated non-MCP coordinator control API (local host only).

    Never registered as a FastMCP tool. Requires
    ``X-Digital-Brain-Coordinator-Secret``. Analyzer/evaluator environments
    must not receive ``DIGITAL_BRAIN_COORDINATOR_SECRET``.
    """
    def _maintenance_dispatch(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        # Lazy schema ensure only when a workflow op is authorized — never on
        # the auth/ping path (unit tests must not require a live Neo4j).
        try:
            _ensure_quality_schema()
        except Exception as exc:  # noqa: BLE001 — best-effort; write will surface errors
            _logger.debug("maintenance schema ensure: %s", exc)
        return _maintenance_store().dispatch(operation, payload)

    return await handle_quality_control(
        request,
        quality_ping=lambda: _quality_store().ping(),
        maintenance_dispatch=_maintenance_dispatch,
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Neo4j Schema",
        description="Inspect labels, relationship types, and property keys.",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def get_neo4j_schema(sample_size: int | None = Field(default=100, ge=1, le=1000)) -> str:
    sample_size = sample_size or 100
    query = """
    CALL apoc.meta.schema({sample: $sample_size})
    YIELD value
    RETURN value
    """
    rows = _run_cypher(query, {"sample_size": sample_size}, write=False)
    if rows and "value" in rows[0]:
        return json.dumps(rows[0]["value"], ensure_ascii=False, default=str)
    return "{}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="Read Neo4j Cypher",
        description="Execute a read-only Cypher query.",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,
    )
)
def read_neo4j_cypher(
    query: str = Field(..., description="Read-only Cypher query"),
    params: dict[str, Any] | None = Field(default=None, description="Cypher parameters"),
    embed_text: str | None = Field(default=None, description="Text to embed into `$embedding`"),
) -> str:
    try:
        assert_read_only(query)
        embedding = generate_embedding(embed_text)
        rows = _run_cypher(query, with_embedding_param(params, embedding), write=False)
    except Exception as exc:
        _instrument_mcp_tool_outcome(
            tool="read_neo4j_cypher",
            tool_outcome=_tool_outcome_for_exception(exc),
            route="READ",
            error_class=_error_class_for_exception(exc),
        )
        raise
    if not rows:
        # Empty-result instrumentation: any zero-row READ emits a RunEvent.
        # High-volume legitimate empty queries can fill the quality plane —
        # prefer targeted reads when pinning sessions that instrument READs.
        _instrument_mcp_tool_outcome(
            tool="read_neo4j_cypher",
            tool_outcome="empty",
            route="READ",
            error_class="no_hits",
        )
    return json.dumps(rows, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Write Neo4j Cypher",
        description="Execute a write Cypher query.",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
    )
)
def write_neo4j_cypher(
    query: str = Field(
        ...,
        description=(
            "Write Cypher for post-append graph links only (MATCH/MERGE). "
            "Cannot create JournalEntry/FOLLOWS/HEAD/JournalChain or mutate "
            "protected journal fields; use append_journal_entry for the chain."
        ),
    ),
    params: dict[str, Any] | None = Field(default=None, description="Cypher parameters"),
    embed_text: str | None = Field(default=None, description="Text to embed into `$embedding`"),
) -> str:
    try:
        assert_general_write_allowed(query)
        validate_embedding_usage(query, embed_text)
        embedding = generate_embedding(embed_text)
        rows = _run_cypher(query, with_embedding_param(params, embedding), write=True)
    except Exception as exc:
        _instrument_mcp_tool_outcome(
            tool="write_neo4j_cypher",
            tool_outcome=_tool_outcome_for_exception(exc),
            route="WRITE",
            error_class=_error_class_for_exception(exc),
        )
        raise
    return json.dumps(rows, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Journal Chain Head",
        description="Read the current primary JournalChain version and head metadata.",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def get_journal_chain_head() -> str:
    """Return the current chain version without exposing journal content."""
    payload = _journal_store().get_chain_head(PRIMARY_JOURNAL_CHAIN_KEY)
    return json.dumps(payload, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Journal Append Receipt",
        description="Look up a journal append by its UUID idempotency key.",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def get_journal_append_receipt(
    append_key: str = Field(..., description="UUID generated once for the append operation"),
) -> str:
    """Return a receipt that callers can use after an ambiguous timeout."""
    payload = _journal_store().get_receipt(append_key)
    return json.dumps(payload, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Bootstrap Journal Chain",
        description="Initialize the protected primary JournalChain from a reviewed legacy head or an empty graph.",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def bootstrap_journal_chain(
    head_element_id: str | None = Field(
        default=None,
        description="Reviewed legacy JournalEntry elementId; required unless empty=True",
    ),
    empty: bool = Field(
        default=False,
        description="Only for a graph that contains no JournalEntry nodes",
    ),
) -> str:
    """Bootstrap the chain through a dedicated, non-generic mutation path."""
    _ensure_journal_schema()
    payload = _journal_store().bootstrap(
        head_element_id=head_element_id,
        empty=empty,
        chain_key=PRIMARY_JOURNAL_CHAIN_KEY,
    )
    return json.dumps(payload, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Quality Receipt",
        description=(
            "Look up a quality sensor/control receipt by stable id. Resolves "
            "Feedback, RunEvent, FeedbackLifecycleEvent, and EffectReceipt. "
            "Use after transport timeout instead of blind-retrying a write. "
            "Model-facing read surface; coordinator mutations use the "
            "authenticated local control API."
        ),
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def get_quality_receipt(
    receipt_id: str = Field(
        ...,
        description=(
            "Stable id for Feedback, RunEvent, FeedbackLifecycleEvent, or "
            "EffectReceipt reconciliation"
        ),
    ),
) -> str:
    """Read-only quality receipt reconciliation helper."""
    try:
        _ensure_quality_schema()
    except Exception:
        # Constraints may already exist from admin bootstrap; reads still work.
        pass
    payload = _quality_store().get_receipt(receipt_id)
    return json.dumps(payload, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Harness Generation",
        description=(
            "Read back a pinned Operational:HarnessGeneration by id for "
            "session reconciliation. Returns digests only — never SOUL content."
        ),
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def get_harness_generation(
    generation_id: str = Field(..., description="Stable HarnessGeneration id"),
) -> str:
    """Reconcile a session pin against the quality ledger."""
    try:
        _ensure_quality_schema()
    except Exception:
        pass
    payload = _quality_store().get_harness_generation(generation_id)
    return json.dumps(payload, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Record Harness Generation",
        description=(
            "Idempotently record an Operational:HarnessGeneration for the "
            "session pin. Replay/conflict by id + request_fingerprint. "
            "SOUL body is rejected; only soul_sha is stored."
        ),
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def record_harness_generation(
    id: str = Field(..., description="Stable generation id (hg-<fingerprint>)"),
    core_commit: str = Field(..., description="Git commit SHA or unknown"),
    core_tree_digest: str = Field(..., description="Git tree digest"),
    dirty_state_digest: str = Field(..., description="Dirty worktree digest"),
    plugin_version: str = Field(..., description="digital-brain-buddy plugin version"),
    soul_sha: str = Field(..., description="Local SHA-256 of SOUL file bytes only"),
    overlay_manifest_digest: str = Field(..., description="Active overlay manifest digest"),
    policy_digest: str = Field(..., description="Active policy digest"),
    mcp_version: str = Field(..., description="MCP server version"),
    schema_version: str = Field(..., description="HarnessGeneration schema version"),
    taxonomy_version: str = Field(..., description="Evidence taxonomy version"),
    request_fingerprint: str = Field(..., description="Canonical identity fingerprint"),
    model_id: str | None = Field(default=None, description="Host model id when known"),
    created_at: str | None = Field(default=None, description="ISO timestamp; set on first create"),
) -> str:
    """Quality-plane create with replay/conflict receipt."""
    _ensure_quality_schema()
    generation = {
        "id": id,
        "core_commit": core_commit,
        "core_tree_digest": core_tree_digest,
        "dirty_state_digest": dirty_state_digest,
        "plugin_version": plugin_version,
        "soul_sha": soul_sha,
        "overlay_manifest_digest": overlay_manifest_digest,
        "policy_digest": policy_digest,
        "mcp_version": mcp_version,
        "model_id": model_id,
        "schema_version": schema_version,
        "taxonomy_version": taxonomy_version,
        "request_fingerprint": request_fingerprint,
        "created_at": created_at,
    }
    payload = _quality_store().record_harness_generation(generation)
    return json.dumps(payload, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create Feedback",
        description=(
            "Idempotently record an Operational:Feedback observation. "
            "Requires harness_generation_id. Raw text is stored separately "
            "as QualityPayload for later redaction; never journal-indexed."
        ),
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def create_feedback(
    id: str = Field(..., description="Stable feedback id (client-minted)"),
    kind: str = Field(
        ...,
        description="entity_wrong | claim_false | miss | invent | praise",
    ),
    sensitivity: str = Field(
        ...,
        description="public_ops | personal | intimate",
    ),
    harness_generation_id: str = Field(
        ...,
        description="Session-pinned HarnessGeneration id (required)",
    ),
    source_turn_ref: str | None = Field(
        default=None, description="Optional source turn reference"
    ),
    redacted_summary: str | None = Field(
        default=None, description="Bounded redacted summary (max 512 chars)"
    ),
    raw_payload: str | None = Field(
        default=None,
        description="Optional removable raw text (stored as QualityPayload)",
    ),
    schema_version: str | None = Field(default=None, description="Feedback schema version"),
    taxonomy_version: str | None = Field(
        default=None, description="Evidence taxonomy version"
    ),
    request_fingerprint: str | None = Field(
        default=None, description="Optional client fingerprint for integrity check"
    ),
    created_at: str | None = Field(default=None, description="ISO timestamp; set on create"),
) -> str:
    """Quality-plane Feedback create with replay/conflict receipt."""
    _ensure_quality_schema()
    feedback: dict[str, Any] = {
        "id": id,
        "kind": kind,
        "sensitivity": sensitivity,
        "harness_generation_id": harness_generation_id,
        "source_turn_ref": source_turn_ref,
        "redacted_summary": redacted_summary,
        "raw_payload": raw_payload,
        "schema_version": schema_version,
        "taxonomy_version": taxonomy_version,
        "request_fingerprint": request_fingerprint,
        "created_at": created_at,
    }
    payload = _quality_store().create_feedback(feedback)
    return json.dumps(payload, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Revoke Feedback",
        description=(
            "Append a revoked FeedbackLifecycleEvent for an existing Feedback. "
            "Idempotent by lifecycle event id + request fingerprint."
        ),
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def revoke_feedback(
    id: str = Field(..., description="Stable lifecycle event id (client-minted)"),
    feedback_id: str = Field(..., description="Target Feedback id"),
    actor: str = Field(..., description="Actor performing the revocation"),
    reason_code: str | None = Field(default=None, description="Optional reason code"),
    request_fingerprint: str | None = Field(
        default=None, description="Optional client fingerprint for integrity check"
    ),
    created_at: str | None = Field(default=None, description="ISO timestamp; set on create"),
) -> str:
    """Quality-plane Feedback revocation lifecycle event."""
    _ensure_quality_schema()
    revocation: dict[str, Any] = {
        "id": id,
        "feedback_id": feedback_id,
        "actor": actor,
        "reason_code": reason_code,
        "request_fingerprint": request_fingerprint,
        "created_at": created_at,
    }
    payload = _quality_store().revoke_feedback(revocation)
    return json.dumps(payload, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Record Run Event",
        description=(
            "Idempotently record an Operational:RunEvent. Model-facing path "
            "always stores outcome_source=model_advisory (callers cannot claim "
            "mcp/host/user authority). Requires harness_generation_id."
        ),
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def record_run_event(
    id: str = Field(..., description="Stable run event id (client-minted)"),
    harness_generation_id: str = Field(
        ...,
        description="Session-pinned HarnessGeneration id (required, unchanged)",
    ),
    route: str = Field(
        ...,
        description="SKIP | READ | WRITE | FEEDBACK | MAINTAIN",
    ),
    tool_outcome: str = Field(
        ...,
        description="success | fail | empty | conflict | timeout",
    ),
    tool: str | None = Field(default=None, description="Tool name when applicable"),
    outcome_source: str | None = Field(
        default=None,
        description=(
            "Ignored on model-facing path; always forced to model_advisory. "
            "Deterministic MCP/host outcomes use the trusted internal recorder."
        ),
    ),
    task_outcome: str | None = Field(
        default=None, description="success | fail | corrected | unknown"
    ),
    approach: str | None = Field(default=None, description="Advisory approach label"),
    error_class: str | None = Field(default=None, description="Stable error class"),
    decision_point: str | None = Field(default=None, description="Decision point label"),
    eligible_exposure: bool | None = Field(default=None, description="Exposure eligibility"),
    entity_refs: list[str] | None = Field(
        default=None, description="Bounded entity id references (max 16)"
    ),
    journal_refs: list[str] | None = Field(
        default=None, description="Bounded journal id references (max 16)"
    ),
    redacted_summary: str | None = Field(
        default=None, description="Bounded redacted summary (max 512 chars)"
    ),
    sensitivity: str | None = Field(
        default="public_ops",
        description="public_ops | personal | intimate",
    ),
    recurrence_key: str | None = Field(default=None, description="Recurrence grouping key"),
    session_ref: str | None = Field(default=None, description="Session reference"),
    host: str | None = Field(default=None, description="Host identifier"),
    trace_id: str | None = Field(default=None, description="Trace id"),
    attempt_id: str | None = Field(default=None, description="Attempt id"),
    latency_ms: int | None = Field(default=None, description="Latency in milliseconds"),
    observed_at: str | None = Field(default=None, description="Observation timestamp"),
    schema_version: str | None = Field(default=None, description="RunEvent schema version"),
    taxonomy_version: str | None = Field(
        default=None, description="Evidence taxonomy version"
    ),
    request_fingerprint: str | None = Field(
        default=None, description="Optional client fingerprint for integrity check"
    ),
    plugin_version: str | None = Field(default=None, description="Denormalized plugin version"),
    policy_digest: str | None = Field(default=None, description="Denormalized policy digest"),
    mcp_version: str | None = Field(default=None, description="Denormalized MCP version"),
    model_id: str | None = Field(default=None, description="Denormalized model id"),
) -> str:
    """Model-facing RunEvent recorder — outcome_source forced to model_advisory."""
    _ensure_quality_schema()
    # outcome_source from the model is intentionally discarded.
    _ = outcome_source
    event: dict[str, Any] = {
        "id": id,
        "harness_generation_id": harness_generation_id,
        "route": route,
        "tool_outcome": tool_outcome,
        "tool": tool,
        "task_outcome": task_outcome,
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
        "schema_version": schema_version,
        "taxonomy_version": taxonomy_version,
        "request_fingerprint": request_fingerprint,
        "plugin_version": plugin_version,
        "policy_digest": policy_digest,
        "mcp_version": mcp_version,
        "model_id": model_id,
    }
    payload = _quality_store().record_run_event(
        event,
        force_outcome_source="model_advisory",
    )
    return json.dumps(payload, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Append Journal Entry",
        description="Atomically append one embedded JournalEntry to the primary chain.",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def append_journal_entry(
    append_key: str = Field(..., description="UUID generated once before the first attempt"),
    content: str = Field(..., description="Non-empty journal entry content to embed"),
    timestamp: str = Field(..., description="Entry timestamp"),
    expected_version: int = Field(..., ge=0, description="Version returned by get_journal_chain_head"),
    mood: str | None = Field(default=None, description="Optional entry mood"),
    properties: dict[str, Any] | None = Field(
        default=None,
        description="Optional additional flat Neo4j properties; reserved journal fields are rejected",
    ),
) -> str:
    """Append with compare-and-swap and idempotency semantics.

    A receipt lookup happens before embedding, and the embedding happens before
    entering the Neo4j write transaction. This keeps retry reconciliation fast
    and never holds a JournalChain lock while Ollama is running.
    """
    request = build_append_request(
        append_key=append_key,
        content=content,
        timestamp=timestamp,
        mood=mood,
        expected_version=expected_version,
        properties=properties,
    )
    store = _journal_store()

    existing_receipt = store.find_receipt(request.append_key)
    if existing_receipt is not None:
        payload = replay_or_key_conflict(existing_receipt, request)
        if payload.get("outcome") == "conflict":
            _instrument_mcp_tool_outcome(
                tool="append_journal_entry",
                tool_outcome="conflict",
                route="WRITE",
                error_class="append_key_conflict",
            )
        return json.dumps(payload, ensure_ascii=False, default=str)

    chain = store.get_chain_head(PRIMARY_JOURNAL_CHAIN_KEY)
    if chain["outcome"] != "ok":
        chain["append_key"] = request.append_key
        return json.dumps(chain, ensure_ascii=False, default=str)

    _ensure_journal_schema()
    embedding = generate_embedding(request.content)
    if embedding is None:
        # build_append_request already rejects blank content; this protects the
        # invariant if an embedding provider is replaced in-process.
        raise RuntimeError("Journal append did not receive an embedding")
    payload = store.append(request.with_embedding(embedding), PRIMARY_JOURNAL_CHAIN_KEY)
    if payload.get("outcome") == "conflict":
        reason = str(payload.get("reason") or "chain_conflict")
        _instrument_mcp_tool_outcome(
            tool="append_journal_entry",
            tool_outcome="conflict",
            route="WRITE",
            error_class=reason if len(reason) <= 128 else "chain_conflict",
        )
    return json.dumps(payload, ensure_ascii=False, default=str)


def main() -> None:
    transport = os.getenv("NEO4J_TRANSPORT", "streamable-http")
    if transport == "stdio":
        mcp.run()
        return
    mcp.run(
        transport=transport,
        host=os.getenv("NEO4J_MCP_SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("NEO4J_MCP_SERVER_PORT", "8000")),
        path=os.getenv("NEO4J_MCP_SERVER_PATH", "/api/mcp/"),
    )


if __name__ == "__main__":
    main()
