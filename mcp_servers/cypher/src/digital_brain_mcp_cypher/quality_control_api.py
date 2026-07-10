"""Authenticated local non-MCP coordinator control API.

Coordinator operations (leases, snapshots, proposal decisions, authority,
effects, deployments) are intentionally **not** registered as FastMCP tools.
Deterministic host/coordinator code calls this HTTP surface with a shared
secret that must not be present in analyzer/evaluator environments.

Workflow stores (Task 5) implement fenced lease + DreamRun stage transitions.
Activation mint/consume and effects stay off this model-facing surface.
"""

from __future__ import annotations

import hmac
import json
import os
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse

# Max JSON body size for coordinator requests (bytes).
MAX_COORDINATOR_BODY_BYTES = 16_384

# Operations accepted on the control API. Workflow ops dispatch to MaintenanceStore.
# Activation/effect ops remain absent (not even stubs that agents might discover).
COORDINATOR_OPERATIONS: frozenset[str] = frozenset(
    {
        "ping",
        "acquire_maintenance_lease",
        "renew_maintenance_lease",
        "release_maintenance_lease",
        "create_dream_run",
        "record_dream_stage",
        "create_evidence_snapshot",
        "create_finding",
        "create_proposal",
        "record_evaluation",
        "record_decision",
    }
)

# Workflow operations implemented by MaintenanceStore.dispatch.
WORKFLOW_OPERATIONS: frozenset[str] = frozenset(
    {
        "acquire_maintenance_lease",
        "renew_maintenance_lease",
        "release_maintenance_lease",
        "create_dream_run",
        "record_dream_stage",
        "create_evidence_snapshot",
        "create_finding",
        "create_proposal",
        "record_evaluation",
        "record_decision",
        "record_retention_effect",
    }
)

# Names that must never appear on the model-facing FastMCP tool list.
COORDINATOR_FORBIDDEN_MCP_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "acquire_maintenance_lease",
        "renew_maintenance_lease",
        "release_maintenance_lease",
        "create_dream_run",
        "record_dream_stage",
        "create_evidence_snapshot",
        "create_finding",
        "create_proposal",
        "record_evaluation",
        "record_decision",
        "record_retention_effect",
        "activate_alias",
        "revoke_alias",
        "apply_alias",
        "mint_activation_authority",
        "activate_policy",
        "activate_overlay",
        "publish_deployment",
        "record_effect",
        "apply_effect",
        "operator_activate",
        "quality_control",
    }
)

COORDINATOR_SECRET_HEADER = "X-Digital-Brain-Coordinator-Secret"
COORDINATOR_SECRET_ENV = "DIGITAL_BRAIN_COORDINATOR_SECRET"


def coordinator_secret() -> str | None:
    value = os.getenv(COORDINATOR_SECRET_ENV)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _unauthorized(reason: str = "unauthorized") -> JSONResponse:
    return JSONResponse({"status": "error", "reason": reason}, status_code=401)


def _bad_request(reason: str) -> JSONResponse:
    return JSONResponse({"status": "error", "reason": reason}, status_code=400)


def _payload_too_large() -> JSONResponse:
    return JSONResponse({"status": "error", "reason": "payload_too_large"}, status_code=413)


def authorize_coordinator(request: Request) -> JSONResponse | None:
    """Return an error response when the coordinator secret is missing/wrong."""
    expected = coordinator_secret()
    if not expected:
        return JSONResponse(
            {"status": "error", "reason": "coordinator_not_configured"},
            status_code=503,
        )
    provided = request.headers.get(COORDINATOR_SECRET_HEADER) or request.headers.get(
        COORDINATOR_SECRET_HEADER.lower()
    )
    # Constant-time compare so wrong secrets do not leak via early char exit.
    if not isinstance(provided, str) or not hmac.compare_digest(provided, expected):
        return _unauthorized()
    return None


def parse_coordinator_body(raw: bytes) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    if len(raw) > MAX_COORDINATOR_BODY_BYTES:
        return None, _payload_too_large()
    if not raw:
        return None, _bad_request("empty_body")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _bad_request("invalid_json")
    if not isinstance(decoded, dict):
        return None, _bad_request("body_must_be_object")
    return decoded, None


def dispatch_coordinator(
    body: dict[str, Any],
    *,
    quality_ping: Callable[[], dict[str, Any]] | None = None,
    maintenance_dispatch: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> JSONResponse:
    operation = body.get("operation")
    if not isinstance(operation, str) or not operation.strip():
        return _bad_request("operation_required")
    operation = operation.strip()
    if operation not in COORDINATOR_OPERATIONS:
        return _bad_request("unknown_operation")

    payload = body.get("payload", {})
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return _bad_request("payload_must_be_object")
    # Bound nested payload size again after parse (string keys/values).
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return _bad_request("payload_not_serializable")
    if len(encoded.encode("utf-8")) > MAX_COORDINATOR_BODY_BYTES:
        return _payload_too_large()

    if operation == "ping":
        probe: dict[str, Any] = {"outcome": "ok", "operation": "ping"}
        if quality_ping is not None:
            try:
                probe["quality"] = quality_ping()
            except Exception:
                probe["quality"] = {"outcome": "unavailable"}
        return JSONResponse(probe, status_code=200)

    if operation in WORKFLOW_OPERATIONS:
        if maintenance_dispatch is None:
            return JSONResponse(
                {
                    "outcome": "unavailable",
                    "operation": operation,
                    "reason": "maintenance_store_not_configured",
                },
                status_code=503,
            )
        try:
            result = maintenance_dispatch(operation, payload)
        except (TypeError, ValueError) as exc:
            return JSONResponse(
                {
                    "outcome": "error",
                    "operation": operation,
                    "reason": str(exc),
                },
                status_code=400,
            )
        except Exception as exc:  # pragma: no cover - defensive
            return JSONResponse(
                {
                    "outcome": "error",
                    "operation": operation,
                    "reason": f"internal_error:{type(exc).__name__}",
                },
                status_code=500,
            )
        if not isinstance(result, dict):
            return JSONResponse(
                {
                    "outcome": "error",
                    "operation": operation,
                    "reason": "invalid_store_result",
                },
                status_code=500,
            )
        body_out = dict(result)
        body_out.setdefault("operation", operation)
        # Domain outcomes (stale_epoch, illegal_transition, held, …) are 200
        # with structured outcome — only transport/config failures use non-2xx.
        status = 200
        outcome = body_out.get("outcome")
        if outcome == "not_implemented":
            status = 501
        return JSONResponse(body_out, status_code=status)

    return JSONResponse(
        {
            "outcome": "not_implemented",
            "operation": operation,
            "reason": "deferred",
        },
        status_code=501,
    )


async def handle_quality_control(
    request: Request,
    *,
    quality_ping: Callable[[], dict[str, Any]] | None = None,
    maintenance_dispatch: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> JSONResponse:
    """HTTP entrypoint for the authenticated coordinator control API."""
    auth_error = authorize_coordinator(request)
    if auth_error is not None:
        return auth_error
    raw = await request.body()
    body, parse_error = parse_coordinator_body(raw)
    if parse_error is not None:
        return parse_error
    assert body is not None
    return dispatch_coordinator(
        body,
        quality_ping=quality_ping,
        maintenance_dispatch=maintenance_dispatch,
    )
