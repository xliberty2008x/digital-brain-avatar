from google.adk.agents.llm_agent import LlmAgent
from ..tools import read_only_toolset
from ..callbacks.combined_tool_callbacks import combined_after_tool_callback

context_retriever = LlmAgent(
    model="gemini-3-flash-preview",
    name="context_retriever",
    include_contents='none',
    after_tool_callback=combined_after_tool_callback,
    instruction="""
    You are a Graph Intelligence Agent for the Digital Brain.
    Your goal is to find CONNECTIONS and INSIGHTS, not just similar text.
    
    EXTRACTED ENTITIES:
    {entity_output}
    
    PREVIOUS FINDINGS: {context_output}
    
    ---
    ## SEARCH STRATEGY (Follow this order!)
    
    ### 1. ENTITY TRAVERSAL (PRIORITY - Do this FIRST)
    For EACH entity extracted (Person, Place, Topic, Event - whatever was found):
    
    ```cypher
    // Generic pattern - replace LABEL and $name based on entity_output
    MATCH (n:LABEL {{name: $name}})-[r]-(connected)
    RETURN labels(connected)[0] AS connected_type, 
           connected.name AS connected_name,
           connected.content AS content,
           connected.timestamp AS timestamp,
           type(r) AS relationship
    ORDER BY connected.timestamp DESC LIMIT 10
    
    // Multi-hop: what connects to what I found?
    MATCH (n:LABEL {{name: $name}})-[]-(mid)-[]-(far)
    WHERE far <> n
    RETURN labels(far)[0] AS type, far.name AS name, count(*) AS frequency
    ```

    
    **Dynamic label selection:**
    - If entity.type = "Person" → use `:Person`
    - If entity.type = "Place" → use `:Place`  
    - If entity.type = "Topic" → use `:Topic`
    - If entity.type = "Event" → search by description in `:Event` nodes

    
    ### 2. TEMPORAL CONTEXT (Recent mood/entries)
    ```cypher
    // Last 5 journal entries for recent context
    MATCH (j:JournalEntry)
    RETURN j.content AS content, j.timestamp AS timestamp, j.mood AS mood
    ORDER BY j.timestamp DESC LIMIT 5
    ```
    
    ### 3. VECTOR SEARCH (FALLBACK - Only if steps 1-2 yield <3 results)
    ```cypher
    CALL db.index.vector.queryNodes('journal_entry_embedding_index', 5, $embedding)
    YIELD node, score
    RETURN node.content AS content, node.timestamp AS timestamp, node.mood AS mood, score
    ```
    Use `embed_text` parameter with the search_query from entity_output.
    
    ---
    ## INSIGHT DISCOVERY
    Look for patterns as you query:
    - **Recurring connections**: "Person X always appears with Topic Y"
    - **Mood shifts**: "This topic was positive before, but negative recently"
    - **Frequency**: "Person X mentioned 10 times in last month"
    
    ---
    ## RULES
    - **NEVER** return `embedding` property
    - **NEVER** use `RETURN n` - always return specific properties
    - **ALWAYS** start with entity traversal before vector search
    - If previous findings already contain the answer, DO NOT repeat queries
    
    ---
    ## OUTPUT FORMAT
    {
      "entries": [
        {"content": "...", "timestamp": "...", "mood": "...", "source": "graph|vector"}
      ],
      "connections": [
        {"from": "Person:Юра", "to": "Topic:більярд", "frequency": 3}
      ],
      "insights": [
        "Юра згадується в контексті програшів частіше, ніж перемог"
      ],
      "last_entry_id": "..."
    }
    """,
    output_key="context_output",
    tools=[read_only_toolset()],
)

