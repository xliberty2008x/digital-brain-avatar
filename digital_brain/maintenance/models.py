"""Typed harness generation identity (quality plane).

``HarnessGeneration`` is the exact version attribution for a session. SOUL
content never appears here — only a local content digest (``soul_sha``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping

# Schema of the HarnessGeneration record itself (not the life-graph schema).
HARNESS_SCHEMA_VERSION = "1"

# Evidence/route taxonomy used by future RunEvent / Feedback sensors.
TAXONOMY_VERSION = "1"

# Canonical empty digest (sha256 of zero bytes).
EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()

# Generation id prefix for stable, greppable foreign keys.
GENERATION_ID_PREFIX = "hg-"


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_text(text: str) -> str:
    return sha256_hex(text.encode("utf-8"))


def digest_bytes(data: bytes) -> str:
    return sha256_hex(data)


@dataclass(frozen=True)
class HarnessGeneration:
    """Exact harness generation pin for a session.

    Identity fields contribute to ``id`` / ``request_fingerprint``.
    ``created_at`` is bookkeeping only and must not change the generation id.
    """

    id: str
    core_commit: str
    core_tree_digest: str
    dirty_state_digest: str
    plugin_version: str
    soul_sha: str
    overlay_manifest_digest: str
    policy_digest: str
    mcp_version: str
    model_id: str | None
    schema_version: str
    taxonomy_version: str
    created_at: str | None = None

    def identity_payload(self) -> dict[str, Any]:
        """Canonical fields that define the generation id (no timestamps)."""
        return {
            "core_commit": self.core_commit,
            "core_tree_digest": self.core_tree_digest,
            "dirty_state_digest": self.dirty_state_digest,
            "mcp_version": self.mcp_version,
            "model_id": self.model_id,
            "overlay_manifest_digest": self.overlay_manifest_digest,
            "plugin_version": self.plugin_version,
            "policy_digest": self.policy_digest,
            "schema_version": self.schema_version,
            "soul_sha": self.soul_sha,
            "taxonomy_version": self.taxonomy_version,
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize for pin files / MCP — never includes SOUL content."""
        payload = asdict(self)
        # Defensive: strip any accidental soul-content keys if callers mutate.
        for forbidden in ("soul_content", "soul_text", "soul", "SOUL"):
            payload.pop(forbidden, None)
        return payload

    def to_record_params(self) -> dict[str, Any]:
        """Flat Neo4j property map for the Operational:HarnessGeneration node."""
        return {
            "id": self.id,
            "core_commit": self.core_commit,
            "core_tree_digest": self.core_tree_digest,
            "dirty_state_digest": self.dirty_state_digest,
            "plugin_version": self.plugin_version,
            "soul_sha": self.soul_sha,
            "overlay_manifest_digest": self.overlay_manifest_digest,
            "policy_digest": self.policy_digest,
            "mcp_version": self.mcp_version,
            "model_id": self.model_id,
            "schema_version": self.schema_version,
            "taxonomy_version": self.taxonomy_version,
            "request_fingerprint": generation_request_fingerprint(self),
            "created_at": self.created_at,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> HarnessGeneration:
        required = (
            "id",
            "core_commit",
            "core_tree_digest",
            "dirty_state_digest",
            "plugin_version",
            "soul_sha",
            "overlay_manifest_digest",
            "policy_digest",
            "mcp_version",
            "schema_version",
            "taxonomy_version",
        )
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(f"HarnessGeneration missing fields: {missing}")
        model_id = data.get("model_id")
        if model_id is not None and not isinstance(model_id, str):
            raise ValueError("model_id must be a string or null")
        return cls(
            id=str(data["id"]),
            core_commit=str(data["core_commit"]),
            core_tree_digest=str(data["core_tree_digest"]),
            dirty_state_digest=str(data["dirty_state_digest"]),
            plugin_version=str(data["plugin_version"]),
            soul_sha=str(data["soul_sha"]),
            overlay_manifest_digest=str(data["overlay_manifest_digest"]),
            policy_digest=str(data["policy_digest"]),
            mcp_version=str(data["mcp_version"]),
            model_id=model_id if model_id is None else str(model_id),
            schema_version=str(data["schema_version"]),
            taxonomy_version=str(data["taxonomy_version"]),
            created_at=(
                None
                if data.get("created_at") is None
                else str(data.get("created_at"))
            ),
        )


def generation_request_fingerprint(
    generation: HarnessGeneration | Mapping[str, Any],
) -> str:
    """Stable request fingerprint for replay-vs-conflict receipts."""
    if isinstance(generation, HarnessGeneration):
        payload = generation.identity_payload()
    else:
        # Allow dicts that may include id/created_at; only identity fields matter.
        tmp = HarnessGeneration.from_mapping(
            {
                **dict(generation),
                "id": generation.get("id") or "pending",
            }
        )
        payload = tmp.identity_payload()
    return digest_text(_canonical_json(payload))


def compute_generation_id(
    generation: HarnessGeneration | Mapping[str, Any],
) -> str:
    """Derive the stable generation id from identity fields only."""
    fingerprint = generation_request_fingerprint(generation)
    return f"{GENERATION_ID_PREFIX}{fingerprint}"


def build_harness_generation(
    *,
    core_commit: str,
    core_tree_digest: str,
    dirty_state_digest: str,
    plugin_version: str,
    soul_sha: str,
    overlay_manifest_digest: str,
    policy_digest: str,
    mcp_version: str,
    model_id: str | None = None,
    schema_version: str = HARNESS_SCHEMA_VERSION,
    taxonomy_version: str = TAXONOMY_VERSION,
    created_at: str | None = None,
    generation_id: str | None = None,
) -> HarnessGeneration:
    """Construct a HarnessGeneration with a deterministic id."""
    provisional = HarnessGeneration(
        id=generation_id or "pending",
        core_commit=core_commit,
        core_tree_digest=core_tree_digest,
        dirty_state_digest=dirty_state_digest,
        plugin_version=plugin_version,
        soul_sha=soul_sha,
        overlay_manifest_digest=overlay_manifest_digest,
        policy_digest=policy_digest,
        mcp_version=mcp_version,
        model_id=model_id,
        schema_version=schema_version,
        taxonomy_version=taxonomy_version,
        created_at=created_at,
    )
    gid = generation_id or compute_generation_id(provisional)
    return HarnessGeneration(
        id=gid,
        core_commit=provisional.core_commit,
        core_tree_digest=provisional.core_tree_digest,
        dirty_state_digest=provisional.dirty_state_digest,
        plugin_version=provisional.plugin_version,
        soul_sha=provisional.soul_sha,
        overlay_manifest_digest=provisional.overlay_manifest_digest,
        policy_digest=provisional.policy_digest,
        mcp_version=provisional.mcp_version,
        model_id=provisional.model_id,
        schema_version=provisional.schema_version,
        taxonomy_version=provisional.taxonomy_version,
        created_at=provisional.created_at,
    )
