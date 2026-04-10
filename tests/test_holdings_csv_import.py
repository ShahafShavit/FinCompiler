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


if __name__ == "__main__":
    unittest.main()
