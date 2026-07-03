"""Package init for Digital Brain.

Keep service imports lightweight so unit tests can import modules without the
runtime ADK stack installed.
"""

__all__ = ["agent"]

try:
    from . import agent  # type: ignore
except ModuleNotFoundError:
    agent = None
