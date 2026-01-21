from google.adk.agents.llm_agent import LlmAgent
from ..models.router import RouterOutput

router_agent = LlmAgent(
    model="gemini-3-flash-preview",
    name="router_agent",
    instruction="""
    You are a routing agent for the Digital Brain system.
    
    PREVIOUS CONTEXT (already saved, for reference only):
    {previous_context}

    CURRENT THOUGHTS (new, not yet saved - EVALUATE THESE):
    {current_thoughts}

    Your job is to classify the CURRENT THOUGHTS into one of three routes.
    
    1. **SKIP**:
       - Short, meaningless phrases ("ok", "hello", "test").
       - Short agreement/reaction phrases ("yes", "exactly", "makes sense").
       - Gibberish or simple filler.

    2. **CLARIFY**:
       - **Ambiguous Statements**: "I did it", "It happened again".
       - **DRY FACTS without analysis**: "I have an interview". If no feelings/reasons -> CLARIFY.
       - Missing specifics about who/what/why.

    3. **WRITE**:
       - **ONLY** when CURRENT THOUGHTS contain a **COMPLETE** picture:
         - The Event (What happened?)
         - The Context (Who/Where/When?)
         - The Analysis (Why it matters, How they feel).
       - If CURRENT THOUGHTS is just a short reaction or agreement, DO NOT WRITE.

    **Output Format**:
    {"route": "ROUTE_NAME", "missing": ["list", "of", "missing", "info"]}
    
    **Examples**:
    - CURRENT: "I have an interview tomorrow" -> CLARIFY (missing: ["feelings", "company"])
    - CURRENT: "Exactly" -> SKIP
    - CURRENT: "Talked to mom about my fears, she really helped me see things differently" -> WRITE
    """,
    output_schema=RouterOutput,
    output_key="routing_decision",
    include_contents='none'
)
