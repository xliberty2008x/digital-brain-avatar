# File: digital_brain/callbacks/rate_limiter.py
"""
Rate limiting callback for tool calls to prevent Gemini API rate limit errors.
"""
import time
from google.adk.tools.tool_context import ToolContext
from google.adk.tools.base_tool import BaseTool
from typing import Optional, Dict, Any


def rate_limit_after_tool(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Dict
) -> Optional[Dict]:
    """
    Rate limiting callback: adds delay after every N tool calls.
    Attach this to agents that make many rapid tool calls (e.g., context_retriever, executor).
    
    Args:
        tool: The tool that was executed
        args: Arguments passed to the tool
        tool_context: Context containing state and agent info
        tool_response: The result from the tool
    
    Returns:
        None to keep original tool_response unchanged
    """
    CALLS_BEFORE_DELAY = 5  # Add delay after every 5 calls
    DELAY_SECONDS = 1.0     # Wait 1 second
    
    state = tool_context.state
    
    # Initialize or increment counter
    call_count = state.get("tool_call_count", 0) + 1
    state["tool_call_count"] = call_count
    
    agent_name = tool_context.agent_name
    print(f"[RateLimiter] Agent '{agent_name}' - Tool '{tool.name}' completed. Total calls: {call_count}")
    
    if call_count % CALLS_BEFORE_DELAY == 0:
        print(f"⏳ [RateLimiter] Sleeping {DELAY_SECONDS}s after {call_count} calls...")
        time.sleep(DELAY_SECONDS)
    
    return None  # Return None to keep original tool_response
