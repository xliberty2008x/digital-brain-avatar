#!/usr/bin/env python3
"""Manual local-only report-only DreamRun CLI.

Freezes a deterministic evidence snapshot, walks fenced stage receipts, and
prints a counts/ids report. Never activates Alias/policy/overlay, never
compiles patches, never runs retention.

Examples:

  # Synthetic fixture (no Neo4j required when --dry-store)
  uv run python scripts/digital_brain_dream.py run \\
      --evidence tests/fixtures/dreams/evidence/sample_ledger.json \\
      --cutoff 2026-07-10T12:00:00Z \\
      --generation-id hg-demo \\
      --dry-store

  # Against a live MaintenanceStore (quality credentials)
  uv run python scripts/digital_brain_dream.py run \\
      --evidence path/to/evidence.json \\
      --cutoff 2026-07-10T12:00:00Z \\
      --generation-id hg-...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_MCP_SRC = ROOT / "mcp_servers" / "cypher" / "src"
if str(_MCP_SRC) not in sys.path:
    sys.path.insert(0, str(_MCP_SRC))

from digital_brain.maintenance.runner import (  # noqa: E402
    DreamRunner,
    assert_no_activation_capability,
    maintainer_tool_profile,
)
from digital_brain.maintenance.snapshot import load_evidence_fixture  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report-only DreamRun coordinator (manual, local-only)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Execute a fenced report-only dream")
    run_p.add_argument(
        "--evidence",
        required=True,
        help="JSON file: list of evidence rows or {evidence: [...]}",
    )
    run_p.add_argument(
        "--cutoff",
        required=True,
        help="ISO-8601 cutoff; later events are excluded from the snapshot",
    )
    run_p.add_argument(
        "--generation-id",
        required=True,
        help="HarnessGeneration id bound into the snapshot",
    )
    run_p.add_argument("--run-id", default=None, help="Stable DreamRun id (resume)")
    run_p.add_argument("--holder-id", default=None, help="Lease holder identity")
    run_p.add_argument(
        "--correlation-key",
        default=None,
        help="Local HMAC key (or set DIGITAL_BRAIN_CORRELATION_HMAC_KEY)",
    )
    run_p.add_argument("--base-commit", default=None, help="Git base commit pin")
    run_p.add_argument("--graph-bookmark", default=None, help="Graph watermark")
    run_p.add_argument(
        "--holdout-ratio",
        type=float,
        default=0.2,
        help="Deterministic holdout fraction (default 0.2)",
    )
    run_p.add_argument(
        "--dry-store",
        action="store_true",
        help="Use in-memory MaintenanceStore fake (no Neo4j)",
    )
    run_p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report only",
    )

    tools_p = sub.add_parser(
        "tools",
        help="Show maintainer tool profile (proves no activation capability)",
    )
    tools_p.add_argument(
        "--all-tools",
        action="store_true",
        default=True,
        help="List the full maintainer profile (default)",
    )
    return parser


def _dry_store() -> Any:
    # Reuse the unit-test fake so offline runs need no Neo4j.
    from tests.test_mcp_cypher_maintenance import _FakeMaintSession, _store_with

    return _store_with(_FakeMaintSession())


def _live_store() -> Any:
    from neo4j import GraphDatabase

    from digital_brain_mcp_cypher.maintenance import MaintenanceStore

    uri = os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL") or "bolt://localhost:7687"
    user = (
        os.getenv("NEO4J_QUALITY_USERNAME")
        or os.getenv("NEO4J_ADMIN_USERNAME")
        or os.getenv("NEO4J_USERNAME")
        or "neo4j"
    )
    password = (
        os.getenv("NEO4J_QUALITY_PASSWORD")
        or os.getenv("NEO4J_ADMIN_PASSWORD")
        or os.getenv("NEO4J_PASSWORD")
    )
    database = os.getenv("NEO4J_DATABASE") or "neo4j"
    if not password:
        raise SystemExit(
            "Neo4j quality/admin password required "
            "(NEO4J_QUALITY_PASSWORD or NEO4J_ADMIN_PASSWORD), or pass --dry-store"
        )

    def factory():
        return GraphDatabase.driver(uri, auth=(user, password))

    return MaintenanceStore(factory, database)


def cmd_run(args: argparse.Namespace) -> int:
    evidence = load_evidence_fixture(args.evidence)
    store = _dry_store() if args.dry_store else _live_store()
    holder = args.holder_id or (os.getenv("USER") or "local-host")
    correlation_key = args.correlation_key or os.getenv(
        "DIGITAL_BRAIN_CORRELATION_HMAC_KEY"
    )
    if correlation_key is None:
        # Deterministic local default for manual dry runs only.
        correlation_key = "local-dev-correlation-key-not-for-production"

    runner = DreamRunner(
        store=store,
        holder_id=holder,
        harness_generation_id=args.generation_id,
        correlation_key=correlation_key,
        base_commit=args.base_commit,
    )
    # Capability ceiling check before any work.
    assert_no_activation_capability(runner.tool_profile(all_tools=True))

    result = runner.run(
        evidence,
        cutoff_at=args.cutoff,
        run_id=args.run_id,
        holdout_ratio=args.holdout_ratio,
        graph_bookmark=args.graph_bookmark,
    )
    public = result.to_public_dict()
    if args.json:
        print(json.dumps(public, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    report = public.get("report") or {}
    buckets = report.get("buckets") or {}
    print(f"DreamRun {public['run_id']} stage={public['stage']} "
          f"owner_status={public['owner_status']}")
    print(f"  snapshot={public.get('snapshot_id')} "
          f"digest={public.get('source_ids_digest')}")
    print(f"  processing_mode={public.get('processing_mode')} "
          f"resumed={public.get('resumed')}")
    print(
        f"  reviewed={report.get('reviewed_count', 0)} "
        f"auto_applied={report.get('auto_applied_count', 0)} "
        f"(report-only: always 0)"
    )
    for name in (
        "applied_housekeeping",
        "waiting_for_owner",
        "deliberately_left_alone",
    ):
        bucket = buckets.get(name) or {}
        print(f"  {name}: count={bucket.get('count', 0)} ids={bucket.get('ids', [])}")
    return 0


def cmd_tools(_args: argparse.Namespace) -> int:
    tools = sorted(maintainer_tool_profile(all_tools=True))
    assert_no_activation_capability(tools)
    print("maintainer_profile (all_tools=True):")
    for name in tools:
        print(f"  - {name}")
    print("activation_capability: none")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "tools":
        return cmd_tools(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
