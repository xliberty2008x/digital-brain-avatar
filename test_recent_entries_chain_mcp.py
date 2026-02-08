#!/usr/bin/env python3
"""Compare current recent-entry fetch vs chain-based fetch over JournalEntry links."""

import asyncio
import os
import sys
from typing import Any
from collections import Counter

sys.path.insert(0, os.getcwd())

from digital_brain.services.recent_entries_service import get_recent_journal_entries
from digital_brain.tools.mcp_client import execute_cypher


VALID_ENTRY_WHERE = """
trim(coalesce(toString(j.content), toString(j.raw_text), '')) <> ''
AND trim(coalesce(toString(j.timestamp), toString(j.entry_date), toString(j.created_at), '')) <> ''
"""


def _short(text: str | None, n: int = 90) -> str:
    return (text or "").replace("\n", " ")[:n]


def _print_rows(title: str, rows: list[dict[str, Any]]) -> None:
    print(f"\n{title}: {len(rows)}")
    for i, r in enumerate(rows, 1):
        print(
            f"  {i}. hop={r.get('hop')} id={r.get('id')} ts={r.get('timestamp')} "
            f"mood={r.get('mood')!r} content='{_short(r.get('content'))}'"
        )


def _print_linked(rows: list[dict[str, Any]], title: str) -> None:
    rel_counter: Counter[str] = Counter()
    label_counter: Counter[str] = Counter()
    print(f"\n{title}:")
    for i, r in enumerate(rows, 1):
        linked = r.get("linked_entities") or []
        if not linked:
            print(f"  {i}. (no linked entities)")
            continue
        compact = []
        for e in linked:
            rel = e.get("relation")
            lab = e.get("label")
            name = e.get("name")
            compact.append(f"{rel}:{lab}:{name}")
            if rel:
                rel_counter[rel] += 1
            if lab:
                label_counter[lab] += 1
        print(f"  {i}. " + "; ".join(compact))
    print(f"  relation counts: {dict(rel_counter)}")
    print(f"  label counts: {dict(label_counter)}")


async def get_entry_to_entry_rel_types() -> list[dict[str, Any]]:
    q = """
    MATCH (:JournalEntry)-[r]->(:JournalEntry)
    RETURN type(r) AS rel_type, count(*) AS cnt
    ORDER BY cnt DESC
    """
    rows = await execute_cypher(q)
    return rows or []


def choose_strategy(rel_types: list[dict[str, Any]]) -> tuple[str, str] | None:
    available = {str(r.get("rel_type")) for r in rel_types}
    if "FOLLOWS" in available:
        # Newer -> Older
        return ("FOLLOWS", "outgoing")
    if "NEXT_ENTRY" in available:
        # Usually Older -> Newer; to go backward from newest we traverse incoming.
        return ("NEXT_ENTRY", "incoming")
    if rel_types:
        # Fallback heuristic: use the most frequent as newer -> older.
        return (str(rel_types[0].get("rel_type")), "outgoing")
    return None


def build_chain_query(rel_type: str, direction: str) -> str:
    safe_rel = rel_type.replace("`", "")
    if direction == "outgoing":
        path_pattern = f"(head)-[:{safe_rel}*0..25]->(j:JournalEntry)"
    else:
        path_pattern = f"(head)<-[:{safe_rel}*0..25]-(j:JournalEntry)"

    return f"""
    MATCH (head:JournalEntry)
    WHERE trim(coalesce(toString(head.content), toString(head.raw_text), '')) <> ''
      AND trim(coalesce(toString(head.timestamp), toString(head.entry_date), toString(head.created_at), '')) <> ''
    WITH head
    ORDER BY coalesce(toString(head.entry_date), toString(head.timestamp), toString(head.created_at)) DESC
    LIMIT 1

    MATCH p={path_pattern}
    WHERE {VALID_ENTRY_WHERE}
    WITH j, min(length(p)) AS hop
    ORDER BY hop ASC
    LIMIT $limit

    OPTIONAL MATCH (j)-[r]->(e)
    WITH
      j,
      hop,
      collect(DISTINCT CASE
        WHEN e IS NULL THEN NULL
        WHEN 'JournalEntry' IN labels(e) THEN NULL
        WHEN 'Alias' IN labels(e) THEN NULL
        ELSE {{
          name: CASE
            WHEN e.name IS :: LIST<STRING> THEN e.name[0]
            WHEN e.name IS NOT NULL THEN e.name
            WHEN e.type IS NOT NULL THEN e.type
            WHEN e.description IS NOT NULL THEN left(e.description, 120)
            ELSE NULL
          END,
          label: head(labels(e)),
          relation: type(r)
        }}
      END) AS raw_linked
    RETURN
      hop,
      j.id AS id,
      coalesce(j.content, j.raw_text) AS content,
      coalesce(j.timestamp, toString(j.entry_date), toString(j.created_at)) AS timestamp,
      j.mood AS mood,
      [x IN raw_linked WHERE x IS NOT NULL AND x.name IS NOT NULL] AS linked_entities
    ORDER BY hop ASC
    """


async def get_chain_entries(limit: int = 3) -> dict[str, Any]:
    rel_types = await get_entry_to_entry_rel_types()
    strategy = choose_strategy(rel_types)
    if not strategy:
        return {"strategy": None, "entries": [], "rel_types": rel_types}

    rel_type, direction = strategy
    query = build_chain_query(rel_type, direction)
    rows = await execute_cypher(query, {"limit": limit})
    chosen_direction = direction

    # Fallback: if no rows in first direction, try opposite direction for same relation type.
    if not rows:
        fallback_direction = "incoming" if direction == "outgoing" else "outgoing"
        fallback_query = build_chain_query(rel_type, fallback_direction)
        fallback_rows = await execute_cypher(fallback_query, {"limit": limit})
        if fallback_rows:
            rows = fallback_rows
            chosen_direction = fallback_direction

    return {
        "strategy": {"rel_type": rel_type, "direction": chosen_direction},
        "entries": rows or [],
        "rel_types": rel_types,
    }


async def main() -> int:
    limit = int(os.getenv("RECENT_TEST_LIMIT", "3"))
    print(f"=== Compare Current Fetch vs Chain-Based Fetch (limit={limit}) ===")

    total_q = "MATCH (j:JournalEntry) RETURN count(j) AS total"
    total_rows = await execute_cypher(total_q)
    total = int((total_rows[0].get("total") if total_rows else 0) or 0)
    print(f"Total JournalEntry nodes: {total}")

    current_rows = await get_recent_journal_entries(limit=limit)
    chain_result = await get_chain_entries(limit=limit)
    chain_rows = chain_result.get("entries", [])
    strategy = chain_result.get("strategy")
    rel_types = chain_result.get("rel_types", [])

    print(f"Entry->Entry relation types: {rel_types}")
    print(f"Chosen chain strategy: {strategy}")

    # Normalize for comparable print layout
    current_norm = [{"hop": i, **r} for i, r in enumerate(current_rows)]
    _print_rows("Current service fetch", current_norm)
    _print_rows("Chain-based fetch", chain_rows)
    _print_linked(current_norm, "Current service linked entities")
    _print_linked(chain_rows, "Chain-based linked entities")

    current_keys = [(r.get("id"), r.get("timestamp"), _short(r.get("content"), 40)) for r in current_rows]
    chain_keys = [(r.get("id"), r.get("timestamp"), _short(r.get("content"), 40)) for r in chain_rows]

    print("\nComparison summary:")
    print(f"  current keys: {current_keys}")
    print(f"  chain keys:   {chain_keys}")

    if not strategy:
        print("[WARN] No JournalEntry->JournalEntry relationship found; chain logic cannot be evaluated.")
        return 2

    if not chain_rows:
        print("[WARN] Chain strategy selected but returned no rows.")
        return 3

    if current_keys == chain_keys:
        print("[INFO] Both strategies currently return the same top entries.")
    else:
        print("[INFO] Strategies return different entry sets/order.")

    print("[PASS] Comparison run completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
