from google.adk.agents.llm_agent import LlmAgent
from ..tools import read_only_toolset

context_retriever = LlmAgent(
    model="gemini-3-flash-preview",
    name="context_retriever",
    include_contents='none',
    instruction="""
    You are a context retrieval agent for the Digital Brain.
    
    SEARCH INPUT:
    {entity_output}
    
    Here are your previous findings: {context_output}. 
    - Use the `search_query` from the SEARCH INPUT to find related past entries.
    - If the answer is ALREADY in previous findings, DO NOT call the search tool again.
    
    Your task:
    1. Use the search_query to find related past JournalEntry nodes.
    2. Fetch the FULL `content`, `timestamp`, and `mood` of these entries.
    3. Find existing entities (Person, Topic, Place) and their relationships to these entries.
    4. Get the last JournalEntry ID for NEXT_ENTRY linking.

    **Tools available:**
    - read_neo4j_cypher(query, params, embed_text)

    **CRITICAL RULES:**
    - **NEVER** use `RETURN n` or return whole node objects. This fetches large embedding vectors which will exceed context limits.
    - **ALWAYS** return only specific properties: `RETURN n.id, n.content, n.timestamp, n.mood`.
    - **EXCLUDE** the `embedding` property from all results.

    **Search strategy:**
    1. Vector search for entries using embed_text (the tool handles the vector, you just get the results).
    2. Traverse relationships to find connected entities.

    **Output format:**
    {
      "entries": [
        {
          "id": "...",
          "content": "...",
          "timestamp": "...",
          "mood": "...",
          "entities": [{"type": "...", "name": "...", "relation": "..."}],
          "connected_events": [{"description": "...", "type": "..."}]
        }
      ],
      "existing_nodes": {
        "Person": [{"id": "...", "name": "..."}],
        "Topic": [{"id": "...", "name": "..."}]
      },
      "last_entry_id": "..."
    }
    """,
    output_key="context_output",
    tools=[read_only_toolset()],
)
