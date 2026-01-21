import asyncio
import sys
import os

# Simulate the mcp_client logic directly
import urllib.request
import json
import ssl

URL = "https://mcp-neo4j-cypher-858161250402.us-central1.run.app/api/mcp/"

def test_entity_lookup():
    print("🔍 Testing entity lookup for 'Кира'...")
    
    # Use ANY() to safely handle arrays
    cypher_query = """
    MATCH (p:Person) 
    WHERE CASE 
        WHEN p.name IS :: LIST<STRING> THEN ANY(n IN p.name WHERE toLower(n) CONTAINS toLower($name))
        ELSE toLower(p.name) CONTAINS toLower($name)
    END
    RETURN p.id AS id, 
           CASE WHEN p.name IS :: LIST<STRING> THEN p.name[0] ELSE p.name END AS name, 
           'Person' AS type
    LIMIT 1
    """
    
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "read_neo4j_cypher",
            "arguments": {
                "query": cypher_query,
                "params": {"name": "Кира"}  # Dict, not JSON string!
            }
        }
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(URL, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    
    ctx = ssl.create_default_context()
    
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
            raw_body = response.read().decode('utf-8')
            print(f"📦 Raw Response:\n{raw_body}\n")
            
            # Parse SSE
            for line in raw_body.splitlines():
                if line.strip().startswith("data:"):
                    data_str = line.strip()[5:].strip()
                    parsed = json.loads(data_str)
                    print(f"📊 Parsed result: {json.dumps(parsed, indent=2, ensure_ascii=False)}")
                    
                    # Check structure
                    if "result" in parsed:
                        result = parsed["result"]
                        print(f"🎯 Result content: {result}")
                        if "content" in result:
                            for item in result["content"]:
                                if item.get("type") == "text":
                                    print(f"📝 Text content: {item.get('text')}")
                    break
                    
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_entity_lookup()
