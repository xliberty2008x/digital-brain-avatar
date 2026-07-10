#!/usr/bin/env python3
"""Report JournalEntry integrity without reading or emitting journal content.

The report is intentionally non-destructive. Its candidate `element_id`
values are the production inputs to ``bootstrap_journal_chain.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_brain.tools.mcp_client import execute_cypher


async def audit_journal_integrity() -> dict[str, Any]:
    summary = await execute_cypher(
        """
        MATCH (j:JournalEntry)
        RETURN
          count(j) AS total_entries,
          sum(CASE
            WHEN j.id IS NULL THEN 1
            WHEN j.id IS :: STRING AND trim(j.id) = '' THEN 1
            ELSE 0
          END) AS missing_id_entries,
          sum(CASE
            WHEN j.id IS NOT NULL AND NOT j.id IS :: STRING THEN 1
            ELSE 0
          END) AS non_string_id_entries
        """
    )
    duplicate_ids = await execute_cypher(
        """
        MATCH (j:JournalEntry)
        WHERE j.id IS :: STRING AND trim(j.id) <> ''
        WITH j.id AS id, collect(elementId(j)) AS element_ids, count(*) AS count
        WHERE count > 1
        RETURN id, count, element_ids
        ORDER BY count DESC, id
        """
    )
    follows_forks = await execute_cypher(
        """
        MATCH (child:JournalEntry)-[:FOLLOWS]->(parent:JournalEntry)
        WITH parent, collect({element_id: elementId(child), id: child.id}) AS children
        WHERE size(children) > 1
        RETURN elementId(parent) AS parent_element_id,
               parent.id AS parent_id,
               size(children) AS child_count,
               children
        ORDER BY child_count DESC, parent_element_id
        """
    )
    candidates = await execute_cypher(
        """
        MATCH (j:JournalEntry)
        WHERE NOT EXISTS { MATCH (:JournalEntry)-[:FOLLOWS]->(j) }
        RETURN elementId(j) AS element_id,
               j.id AS id,
               coalesce(j.timestamp, j.entry_date, j.created_at) AS timestamp
        ORDER BY timestamp DESC, element_id
        """
    )

    return {
        "summary": summary[0] if summary else {},
        "duplicate_id_groups": duplicate_ids,
        "follows_forks": follows_forks,
        "bootstrap_candidates": candidates,
        "note": (
            "This report never includes JournalEntry content. Select one "
            "bootstrap candidate manually; do not infer a canonical branch from order alone."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON output file. Defaults to stdout.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(audit_journal_integrity())
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(args.output)
    else:
        print(rendered)


if __name__ == "__main__":
    main()
