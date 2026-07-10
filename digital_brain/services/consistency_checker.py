"""
Report-only duplicate candidate discovery.

Detects possible identity collisions and returns evidence/proposal inputs.
Never merges nodes, deletes nodes, or creates Alias records.
"""

from __future__ import annotations

from typing import Any

from ..tools.mcp_client import execute_cypher

# Re-exported for tests that patch mutation paths; discovery must not call it.
from ..tools.mcp_client import call_mcp_tool  # noqa: F401


async def _find_duplicate_persons() -> list[dict[str, Any]]:
    """Find Person nodes that might be duplicates based on similar names."""
    query = """
    MATCH (a:Person), (b:Person)
    WHERE a.id < b.id
      AND (
        toLower(a.name) = toLower(b.name)
        OR toLower(a.name) CONTAINS toLower(b.name)
        OR toLower(b.name) CONTAINS toLower(a.name)
      )
    RETURN
      a.id AS id_a, a.name AS name_a,
      b.id AS id_b, b.name AS name_b,
      1.0 AS similarity_score,
      'name_similarity' AS evidence_kind
    LIMIT 10
    """
    return await execute_cypher(query)


async def _find_duplicates_by_topology() -> list[dict[str, Any]]:
    """Find potential duplicates based on shared JournalEntry connections."""
    # Prefer APOC similarity when available; fall back to name containment only.
    apoc_query = """
    MATCH (a:Person)<-[:MENTIONS]-(j:JournalEntry)-[:MENTIONS]->(b:Person)
    WHERE a.id < b.id
      AND (
        toLower(a.name) CONTAINS toLower(b.name)
        OR toLower(b.name) CONTAINS toLower(a.name)
        OR apoc.text.levenshteinSimilarity(a.name, b.name) > 0.8
      )
    RETURN DISTINCT
      a.id AS id_a, a.name AS name_a,
      b.id AS id_b, b.name AS name_b,
      count(j) AS shared_entries,
      'shared_journal_topology' AS evidence_kind
    ORDER BY shared_entries DESC
    LIMIT 10
    """
    try:
        return await execute_cypher(apoc_query)
    except Exception:
        fallback = """
        MATCH (a:Person)<-[:MENTIONS]-(j:JournalEntry)-[:MENTIONS]->(b:Person)
        WHERE a.id < b.id
          AND (
            toLower(a.name) CONTAINS toLower(b.name)
            OR toLower(b.name) CONTAINS toLower(a.name)
          )
        RETURN DISTINCT
          a.id AS id_a, a.name AS name_a,
          b.id AS id_b, b.name AS name_b,
          count(j) AS shared_entries,
          'shared_journal_topology' AS evidence_kind
        ORDER BY shared_entries DESC
        LIMIT 10
        """
        try:
            return await execute_cypher(fallback)
        except Exception as e:
            print(f"Topology duplicate scan failed: {e}")
            return []


def _candidate_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("id_a", "")), str(row.get("id_b", "")))


async def find_duplicate_candidates() -> list[dict[str, Any]]:
    """
    Read-only discovery of possible duplicate entities.

    Returns evidence/proposal inputs only. Does not merge, delete, or
    create Alias nodes. Callers must not treat results as mutation commands.
    """
    by_name = await _find_duplicate_persons()
    by_topo = await _find_duplicates_by_topology()

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in by_name + by_topo:
        key = _candidate_key(row)
        if not key[0] or not key[1]:
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(row)
        else:
            # Prefer richer evidence when topology adds shared_entries
            if "shared_entries" in row and "shared_entries" not in existing:
                existing["shared_entries"] = row["shared_entries"]
            kinds = {
                existing.get("evidence_kind"),
                row.get("evidence_kind"),
            }
            kinds.discard(None)
            if len(kinds) > 1:
                existing["evidence_kind"] = "name_similarity+shared_journal_topology"
            elif kinds:
                existing["evidence_kind"] = next(iter(kinds))

    candidates = list(merged.values())
    if not candidates:
        print("✅ No duplicate candidates found")
    else:
        print(f"📋 Found {len(candidates)} duplicate candidate pair(s) (report-only)")
    return candidates
