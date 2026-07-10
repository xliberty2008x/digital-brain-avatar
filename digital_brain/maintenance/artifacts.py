"""Quarantine artifact layout and secure state-directory resolution.

Quarantine lives under ``$DIGITAL_BRAIN_STATE_DIR/dreams/quarantine/`` only.
Writing or modifying quarantine files must have zero runtime effect: session
loaders read only the active-overlay manifest, never this tree.

Patch digests use a single algorithm (:func:`compute_patch_sha256`) shared by
the compiler and the on-disk bundle writer so control-plane and disk match.
Isolated validation runs only fixed allowlisted repository-owned checks —
never free-form shell — against an ephemeral copy outside the live plugin.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from digital_brain.maintenance.generation import resolve_state_dir
from digital_brain.maintenance.models import (
    MAINTENANCE_SCHEMA_VERSION,
    PatchArtifactMetadata,
    digest_bytes,
    digest_text,
)
from digital_brain.maintenance.overlay_rules import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_FILES,
)

# Artifact schema for quarantine bundles (independent of maintenance schema).
ARTIFACT_SCHEMA_VERSION = "1"
COMPILER_VERSION_DEFAULT = "1"

QUARANTINE_REL = Path("dreams") / "quarantine"
# Active trial directory (Task 11) — never written by the compiler.
ACTIVE_OVERLAYS_REL = Path("dreams") / "active-overlays"
# Ephemeral isolated validation worktrees (never a runtime load path).
VALIDATION_WORK_REL = Path("dreams") / "validation-work"

# Quarantine file names (closed set — no extras).
INTENT_FILENAME = "intent.json"
ARTIFACT_FILENAME = "artifact.md"
MANIFEST_FILENAME = "manifest.json"
EVALUATION_FILENAME = "evaluation.json"
CHECKSUMS_FILENAME = "checksums.json"

QUARANTINE_FILENAMES: frozenset[str] = frozenset(
    {
        INTENT_FILENAME,
        ARTIFACT_FILENAME,
        MANIFEST_FILENAME,
        EVALUATION_FILENAME,
        CHECKSUMS_FILENAME,
    }
)

# JSON files validated by the fixed ``json.tool:<name>`` command family.
_JSON_QUARANTINE_FILES: frozenset[str] = frozenset(
    {
        INTENT_FILENAME,
        MANIFEST_FILENAME,
        EVALUATION_FILENAME,
        CHECKSUMS_FILENAME,
    }
)

# Fixed repository-owned validation command names (no free-form shell, no argv).
# ``json.tool:<filename>`` mirrors ``python -m json.tool`` on that file only.
ISOLATED_VALIDATION_COMMANDS: frozenset[str] = frozenset(
    {
        "json.tool:manifest.json",
        "json.tool:intent.json",
        "json.tool:evaluation.json",
        "json.tool:checksums.json",
        "recompute_checksums",
        "schema_check",
        "patch_digest_check",
    }
)

DEFAULT_ISOLATED_VALIDATION_COMMANDS: tuple[str, ...] = (
    "json.tool:manifest.json",
    "json.tool:intent.json",
    "json.tool:evaluation.json",
    "json.tool:checksums.json",
    "recompute_checksums",
    "schema_check",
    "patch_digest_check",
)

# Manifest keys required by schema_check (closed contract).
_MANIFEST_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        "artifact_path",
        "base_commit",
        "before_hashes",
        "compiler_version",
        "dream_id",
        "evidence_snapshot_id",
        "extension_slot",
        "patch_sha256",
        "proposal_id",
        "rule_id",
        "schema_version",
        "target_file",
        "target_skill",
    }
)

# Sentinel values for patch_sha256 before the shared algorithm fills it in.
_PATCH_SHA_PLACEHOLDERS: frozenset[str] = frozenset({"", "pending", "0" * 64})

# Safe path segment: alphanumeric, dash, underscore, dot (no separators).
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Directories that must never host quarantine (plugin load / cache / repo).
_FORBIDDEN_STATE_NAME_MARKERS = (
    "node_modules",
    ".git",
    "plugins",
    "__pycache__",
    ".cache",
)


class ArtifactError(ValueError):
    """Raised when quarantine paths, security checks, or writes fail."""


class SecureStateDirError(ArtifactError):
    """Raised when the state directory is symlinked, unowned, or insecure."""


class ImmutableArtifactError(ArtifactError):
    """Raised when an existing quarantine bundle would be mutated."""


class ValidationCommandError(ArtifactError):
    """Raised when an unknown or disallowed validation command is requested."""


class IsolatedValidationError(ArtifactError):
    """Raised when isolated validation of a quarantine bundle fails."""


@dataclass(frozen=True)
class QuarantineBundle:
    """In-memory representation of a written quarantine directory."""

    dream_id: str
    proposal_id: str
    directory: Path
    intent: dict[str, Any]
    artifact_md: str
    manifest: dict[str, Any]
    evaluation: dict[str, Any]
    checksums: dict[str, str]
    patch_sha256: str
    metadata: PatchArtifactMetadata


@dataclass(frozen=True)
class IsolatedValidationResult:
    """Outcome of fixed-command validation in an isolated worktree."""

    ok: bool
    work_dir: str
    commands_run: tuple[str, ...]
    results: dict[str, Any]
    errors: tuple[str, ...]


def sanitize_path_segment(value: str, *, field: str = "id") -> str:
    text = (value or "").strip()
    if not text or not _SAFE_SEGMENT.match(text):
        raise ArtifactError(f"unsafe_path_segment:{field}:{value!r}")
    if ".." in text or "/" in text or "\\" in text:
        raise ArtifactError(f"path_traversal_segment:{field}:{value!r}")
    return text


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _is_world_writable(mode: int) -> bool:
    return bool(mode & stat.S_IWOTH)


def _path_has_symlink(path: Path) -> bool:
    """True if path or any existing ancestor is a symlink."""
    current = path
    # Walk from path up to root; check each existing component.
    seen: set[Path] = set()
    while True:
        if current in seen:
            break
        seen.add(current)
        try:
            if current.exists(follow_symlinks=False) and current.is_symlink():
                return True
        except OSError:
            return True
        if current.parent == current:
            break
        current = current.parent
    return False


def _ensure_mode_0700(path: Path) -> None:
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        raise SecureStateDirError(f"cannot_set_mode_0700:{path}:{exc}") from exc


def _is_under(path: Path, root: Path) -> bool:
    """True when ``path`` is ``root`` or a descendant (after resolve)."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        pass
    # Fallback for platform path aliasing (e.g. /var vs /private/var).
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        return False


def resolve_secure_state_dir(
    explicit: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    create: bool = True,
    require_owner: bool = True,
) -> Path:
    """Resolve a secure runtime state directory outside the repo and caches.

    Requirements:
    - not a symlink (path or ancestors)
    - directory mode ``0700`` (created if missing)
    - owned by the current user when ``require_owner``
    - not world-writable
    - not under the repository root or known plugin/cache markers
    """
    # Expand without resolve first so we can detect symlink components.
    if explicit is not None:
        pre = Path(explicit).expanduser()
    else:
        env = (os.getenv("DIGITAL_BRAIN_STATE_DIR") or "").strip()
        if env:
            pre = Path(env).expanduser()
        else:
            # Fall back to generation.resolve_state_dir (already resolved).
            pre = resolve_state_dir(None)

    if _path_has_symlink(pre):
        raise SecureStateDirError(f"state_dir_symlink_forbidden:{pre}")

    try:
        state = pre.resolve(strict=False)
    except OSError as exc:
        raise SecureStateDirError(f"state_dir_unresolvable:{pre}:{exc}") from exc

    if _path_has_symlink(state):
        raise SecureStateDirError(f"state_dir_symlink_forbidden:{state}")

    # Refuse repository / plugin / cache nesting.
    if repo_root is not None:
        try:
            repo = Path(repo_root).expanduser().resolve()
        except OSError as exc:
            raise SecureStateDirError(f"repo_root_unresolvable:{exc}") from exc
        if _is_under(state, repo):
            raise SecureStateDirError(f"state_dir_inside_repo:{state}")

    parts_lower = {p.lower() for p in state.parts}
    for marker in _FORBIDDEN_STATE_NAME_MARKERS:
        if marker in parts_lower:
            # Allow "digital-brain" state homes; only block nested plugin trees.
            if marker == "plugins":
                # e.g. .../plugins/... is never a valid state dir
                raise SecureStateDirError(f"state_dir_forbidden_marker:{marker}:{state}")
            if marker in ("node_modules", ".git", "__pycache__", ".cache"):
                raise SecureStateDirError(
                    f"state_dir_forbidden_marker:{marker}:{state}"
                )

    if state.exists():
        if not state.is_dir():
            raise SecureStateDirError(f"state_dir_not_directory:{state}")
        if state.is_symlink():
            raise SecureStateDirError(f"state_dir_symlink_forbidden:{state}")
        st = state.stat()
        if require_owner and hasattr(os, "getuid") and st.st_uid != os.getuid():
            raise SecureStateDirError(f"state_dir_unowned:{state}")
        if _is_world_writable(st.st_mode):
            raise SecureStateDirError(f"state_dir_world_writable:{state}")
        # Tighten to 0700 when we own the directory.
        if hasattr(os, "getuid") and st.st_uid == os.getuid():
            _ensure_mode_0700(state)
        mode = state.stat().st_mode
        if mode & 0o077:
            # group/other bits still set — refuse insecure shared dirs
            raise SecureStateDirError(f"state_dir_insecure_mode:{oct(mode & 0o777)}")
    elif create:
        try:
            state.mkdir(parents=True, mode=0o700, exist_ok=True)
        except OSError as exc:
            raise SecureStateDirError(f"state_dir_create_failed:{state}:{exc}") from exc
        # mkdir mode is masked by umask — force 0700.
        _ensure_mode_0700(state)
        if state.is_symlink():
            raise SecureStateDirError(f"state_dir_symlink_forbidden:{state}")
        st = state.stat()
        if require_owner and hasattr(os, "getuid") and st.st_uid != os.getuid():
            raise SecureStateDirError(f"state_dir_unowned:{state}")
    else:
        raise SecureStateDirError(f"state_dir_missing:{state}")

    return state


def quarantine_root(state_dir: Path) -> Path:
    return state_dir / QUARANTINE_REL


def quarantine_proposal_dir(
    state_dir: Path,
    dream_id: str,
    proposal_id: str,
) -> Path:
    dream = sanitize_path_segment(dream_id, field="dream_id")
    prop = sanitize_path_segment(proposal_id, field="proposal_id")
    root = quarantine_root(state_dir).resolve()
    target = (root / dream / prop).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ArtifactError(f"quarantine_path_escape:{target}") from exc
    return target


def _write_private_file(path: Path, data: bytes) -> None:
    """Write file with mode 0600, no execute bit, refuse symlinks."""
    if path.exists() or path.is_symlink():
        if path.is_symlink():
            raise ArtifactError(f"symlink_target_forbidden:{path}")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    # Atomic-ish write via temp file in the same directory.
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        os.write(fd, data)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.chmod(tmp_path, 0o600)
        # Refuse executable bits explicitly.
        mode = tmp_path.stat().st_mode
        if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            raise ArtifactError(f"executable_mode_forbidden:{tmp_path}")
        tmp_path.replace(path)
    except Exception:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    # Final check: no execute, not symlink.
    if path.is_symlink():
        raise ArtifactError(f"symlink_target_forbidden:{path}")
    final_mode = path.stat().st_mode
    if final_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        raise ArtifactError(f"executable_mode_forbidden:{path}")


def compute_file_checksums(files: Mapping[str, bytes]) -> dict[str, str]:
    return {name: digest_bytes(data) for name, data in sorted(files.items())}


def _as_utf8_bytes(value: str | bytes | Mapping[str, Any], *, label: str) -> bytes:
    """Normalize payload inputs to UTF-8 bytes (mappings → canonical JSON)."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, Mapping):
        return _canonical_json(dict(value)).encode("utf-8")
    raise TypeError(f"unsupported_patch_payload_type:{label}:{type(value).__name__}")


def manifest_core_without_patch(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return manifest fields that contribute to the patch digest (no self-hash)."""
    return {k: v for k, v in sorted(dict(manifest).items()) if k != "patch_sha256"}


def compute_patch_sha256(
    *,
    intent: Mapping[str, Any] | bytes,
    artifact_md: str | bytes,
    evaluation: Mapping[str, Any] | bytes,
    manifest: Mapping[str, Any] | bytes,
) -> str:
    """Single patch digest algorithm for compiler and on-disk quarantine.

    Digests the closed set of quarantine payload files excluding
    ``checksums.json`` and excluding ``patch_sha256`` from the manifest
    (so the digest can be embedded without circularity):

    - ``intent.json``
    - ``artifact.md``
    - ``evaluation.json``
    - manifest core (all manifest keys except ``patch_sha256``)

    Both :func:`digital_brain.maintenance.compiler.compile_change_intent` and
    :func:`write_quarantine_bundle` must call this function so the control-plane
    digest equals the on-disk ``manifest.patch_sha256`` / checksums binding.
    """
    intent_bytes = _as_utf8_bytes(intent, label="intent")
    artifact_bytes = _as_utf8_bytes(artifact_md, label="artifact_md")
    evaluation_bytes = _as_utf8_bytes(evaluation, label="evaluation")
    if isinstance(manifest, (bytes, str)):
        # Already-serialized core: treat as opaque bytes of the core document.
        core_bytes = _as_utf8_bytes(manifest, label="manifest")
    else:
        core_bytes = _canonical_json(manifest_core_without_patch(manifest)).encode(
            "utf-8"
        )
    return digest_text(
        _canonical_json(
            {
                ARTIFACT_FILENAME: digest_bytes(artifact_bytes),
                EVALUATION_FILENAME: digest_bytes(evaluation_bytes),
                INTENT_FILENAME: digest_bytes(intent_bytes),
                "manifest_core": digest_bytes(core_bytes),
            }
        )
    )


def build_manifest(
    *,
    proposal_id: str,
    dream_id: str,
    evidence_snapshot_id: str,
    target_skill: str | None,
    extension_slot: str | None,
    rule_id: str,
    base_commit: str,
    before_hashes: Mapping[str, str],
    target_file: str | None,
    compiler_version: str,
    schema_version: str,
    patch_sha256: str,
    artifact_relpath: str,
    expected_plugin_generation: str | None = None,
    rollback_ref: str | None = None,
    target_path_allowlist: Sequence[str] | None = None,
    lease_epoch: int | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    allowlist = list(target_path_allowlist or [])
    if target_file and target_file not in allowlist:
        allowlist.append(target_file)
    return {
        "artifact_path": artifact_relpath,
        "base_commit": base_commit,
        "before_hashes": dict(sorted((str(k), str(v)) for k, v in before_hashes.items())),
        "compiler_version": compiler_version,
        "dream_id": dream_id,
        "evidence_snapshot_id": evidence_snapshot_id,
        "expected_plugin_generation": expected_plugin_generation,
        "extension_slot": extension_slot,
        "lease_epoch": lease_epoch,
        "patch_sha256": patch_sha256,
        "proposal_id": proposal_id,
        "rollback_ref": rollback_ref,
        "rule_id": rule_id,
        "run_id": run_id,
        "schema_version": schema_version,
        "target_file": target_file,
        "target_path_allowlist": sorted(allowlist),
        "target_skill": target_skill,
    }


def _assert_size_limits(files: Mapping[str, bytes]) -> None:
    if len(files) > MAX_ARTIFACT_FILES:
        raise ArtifactError(f"file_count_overflow:{len(files)}")
    total = sum(len(v) for v in files.values())
    if total > MAX_ARTIFACT_BYTES:
        raise ArtifactError(f"size_overflow:{total}")
    for name in files:
        if name not in QUARANTINE_FILENAMES:
            raise ArtifactError(f"unknown_quarantine_filename:{name}")


def write_quarantine_bundle(
    *,
    state_dir: str | Path,
    dream_id: str,
    proposal_id: str,
    intent: Mapping[str, Any],
    artifact_md: str,
    manifest: Mapping[str, Any],
    evaluation: Mapping[str, Any] | None = None,
    metadata_id: str | None = None,
    repo_root: str | Path | None = None,
    create_state: bool = True,
) -> QuarantineBundle:
    """Write an immutable quarantine bundle. Same digest → replay; drift → error.

    Does **not** touch active-overlays, plugin skills, or any runtime load path.
    """
    secure = resolve_secure_state_dir(
        state_dir, repo_root=repo_root, create=create_state
    )
    directory = quarantine_proposal_dir(secure, dream_id, proposal_id)

    intent_obj = dict(intent)
    intent_bytes = _canonical_json(intent_obj).encode("utf-8")
    artifact_bytes = artifact_md.encode("utf-8")
    evaluation_obj = dict(evaluation or {})
    evaluation_bytes = _canonical_json(evaluation_obj).encode("utf-8")

    # Single shared algorithm with the compiler (control-plane == on-disk).
    manifest_obj = dict(manifest)
    declared = str(manifest_obj.get("patch_sha256") or "")
    patch_sha = compute_patch_sha256(
        intent=intent_obj,
        artifact_md=artifact_md,
        evaluation=evaluation_obj,
        manifest=manifest_obj,
    )
    if declared and declared not in _PATCH_SHA_PLACEHOLDERS and declared != patch_sha:
        raise ArtifactError(
            f"patch_sha256_mismatch:declared={declared}:computed={patch_sha}"
        )
    manifest_obj["patch_sha256"] = patch_sha
    manifest_bytes = _canonical_json(manifest_obj).encode("utf-8")

    files: dict[str, bytes] = {
        INTENT_FILENAME: intent_bytes,
        ARTIFACT_FILENAME: artifact_bytes,
        MANIFEST_FILENAME: manifest_bytes,
        EVALUATION_FILENAME: evaluation_bytes,
    }
    checksums = compute_file_checksums(files)
    checksums_bytes = _canonical_json(checksums).encode("utf-8")
    files[CHECKSUMS_FILENAME] = checksums_bytes
    _assert_size_limits(files)

    if directory.exists():
        if directory.is_symlink():
            raise ArtifactError(f"symlink_target_forbidden:{directory}")
        # Immutability: existing bundle must match exactly.
        existing_checksums_path = directory / CHECKSUMS_FILENAME
        if existing_checksums_path.is_file():
            try:
                existing = json.loads(existing_checksums_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ImmutableArtifactError(
                    f"corrupt_existing_quarantine:{directory}:{exc}"
                ) from exc
            if existing == checksums:
                return _bundle_from_disk(
                    directory=directory,
                    dream_id=dream_id,
                    proposal_id=proposal_id,
                    intent=dict(intent),
                    artifact_md=artifact_md,
                    manifest=manifest_obj,
                    evaluation=evaluation_obj,
                    checksums=checksums,
                    patch_sha256=patch_sha,
                    metadata_id=metadata_id,
                )
            raise ImmutableArtifactError(
                f"immutable_quarantine_drift:{dream_id}/{proposal_id}"
            )
        # Directory exists without checksums — refuse partial overwrite.
        raise ImmutableArtifactError(
            f"immutable_quarantine_partial_exists:{dream_id}/{proposal_id}"
        )

    # Create quarantine parents with 0700.
    directory.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        os.chmod(directory.parent, 0o700)
    except OSError:
        pass
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass

    for name, data in files.items():
        _write_private_file(directory / name, data)

    return _bundle_from_disk(
        directory=directory,
        dream_id=dream_id,
        proposal_id=proposal_id,
        intent=dict(intent),
        artifact_md=artifact_md,
        manifest=manifest_obj,
        evaluation=evaluation_obj,
        checksums=checksums,
        patch_sha256=patch_sha,
        metadata_id=metadata_id,
    )


def _bundle_from_disk(
    *,
    directory: Path,
    dream_id: str,
    proposal_id: str,
    intent: dict[str, Any],
    artifact_md: str,
    manifest: dict[str, Any],
    evaluation: dict[str, Any],
    checksums: dict[str, str],
    patch_sha256: str,
    metadata_id: str | None,
) -> QuarantineBundle:
    mid = metadata_id or f"patch-{patch_sha256[:24]}"
    meta = PatchArtifactMetadata(
        id=mid,
        proposal_id=proposal_id,
        evidence_snapshot_id=str(manifest.get("evidence_snapshot_id") or ""),
        base_commit=str(manifest.get("base_commit") or ""),
        before_hashes_json=_canonical_json(manifest.get("before_hashes") or {}),
        compiler_version=str(manifest.get("compiler_version") or COMPILER_VERSION_DEFAULT),
        schema_version=str(
            manifest.get("schema_version") or ARTIFACT_SCHEMA_VERSION
        ),
        target_path_allowlist_json=_canonical_json(
            manifest.get("target_path_allowlist") or []
        ),
        patch_sha256=patch_sha256,
        artifact_path=str(directory / ARTIFACT_FILENAME),
        expected_plugin_generation=manifest.get("expected_plugin_generation"),
        rollback_ref=manifest.get("rollback_ref"),
    )
    return QuarantineBundle(
        dream_id=dream_id,
        proposal_id=proposal_id,
        directory=directory,
        intent=intent,
        artifact_md=artifact_md,
        manifest=manifest,
        evaluation=evaluation,
        checksums=checksums,
        patch_sha256=patch_sha256,
        metadata=meta,
    )


def read_quarantine_manifest(
    state_dir: str | Path,
    dream_id: str,
    proposal_id: str,
) -> dict[str, Any] | None:
    """Read a quarantine manifest if present (review tooling only — not runtime)."""
    secure = resolve_secure_state_dir(state_dir, create=False)
    path = quarantine_proposal_dir(secure, dream_id, proposal_id) / MANIFEST_FILENAME
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def metadata_to_public_dict(meta: PatchArtifactMetadata) -> dict[str, Any]:
    return asdict(meta)


def assert_validation_commands_allowed(commands: Sequence[str]) -> tuple[str, ...]:
    """Refuse unknown commands before any filesystem work (no shell injection)."""
    if not commands:
        raise ValidationCommandError("validation_commands_required")
    normalized: list[str] = []
    for raw in commands:
        name = str(raw or "").strip()
        if not name:
            raise ValidationCommandError("empty_validation_command")
        # Hard reject shell / path injection patterns even before allowlist.
        if any(ch in name for ch in ("\n", "\r", ";", "|", "&", "$", "`", ">", "<")):
            raise ValidationCommandError(f"disallowed_validation_command:{name!r}")
        if name not in ISOLATED_VALIDATION_COMMANDS:
            raise ValidationCommandError(f"disallowed_validation_command:{name}")
        normalized.append(name)
    return tuple(normalized)


def _validation_work_root(
    *,
    state_dir: str | Path | None,
    repo_root: str | Path | None,
) -> Path:
    """Ephemeral work root under secure state (preferred) or a private temp dir."""
    if state_dir is not None:
        secure = resolve_secure_state_dir(
            state_dir, repo_root=repo_root, create=True
        )
        root = secure / VALIDATION_WORK_REL
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
        work = root / f"v-{uuid.uuid4().hex}"
        work.mkdir(mode=0o700)
        return work

    # No state dir: private temp outside the live plugin (caller must not pass plugin).
    work = Path(
        tempfile.mkdtemp(prefix="db-quarantine-validate-", suffix=".work")
    )
    try:
        os.chmod(work, 0o700)
    except OSError:
        pass
    return work


def _copy_quarantine_to_work(source_dir: Path, work_dir: Path) -> None:
    """Copy the closed quarantine file set into the isolated worktree."""
    if not source_dir.is_dir() or source_dir.is_symlink():
        raise IsolatedValidationError(f"invalid_quarantine_source:{source_dir}")
    for name in sorted(QUARANTINE_FILENAMES):
        src = source_dir / name
        if not src.is_file() or src.is_symlink():
            raise IsolatedValidationError(f"missing_quarantine_file:{name}")
        dest = work_dir / name
        # Byte-copy only; never follow symlinks into the live plugin.
        data = src.read_bytes()
        _write_private_file(dest, data)


def _cmd_json_tool(work_dir: Path, filename: str) -> dict[str, Any]:
    """Repository-owned equivalent of ``python -m json.tool <file>`` (no shell)."""
    if filename not in _JSON_QUARANTINE_FILES:
        raise ValidationCommandError(f"disallowed_json_tool_target:{filename}")
    path = work_dir / filename
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IsolatedValidationError(f"json_tool_failed:{filename}:{exc}") from exc
    # Round-trip check like json.tool success (well-formed + re-serializable).
    try:
        json.dumps(parsed, allow_nan=False, default=str)
    except (TypeError, ValueError) as exc:
        raise IsolatedValidationError(f"json_tool_failed:{filename}:{exc}") from exc
    return {"file": filename, "ok": True, "type": type(parsed).__name__}


def _cmd_recompute_checksums(work_dir: Path) -> dict[str, Any]:
    files: dict[str, bytes] = {}
    for name in (INTENT_FILENAME, ARTIFACT_FILENAME, MANIFEST_FILENAME, EVALUATION_FILENAME):
        path = work_dir / name
        files[name] = path.read_bytes()
    expected = compute_file_checksums(files)
    try:
        recorded = json.loads((work_dir / CHECKSUMS_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IsolatedValidationError(f"checksums_unreadable:{exc}") from exc
    if recorded != expected:
        raise IsolatedValidationError(
            f"checksum_mismatch:expected={expected}:recorded={recorded}"
        )
    return {"ok": True, "checksums": expected}


def _cmd_schema_check(work_dir: Path) -> dict[str, Any]:
    try:
        manifest = json.loads((work_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        intent = json.loads((work_dir / INTENT_FILENAME).read_text(encoding="utf-8"))
        evaluation = json.loads(
            (work_dir / EVALUATION_FILENAME).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IsolatedValidationError(f"schema_check_unreadable:{exc}") from exc
    if not isinstance(manifest, dict):
        raise IsolatedValidationError("schema_check:manifest_not_object")
    missing = sorted(_MANIFEST_REQUIRED_KEYS - set(manifest))
    if missing:
        raise IsolatedValidationError(f"schema_check:missing_keys:{missing}")
    patch = str(manifest.get("patch_sha256") or "")
    if len(patch) != 64 or any(c not in "0123456789abcdef" for c in patch.lower()):
        raise IsolatedValidationError("schema_check:invalid_patch_sha256")
    if not isinstance(intent, dict) or not intent:
        raise IsolatedValidationError("schema_check:intent_not_object")
    if not isinstance(evaluation, dict):
        raise IsolatedValidationError("schema_check:evaluation_not_object")
    artifact = work_dir / ARTIFACT_FILENAME
    if not artifact.is_file() or artifact.stat().st_size == 0:
        raise IsolatedValidationError("schema_check:empty_artifact")
    # Closed file set only.
    present = {p.name for p in work_dir.iterdir() if p.is_file()}
    if not present <= QUARANTINE_FILENAMES:
        raise IsolatedValidationError(
            f"schema_check:unknown_files:{sorted(present - QUARANTINE_FILENAMES)}"
        )
    if present != QUARANTINE_FILENAMES:
        raise IsolatedValidationError(
            f"schema_check:incomplete_bundle:{sorted(QUARANTINE_FILENAMES - present)}"
        )
    return {
        "ok": True,
        "schema_version": manifest.get("schema_version"),
        "compiler_version": manifest.get("compiler_version"),
    }


def _cmd_patch_digest_check(work_dir: Path) -> dict[str, Any]:
    try:
        manifest = json.loads((work_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        intent = json.loads((work_dir / INTENT_FILENAME).read_text(encoding="utf-8"))
        evaluation = json.loads(
            (work_dir / EVALUATION_FILENAME).read_text(encoding="utf-8")
        )
        artifact_md = (work_dir / ARTIFACT_FILENAME).read_text(encoding="utf-8")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IsolatedValidationError(f"patch_digest_unreadable:{exc}") from exc
    computed = compute_patch_sha256(
        intent=intent,
        artifact_md=artifact_md,
        evaluation=evaluation,
        manifest=manifest,
    )
    recorded = str(manifest.get("patch_sha256") or "")
    if computed != recorded:
        raise IsolatedValidationError(
            f"patch_digest_mismatch:computed={computed}:recorded={recorded}"
        )
    return {"ok": True, "patch_sha256": computed}


def _run_one_validation_command(work_dir: Path, command: str) -> dict[str, Any]:
    if command.startswith("json.tool:"):
        filename = command.split(":", 1)[1]
        return _cmd_json_tool(work_dir, filename)
    if command == "recompute_checksums":
        return _cmd_recompute_checksums(work_dir)
    if command == "schema_check":
        return _cmd_schema_check(work_dir)
    if command == "patch_digest_check":
        return _cmd_patch_digest_check(work_dir)
    # Unreachable when assert_validation_commands_allowed is used first.
    raise ValidationCommandError(f"disallowed_validation_command:{command}")


def validate_quarantine_isolated(
    source_dir: str | Path,
    *,
    commands: Sequence[str] | None = None,
    state_dir: str | Path | None = None,
    repo_root: str | Path | None = None,
    keep_work: bool = False,
) -> IsolatedValidationResult:
    """Validate a quarantine bundle in an isolated temp worktree.

    Copies the closed artifact set into an ephemeral directory under the secure
    state tree (``dreams/validation-work/``) or a private temp dir — never the
    live plugin. Runs only fixed allowlisted repository-owned command names;
    unknown commands are refused before any worktree is created.

    Does **not** invoke a shell. ``json.tool:<file>`` is a pure-Python equivalent
    of ``python -m json.tool`` on that fixed filename.
    """
    cmd_list = list(commands) if commands is not None else list(
        DEFAULT_ISOLATED_VALIDATION_COMMANDS
    )
    # Refuse disallowed commands before creating any worktree.
    allowed = assert_validation_commands_allowed(cmd_list)

    source = Path(source_dir)
    work: Path | None = None
    results: dict[str, Any] = {}
    errors: list[str] = []
    try:
        work = _validation_work_root(state_dir=state_dir, repo_root=repo_root)
        # Refuse worktrees that land under a plugin path (defense in depth).
        work_resolved = work.resolve()
        parts_lower = {p.lower() for p in work_resolved.parts}
        if "plugins" in parts_lower:
            raise IsolatedValidationError(
                f"validation_work_under_plugin_forbidden:{work_resolved}"
            )
        _copy_quarantine_to_work(source.resolve(), work)

        for command in allowed:
            try:
                results[command] = _run_one_validation_command(work, command)
            except (ValidationCommandError, IsolatedValidationError) as exc:
                errors.append(str(exc))
                results[command] = {"ok": False, "error": str(exc)}
            except Exception as exc:  # pragma: no cover - unexpected
                errors.append(f"validation_internal_error:{command}:{exc}")
                results[command] = {"ok": False, "error": str(exc)}

        ok = not errors
        if not ok:
            raise IsolatedValidationError(
                f"isolated_validation_failed:{';'.join(errors)}"
            )
        return IsolatedValidationResult(
            ok=True,
            work_dir=str(work),
            commands_run=allowed,
            results=results,
            errors=tuple(errors),
        )
    finally:
        if work is not None and not keep_work:
            shutil.rmtree(work, ignore_errors=True)

