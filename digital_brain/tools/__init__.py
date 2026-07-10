"""Tool exports for Digital Brain.

Keep direct MCP helpers importable without the ADK runtime present.
"""

from .mcp_client import (
    append_journal_entry,
    call_mcp_tool,
    execute_cypher,
    get_harness_generation,
    get_journal_append_receipt,
    get_journal_chain_head,
    record_harness_generation,
)

__all__ = [
    "call_mcp_tool",
    "execute_cypher",
    "get_journal_chain_head",
    "append_journal_entry",
    "get_journal_append_receipt",
    "get_harness_generation",
    "record_harness_generation",
]

try:
    from .neo4j_toolkit import create_neo4j_toolset, full_access_toolset, read_only_toolset

    __all__.extend(["create_neo4j_toolset", "read_only_toolset", "full_access_toolset"])
except ModuleNotFoundError:
    create_neo4j_toolset = None
    read_only_toolset = None
    full_access_toolset = None
