import importlib.util
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_journal_chain.py"
_SPEC = importlib.util.spec_from_file_location("bootstrap_journal_chain_test", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
bootstrap = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bootstrap)


class JournalBootstrapTests(IsolatedAsyncioTestCase):
    async def test_empty_preview_refuses_graph_with_legacy_journals(self) -> None:
        with patch.object(
            bootstrap,
            "execute_cypher",
            new=AsyncMock(return_value=[{"count": 1}]),
        ):
            with self.assertRaisesRegex(RuntimeError, "--empty is allowed"):
                await bootstrap.preview(None)

    async def test_empty_apply_checks_for_legacy_journals_before_any_write(self) -> None:
        with (
            patch.object(
                bootstrap,
                "execute_cypher",
                new=AsyncMock(return_value=[{"count": 2}]),
            ),
            patch.object(bootstrap, "call_mcp_tool", new=AsyncMock()) as tool,
        ):
            with self.assertRaisesRegex(RuntimeError, "Refusing --empty bootstrap"):
                await bootstrap.apply(None)

        tool.assert_not_awaited()

    async def test_apply_uses_dedicated_bootstrap_tool(self) -> None:
        response = {
            "content": [
                {
                    "type": "text",
                    "text": '{"outcome":"bootstrapped","chain_key":"primary","version":0,"journal_id":null,"head_element_id":null}',
                }
            ]
        }
        with patch.object(bootstrap, "call_mcp_tool", new=AsyncMock(return_value=response)) as tool:
            result = await bootstrap.apply("legacy-element-id")

        self.assertEqual(result["outcome"], "bootstrapped")
        tool.assert_awaited_once_with(
            "bootstrap_journal_chain",
            {"head_element_id": "legacy-element-id", "empty": False},
        )
