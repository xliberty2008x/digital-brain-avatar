# File: digital_brain/tools/mcp_client.py
"""
Direct MCP Client for deterministic Neo4j queries.
Bypasses LLM agents for reliable entity existence checks.
Includes retry logic for Cloud Run cold starts.
"""

import aiohttp
import asyncio
import json
from typing import Any

DEFAULT_MCP_URL = "https://mcp-neo4j-cypher-858161250402.us-central1.run.app/api/mcp/"

# Retry configuration for cold starts
MAX_RETRIES = 4
INITIAL_DELAY = 5  # seconds


async def call_mcp_tool(
    tool_name: str,
    arguments: dict[str, Any],
    url: str = DEFAULT_MCP_URL,
    max_retries: int = MAX_RETRIES,
    initial_delay: int = INITIAL_DELAY
) -> dict[str, Any]:
    """
    Call an MCP tool directly via HTTP POST with retry logic.
    
    Args:
        tool_name: Name of the MCP tool (e.g., 'read_neo4j_cypher')
        arguments: Tool arguments as a dictionary
        url: MCP server endpoint
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries (doubles each attempt)
    
    Returns:
        Tool response as a dictionary
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }
    
    last_error = None
    delay = initial_delay
    
    for attempt in range(max_retries + 1):
        try:
            timeout = aiohttp.ClientTimeout(total=30)  # 30 second timeout
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"
                    }
                ) as response:
                    if response.status == 200:
                        # Read raw text to bypass strict mimetype checks of response.json()
                        text = await response.text()
                        
                        # Try to parse as SSE (Server-Sent Events)
                        # Look for lines starting with "data:"
                        for line in text.splitlines():
                            if line.strip().startswith("data:"):
                                try:
                                    data_str = line.strip()[5:].strip()
                                    result = json.loads(data_str)
                                    if "error" in result:
                                        raise Exception(f"MCP error: {result['error']}")
                                    return result.get("result", {})
                                except json.JSONDecodeError:
                                    continue
                        
                        # Fallback: Try to parse the whole body as JSON
                        # This works if the server sent JSON but with wrong mimetype,
                        # or if it's a standard JSON response.
                        try:
                            result = json.loads(text)
                            if "error" in result:
                                raise Exception(f"MCP error: {result['error']}")
                            return result.get("result", {})
                        except json.JSONDecodeError:
                            raise Exception(f"Could not parse response (first 200 chars): {text[:200]}")

                    elif response.status in [502, 503, 504]:
                        # Cold start or temporary unavailability
                        error_text = await response.text()
                        raise aiohttp.ClientError(f"Server warming up ({response.status}): {error_text}")
                    else:
                        error_text = await response.text()
                        raise Exception(f"MCP call failed ({response.status}): {error_text}")
                        
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
            last_error = e
            if attempt < max_retries:
                print(f"⏳ MCP cold start... waiting {delay}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                print(f"❌ MCP call failed after {max_retries} retries: {e}")
                raise
        except Exception as e:
            # Non-retryable error
            raise
    
    raise last_error or Exception("MCP call failed with unknown error")


async def execute_cypher(query: str, params: dict = None) -> list[dict]:
    """
    Execute a Cypher query directly via MCP with retry logic.
    
    Args:
        query: Cypher query string
        params: Query parameters
    
    Returns:
        List of result records
    """
    arguments = {"query": query}
    if params:
        arguments["params"] = params  # Pass dict directly, not JSON string!
    
    result = await call_mcp_tool("read_neo4j_cypher", arguments)
    
    # Parse the result content
    if isinstance(result, dict) and "content" in result:
        content = result["content"]
        if isinstance(content, list) and len(content) > 0:
            text = content[0].get("text", "[]")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return []
    
    return []
