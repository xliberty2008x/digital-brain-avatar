#!/usr/bin/env python3
"""Apply Neo4j Enterprise roles for the Operational / quality boundary.

Reviewed, explicit bootstrap — never runs silently at session startup.
Uses admin credentials (NEO4J_ADMIN_* or NEO4J_USERNAME/PASSWORD) against the
system database. Model-facing MCP must mount runtime + quality credentials
only; do not put admin/operator passwords into analyzer environments.

Usage:
  # dry-run (default): print the statements that would run
  python scripts/init_quality_roles.py

  # apply
  python scripts/init_quality_roles.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Labels denied for the runtime role (must match quality.PROTECTED_QUALITY_LABELS
# and scripts/init-quality-roles.cypher).
PROTECTED_LABELS = (
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
)

# Subset that also gets property/delete denies (hot mutation surface).
MUTATION_DENY_LABELS = (
    "Operational",
    "Alias",
    "LearningLog",
    "Feedback",
    "EffectReceipt",
    "RunEvent",
    "DreamRun",
    "MaintenanceLease",
    "AgentPolicyRevision",
    "PolicySlot",
    "ActivationAuthority",
    "EvaluationReceipt",
    "Deployment",
    "HarnessGeneration",
)

SET_LABEL_DENY = (
    "Operational",
    "Alias",
    "LearningLog",
    "Feedback",
    "EffectReceipt",
    "RunEvent",
    "DreamRun",
    "MaintenanceLease",
    "AgentPolicyRevision",
    "PolicySlot",
    "ActivationAuthority",
    "EvaluationReceipt",
    "Deployment",
    "HarnessGeneration",
    "Finding",
    "Proposal",
)


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _admin_auth() -> tuple[str, str]:
    user = _env("NEO4J_ADMIN_USERNAME") or _env("NEO4J_USERNAME") or "neo4j"
    password = _env("NEO4J_ADMIN_PASSWORD") or _env("NEO4J_PASSWORD")
    if not password:
        raise SystemExit(
            "NEO4J_ADMIN_PASSWORD or NEO4J_PASSWORD is required to bootstrap roles"
        )
    return user, password


def _config() -> dict[str, str]:
    runtime_user = _env("NEO4J_RUNTIME_USERNAME") or "digital_brain_runtime"
    runtime_password = _env("NEO4J_RUNTIME_PASSWORD")
    quality_user = _env("NEO4J_QUALITY_USERNAME") or "digital_brain_quality"
    quality_password = _env("NEO4J_QUALITY_PASSWORD")
    if not runtime_password:
        raise SystemExit("NEO4J_RUNTIME_PASSWORD is required")
    if not quality_password:
        raise SystemExit("NEO4J_QUALITY_PASSWORD is required")
    database = _env("NEO4J_DATABASE") or "neo4j"
    uri = _env("NEO4J_URI") or _env("NEO4J_URL") or "bolt://localhost:7687"
    return {
        "uri": uri,
        "database": database,
        "runtime_user": runtime_user,
        "runtime_password": runtime_password,
        "quality_user": quality_user,
        "quality_password": quality_password,
    }


def build_statements(cfg: dict[str, str]) -> list[str]:
    db = cfg["database"]
    runtime_user = cfg["runtime_user"]
    quality_user = cfg["quality_user"]
    # Passwords are passed as query parameters, not interpolated.
    statements: list[str] = [
        "CREATE ROLE digital_brain_runtime IF NOT EXISTS",
        (
            f"CREATE USER {runtime_user} IF NOT EXISTS "
            "SET PASSWORD $runtime_password CHANGE NOT REQUIRED SET STATUS ACTIVE"
        ),
        (
            f"ALTER USER {runtime_user} SET PASSWORD $runtime_password "
            "CHANGE NOT REQUIRED"
        ),
        f"GRANT ROLE digital_brain_runtime TO {runtime_user}",
        f"GRANT ACCESS ON DATABASE {db} TO digital_brain_runtime",
        f"GRANT MATCH {{*}} ON GRAPH {db} TO digital_brain_runtime",
        f"GRANT WRITE ON GRAPH {db} TO digital_brain_runtime",
        f"GRANT NAME MANAGEMENT ON DATABASE {db} TO digital_brain_runtime",
        # JournalStore.ensure_constraints / optional vector index DDL.
        f"GRANT CREATE CONSTRAINT ON DATABASE {db} TO digital_brain_runtime",
        f"GRANT CREATE INDEX ON DATABASE {db} TO digital_brain_runtime",
        f"GRANT SHOW INDEX ON DATABASE {db} TO digital_brain_runtime",
        f"GRANT SHOW CONSTRAINT ON DATABASE {db} TO digital_brain_runtime",
    ]
    for label in PROTECTED_LABELS:
        statements.append(
            f"DENY CREATE ON GRAPH {db} NODE {label} TO digital_brain_runtime"
        )
    for label in MUTATION_DENY_LABELS:
        statements.append(
            f"DENY DELETE ON GRAPH {db} NODE {label} TO digital_brain_runtime"
        )
        statements.append(
            f"DENY SET PROPERTY {{*}} ON GRAPH {db} NODE {label} "
            "TO digital_brain_runtime"
        )
    for label in SET_LABEL_DENY:
        statements.append(
            f"DENY SET LABEL {label} ON GRAPH {db} TO digital_brain_runtime"
        )

    statements.extend(
        [
            "CREATE ROLE digital_brain_quality IF NOT EXISTS",
            (
                f"CREATE USER {quality_user} IF NOT EXISTS "
                "SET PASSWORD $quality_password CHANGE NOT REQUIRED SET STATUS ACTIVE"
            ),
            (
                f"ALTER USER {quality_user} SET PASSWORD $quality_password "
                "CHANGE NOT REQUIRED"
            ),
            f"GRANT ROLE digital_brain_quality TO {quality_user}",
            f"GRANT ACCESS ON DATABASE {db} TO digital_brain_quality",
            f"GRANT MATCH {{*}} ON GRAPH {db} TO digital_brain_quality",
            f"GRANT WRITE ON GRAPH {db} TO digital_brain_quality",
            f"GRANT NAME MANAGEMENT ON DATABASE {db} TO digital_brain_quality",
            f"GRANT CREATE CONSTRAINT ON DATABASE {db} TO digital_brain_quality",
            f"GRANT CREATE INDEX ON DATABASE {db} TO digital_brain_quality",
            f"GRANT SHOW INDEX ON DATABASE {db} TO digital_brain_quality",
            f"GRANT SHOW CONSTRAINT ON DATABASE {db} TO digital_brain_quality",
        ]
    )
    return statements


def _is_idempotent_auth_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "old password and new password cannot be the same",
            "already exists",
            "equivalent privilege",
            "already granted",
            "already denied",
        )
    )


def apply_statements(cfg: dict[str, str], statements: list[str]) -> None:
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise SystemExit(
            "neo4j driver is required; run via `uv run --group dev python "
            "scripts/init_quality_roles.py --apply`"
        ) from exc

    admin_user, admin_password = _admin_auth()
    params = {
        "runtime_password": cfg["runtime_password"],
        "quality_password": cfg["quality_password"],
    }
    driver = GraphDatabase.driver(cfg["uri"], auth=(admin_user, admin_password))
    try:
        with driver.session(database="system") as session:
            for statement in statements:
                try:
                    session.run(statement, params).consume()
                except Exception as exc:
                    # Re-applying passwords/privileges is expected to be safe.
                    if _is_idempotent_auth_error(exc):
                        continue
                    raise
    finally:
        driver.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute against Neo4j system database (default is dry-run)",
    )
    args = parser.parse_args(argv)

    cfg = _config()
    statements = build_statements(cfg)
    print(f"# target uri={cfg['uri']} database={cfg['database']}")
    print(f"# runtime_user={cfg['runtime_user']} quality_user={cfg['quality_user']}")
    for statement in statements:
        print(f"{statement};")

    if not args.apply:
        print("\n# dry-run only; re-run with --apply to execute", file=sys.stderr)
        return 0

    apply_statements(cfg, statements)
    print("# applied successfully", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
