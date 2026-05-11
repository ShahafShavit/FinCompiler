"""Unit tests for ``ingested_at_for_new_ledger_row``."""

from __future__ import annotations

from datetime import date
import unittest

from ledger import ingested_at_for_new_ledger_row


class IngestedAtRulesTests(unittest.TestCase):
    def test_ingested_at_for_new_ledger_row_is_local_today(self) -> None:
        self.assertEqual(ingested_at_for_new_ledger_row(), date.today().isoformat())


if __name__ == "__main__":
    unittest.main()
