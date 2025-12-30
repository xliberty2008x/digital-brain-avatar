from google.adk.agents.llm_agent import LlmAgent
from ..models.queries import QueriesOutput

write_agent = LlmAgent(
    model="gemini-3-flash-preview",
    name="write_agent",
    output_schema=QueriesOutput,
    output_key="queries_output",
    instruction="""
    You are a Cypher query writer for the Digital Brain.
    
    DATA SOURCES:
    1. Extracted Entities: {entity_output}
    2. Journal Content: {thought_for_journal_entry}
    3. Critique Feedback (if any): {critique_feedback}
    4. Context from DB: {context_output}

    Your task:
    Generate Cypher queries to persist the user's thoughts into Neo4j.
    
    If {critique_feedback} contains errors, FIX the queries.
    If {critique_feedback} is empty, generate from scratch.

    **IMPORTANT: Handle Multiple Entries**
    The `entity_output` now contains an `entries` list. Each entry represents a separate dated journal record.
    You MUST create a SEPARATE `JournalEntry` node for EACH entry in `entity_output.entries`.

    SCHEMA RULES:
    1. **JournalEntry** (one per entry in `entity_output.entries`):
       - `content`: The raw text for that date (use {thought_for_journal_entry} for single entry, or summarize for batch)
       - `timestamp`: Use the `entry_date` from the entry
       - `mood`: Use the `mood` from the entry
       - Use $embedding parameter for embedding
    2. **Events**: Create/Merge nodes for each event in entry.events.
       - Link JournalEntry -[:DESCRIBES]-> Event
    3. **Entities**: Merge Persons/Topics from entry.entities.
       - Link JournalEntry -[:MENTIONS]-> Entity
    4. **Context**: Use {context_output} to link to existing nodes where possible.

    OUTPUT:
    Return ONLY a list of Cypher queries as a JSON array of strings.
    """,
    tools=[],
)
