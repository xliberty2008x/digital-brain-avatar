# File: digital_brain/services/entity_resolver.py
"""
Deterministic Entity Resolution Service.
Uses PRD schema contract to check if entities already exist in Neo4j.

Alias lookup is scoped (namespace + entity type + normalized source),
active-revision-aware, deterministic, and direct-to-canonical only.
"""

from __future__ import annotations

from typing import Any

from ..tools.mcp_client import execute_cypher

DEFAULT_ALIAS_NAMESPACE = "life"


def normalize_lookup_name(name: str) -> str:
    """Match digital_brain.maintenance.alias_effects.normalize_alias_source."""
    import re

    collapsed = re.sub(r"\s+", " ", str(name).strip())
    return collapsed.casefold()


# Active scoped Alias → canonical entity. Rejects Alias→Alias by excluding
# targets that are themselves Alias nodes. Highest revision wins; id ASC tiebreak.
SCOPED_ACTIVE_ALIAS_QUERY = """
MATCH (a:Alias)
WHERE coalesce(a.status, 'active') = 'active'
  AND coalesce(a.namespace, $namespace) = $namespace
  AND (
    a.entity_type IS NULL OR a.entity_type = $entity_type
  )
  AND (
    a.normalized_from = $normalized
    OR (
      a.normalized_from IS NULL
      AND toLower(coalesce(a.from_name, a.display_from, '')) = toLower($name)
    )
  )
  AND a.canonical_id IS NOT NULL
  AND NOT EXISTS {
    MATCH (bad:Alias {id: a.canonical_id})
  }
RETURN a.canonical_id AS id,
       coalesce(a.canonical_name, a.to_name) AS name,
       coalesce(a.revision, 0) AS revision,
       a.id AS alias_id,
       a.entity_type AS alias_entity_type
ORDER BY coalesce(a.revision, 0) DESC, a.id ASC
LIMIT 1
"""


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
            "new_entities": [{"name": "Olivia", "type": "Person"}]
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

        # STEP 0: Scoped active Alias (learned mappings) — direct-to-canonical
        try:
            normalized = normalize_lookup_name(entity_name)
            alias_results = await execute_cypher(
                SCOPED_ACTIVE_ALIAS_QUERY,
                {
                    "name": entity_name,
                    "normalized": normalized,
                    "namespace": DEFAULT_ALIAS_NAMESPACE,
                    "entity_type": entity_type or "",
                },
            )
            if alias_results and len(alias_results) > 0:
                hit = alias_results[0]
                resolved_type = entity_type or hit.get("alias_entity_type") or ""
                existing.append(
                    {
                        "id": hit.get("id"),
                        "name": hit.get("name"),
                        "type": resolved_type,
                        "original_query": entity_name,
                        "source": "alias",
                        "alias_id": hit.get("alias_id"),
                        "alias_revision": hit.get("revision"),
                    }
                )
                print(
                    f"🧠 LEARNED: '{entity_name}' → '{hit.get('name')}' "
                    f"(from Alias rev={hit.get('revision')})"
                )
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
            params = {"name": event_type}
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
                existing.append(
                    {
                        "id": results[0].get("id"),
                        "name": results[0].get("name"),
                        "type": results[0].get("type"),
                        "original_query": entity_name,  # What user called it
                    }
                )
            else:
                new_entities.append({"name": entity_name, "type": entity_type})
        except Exception as e:
            print(f"Entity lookup failed for {entity_name}: {e}")
            # On error, assume it's new to avoid blocking
            new_entities.append({"name": entity_name, "type": entity_type})

    return {"existing_entities": existing, "new_entities": new_entities}
