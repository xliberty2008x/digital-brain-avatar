"""
Deterministic guard for JournalEntry write queries.

Blocks write_neo4j_cypher calls when JournalEntry creation does not include:
1) explicit JournalEntry id
2) chain link to the previous JournalEntry (FOLLOWS/NEXT_ENTRY/PRECEDED_BY/NEXT),
   when a previous id is known in state.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext


CHAIN_REL_TYPES = ("FOLLOWS", "NEXT_ENTRY", "PRECEDED_BY", "NEXT")


def _error_response(message: str) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def _extract_created_journal_aliases(query: str) -> list[str]:
    pattern = r"(?is)\b(?:CREATE|MERGE)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*JournalEntry\b"
    return re.findall(pattern, query)


def _extract_created_journal_id(query: str, alias: str, params: dict[str, Any]) -> Optional[str]:
    # Capture the property map for CREATE/MERGE (alias:JournalEntry { ... })
    block_pattern = (
        rf"(?is)\b(?:CREATE|MERGE)\s*\(\s*{re.escape(alias)}\s*:\s*JournalEntry\s*\{{(.*?)\}}\s*\)"
    )
    block_match = re.search(block_pattern, query)
    if not block_match:
        return None

    props = block_match.group(1)
    id_match = re.search(r"(?is)\bid\s*:\s*([A-Za-z_][A-Za-z0-9_]*|\$[A-Za-z_][A-Za-z0-9_]*|\"[^\"]*\"|'[^']*')", props)
    if not id_match:
        return None

    raw_id = id_match.group(1).strip()
    if raw_id.startswith("$"):
        param_name = raw_id[1:]
        value = params.get(param_name)
        return str(value) if value is not None else None
    if raw_id.startswith('"') and raw_id.endswith('"'):
        return raw_id[1:-1]
    if raw_id.startswith("'") and raw_id.endswith("'"):
        return raw_id[1:-1]
    # Bare identifier value (rare). Accept as-is.
    return raw_id


def _has_chain_relationship_for_alias(query: str, alias: str) -> bool:
    rels = "|".join(CHAIN_REL_TYPES)
    # alias as source
    out_pattern = rf"(?is)\(\s*{re.escape(alias)}\s*\)\s*-\s*\[[^\]]*:(?:{rels})\b[^\]]*\]\s*->\s*\("
    # alias as target
    in_pattern = rf"(?is)\(\s*[A-Za-z_][A-Za-z0-9_]*\s*\)\s*-\s*\[[^\]]*:(?:{rels})\b[^\]]*\]\s*->\s*\(\s*{re.escape(alias)}\s*\)"
    return bool(re.search(out_pattern, query) or re.search(in_pattern, query))


def _query_references_prev_id(query: str, params: dict[str, Any], prev_id: str) -> bool:
    if prev_id in query:
        return True
    for key, value in params.items():
        if str(value) == str(prev_id) and f"${key}" in query:
            return True
    return False


async def journal_chain_guard_before_tool_callback(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
) -> Optional[Dict[str, Any]]:
    """
    Deterministic guard for JournalEntry writes.

    Returns:
    - None: query is accepted unchanged
    - Error response dict (isError=True): block unsafe/non-chain JournalEntry writes
    """
    if tool.name != "write_neo4j_cypher":
        return None

    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return None

    created_aliases = _extract_created_journal_aliases(query)
    if not created_aliases:
        return None

    params = args.get("params")
    if not isinstance(params, dict):
        params = {}

    state = tool_context.state
    prev_id = state.get("last_journal_entry_id")

    created_ids: list[str] = []
    for alias in created_aliases:
        created_id = _extract_created_journal_id(query, alias, params)
        if not created_id:
            return _error_response(
                "❌ JOURNAL CHAIN GUARD: JournalEntry CREATE/MERGE must include explicit `id` "
                f"(alias `{alias}`). Retry with `id: randomUUID()` and reuse that id for chain linking."
            )
        created_ids.append(created_id)

        if prev_id:
            has_alias_chain = _has_chain_relationship_for_alias(query, alias)
            references_prev = _query_references_prev_id(query, params, str(prev_id))
            if not has_alias_chain or not references_prev:
                return _error_response(
                    "❌ JOURNAL CHAIN GUARD: Missing deterministic JournalEntry chain link.\n"
                    f"Expected: new entry `{alias}` must be linked to previous entry id `{prev_id}` "
                    "using one of: FOLLOWS/NEXT_ENTRY/PRECEDED_BY/NEXT.\n"
                    "Example:\n"
                    "MATCH (prev:JournalEntry {id: \"<prev_id>\"})\n"
                    "CREATE (j:JournalEntry {id: randomUUID(), ...})\n"
                    "MERGE (j)-[:FOLLOWS]->(prev)"
                )

    # Advance chain head for subsequent write calls in the same run.
    if created_ids:
        state["last_journal_entry_id"] = created_ids[-1]

    return None

