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

DEFAULT_SESSION_ID = "current"
PIN_FILENAME = "harness_generation.json"
SESSION_ENV_GENERATION_ID = "DIGITAL_BRAIN_HARNESS_GENERATION_ID"
SESSION_ENV_PIN_PATH = "DIGITAL_BRAIN_HARNESS_GENERATION_PIN"

# Active overlay manifest relative to the state directory.
ACTIVE_OVERLAY_MANIFEST_REL = Path("dreams") / "active-overlays" / "manifest.json"
# Optional structured policy file (enum/numeric knobs only when present).
ACTIVE_POLICY_REL = Path("dreams") / "active-policy" / "policy.json"


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
    safe_session = "".join(
        ch if ch.isalnum() or ch in ("-", "_", ".") else "_"
        for ch in (session_id or DEFAULT_SESSION_ID)
    ) or DEFAULT_SESSION_ID
    return state / "sessions" / safe_session / PIN_FILENAME


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
