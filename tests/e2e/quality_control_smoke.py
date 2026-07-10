"""Isolated Neo4j smoke for the Operational / role boundary.

Run against a live Neo4j with roles applied (see scripts/init_quality_roles.py).
Exits 0 on success.

By default, missing credentials or an unavailable Neo4j yield skip (exit 0) so
unit CI without the stack still passes. Set DIGITAL_BRAIN_REQUIRE_ROLE_SMOKE=1
to fail instead of skip — use this after bootstrap with
DIGITAL_BRAIN_APPLY_QUALITY_ROLES=1.

  NEO4J_URI=bolt://localhost:7687 \
  NEO4J_RUNTIME_USERNAME=digital_brain_runtime \
  NEO4J_RUNTIME_PASSWORD=... \
  NEO4J_QUALITY_USERNAME=digital_brain_quality \
  NEO4J_QUALITY_PASSWORD=... \
  DIGITAL_BRAIN_REQUIRE_ROLE_SMOKE=1 \
  uv run --group dev python tests/e2e/quality_control_smoke.py
"""

from __future__ import annotations

import os
import sys
from typing import Any


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _require_role_smoke() -> bool:
    return (_env("DIGITAL_BRAIN_REQUIRE_ROLE_SMOKE") or "0") == "1"


def _skip_or_fail(message: str) -> int:
    if _require_role_smoke():
        print(f"FAIL: required role smoke cannot run: {message}", file=sys.stderr)
        return 1
    print(f"skip: {message}")
    return 0


def _driver(user: str, password: str):
    from neo4j import GraphDatabase

    uri = _env("NEO4J_URI") or _env("NEO4J_URL") or "bolt://localhost:7687"
    return GraphDatabase.driver(uri, auth=(user, password))


def _run(user: str, password: str, query: str, params: dict[str, Any] | None = None):
    database = _env("NEO4J_DATABASE") or "neo4j"
    driver = _driver(user, password)
    try:
        with driver.session(database=database) as session:
            result = session.run(query, params or {})
            rows = [record.data() for record in result]
            result.consume()
            return rows
    finally:
        driver.close()


def _expect_denied(user: str, password: str, query: str, label: str) -> None:
    try:
        _run(user, password, query)
    except Exception as exc:  # neo4j.exceptions.ClientError / Forbidden
        message = str(exc).lower()
        if "denied" in message or "permission" in message or "access" in message:
            print(f"ok: runtime denied {label}")
            return
        raise AssertionError(f"expected permission deny for {label}, got: {exc}") from exc
    raise AssertionError(f"runtime role was allowed to run protected query: {label}")


# Labels previously under-denied (CREATE-only coverage gaps) must be probed live.
_GAP_SET_LABEL_PROBES: tuple[tuple[str, str], ...] = (
    (
        "CREATE (n:Person {id: 'quality-smoke-setlab-qp'}) SET n:QualityPayload RETURN n",
        "SET LABEL QualityPayload",
    ),
    (
        "CREATE (n:Person {id: 'quality-smoke-setlab-dec'}) SET n:Decision RETURN n",
        "SET LABEL Decision",
    ),
    (
        "CREATE (n:Person {id: 'quality-smoke-setlab-ep'}) SET n:EntityProtection RETURN n",
        "SET LABEL EntityProtection",
    ),
)

_GAP_CREATE_PROBES: tuple[tuple[str, str], ...] = (
    (
        "CREATE (n:QualityPayload {id: 'quality-smoke-qp'}) RETURN n",
        "CREATE QualityPayload",
    ),
    (
        "CREATE (n:Decision {id: 'quality-smoke-dec'}) RETURN n",
        "CREATE Decision",
    ),
    (
        "CREATE (n:EntityProtection {id: 'quality-smoke-ep'}) RETURN n",
        "CREATE EntityProtection",
    ),
)

_GAP_SET_PROPERTY_PROBES: tuple[tuple[str, str], ...] = (
    (
        "CREATE (n:Decision {id: 'quality-smoke-dec-prop', status: 'x'}) RETURN n",
        "CREATE+props Decision (also CREATE deny)",
    ),
    # SET property on an existing Decision-only node (quality role seeds it).
    (
        "MATCH (n:Decision {id: 'quality-smoke-dec-seed'}) SET n.status = 'hacked' RETURN n",
        "SET PROPERTY on Decision-only node",
    ),
)


def main() -> int:
    runtime_user = _env("NEO4J_RUNTIME_USERNAME")
    runtime_password = _env("NEO4J_RUNTIME_PASSWORD")
    quality_user = _env("NEO4J_QUALITY_USERNAME")
    quality_password = _env("NEO4J_QUALITY_PASSWORD")

    if not runtime_user or not runtime_password:
        return _skip_or_fail("NEO4J_RUNTIME_USERNAME/PASSWORD not set")

    try:
        from neo4j import GraphDatabase  # noqa: F401
    except ImportError:
        return _skip_or_fail("neo4j driver not installed")

    # Connectivity
    try:
        rows = _run(runtime_user, runtime_password, "RETURN 1 AS ok")
    except Exception as exc:
        return _skip_or_fail(f"runtime cannot connect ({exc})")
    if not rows or rows[0].get("ok") != 1:
        return _skip_or_fail("unexpected runtime probe response")

    # Life-graph write still allowed.
    _run(
        runtime_user,
        runtime_password,
        "MERGE (p:Person {id: $id}) SET p.name = $name RETURN p.id AS id",
        {"id": "quality-smoke-person", "name": "Quality Smoke"},
    )
    print("ok: runtime can MERGE Person")

    # Lexical-bypass-ish shapes that a simple "Operational" regex might miss:
    # create Feedback without Operational, or SET property via relationship.
    _expect_denied(
        runtime_user,
        runtime_password,
        "CREATE (f:Feedback {id: 'quality-smoke-feedback-bypass'}) RETURN f",
        "CREATE Feedback (no Operational literal)",
    )
    _expect_denied(
        runtime_user,
        runtime_password,
        "CREATE (a:Alias {from_name: 'quality-smoke-alias'}) RETURN a",
        "CREATE Alias",
    )
    _expect_denied(
        runtime_user,
        runtime_password,
        "CREATE (n:Person {id: 'quality-smoke-setlab'}) SET n:Operational RETURN n",
        "SET LABEL Operational",
    )
    _expect_denied(
        runtime_user,
        runtime_password,
        "CREATE (n:Operational {id: 'quality-smoke-op'}) RETURN n",
        "CREATE Operational",
    )

    # Previously incomplete DENY surface (QualityPayload / Decision / EntityProtection).
    for query, label in _GAP_CREATE_PROBES:
        _expect_denied(runtime_user, runtime_password, query, label)
    for query, label in _GAP_SET_LABEL_PROBES:
        _expect_denied(runtime_user, runtime_password, query, label)

    # Quality role can create Operational control records when credentials exist.
    if quality_user and quality_password:
        _run(
            quality_user,
            quality_password,
            "MERGE (r:Operational:EffectReceipt {id: $id}) "
            "SET r.status = 'smoke', r.request_fingerprint = 'smoke' "
            "RETURN r.id AS id",
            {"id": "quality-smoke-receipt"},
        )
        print("ok: quality role can MERGE Operational:EffectReceipt")

        # Seed a Decision-only node so runtime SET PROPERTY can be probed.
        _run(
            quality_user,
            quality_password,
            "MERGE (d:Decision {id: $id}) SET d.status = 'seeded' RETURN d.id AS id",
            {"id": "quality-smoke-dec-seed"},
        )
        print("ok: quality role seeded Decision for property probe")

        for query, label in _GAP_SET_PROPERTY_PROBES:
            _expect_denied(runtime_user, runtime_password, query, label)

        _run(
            quality_user,
            quality_password,
            "MATCH (r:Operational:EffectReceipt {id: $id}) DETACH DELETE r",
            {"id": "quality-smoke-receipt"},
        )
        _run(
            quality_user,
            quality_password,
            "MATCH (d:Decision {id: $id}) DETACH DELETE d",
            {"id": "quality-smoke-dec-seed"},
        )
        print("ok: quality role cleaned smoke control nodes")
    else:
        # Without quality creds we can still prove CREATE deny for Decision props.
        _expect_denied(
            runtime_user,
            runtime_password,
            "CREATE (n:Decision {id: 'quality-smoke-dec-prop', status: 'x'}) RETURN n",
            "CREATE Decision with properties",
        )
        if _require_role_smoke():
            return _skip_or_fail(
                "NEO4J_QUALITY_USERNAME/PASSWORD required when "
                "DIGITAL_BRAIN_REQUIRE_ROLE_SMOKE=1 (SET PROPERTY probes need a seed)"
            )
        print("skip: quality credentials not set (runtime denies still verified)")

    # Cleanup life-graph smoke node (runtime may delete non-protected Person).
    # Also sweep any Person stubs left from failed SET LABEL attempts (should not exist if denied).
    _run(
        runtime_user,
        runtime_password,
        "MATCH (p:Person) WHERE p.id STARTS WITH 'quality-smoke-' DETACH DELETE p",
    )
    print("ok: quality control smoke passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
