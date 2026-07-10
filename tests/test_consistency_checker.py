"""Consistency checker must be report-only: detect candidates, never mutate."""

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import digital_brain.services.consistency_checker as consistency_checker


class ConsistencyCheckerReportOnlyTests(IsolatedAsyncioTestCase):
    async def test_find_duplicate_candidates_never_calls_write_mutation_stub(self) -> None:
        """Generic write/MCP mutation stub always raises; detection still works."""
        sample_rows = [
            {
                "id_a": "person_a",
                "name_a": "Sasha",
                "id_b": "person_b",
                "name_b": "Sashka",
                "similarity_score": 1.0,
            }
        ]

        async def mutation_always_raises(*_args, **_kwargs):
            raise AssertionError(
                "write/MCP mutation must not be called during duplicate detection"
            )

        with (
            patch.object(
                consistency_checker,
                "execute_cypher",
                new=AsyncMock(return_value=sample_rows),
            ) as mock_read,
            patch.object(
                consistency_checker,
                "call_mcp_tool",
                new=AsyncMock(side_effect=mutation_always_raises),
            ),
        ):
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

    async def test_no_legacy_mutation_apis(self) -> None:
        self.assertFalse(hasattr(consistency_checker, "merge_duplicate_nodes"))
        self.assertFalse(hasattr(consistency_checker, "create_alias"))
        self.assertFalse(hasattr(consistency_checker, "run_consistency_check"))
        self.assertTrue(callable(consistency_checker.find_duplicate_candidates))

    async def test_find_duplicate_candidates_returns_empty_when_no_rows(self) -> None:
        with patch.object(
            consistency_checker,
            "execute_cypher",
            new=AsyncMock(return_value=[]),
        ):
            candidates = await consistency_checker.find_duplicate_candidates()
        self.assertEqual(candidates, [])
