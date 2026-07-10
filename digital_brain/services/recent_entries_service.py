"""
Recent Journal Entries Service.
Loads last N journal entries with their linked entities for fallback context.
"""

from typing import Any
import logging
from ..tools.mcp_client import execute_cypher

logger = logging.getLogger(__name__)


async def get_latest_journal_entry_id() -> str | None:
    """
    Fetch the latest valid JournalEntry id for deterministic chain linking.

    Returns:
        JournalEntry.id or None when no valid id is found.
    """
    query = """
    MATCH (j:JournalEntry)
    WHERE j.id IS NOT NULL
      AND trim(toString(j.id)) <> ''
      AND trim(coalesce(toString(j.content), toString(j.raw_text), '')) <> ''
      AND trim(coalesce(toString(j.timestamp), toString(j.entry_date), toString(j.created_at), '')) <> ''
    RETURN j.id AS id
    ORDER BY j.entry_date DESC, j.timestamp DESC, j.created_at DESC
    LIMIT 1
    """
    try:
        rows = await execute_cypher(query)
        if rows and isinstance(rows, list):
            first = rows[0] or {}
            value = first.get("id")
            if value:
                return str(value)
    except Exception as e:
        logger.warning(f"⚠️ Latest JournalEntry ID lookup failed: {e}")
    return None


async def get_recent_journal_entries(limit: int = 3) -> list[dict[str, Any]]:
    """
    Fetch the most recent journal entries with their linked entities.
    
    Returns:
        [
            {
                "id": "journal_123",
                "content": "Today I talked with Sasha about...",
                "timestamp": "2024-01-15 10:30:00",
                "linked_entities": [
                    {"name": "Sasha", "label": "Person", "relation": "MENTIONS"}
                ]
            }
        ]
    """
    query = """
    MATCH (j:JournalEntry)
    WHERE trim(coalesce(toString(j.content), toString(j.raw_text), '')) <> ''
      AND trim(coalesce(toString(j.timestamp), toString(j.entry_date), toString(j.created_at), '')) <> ''
    WITH j
    ORDER BY j.entry_date DESC, j.timestamp DESC, j.created_at DESC
    LIMIT $limit
    OPTIONAL MATCH (j)-[r]->(e)
    WITH
        j,
        collect(DISTINCT CASE
            WHEN e IS NULL THEN NULL
            WHEN 'Operational' IN labels(e) THEN NULL
            WHEN 'JournalEntry' IN labels(e) THEN NULL
            WHEN 'Alias' IN labels(e) THEN NULL
            WHEN 'LearningLog' IN labels(e) THEN NULL
            ELSE {
                name: CASE
                    WHEN e.name IS :: LIST<STRING> THEN e.name[0]
                    WHEN e.name IS NOT NULL THEN e.name
                    WHEN e.type IS NOT NULL THEN e.type
                    WHEN e.description IS NOT NULL THEN left(e.description, 120)
                    ELSE NULL
                END,
                label: head(labels(e)),
                relation: type(r)
            }
        END) AS raw_linked_entities
    RETURN
        j.id AS id,
        coalesce(j.content, j.raw_text) AS content,
        coalesce(j.timestamp, toString(j.entry_date), toString(j.created_at)) AS timestamp,
        j.mood AS mood,
        [entity IN raw_linked_entities WHERE entity IS NOT NULL AND entity.name IS NOT NULL] AS linked_entities
    ORDER BY j.entry_date DESC, j.timestamp DESC, j.created_at DESC
    """
    
    params = {"limit": limit}
    
    logger.info(f"📔 RECENT ENTRIES: Loading last {limit} journal entries...")
    
    try:
        results = await execute_cypher(query, params)
        
        if results is None:
            logger.warning("📔 RECENT ENTRIES: Query returned None")
            return []
        
        entries = []
        for r in results:
            if r is None:
                continue
            
            # Defensive extraction - handle None and non-dict items
            raw_linked = r.get("linked_entities") or []
            linked = []
            for e in raw_linked:
                if e is not None and isinstance(e, dict) and e.get("name"):
                    linked.append(e)
            
            content = r.get("content") or ""
            entry = {
                "id": r.get("id"),
                "content": content[:200] if content else "",
                "timestamp": r.get("timestamp"),
                "mood": r.get("mood"),
                "linked_entities": linked
            }
            entries.append(entry)
        
        if entries:
            entity_count = sum(len(e.get("linked_entities", [])) for e in entries)
            logger.info(f"📔 RECENT ENTRIES LOADED: {len(entries)} entries, {entity_count} linked entities")
        else:
            logger.info("📔 RECENT ENTRIES: No rows returned by recent-entry query, checking total JournalEntry count...")
            count_result = await execute_cypher("MATCH (j:JournalEntry) RETURN count(j) AS total")
            total_entries = 0
            if count_result and isinstance(count_result, list) and count_result[0]:
                total_entries = count_result[0].get("total", 0) or 0

            if total_entries > 0:
                logger.warning(
                    f"📔 RECENT ENTRIES: Query returned 0 rows, but graph contains {total_entries} JournalEntry nodes"
                )
            else:
                logger.info("📔 RECENT ENTRIES: No journal entries found")
        
        return entries
        
    except Exception as e:
        logger.error(f"⚠️ Recent Entries Lookup FAILED: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return []
