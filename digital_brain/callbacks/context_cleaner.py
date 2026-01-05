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
                # Search backwards for the LAST retriever event (the final text/JSON output)
                for i in range(len(history) - 1, last_user_idx, -1):
                    if history[i].author == "context_retriever":
                        context_event = history[i]
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
                
                # --- Detailed Logging of Removed Events ---
                logger.info("--- 🗑️  ContextCleaner: Detailed Removal Log ---")
                removed_count = 0
                kept_ids = {id(e) for e in clean_history}
                
                # Check events starting from the user message to the end (since previous history is kept by default)
                for event in history[last_user_idx:]:
                    if id(event) not in kept_ids:
                        removed_count += 1
                        # Safe content preview
                        content_str = "No content"
                        if hasattr(event, "content") and event.content:
                             content_str = str(event.content)
                        
                        # Clean newlines for log readability
                        preview = content_str[:150].replace('\n', ' ')
                        
                        # Check for tool info
                        tool_info = ""
                        # Events might be ToolCall or ToolResponse types in ADK, 
                        # but typically valid Event objects have 'tool_calls' or similar fields if they are complex.
                        # We stick to generic author/content logging.
                        
                        logger.info(f"❌ Removing [{event.author}]: {preview}...")

                logger.info(f"--- Summary: Removed {removed_count} events. Kept {len(clean_history)} events. ---")

                # Update the session events
                session.events[:] = clean_history
                
                # Reset the flag
                state["is_write_flow"] = False
        
    except Exception as e:
        logger.error(f"⚠️ ContextCleaner Error: {e}")
        
    return None # Return None to let ADK use the original agent result
