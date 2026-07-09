"""Combine before-tool callbacks for the executor write path."""

from __future__ import annotations

from typing import Any, Dict, Optional

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from .journal_chain_guard import journal_chain_guard_before_tool_callback
from .query_sanitizer import query_sanitizer_callback


async def combined_before_tool_callback(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
) -> Optional[Dict[str, Any]]:
    """
    Run MISSING-id DETACH DELETE sanitizer, then JournalEntry chain guard.

    Either callback may return an error tool response (isError=True) to block
    the underlying write. Returning None means the write may proceed.
    """
    sanitizer_result = await query_sanitizer_callback(tool, args, tool_context)
    if sanitizer_result is not None:
        return sanitizer_result
    return await journal_chain_guard_before_tool_callback(tool, args, tool_context)
