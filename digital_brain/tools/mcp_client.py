# File: digital_brain/tools/mcp_client.py
"""
Direct MCP Client for deterministic Neo4j queries.
Bypasses LLM agents for reliable entity existence checks.
Includes retry logic for local MCP startup and temporary unavailability.
"""

import aiohttp
import asyncio
import json
import os
from typing import Any

from ..config import DEFAULT_LOCAL_MCP_URL, get_mcp_url

DEFAULT_MCP_URL = DEFAULT_LOCAL_MCP_URL

# Retry configuration for MCP startup or temporary unavailability.  A retry is
# safe only for read-style tools; a timed-out generic write may already have
# committed on Neo4j.
MAX_RETRIES = 1
INITIAL_DELAY = 1  # seconds
MCP_REQUEST_TIMEOUT_SECONDS = int(os.getenv("MCP_REQUEST_TIMEOUT_SECONDS", "25"))

_RETRY_SAFE_TOOLS = {
    "read_neo4j_cypher",
    "get_neo4j_schema",
    "get_journal_chain_head",
    "get_journal_append_receipt",
    "get_quality_receipt",
    "get_harness_generation",
}

# Quality sensor writes: never blind-retry; reconcile via get_quality_receipt.
_QUALITY_SENSOR_WRITE_TOOLS = frozenset(
    {
        "create_feedback",
        "revoke_feedback",
        "record_run_event",
        "record_harness_generation",
    }
)

MCP_PROTOCOL_VERSION = "2024-11-05"


class McpWriteOutcomeUnknown(RuntimeError):
    """A write may have committed after the client lost its response.

    Callers must reconcile an append through its stable ``append_key`` instead
    of resubmitting a fresh write.
    """

    def __init__(self, tool_name: str, arguments: dict[str, Any], cause: BaseException):
        self.tool_name = tool_name
        self.arguments = arguments
        self.__cause__ = cause
        super().__init__(
            f"MCP write outcome is unknown for {tool_name}; reconcile before retrying: {cause}"
        )


def _response_mentions_missing_session(text: str) -> bool:
    return "Missing session ID" in text or "mcp-session-id" in text


async def _parse_json_or_sse_response(response: aiohttp.ClientResponse) -> dict[str, Any]:
    # Read raw text to bypass strict mimetype checks of response.json()
    text = await response.text()

    # Try to parse as SSE (Server-Sent Events)
    for line in text.splitlines():
        if line.strip().startswith("data:"):
            try:
                data_str = line.strip()[5:].strip()
                result = json.loads(data_str)
                if "error" in result:
                    raise Exception(f"MCP error: {result['error']}")
                return result
            except json.JSONDecodeError:
                continue

    try:
        result = json.loads(text)
        if "error" in result:
            raise Exception(f"MCP error: {result['error']}")
        return result
    except json.JSONDecodeError as exc:
        raise Exception(f"Could not parse response (first 200 chars): {text[:200]}") from exc


async def _initialize_mcp_session(
    session: aiohttp.ClientSession,
    url: str,
) -> str:
    payload = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "avatar-digital-brain", "version": "0.1.0"},
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with session.post(url, json=payload, headers=headers) as response:
        if response.status not in (200, 202):
            error_text = await response.text()
            raise Exception(f"MCP initialize failed ({response.status}): {error_text}")
        session_id = response.headers.get("mcp-session-id")
        await _parse_json_or_sse_response(response)
    if not session_id:
        raise Exception("MCP initialize did not return mcp-session-id")

    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    async with session.post(
        url,
        json=initialized,
        headers={**headers, "mcp-session-id": session_id},
    ) as response:
        if response.status not in (200, 202):
            error_text = await response.text()
            raise Exception(f"MCP initialized notification failed ({response.status}): {error_text}")
        if response.status == 200:
            await response.text()
    return session_id


async def call_mcp_tool(
    tool_name: str,
    arguments: dict[str, Any],
    url: str | None = None,
    max_retries: int | None = None,
    initial_delay: int = INITIAL_DELAY
) -> dict[str, Any]:
    """
    Call an MCP tool directly via HTTP POST with retry logic.
    
    Args:
        tool_name: Name of the MCP tool (e.g., 'read_neo4j_cypher')
        arguments: Tool arguments as a dictionary
        url: MCP server endpoint
        max_retries: Maximum number of retry attempts.  Defaults to one retry
            for read-style tools and no retry for writes.
        initial_delay: Initial delay between retries (doubles each attempt)
    
    Returns:
        Tool response as a dictionary
    """
    endpoint = url or get_mcp_url()
    retry_safe = tool_name in _RETRY_SAFE_TOOLS
    if max_retries is None:
        max_retries = MAX_RETRIES if retry_safe else 0
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }
    
    last_error = None
    delay = initial_delay
    mcp_session_id: str | None = None
    
    for attempt in range(max_retries + 1):
        try:
            timeout = aiohttp.ClientTimeout(total=MCP_REQUEST_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                if mcp_session_id is None:
                    mcp_session_id = await _initialize_mcp_session(session, endpoint)
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "mcp-session-id": mcp_session_id,
                }
                async with session.post(
                    endpoint,
                    json=payload,
                    headers=headers,
                ) as response:
                    if response.status == 200:
                        result = await _parse_json_or_sse_response(response)
                        tool_result = result.get("result", {})
                        if isinstance(tool_result, dict) and tool_result.get("isError"):
                            content = tool_result.get("content") or []
                            error_text = (
                                content[0].get("text", "")
                                if content and isinstance(content[0], dict)
                                else str(tool_result)
                            )
                            raise Exception(f"MCP tool '{tool_name}' returned an error: {error_text}")
                        return tool_result

                    elif response.status in [502, 503, 504]:
                        # Cold start or temporary unavailability
                        error_text = await response.text()
                        raise aiohttp.ClientError(f"Server warming up ({response.status}): {error_text}")
                    else:
                        error_text = await response.text()
                        if response.status == 400 and _response_mentions_missing_session(error_text):
                            mcp_session_id = None
                            raise aiohttp.ClientError(f"MCP session unavailable ({response.status}): {error_text}")
                        raise Exception(f"MCP call failed ({response.status}): {error_text}")
                        
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
            last_error = e
            if attempt < max_retries:
                print(f"⏳ MCP cold start... waiting {delay}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                print(f"❌ MCP call failed after {max_retries} retries: {e}")
                if not retry_safe:
                    raise McpWriteOutcomeUnknown(tool_name, arguments, e) from e
                raise
        except Exception as e:
            # Non-retryable error
            raise
    
    raise last_error or Exception("MCP call failed with unknown error")


def _tool_content_json(result: dict[str, Any]) -> dict[str, Any]:
    """Decode the JSON payload returned by an MCP tool."""
    content = result.get("content") if isinstance(result, dict) else None
    if not isinstance(content, list) or not content:
        raise ValueError(f"MCP tool returned no content: {result}")
    text = content[0].get("text") if isinstance(content[0], dict) else None
    if not isinstance(text, str):
        raise ValueError(f"MCP tool returned invalid content: {result}")
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        raise ValueError(f"MCP tool returned non-object JSON: {decoded!r}")
    return decoded


async def get_journal_chain_head() -> dict[str, Any]:
    """Read the server-owned primary JournalEntry chain head."""
    return _tool_content_json(await call_mcp_tool("get_journal_chain_head", {}))


async def get_journal_append_receipt(append_key: str) -> dict[str, Any]:
    """Reconcile a possibly timed-out append without issuing another write."""
    return _tool_content_json(
        await call_mcp_tool("get_journal_append_receipt", {"append_key": append_key})
    )


async def append_journal_entry(
    *,
    append_key: str,
    content: str,
    timestamp: str,
    mood: str | None,
    expected_version: int,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one JournalEntry through the server-owned CAS protocol.

    This function deliberately does not retry a failed request.  On
    ``McpWriteOutcomeUnknown``, call :func:`get_journal_append_receipt` (or
    submit this exact payload again only after reconciliation).
    """
    arguments: dict[str, Any] = {
        "append_key": append_key,
        "content": content,
        "timestamp": timestamp,
        "mood": mood,
        "expected_version": expected_version,
    }
    if properties:
        arguments["properties"] = properties
    return _tool_content_json(await call_mcp_tool("append_journal_entry", arguments))


async def get_harness_generation(generation_id: str) -> dict[str, Any]:
    """Reconcile a session harness generation pin against the quality ledger."""
    return _tool_content_json(
        await call_mcp_tool("get_harness_generation", {"generation_id": generation_id})
    )


async def record_harness_generation(generation: dict[str, Any]) -> dict[str, Any]:
    """Record a HarnessGeneration through the quality-plane MCP tool.

    Callers must not include SOUL content — only digests/public fields.
    On ``McpWriteOutcomeUnknown``, reconcile with :func:`get_harness_generation`
    rather than blindly retrying a conflicting payload.
    """
    if not isinstance(generation, dict):
        raise TypeError("generation must be a dict of public harness fields")
    for forbidden in ("soul_content", "soul_text", "soul", "SOUL"):
        if forbidden in generation:
            raise ValueError(
                f"SOUL content must not be sent to record_harness_generation ({forbidden})"
            )
    fingerprint = generation.get("request_fingerprint")
    if not fingerprint:
        gen_id = generation.get("id")
        if isinstance(gen_id, str) and gen_id.startswith("hg-"):
            fingerprint = gen_id[3:]
    if not fingerprint:
        raise ValueError("generation.request_fingerprint is required")
    arguments: dict[str, Any] = {
        "id": generation["id"],
        "core_commit": generation["core_commit"],
        "core_tree_digest": generation["core_tree_digest"],
        "dirty_state_digest": generation["dirty_state_digest"],
        "plugin_version": generation["plugin_version"],
        "soul_sha": generation["soul_sha"],
        "overlay_manifest_digest": generation["overlay_manifest_digest"],
        "policy_digest": generation["policy_digest"],
        "mcp_version": generation["mcp_version"],
        "schema_version": generation["schema_version"],
        "taxonomy_version": generation["taxonomy_version"],
        "request_fingerprint": fingerprint,
    }
    if generation.get("model_id") is not None:
        arguments["model_id"] = generation["model_id"]
    if generation.get("created_at") is not None:
        arguments["created_at"] = generation["created_at"]
    try:
        return _tool_content_json(
            await call_mcp_tool("record_harness_generation", arguments)
        )
    except McpWriteOutcomeUnknown:
        gen_id = str(arguments["id"])
        found = await get_harness_generation(gen_id)
        if found.get("outcome") == "ok":
            return {**found, "reconciled": True}
        raise


async def get_quality_receipt(receipt_id: str) -> dict[str, Any]:
    """Reconcile a quality write by stable id without issuing another write."""
    return _tool_content_json(
        await call_mcp_tool("get_quality_receipt", {"receipt_id": receipt_id})
    )


def _session_harness_generation_id(explicit: str | None = None) -> str:
    """Prefer explicit id; else require DIGITAL_BRAIN_HARNESS_GENERATION_ID."""
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    pinned = (os.getenv("DIGITAL_BRAIN_HARNESS_GENERATION_ID") or "").strip()
    if not pinned:
        raise ValueError(
            "harness_generation_id is required "
            "(set DIGITAL_BRAIN_HARNESS_GENERATION_ID or pass explicitly)"
        )
    return pinned


async def _quality_write_with_receipt_reconcile(
    tool_name: str,
    arguments: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    """Issue a quality sensor write; on unknown outcome reconcile by receipt.

    Never blind-retries the write. Timeout/transport failure → receipt lookup.
    """
    if tool_name not in _QUALITY_SENSOR_WRITE_TOOLS and tool_name != "create_feedback":
        pass  # allowlist is documentation; callers control tool_name
    try:
        return _tool_content_json(await call_mcp_tool(tool_name, arguments))
    except McpWriteOutcomeUnknown:
        receipt = await get_quality_receipt(receipt_id)
        if receipt.get("outcome") == "ok":
            return {**receipt, "reconciled": True}
        raise


async def create_feedback(
    *,
    id: str,
    kind: str,
    sensitivity: str,
    harness_generation_id: str | None = None,
    source_turn_ref: str | None = None,
    redacted_summary: str | None = None,
    raw_payload: str | None = None,
    schema_version: str | None = None,
    taxonomy_version: str | None = None,
    request_fingerprint: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Create Feedback through the quality-plane MCP tool.

    On ``McpWriteOutcomeUnknown``, reconciles via :func:`get_quality_receipt`
    instead of blind-retrying.
    """
    arguments: dict[str, Any] = {
        "id": id,
        "kind": kind,
        "sensitivity": sensitivity,
        "harness_generation_id": _session_harness_generation_id(harness_generation_id),
    }
    if source_turn_ref is not None:
        arguments["source_turn_ref"] = source_turn_ref
    if redacted_summary is not None:
        arguments["redacted_summary"] = redacted_summary
    if raw_payload is not None:
        arguments["raw_payload"] = raw_payload
    if schema_version is not None:
        arguments["schema_version"] = schema_version
    if taxonomy_version is not None:
        arguments["taxonomy_version"] = taxonomy_version
    if request_fingerprint is not None:
        arguments["request_fingerprint"] = request_fingerprint
    if created_at is not None:
        arguments["created_at"] = created_at
    return await _quality_write_with_receipt_reconcile(
        "create_feedback", arguments, str(id)
    )


async def revoke_feedback(
    *,
    id: str,
    feedback_id: str,
    actor: str,
    reason_code: str | None = None,
    request_fingerprint: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Revoke Feedback via lifecycle event; reconcile by receipt on timeout."""
    arguments: dict[str, Any] = {
        "id": id,
        "feedback_id": feedback_id,
        "actor": actor,
    }
    if reason_code is not None:
        arguments["reason_code"] = reason_code
    if request_fingerprint is not None:
        arguments["request_fingerprint"] = request_fingerprint
    if created_at is not None:
        arguments["created_at"] = created_at
    return await _quality_write_with_receipt_reconcile(
        "revoke_feedback", arguments, str(id)
    )


async def record_run_event(
    *,
    id: str,
    route: str,
    tool_outcome: str,
    harness_generation_id: str | None = None,
    tool: str | None = None,
    outcome_source: str | None = None,
    task_outcome: str | None = None,
    approach: str | None = None,
    error_class: str | None = None,
    decision_point: str | None = None,
    eligible_exposure: bool | None = None,
    entity_refs: list[str] | None = None,
    journal_refs: list[str] | None = None,
    redacted_summary: str | None = None,
    sensitivity: str | None = None,
    recurrence_key: str | None = None,
    session_ref: str | None = None,
    host: str | None = None,
    trace_id: str | None = None,
    attempt_id: str | None = None,
    latency_ms: int | None = None,
    observed_at: str | None = None,
    schema_version: str | None = None,
    taxonomy_version: str | None = None,
    request_fingerprint: str | None = None,
    plugin_version: str | None = None,
    policy_digest: str | None = None,
    mcp_version: str | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    """Record a model-facing RunEvent (server forces model_advisory source).

    On timeout, reconciles via :func:`get_quality_receipt` — never blind-retries.
    Deterministic MCP/host outcomes must use the server-side trusted recorder,
    not this model-facing path.
    """
    arguments: dict[str, Any] = {
        "id": id,
        "harness_generation_id": _session_harness_generation_id(harness_generation_id),
        "route": route,
        "tool_outcome": tool_outcome,
    }
    optional: dict[str, Any] = {
        "tool": tool,
        "outcome_source": outcome_source,
        "task_outcome": task_outcome,
        "approach": approach,
        "error_class": error_class,
        "decision_point": decision_point,
        "eligible_exposure": eligible_exposure,
        "entity_refs": entity_refs,
        "journal_refs": journal_refs,
        "redacted_summary": redacted_summary,
        "sensitivity": sensitivity,
        "recurrence_key": recurrence_key,
        "session_ref": session_ref,
        "host": host,
        "trace_id": trace_id,
        "attempt_id": attempt_id,
        "latency_ms": latency_ms,
        "observed_at": observed_at,
        "schema_version": schema_version,
        "taxonomy_version": taxonomy_version,
        "request_fingerprint": request_fingerprint,
        "plugin_version": plugin_version,
        "policy_digest": policy_digest,
        "mcp_version": mcp_version,
        "model_id": model_id,
    }
    for key, value in optional.items():
        if value is not None:
            arguments[key] = value
    return await _quality_write_with_receipt_reconcile(
        "record_run_event", arguments, str(id)
    )


async def execute_cypher(query: str, params: dict = None, embed_text: str | None = None) -> list[dict]:
    """
    Execute a Cypher query directly via MCP with retry logic.
    
    Args:
        query: Cypher query string
        params: Query parameters
    
    Returns:
        List of result records
    """
    arguments = {"query": query}
    if params:
        arguments["params"] = params  # Pass dict directly, not JSON string!
    if embed_text:
        arguments["embed_text"] = embed_text
    
    result = await call_mcp_tool("read_neo4j_cypher", arguments)
    
    # Parse the result content
    if isinstance(result, dict) and "content" in result:
        content = result["content"]
        if isinstance(content, list) and len(content) > 0:
            text = content[0].get("text", "[]")
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise Exception(f"Could not parse Cypher result (first 200 chars): {text[:200]}") from exc

    return []
