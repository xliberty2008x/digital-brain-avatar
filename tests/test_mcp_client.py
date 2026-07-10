from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import aiohttp

from digital_brain.tools.mcp_client import (
    append_journal_entry,
    call_mcp_tool,
    create_feedback,
    execute_cypher,
    get_journal_append_receipt,
    get_journal_chain_head,
    get_quality_receipt,
    record_run_event,
    revoke_feedback,
    set_host_deterministic_run_event_recorder,
    McpWriteOutcomeUnknown,
)


class _FailingRequest:
    async def __aenter__(self):
        raise aiohttp.ClientConnectionError("offline")

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FailingSession:
    def __init__(self, *args, **kwargs):
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        self.calls += 1
        return _FailingRequest()


class McpClientTests(IsolatedAsyncioTestCase):
    async def test_execute_cypher_passes_embed_text_to_read_tool(self) -> None:
        with patch(
            "digital_brain.tools.mcp_client.call_mcp_tool",
            new=AsyncMock(return_value={"content": [{"type": "text", "text": '[{"id": "j-1"}]'}]}),
        ) as mocked_call:
            rows = await execute_cypher(
                "CALL db.index.vector.queryNodes('journal_entry_embedding_index', $limit, $embedding) "
                "YIELD node RETURN node.id AS id",
                {"limit": 5},
                embed_text="semantic query",
            )

        self.assertEqual(rows, [{"id": "j-1"}])
        mocked_call.assert_awaited_once_with(
            "read_neo4j_cypher",
            {
                "query": "CALL db.index.vector.queryNodes('journal_entry_embedding_index', $limit, $embedding) "
                "YIELD node RETURN node.id AS id",
                "params": {"limit": 5},
                "embed_text": "semantic query",
            },
        )

    async def test_append_uses_one_stable_payload_and_decodes_receipt(self) -> None:
        response = {
            "content": [
                {
                    "type": "text",
                    "text": '{"outcome":"created","append_key":"key-1","journal_id":"journal-key-1","version":2}',
                }
            ]
        }
        with patch(
            "digital_brain.tools.mcp_client.call_mcp_tool",
            new=AsyncMock(return_value=response),
        ) as mocked_call:
            receipt = await append_journal_entry(
                append_key="key-1",
                content="memory",
                timestamp="2026-07-09T00:00:00Z",
                mood="calm",
                expected_version=1,
                properties={"source": "test"},
            )

        self.assertEqual(receipt["outcome"], "created")
        mocked_call.assert_awaited_once_with(
            "append_journal_entry",
            {
                "append_key": "key-1",
                "content": "memory",
                "timestamp": "2026-07-09T00:00:00Z",
                "mood": "calm",
                "expected_version": 1,
                "properties": {"source": "test"},
            },
        )

    async def test_chain_reads_and_receipt_are_decoded(self) -> None:
        responses = [
            {
                "content": [
                    {
                        "type": "text",
                        "text": '{"outcome":"ok","version":4,"journal_id":"journal-head"}',
                    }
                ]
            },
            {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            '{"outcome":"found","append_key":"key-1",'
                            '"journal_id":"journal-key-1","version":2}'
                        ),
                    }
                ]
            },
        ]
        with patch(
            "digital_brain.tools.mcp_client.call_mcp_tool",
            new=AsyncMock(side_effect=responses),
        ) as mocked_call:
            self.assertEqual((await get_journal_chain_head())["version"], 4)
            self.assertEqual((await get_journal_append_receipt("key-1"))["outcome"], "found")

        self.assertEqual(
            mocked_call.await_args_list[0].args,
            ("get_journal_chain_head", {}),
        )
        self.assertEqual(
            mocked_call.await_args_list[1].args,
            ("get_journal_append_receipt", {"append_key": "key-1"}),
        )

    async def test_transport_failure_on_generic_write_is_outcome_unknown_without_retry(self) -> None:
        session = _FailingSession()
        with patch(
            "digital_brain.tools.mcp_client.aiohttp.ClientSession",
            return_value=session,
        ):
            with self.assertRaises(McpWriteOutcomeUnknown):
                await call_mcp_tool("write_neo4j_cypher", {"query": "CREATE (n:Topic {id: 'x'})"})

        self.assertEqual(session.calls, 1)

    async def test_transport_failure_on_append_is_outcome_unknown_without_retry(self) -> None:
        session = _FailingSession()
        with patch(
            "digital_brain.tools.mcp_client.aiohttp.ClientSession",
            return_value=session,
        ):
            with self.assertRaises(McpWriteOutcomeUnknown):
                await call_mcp_tool(
                    "append_journal_entry",
                    {
                        "append_key": "00000000-0000-4000-8000-000000000001",
                        "content": "memory",
                        "timestamp": "2026-07-09T00:00:00Z",
                        "expected_version": 0,
                    },
                )

        self.assertEqual(session.calls, 1)

    async def test_write_timeout_call_site_emits_host_run_event(self) -> None:
        """Host path: McpWriteOutcomeUnknown on write tools records host RunEvent."""
        recorded: list[dict] = []

        def recorder(event: dict) -> dict:
            recorded.append(event)
            return {"outcome": "created", "run_event_id": event.get("id")}

        set_host_deterministic_run_event_recorder(recorder)
        session = _FailingSession()
        try:
            with patch.dict(
                "os.environ",
                {"DIGITAL_BRAIN_HARNESS_GENERATION_ID": "hg-host-pin"},
                clear=False,
            ):
                with patch(
                    "digital_brain.tools.mcp_client.aiohttp.ClientSession",
                    return_value=session,
                ):
                    with self.assertRaises(McpWriteOutcomeUnknown):
                        await call_mcp_tool(
                            "append_journal_entry",
                            {
                                "append_key": "00000000-0000-4000-8000-000000000002",
                                "content": "memory",
                                "timestamp": "2026-07-09T00:00:00Z",
                                "expected_version": 0,
                            },
                        )
        finally:
            set_host_deterministic_run_event_recorder(None)

        self.assertEqual(len(recorded), 1)
        event = recorded[0]
        self.assertEqual(event["tool"], "append_journal_entry")
        self.assertEqual(event["tool_outcome"], "timeout")
        self.assertEqual(event["route"], "WRITE")
        self.assertEqual(event["outcome_source"], "host")
        self.assertEqual(event["harness_generation_id"], "hg-host-pin")
        self.assertIn(event.get("error_class"), {"mcp_timeout", "mcp_transport_unknown"})

    async def test_write_timeout_instrumentation_failure_still_raises_unknown(
        self,
    ) -> None:
        """Best-effort: broken host recorder must not swallow McpWriteOutcomeUnknown."""

        def boom(_event: dict) -> dict:
            raise RuntimeError("host quality store down")

        set_host_deterministic_run_event_recorder(boom)
        session = _FailingSession()
        try:
            with patch.dict(
                "os.environ",
                {"DIGITAL_BRAIN_HARNESS_GENERATION_ID": "hg-host-pin"},
                clear=False,
            ):
                with patch(
                    "digital_brain.tools.mcp_client.aiohttp.ClientSession",
                    return_value=session,
                ):
                    with self.assertRaises(McpWriteOutcomeUnknown):
                        await call_mcp_tool(
                            "write_neo4j_cypher",
                            {"query": "MERGE (t:Topic {id: 'x'})"},
                        )
        finally:
            set_host_deterministic_run_event_recorder(None)

    async def test_create_feedback_passes_stable_payload(self) -> None:
        response = {
            "content": [
                {
                    "type": "text",
                    "text": (
                        '{"outcome":"created","feedback_id":"fb-1",'
                        '"harness_generation_id":"hg-pin"}'
                    ),
                }
            ]
        }
        with patch(
            "digital_brain.tools.mcp_client.call_mcp_tool",
            new=AsyncMock(return_value=response),
        ) as mocked_call:
            receipt = await create_feedback(
                id="fb-1",
                kind="entity_wrong",
                sensitivity="personal",
                harness_generation_id="hg-pin",
                redacted_summary="not that",
                raw_payload="raw note",
            )

        self.assertEqual(receipt["outcome"], "created")
        mocked_call.assert_awaited_once_with(
            "create_feedback",
            {
                "id": "fb-1",
                "kind": "entity_wrong",
                "sensitivity": "personal",
                "harness_generation_id": "hg-pin",
                "redacted_summary": "not that",
                "raw_payload": "raw note",
            },
        )

    async def test_record_run_event_uses_session_env_generation_id(self) -> None:
        response = {
            "content": [
                {
                    "type": "text",
                    "text": (
                        '{"outcome":"created","run_event_id":"re-1",'
                        '"outcome_source":"model_advisory"}'
                    ),
                }
            ]
        }
        with patch.dict(
            "os.environ",
            {"DIGITAL_BRAIN_HARNESS_GENERATION_ID": "hg-from-env"},
            clear=False,
        ):
            with patch(
                "digital_brain.tools.mcp_client.call_mcp_tool",
                new=AsyncMock(return_value=response),
            ) as mocked_call:
                receipt = await record_run_event(
                    id="re-1",
                    route="WRITE",
                    tool_outcome="timeout",
                    tool="append_journal_entry",
                    outcome_source="mcp",
                )

        self.assertEqual(receipt["outcome"], "created")
        args = mocked_call.await_args.args
        self.assertEqual(args[0], "record_run_event")
        self.assertEqual(args[1]["harness_generation_id"], "hg-from-env")
        self.assertEqual(args[1]["outcome_source"], "mcp")

    async def test_quality_write_timeout_reconciles_via_receipt_not_retry(self) -> None:
        async def side_effect(tool_name, arguments, **_kwargs):
            if tool_name == "create_feedback":
                raise McpWriteOutcomeUnknown(tool_name, arguments, TimeoutError("t"))
            if tool_name == "get_quality_receipt":
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                '{"outcome":"ok","receipt_id":"fb-timeout",'
                                '"record_type":"Feedback","kind":"miss"}'
                            ),
                        }
                    ]
                }
            raise AssertionError(f"unexpected tool {tool_name}")

        with patch(
            "digital_brain.tools.mcp_client.call_mcp_tool",
            new=AsyncMock(side_effect=side_effect),
        ) as mocked_call:
            receipt = await create_feedback(
                id="fb-timeout",
                kind="miss",
                sensitivity="public_ops",
                harness_generation_id="hg-pin",
            )

        self.assertTrue(receipt.get("reconciled"))
        self.assertEqual(receipt["record_type"], "Feedback")
        tool_names = [c.args[0] for c in mocked_call.await_args_list]
        self.assertEqual(tool_names, ["create_feedback", "get_quality_receipt"])

    async def test_quality_write_timeout_without_receipt_reraises(self) -> None:
        async def side_effect(tool_name, arguments, **_kwargs):
            if tool_name == "record_run_event":
                raise McpWriteOutcomeUnknown(tool_name, arguments, TimeoutError("t"))
            if tool_name == "get_quality_receipt":
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": '{"outcome":"not_found","receipt_id":"re-miss"}',
                        }
                    ]
                }
            raise AssertionError(tool_name)

        with patch(
            "digital_brain.tools.mcp_client.call_mcp_tool",
            new=AsyncMock(side_effect=side_effect),
        ):
            with self.assertRaises(McpWriteOutcomeUnknown):
                await record_run_event(
                    id="re-miss",
                    route="READ",
                    tool_outcome="empty",
                    harness_generation_id="hg-pin",
                )

    async def test_get_quality_receipt_and_revoke_decoded(self) -> None:
        responses = [
            {
                "content": [
                    {
                        "type": "text",
                        "text": '{"outcome":"ok","receipt_id":"fb-1","record_type":"Feedback"}',
                    }
                ]
            },
            {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            '{"outcome":"created","lifecycle_event_id":"fle-1",'
                            '"event":"revoked"}'
                        ),
                    }
                ]
            },
        ]
        with patch(
            "digital_brain.tools.mcp_client.call_mcp_tool",
            new=AsyncMock(side_effect=responses),
        ) as mocked_call:
            self.assertEqual(
                (await get_quality_receipt("fb-1"))["record_type"], "Feedback"
            )
            rev = await revoke_feedback(id="fle-1", feedback_id="fb-1", actor="user")
            self.assertEqual(rev["event"], "revoked")

        self.assertEqual(
            mocked_call.await_args_list[0].args,
            ("get_quality_receipt", {"receipt_id": "fb-1"}),
        )

    async def test_transport_failure_on_create_feedback_is_outcome_unknown_without_retry(
        self,
    ) -> None:
        session = _FailingSession()
        with patch(
            "digital_brain.tools.mcp_client.aiohttp.ClientSession",
            return_value=session,
        ):
            with self.assertRaises(McpWriteOutcomeUnknown):
                await call_mcp_tool(
                    "create_feedback",
                    {
                        "id": "fb-x",
                        "kind": "praise",
                        "sensitivity": "public_ops",
                        "harness_generation_id": "hg-pin",
                    },
                )
        self.assertEqual(session.calls, 1)
