"""Unit tests for ``compute_ingested_at_iso`` (ledger v7)."""

from __future__ import annotations

import unittest

from pipeline.ingested_at_rules import compute_ingested_at_iso


class IngestedAtRulesTests(unittest.TestCase):
    def test_fifteenth_rule(self) -> None:
        self.assertEqual(
            compute_ingested_at_iso("2025-10-10", None),
            "2025-10-15",
        )
        self.assertEqual(
            compute_ingested_at_iso("2025-10-19", None),
            "2025-11-15",
        )
        self.assertEqual(
            compute_ingested_at_iso("2025-10-15", None),
            "2025-10-15",
        )
        self.assertEqual(
            compute_ingested_at_iso("2025-12-20", None),
            "2026-01-15",
        )

    def test_taarich_hidon_overrides(self) -> None:
        self.assertEqual(
            compute_ingested_at_iso("2025-10-10", "2026-04-09"),
            "2026-04-09",
        )


if __name__ == "__main__":
    unittest.main()
