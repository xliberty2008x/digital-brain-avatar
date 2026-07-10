"""Collect, pin, and load session harness generations.

Pin once at session start. Mid-session file/policy edits must not change the
session's pinned id — only a new session recollects inputs.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .models import (
    EMPTY_DIGEST,
    HARNESS_SCHEMA_VERSION,
    TAXONOMY_VERSION,
    HarnessGeneration,
    build_harness_generation,
    digest_bytes,
    digest_text,
)

# Legacy API default only — production SessionStart must bind a real host session
# id (Claude hook session_id / DIGITAL_BRAIN_SESSION_ID). Never reuse a global
# "current" pin across SessionStarts; prefer :func:`new_ephemeral_session_id`.
DEFAULT_SESSION_ID = "current"
PIN_FILENAME = "harness_generation.json"
SESSION_ENV_GENERATION_ID = "DIGITAL_BRAIN_HARNESS_GENERATION_ID"
SESSION_ENV_PIN_PATH = "DIGITAL_BRAIN_HARNESS_PIN_PATH"
# Claude SessionStart sources that must recollect a pin vs reload an existing one.
FORCE_NEW_HOOK_SOURCES = frozenset({"startup", "clear"})
RELOAD_HOOK_SOURCES = frozenset({"resume", "compact"})

# Active overlay manifest relative to the state directory.
ACTIVE_OVERLAY_MANIFEST_REL = Path("dreams") / "active-overlays" / "manifest.json"
# Optional structured policy file (enum/numeric knobs only when present).
ACTIVE_POLICY_REL = Path("dreams") / "active-policy" / "policy.json"
# Well-known active pin (id only / pin json without SOUL) so dual-process MCP
# containers can read the session pin without host-only env injection.
ACTIVE_PIN_DIR_REL = Path("active")
ACTIVE_PIN_ID_FILENAME = "harness_generation.id"
ACTIVE_PIN_JSON_FILENAME = "harness_generation.json"


def resolve_state_dir(explicit: str | Path | None = None) -> Path:
    """Resolve the local runtime state directory (never a repo path by default)."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env = (os.getenv("DIGITAL_BRAIN_STATE_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    xdg = (os.getenv("XDG_STATE_HOME") or "").strip()
    if xdg:
        return Path(xdg).expanduser().resolve() / "digital-brain"
    return Path.home().resolve() / ".local" / "state" / "digital-brain"


def sanitize_session_id(session_id: str | None) -> str:
    """Filesystem-safe session key (alphanumeric, dash, underscore, dot)."""
    raw = (session_id or "").strip()
    safe = "".join(
        ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in raw
    )
    return safe or DEFAULT_SESSION_ID


def new_ephemeral_session_id() -> str:
    """Mint a per-invocation session id when the host provides none.

    Prefer Claude hook ``session_id`` or ``DIGITAL_BRAIN_SESSION_ID``. Falling
    back to a timestamped id is better than reusing a global ``current`` pin
    forever across SessionStarts.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"local-{ts}-{os.getpid()}"


def resolve_session_binding(
    *,
    env_session_id: str | None = None,
    hook_session_id: str | None = None,
    hook_source: str | None = None,
    force_new: bool | None = None,
) -> tuple[str, bool]:
    """Resolve ``(session_id, force_new)`` for SessionStart / compose-up.

    Session id priority:
    1. ``env_session_id`` / ``DIGITAL_BRAIN_SESSION_ID``
    2. Claude hook ``session_id`` from stdin JSON
    3. Ephemeral timestamped id (never sticky global ``current``)

    Force-new:
    - ``startup`` / ``clear`` → recollect
    - ``resume`` / ``compact`` → reload existing pin for that session
    - explicit ``force_new`` overrides source when provided
    - ephemeral fallback always uses a fresh id (pin path is empty)
    """
    env_sid = (env_session_id if env_session_id is not None else os.getenv("DIGITAL_BRAIN_SESSION_ID") or "").strip()
    hook_sid = (hook_session_id or "").strip()
    source = (hook_source or "").strip().lower()

    if env_sid:
        session_id = sanitize_session_id(env_sid)
        ephemeral = False
    elif hook_sid:
        session_id = sanitize_session_id(hook_sid)
        ephemeral = False
    else:
        session_id = new_ephemeral_session_id()
        ephemeral = True

    if force_new is not None:
        return session_id, bool(force_new)
    if source in FORCE_NEW_HOOK_SOURCES:
        return session_id, True
    if source in RELOAD_HOOK_SOURCES:
        return session_id, False
    # Manual compose-up / unknown source: reuse pin for known session ids;
    # ephemeral ids are new paths so recollect is implicit.
    return session_id, ephemeral


def export_pin_to_claude_env_file(
    generation_id: str,
    pin_path: str | Path,
    *,
    env_file: str | Path | None = None,
) -> Path | None:
    """Append pin exports to Claude Code ``CLAUDE_ENV_FILE`` when set.

    Claude SessionStart loads this file so subsequent host bash tools see
    ``DIGITAL_BRAIN_HARNESS_GENERATION_ID`` and ``DIGITAL_BRAIN_HARNESS_PIN_PATH``.
    """
    target = env_file if env_file is not None else (os.getenv("CLAUDE_ENV_FILE") or "").strip()
    if not target:
        return None
    path = Path(target).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Values are controlled (hg-<hex> + local path); still quote for shell safety.
    gid = str(generation_id).replace("'", "'\"'\"'")
    ppath = str(pin_path).replace("'", "'\"'\"'")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"export {SESSION_ENV_GENERATION_ID}='{gid}'\n")
        fh.write(f"export {SESSION_ENV_PIN_PATH}='{ppath}'\n")
    return path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _run_git(repo_root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _core_commit_and_tree(repo_root: Path) -> tuple[str, str]:
    commit = _run_git(repo_root, "rev-parse", "HEAD")
    if not commit:
        return "unknown", EMPTY_DIGEST
    tree = _run_git(repo_root, "rev-parse", "HEAD^{tree}")
    return commit, (tree or EMPTY_DIGEST)


def _dirty_state_digest(repo_root: Path) -> str:
    porcelain = _run_git(repo_root, "status", "--porcelain")
    if porcelain is None:
        return EMPTY_DIGEST
    lines = [line for line in porcelain.splitlines() if line.strip()]
    if not lines:
        return EMPTY_DIGEST
    # Sort for path-order stability across git versions.
    normalized = "\n".join(sorted(lines)) + "\n"
    return digest_text(normalized)


def _read_plugin_version(plugin_root: Path) -> str:
    version_path = plugin_root / "version.json"
    if not version_path.is_file():
        return "unknown"
    try:
        raw = json.loads(version_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "unknown"
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, dict):
        value = raw.get("version")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def _soul_sha(soul_path: Path | None) -> str:
    """Hash SOUL file bytes locally. Never return or store the content."""
    if soul_path is None or not soul_path.is_file():
        return EMPTY_DIGEST
    try:
        return digest_bytes(soul_path.read_bytes())
    except OSError:
        return EMPTY_DIGEST


def _file_digest(path: Path | None) -> str:
    if path is None or not path.is_file():
        return EMPTY_DIGEST
    try:
        return digest_bytes(path.read_bytes())
    except OSError:
        return EMPTY_DIGEST


def _overlay_manifest_digest(state_dir: Path) -> str:
    """Digest of the active overlay manifest only (not quarantine)."""
    return _file_digest(state_dir / ACTIVE_OVERLAY_MANIFEST_REL)


def _policy_digest(state_dir: Path) -> str:
    return _file_digest(state_dir / ACTIVE_POLICY_REL)


def _default_mcp_version() -> str:
    env = (os.getenv("DIGITAL_BRAIN_MCP_VERSION") or "").strip()
    if env:
        return env
    # Packaged local MCP server version (kept in sync with pyproject).
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("digital-brain-mcp-cypher")
    except Exception:
        pass
    return "0.1.0"


def _default_model_id() -> str | None:
    for key in ("DIGITAL_BRAIN_MODEL_ID", "ANTHROPIC_MODEL", "OPENAI_MODEL"):
        value = (os.getenv(key) or "").strip()
        if value:
            return value
    return None


def _default_repo_root() -> Path:
    env = (os.getenv("CLAUDE_PROJECT_DIR") or os.getenv("DIGITAL_BRAIN_REPO_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # digital_brain/maintenance/generation.py → repo root is parents[2]
    return Path(__file__).resolve().parents[2]


def _default_plugin_root(repo_root: Path) -> Path:
    env = (os.getenv("CLAUDE_PLUGIN_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return repo_root / "plugins" / "digital-brain-buddy"


def _default_soul_path(plugin_root: Path, repo_root: Path) -> Path:
    env = (os.getenv("DIGITAL_BRAIN_SOUL_PATH") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # Plugin-local SOUL.MD is the buddy contract; fall back to repo root.
    candidates = (
        plugin_root / "SOUL.MD",
        plugin_root / "SOUL.md",
        repo_root / "SOUL.MD",
        repo_root / "SOUL.md",
    )
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


def collect_harness_generation(
    *,
    repo_root: str | Path | None = None,
    plugin_root: str | Path | None = None,
    soul_path: str | Path | None = None,
    state_dir: str | Path | None = None,
    mcp_version: str | None = None,
    model_id: str | None | object = ...,
    schema_version: str = HARNESS_SCHEMA_VERSION,
    taxonomy_version: str = TAXONOMY_VERSION,
    core_commit: str | None = None,
    core_tree_digest: str | None = None,
    dirty_state_digest: str | None = None,
    plugin_version: str | None = None,
    soul_sha: str | None = None,
    overlay_manifest_digest: str | None = None,
    policy_digest: str | None = None,
    created_at: str | None = None,
) -> HarnessGeneration:
    """Collect a deterministic HarnessGeneration from local inputs.

    Explicit field overrides are intended for tests and hosts that already
    measured digests. SOUL content is never accepted as an argument and never
    stored — only ``soul_sha``.
    """
    root = Path(repo_root).expanduser().resolve() if repo_root else _default_repo_root()
    plugin = (
        Path(plugin_root).expanduser().resolve()
        if plugin_root
        else _default_plugin_root(root)
    )
    state = resolve_state_dir(state_dir)
    soul = (
        Path(soul_path).expanduser().resolve()
        if soul_path
        else _default_soul_path(plugin, root)
    )

    measured_commit, measured_tree = _core_commit_and_tree(root)
    if model_id is ...:
        resolved_model_id = _default_model_id()
    else:
        resolved_model_id = model_id  # type: ignore[assignment]

    return build_harness_generation(
        core_commit=core_commit if core_commit is not None else measured_commit,
        core_tree_digest=(
            core_tree_digest if core_tree_digest is not None else measured_tree
        ),
        dirty_state_digest=(
            dirty_state_digest
            if dirty_state_digest is not None
            else _dirty_state_digest(root)
        ),
        plugin_version=(
            plugin_version if plugin_version is not None else _read_plugin_version(plugin)
        ),
        soul_sha=soul_sha if soul_sha is not None else _soul_sha(soul),
        overlay_manifest_digest=(
            overlay_manifest_digest
            if overlay_manifest_digest is not None
            else _overlay_manifest_digest(state)
        ),
        policy_digest=(
            policy_digest if policy_digest is not None else _policy_digest(state)
        ),
        mcp_version=mcp_version if mcp_version is not None else _default_mcp_version(),
        model_id=resolved_model_id,
        schema_version=schema_version,
        taxonomy_version=taxonomy_version,
        created_at=created_at,
    )


def session_pin_path(
    state_dir: str | Path | None = None,
    session_id: str = DEFAULT_SESSION_ID,
) -> Path:
    state = resolve_state_dir(state_dir)
    safe_session = sanitize_session_id(session_id)
    return state / "sessions" / safe_session / PIN_FILENAME


def active_pin_dir(state_dir: str | Path | None = None) -> Path:
    """Directory for the well-known cross-process active harness pin."""
    return resolve_state_dir(state_dir) / ACTIVE_PIN_DIR_REL


def active_pin_id_path(state_dir: str | Path | None = None) -> Path:
    return active_pin_dir(state_dir) / ACTIVE_PIN_ID_FILENAME


def active_pin_json_path(state_dir: str | Path | None = None) -> Path:
    return active_pin_dir(state_dir) / ACTIVE_PIN_JSON_FILENAME


def write_active_harness_pin(
    generation_id: str,
    *,
    state_dir: str | Path | None = None,
    session_id: str | None = None,
) -> Path:
    """Write id-only active pin files under ``$DIGITAL_BRAIN_STATE_DIR/active/``.

    Dual-process emit (host SessionStart + mcp-cypher container) shares this
    well-known location. Content is the generation id only — never SOUL body.
    Returns the JSON path.
    """
    gid = (generation_id or "").strip()
    if not gid:
        raise ValueError("generation_id is required for active pin")
    state = resolve_state_dir(state_dir)
    directory = state / ACTIVE_PIN_DIR_REL
    directory.mkdir(parents=True, exist_ok=True)

    id_path = directory / ACTIVE_PIN_ID_FILENAME
    id_tmp = id_path.with_suffix(".id.tmp")
    id_tmp.write_text(gid + "\n", encoding="utf-8")
    id_tmp.replace(id_path)

    payload: dict[str, Any] = {"id": gid}
    if session_id is not None and str(session_id).strip():
        payload["session_id"] = sanitize_session_id(session_id)
    json_path = directory / ACTIVE_PIN_JSON_FILENAME
    json_tmp = json_path.with_suffix(".tmp")
    json_tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    json_tmp.replace(json_path)
    return json_path


def pin_session_generation(
    generation: HarnessGeneration,
    *,
    state_dir: str | Path | None = None,
    session_id: str = DEFAULT_SESSION_ID,
    export_env: bool = True,
) -> Path:
    """Persist the pinned generation for this session (public fields only)."""
    path = session_pin_path(state_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = generation.to_public_dict()
    # Never allow SOUL body keys into the pin file.
    for forbidden in ("soul_content", "soul_text", "soul", "SOUL"):
        payload.pop(forbidden, None)
    if not payload.get("created_at"):
        payload["created_at"] = _utc_now_iso()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    # Cross-process active pin for MCP instrumentation (no SOUL content).
    write_active_harness_pin(
        generation.id, state_dir=state_dir, session_id=session_id
    )
    if export_env:
        os.environ[SESSION_ENV_GENERATION_ID] = generation.id
        os.environ[SESSION_ENV_PIN_PATH] = str(path)
    return path


def load_session_pin(
    *,
    state_dir: str | Path | None = None,
    session_id: str = DEFAULT_SESSION_ID,
) -> HarnessGeneration | None:
    """Load a previously pinned generation without recollecting digests."""
    path = session_pin_path(state_dir, session_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for forbidden in ("soul_content", "soul_text", "soul", "SOUL"):
        if forbidden in data:
            # Corrupt/malicious pin — refuse to load contentful soul fields.
            data = {k: v for k, v in data.items() if k not in (
                "soul_content",
                "soul_text",
                "soul",
                "SOUL",
            )}
    try:
        return HarnessGeneration.from_mapping(data)
    except (TypeError, ValueError):
        return None


def get_or_pin_session_generation(
    *,
    state_dir: str | Path | None = None,
    session_id: str = DEFAULT_SESSION_ID,
    force_new: bool = False,
    **collect_kwargs: Any,
) -> HarnessGeneration:
    """Return the session pin if present; otherwise collect and pin.

    Mid-session callers must use this (or :func:`load_session_pin`) so that
    file/policy changes after SessionStart do not alter the pinned id.
    """
    if not force_new:
        existing = load_session_pin(state_dir=state_dir, session_id=session_id)
        if existing is not None:
            os.environ[SESSION_ENV_GENERATION_ID] = existing.id
            os.environ[SESSION_ENV_PIN_PATH] = str(
                session_pin_path(state_dir, session_id)
            )
            # Refresh active pin so MCP sees the reloaded session pin.
            write_active_harness_pin(
                existing.id, state_dir=state_dir, session_id=session_id
            )
            return existing
    generation = collect_harness_generation(state_dir=state_dir, **collect_kwargs)
    if generation.created_at is None:
        generation = HarnessGeneration(
            **{
                **generation.to_public_dict(),
                "created_at": _utc_now_iso(),
            }
        )
    pin_session_generation(
        generation, state_dir=state_dir, session_id=session_id, export_env=True
    )
    return generation


def assert_no_soul_content(payload: Mapping[str, Any]) -> None:
    """Raise if a public payload appears to embed SOUL body text."""
    for key in payload:
        lower = str(key).lower()
        if lower in {"soul_content", "soul_text", "soul"} or lower.startswith("soul_body"):
            raise ValueError(f"SOUL content key forbidden in harness payload: {key}")
