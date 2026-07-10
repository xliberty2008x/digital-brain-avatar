#!/usr/bin/env python3
"""Bootstrap the server-owned primary JournalChain from a reviewed legacy tip.

Run ``audit_journal_integrity.py`` first when bootstrapping an existing graph.
This script never deletes or rewrites legacy ``FOLLOWS`` edges; it only adds
the independent ``HEAD`` link required by the new append protocol. ``--empty``
exists for a new graph or isolated test stack only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_brain.tools.mcp_client import call_mcp_tool, execute_cypher


CHAIN_KEY = "primary"


def _decode_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    content = result.get("content") if isinstance(result, dict) else None
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        raise RuntimeError(f"Unexpected MCP result: {result}")
    text = content[0].get("text")
    if not isinstance(text, str):
        raise RuntimeError(f"Unexpected MCP text result: {result}")
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        raise RuntimeError(f"Expected MCP object result, got: {decoded!r}")
    return decoded


async def preview(head_element_id: str | None) -> dict[str, Any]:
    if head_element_id is None:
        journal_count = await execute_cypher(
            "MATCH (entry:JournalEntry) RETURN count(entry) AS count"
        )
        if journal_count and int(journal_count[0].get("count") or 0) > 0:
            raise RuntimeError(
                "--empty is allowed only when the graph contains no JournalEntry nodes; "
                "run the integrity audit and pass --head-element-id instead"
            )
        chain = await execute_cypher(
            """
            OPTIONAL MATCH (chain:JournalChain {key: $key})-[:HEAD]->(head:JournalEntry)
            RETURN chain.key AS key, chain.version AS version,
                   collect(elementId(head)) AS head_element_ids
            """,
            {"key": CHAIN_KEY},
        )
        return {
            "selected_head": None,
            "existing_chain": chain[0] if chain else {},
            "empty_chain": True,
        }
    rows = await execute_cypher(
        """
        MATCH (head:JournalEntry)
        WHERE elementId(head) = $head_element_id
        RETURN elementId(head) AS element_id, head.id AS id,
               coalesce(head.timestamp, head.entry_date, head.created_at) AS timestamp
        """,
        {"head_element_id": head_element_id},
    )
    if not rows:
        raise RuntimeError("No JournalEntry matches --head-element-id")
    chain = await execute_cypher(
        """
        OPTIONAL MATCH (chain:JournalChain {key: $key})-[:HEAD]->(head:JournalEntry)
        RETURN chain.key AS key, chain.version AS version,
               collect(elementId(head)) AS head_element_ids
        """,
        {"key": CHAIN_KEY},
    )
    return {"selected_head": rows[0], "existing_chain": chain[0] if chain else {}}


async def apply(head_element_id: str | None) -> dict[str, Any]:
    if head_element_id is None:
        existing_entries = await execute_cypher(
            "MATCH (entry:JournalEntry) RETURN count(entry) AS count"
        )
        if existing_entries and int(existing_entries[0].get("count") or 0) > 0:
            raise RuntimeError(
                "Refusing --empty bootstrap because JournalEntry nodes already exist; "
                "select an audited --head-element-id"
            )
    result = await call_mcp_tool(
        "bootstrap_journal_chain",
        {"head_element_id": head_element_id, "empty": head_element_id is None},
    )
    return _decode_tool_result(result)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--head-element-id", help="Reviewed candidate from audit output")
    source.add_argument(
        "--empty",
        action="store_true",
        help="Initialize an empty primary chain (only for a new graph or isolated test stack)",
    )
    parser.add_argument("--apply", action="store_true", help="Create constraints and the primary HEAD link")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    head_element_id = None if args.empty else args.head_element_id
    result = asyncio.run(preview(head_element_id))
    if not args.apply:
        print(json.dumps({"preview": result, "apply_required": True}, ensure_ascii=False, indent=2, default=str))
        return
    result = asyncio.run(apply(head_element_id))
    if result.get("outcome") != "bootstrapped":
        raise RuntimeError(f"Bootstrap was not applied: {result}")
    print(json.dumps({"bootstrapped": result}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
