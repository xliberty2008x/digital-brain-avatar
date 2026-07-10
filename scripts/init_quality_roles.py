#!/usr/bin/env python3
"""Apply Neo4j Enterprise roles for the Operational / quality boundary.

Reviewed, explicit bootstrap — never runs silently at session startup.
Uses admin credentials (NEO4J_ADMIN_* or NEO4J_USERNAME/PASSWORD) against the
system database. Model-facing MCP must mount runtime + quality credentials
only; do not put admin/operator passwords into analyzer environments.

DENY privileges are generated from
``digital_brain_mcp_cypher.quality.PROTECTED_QUALITY_LABELS`` so every
protected label gets CREATE + SET LABEL + SET PROPERTY + DELETE denied for
``digital_brain_runtime``. Regenerate the companion Cypher file with
``--write-cypher`` after changing the label set.

Usage:
  # dry-run (default): print the statements that would run
  python scripts/init_quality_roles.py

  # apply
  python scripts/init_quality_roles.py --apply

  # regenerate scripts/init-quality-roles.cypher from the same source list
  python scripts/init_quality_roles.py --write-cypher
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_MCP_SRC = ROOT / "mcp_servers" / "cypher" / "src"
if str(_MCP_SRC) not in sys.path:
    sys.path.insert(0, str(_MCP_SRC))

from digital_brain_mcp_cypher.quality import PROTECTED_QUALITY_LABELS  # noqa: E402

# Single source of truth: quality.PROTECTED_QUALITY_LABELS.
# Sorted for deterministic Cypher / dry-run output.
PROTECTED_LABELS: tuple[str, ...] = tuple(sorted(PROTECTED_QUALITY_LABELS))

# Every protected label gets the full mutation surface denied.
DENY_PRIVILEGES: tuple[str, ...] = (
    "CREATE",
    "DELETE",
    "SET PROPERTY",
    "SET LABEL",
)

CYPHER_PATH = ROOT / "scripts" / "init-quality-roles.cypher"

_CYPHER_HEADER = """\
// Deterministic Neo4j Enterprise role bootstrap for the Operational boundary.
//
// GENERATED — do not hand-edit DENY lists. Regenerate with:
//   uv run --group dev python scripts/init_quality_roles.py --write-cypher
// Source of truth: digital_brain_mcp_cypher.quality.PROTECTED_QUALITY_LABELS
//
// Run against the system database as an admin user (default neo4j), for example:
//
//   cypher-shell -u neo4j -p "$NEO4J_ADMIN_PASSWORD" -d system \\
//     -f scripts/init-quality-roles.cypher
//
// Or via the reviewed host helper:
//
//   python scripts/init_quality_roles.py --apply
//
// Parameter substitution is performed by init_quality_roles.py. When running
// cypher-shell manually, replace the $... placeholders first.
//
// Roles:
//   digital_brain_runtime  — life-graph MATCH/WRITE; DENY quality/control labels
//   digital_brain_quality  — typed quality/control transactions (Operational OK)
//
// Operator/admin (neo4j) retains full privileges for bootstrap/migration only.
// Operator activation credentials must not be mounted into model-facing MCP.

// --- runtime role ---
CREATE ROLE digital_brain_runtime IF NOT EXISTS;
CREATE USER $runtime_user IF NOT EXISTS
  SET PASSWORD $runtime_password CHANGE NOT REQUIRED
  SET STATUS ACTIVE;
// Password rotate when user already exists (idempotent re-apply):
ALTER USER $runtime_user SET PASSWORD $runtime_password CHANGE NOT REQUIRED;
GRANT ROLE digital_brain_runtime TO $runtime_user;

GRANT ACCESS ON DATABASE $database TO digital_brain_runtime;
GRANT MATCH {*} ON GRAPH $database TO digital_brain_runtime;
GRANT WRITE ON GRAPH $database TO digital_brain_runtime;
GRANT NAME MANAGEMENT ON DATABASE $database TO digital_brain_runtime;
GRANT CREATE CONSTRAINT ON DATABASE $database TO digital_brain_runtime;
GRANT CREATE INDEX ON DATABASE $database TO digital_brain_runtime;
GRANT SHOW INDEX ON DATABASE $database TO digital_brain_runtime;
GRANT SHOW CONSTRAINT ON DATABASE $database TO digital_brain_runtime;

// Deny CREATE / DELETE / SET PROPERTY / SET LABEL on every protected control
// label (PROTECTED_QUALITY_LABELS). Partial labels (e.g. Feedback without
// Operational) are also denied so a missing Operational tag cannot bypass.
"""

_CYPHER_FOOTER = """\

// --- quality role ---
CREATE ROLE digital_brain_quality IF NOT EXISTS;
CREATE USER $quality_user IF NOT EXISTS
  SET PASSWORD $quality_password CHANGE NOT REQUIRED
  SET STATUS ACTIVE;
ALTER USER $quality_user SET PASSWORD $quality_password CHANGE NOT REQUIRED;
GRANT ROLE digital_brain_quality TO $quality_user;

GRANT ACCESS ON DATABASE $database TO digital_brain_quality;
GRANT MATCH {*} ON GRAPH $database TO digital_brain_quality;
GRANT WRITE ON GRAPH $database TO digital_brain_quality;
GRANT NAME MANAGEMENT ON DATABASE $database TO digital_brain_quality;
GRANT CREATE CONSTRAINT ON DATABASE $database TO digital_brain_quality;
GRANT CREATE INDEX ON DATABASE $database TO digital_brain_quality;
GRANT SHOW INDEX ON DATABASE $database TO digital_brain_quality;
GRANT SHOW CONSTRAINT ON DATABASE $database TO digital_brain_quality;
"""


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


def deny_statement(database: str, privilege: str, label: str, role: str) -> str:
    """Build one DENY privilege statement for a protected label."""
    if privilege == "CREATE":
        return f"DENY CREATE ON GRAPH {database} NODE {label} TO {role}"
    if privilege == "DELETE":
        return f"DENY DELETE ON GRAPH {database} NODE {label} TO {role}"
    if privilege == "SET PROPERTY":
        return (
            f"DENY SET PROPERTY {{*}} ON GRAPH {database} NODE {label} TO {role}"
        )
    if privilege == "SET LABEL":
        return f"DENY SET LABEL {label} ON GRAPH {database} TO {role}"
    raise ValueError(f"unknown privilege: {privilege}")


def protected_deny_statements(
    database: str = "$database",
    role: str = "digital_brain_runtime",
    labels: tuple[str, ...] | None = None,
) -> list[str]:
    """Full DENY surface for every protected label (CREATE/DELETE/SET PROPERTY/SET LABEL)."""
    labels = labels if labels is not None else PROTECTED_LABELS
    statements: list[str] = []
    for privilege in DENY_PRIVILEGES:
        for label in labels:
            statements.append(deny_statement(database, privilege, label, role))
    return statements


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
    statements.extend(protected_deny_statements(database=db))

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


def render_cypher_file() -> str:
    """Render scripts/init-quality-roles.cypher from PROTECTED_QUALITY_LABELS."""
    deny_lines = [
        f"{stmt};" for stmt in protected_deny_statements(database="$database")
    ]
    return _CYPHER_HEADER + "\n".join(deny_lines) + "\n" + _CYPHER_FOOTER


def write_cypher_file(path: Path | None = None) -> Path:
    target = path or CYPHER_PATH
    target.write_text(render_cypher_file(), encoding="utf-8")
    return target


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
    parser.add_argument(
        "--write-cypher",
        action="store_true",
        help=(
            "Regenerate scripts/init-quality-roles.cypher from "
            "PROTECTED_QUALITY_LABELS and exit (no Neo4j connection)"
        ),
    )
    args = parser.parse_args(argv)

    if args.write_cypher:
        path = write_cypher_file()
        print(f"# wrote {path} ({len(PROTECTED_LABELS)} protected labels)")
        return 0

    cfg = _config()
    statements = build_statements(cfg)
    print(f"# target uri={cfg['uri']} database={cfg['database']}")
    print(f"# runtime_user={cfg['runtime_user']} quality_user={cfg['quality_user']}")
    print(
        f"# protected_labels={len(PROTECTED_LABELS)} "
        f"deny_privileges={list(DENY_PRIVILEGES)}"
    )
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
