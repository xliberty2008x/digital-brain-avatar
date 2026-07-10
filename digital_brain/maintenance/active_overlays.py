"""Active trial overlay manifest, atomic staging, and fail-closed load.

Runtime loads overlays **only** from
``$DIGITAL_BRAIN_STATE_DIR/dreams/active-overlays/`` via exact digests listed
in ``manifest.json``. Quarantine, plugin cache, and bare file presence never
activate behavior.

Session pin: validated manifest is pinned once per session so mid-session
manifest edits cannot change an existing session's loaded overlays.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from digital_brain.maintenance.artifacts import (
    ACTIVE_OVERLAYS_REL,
    sanitize_path_segment,
)
from digital_brain.maintenance.generation import resolve_state_dir, sanitize_session_id
from digital_brain.maintenance.models import EMPTY_DIGEST, digest_bytes, digest_text

ACTIVE_MANIFEST_SCHEMA_VERSION = "1"
MANIFEST_FILENAME = "manifest.json"
SESSION_OVERLAY_PIN_FILENAME = "active_overlays.json"

# Entry statuses that may be loaded into a session.
LOADABLE_ENTRY_STATUSES: frozenset[str] = frozenset({"trial_active"})
# Terminal / non-loadable.
DISABLED_ENTRY_STATUSES: frozenset[str] = frozenset(
    {"expired", "disabled", "rolled_back"}
)

ENTRY_STATUSES: frozenset[str] = LOADABLE_ENTRY_STATUSES | DISABLED_ENTRY_STATUSES


class ActiveOverlayError(ValueError):
    """Raised for invalid overlay paths, manifests, or rollback bindings."""


class ManifestMismatchError(ActiveOverlayError):
    """Raised when a manifest entry does not match on-disk content.

    Callers that load for runtime must catch this and fail closed — never
    partially load mismatched digests.
    """


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


@dataclass(frozen=True)
class ActiveOverlayEntry:
    """One reviewed trial overlay listed in the active manifest."""

    proposal_id: str
    digest: str
    rule_id: str
    extension_slot: str
    target_skill: str
    target_file: str
    trial_expires_at: str
    exposure_budget: int
    rollback_generation: str
    status: str = "trial_active"
    exposure_used: int = 0
    base_commit: str = ""
    artifact_hash: str = ""
    deployment_id: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ENTRY_STATUSES:
            raise ActiveOverlayError(f"invalid_entry_status:{self.status}")
        if not self.proposal_id or not self.digest or not self.rule_id:
            raise ActiveOverlayError("entry_missing_required_identity")
        if int(self.exposure_budget) < 0:
            raise ActiveOverlayError("exposure_budget_negative")
        if not self.trial_expires_at:
            raise ActiveOverlayError("trial_expires_at_required")
        if not self.rollback_generation:
            raise ActiveOverlayError("rollback_generation_required")


@dataclass(frozen=True)
class ActiveManifest:
    """Exact-digest active overlay manifest (runtime trial source of truth)."""

    schema_version: str
    entries: tuple[ActiveOverlayEntry, ...]
    prior_manifest_digest: str
    rollback_generation: str
    created_at: str
    generation_counter: int = 1
    fail_closed: bool = False
    fail_reason: str | None = None

    def loadable_entries(self) -> tuple[ActiveOverlayEntry, ...]:
        if self.fail_closed:
            return ()
        return tuple(e for e in self.entries if e.status in LOADABLE_ENTRY_STATUSES)


def empty_active_manifest(
    *,
    rollback_generation: str = EMPTY_DIGEST,
    created_at: str | None = None,
    prior_manifest_digest: str = EMPTY_DIGEST,
    generation_counter: int = 0,
    fail_closed: bool = False,
    fail_reason: str | None = None,
) -> ActiveManifest:
    return ActiveManifest(
        schema_version=ACTIVE_MANIFEST_SCHEMA_VERSION,
        entries=(),
        prior_manifest_digest=prior_manifest_digest,
        rollback_generation=rollback_generation or EMPTY_DIGEST,
        created_at=created_at or _utc_now_iso(),
        generation_counter=int(generation_counter),
        fail_closed=fail_closed,
        fail_reason=fail_reason,
    )


def entry_to_dict(entry: ActiveOverlayEntry) -> dict[str, Any]:
    return asdict(entry)


def entry_from_mapping(data: Mapping[str, Any]) -> ActiveOverlayEntry:
    return ActiveOverlayEntry(
        proposal_id=str(data["proposal_id"]),
        digest=str(data["digest"]),
        rule_id=str(data["rule_id"]),
        extension_slot=str(data.get("extension_slot") or ""),
        target_skill=str(data.get("target_skill") or ""),
        target_file=str(data.get("target_file") or ""),
        trial_expires_at=str(data["trial_expires_at"]),
        exposure_budget=int(data["exposure_budget"]),
        rollback_generation=str(data["rollback_generation"]),
        status=str(data.get("status") or "trial_active"),
        exposure_used=int(data.get("exposure_used") or 0),
        base_commit=str(data.get("base_commit") or ""),
        artifact_hash=str(data.get("artifact_hash") or data.get("digest") or ""),
        deployment_id=(
            None
            if data.get("deployment_id") in (None, "")
            else str(data.get("deployment_id"))
        ),
    )


def manifest_to_public_dict(manifest: ActiveManifest) -> dict[str, Any]:
    """Public/on-disk shape (no fail_closed bookkeeping)."""
    return {
        "created_at": manifest.created_at,
        "entries": [entry_to_dict(e) for e in manifest.entries],
        "generation_counter": int(manifest.generation_counter),
        "prior_manifest_digest": manifest.prior_manifest_digest,
        "rollback_generation": manifest.rollback_generation,
        "schema_version": manifest.schema_version,
    }


def manifest_from_mapping(data: Mapping[str, Any]) -> ActiveManifest:
    if not isinstance(data, Mapping):
        raise ActiveOverlayError("manifest_not_object")
    entries_raw = data.get("entries") or []
    if not isinstance(entries_raw, list):
        raise ActiveOverlayError("manifest_entries_not_array")
    entries = tuple(entry_from_mapping(e) for e in entries_raw)
    return ActiveManifest(
        schema_version=str(data.get("schema_version") or ACTIVE_MANIFEST_SCHEMA_VERSION),
        entries=entries,
        prior_manifest_digest=str(data.get("prior_manifest_digest") or EMPTY_DIGEST),
        rollback_generation=str(data.get("rollback_generation") or EMPTY_DIGEST),
        created_at=str(data.get("created_at") or _utc_now_iso()),
        generation_counter=int(data.get("generation_counter") or 0),
        fail_closed=bool(data.get("fail_closed") or False),
        fail_reason=(
            None if data.get("fail_reason") in (None, "") else str(data.get("fail_reason"))
        ),
    )


def compute_manifest_digest(manifest: ActiveManifest | Mapping[str, Any]) -> str:
    if isinstance(manifest, ActiveManifest):
        payload = manifest_to_public_dict(manifest)
    else:
        # Accept either full public dict or already-canonical mapping.
        payload = dict(manifest)
        payload.pop("fail_closed", None)
        payload.pop("fail_reason", None)
    return digest_text(_canonical_json(payload))


def active_overlays_root(state_dir: str | Path | None = None) -> Path:
    state = resolve_state_dir(state_dir)
    return state / ACTIVE_OVERLAYS_REL


def manifest_path(state_dir: str | Path | None = None) -> Path:
    return active_overlays_root(state_dir) / MANIFEST_FILENAME


def overlay_file_path(
    state_dir: str | Path | None,
    proposal_id: str,
    digest: str,
) -> Path:
    prop = sanitize_path_segment(proposal_id, field="proposal_id")
    dig = sanitize_path_segment(digest, field="digest")
    if not dig.endswith(".md"):
        dig = f"{dig}.md"
    # digest may already be hex; strip accidental .md from hex then re-add.
    stem = dig[: -3] if dig.endswith(".md") else dig
    # re-sanitize stem only
    stem = sanitize_path_segment(stem, field="digest")
    root = active_overlays_root(state_dir).resolve()
    target = (root / prop / f"{stem}.md").resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ActiveOverlayError(f"overlay_path_escape:{target}") from exc
    return target


def _write_private_file_fsync(path: Path, data: bytes) -> None:
    """Write with fsync and atomic replace within the same directory."""
    if path.is_symlink():
        raise ActiveOverlayError(f"symlink_target_forbidden:{path}")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
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
        mode = tmp_path.stat().st_mode
        if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            raise ActiveOverlayError(f"executable_mode_forbidden:{tmp_path}")
        # fsync directory after rename for durability on crash.
        tmp_path.replace(path)
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
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
    if path.is_symlink():
        raise ActiveOverlayError(f"symlink_target_forbidden:{path}")


def stage_overlay_content(
    *,
    state_dir: str | Path | None,
    proposal_id: str,
    content: str | bytes,
) -> tuple[Path, str]:
    """Stage reviewed overlay bytes under active-overlays/<proposal-id>/<digest>.md.

    Idempotent for the same content digest. Never writes quarantine or plugin
    paths. Staging alone has **no** runtime effect until the digest is listed
    in the active manifest.
    """
    if isinstance(content, str):
        data = content.encode("utf-8")
    else:
        data = content
    digest = digest_bytes(data)
    path = overlay_file_path(state_dir, proposal_id, digest)
    if path.is_file() and not path.is_symlink():
        existing = path.read_bytes()
        if digest_bytes(existing) == digest:
            return path, digest
        raise ActiveOverlayError(f"overlay_digest_collision:{path}")
    _write_private_file_fsync(path, data)
    return path, digest


def atomic_replace_manifest(
    *,
    state_dir: str | Path | None,
    manifest: ActiveManifest,
) -> str:
    """Atomically replace the active manifest (tmp + fsync + rename)."""
    public = manifest_to_public_dict(manifest)
    # Never persist fail_closed flags into the live file as truth; those are
    # load-time outcomes. On-disk empty entries mean known-good empty.
    data = (_canonical_json(public) + "\n").encode("utf-8")
    path = manifest_path(state_dir)
    _write_private_file_fsync(path, data)
    return compute_manifest_digest(public)


def load_raw_manifest(state_dir: str | Path | None = None) -> dict[str, Any] | None:
    path = manifest_path(state_dir)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _file_digest(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        return digest_bytes(path.read_bytes())
    except OSError:
        return None


def validate_manifest_against_files(
    state_dir: str | Path | None,
    manifest: ActiveManifest,
) -> ActiveManifest:
    """Validate every entry digest against the staged file.

    On any mismatch, raise :class:`ManifestMismatchError`. Does not fail open.
    """
    if manifest.schema_version != ACTIVE_MANIFEST_SCHEMA_VERSION:
        raise ManifestMismatchError(
            f"unsupported_manifest_schema:{manifest.schema_version}"
        )
    seen_rules: set[str] = set()
    seen_digests: set[str] = set()
    for entry in manifest.entries:
        if entry.rule_id in seen_rules:
            raise ManifestMismatchError(f"duplicate_rule_id:{entry.rule_id}")
        seen_rules.add(entry.rule_id)
        if entry.digest in seen_digests:
            raise ManifestMismatchError(f"duplicate_digest:{entry.digest}")
        seen_digests.add(entry.digest)
        path = overlay_file_path(state_dir, entry.proposal_id, entry.digest)
        live = _file_digest(path)
        if live is None:
            raise ManifestMismatchError(
                f"missing_overlay_file:{entry.proposal_id}:{entry.digest}"
            )
        if live != entry.digest:
            raise ManifestMismatchError(
                f"overlay_digest_mismatch:{entry.proposal_id}:{entry.digest}"
            )
        if entry.artifact_hash and entry.artifact_hash != entry.digest:
            raise ManifestMismatchError(
                f"artifact_hash_mismatch:{entry.proposal_id}"
            )
    return manifest


def load_validated_active_overlays(
    state_dir: str | Path | None = None,
) -> ActiveManifest:
    """Load and validate the active manifest; fail closed on any mismatch.

    Fail-closed returns an empty manifest with ``fail_closed=True`` and the
    prior ``rollback_generation`` when available — never a partial entry set.
    """
    raw = load_raw_manifest(state_dir)
    if raw is None:
        return empty_active_manifest()
    try:
        manifest = manifest_from_mapping(raw)
        return validate_manifest_against_files(state_dir, manifest)
    except (ActiveOverlayError, TypeError, ValueError, KeyError) as exc:
        rollback = EMPTY_DIGEST
        if isinstance(raw, dict):
            rollback = str(raw.get("rollback_generation") or EMPTY_DIGEST)
        return empty_active_manifest(
            rollback_generation=rollback,
            prior_manifest_digest=str(
                (raw or {}).get("prior_manifest_digest") or EMPTY_DIGEST
            ),
            fail_closed=True,
            fail_reason=str(exc),
        )


def resolve_loadable_overlays(
    state_dir: str | Path | None = None,
    *,
    manifest: ActiveManifest | None = None,
) -> list[dict[str, Any]]:
    """Return bodies only for manifest-listed, digest-matching, loadable entries.

    Presence of files without a loadable manifest entry yields nothing.
    """
    man = manifest if manifest is not None else load_validated_active_overlays(state_dir)
    if man.fail_closed:
        return []
    out: list[dict[str, Any]] = []
    for entry in man.loadable_entries():
        path = overlay_file_path(state_dir, entry.proposal_id, entry.digest)
        live = _file_digest(path)
        if live != entry.digest:
            # Fail closed for the whole load set — never partial.
            return []
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            return []
        out.append(
            {
                "proposal_id": entry.proposal_id,
                "digest": entry.digest,
                "rule_id": entry.rule_id,
                "extension_slot": entry.extension_slot,
                "target_skill": entry.target_skill,
                "target_file": entry.target_file,
                "body": body,
                "status": entry.status,
                "trial_expires_at": entry.trial_expires_at,
                "exposure_budget": entry.exposure_budget,
                "rollback_generation": entry.rollback_generation,
            }
        )
    return out


def session_overlay_pin_path(
    state_dir: str | Path | None,
    session_id: str,
) -> Path:
    state = resolve_state_dir(state_dir)
    safe = sanitize_session_id(session_id)
    return state / "sessions" / safe / SESSION_OVERLAY_PIN_FILENAME


def pin_session_active_overlays(
    *,
    state_dir: str | Path | None = None,
    session_id: str,
) -> ActiveManifest:
    """Validate live manifest and pin it for this session (once).

    Subsequent mid-session live-manifest changes do not alter the pin.
    """
    existing = load_session_active_overlays(state_dir=state_dir, session_id=session_id)
    if existing is not None:
        return existing
    validated = load_validated_active_overlays(state_dir)
    path = session_overlay_pin_path(state_dir, session_id)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    payload = manifest_to_public_dict(validated)
    payload["fail_closed"] = validated.fail_closed
    if validated.fail_reason:
        payload["fail_reason"] = validated.fail_reason
    payload["pinned_at"] = _utc_now_iso()
    _write_private_file_fsync(
        path, (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    return validated


def load_session_active_overlays(
    *,
    state_dir: str | Path | None = None,
    session_id: str,
) -> ActiveManifest | None:
    """Load a previously pinned session overlay set without revalidating live FS.

    Bodies are still digest-checked when resolved via
    :func:`resolve_loadable_overlays` against the pin's digests.
    """
    path = session_overlay_pin_path(state_dir, session_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return manifest_from_mapping(data)
    except (ActiveOverlayError, TypeError, ValueError, KeyError):
        return None


def replace_entries_status(
    manifest: ActiveManifest,
    *,
    proposal_id: str | None = None,
    new_status: str,
    now_iso: str | None = None,
) -> ActiveManifest:
    """Return a copy with matching entries set to ``new_status``."""
    if new_status not in ENTRY_STATUSES:
        raise ActiveOverlayError(f"invalid_entry_status:{new_status}")
    updated: list[ActiveOverlayEntry] = []
    for e in manifest.entries:
        if proposal_id is None or e.proposal_id == proposal_id:
            updated.append(
                ActiveOverlayEntry(
                    **{
                        **entry_to_dict(e),
                        "status": new_status,
                    }
                )
            )
        else:
            updated.append(e)
    return ActiveManifest(
        schema_version=manifest.schema_version,
        entries=tuple(updated),
        prior_manifest_digest=manifest.prior_manifest_digest,
        rollback_generation=manifest.rollback_generation,
        created_at=now_iso or _utc_now_iso(),
        generation_counter=manifest.generation_counter + 1,
        fail_closed=False,
        fail_reason=None,
    )


def prior_digest_for(manifest: ActiveManifest) -> str:
    """Digest used as the prior-ref: EMPTY_DIGEST when there are no entries."""
    if not manifest.entries:
        return EMPTY_DIGEST
    return compute_manifest_digest(manifest_to_public_dict(manifest))


def build_manifest_with_entry(
    *,
    prior: ActiveManifest,
    entry: ActiveOverlayEntry,
    rollback_generation: str | None = None,
    created_at: str | None = None,
) -> ActiveManifest:
    """Build next manifest adding/replacing entry by proposal_id; records prior digest."""
    prior_digest = prior_digest_for(prior)
    # Drop any existing entry for same proposal or same rule_id.
    kept = [
        e
        for e in prior.entries
        if e.proposal_id != entry.proposal_id and e.rule_id != entry.rule_id
    ]
    return ActiveManifest(
        schema_version=ACTIVE_MANIFEST_SCHEMA_VERSION,
        entries=tuple(kept) + (entry,),
        prior_manifest_digest=prior_digest,
        rollback_generation=rollback_generation or prior.rollback_generation or EMPTY_DIGEST,
        created_at=created_at or _utc_now_iso(),
        generation_counter=int(prior.generation_counter) + 1,
        fail_closed=False,
        fail_reason=None,
    )


def build_manifest_without_proposal(
    *,
    prior: ActiveManifest,
    proposal_id: str,
    created_at: str | None = None,
) -> ActiveManifest:
    prior_digest = prior_digest_for(prior)
    kept = tuple(e for e in prior.entries if e.proposal_id != proposal_id)
    return ActiveManifest(
        schema_version=ACTIVE_MANIFEST_SCHEMA_VERSION,
        entries=kept,
        prior_manifest_digest=prior_digest,
        rollback_generation=prior.rollback_generation,
        created_at=created_at or _utc_now_iso(),
        generation_counter=int(prior.generation_counter) + 1,
        fail_closed=False,
        fail_reason=None,
    )


def restore_prior_manifest(
    *,
    state_dir: str | Path | None,
    prior_manifest: ActiveManifest | Mapping[str, Any],
    expected_prior_digest: str,
) -> str:
    """Restore a prior known-good manifest (artifact-specific).

    Requires the caller-supplied prior payload to digest to
    ``expected_prior_digest`` so rollback cannot be artifact-agnostic.
    Empty priors may use the ``EMPTY_DIGEST`` sentinel.
    """
    if isinstance(prior_manifest, ActiveManifest):
        man = prior_manifest
        public = manifest_to_public_dict(man)
    else:
        man = manifest_from_mapping(prior_manifest)
        public = manifest_to_public_dict(man)

    # Empty prior: EMPTY_DIGEST sentinel or digest of empty public shape.
    if not man.entries:
        empty = empty_active_manifest(
            rollback_generation=man.rollback_generation,
            created_at=man.created_at,
            generation_counter=man.generation_counter,
        )
        empty_public_digest = compute_manifest_digest(manifest_to_public_dict(empty))
        if expected_prior_digest not in {EMPTY_DIGEST, empty_public_digest}:
            raise ActiveOverlayError(
                f"prior_manifest_digest_mismatch:expected={expected_prior_digest}"
                f":computed={EMPTY_DIGEST}"
            )
        atomic_replace_manifest(state_dir=state_dir, manifest=empty)
        return EMPTY_DIGEST

    live_digest = compute_manifest_digest(public)
    if live_digest != expected_prior_digest:
        raise ActiveOverlayError(
            f"prior_manifest_digest_mismatch:expected={expected_prior_digest}"
            f":computed={live_digest}"
        )
    validate_manifest_against_files(state_dir, man)
    return atomic_replace_manifest(state_dir=state_dir, manifest=man)
