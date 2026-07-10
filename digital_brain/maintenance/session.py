"""Host-agnostic harness session open / resolve.

Brains (Grok, Claude, Codex, …) call :func:`open_harness_session` to bind a
conversation to a frozen harness generation. The sticky ``active/`` pin is a
dual-process breadcrumb for MCP containers — never sole identity for a brain.

See: ``docs/superpowers/specs/2026-07-10-host-agnostic-harness-session-design.md``
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .generation import (
    SESSION_ENV_GENERATION_ID,
    SESSION_ENV_PIN_PATH,
    FORCE_NEW_HOOK_SOURCES,
    RELOAD_HOOK_SOURCES,
    collect_harness_generation,
    get_or_pin_session_generation,
    load_session_pin,
    new_ephemeral_session_id,
    pin_session_generation,
    resolve_session_binding,
    resolve_state_dir,
    sanitize_session_id,
    session_pin_path,
    write_active_harness_pin,
)
from .models import HarnessGeneration

SESSION_HANDLE_SCHEMA_VERSION = 1
SESSION_ENV_SESSION_ID = "DIGITAL_BRAIN_SESSION_ID"

# Host labels used in synthetic session ids (filesystem-safe).
_HOST_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def normalize_host(host: str | None) -> str:
    """Return a short filesystem-safe host label (default ``unknown``)."""
    raw = (host or "unknown").strip().lower()
    if not raw:
        return "unknown"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw)
    safe = safe.strip("-_")[:32] or "unknown"
    if not _HOST_RE.match(safe):
        return "unknown"
    return safe


def new_host_session_id(host: str | None = None) -> str:
    """Mint a host-prefixed ephemeral session id when the host provides none."""
    label = normalize_host(host)
    base = new_ephemeral_session_id()
    # new_ephemeral_session_id → local-<ts>-<pid>; replace local- with host-
    if base.startswith("local-"):
        return f"{label}-{base[len('local-'):]}"
    return f"{label}-{base}"


@dataclass(frozen=True)
class SessionHandle:
    """Public handle for a harness-bound conversation (no SOUL body)."""

    schema_version: int
    session_id: str
    harness_generation_id: str
    pin_path: str
    state_dir: str
    mode: str  # opened | resumed | recollected
    force_new: bool
    host: str
    plugin_version: str
    record_outcome: str
    overlay_pin_path: str | None
    created_at: str | None

    def to_public_dict(self) -> dict[str, Any]:
        """JSON-safe dict; never includes SOUL content keys."""
        payload = asdict(self)
        for forbidden in ("soul_content", "soul_text", "soul", "SOUL"):
            payload.pop(forbidden, None)
        # Legacy alias used by older pin CLI consumers.
        payload["generation_id"] = self.harness_generation_id
        return payload

    def apply_to_environ(self) -> None:
        """Export session + generation into process env for this process only."""
        os.environ[SESSION_ENV_SESSION_ID] = self.session_id
        os.environ[SESSION_ENV_GENERATION_ID] = self.harness_generation_id
        os.environ[SESSION_ENV_PIN_PATH] = self.pin_path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _write_session_env_file(
    *,
    pin_path: Path,
    generation_id: str,
    session_id: str,
) -> Path:
    export_path = pin_path.parent / "harness_generation.env"
    export_path.write_text(
        f"{SESSION_ENV_GENERATION_ID}={generation_id}\n"
        f"{SESSION_ENV_PIN_PATH}={pin_path}\n"
        f"{SESSION_ENV_SESSION_ID}={session_id}\n",
        encoding="utf-8",
    )
    return export_path


def _mode_for(
    *,
    force_new: bool,
    had_existing: bool,
    recollected: bool,
) -> str:
    if force_new and recollected:
        return "recollected"
    if had_existing and not force_new:
        return "resumed"
    return "opened"


def open_harness_session(
    *,
    session_id: str | None = None,
    host: str | None = "unknown",
    hook_source: str | None = None,
    force_new: bool | None = None,
    state_dir: str | Path | None = None,
    repo_root: str | Path | None = None,
    plugin_root: str | Path | None = None,
    soul_path: str | Path | None = None,
    skip_record: bool = True,
    record_outcome: str | None = None,
    export_env: bool = True,
    pin_overlays: bool = False,
    collect_kwargs: Mapping[str, Any] | None = None,
) -> SessionHandle:
    """Bind this conversation to a frozen harness generation.

    Pins under ``$STATE/sessions/<session_id>/`` and refreshes the dual-process
    ``active/`` breadcrumb. Does **not** adopt ``active/`` as session identity.

    MCP quality-plane record is left to the CLI by default (``skip_record=True``)
    so hermetic unit tests need no network. Callers may pass
    ``record_outcome`` after an external record attempt.
    """
    host_label = normalize_host(host)
    state = resolve_state_dir(state_dir)

    # When neither session_id nor env is set, mint a host-prefixed id (never sticky current).
    env_sid = (session_id if session_id is not None else os.getenv(SESSION_ENV_SESSION_ID) or "").strip()
    if not env_sid:
        resolved_session_id = new_host_session_id(host_label)
        ephemeral = True
    else:
        resolved_session_id, _ = resolve_session_binding(
            env_session_id=env_sid,
            hook_session_id=None,
            hook_source=hook_source,
            force_new=force_new,
        )
        ephemeral = False

    source = (hook_source or "").strip().lower()
    if force_new is not None:
        do_force = bool(force_new)
    elif source in FORCE_NEW_HOOK_SOURCES:
        do_force = True
    elif source in RELOAD_HOOK_SOURCES:
        do_force = False
    else:
        # Unknown/manual: force only when we just minted an ephemeral id or no pin yet.
        existing_probe = load_session_pin(
            state_dir=state, session_id=resolved_session_id
        )
        do_force = ephemeral or existing_probe is None

    had_existing = (
        load_session_pin(state_dir=state, session_id=resolved_session_id) is not None
    )

    collect: dict[str, Any] = {
        "state_dir": state,
    }
    if repo_root is not None:
        collect["repo_root"] = repo_root
    if plugin_root is not None:
        collect["plugin_root"] = plugin_root
    if soul_path is not None:
        collect["soul_path"] = soul_path
    if collect_kwargs:
        collect.update(dict(collect_kwargs))

    # collect may include state_dir for collect_harness_generation; strip before
    # get_or_pin which takes state_dir as an explicit kw-only arg.
    collect_for_pin = {k: v for k, v in collect.items() if k != "state_dir"}

    if do_force:
        generation = collect_harness_generation(**collect)
        if generation.created_at is None:
            generation = HarnessGeneration(
                **{**generation.to_public_dict(), "created_at": _utc_now_iso()}
            )
        pin_path = pin_session_generation(
            generation,
            state_dir=state,
            session_id=resolved_session_id,
            export_env=export_env,
        )
    else:
        generation = get_or_pin_session_generation(
            state_dir=state,
            session_id=resolved_session_id,
            force_new=False,
            **collect_for_pin,
        )
        pin_path = session_pin_path(state, resolved_session_id)

    # Refresh dual-process breadcrumb with this session_id (not a ticket).
    write_active_harness_pin(
        generation.id, state_dir=state, session_id=resolved_session_id
    )
    _write_session_env_file(
        pin_path=pin_path,
        generation_id=generation.id,
        session_id=resolved_session_id,
    )

    if export_env:
        os.environ[SESSION_ENV_SESSION_ID] = resolved_session_id
        os.environ[SESSION_ENV_GENERATION_ID] = generation.id
        os.environ[SESSION_ENV_PIN_PATH] = str(pin_path)

    overlay_pin: str | None = None
    if pin_overlays:
        try:
            from .active_overlays import pin_session_active_overlays

            op = pin_session_active_overlays(
                state_dir=state, session_id=resolved_session_id
            )
            overlay_pin = str(op) if op is not None else None
        except Exception:
            overlay_pin = None

    outcome = record_outcome
    if outcome is None:
        outcome = "skipped" if skip_record else "unknown"

    mode = _mode_for(
        force_new=do_force,
        had_existing=had_existing,
        recollected=do_force and had_existing,
    )
    if do_force and had_existing:
        mode = "recollected"
    elif had_existing and not do_force:
        mode = "resumed"
    else:
        mode = "opened"

    handle = SessionHandle(
        schema_version=SESSION_HANDLE_SCHEMA_VERSION,
        session_id=resolved_session_id,
        harness_generation_id=generation.id,
        pin_path=str(pin_path),
        state_dir=str(state),
        mode=mode,
        force_new=do_force,
        host=host_label,
        plugin_version=generation.plugin_version,
        record_outcome=outcome,
        overlay_pin_path=overlay_pin,
        created_at=generation.created_at,
    )
    return handle


def resolve_handle_for_chat(
    *,
    session_id: str | None = None,
    state_dir: str | Path | None = None,
    host: str | None = "unknown",
    open_if_missing: bool = False,
    **open_kwargs: Any,
) -> SessionHandle | None:
    """Resolve a SessionHandle for *this* chat without stealing ``active/``.

    Order:
      1. Explicit session_id + load pin under sessions/<id>/
      2. Env ``DIGITAL_BRAIN_SESSION_ID`` + pin file
      3. Env ``DIGITAL_BRAIN_HARNESS_GENERATION_ID`` only when session_id also known
         (env generation alone is insufficient without session binding)
      4. Optional :func:`open_harness_session` if ``open_if_missing``

    Never uses ``$STATE/active/`` alone as identity.
    """
    state = resolve_state_dir(state_dir)
    sid = (session_id or os.getenv(SESSION_ENV_SESSION_ID) or "").strip()
    if sid:
        safe = sanitize_session_id(sid)
        existing = load_session_pin(state_dir=state, session_id=safe)
        if existing is not None:
            pin_path = session_pin_path(state, safe)
            env_gid = (os.getenv(SESSION_ENV_GENERATION_ID) or "").strip()
            # Prefer pin file id; env may lag but must not override a different pin silently.
            gid = existing.id
            if env_gid and env_gid != gid:
                # Pin file is source of truth for this session_id.
                gid = existing.id
            return SessionHandle(
                schema_version=SESSION_HANDLE_SCHEMA_VERSION,
                session_id=safe,
                harness_generation_id=gid,
                pin_path=str(pin_path),
                state_dir=str(state),
                mode="resumed",
                force_new=False,
                host=normalize_host(host),
                plugin_version=existing.plugin_version,
                record_outcome="skipped",
                overlay_pin_path=None,
                created_at=existing.created_at,
            )

    # Generation env without session_id is not enough to claim a chat identity.
    if open_if_missing:
        return open_harness_session(
            session_id=session_id,
            host=host,
            state_dir=state,
            **open_kwargs,
        )
    return None


def assert_no_soul_in_handle_payload(payload: Mapping[str, Any]) -> None:
    """Raise if a public handle payload embeds SOUL body text keys."""
    for key in payload:
        lower = str(key).lower()
        if lower in {"soul_content", "soul_text", "soul"} or lower.startswith("soul_body"):
            raise ValueError(f"SOUL content key forbidden in session handle: {key}")


def handle_from_public_dict(data: Mapping[str, Any]) -> SessionHandle:
    """Rehydrate a SessionHandle from JSON (tests / CLI round-trip)."""
    assert_no_soul_in_handle_payload(data)
    gid = (
        str(data.get("harness_generation_id") or data.get("generation_id") or "").strip()
    )
    if not gid:
        raise ValueError("harness_generation_id required")
    sid = str(data.get("session_id") or "").strip()
    if not sid:
        raise ValueError("session_id required")
    return SessionHandle(
        schema_version=int(data.get("schema_version") or SESSION_HANDLE_SCHEMA_VERSION),
        session_id=sanitize_session_id(sid),
        harness_generation_id=gid,
        pin_path=str(data.get("pin_path") or ""),
        state_dir=str(data.get("state_dir") or ""),
        mode=str(data.get("mode") or "opened"),
        force_new=bool(data.get("force_new", False)),
        host=normalize_host(str(data.get("host") or "unknown")),
        plugin_version=str(data.get("plugin_version") or "unknown"),
        record_outcome=str(data.get("record_outcome") or "skipped"),
        overlay_pin_path=(
            str(data["overlay_pin_path"])
            if data.get("overlay_pin_path") is not None
            else None
        ),
        created_at=(
            str(data["created_at"]) if data.get("created_at") is not None else None
        ),
    )


def load_active_pin_meta(
    state_dir: str | Path | None = None,
) -> dict[str, str] | None:
    """Read active/ breadcrumb metadata (diagnostic only — not a session ticket).

    Skills must not treat this as resolve_handle_for_chat identity.
    """
    state = resolve_state_dir(state_dir)
    path = state / "active" / "harness_generation.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("id"):
        return None
    out = {"id": str(data["id"])}
    if data.get("session_id"):
        out["session_id"] = sanitize_session_id(str(data["session_id"]))
    return out
