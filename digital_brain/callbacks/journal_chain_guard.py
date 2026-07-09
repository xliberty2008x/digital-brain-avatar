"""
Deterministic guard for JournalEntry write queries.

Blocks write_neo4j_cypher calls when JournalEntry creation does not include:
1) explicit JournalEntry id (string literal or non-empty $param)
2) chain link to the previous JournalEntry (FOLLOWS/NEXT_ENTRY/PRECEDED_BY/NEXT),
   when a previous id is known in state.

Chain head (`last_journal_entry_id`) is advanced only in the after-tool callback
when the write reports success, never optimistically before the tool runs.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext


CHAIN_REL_TYPES = ("FOLLOWS", "NEXT_ENTRY", "PRECEDED_BY", "NEXT")
_SPAN_STOP_KEYWORDS = (
    r"CREATE|MERGE|MATCH|WITH|RETURN|WHERE|SET|DELETE|DETACH|REMOVE|UNWIND|CALL|"
    r"FOREACH|FOR|INDEX|CONSTRAINT"
)
_ALIAS_RE = re.compile(
    rf"(?is)\b(?:CREATE|MERGE)\b(?:(?!\b(?:{_SPAN_STOP_KEYWORDS})\b).)*?"
    r"\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*JournalEntry\b"
)
_PENDING_CREATED_ID_KEY = "_pending_journal_entry_id"


def _error_response(message: str) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def _extract_created_journal_aliases(query: str) -> list[str]:
    """Return aliases of JournalEntry nodes created/merged, including path-chained forms."""
    return _ALIAS_RE.findall(query or "")


def _extract_created_journal_id(query: str, alias: str, params: dict[str, Any]) -> Optional[str]:
    """
    Extract a concrete JournalEntry id for `alias`.

    Accepts only:
    - quoted string literals: id: "j-1" / id: 'j-1'
    - $params whose value is present and non-empty

    Rejects bare identifiers and function calls (e.g. randomUUID()).
    """
    block_pattern = (
        rf"(?is)\(\s*{re.escape(alias)}\s*:\s*JournalEntry\s*\{{(.*?)\}}\s*\)"
    )
    block_match = re.search(block_pattern, query)
    if not block_match:
        return None

    props = block_match.group(1)
    id_match = re.search(
        r"(?is)\bid\s*:\s*(\$[A-Za-z_][A-Za-z0-9_]*|\"[^\"]*\"|'[^']*')",
        props,
    )
    if not id_match:
        return None

    raw_id = id_match.group(1).strip()
    if raw_id.startswith("$"):
        param_name = raw_id[1:]
        value = params.get(param_name)
        if value is None:
            return None
        text = str(value).strip()
        return text or None
    if raw_id.startswith('"') and raw_id.endswith('"'):
        text = raw_id[1:-1].strip()
        return text or None
    if raw_id.startswith("'") and raw_id.endswith("'"):
        text = raw_id[1:-1].strip()
        return text or None
    return None


def _has_chain_relationship_for_alias(query: str, alias: str) -> bool:
    rels = "|".join(CHAIN_REL_TYPES)
    out_pattern = (
        rf"(?is)\(\s*{re.escape(alias)}\s*\)\s*-\s*\[[^\]]*:(?:{rels})\b[^\]]*\]\s*->\s*\("
    )
    in_pattern = (
        rf"(?is)\(\s*[A-Za-z_][A-Za-z0-9_]*\s*\)\s*-\s*\[[^\]]*:(?:{rels})\b[^\]]*\]\s*->"
        rf"\s*\(\s*{re.escape(alias)}\s*\)"
    )
    return bool(re.search(out_pattern, query) or re.search(in_pattern, query))


def _query_references_prev_id(query: str, params: dict[str, Any], prev_id: str) -> bool:
    # Quoted literal occurrence
    if re.search(rf"(?is)['\"]{re.escape(prev_id)}['\"]", query):
        return True
    for key, value in params.items():
        if str(value) == str(prev_id) and re.search(rf"\${re.escape(key)}\b", query):
            return True
    return False


def _tool_response_is_error(tool_response: Any) -> bool:
    if tool_response is None:
        return True
    if isinstance(tool_response, dict):
        if tool_response.get("isError") is True:
            return True
        # MCP / ADK error shapes
        err = tool_response.get("error")
        if err:
            return True
        text_bits: list[str] = []
        content = tool_response.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    text_bits.append(item["text"])
        blob = " ".join(text_bits) if text_bits else str(tool_response)
        lowered = blob.lower()
        if "valueerror" in lowered or "journal chain guard" in lowered:
            return True
        if "must pass embed_text" in lowered or "embedding dimension" in lowered:
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
    # Clear any stale pending id from a previous attempt.
    state.pop(_PENDING_CREATED_ID_KEY, None)

    created_ids: list[str] = []
    for alias in created_aliases:
        created_id = _extract_created_journal_id(query, alias, params)
        if not created_id:
            return _error_response(
                "❌ JOURNAL CHAIN GUARD: JournalEntry CREATE/MERGE must include explicit `id` "
                f"(alias `{alias}`) as a string literal or non-empty `$param` "
                "(e.g. `id: $journal_id`). Function calls like `randomUUID()` are not accepted."
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
                    "MATCH (prev:JournalEntry {id: $prev_id})\n"
                    "CREATE (j:JournalEntry {id: $journal_id, content: $content, "
                    "timestamp: $timestamp, embedding: $embedding})\n"
                    "MERGE (j)-[:FOLLOWS]->(prev)"
                )

    # Stash for after-tool advance; do not mutate last_journal_entry_id yet.
    if created_ids:
        state[_PENDING_CREATED_ID_KEY] = created_ids[-1]

    return None


async def journal_chain_guard_after_tool_callback(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Any,
) -> Optional[Dict[str, Any]]:
    """Advance chain head only after a successful JournalEntry write."""
    if tool.name != "write_neo4j_cypher":
        return None

    state = tool_context.state
    pending = state.pop(_PENDING_CREATED_ID_KEY, None)
    if not pending:
        return None

    if _tool_response_is_error(tool_response):
        return None

    state["last_journal_entry_id"] = pending
    return None
