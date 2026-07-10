"""Exercise the production JournalEntry MCP append protocol end-to-end.

The isolated Compose runner invokes this only after its empty JournalChain has
been bootstrapped through the MCP bootstrap CLI.  It deliberately never opens
a Bolt connection: all graph observations and mutations go through MCP tools.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from digital_brain.tools.mcp_client import (
    append_journal_entry,
    execute_cypher,
    get_journal_append_receipt,
    get_journal_chain_head,
)


def _status(payload: dict[str, Any]) -> str:
    """Read the stable outcome name without coupling to envelope metadata."""
    for candidate in (payload, payload.get("receipt"), payload.get("append")):
        if isinstance(candidate, dict):
            value = candidate.get("status") or candidate.get("outcome")
            if isinstance(value, str):
                return value
    raise AssertionError(f"Append result has no status: {payload!r}")


def _version(head: dict[str, Any]) -> int:
    value = head.get("version")
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssertionError(f"Chain head has no integer version: {head!r}")
    return value


def _assert_status(payload: dict[str, Any], expected: str) -> None:
    actual = _status(payload)
    if actual != expected:
        raise AssertionError(f"Expected {expected!r}, got {actual!r}: {payload!r}")


async def _append(append_key: str, expected_version: int, content: str) -> dict[str, Any]:
    return await append_journal_entry(
        append_key=append_key,
        content=content,
        timestamp="2026-07-09T00:00:00Z",
        mood="journal-e2e",
        expected_version=expected_version,
        properties={"source": "isolated-journal-e2e"},
    )


async def main() -> None:
    initial_head = await get_journal_chain_head()
    initial_version = _version(initial_head)

    first_key = str(uuid.uuid4())
    first = await _append(first_key, initial_version, "journal e2e first append")
    _assert_status(first, "created")

    second_expected_version = _version(await get_journal_chain_head())
    second_key = str(uuid.uuid4())
    second = await _append(second_key, second_expected_version, "journal e2e second append")
    _assert_status(second, "created")

    # Reusing the same key and fingerprint must return the original result
    # without inserting a second JournalEntry, even though the head advanced.
    replay = await _append(second_key, second_expected_version, "journal e2e second append")
    _assert_status(replay, "replayed")

    receipt = await get_journal_append_receipt(second_key)
    if second_key not in json.dumps(receipt, sort_keys=True):
        raise AssertionError(f"Receipt does not identify the replayed append key: {receipt!r}")

    concurrent_expected_version = _version(await get_journal_chain_head())
    concurrent_keys = [str(uuid.uuid4()), str(uuid.uuid4())]
    concurrent_results = await asyncio.gather(
        _append(concurrent_keys[0], concurrent_expected_version, "journal e2e concurrent one"),
        _append(concurrent_keys[1], concurrent_expected_version, "journal e2e concurrent two"),
    )
    concurrent_statuses = sorted(_status(result) for result in concurrent_results)
    if concurrent_statuses != ["conflict", "created"]:
        raise AssertionError(f"Concurrent append outcomes must be created/conflict: {concurrent_results!r}")

    verification_rows = await execute_cypher(
        """
        MATCH (chain:JournalChain {key: 'primary'})
        OPTIONAL MATCH (chain)-[:HEAD]->(head:JournalEntry)
        OPTIONAL MATCH (head)-[:FOLLOWS]->(previous:JournalEntry)
        RETURN count(head) AS head_count,
               count(previous) AS follows_count,
               size(head.embedding) AS embedding_dimensions,
               chain.version AS version
        """
    )
    if len(verification_rows) != 1:
        raise AssertionError(f"Expected one chain verification row: {verification_rows!r}")
    verification = verification_rows[0]
    expected_chain_version = initial_version + 3
    if verification != {
        "head_count": 1,
        "follows_count": 1,
        "embedding_dimensions": 1024,
        "version": expected_chain_version,
    }:
        raise AssertionError(f"Unexpected chain verification: {verification!r}")

    append_nodes = await execute_cypher(
        """
        MATCH (journal:JournalEntry)
        WHERE journal.append_key IN $append_keys
        RETURN count(journal) AS count
        """,
        {"append_keys": [first_key, second_key, *concurrent_keys]},
    )
    if append_nodes != [{"count": 3}]:
        raise AssertionError(f"Replay or conflict created an unexpected node: {append_nodes!r}")

    print(
        json.dumps(
            {
                "result": "passed",
                "chain_version": expected_chain_version,
                "embedding_dimensions": 1024,
                "concurrent_statuses": concurrent_statuses,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
