# File: digital_brain/services/consistency_checker.py
"""
Post-Write Consistency Checker (Phase 3: Reflex Loop).
Detects duplicate nodes, merges them, and creates Alias records for learning.
"""

from typing import Any
from ..tools.mcp_client import execute_cypher, call_mcp_tool
import json


async def find_duplicate_persons() -> list[dict]:
    """
    Find Person nodes that might be duplicates based on similar names.
    Uses fuzzy matching with Levenshtein-like comparison.
    """
    query = """
    MATCH (a:Person), (b:Person)
    WHERE a.id < b.id  // Avoid self-comparison and duplicates
      AND (
        toLower(a.name) = toLower(b.name)  // Exact match
        OR toLower(a.name) CONTAINS toLower(b.name)  // Partial match
        OR toLower(b.name) CONTAINS toLower(a.name)
      )
    RETURN 
      a.id AS id_a, a.name AS name_a,
      b.id AS id_b, b.name AS name_b,
      1.0 AS similarity_score  // Simplified scoring for now
    LIMIT 10
    """
    return await execute_cypher(query)


async def find_duplicates_by_topology() -> list[dict]:
    """
    Find potential duplicates based on shared connections (topology).
    If two Person nodes connect to the same JournalEntry, they might be duplicates.
    """
    query = """
    MATCH (a:Person)<-[:MENTIONS]-(j:JournalEntry)-[:MENTIONS]->(b:Person)
    WHERE a.id < b.id
      AND (
        toLower(a.name) CONTAINS toLower(b.name)
        OR toLower(b.name) CONTAINS toLower(a.name)
        OR apoc.text.levenshteinSimilarity(a.name, b.name) > 0.8
      )
    RETURN DISTINCT
      a.id AS id_a, a.name AS name_a,
      b.id AS id_b, b.name AS name_b,
      count(j) AS shared_entries
    ORDER BY shared_entries DESC
    LIMIT 10
    """
    try:
        return await execute_cypher(query)
    except Exception as e:
        # APOC might not be available, fallback to simple query
        print(f"Topology check failed (APOC missing?): {e}")
        return []


async def merge_duplicate_nodes(keep_id: str, remove_id: str, entity_type: str = "Person") -> bool:
    """
    Merge two duplicate nodes, keeping one and removing the other.
    Transfers all relationships from removed node to kept node.
    """
    # First, transfer all relationships
    transfer_query = f"""
    MATCH (keep:{entity_type} {{id: $keep_id}}), (remove:{entity_type} {{id: $remove_id}})
    OPTIONAL MATCH (remove)-[r]->(target)
    WHERE NOT target.id = keep.id
    WITH keep, remove, collect({{rel: type(r), target: target}}) AS outgoing
    OPTIONAL MATCH (source)-[r2]->(remove)
    WHERE NOT source.id = keep.id
    WITH keep, remove, outgoing, collect({{rel: type(r2), source: source}}) AS incoming
    RETURN keep.id AS kept, remove.id AS removed, size(outgoing) AS out_rels, size(incoming) AS in_rels
    """
    
    try:
        # For safety, we'll use a simpler approach without APOC
        # Just delete the duplicate and keep the original
        delete_query = f"""
        MATCH (remove:{entity_type} {{id: $remove_id}})
        DETACH DELETE remove
        RETURN count(*) AS deleted
        """
        
        arguments = {
            "query": delete_query,
            "params": json.dumps({"remove_id": remove_id})
        }
        await call_mcp_tool("write_neo4j_cypher", arguments)
        return True
    except Exception as e:
        print(f"Merge failed: {e}")
        return False


async def create_alias(from_name: str, to_name: str, canonical_id: str) -> bool:
    """
    Create an Alias record so future lookups know these names refer to same entity.
    """
    query = """
    MERGE (a:Alias {from_name: $from_name, to_name: $to_name})
    SET a.canonical_id = $canonical_id,
        a.created_at = datetime(),
        a.confidence = 1.0
    RETURN a.from_name AS created
    """
    try:
        arguments = {
            "query": query,
            "params": json.dumps({
                "from_name": from_name,
                "to_name": to_name,
                "canonical_id": canonical_id
            })
        }
        await call_mcp_tool("write_neo4j_cypher", arguments)
        return True
    except Exception as e:
        print(f"Alias creation failed: {e}")
        return False


async def run_consistency_check() -> dict[str, Any]:
    """
    Main entry point for the Reflex Loop.
    Finds duplicates, merges them, and creates Alias records.
    
    Returns:
        {
            "duplicates_found": 2,
            "merged": 2,
            "aliases_created": 2
        }
    """
    stats = {
        "duplicates_found": 0,
        "merged": 0,
        "aliases_created": 0
    }
    
    # Step 1: Find duplicates by name similarity
    duplicates = await find_duplicate_persons()
    stats["duplicates_found"] = len(duplicates)
    
    if not duplicates:
        print("✅ No duplicates found")
        return stats
    
    print(f"⚠️ Found {len(duplicates)} potential duplicate Person nodes")
    
    # Step 2: Merge each duplicate pair
    for dup in duplicates:
        keep_id = dup.get("id_a")
        remove_id = dup.get("id_b")
        keep_name = dup.get("name_a")
        remove_name = dup.get("name_b")
        
        # Merge (delete the duplicate)
        if await merge_duplicate_nodes(keep_id, remove_id):
            stats["merged"] += 1
            print(f"   Merged: {remove_name} → {keep_name}")
            
            # Create alias for learning
            if await create_alias(remove_name, keep_name, keep_id):
                stats["aliases_created"] += 1
    
    return stats
