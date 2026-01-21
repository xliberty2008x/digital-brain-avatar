# File: digital_brain/callbacks/query_sanitizer.py
"""
Before-tool callback that sanitizes Cypher queries before execution.
Handles MISSING IDs and prevents unsafe DELETE operations.
"""
import re
import structlog
from google.adk.tools.tool_context import ToolContext
from google.adk.tools.base_tool import BaseTool
from typing import Optional, Dict, Any

logger = structlog.get_logger(__name__)


async def query_sanitizer_callback(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext
) -> Optional[Dict[str, Any]]:
    """
    Before-tool callback for write_neo4j_cypher.
    
    Sanitizes queries by:
    1. Detecting "MISSING" IDs in MATCH/DETACH DELETE patterns
    2. Transforming ID-based lookups to name-based fallbacks
    3. Blocking unsafe operations when resolution fails
    
    Returns:
        Modified args dict with sanitized query, or None if unchanged.
    """
    # Only intercept write_neo4j_cypher calls
    if tool.name != "write_neo4j_cypher":
        return None
    
    query = args.get("query", "")
    query_preview = query[:150].replace('\n', ' ') + "..." if len(query) > 150 else query.replace('\n', ' ')
    
    # Check if this is a merge/delete operation with potential MISSING IDs
    if "DETACH DELETE" not in query.upper():
        logger.debug(f"[QuerySanitizer] SKIP (no DETACH DELETE): {query_preview}")
        return None
    
    logger.info(f"[QuerySanitizer] ⚠️ DETACH DELETE detected: {query_preview}")
    
    # Pattern: MATCH (x {id: "MISSING"}) or MATCH (x) WHERE x.id = "MISSING"
    missing_id_patterns = [
        r'\{id:\s*["\']MISSING["\']\}',  # {id: "MISSING"}
        r'\.id\s*=\s*["\']MISSING["\']',  # .id = "MISSING"
    ]
    
    has_missing_id = any(re.search(p, query, re.IGNORECASE) for p in missing_id_patterns)
    
    if not has_missing_id:
        logger.info(f"[QuerySanitizer] ✅ PASS (no MISSING ID found, query is safe)")
        return None
    
    logger.warning(f"[QuerySanitizer] 🔴 MISSING ID detected! Returning guidance to agent...")
    
    # Return an instructive error as the tool response
    # The agent will see this and can adjust its approach
    return {
        "content": [{
            "type": "text",
            "text": f"""⚠️ UNSAFE QUERY BLOCKED: Cannot execute DETACH DELETE with ID="MISSING".

PROBLEM: You tried to delete an entity but the ID is unknown ("MISSING").

HOW TO FIX - Use name-based lookup:
```cypher
// Instead of: MATCH (remove {{id: "MISSING"}}) DETACH DELETE remove
// Use this pattern:
MATCH (keep {{id: $keep_id}})
MATCH (remove) WHERE remove.name = $remove_name AND id(keep) <> id(remove)
// Transfer relationships first, then delete
CALL {{
  WITH keep, remove
  MATCH (remove)-[r]->()
  CALL apoc.create.relationship(keep, type(r), properties(r), endNode(r)) YIELD rel
  RETURN count(*) AS out
}}
CALL {{
  WITH keep, remove
  MATCH ()-[r]->(remove)
  CALL apoc.create.relationship(startNode(r), type(r), properties(r), keep) YIELD rel
  RETURN count(*) AS in
}}
DETACH DELETE remove
```

Please retry with the corrected query pattern."""
        }],
        "isError": True
    }


def _transform_missing_id_query(query: str) -> str:
    """
    Transform a query with {id: "MISSING"} to use name-based lookup.
    
    Transforms:
        MATCH (keep {id: $keep_id}), (remove {id: $remove_id})
    To:
        MATCH (keep {id: $keep_id})
        MATCH (remove) WHERE remove.name = $remove_name AND NOT 'JournalEntry' IN labels(remove)
        WHERE id(keep) <> id(remove)
    """
    # Pattern for merge operations: MATCH (keep {id: $keep_id}), (remove {id: $remove_id})
    merge_pattern = r'MATCH\s*\(keep\s*\{id:\s*\$keep_id\}\)\s*,\s*\(remove\s*\{id:\s*\$remove_id\}\)'
    
    replacement = """MATCH (keep {id: $keep_id})
OPTIONAL MATCH (remove) 
WHERE remove.name = $remove_name 
  AND id(keep) <> id(remove) 
  AND NOT 'JournalEntry' IN labels(remove)
WITH keep, remove
WHERE remove IS NOT NULL"""
    
    transformed = re.sub(merge_pattern, replacement, query, flags=re.IGNORECASE)
    
    # Also handle: MATCH (remove {id: "MISSING"})
    missing_match = r'MATCH\s*\(remove\s*\{id:\s*["\']MISSING["\']\}\)'
    missing_replacement = """MATCH (remove) 
WHERE remove.name = $remove_name 
  AND NOT 'JournalEntry' IN labels(remove)"""
    
    transformed = re.sub(missing_match, missing_replacement, transformed, flags=re.IGNORECASE)
    
    return transformed
