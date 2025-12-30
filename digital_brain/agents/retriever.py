from google.adk.agents.llm_agent import LlmAgent
from ..tools import read_only_toolset

context_retriever = LlmAgent(
    model="gemini-3-flash-preview",
    name="context_retriever",
    instruction="""
    You are a context retrieval agent for the Digital Brain.
    Here are your previous findings: {context_output}. 
    - If the answer to the current search_query is ALREADY in previous findings, just summarize it and DO NOT call the search tool again.
    - Only search for NEW information.
    
    Your task:
    1. Use the search_query to find related past entries
    2. Find existing nodes that should be linked (MERGE, not CREATE)
    3. Get the last JournalEntry ID for NEXT_ENTRY linking


    **Tools available:**
    - read_neo4j_cypher(query, params, embed_text)

    **Search strategy:**
    1. Vector search using embed_text parameter
    2. Exact match for Person/Topic nodes

    **Output format:**
    {
      "related_entries": [...],
      "existing_nodes": {
        "Person": [{"id": "...", "name": "батько"}],
        "Topic": [{"id": "...", "name": "робота"}]
      },
      "last_entry_id": "...",
      "context_summary": "User has had 2 previous conflicts with father about work"
    }
    """,
    output_key="context_output",
    tools=[read_only_toolset()],
)
