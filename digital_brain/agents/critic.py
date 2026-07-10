from google.adk.agents.llm_agent import LlmAgent
from ..models.queries import QueriesOutput

critic_agent = LlmAgent(
    model="gemini-3-flash-preview",
    name="critic_agent",
    output_schema=QueriesOutput,
    output_key="queries_output",
    instruction="""
    You validate a structured append plan against `docs/GRAPH_SCHEMA_CONTRACT.md`.
    
    PLAN TO VALIDATE:
    {queries_output}
    
    **CRITICAL DATA FROM ENTITY RESOLUTION (Phase 1):**
    - EXISTING ENTITIES: {existing_entities}
    - NEW ENTITIES: {new_entities}
    
    YOUR TASK:
    Ensure the journal payload and post-append mutations follow the contract:
    0. The `journal` object contains the journal text, timestamp and optional
       mood only. It must not contain Cypher or embedding values.
    1. **Duplicate Prevention**: 
       - No `CREATE` for entities in `existing_entities`. 
       - If ID is present: Validated `MERGE` with ID.
       - If ID="MISSING": Must use `MATCH` by name and `SET id = randomUUID()`.
    2. **Labels & Relationships**: Must use PascalCase for labels and UPPER_SNAKE_CASE for relationship types.
    3. **Specific Relationships**:
       - Emotional states must use (Person)-[:EXPERIENCED]->(State).
       - People involved in events must use (Person)-[:PARTICIPATED]->(Event).
    4. **Append invariant**: post-append mutations must not create or merge
       JournalEntry or FOLLOWS. They must use `$journal_id` to link the entry
       and stable `$append_key`-derived identifiers for retry-safe new nodes.
    5. **Common Errors**: Fix missing IDs, incorrect relationship directions,
       or non-contract labels.
    
    **OUTPUT:**
    Return the validated `QueriesOutput` object. If fixed, preserve the
    journal payload and make every post-append mutation idempotent.
    """,
    tools=[],
)
