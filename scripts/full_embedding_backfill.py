#!/usr/bin/env python
"""Backup local Neo4j, then rebuild local embeddings when graph data exists."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_brain.tools.mcp_client import execute_cypher


ROOT = Path(__file__).resolve().parents[1]
BACKUP_ROOT = ROOT / "backups" / "neo4j"
NEO4J_IMAGE = "neo4j:5.26-community"
DATABASES = ("system", "neo4j")
MCP_SERVICES = ("mcp-cypher", "mcp-memory")


def run_command(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(command))
    return subprocess.run(
        command,
        cwd=ROOT,
        check=check,
        text=True,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def capture_command(command: list[str]) -> str:
    return subprocess.check_output(command, cwd=ROOT, text=True, stderr=subprocess.STDOUT)


def compose_project_name() -> str:
    config = json.loads(capture_command(["docker", "compose", "config", "--format", "json"]))
    name = config.get("name")
    if not name:
        raise RuntimeError("Could not determine Docker Compose project name")
    return str(name)


def neo4j_data_volume() -> str:
    return f"{compose_project_name()}_neo4j-data"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def fetch_label_counts() -> list[dict[str, Any]]:
    query = """
    MATCH (n)
    UNWIND labels(n) AS label
    RETURN label, count(*) AS count
    ORDER BY label
    """
    return await execute_cypher(query)


async def fetch_total_nodes() -> int:
    rows = await execute_cypher("MATCH (n) RETURN count(n) AS total")
    return int(rows[0]["total"]) if rows else 0


async def fetch_embedding_labels() -> list[str]:
    query = """
    MATCH (n)
    WHERE n.embedding IS NOT NULL
    UNWIND labels(n) AS label
    RETURN DISTINCT label
    ORDER BY label
    """
    rows = await execute_cypher(query)
    return [str(row["label"]) for row in rows if row.get("label")]


async def fetch_journal_embedding_coverage() -> list[dict[str, Any]]:
    query = """
    MATCH (j:JournalEntry)
    RETURN count(j) AS journal_entries,
           count(j.embedding) AS embedded_entries,
           collect(DISTINCT CASE WHEN j.embedding IS NULL THEN null ELSE size(j.embedding) END) AS embedding_sizes
    """
    return await execute_cypher(query)


def verify_ollama_model(model: str) -> None:
    output = capture_command(["docker", "compose", "exec", "-T", "ollama", "ollama", "list"])
    if model not in output:
        raise RuntimeError(f"Ollama model `{model}` is not installed. Run: docker compose exec ollama ollama pull {model}")
    print(f"Verified Ollama model: {model}")


def benchmark_model(model: str) -> None:
    run_command([sys.executable, "scripts/benchmark_embedding_models.py", "--provider", "ollama", "--model", model])


def wait_for_neo4j_health(timeout_seconds: int = 120) -> None:
    container_id = capture_command(["docker", "compose", "ps", "-q", "neo4j"]).strip()
    if not container_id:
        raise RuntimeError("Neo4j container is not running")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = capture_command(["docker", "inspect", "-f", "{{.State.Health.Status}}", container_id]).strip()
        if status == "healthy":
            print("Neo4j is healthy")
            return
        time.sleep(2)
    raise TimeoutError("Neo4j did not become healthy after restart")


def create_offline_dump() -> Path:
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUP_ROOT.parent, 0o700)
    os.chmod(BACKUP_ROOT, 0o700)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BACKUP_ROOT / f"pre-embedding-backfill-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(backup_dir, 0o700)

    volume = neo4j_data_volume()
    try:
        run_command(["docker", "compose", "stop", *MCP_SERVICES, "neo4j"])
        for database in DATABASES:
            run_command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--entrypoint=neo4j-admin",
                    "-v",
                    f"{volume}:/data:ro",
                    "-v",
                    f"{backup_dir}:/backups",
                    NEO4J_IMAGE,
                    "database",
                    "dump",
                    database,
                    "--to-path=/backups",
                    "--verbose",
                ]
            )

        checksums = []
        for database in DATABASES:
            dump_path = backup_dir / f"{database}.dump"
            if not dump_path.exists() or dump_path.stat().st_size == 0:
                raise RuntimeError(f"Neo4j dump was not created at {dump_path}")
            checksum = sha256_file(dump_path)
            checksums.append((checksum, dump_path.name))

        checksum_path = backup_dir / "SHA256SUMS"
        with checksum_path.open("w", encoding="utf-8") as handle:
            for checksum, filename in checksums:
                handle.write(f"{checksum}  {filename}\n")

        for database in DATABASES:
            run_command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--entrypoint=neo4j-admin",
                    "-v",
                    f"{backup_dir}:/backups:ro",
                    NEO4J_IMAGE,
                    "database",
                    "load",
                    database,
                    "--from-path=/backups",
                    "--info",
                ]
            )

        print(f"Backup created: {backup_dir}")
        print(f"Checksums written: {checksum_path}")
        return backup_dir
    finally:
        run_command(["docker", "compose", "up", "-d", "neo4j"], check=False)
        try:
            wait_for_neo4j_health()
        except Exception as exc:
            print(f"WARNING: Neo4j health check after backup failed: {exc}")
        run_command(["docker", "compose", "up", "-d", *MCP_SERVICES], check=False)


def run_backfill_command(label: str, *, dry_run: bool, batch_size: int) -> None:
    command = [
        sys.executable,
        "scripts/backfill_embeddings.py",
        "--label",
        label,
        "--batch-size",
        str(batch_size),
    ]
    if dry_run:
        command.append("--dry-run")
    run_command(command)


def run_regression_tests() -> None:
    run_command(
        [
            sys.executable,
            "-m",
            "unittest",
            "tests.test_mcp_client",
            "tests.test_mcp_config",
            "tests.test_local_mcp_query_tools",
            "tests.test_historical_import",
            "tests.test_journal_chain_guard",
        ]
    )


async def main_async(args: argparse.Namespace) -> None:
    total_nodes = await fetch_total_nodes()
    label_counts = await fetch_label_counts()
    print(f"Local Neo4j node count: {total_nodes}")
    print(json.dumps(label_counts, ensure_ascii=False, indent=2))
    if total_nodes == 0:
        print("Local Neo4j is empty; skipping backup, index recreation, and backfill.")
        return

    verify_ollama_model(args.model)
    benchmark_model(args.model)
    print("JournalEntry embedding coverage before backfill:")
    print(json.dumps(await fetch_journal_embedding_coverage(), ensure_ascii=False, indent=2))
    if not args.skip_backup:
        create_offline_dump()

    run_command([sys.executable, "scripts/recreate_vector_index.py", "--dimensions", str(args.dimensions)])
    run_backfill_command("JournalEntry", dry_run=True, batch_size=args.batch_size)
    run_backfill_command("JournalEntry", dry_run=False, batch_size=args.batch_size)
    run_command([sys.executable, "scripts/probe_embedding_quality.py", "--limit", str(args.probe_limit)])

    for label in await fetch_embedding_labels():
        if label == "JournalEntry":
            continue
        run_backfill_command(label, dry_run=True, batch_size=args.batch_size)
        run_backfill_command(label, dry_run=False, batch_size=args.batch_size)

    if not args.skip_tests:
        run_regression_tests()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="bge-m3", help="Ollama embedding model expected in the local runtime")
    parser.add_argument("--dimensions", type=int, default=1024, help="Embedding vector dimensions")
    parser.add_argument("--batch-size", type=int, default=10, help="Concurrent embedding writes per label")
    parser.add_argument("--probe-limit", type=int, default=5, help="Semantic probe top-k")
    parser.add_argument("--skip-backup", action="store_true", help="Do not create the offline Neo4j dump")
    parser.add_argument("--skip-tests", action="store_true", help="Do not run focused regression tests after backfill")
    return parser.parse_args()


def main() -> None:
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
