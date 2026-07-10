#!/usr/bin/env python3
"""Pure initiation status derivation for digital-brain-buddy.

Agents and tests share these rules. Evidence is collected from BOOTSTRAP + SOUL;
this module does not call Neo4j.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

# Graph is "thin" when still at meeting-1 minimum density.
THIN_MAX_NON_SELF_PERSONS = 1
THIN_MAX_TOPICS = 1

# Ordered stages: first failed predicate wins.
_STAGES: tuple[tuple[str, str, str], ...] = (
    # status_key, evidence_flag, next_step
    ("missing_language", "has_language", "language"),
    ("missing_self", "has_self", "self"),
    ("missing_anchor_person", "has_anchor_person", "anchor_person"),
    ("missing_focus", "has_focus", "focus"),
    ("missing_soul_overlay", "has_soul_overlay_beyond_language", "soul_overlay"),
    ("missing_receipt", "has_receipt", "receipt"),
)

_REQUIRED_BOOLS = (
    "has_language",
    "has_self",
    "has_anchor_person",
    "has_focus",
    "has_soul_overlay_beyond_language",
    "has_receipt",
)


def compute_initiation_status(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Return initiation status from a flat evidence map.

    Required boolean keys: has_language, has_self, has_anchor_person, has_focus,
    has_soul_overlay_beyond_language, has_receipt.

    Optional ints: non_self_person_count, topic_count (default 0).
    """
    for key in _REQUIRED_BOOLS:
        if key not in evidence:
            raise KeyError(f"missing evidence key: {key}")
        if not isinstance(evidence[key], bool):
            raise TypeError(f"{key} must be bool")

    non_self = int(evidence.get("non_self_person_count") or 0)
    topics = int(evidence.get("topic_count") or 0)

    for status_key, flag, next_step in _STAGES:
        if not evidence[flag]:
            return {
                "status": status_key,
                "complete": False,
                "mode": "INITIATE",
                "next_step": next_step,
                "graph_thin": True,
                "soft_hooks_allowed": False,
            }

    graph_thin = (
        non_self <= THIN_MAX_NON_SELF_PERSONS or topics <= THIN_MAX_TOPICS
    )
    return {
        "status": "complete",
        "complete": True,
        "mode": "NORMAL",
        "next_step": None,
        "graph_thin": graph_thin,
        "soft_hooks_allowed": graph_thin,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute initiation status from JSON evidence.")
    parser.add_argument(
        "evidence_json",
        nargs="?",
        help='JSON object evidence, e.g. \'{"has_language":true,...}\'',
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="Path to JSON file with evidence",
    )
    args = parser.parse_args()
    if args.file:
        raw = args.file.read_text(encoding="utf-8")
    elif args.evidence_json:
        raw = args.evidence_json
    else:
        raise SystemExit("Provide evidence_json or --file")
    evidence = json.loads(raw)
    print(json.dumps(compute_initiation_status(evidence), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
