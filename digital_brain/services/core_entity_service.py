"""
Core Entity Service (Phase 0).
Identifies "Heavy Nodes" (Core Entities) in the graph based on connection count.
Returns structured data grouped by label with weights.
"""

from typing import Any
import logging
from ..tools.mcp_client import execute_cypher

logger = logging.getLogger(__name__)

# Threshold to be considered "Core" (Heavy)
CONNECTION_THRESHOLD = 3


async def get_all_core_entities() -> dict[str, list[dict[str, Any]]]:
    """
    Fetch ALL Core Entities (Heavy Nodes) grouped by label.
    
    Returns:
        {
            "Person": [
                {"name": "Kirill", "id": "person_123", "weight": 42},
                {"name": "Sasha", "id": "person_456", "weight": 28}
            ],
            "Topic": [...],
            "Organization": [...],
            ...
        }
    """
    query = """
    MATCH (n)
    WHERE n.name IS NOT NULL
      AND NOT 'JournalEntry' IN labels(n)
      AND NOT 'Alias' IN labels(n)
      AND NOT 'LearningLog' IN labels(n)
      AND (
        any(label IN labels(n) WHERE label IN ['Person', 'Organization'])
        OR COUNT { (n)--() } >= $threshold
      )
    RETURN DISTINCT
        coalesce(n.id, 'MISSING') AS id,
        CASE WHEN n.name IS :: LIST<STRING> THEN n.name[0] ELSE n.name END AS name,
        labels(n) AS labels,
        COUNT { (n)--() } AS weight
    ORDER BY weight DESC
    LIMIT 200
    """
    
    params = {"threshold": CONNECTION_THRESHOLD}
    
    logger.info(f"🌟 CORE ENTITY LOOKUP: Starting query (threshold={CONNECTION_THRESHOLD})...")
    
    try:
        results = await execute_cypher(query, params)
        logger.info(f"🌟 CORE ENTITY LOOKUP: Query returned {len(results)} results")
        
        # Group by primary label
        grouped: dict[str, list[dict]] = {}
        
        for r in results:
            labels = r.get("labels", [])
            # Get primary label (first non-internal label)
            primary_label = labels[0] if labels else "Unknown"
            
            entity = {
                "id": r.get("id"),
                "name": r.get("name"),
                "weight": r.get("weight", 0)
            }
            
            if primary_label not in grouped:
                grouped[primary_label] = []
            grouped[primary_label].append(entity)
        
        # Log summary
        total = sum(len(v) for v in grouped.values())
        if total > 0:
            summary = ", ".join([f"{k}: {len(v)}" for k, v in grouped.items()])
            logger.info(f"🌟 CORE ENTITIES LOADED ({total}): {summary}")
        else:
            logger.info(f"🌟 CORE ENTITIES: None found with >= {CONNECTION_THRESHOLD} connections")
        
        return grouped
        
    except Exception as e:
        logger.error(f"⚠️ Core Entity Lookup FAILED: {e}")
        return {}


# Keep old function signature for backward compatibility
async def get_potential_core_entities(text: str = None) -> list[dict[str, Any]]:
    """Backward compatible wrapper - returns flat list."""
    grouped = await get_all_core_entities()
    flat = []
    for label, entities in grouped.items():
        for e in entities:
            flat.append({**e, "labels": [label], "is_core": True})
    return flat
