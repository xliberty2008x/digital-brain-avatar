# File: my_agent/tools/neo4j_toolkit.py
"""
Reusable Neo4j MCP Toolkit factory for different agents.
"""

from google.adk.tools.mcp_tool.mcp_toolset import (
    McpToolset,
    StreamableHTTPConnectionParams,
)

from ..config import DEFAULT_LOCAL_MCP_URL, get_mcp_url

DEFAULT_MCP_URL = DEFAULT_LOCAL_MCP_URL


def create_neo4j_toolset(
    tools: list[str] | None = None,
    url: str | None = None
) -> McpToolset:
    """
    Create a Neo4j MCP toolset with optional tool filtering.
    
    Args:
        tools: List of tool names to include. If None, all tools are available.
        url: MCP server URL
    
    Returns:
        Configured McpToolset
    """
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(url=url or get_mcp_url()),
        tool_filter=tools,
    )


# Pre-configured toolsets for common use cases
def read_only_toolset(url: str | None = None) -> McpToolset:
    """Toolset with only read operations (no writes)."""
    return create_neo4j_toolset(
        tools=['read_neo4j_cypher', 'get_neo4j_schema'],
        url=url
    )


def full_access_toolset(url: str | None = None) -> McpToolset:
    """Toolset with full read/write access."""
    return create_neo4j_toolset(
        tools=[
            'read_neo4j_cypher',
            'write_neo4j_cypher',
            'get_neo4j_schema',
            'get_journal_chain_head',
            'append_journal_entry',
            'get_journal_append_receipt',
        ],
        url=url
    )
