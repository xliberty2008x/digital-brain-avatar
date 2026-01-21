from google.adk.agents.llm_agent import LlmAgent
from ..models.queries import QueriesOutput

critic_agent = LlmAgent(
    model="gemini-3-flash-preview",
    name="critic_agent",
    output_schema=QueriesOutput,
    output_key="queries_output",
    instruction="""
    You are a Cypher query validator that ensures adherence to the `docs/GRAPH_SCHEMA_CONTRACT.md`.
    
    QUERIES TO VALIDATE:
    {queries_output}
    
    **CRITICAL DATA FROM ENTITY RESOLUTION (Phase 1):**
    - EXISTING ENTITIES: {existing_entities}
    - NEW ENTITIES: {new_entities}
    
    YOUR TASK:
    Ensure all queries follow the Graph Schema Contract rules:
    1. **Duplicate Prevention**: 
       - No `CREATE` for entities in `existing_entities`. 
       - If ID is present: Validated `MERGE` with ID.
       - If ID="MISSING": Must use `MATCH` by name and `SET id = randomUUID()`.
    2. **Labels & Relationships**: Must use PascalCase for labels and UPPER_SNAKE_CASE for relationship types.
    3. **Specific Relationships**:
       - Emotional states must use (Person)-[:EXPERIENCED]->(State).
       - People involved in events must use (Person)-[:PARTICIPATED]->(Event).
    4. **Common Errors**: Fix missing IDs, incorrect relationship directions, or non-contract labels.
    
    **OUTPUT:**
    Return the VALIDATED/FIXED list of Cypher queries. 
    - If correct, return unchanged.
    - If fixed, ensure it is a valid Cypher query using the contract rules.
    """,
    tools=[],
)
