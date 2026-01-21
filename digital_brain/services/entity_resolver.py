# File: digital_brain/services/entity_resolver.py
"""
Deterministic Entity Resolution Service.
Uses PRD schema contract to check if entities already exist in Neo4j.
"""

from typing import Any
from ..tools.mcp_client import execute_cypher


async def resolve_entities(entity_output: dict) -> dict[str, Any]:
    """
    Check which entities from entity_output already exist in Neo4j.
    Uses PRD schema contract for type-specific lookups.
    Also checks Alias nodes for learned mappings from past merges.
    
    Args:
        entity_output: Output from entity_extractor agent (EntityOutput schema)
    
    Returns:
        {
            "existing_entities": [{"id": "...", "name": "...", "type": "Person"}],
            "new_entities": [{"name": "Оливія", "type": "Person"}]
        }
    """
    existing = []
    new_entities = []
    
    # EntityOutput structure: entries[] -> JournalEntryExtraction -> entities[] -> Entity
    entries = entity_output.get("entries", [])
    if not entries:
        print("⚠️ Entity resolver: No entries found in entity_output")
        return {"existing_entities": [], "new_entities": []}
    
    # Collect all entities from all entries
    all_entities = []
    for entry in entries:
        entry_entities = entry.get("entities", [])
        all_entities.extend(entry_entities)
    
    print(f"📊 Entity resolver: Found {len(all_entities)} entities across {len(entries)} entries")
    
    if not all_entities:
        return {"existing_entities": [], "new_entities": []}
    
    for entity in all_entities:
        entity_type = entity.get("type", "")
        entity_name = entity.get("name", "")
        
        if not entity_name:
            continue
        
        # STEP 0: Check Alias nodes first (learned from past merges)
        alias_query = """
        MATCH (a:Alias)
        WHERE toLower(a.from_name) = toLower($name)
        RETURN a.canonical_id AS id, a.to_name AS name
        LIMIT 1
        """
        try:
            alias_results = await execute_cypher(alias_query, {"name": entity_name})
            if alias_results and len(alias_results) > 0:
                existing.append({
                    "id": alias_results[0].get("id"),
                    "name": alias_results[0].get("name"),
                    "type": entity_type,
                    "original_query": entity_name,
                    "source": "alias"  # Mark as learned mapping
                })
                print(f"🧠 LEARNED: '{entity_name}' → '{alias_results[0].get('name')}' (from Alias)")
                continue
        except Exception as e:
            print(f"Alias lookup failed: {e}")
        
        # STEP 1: Build type-specific query based on PRD schema contract
        query = None
        params = {"name": entity_name}
        
        if entity_type == "Person":
            # Person: use CASE to handle both string and array name properties
            query = """
            MATCH (p:Person) 
            WHERE CASE 
                WHEN p.name IS :: LIST<STRING> THEN ANY(n IN p.name WHERE toLower(n) CONTAINS toLower($name))
                ELSE toLower(p.name) CONTAINS toLower($name)
            END
            RETURN coalesce(p.id, 'MISSING') AS id, 
                   CASE WHEN p.name IS :: LIST<STRING> THEN p.name[0] ELSE p.name END AS name, 
                   'Person' AS type
            LIMIT 1
            """
        elif entity_type == "Topic":
            query = """
            MATCH (t:Topic) WHERE toLower(t.name) = toLower($name)
            RETURN coalesce(t.id, 'MISSING') AS id, t.name AS name, 'Topic' AS type
            LIMIT 1
            """
        elif entity_type == "State":
            query = """
            MATCH (s:State) WHERE toLower(s.name) = toLower($name)
            RETURN coalesce(s.id, 'MISSING') AS id, s.name AS name, 'State' AS type
            LIMIT 1
            """
        elif entity_type == "Event":
            # Event: lookup by type (not name)
            event_type = entity.get("event_type", entity_name)
            query = """
            MATCH (e:Event) WHERE toLower(e.type) = toLower($name)
            RETURN coalesce(e.id, 'MISSING') AS id, e.type AS name, 'Event' AS type
            LIMIT 1
            """
        else:
            # Generic fallback for any other node type
            query = f"""
            MATCH (n:{entity_type}) WHERE toLower(n.name) = toLower($name)
            RETURN coalesce(n.id, 'MISSING') AS id, n.name AS name, '{entity_type}' AS type
            LIMIT 1
            """
        
        try:
            results = await execute_cypher(query, params)
            if results and len(results) > 0:
                existing.append({
                    "id": results[0].get("id"),
                    "name": results[0].get("name"),
                    "type": results[0].get("type"),
                    "original_query": entity_name  # What user called it
                })
            else:
                new_entities.append({
                    "name": entity_name,
                    "type": entity_type
                })
        except Exception as e:
            print(f"Entity lookup failed for {entity_name}: {e}")
            # On error, assume it's new to avoid blocking
            new_entities.append({
                "name": entity_name,
                "type": entity_type
            })
    
    return {
        "existing_entities": existing,
        "new_entities": new_entities
    }
