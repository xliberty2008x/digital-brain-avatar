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
            logger.info(f"🧹 ContextCleaner: Detected end of WRITE flow (Agent: {agent_name}). Cleaning up history...")
            
            # session.events is a list-like object in ADK that triggers persistence.
            history = session.events
            
            # 1. Find the last message from 'user'.
            last_user_idx = -1
            for i in range(len(history) - 1, -1, -1):
                if history[i].author == "user":
                    last_user_idx = i
                    break
                    
            if last_user_idx != -1:
                # 2. Identify and preserve important events:
                # - The retrieved context (context_retriever)
                # - The final bot response (response_agent)
                
                context_event = None
                for event in history[last_user_idx:]:
                    if event.author == "context_retriever":
                        context_event = event
                        break
                
                final_event = history[-1] if len(history) > 0 else None
                
                # Start with history before the turn + the user message
                clean_history = list(history[:last_user_idx + 1])
                
                if context_event:
                    clean_history.append(context_event)
                    logger.info("🧹 ContextCleaner: Preserved the context_retriever output.")
                
                if final_event and final_event.author == "response_agent":
                    clean_history.append(final_event)
                    logger.info("🧹 ContextCleaner: Preserved the final response from response_agent.")
                
                # Update the session events
                session.events.clear()
                session.events.extend(clean_history)
                
                logger.info(f"🧹 ContextCleaner: Cleaned history. Kept {len(clean_history)} events.")
                
                # Reset the flag
                state["is_write_flow"] = False
        
    except Exception as e:
        logger.error(f"⚠️ ContextCleaner Error: {e}")
        
    return None # Return None to let ADK use the original agent result
