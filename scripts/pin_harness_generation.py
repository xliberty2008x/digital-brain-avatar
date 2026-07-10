#!/usr/bin/env python3
"""SessionStart host entrypoint: pin and record a harness generation.

Runs after the local Neo4j + MCP stack is ready. Collects a deterministic
HarnessGeneration (SOUL hash only), records it on the quality plane when MCP
is reachable, and persists the pin for the session so mid-session file/policy
changes cannot alter the id.

Session identity:
  Prefer DIGITAL_BRAIN_SESSION_ID or Claude hook session_id (via --session-id /
  --hook-source from compose-up). Never stick forever to a global "current" pin
  across SessionStarts — compose-up falls back to a timestamped local id when
  the host provides none.

  --force-new (or SessionStart source startup/clear): recollect and overwrite.
  resume/compact: reload the existing pin for that session.

When CLAUDE_ENV_FILE is set, appends export lines for the host session env.
Never prints SOUL content.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from digital_brain.maintenance.generation import (  # noqa: E402
    SESSION_ENV_GENERATION_ID,
    SESSION_ENV_PIN_PATH,
    collect_harness_generation,
    export_pin_to_claude_env_file,
    get_or_pin_session_generation,
    load_session_pin,
    pin_session_generation,
    resolve_session_binding,
    resolve_state_dir,
    session_pin_path,
)
from digital_brain.maintenance.models import (  # noqa: E402
    HarnessGeneration,
    generation_request_fingerprint,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pin and optionally record the session harness generation."
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help=(
            "Session key under DIGITAL_BRAIN_STATE_DIR/sessions/<id>/. "
            "Prefer Claude hook session_id or DIGITAL_BRAIN_SESSION_ID; "
            "when omitted, resolve_session_binding may mint an ephemeral id."
        ),
    )
    parser.add_argument(
        "--hook-source",
        default=None,
        help="Claude SessionStart source (startup|resume|clear|compact)",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Override DIGITAL_BRAIN_STATE_DIR",
    )
    parser.add_argument(
        "--repo-root",
        default=os.getenv("CLAUDE_PROJECT_DIR") or str(ROOT),
        help="Repository root for git commit/tree digests",
    )
    parser.add_argument(
        "--plugin-root",
        default=os.getenv("CLAUDE_PLUGIN_ROOT") or None,
        help="digital-brain-buddy plugin root (version.json)",
    )
    parser.add_argument(
        "--soul-path",
        default=os.getenv("DIGITAL_BRAIN_SOUL_PATH") or None,
        help="Path to local SOUL.MD (hashed only; content never exported)",
    )
    parser.add_argument(
        "--force-new",
        action="store_true",
        help="Ignore an existing session pin and recollect (startup/clear)",
    )
    parser.add_argument(
        "--skip-record",
        action="store_true",
        help="Pin locally without calling MCP record_harness_generation",
    )
    parser.add_argument(
        "--require-record",
        action="store_true",
        help="Exit non-zero when MCP record fails (default: warn and keep pin)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a public JSON summary (no SOUL content) on stdout",
    )
    return parser.parse_args()


def _public_summary(
    generation: HarnessGeneration,
    *,
    pin_path: Path,
    record_outcome: str | None,
    session_id: str,
) -> dict[str, Any]:
    return {
        "generation_id": generation.id,
        "session_id": session_id,
        "core_commit": generation.core_commit,
        "plugin_version": generation.plugin_version,
        "soul_sha": generation.soul_sha,
        "overlay_manifest_digest": generation.overlay_manifest_digest,
        "policy_digest": generation.policy_digest,
        "mcp_version": generation.mcp_version,
        "model_id": generation.model_id,
        "schema_version": generation.schema_version,
        "taxonomy_version": generation.taxonomy_version,
        "created_at": generation.created_at,
        "pin_path": str(pin_path),
        "record_outcome": record_outcome,
    }


async def _record(generation: HarnessGeneration) -> dict[str, Any]:
    from digital_brain.tools.mcp_client import (
        get_harness_generation,
        record_harness_generation,
    )

    payload = generation.to_record_params()
    # Drop None model_id / created_at for cleaner tool args when absent.
    if payload.get("model_id") is None:
        payload.pop("model_id", None)
    try:
        return await record_harness_generation(payload)
    except Exception as first_exc:
        # Timeout / unknown write: reconcile by id before failing.
        try:
            readback = await get_harness_generation(generation.id)
        except Exception as read_exc:
            raise RuntimeError(
                f"record failed and reconciliation failed: {first_exc}; {read_exc}"
            ) from first_exc
        if readback.get("outcome") == "ok":
            existing_fp = readback.get("request_fingerprint")
            expected_fp = generation_request_fingerprint(generation)
            if existing_fp == expected_fp:
                return {
                    "outcome": "replayed",
                    "generation_id": readback.get("id") or generation.id,
                    "request_fingerprint": existing_fp,
                    "created_at": readback.get("created_at"),
                    "reconciled_after_error": True,
                }
            return {
                "outcome": "conflict",
                "reason": "generation_id_reused",
                "generation_id": generation.id,
                "request_fingerprint": existing_fp,
            }
        raise RuntimeError(f"record failed: {first_exc}") from first_exc


def _export_session_env(generation: HarnessGeneration, pin_path: Path) -> Path:
    """Write process env, state-dir .env, and optional CLAUDE_ENV_FILE exports."""
    os.environ[SESSION_ENV_GENERATION_ID] = generation.id
    os.environ[SESSION_ENV_PIN_PATH] = str(pin_path)

    export_path = pin_path.parent / "harness_generation.env"
    export_path.write_text(
        f"{SESSION_ENV_GENERATION_ID}={generation.id}\n"
        f"{SESSION_ENV_PIN_PATH}={pin_path}\n",
        encoding="utf-8",
    )
    export_pin_to_claude_env_file(generation.id, pin_path)
    return export_path


def main() -> int:
    args = parse_args()
    state_dir = resolve_state_dir(args.state_dir)

    # Resolve session binding: CLI session-id wins as env override, then hook
    # source decides force-new unless --force-new was passed.
    force_flag = True if args.force_new else None
    session_id, force_new = resolve_session_binding(
        env_session_id=args.session_id,
        hook_session_id=None,
        hook_source=args.hook_source,
        force_new=force_flag,
    )
    # Persist resolved id for child tools / compose-up log correlation.
    os.environ["DIGITAL_BRAIN_SESSION_ID"] = session_id

    collect_kwargs: dict[str, Any] = {
        "repo_root": args.repo_root,
        "state_dir": state_dir,
    }
    if args.plugin_root:
        collect_kwargs["plugin_root"] = args.plugin_root
    if args.soul_path:
        collect_kwargs["soul_path"] = args.soul_path

    if force_new:
        generation = collect_harness_generation(**collect_kwargs)
        pin_path = pin_session_generation(
            generation, state_dir=state_dir, session_id=session_id, export_env=True
        )
    else:
        existing = load_session_pin(state_dir=state_dir, session_id=session_id)
        if existing is not None:
            generation = existing
            pin_path = session_pin_path(state_dir, session_id)
            os.environ[SESSION_ENV_GENERATION_ID] = generation.id
            os.environ[SESSION_ENV_PIN_PATH] = str(pin_path)
        else:
            # collect_kwargs already includes state_dir; do not pass it twice.
            generation = get_or_pin_session_generation(
                session_id=session_id,
                force_new=False,
                **collect_kwargs,
            )
            pin_path = session_pin_path(state_dir, session_id)

    record_outcome: str | None = None
    if not args.skip_record:
        try:
            receipt = asyncio.run(_record(generation))
            record_outcome = str(receipt.get("outcome") or "unknown")
            if record_outcome == "conflict":
                print(
                    f"pin_harness_generation: CONFLICT generation_id={generation.id} "
                    f"(existing fingerprint differs); local pin kept",
                    file=sys.stderr,
                )
                if args.require_record:
                    return 2
            else:
                print(
                    f"pin_harness_generation: recorded outcome={record_outcome} "
                    f"id={generation.id}",
                    file=sys.stderr,
                )
        except Exception as exc:
            print(
                f"pin_harness_generation: MCP record skipped/failed ({exc}); "
                f"local pin retained id={generation.id}",
                file=sys.stderr,
            )
            record_outcome = "record_unavailable"
            if args.require_record:
                return 1
    else:
        record_outcome = "skipped"

    # Final export for SessionStart consumers / compose logs / CLAUDE_ENV_FILE.
    _export_session_env(generation, pin_path)

    summary = _public_summary(
        generation,
        pin_path=pin_path,
        record_outcome=record_outcome,
        session_id=session_id,
    )
    # Hard guarantee: never emit SOUL body keys.
    for forbidden in ("soul_content", "soul_text", "soul", "SOUL"):
        summary.pop(forbidden, None)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"pin_harness_generation: pinned id={generation.id} "
            f"session={session_id} plugin={generation.plugin_version} "
            f"soul_sha={generation.soul_sha[:12]}… "
            f"pin={pin_path} record={record_outcome}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
