# File: digital_brain/callbacks/query_sanitizer.py
"""
Before-tool callback that sanitizes Cypher queries before execution.
Blocks unsafe DETACH DELETE with unresolved MISSING IDs.
Does not coach identity merges, APOC rewiring, or auto-delete patterns.
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
    2. Blocking unsafe operations when resolution fails

    Returns:
        Error tool response when blocked, or None if the query may proceed.
    """
    # Only intercept write_neo4j_cypher calls
    if tool.name != "write_neo4j_cypher":
        return None

    query = args.get("query", "")
    query_preview = query[:150].replace('\n', ' ') + "..." if len(query) > 150 else query.replace('\n', ' ')

    # Check if this is a delete operation with potential MISSING IDs
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

    # Block only — do not recommend APOC merge, relationship transfer, or
    # DETACH DELETE "fixes". Identity changes are owner-confirmed elsewhere.
    return {
        "content": [{
            "type": "text",
            "text": """⚠️ UNSAFE QUERY BLOCKED: Cannot execute destructive delete with ID="MISSING".

PROBLEM: You tried to delete an entity but the ID is unknown ("MISSING").
Automatic identity merges and deletes are not allowed.

WHAT TO DO INSTEAD:
- Prefer MATCH by a known stable id from core/existing entities.
- If two names may refer to the same entity, report a duplicate candidate
  (evidence only) in structured output — do not delete or merge nodes.
- Do not invent IDs or attempt identity "fixes" via graph rewiring.
  Generic destructive delete is rejected by the write path.

Please continue without mutating identity for unresolved entities."""
        }],
        "isError": True
    }
