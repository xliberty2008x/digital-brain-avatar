from google.adk.agents.callback_context import CallbackContext
from typing import Optional, Any
import logging

logger = logging.getLogger(__name__)

async def clean_context_after_write(callback_context: CallbackContext) -> Optional[Any]:
    """
    Cleans up session history after a heavy agent flow (like WRITE) to save tokens and context.
    Removes intermediate tool calls and thoughts, keeping only the User input and Final Response.
    """
    try:
        agent_name = callback_context.agent_name
        session = callback_context.session
        state = callback_context.state

        # Only run cleanup if we are at the end of the chain AND it was a WRITE flow
        # In this architecture, we attach it to the Orchestrator, so agent_name will be 'digital_brain_orchestrator'
        is_write = state.get("is_write_flow", False)
        
        if is_write:
            # Final check and cleanup flag reset
            logger.info(f"🧹 ContextCleaner: Finalizing WRITE flow (Agent: {agent_name}).")
            # We already pruned in Orchestrator, but this resets the state
            state["is_write_flow"] = False
        
    except Exception as e:
        logger.error(f"⚠️ ContextCleaner Error: {e}")
        
    return None # Return None to let ADK use the original agent result
