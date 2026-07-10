"""Package init for Digital Brain.

Keep service imports lightweight so unit tests can import modules without the
runtime ADK stack installed.
"""

__all__ = ["agent"]

try:
    from . import agent  # type: ignore
except ImportError:
    # Minimal clients (for example, the isolated MCP E2E image) deliberately
    # ship only config/tools, not the optional ADK runtime graph.
    agent = None
