#!/usr/bin/env python3
"""Host-agnostic harness session pin: open/resume + optional quality-plane record.

Portable entrypoint for any brain host (Claude SessionStart, Grok/Codex skill
step 0, manual operator). Collects a deterministic HarnessGeneration (SOUL hash
only), records it on the quality plane when MCP is reachable, and persists the
pin for the session so mid-session file/policy changes cannot alter the id.

Session identity:
  Prefer DIGITAL_BRAIN_SESSION_ID or host session id (via --session-id /
  --hook-source from compose-up). Never stick forever to a global "current" pin
  across opens — falls back to a host-prefixed timestamped id when none given.

  --force-new (or SessionStart source startup/clear): recollect and overwrite.
  resume/compact: reload the existing pin for that session.

  --json emits a SessionHandle-shaped public summary (no SOUL content).

When CLAUDE_ENV_FILE is set, appends export lines for the Claude host session env.
Never prints SOUL content.

See: docs/superpowers/specs/2026-07-10-host-agnostic-harness-session-design.md
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
from digital_brain.maintenance.session import (  # noqa: E402
    SESSION_HANDLE_SCHEMA_VERSION,
    normalize_host,
    open_harness_session,
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
        "--host",
        default=None,
        help="Brain host label (claude|grok|codex|…); used in synthetic session ids",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit SessionHandle-shaped public JSON (no SOUL content) on stdout",
    )
    parser.add_argument(
        "--use-open-api",
        action="store_true",
        help=(
            "Route through open_harness_session library (portable path). "
            "Default keeps legacy pin steps then shapes the same JSON handle."
        ),
    )
    return parser.parse_args()


def _public_summary(
    generation: HarnessGeneration,
    *,
    pin_path: Path,
    record_outcome: str | None,
    session_id: str,
    host: str = "unknown",
    mode: str = "opened",
    force_new: bool = False,
    state_dir: str | Path | None = None,
) -> dict[str, Any]:
    """SessionHandle-shaped summary plus legacy generation digests."""
    state = str(resolve_state_dir(state_dir))
    return {
        # SessionHandle fields (design §4.2)
        "schema_version": SESSION_HANDLE_SCHEMA_VERSION,
        "session_id": session_id,
        "harness_generation_id": generation.id,
        "generation_id": generation.id,  # legacy alias
        "pin_path": str(pin_path),
        "state_dir": state,
        "mode": mode,
        "force_new": force_new,
        "host": normalize_host(host),
        "plugin_version": generation.plugin_version,
        "record_outcome": record_outcome,
        "overlay_pin_path": None,
        "created_at": generation.created_at,
        # Extra digests for operators / compose logs
        "core_commit": generation.core_commit,
        "soul_sha": generation.soul_sha,
        "overlay_manifest_digest": generation.overlay_manifest_digest,
        "policy_digest": generation.policy_digest,
        "mcp_version": generation.mcp_version,
        "model_id": generation.model_id,
        "harness_schema_version": generation.schema_version,
        "taxonomy_version": generation.taxonomy_version,
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
    """Write process env, state-dir .env, active pin, and optional CLAUDE_ENV_FILE."""
    from digital_brain.maintenance.generation import write_active_harness_pin

    os.environ[SESSION_ENV_GENERATION_ID] = generation.id
    os.environ[SESSION_ENV_PIN_PATH] = str(pin_path)

    export_path = pin_path.parent / "harness_generation.env"
    export_path.write_text(
        f"{SESSION_ENV_GENERATION_ID}={generation.id}\n"
        f"{SESSION_ENV_PIN_PATH}={pin_path}\n",
        encoding="utf-8",
    )
    # Well-known active pin for dual-process MCP instrumentation (id only).
    write_active_harness_pin(
        generation.id,
        session_id=os.environ.get("DIGITAL_BRAIN_SESSION_ID"),
    )
    export_pin_to_claude_env_file(generation.id, pin_path)
    return export_path


def main() -> int:
    args = parse_args()
    state_dir = resolve_state_dir(args.state_dir)
    host_label = normalize_host(args.host or os.getenv("DIGITAL_BRAIN_HOST") or "unknown")

    collect_kwargs: dict[str, Any] = {
        "repo_root": args.repo_root,
        "state_dir": state_dir,
    }
    if args.plugin_root:
        collect_kwargs["plugin_root"] = args.plugin_root
    if args.soul_path:
        collect_kwargs["soul_path"] = args.soul_path

    # Portable path: open_harness_session owns pin + env + active breadcrumb.
    if args.use_open_api:
        force_flag = True if args.force_new else None
        handle = open_harness_session(
            session_id=args.session_id,
            host=host_label,
            hook_source=args.hook_source,
            force_new=force_flag,
            state_dir=state_dir,
            repo_root=args.repo_root,
            plugin_root=args.plugin_root,
            soul_path=args.soul_path,
            skip_record=True,
            export_env=True,
        )
        session_id = handle.session_id
        force_new = handle.force_new
        mode = handle.mode
        pin_path = Path(handle.pin_path)
        generation = load_session_pin(state_dir=state_dir, session_id=session_id)
        if generation is None:
            print(
                "pin_harness_generation: open_harness_session produced no pin",
                file=sys.stderr,
            )
            return 1
    else:
        # Legacy path: resolve binding then pin (Claude compose-up compatible).
        force_flag = True if args.force_new else None
        session_id, force_new = resolve_session_binding(
            env_session_id=args.session_id,
            hook_session_id=None,
            hook_source=args.hook_source,
            force_new=force_flag,
        )
        # Host-prefix ephemeral ids when binding minted local-* without --session-id.
        if (
            not (args.session_id or "").strip()
            and not (os.getenv("DIGITAL_BRAIN_SESSION_ID") or "").strip()
            and session_id.startswith("local-")
            and host_label != "unknown"
        ):
            session_id = f"{host_label}-{session_id[len('local-'):]}"
        os.environ["DIGITAL_BRAIN_SESSION_ID"] = session_id

        had_existing = (
            load_session_pin(state_dir=state_dir, session_id=session_id) is not None
        )
        if force_new:
            generation = collect_harness_generation(**collect_kwargs)
            pin_path = pin_session_generation(
                generation,
                state_dir=state_dir,
                session_id=session_id,
                export_env=True,
            )
            mode = "recollected" if had_existing else "opened"
        else:
            existing = load_session_pin(state_dir=state_dir, session_id=session_id)
            if existing is not None:
                generation = existing
                pin_path = session_pin_path(state_dir, session_id)
                os.environ[SESSION_ENV_GENERATION_ID] = generation.id
                os.environ[SESSION_ENV_PIN_PATH] = str(pin_path)
                mode = "resumed"
            else:
                generation = get_or_pin_session_generation(
                    session_id=session_id,
                    force_new=False,
                    **collect_kwargs,
                )
                pin_path = session_pin_path(state_dir, session_id)
                mode = "opened"

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
    # Ensure session id is in the .env file for non-Claude hosts.
    env_file = pin_path.parent / "harness_generation.env"
    if env_file.is_file():
        text = env_file.read_text(encoding="utf-8")
        if "DIGITAL_BRAIN_SESSION_ID=" not in text:
            with env_file.open("a", encoding="utf-8") as fh:
                fh.write(f"DIGITAL_BRAIN_SESSION_ID={session_id}\n")

    summary = _public_summary(
        generation,
        pin_path=pin_path,
        record_outcome=record_outcome,
        session_id=session_id,
        host=host_label,
        mode=mode,
        force_new=force_new,
        state_dir=state_dir,
    )
    # Hard guarantee: never emit SOUL body keys.
    for forbidden in ("soul_content", "soul_text", "soul", "SOUL"):
        summary.pop(forbidden, None)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"pin_harness_generation: pinned id={generation.id} "
            f"session={session_id} host={host_label} mode={mode} "
            f"plugin={generation.plugin_version} "
            f"soul_sha={generation.soul_sha[:12]}… "
            f"pin={pin_path} record={record_outcome}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
