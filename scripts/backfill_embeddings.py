#!/usr/bin/env python
"""Recompute local embeddings for searchable graph records through MCP."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_brain.tools.mcp_client import call_mcp_tool, execute_cypher


TEXT_FIELDS = ("content", "raw_text", "summary", "description", "body", "title", "name")


def _text_expression() -> str:
    return "coalesce(" + ", ".join(f"toString(n.{field})" for field in TEXT_FIELDS) + ", '')"


async def _scan_records(label: str, limit: int | None) -> list[dict[str, Any]]:
    query = f"""
    MATCH (n:{label})
    WITH n, {_text_expression()} AS embed_text
    WHERE trim(embed_text) <> ''
    RETURN elementId(n) AS element_id,
           coalesce(n.id, '') AS id,
           labels(n) AS labels,
           embed_text,
           CASE WHEN n.embedding IS NULL THEN 0 ELSE size(n.embedding) END AS embedding_size
    ORDER BY coalesce(n.timestamp, n.entry_date, n.created_at, n.id, elementId(n))
    {f"LIMIT {int(limit)}" if limit else ""}
    """
    return await execute_cypher(query)


async def _update_record(element_id: str, embed_text: str) -> None:
    query = """
    MATCH (n)
    WHERE elementId(n) = $element_id
    SET n.embedding = $embedding
    RETURN elementId(n) AS element_id, size(n.embedding) AS embedding_size
    """
    await call_mcp_tool(
        "write_neo4j_cypher",
        {
            "query": query,
            "params": {"element_id": element_id},
            "embed_text": embed_text,
        },
    )


async def run_backfill(args: argparse.Namespace) -> dict[str, int]:
    records = await _scan_records(args.label, args.limit)
    counters = {"scanned": len(records), "updated": 0, "skipped": 0, "failed": 0}
    if args.dry_run:
        for record in records[: args.preview]:
            print(
                f"DRY-RUN {record.get('labels')} id={record.get('id') or record.get('element_id')} "
                f"embedding_size={record.get('embedding_size')} text={record.get('embed_text', '')[:80]!r}"
            )
        counters["skipped"] = len(records)
        return counters

    semaphore = asyncio.Semaphore(args.batch_size)

    async def update(record: dict[str, Any]) -> None:
        async with semaphore:
            try:
                await _update_record(record["element_id"], record["embed_text"])
                counters["updated"] += 1
            except Exception as exc:
                counters["failed"] += 1
                print(f"FAILED element_id={record.get('element_id')}: {exc}")

    await asyncio.gather(*(update(record) for record in records))
    return counters


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="JournalEntry", help="Neo4j label to backfill")
    parser.add_argument("--limit", type=int, default=None, help="Maximum records to scan")
    parser.add_argument("--batch-size", type=int, default=10, help="Concurrent MCP updates")
    parser.add_argument("--dry-run", action="store_true", help="Scan without writing embeddings")
    parser.add_argument("--preview", type=int, default=10, help="Rows to print during dry-run")
    return parser.parse_args()


def main() -> None:
    counters = asyncio.run(run_backfill(parse_args()))
    print(
        "Backfill complete: "
        f"scanned={counters['scanned']} updated={counters['updated']} "
        f"skipped={counters['skipped']} failed={counters['failed']}"
    )
    if counters["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
