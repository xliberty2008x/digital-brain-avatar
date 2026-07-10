from contextvars import ContextVar
from typing import Optional

# Holds the agent JWT for the current execution context so tools can read it
# without passing tokens through the LLM-visible tool arguments.
agent_token_context: ContextVar[Optional[str]] = ContextVar("agent_token", default=None)


def set_agent_token(token: str):
    """Set the token for the current context."""
    agent_token_context.set(token)


def get_agent_token() -> Optional[str]:
    """Get the token from the current context."""
    return agent_token_context.get()
