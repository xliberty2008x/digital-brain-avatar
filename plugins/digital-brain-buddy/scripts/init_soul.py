#!/usr/bin/env python3
"""Initialize a SOUL.md file from the bundled template."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize SOUL.md from the plugin template.")
    parser.add_argument(
        "target",
        nargs="?",
        default="SOUL.MD",
        help="Target path for the generated SOUL.MD file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the target file if it already exists.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plugin_root = Path(__file__).resolve().parent.parent
    source = plugin_root / "assets" / "SOUL.template.md"
    target = Path(args.target).expanduser().resolve()

    if target.exists() and not args.force:
        raise SystemExit(f"{target} already exists. Use --force to overwrite.")

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    print(f"Initialized SOUL from template: {target}")


if __name__ == "__main__":
    main()
