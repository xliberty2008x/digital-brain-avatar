#!/usr/bin/env python
"""Recreate the JournalEntry vector index for the selected embedding model."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_brain.tools.mcp_client import call_mcp_tool


async def recreate_index(dimensions: int, similarity: str) -> None:
    drop_query = "DROP INDEX journal_entry_embedding_index IF EXISTS"
    create_query = f"""
    CREATE VECTOR INDEX journal_entry_embedding_index IF NOT EXISTS
    FOR (j:JournalEntry) ON (j.embedding)
    OPTIONS {{
      indexConfig: {{
        `vector.dimensions`: {dimensions},
        `vector.similarity_function`: '{similarity}'
      }}
    }}
    """
    await call_mcp_tool("write_neo4j_cypher", {"query": drop_query})
    await call_mcp_tool("write_neo4j_cypher", {"query": create_query})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dimensions", type=int, required=True, help="Embedding vector dimension")
    parser.add_argument("--similarity", default="cosine", choices=("cosine", "euclidean"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(recreate_index(args.dimensions, args.similarity))
    print(
        "Recreated journal_entry_embedding_index "
        f"with dimensions={args.dimensions} similarity={args.similarity}"
    )


if __name__ == "__main__":
    main()
