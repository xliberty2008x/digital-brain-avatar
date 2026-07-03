"""Runtime configuration helpers for Digital Brain."""

from __future__ import annotations

import os


DEFAULT_LOCAL_MCP_URL = "http://localhost:8000/api/mcp/"


def get_mcp_url() -> str:
    """Return the configured Neo4j MCP endpoint.

    The project now defaults to the local Dockerized MCP server. Set
    DIGITAL_BRAIN_MCP_URL to point at another compatible server.
    """
    return os.getenv("DIGITAL_BRAIN_MCP_URL", DEFAULT_LOCAL_MCP_URL).strip() or DEFAULT_LOCAL_MCP_URL
