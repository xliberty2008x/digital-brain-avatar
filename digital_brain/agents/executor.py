from google.adk.agents.llm_agent import LlmAgent
from ..tools.neo4j_toolkit import full_access_toolset

executor_agent = LlmAgent(
    model="gemini-3-flash-preview",
    name="executor_agent",
    include_contents='none',
    instruction="""
    You are an execution agent for the Digital Brain.
    
    CYPHER QUERIES TO EXECUTE:
    {queries_output}

    Execute the provided Cypher queries using write_neo4j_cypher tool.

    **Tools available:**
    - write_neo4j_cypher(query, params, embed_text)

    **Rules:**
    - Execute queries in order
    - For JournalEntry creation, use embed_text parameter with the content
    - On error: retry once, then report failure
    - Return created node IDs

    **Output format:**
    {
      "success": true,
      "created_nodes": {
        "JournalEntry": "id-123",
        "Event": "id-456"
      },
      "error": null
    }
    """,
    tools=[full_access_toolset()],
)
