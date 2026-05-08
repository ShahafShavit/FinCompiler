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

    def test_get_meta_and_timeline_and_conflicts(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from pipeline.holdings_csv_import import (
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
        from pipeline.holdings_csv_import import parse_holdings_paste_grid

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
