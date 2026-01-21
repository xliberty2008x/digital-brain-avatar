#!/usr/bin/env python3
"""Test Core Entity query using project's MCP client with expanded strategy."""
import asyncio
import sys
import os

# Додаємо шлях до проекту
sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv
load_dotenv('.env')

from digital_brain.tools.mcp_client import execute_cypher

async def test():
    # Розширена кверя: всі Person, Organization, Topic + інші з >= 3 зв'язками
    query = """
    MATCH (n)
    WHERE n.name IS NOT NULL
      AND NOT 'JournalEntry' IN labels(n)
      AND NOT 'Alias' IN labels(n)
      AND NOT 'LearningLog' IN labels(n)
      AND (
        any(label IN labels(n) WHERE label IN ['Person', 'Organization'])
        OR COUNT { (n)--() } >= 3
      )
    RETURN DISTINCT
        labels(n) AS labels,
        n.name AS name,
        coalesce(n.id, 'MISSING') AS id,
        COUNT { (n)--() } AS weight
    ORDER BY weight DESC
    LIMIT 200
    """
    
    print("=== Testing Expanded Entity Query (ALL Key Types + Heavy Nodes) ===")
    
    try:
        results = await execute_cypher(query)
        print(f"\nReturned {len(results)} results:\n")
        
        if not results:
            print("NO ENTITIES FOUND matching criteria!")
        else:
            print(f"{'Label':<15} | {'Name':<30} | {'Weight':<6} | {'ID'}")
            print("-" * 80)
            for r in results:
                label = r.get('labels', ['?'])[0]
                name = r.get('name', '?')
                weight = r.get('weight', 0)
                id_ = r.get('id', '?')
                print(f"{label:<15} | {name:<30} | {weight:<6} | {id_}")
                
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test())
