"""Tests for wide holdings CSV → holdings_balance import."""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "holdings_sample_2024ish.csv"


class HoldingsCsvImportTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_import_sample_parity(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from pipeline.holdings_csv_import import import_holdings_csvs

            report = import_holdings_csvs(
                [str(_FIXTURE)],
                config_mod.ledger_db_file,
                clear_holdings_first=True,
            )
            self.assertTrue(report.get("parity_ok"), msg=str(report))
            self.assertEqual(report.get("rows_upserted"), 8)

    def test_long_wide_roundtrip_matches_melt(self) -> None:
        from pipeline.holdings_csv_import import (
            holdings_long_to_wide,
            load_holdings_wide_csv,
            wide_holdings_to_long,
        )

        long_a = load_holdings_wide_csv(str(_FIXTURE))
        wide = holdings_long_to_wide(long_a)
        long_b = wide_holdings_to_long(wide)
        long_a = long_a.sort_values(["as_of_date", "activity_type"]).reset_index(drop=True)
        long_b = long_b.sort_values(["as_of_date", "activity_type"]).reset_index(drop=True)
        self.assertEqual(len(long_a), len(long_b))
        for c in ("as_of_date", "activity_type"):
            self.assertTrue(long_a[c].astype(str).equals(long_b[c].astype(str)))
        self.assertTrue(
            (long_a["balance_ils"] - long_b["balance_ils"]).abs().max() < 0.01,
        )


if __name__ == "__main__":
    unittest.main()
