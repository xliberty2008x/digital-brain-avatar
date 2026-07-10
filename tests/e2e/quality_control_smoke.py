"""Isolated Neo4j smoke for the Operational / role boundary.

Run against a live Neo4j with roles applied (see scripts/init_quality_roles.py).
Exits 0 on success. Skips (exit 0 with message) when runtime credentials or
Neo4j are unavailable so CI without the stack still passes unit gates.

  NEO4J_URI=bolt://localhost:7687 \
  NEO4J_RUNTIME_USERNAME=digital_brain_runtime \
  NEO4J_RUNTIME_PASSWORD=... \
  NEO4J_QUALITY_USERNAME=digital_brain_quality \
  NEO4J_QUALITY_PASSWORD=... \
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


def main() -> int:
    runtime_user = _env("NEO4J_RUNTIME_USERNAME")
    runtime_password = _env("NEO4J_RUNTIME_PASSWORD")
    quality_user = _env("NEO4J_QUALITY_USERNAME")
    quality_password = _env("NEO4J_QUALITY_PASSWORD")

    if not runtime_user or not runtime_password:
        print("skip: NEO4J_RUNTIME_USERNAME/PASSWORD not set")
        return 0

    try:
        from neo4j import GraphDatabase  # noqa: F401
    except ImportError:
        print("skip: neo4j driver not installed")
        return 0

    # Connectivity
    try:
        rows = _run(runtime_user, runtime_password, "RETURN 1 AS ok")
    except Exception as exc:
        print(f"skip: runtime cannot connect ({exc})")
        return 0
    if not rows or rows[0].get("ok") != 1:
        print("skip: unexpected runtime probe response")
        return 0

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
        _run(
            quality_user,
            quality_password,
            "MATCH (r:Operational:EffectReceipt {id: $id}) DETACH DELETE r",
            {"id": "quality-smoke-receipt"},
        )
        print("ok: quality role cleaned smoke receipt")
    else:
        print("skip: quality credentials not set (runtime denies still verified)")

    # Cleanup life-graph smoke node (runtime may delete non-protected Person).
    _run(
        runtime_user,
        runtime_password,
        "MATCH (p:Person {id: $id}) DETACH DELETE p",
        {"id": "quality-smoke-person"},
    )
    print("ok: quality control smoke passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
