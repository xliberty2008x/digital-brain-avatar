# File: my_agent/tools/neo4j_toolkit.py
"""
Reusable Neo4j MCP Toolkit factory for different agents.
"""

from google.adk.tools.mcp_tool.mcp_toolset import (
    McpToolset,
    StreamableHTTPConnectionParams,
)

# Default MCP Server URL
DEFAULT_MCP_URL = "https://mcp-neo4j-cypher-858161250402.us-central1.run.app/api/mcp/"


def create_neo4j_toolset(
    tools: list[str] | None = None,
    url: str = DEFAULT_MCP_URL
) -> McpToolset:
    """
    Create a Neo4j MCP toolset with optional tool filtering.
    
    Args:
        tools: List of tool names to include. If None, all tools are available.
               Available tools: 'read_neo4j_cypher', 'write_neo4j_cypher', 'get_neo4j_schema'
        url: MCP server URL
    
    Returns:
        Configured McpToolset
    """
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(url=url),
        tool_filter=tools,
    )


# Pre-configured toolsets for common use cases
def read_only_toolset(url: str = DEFAULT_MCP_URL) -> McpToolset:
    """Toolset with only read operations (no writes)."""
    return create_neo4j_toolset(
        tools=['read_neo4j_cypher', 'get_neo4j_schema'],
        url=url
    )


def full_access_toolset(url: str = DEFAULT_MCP_URL) -> McpToolset:
    """Toolset with full read/write access."""
    return create_neo4j_toolset(
        tools=['read_neo4j_cypher', 'write_neo4j_cypher', 'get_neo4j_schema'],
        url=url
    )
