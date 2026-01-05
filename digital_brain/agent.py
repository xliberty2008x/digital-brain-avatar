from google.adk.agents import BaseAgent, LoopAgent, SequentialAgent
from google.adk.apps.app import App
from google.adk.plugins import ReflectAndRetryToolPlugin
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from typing import AsyncGenerator
from datetime import datetime
import asyncio

# Modularized Imports
from .agents.router import router_agent
from .agents.extractor import entity_extractor
from .agents.retriever import context_retriever
from .agents.writer import write_agent
from .agents.critic import critic_agent
from .agents.executor import executor_agent
from .agents.response import response_agent
from .tools.utils import retry_generator
from .callbacks.context_cleaner import clean_context_after_write

async def consume_generator(gen):
    """Helper to drive an async generator to completion in the background."""
    try:
        async for _ in gen:
            pass
    except Exception as e:
        print(f"Background Task Error: {e}")

from pydantic import ConfigDict

class DigitalBrainOrchestrator(BaseAgent):
    model_config = ConfigDict(extra="allow")
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
    

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        # 0. Initial Setup & Context Reconstruction (STATELESS)

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ctx.session.state["current_time"] = current_time
        ctx.session.state["critique_feedback"] = ""  # Default empty for deactivated loop
        ctx.session.state["clarify_missing"] = None  # Reset clarify state each turn
        if "context_output" not in ctx.session.state:
            ctx.session.state["context_output"] = ""
        
        # Capture raw input from current turn
        current_raw_input = ""
        if ctx.user_content and ctx.user_content.parts:
            for part in ctx.user_content.parts:
                if part.text:
                    current_raw_input = part.text
                    break

        # Reconstruct Thought Buffers: Previous (already written) & Current (new)
        events = ctx.session.events if hasattr(ctx.session, 'events') else []
        
        previous_buffer = []  # Messages BEFORE last entity_extractor (already written)
        current_buffer = []   # Messages AFTER last entity_extractor (new, unwritten)
        found_extractor = False

        for event in reversed(events):
            if event.author == 'entity_extractor':
                found_extractor = True
                continue  # Skip the extractor event itself
            
            if event.author == 'user':
                text = ""
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            text += part.text
                if text:
                    if found_extractor:
                        previous_buffer.append(text)
                    else:
                        current_buffer.append(text)
        
        # Reverse to get chronological order
        previous_buffer = list(reversed(previous_buffer))
        current_buffer = list(reversed(current_buffer))

        # Remove duplication of current input if present
        if current_buffer and current_buffer[-1].strip() == current_raw_input.strip():
             current_buffer.pop()
        
        print(f"DEBUG: Previous Context (already written): {len(previous_buffer)} items")
        print(f"DEBUG: Current Thoughts (new): {len(current_buffer)} items")

        # Previous context for reference (read-only, already saved)
        ctx.session.state["previous_context"] = "\n".join(previous_buffer) if previous_buffer else "(No previous context)"
        
        # Current thoughts for Router decision AND Extractor
        current_thoughts_list = current_buffer + [current_raw_input]
        ctx.session.state["current_thoughts"] = "\n".join(current_thoughts_list)
        
        # Full context for Response Agent (needs everything)
        full_context = previous_buffer + current_buffer + [current_raw_input]
        ctx.session.state["thought_buffer_context"] = "\n".join(full_context)
        
        # 1. Run Router
        async for event in router_agent.run_async(ctx):
             yield event
        
        # 2. Handle Decision
        decision = ctx.session.state.get("routing_decision")
        if not decision:
            print("ERROR: No routing decision found.")
            return

        route = decision.get("route")
        print(f"🔀 ROUTER DECISION: {route}")

        if route == "SKIP":
            async for event in response_agent.run_async(ctx):
                yield event
            
        elif route == "CLARIFY":
            ctx.session.state["clarify_missing"] = decision.get("missing", [])
            async for event in response_agent.run_async(ctx):
                yield event
            
        elif route == "WRITE":
            ctx.session.state["thought_for_journal_entry"] = ctx.session.state["current_thoughts"]
            # FLAG START OF WRITE FLOW FOR CONTEXT CLEANER
            ctx.session.state["is_write_flow"] = True 
            
            yield Event(
                author="digital_brain",
                content={"parts": [{"text": "Ця думка дуже важлива. Я надійно записую її у твою пам'ять... 🧠"}]},
            )
            
            # Step 1: Extract Entities (Pure LLM)
            async for event in entity_extractor.run_async(ctx):
                yield event
                
            # Step 2: Retrieve Context from DB (MCP - Network sensitive)
            async for event in retry_generator(lambda: context_retriever.run_async(ctx), max_retries=4, initial_delay=5):
                yield event
                
            # Step 3: Write Queries (Pure LLM)
            async for event in write_agent.run_async(ctx):
                yield event
            
            # Step 4: Critic validates and fixes duplicates (Pure LLM)
            async for event in critic_agent.run_async(ctx):
                yield event
            
            # Step 5: Execute Queries (MCP - Network sensitive)
            async for event in retry_generator(lambda: executor_agent.run_async(ctx), max_retries=4, initial_delay=5):
                yield event
            
            # Step 5: Final Psychologist Response
            async for event in response_agent.run_async(ctx):
                yield event
            
        # elif route == "READ":
        #     ctx.session.state["thought_buffer"] = []
        #     print(">> READ flow")
            
        else:
            print(f"Error: Unknown route {route}")


orchestrator = DigitalBrainOrchestrator(
    name="digital_brain_orchestrator",
    after_agent_callback=clean_context_after_write
)

# 4. Writer-Critic Loop for Cypher generation
# cypher_loop = LoopAgent(
#     name="cypher_loop",
#     sub_agents=[write_agent, critic_agent],
#     max_iterations=3
# )   

# 5. Full Write Sequence
write_flow_sequence = SequentialAgent(
    name="write_flow_sequence",
    sub_agents=[entity_extractor, context_retriever, write_agent],
)

root_agent = orchestrator

app = App(
    name="digital_brain",
    root_agent=root_agent,
    plugins=[
        ReflectAndRetryToolPlugin(max_retries=3),
    ],
)
