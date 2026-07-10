"""Contract tests for quality boundary: MCP surface and coordinator API."""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys

import pytest

pytest.importorskip("fastmcp")
pytest.importorskip("neo4j")

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))

from digital_brain_mcp_cypher import server  # noqa: E402
from digital_brain_mcp_cypher.quality_control_api import (  # noqa: E402
    COORDINATOR_FORBIDDEN_MCP_TOOL_NAMES,
    COORDINATOR_OPERATIONS,
)


def _tool_function(tool):
    return getattr(tool, "fn", tool)


def test_model_facing_mcp_exposes_only_recorder_read_quality_tools():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}

    # Model-facing quality recorder/read surface (sensors + receipts).
    for expected in (
        "get_quality_receipt",
        "create_feedback",
        "revoke_feedback",
        "record_run_event",
        "record_harness_generation",
        "get_harness_generation",
    ):
        assert expected in names

    # Coordinator / operator / activation names must never appear on FastMCP.
    for forbidden in COORDINATOR_FORBIDDEN_MCP_TOOL_NAMES:
        assert forbidden not in names, f"{forbidden} must not be model-facing MCP"


def test_coordinator_operation_names_are_not_registered_as_mcp_tools():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    for op in COORDINATOR_OPERATIONS:
        assert op not in names


def test_coordinator_route_requires_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DIGITAL_BRAIN_COORDINATOR_SECRET", "test-coord-secret")
    # Re-bind auth after env change.
    from digital_brain_mcp_cypher import quality_control_api as api

    monkeypatch.setattr(api, "coordinator_secret", lambda: "test-coord-secret")

    class FakeRequest:
        def __init__(self, headers: dict[str, str], body: bytes):
            self.headers = headers
            self._body = body

        async def body(self):
            return self._body

    # Missing secret → 401
    missing = FakeRequest({}, b"{}")
    response = asyncio.run(server.quality_control(missing))
    assert response.status_code == 401

    # Wrong secret → 401
    wrong = FakeRequest({"X-Digital-Brain-Coordinator-Secret": "nope"}, b"{}")
    response = asyncio.run(server.quality_control(wrong))
    assert response.status_code == 401


def test_coordinator_route_accepts_bounded_typed_ping(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DIGITAL_BRAIN_COORDINATOR_SECRET", "test-coord-secret")
    from digital_brain_mcp_cypher import quality_control_api as api

    monkeypatch.setattr(api, "coordinator_secret", lambda: "test-coord-secret")

    class FakeRequest:
        def __init__(self, headers: dict[str, str], body: bytes):
            self.headers = headers
            self._body = body

        async def body(self):
            return self._body

    payload = json.dumps({"operation": "ping", "payload": {}}).encode("utf-8")
    request = FakeRequest(
        {"X-Digital-Brain-Coordinator-Secret": "test-coord-secret"},
        payload,
    )
    response = asyncio.run(server.quality_control(request))
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["outcome"] == "ok"
    assert body["operation"] == "ping"


def test_coordinator_rejects_oversized_and_unknown_operations(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("DIGITAL_BRAIN_COORDINATOR_SECRET", "test-coord-secret")
    from digital_brain_mcp_cypher import quality_control_api as api

    monkeypatch.setattr(api, "coordinator_secret", lambda: "test-coord-secret")

    class FakeRequest:
        def __init__(self, headers: dict[str, str], body: bytes):
            self.headers = headers
            self._body = body

        async def body(self):
            return self._body

    huge = json.dumps({"operation": "ping", "payload": {"x": "y" * 20000}}).encode()
    response = asyncio.run(
        server.quality_control(
            FakeRequest({"X-Digital-Brain-Coordinator-Secret": "test-coord-secret"}, huge)
        )
    )
    assert response.status_code in (400, 413)

    unknown = json.dumps({"operation": "activate_alias", "payload": {}}).encode()
    response = asyncio.run(
        server.quality_control(
            FakeRequest(
                {"X-Digital-Brain-Coordinator-Secret": "test-coord-secret"},
                unknown,
            )
        )
    )
    assert response.status_code == 400


def test_get_quality_receipt_not_found_shape(monkeypatch: pytest.MonkeyPatch):
    class Store:
        def get_receipt(self, receipt_id: str):
            return {"outcome": "not_found", "receipt_id": receipt_id}

    monkeypatch.setattr(server, "_quality_store", lambda: Store())
    payload = json.loads(
        _tool_function(server.get_quality_receipt)(receipt_id="missing-id")
    )
    assert payload["outcome"] == "not_found"


def test_quality_constraints_are_idempotent_pattern():
    from digital_brain_mcp_cypher.quality import QUALITY_CONSTRAINTS, QualityStore

    assert QUALITY_CONSTRAINTS
    assert all("IF NOT EXISTS" in q for q in QUALITY_CONSTRAINTS)
    # ensure_constraints method exists (JournalStore analogue).
    assert hasattr(QualityStore, "ensure_constraints")


def test_runtime_and_quality_auth_are_separable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NEO4J_RUNTIME_USERNAME", raising=False)
    monkeypatch.delenv("NEO4J_RUNTIME_PASSWORD", raising=False)
    monkeypatch.setenv("NEO4J_USERNAME", "legacy_user")
    monkeypatch.setenv("NEO4J_PASSWORD", "legacy_pass")
    monkeypatch.setenv("NEO4J_QUALITY_USERNAME", "quality_user")
    monkeypatch.setenv("NEO4J_QUALITY_PASSWORD", "quality_pass")

    assert server._neo4j_auth() == ("legacy_user", "legacy_pass")
    assert server._quality_neo4j_auth() == ("quality_user", "quality_pass")

    monkeypatch.setenv("NEO4J_RUNTIME_USERNAME", "runtime_user")
    monkeypatch.setenv("NEO4J_RUNTIME_PASSWORD", "runtime_pass")
    assert server._neo4j_auth() == ("runtime_user", "runtime_pass")


def test_runtime_auth_requires_both_username_and_password(
    monkeypatch: pytest.MonkeyPatch,
):
    """Username-only RUNTIME_* must not mix with admin password (compose defaults)."""
    monkeypatch.setenv("NEO4J_USERNAME", "legacy_user")
    monkeypatch.setenv("NEO4J_PASSWORD", "legacy_pass")
    monkeypatch.setenv("NEO4J_RUNTIME_USERNAME", "digital_brain_runtime")
    monkeypatch.setenv("NEO4J_RUNTIME_PASSWORD", "")
    assert server._neo4j_auth() == ("legacy_user", "legacy_pass")

    monkeypatch.setenv("NEO4J_RUNTIME_PASSWORD", "runtime_pass")
    assert server._neo4j_auth() == ("digital_brain_runtime", "runtime_pass")


def test_operator_activation_env_not_required_for_mcp_runtime(monkeypatch: pytest.MonkeyPatch):
    """Operator activation credentials must not be required by model-facing MCP."""
    monkeypatch.delenv("NEO4J_OPERATOR_USERNAME", raising=False)
    monkeypatch.delenv("NEO4J_OPERATOR_PASSWORD", raising=False)
    monkeypatch.delenv("DIGITAL_BRAIN_OPERATOR_SECRET", raising=False)
    monkeypatch.delenv("NEO4J_RUNTIME_USERNAME", raising=False)
    monkeypatch.delenv("NEO4J_RUNTIME_PASSWORD", raising=False)
    # Runtime auth still resolves without operator vars.
    monkeypatch.setenv("NEO4J_USERNAME", "runtime_user")
    monkeypatch.setenv("NEO4J_PASSWORD", "runtime_pass")
    assert server._neo4j_auth()[0] == "runtime_user"
