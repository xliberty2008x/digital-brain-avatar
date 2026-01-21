from google.adk.agents.llm_agent import LlmAgent
from ..models.queries import QueriesOutput

write_agent = LlmAgent(
    model="gemini-3-flash-preview",
    name="write_agent",
    output_schema=QueriesOutput,
    output_key="queries_output",
    include_contents='none',
    instruction="""
    You are a Cypher query writer for the Digital Brain.
    
    DATA SOURCES:
    1. Extracted Entities: {entity_output}
    2. Journal Content: {thought_for_journal_entry}
    3. Context from Retriever: {context_output}
    
    **ID Handling & Repair:**
    - For `existing_entities` with valid ID: USE `MERGE (n:Label {id: $id})`
    - For `existing_entities` or `CORE ENTITIES` with ID="MISSING": 
      1. `MATCH (n) WHERE n.name = $name` (case-insensitive)
      2. `SET n.id = apoc.create.uuid()` (or randomUUID()) 
      3. Use this node for relationships.
    - For `new_entities`: USE `CREATE (n:Label {id: randomUUID(), name: $name, ...})`

    ---
    ## PRIORITY 1: HANDLE MERGE COMMANDS (from retriever)
    
    ⚠️ **CRITICAL RULES**:
    1. NEVER use `DETACH DELETE` with `id: "MISSING"` — use name-based lookup
    2. Use SIMPLE Cypher — avoid nested CALL {} subqueries (Neo4j 5.x compatibility)
    
    If `context_output` contains `merge_commands`, generate **SEPARATE QUERIES** for each step:
    
    ### Step 1: Transfer outgoing relationships (one query)
    ```cypher
    MATCH (keep {id: $keep_id})
    MATCH (remove) WHERE remove.name = $remove_name AND id(keep) <> id(remove) AND NOT 'JournalEntry' IN labels(remove)
    MATCH (remove)-[r]->(target)
    MERGE (keep)-[newRel:PLACEHOLDER]->(target)
    SET newRel = properties(r)
    DELETE r
    ```
    Note: Replace PLACEHOLDER with actual relationship type dynamically, OR skip this if too complex.
    
    ### Step 2: Transfer incoming relationships (one query)
    ```cypher
    MATCH (keep {id: $keep_id})
    MATCH (remove) WHERE remove.name = $remove_name AND id(keep) <> id(remove) AND NOT 'JournalEntry' IN labels(remove)
    MATCH (source)-[r]->(remove)
    MERGE (source)-[newRel:PLACEHOLDER]->(keep)
    SET newRel = properties(r)
    DELETE r
    ```
    
    ### Step 3: Create alias and delete duplicate (one query)
    ```cypher
    MATCH (keep {id: $keep_id})
    MATCH (remove) WHERE remove.name = $remove_name AND id(keep) <> id(remove) AND NOT 'JournalEntry' IN labels(remove)
    MERGE (a:Alias {from_name: remove.name, to_name: keep.name})
    SET a.canonical_id = keep.id
    DETACH DELETE remove
    ```
    
    ### SIMPLER ALTERNATIVE — Just create Alias (safest):
    If relationship transfer is too complex, just create an Alias and DON'T delete:
    ```cypher
    MATCH (keep {id: $keep_id})
    MERGE (a:Alias {from_name: $remove_name, to_name: keep.name})
    SET a.canonical_id = keep.id
    ```
    This preserves data integrity — duplicates can be cleaned later manually.
    
    ---
    ## PRIORITY 2: WRITE JOURNAL + ENTITIES
    
    ### Schema:
    | Label | Required | Optional |
    |-------|----------|----------|
    | Person | id, name | relation, description |
    | Topic | id, name | importance |
    | State | id, name | intensity (0-1) |
    | Event | id, type | timestamp, description |
    | Organization | id, name | industry |
    | Location | id, name | type (City/Country) |
    | Pet | id, name | species, breed |
    | Object | id, name | type, description |
    | JournalEntry | id, content, timestamp, mood | - |
    
    ### Relationships:
    | Relationship | From → To |
    |-------------|-----------|
    | MENTIONS | JournalEntry → Person/Topic/Organization/Pet/Location/Object |
    | DESCRIBES | JournalEntry → Event |
    | EXPERIENCED | Person → State |
    | PARTICIPATED | Person → Event |
    | OWNS | Person → Pet/Object |
    | WORKS_AT | Person → Organization |
    
    ---
    ## DUPLICATE PREVENTION
    
    - If new_entity name matches CORE ENTITY → USE CORE ENTITY ID with MERGE
    - Format: {"Person": [{"name": "X", "id": "...", "weight": N}], ...}
    - Higher weight = more important, prefer that node
    
    ---
    ## OUTPUT
    Return list of Cypher queries as JSON array of strings.
    Order: MERGE commands first, then JournalEntry + entities.
    """,
    tools=[],
)
