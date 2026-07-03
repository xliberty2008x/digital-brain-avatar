#!/usr/bin/env python
"""Run fixed semantic-search probes against local JournalEntry embeddings."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_brain.tools.mcp_client import execute_cypher


PROBES = {
    "father_family": "конфлікт з батьком сім'я не спілкуюсь з отцем",
    "epam_work": "EPAM робота офер онбординг проект інтерв'ю",
    "swimming": "плавання тренування змагання басейн тренер",
    "digital_brain": "Digital Brain Neo4j цифровий аватар щоденник",
    "ai_dependency": "страх залежності від ChatGPT психолог штучний інтелект",
}


async def run_probe(name: str, text: str, limit: int) -> dict:
    query = """
    CALL db.index.vector.queryNodes('journal_entry_embedding_index', $limit, $embedding)
    YIELD node, score
    RETURN coalesce(node.id, elementId(node)) AS id,
           coalesce(toString(node.entry_date), toString(node.timestamp), toString(node.created_at), '') AS date,
           left(coalesce(node.content, node.raw_text, ''), 220) AS preview,
           score
    ORDER BY score DESC
    """
    rows = await execute_cypher(query, {"limit": limit}, embed_text=text)
    return {"probe": name, "query": text, "results": rows}


async def run_all(limit: int) -> list[dict]:
    return await asyncio.gather(*(run_probe(name, text, limit) for name, text in PROBES.items()))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5)
    return parser.parse_args()


def main() -> None:
    results = asyncio.run(run_all(parse_args().limit))
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
