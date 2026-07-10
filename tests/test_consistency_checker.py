"""Consistency checker must be report-only: detect candidates, never mutate."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

import digital_brain.services.consistency_checker as consistency_checker


class ConsistencyCheckerReportOnlyTests(IsolatedAsyncioTestCase):
    async def test_find_duplicate_candidates_uses_read_only_execute_cypher(self) -> None:
        """Discovery reads via execute_cypher only; no MCP write/mutation surface."""
        sample_rows = [
            {
                "id_a": "person_a",
                "name_a": "Sasha",
                "id_b": "person_b",
                "name_b": "Sashka",
                "similarity_score": 1.0,
                "evidence_kind": "name_similarity",
            }
        ]

        self.assertFalse(hasattr(consistency_checker, "call_mcp_tool"))
        self.assertFalse(hasattr(consistency_checker, "merge_duplicate_nodes"))
        self.assertFalse(hasattr(consistency_checker, "create_alias"))

        with patch.object(
            consistency_checker,
            "execute_cypher",
            new=AsyncMock(return_value=sample_rows),
        ) as mock_read:
            candidates = await consistency_checker.find_duplicate_candidates()

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["id_a"], "person_a")
        self.assertEqual(candidate["id_b"], "person_b")
        self.assertIn("name_a", candidate)
        self.assertIn("name_b", candidate)
        # Evidence/proposal shape — no mutation command fields
        self.assertNotIn("remove_id", candidate)
        mock_read.assert_awaited()
        self.assertGreaterEqual(mock_read.await_count, 1)
        for call in mock_read.await_args_list:
            query = call.args[0] if call.args else call.kwargs.get("query", "")
            upper = str(query).upper()
            self.assertNotIn("DETACH DELETE", upper)
            self.assertNotIn("CREATE (", upper)
            self.assertNotIn("MERGE (", upper)
            self.assertNotIn("DELETE ", upper)

    async def test_no_legacy_mutation_apis(self) -> None:
        self.assertFalse(hasattr(consistency_checker, "merge_duplicate_nodes"))
        self.assertFalse(hasattr(consistency_checker, "create_alias"))
        self.assertFalse(hasattr(consistency_checker, "run_consistency_check"))
        self.assertFalse(hasattr(consistency_checker, "call_mcp_tool"))
        self.assertTrue(callable(consistency_checker.find_duplicate_candidates))

    async def test_find_duplicate_candidates_returns_empty_when_no_rows(self) -> None:
        with patch.object(
            consistency_checker,
            "execute_cypher",
            new=AsyncMock(return_value=[]),
        ):
            candidates = await consistency_checker.find_duplicate_candidates()
        self.assertEqual(candidates, [])


class OrchestratorNoAutoConsistencyMutationTests(TestCase):
    def test_agent_source_does_not_invoke_run_consistency_check(self) -> None:
        """Post-write path must not auto-run legacy consistency mutation entrypoints."""
        agent_path = Path(__file__).resolve().parents[1] / "digital_brain" / "agent.py"
        source = agent_path.read_text(encoding="utf-8")
        self.assertNotIn("run_consistency_check", source)
        self.assertNotIn("merge_duplicate_nodes", source)
        self.assertNotIn("create_alias", source)

        # Import-time surface: agent module must not pull the legacy API.
        import digital_brain.agent as agent_mod

        self.assertFalse(hasattr(agent_mod, "run_consistency_check"))
        # Avoid false confidence from re-exports alone; source is authoritative.
        self.assertNotIn(
            "run_consistency_check",
            inspect.getsource(agent_mod),
        )
