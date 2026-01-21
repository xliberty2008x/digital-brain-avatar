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
                # 2. Build clean history: keep ONLY user messages and response_agent outputs
                # This removes ALL internal sub-agent logs (router, extractor, retriever, writer, executor)
                
                # Define which authors to KEEP
                allowed_authors = {"user", "response_agent", "digital_brain_orchestrator"}
                
                clean_history = []
                
                for event in history:
                    # Keep only events from allowed authors
                    if event.author in allowed_authors:
                        # Additionally filter out orchestrator tool calls (just in case)
                        has_function_calls = False
                        try:
                            function_calls = event.get_function_calls()
                            has_function_calls = bool(function_calls)
                        except (AttributeError, TypeError):
                            pass
                        
                        if not has_function_calls:
                            clean_history.append(event)
                
                logger.info(f"🧹 ContextCleaner: Preserved response_agent output.")
                
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
                
                # Clear memory-related session state to prevent stale data in next retriever run
                state["accumulated_context"] = []
                state["previous_findings"] = []
                state["context_output"] = ""
                logger.info("🧹 ContextCleaner: Cleared accumulated_context, previous_findings, context_output")
                
                # Reset the flag
                state["is_write_flow"] = False
        
    except Exception as e:
        logger.error(f"⚠️ ContextCleaner Error: {e}")
        
    return None # Return None to let ADK use the original agent result
