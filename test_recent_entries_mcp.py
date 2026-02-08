#!/usr/bin/env python3
"""Validate recent JournalEntry fetching against live Neo4j via MCP."""

import asyncio
import os
import sys
from typing import Any
from collections import Counter


sys.path.insert(0, os.getcwd())

from digital_brain.services.recent_entries_service import get_recent_journal_entries
from digital_brain.tools.mcp_client import execute_cypher


BASELINE_RECENT_QUERY = """
MATCH (j:JournalEntry)
WHERE trim(coalesce(toString(j.content), toString(j.raw_text), '')) <> ''
  AND trim(coalesce(toString(j.timestamp), toString(j.entry_date), toString(j.created_at), '')) <> ''
WITH j
ORDER BY j.entry_date DESC, j.timestamp DESC, j.created_at DESC
LIMIT $limit
RETURN
    j.id AS id,
    coalesce(j.content, j.raw_text) AS content,
    coalesce(j.timestamp, toString(j.entry_date), toString(j.created_at)) AS timestamp,
    j.mood AS mood
ORDER BY j.entry_date DESC, j.timestamp DESC, j.created_at DESC
"""


LEGACY_QUERY = """
MATCH (j:JournalEntry)
WITH j ORDER BY j.timestamp DESC LIMIT $limit
OPTIONAL MATCH (j)-[r]->(e)
WHERE e IS NOT NULL
  AND NOT 'JournalEntry' IN labels(e)
  AND NOT 'Alias' IN labels(e)
WITH j,
     CASE WHEN e IS NOT NULL THEN collect(DISTINCT {
        name: CASE WHEN e.name IS :: LIST<STRING> THEN e.name[0] ELSE e.name END,
        label: head(labels(e)),
        relation: type(r)
     }) ELSE [] END AS linked_entities
RETURN
    j.id AS id,
    j.content AS content,
    j.timestamp AS timestamp,
    j.mood AS mood,
    linked_entities
ORDER BY j.timestamp DESC
"""


COUNT_QUERY = "MATCH (j:JournalEntry) RETURN count(j) AS total"
QUALIFIED_COUNT_QUERY = """
MATCH (j:JournalEntry)
WHERE trim(coalesce(toString(j.content), toString(j.raw_text), '')) <> ''
  AND trim(coalesce(toString(j.timestamp), toString(j.entry_date), toString(j.created_at), '')) <> ''
RETURN count(j) AS total
"""


def entry_key(entry: dict[str, Any]) -> tuple[str, str, str]:
    content = entry.get("content") or ""
    return (
        str(entry.get("id") or ""),
        str(entry.get("timestamp") or ""),
        content[:80],
    )


def print_entries(title: str, rows: list[dict[str, Any]]) -> None:
    print(f"\n{title}: {len(rows)}")
    for i, row in enumerate(rows, 1):
        key = entry_key(row)
        mood = row.get("mood")
        print(f"  {i}. id={key[0] or 'None'} ts={key[1] or 'None'} mood={mood!r} content='{key[2]}'")


def print_relation_details(rows: list[dict[str, Any]]) -> None:
    relation_counter: Counter[str] = Counter()
    label_counter: Counter[str] = Counter()

    print("\nService relation details:")
    if not rows:
        print("  (no rows)")
        return

    for i, row in enumerate(rows, 1):
        linked = row.get("linked_entities") or []
        compact = []
        for rel in linked:
            name = rel.get("name")
            label = rel.get("label")
            relation = rel.get("relation")
            compact.append(f"{relation}:{label}:{name}")
            if relation:
                relation_counter[relation] += 1
            if label:
                label_counter[label] += 1
        if compact:
            print(f"  {i}. " + "; ".join(compact))
        else:
            print(f"  {i}. (no linked entities)")

    print(f"\nRelation type counts: {dict(relation_counter)}")
    print(f"Linked label counts: {dict(label_counter)}")


def print_data_warnings(rows: list[dict[str, Any]]) -> None:
    ids = [str(r.get("id") or "") for r in rows]
    duplicate_ids = [item for item, count in Counter(ids).items() if item and count > 1]
    empty_content_count = sum(1 for r in rows if not (r.get("content") or "").strip())
    empty_timestamp_count = sum(1 for r in rows if not (r.get("timestamp") or "").strip())

    if duplicate_ids:
        print(f"[WARN] Duplicate ids in fetched rows: {duplicate_ids}")
    if empty_content_count:
        print(f"[WARN] Rows with empty content: {empty_content_count}/{len(rows)}")
    if empty_timestamp_count:
        print(f"[WARN] Rows with empty timestamp: {empty_timestamp_count}/{len(rows)}")


async def main() -> int:
    limit = int(os.getenv("RECENT_TEST_LIMIT", "3"))
    print(f"=== MCP Recent Entries Validation (limit={limit}) ===")

    total_result = await execute_cypher(COUNT_QUERY)
    total = int((total_result[0].get("total") if total_result else 0) or 0)
    qualified_result = await execute_cypher(QUALIFIED_COUNT_QUERY)
    qualified_total = int((qualified_result[0].get("total") if qualified_result else 0) or 0)
    expected_count = min(qualified_total, limit)
    print(f"Total JournalEntry nodes in graph: {total}")
    print(f"JournalEntry nodes qualifying for fetch: {qualified_total}")

    baseline = await execute_cypher(BASELINE_RECENT_QUERY, {"limit": limit})
    service_rows = await get_recent_journal_entries(limit=limit)
    legacy_rows = await execute_cypher(LEGACY_QUERY, {"limit": limit})

    print_entries("Baseline query rows", baseline)
    print_entries("Service rows", service_rows)
    print_entries("Legacy query rows (for comparison)", legacy_rows)
    print_relation_details(service_rows)
    print_data_warnings(service_rows)

    ok = True

    if len(baseline) != expected_count:
        ok = False
        print(
            f"\n[FAIL] Baseline returned {len(baseline)} rows, expected {expected_count} "
            f"(based on total count and limit)."
        )

    if len(service_rows) != len(baseline):
        ok = False
        print(f"[FAIL] Service returned {len(service_rows)} rows, baseline returned {len(baseline)}.")

    baseline_keys = [entry_key(x) for x in baseline]
    service_keys = [entry_key(x) for x in service_rows]
    if baseline_keys != service_keys:
        ok = False
        print("[FAIL] Service rows do not match baseline order/content keys.")
        print(f"  baseline keys: {baseline_keys}")
        print(f"  service keys:  {service_keys}")

    bad_linked = []
    for idx, row in enumerate(service_rows):
        linked = row.get("linked_entities") or []
        for rel in linked:
            if not isinstance(rel, dict) or not rel.get("name"):
                bad_linked.append((idx, rel))
    if bad_linked:
        ok = False
        print(f"[FAIL] Service returned malformed linked_entities: {bad_linked}")

    if total > 0 and len(service_rows) == 0:
        ok = False
        print("[FAIL] Graph has JournalEntry nodes but service returned zero rows.")

    if ok:
        print("\n[PASS] Recent entries fetch works and matches baseline query.")
        if len(legacy_rows) < len(service_rows):
            print(
                "[INFO] Legacy query returned fewer rows than service; "
                "this confirms the old OPTIONAL MATCH filter was dropping entries."
            )
        return 0

    print("\n[FAIL] Recent entries validation failed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
