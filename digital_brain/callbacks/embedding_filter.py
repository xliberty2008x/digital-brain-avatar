# File: digital_brain/callbacks/embedding_filter.py
"""
Callback to strip embedding vectors from tool responses.
Prevents context overflow when Neo4j returns full node objects with embeddings.
"""
from google.adk.tools.tool_context import ToolContext
from google.adk.tools.base_tool import BaseTool
from typing import Optional, Dict, Any
from copy import deepcopy


def strip_embeddings_after_tool(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Dict
) -> Optional[Dict]:
    """
    Strips 'embedding' fields from tool responses to prevent context overflow.
    
    Args:
        tool: The tool that was executed
        args: Arguments passed to the tool
        tool_context: Context containing state and agent info
        tool_response: The result from the tool
    
    Returns:
        Modified tool_response without embedding fields, or None if no changes.
    """
    
    def remove_embeddings(obj: Any) -> Any:
        """Recursively remove 'embedding' keys from nested structures."""
        if isinstance(obj, dict):
            return {k: remove_embeddings(v) for k, v in obj.items() 
                    if k.lower() != 'embedding'}
        elif isinstance(obj, list):
            return [remove_embeddings(item) for item in obj]
        else:
            return obj
    
    # Check if response contains any embedding data
    response_str = str(tool_response)
    if 'embedding' not in response_str.lower():
        return None  # No embeddings, return original
    
    # Strip embeddings from response
    cleaned_response = remove_embeddings(deepcopy(tool_response))
    
    agent_name = tool_context.agent_name
    print(f"[EmbeddingFilter] Agent '{agent_name}' - Stripped embeddings from '{tool.name}' response")
    
    return cleaned_response
