"""Repository-owned overlay slot registry and locked-rule allowlists.

The compiler only renders additive rules into named extension slots defined here.
Locked safety rules cannot be revised or deleted by dream overlays.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from digital_brain.maintenance.analyzer import EXTENSION_SLOTS

OVERLAY_SLOTS_SCHEMA_VERSION = "1"
LOCKED_RULES_SCHEMA_VERSION = "1"

# Default location relative to the digital-brain-buddy plugin root.
QUALITY_DIR_REL = Path("quality")
OVERLAY_SLOTS_FILENAME = "overlay-slots.json"
LOCKED_RULES_FILENAME = "locked-rules.json"

# Hard limits for a single compiled artifact (bytes / files).
MAX_ARTIFACT_BYTES = 32_768
MAX_ARTIFACT_FILES = 5
MAX_RULE_BODY_BYTES = 4_096
MAX_SUMMARY_BYTES = 1_024


class OverlayRulesError(ValueError):
    """Raised when slot/locked-rule configuration is invalid or a rule is rejected."""


@dataclass(frozen=True)
class OverlaySlot:
    id: str
    target_skill: str | None
    target_file: str | None
    marker_begin: str
    marker_end: str
    max_rules: int
    max_bytes: int
    operations: frozenset[str]
    description: str = ""


@dataclass(frozen=True)
class LockedRules:
    locked_rule_ids: frozenset[str]
    forbidden_path_prefixes: tuple[str, ...]
    forbidden_path_substrings: tuple[str, ...]
    schema_version: str = LOCKED_RULES_SCHEMA_VERSION


@dataclass(frozen=True)
class OverlaySlotRegistry:
    slots: Mapping[str, OverlaySlot]
    schema_version: str = OVERLAY_SLOTS_SCHEMA_VERSION

    def get(self, slot_id: str) -> OverlaySlot:
        slot = self.slots.get(slot_id)
        if slot is None:
            raise OverlayRulesError(f"unknown_extension_slot:{slot_id}")
        return slot

    def require_known(self, slot_id: str) -> OverlaySlot:
        if slot_id not in EXTENSION_SLOTS:
            raise OverlayRulesError(f"unknown_extension_slot:{slot_id}")
        return self.get(slot_id)


def _default_plugin_root() -> Path:
    # digital_brain/maintenance/overlay_rules.py → repo root parents[2]
    return Path(__file__).resolve().parents[2] / "plugins" / "digital-brain-buddy"


def quality_dir(plugin_root: str | Path | None = None) -> Path:
    root = Path(plugin_root).expanduser().resolve() if plugin_root else _default_plugin_root()
    return root / QUALITY_DIR_REL


def overlay_slots_path(plugin_root: str | Path | None = None) -> Path:
    return quality_dir(plugin_root) / OVERLAY_SLOTS_FILENAME


def locked_rules_path(plugin_root: str | Path | None = None) -> Path:
    return quality_dir(plugin_root) / LOCKED_RULES_FILENAME


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise OverlayRulesError(f"missing_quality_config:{path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OverlayRulesError(f"invalid_quality_config:{path}:{exc}") from exc
    if not isinstance(raw, dict):
        raise OverlayRulesError(f"quality_config_must_be_object:{path}")
    return raw


def parse_overlay_slots(data: Mapping[str, Any]) -> OverlaySlotRegistry:
    schema = str(data.get("schema_version") or OVERLAY_SLOTS_SCHEMA_VERSION)
    if schema != OVERLAY_SLOTS_SCHEMA_VERSION:
        raise OverlayRulesError(f"unsupported_overlay_slots_schema:{schema}")
    raw_slots = data.get("slots")
    if not isinstance(raw_slots, Mapping):
        raise OverlayRulesError("overlay_slots.slots_must_be_object")
    slots: dict[str, OverlaySlot] = {}
    for key, value in raw_slots.items():
        if not isinstance(value, Mapping):
            raise OverlayRulesError(f"slot_must_be_object:{key}")
        slot_id = str(value.get("id") or key)
        if slot_id != key:
            raise OverlayRulesError(f"slot_id_mismatch:{key}:{slot_id}")
        ops_raw = value.get("operations") or ["add_rule"]
        if not isinstance(ops_raw, Sequence) or isinstance(ops_raw, (str, bytes)):
            raise OverlayRulesError(f"slot_operations_invalid:{slot_id}")
        target_skill = value.get("target_skill")
        target_file = value.get("target_file")
        slots[slot_id] = OverlaySlot(
            id=slot_id,
            target_skill=str(target_skill) if target_skill else None,
            target_file=str(target_file) if target_file else None,
            marker_begin=str(value.get("marker_begin") or ""),
            marker_end=str(value.get("marker_end") or ""),
            max_rules=int(value.get("max_rules") or 32),
            max_bytes=int(value.get("max_bytes") or MAX_ARTIFACT_BYTES),
            operations=frozenset(str(o) for o in ops_raw),
            description=str(value.get("description") or ""),
        )
    # Registry must cover the analyzer closed set (except pure-engineering may be optional).
    missing = EXTENSION_SLOTS - frozenset(slots)
    if missing:
        raise OverlayRulesError(f"overlay_slots_missing:{sorted(missing)}")
    return OverlaySlotRegistry(slots=slots, schema_version=schema)


def parse_locked_rules(data: Mapping[str, Any]) -> LockedRules:
    schema = str(data.get("schema_version") or LOCKED_RULES_SCHEMA_VERSION)
    if schema != LOCKED_RULES_SCHEMA_VERSION:
        raise OverlayRulesError(f"unsupported_locked_rules_schema:{schema}")
    ids = data.get("locked_rule_ids") or []
    if not isinstance(ids, Sequence) or isinstance(ids, (str, bytes)):
        raise OverlayRulesError("locked_rule_ids_must_be_array")
    prefixes = data.get("forbidden_path_prefixes") or []
    substrings = data.get("forbidden_path_substrings") or []
    if not isinstance(prefixes, Sequence) or isinstance(prefixes, (str, bytes)):
        raise OverlayRulesError("forbidden_path_prefixes_must_be_array")
    if not isinstance(substrings, Sequence) or isinstance(substrings, (str, bytes)):
        raise OverlayRulesError("forbidden_path_substrings_must_be_array")
    return LockedRules(
        locked_rule_ids=frozenset(str(x) for x in ids),
        forbidden_path_prefixes=tuple(str(x) for x in prefixes),
        forbidden_path_substrings=tuple(str(x) for x in substrings),
        schema_version=schema,
    )


@lru_cache(maxsize=8)
def load_overlay_slots(plugin_root: str | None = None) -> OverlaySlotRegistry:
    path = overlay_slots_path(plugin_root)
    return parse_overlay_slots(_load_json(path))


@lru_cache(maxsize=8)
def load_locked_rules(plugin_root: str | None = None) -> LockedRules:
    path = locked_rules_path(plugin_root)
    return parse_locked_rules(_load_json(path))


def clear_overlay_rules_cache() -> None:
    load_overlay_slots.cache_clear()
    load_locked_rules.cache_clear()


def assert_rule_not_locked(rule_id: str, locked: LockedRules | None = None) -> None:
    rules = locked if locked is not None else load_locked_rules()
    if rule_id in rules.locked_rule_ids:
        raise OverlayRulesError(f"locked_rule_change_forbidden:{rule_id}")


def assert_path_allowed(
    relative_path: str | None,
    locked: LockedRules | None = None,
) -> str | None:
    """Reject path traversal, forbidden prefixes, and arbitrary includes."""
    if relative_path is None:
        return None
    path = relative_path.strip().replace("\\", "/")
    if not path:
        return None
    if path.startswith("/") or path.startswith("~"):
        raise OverlayRulesError(f"absolute_path_forbidden:{path}")
    rules = locked if locked is not None else load_locked_rules()
    for sub in rules.forbidden_path_substrings:
        if sub and sub in path:
            raise OverlayRulesError(f"forbidden_path_substring:{path}")
    # Normalize "a/../b" style traversal after substring check for "..".
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise OverlayRulesError(f"path_traversal_forbidden:{path}")
    lowered = path.lstrip("./")
    for prefix in rules.forbidden_path_prefixes:
        if lowered == prefix.rstrip("/") or lowered.startswith(prefix):
            raise OverlayRulesError(f"forbidden_path_prefix:{path}")
    return path


def render_additive_rule_body(
    *,
    rule_id: str,
    summary: str,
    expected_outcome: str,
    extension_slot: str,
    evidence_ids: Sequence[str],
) -> str:
    """Deterministic Markdown body for an additive overlay rule (no frontmatter)."""
    evid = ", ".join(sorted(str(e) for e in evidence_ids)) or "(none)"
    # Stable, plain Markdown — no YAML frontmatter, no tool/include directives.
    lines = [
        f"### Rule `{rule_id}`",
        "",
        f"- **slot:** `{extension_slot}`",
        f"- **summary:** {summary.strip()}",
        f"- **expected_outcome:** {expected_outcome.strip()}",
        f"- **evidence_ids:** {evid}",
        "",
        "This rule is additive and inert until an owner-approved active-manifest",
        "trial loads its digest. Presence under quarantine has no runtime effect.",
        "",
    ]
    body = "\n".join(lines)
    if len(body.encode("utf-8")) > MAX_RULE_BODY_BYTES:
        raise OverlayRulesError("rule_body_size_overflow")
    return body


def wrap_in_slot_markers(slot: OverlaySlot, body: str) -> str:
    """Wrap rule body with repository-owned slot markers (no YAML frontmatter)."""
    begin = slot.marker_begin or f"<!-- OVERLAY_SLOT:{slot.id} BEGIN -->"
    end = slot.marker_end or f"<!-- OVERLAY_SLOT:{slot.id} END -->"
    content = f"{begin}\n{body.rstrip()}\n{end}\n"
    if len(content.encode("utf-8")) > int(slot.max_bytes):
        raise OverlayRulesError(f"slot_size_overflow:{slot.id}")
    return content
