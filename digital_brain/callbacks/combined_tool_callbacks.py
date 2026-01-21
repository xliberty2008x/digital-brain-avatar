# File: digital_brain/callbacks/combined_tool_callbacks.py
"""
Combined after_tool callback that chains multiple callbacks together.
"""
import time
from google.adk.tools.tool_context import ToolContext
from google.adk.tools.base_tool import BaseTool
from typing import Optional, Dict, Any
from copy import deepcopy


def combined_after_tool_callback(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Dict
) -> Optional[Dict]:
    """
    Combined callback that:
    1. Strips embeddings from responses
    2. Rate limits tool calls
    
    Returns:
        Modified tool_response or None if unchanged.
    """
    modified_response = tool_response
    response_was_modified = False
    
    # === 1. Strip Embeddings ===
    import re

    # === 1. Strip Embeddings ===
    def remove_embeddings(obj: Any) -> Any:
        """Recursively remove 'embedding' keys and strip embedding arrays from strings."""
        if isinstance(obj, dict):
            return {k: remove_embeddings(v) for k, v in obj.items() 
                    if k.lower() != 'embedding'}
        elif isinstance(obj, list):
            return [remove_embeddings(item) for item in obj]
        elif isinstance(obj, str):
            # Regex to find 'embedding': [ ... ] pattern in strings and replace it
            pattern = r"(?:\\?['\"])embedding(?:\\?['\"])\s*:\s*\[.*?\]"
            if "embedding" in obj:
                 obj = re.sub(pattern, r'"embedding": [<stripped_vector>]', obj, flags=re.DOTALL | re.IGNORECASE)
            return obj
        else:
            return obj

    # === 2. Normalize Unicode (Fix for \u0430 bloat) ===
    def normalize_unicode(obj: Any) -> Any:
        """Recursively decode unicode escape sequences securely."""
        if isinstance(obj, dict):
            return {k: normalize_unicode(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [normalize_unicode(item) for item in obj]
        elif isinstance(obj, str):
            if "\\u" in obj:
                try:
                    # 1. Unescape only the \uXXXX sequences using a regex to avoid mangling existing UTF-8
                    def repl(m):
                        return m.group(0).encode('utf-8').decode('unicode_escape')
                    
                    res = re.sub(r'(\\u[0-9a-fA-F]{4})+', repl, obj)
                    
                    # 2. Heal any surrogates (like emoji pairs) and ensure valid UTF-8
                    return res.encode('utf-16', 'surrogatepass').decode('utf-16')
                except Exception:
                    return obj
            return obj
        else:
            return obj
    
    response_str = str(tool_response)
    
    # Apply Embedding Strip
    if 'embedding' in response_str.lower():
        modified_response = remove_embeddings(deepcopy(modified_response))
        response_was_modified = True
        print(f"[EmbeddingFilter] Stripped embeddings from '{tool.name}' response")

    # Apply Unicode Normalization
    if '\\u' in response_str:
        modified_response = normalize_unicode(deepcopy(modified_response))
        response_was_modified = True
        print(f"[UnicodeNormalizer] Decoded escaped characters in '{tool.name}' response")
    
    # === 3. Rate Limiting ===
    CALLS_BEFORE_DELAY = 5
    DELAY_SECONDS = 1.0
    
    state = tool_context.state
    call_count = state.get("tool_call_count", 0) + 1
    state["tool_call_count"] = call_count
    
    agent_name = tool_context.agent_name
    print(f"[RateLimiter] Agent '{agent_name}' - Tool '{tool.name}' completed. Total calls: {call_count}")
    
    if call_count % CALLS_BEFORE_DELAY == 0:
        print(f"⏳ [RateLimiter] Sleeping {DELAY_SECONDS}s after {call_count} calls...")
        time.sleep(DELAY_SECONDS)
    
    # Return modified response or None
    return modified_response if response_was_modified else None
