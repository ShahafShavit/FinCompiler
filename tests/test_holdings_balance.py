"""Tests for wide holdings → holdings_balance (SQLite) helpers."""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest

import pandas as pd

# Same numeric layout as former ``tests/fixtures/holdings_sample_2024ish.csv``.
_HOLDINGS_WIDE_SAMPLE = pd.DataFrame(
    [
        {
            "עובר ושב": 100.0,
            "ניירות ערך": 200.0,
            "מטבע חוץ": 1.0,
            "תאריך": "2024-01-01",
            "פיקדונות וחסכונות": 0.0,
        },
        {
            "עובר ושב": 50.0,
            "ניירות ערך": 150.0,
            "מטבע חוץ": 2.0,
            "תאריך": "2024-02-01",
            "פיקדונות וחסכונות": 0.0,
        },
    ]
)


class HoldingsBalanceTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_import_sample_parity(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            from unittest.mock import patch

            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from pipeline.holdings_balance import upsert_holdings_long, wide_holdings_to_long

            wide = _HOLDINGS_WIDE_SAMPLE.copy()
            wide.columns = [str(c).strip() for c in wide.columns]
            long_df = wide_holdings_to_long(wide)
            report = upsert_holdings_long(
                long_df,
                config_mod.ledger_db_file,
                clear_holdings_first=True,
            )
            self.assertTrue(report.get("parity_ok"), msg=str(report))
            self.assertEqual(report.get("rows_upserted"), 8)

    def test_long_wide_roundtrip_matches_melt(self) -> None:
        from pipeline.holdings_balance import holdings_long_to_wide, wide_holdings_to_long

        wide_fixture = _HOLDINGS_WIDE_SAMPLE.copy()
        wide_fixture.columns = [str(c).strip() for c in wide_fixture.columns]
        long_a = wide_holdings_to_long(wide_fixture)
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

    def test_get_meta_and_timeline_and_conflicts(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            from unittest.mock import patch

            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from pipeline.holdings_balance import (
                get_holdings_conflicts,
                get_holdings_meta,
                query_holdings_timeline,
                upsert_holdings_rows,
            )

            rows = [
                {"as_of_date": "2026-05-01", "activity_type": "עובר ושב", "balance_ils": 1000},
                {"as_of_date": "2026-05-01", "activity_type": "ניירות ערך", "balance_ils": 3500},
                {"as_of_date": "2026-05-02", "activity_type": "עובר ושב", "balance_ils": 1200},
            ]
            out = upsert_holdings_rows(rows, config_mod.ledger_db_file, overwrite_conflicts=True)
            self.assertTrue(out.get("ok"), msg=str(out))

            meta = get_holdings_meta(config_mod.ledger_db_file)
            self.assertEqual(meta.get("row_count"), 3)
            self.assertIn("עובר ושב", meta.get("activity_types", []))

            tdf = query_holdings_timeline(
                config_mod.ledger_db_file,
                start_date="2026-05-01",
                end_date="2026-05-01",
                activity_types=["עובר ושב"],
            )
            self.assertEqual(len(tdf), 1)
            self.assertEqual(str(tdf.iloc[0]["as_of_date"]), "2026-05-01")

            conflicts = get_holdings_conflicts(
                [{"as_of_date": "2026-05-01", "activity_type": "עובר ושב", "balance_ils": 999}],
                config_mod.ledger_db_file,
            )
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0]["incoming_balance_ils"], 999.0)

    def test_parse_holdings_paste_grid(self) -> None:
        from pipeline.holdings_balance import parse_holdings_paste_grid

        text = (
            "תאריך\tעובר ושב\tניירות ערך\n"
            "2026-05-01\t100\t200\n"
            "2026-05-02\tabc\t300\n"
        )
        out = parse_holdings_paste_grid(text)
        self.assertTrue(out.get("ok"))
        self.assertEqual(len(out.get("rows", [])), 3)
        self.assertEqual(len(out.get("invalid_cells", [])), 1)


if __name__ == "__main__":
    unittest.main()
