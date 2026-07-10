"""Defence in depth for the server-owned JournalEntry append protocol.

The MCP server enforces this policy authoritatively.  This ADK callback keeps
legacy agents from attempting a raw journal or FOLLOWS mutation before the
network request is made.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext


_JOURNAL_CREATE_RE = re.compile(
    r"\b(?:CREATE|MERGE)\b(?:(?!\b(?:MATCH|WITH|RETURN|WHERE|SET|DELETE|DETACH|REMOVE|"
    r"UNWIND|CALL|FOREACH|FOR|INDEX|CONSTRAINT|VECTOR)\b)[\s\S])*?"
    r"\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*JournalEntry\b",
    re.IGNORECASE,
)
_FOLLOWS_MUTATION_RE = re.compile(
    r"\b(?:CREATE|MERGE)\b[^;]*?\[\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*FOLLOWS\b",
    re.IGNORECASE | re.DOTALL,
)


def _error_response(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def is_raw_journal_mutation(query: str) -> bool:
    """Return whether generic Cypher bypasses the append protocol."""
    return bool(_JOURNAL_CREATE_RE.search(query or "") or _FOLLOWS_MUTATION_RE.search(query or ""))


async def journal_chain_guard_before_tool_callback(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
) -> Optional[Dict[str, Any]]:
    del tool_context
    if tool.name != "write_neo4j_cypher":
        return None
    query = args.get("query")
    if isinstance(query, str) and is_raw_journal_mutation(query):
        return _error_response(
            "JournalEntry and FOLLOWS mutations must use append_journal_entry; "
            "generic write_neo4j_cypher is for post-append graph links only."
        )
    return None


async def journal_chain_guard_after_tool_callback(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Any,
) -> Optional[Dict[str, Any]]:
    """Compatibility no-op; chain state is owned by the MCP transaction."""
    del tool, args, tool_context, tool_response
    return None
