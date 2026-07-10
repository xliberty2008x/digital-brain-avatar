#!/usr/bin/env python3
"""Backfill Operational on legacy Alias/LearningLog nodes.

Explicit reviewed migration — never invoked from session startup or compose-up.
"""

from __future__ import annotations

import argparse
import os
import sys


def _auth() -> tuple[str, str, str, str]:
    uri = os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL") or "bolt://localhost:7687"
    user = (
        os.getenv("NEO4J_ADMIN_USERNAME")
        or os.getenv("NEO4J_QUALITY_USERNAME")
        or os.getenv("NEO4J_USERNAME")
        or "neo4j"
    )
    password = (
        os.getenv("NEO4J_ADMIN_PASSWORD")
        or os.getenv("NEO4J_QUALITY_PASSWORD")
        or os.getenv("NEO4J_PASSWORD")
    )
    database = os.getenv("NEO4J_DATABASE") or "neo4j"
    if not password:
        raise SystemExit("Neo4j password env is required")
    return uri, user, password, database


def preview() -> list[dict]:
    from neo4j import GraphDatabase

    uri, user, password, database = _auth()
    queries = (
        (
            "Alias_missing_Operational",
            "MATCH (a:Alias) WHERE NOT a:Operational RETURN count(a) AS count",
        ),
        (
            "LearningLog_missing_Operational",
            "MATCH (l:LearningLog) WHERE NOT l:Operational RETURN count(l) AS count",
        ),
    )
    rows: list[dict] = []
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database=database) as session:
            for kind, query in queries:
                record = session.run(query).single()
                rows.append({"kind": kind, "count": int(record["count"]) if record else 0})
    finally:
        driver.close()
    return rows


def apply() -> dict[str, int]:
    from neo4j import GraphDatabase

    uri, user, password, database = _auth()
    driver = GraphDatabase.driver(uri, auth=(user, password))
    result: dict[str, int] = {}
    try:
        with driver.session(database=database) as session:
            record = session.run(
                "MATCH (a:Alias) WHERE NOT a:Operational "
                "SET a:Operational RETURN count(a) AS count"
            ).single()
            result["alias_labeled"] = int(record["count"]) if record else 0
            record = session.run(
                "MATCH (l:LearningLog) WHERE NOT l:Operational "
                "SET l:Operational RETURN count(l) AS count"
            ).single()
            result["learning_log_labeled"] = int(record["count"]) if record else 0
    finally:
        driver.close()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply SET :Operational")
    args = parser.parse_args(argv)

    counts = preview()
    for row in counts:
        print(f"{row['kind']}={row['count']}")
    if not args.apply:
        print("# dry-run only; re-run with --apply to label nodes", file=sys.stderr)
        return 0
    applied = apply()
    print(f"applied={applied}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
