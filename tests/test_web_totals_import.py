"""Tests for web_totals → ledger import (MIG-D1 / MIG-D3)."""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "web_totals_sample.csv"


class WebTotalsImportTests(unittest.TestCase):
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

            from pipeline.web_totals_import import import_web_totals_to_ledger

            report = import_web_totals_to_ledger(
                str(_FIXTURE),
                config_mod.ledger_db_file,
                replace=True,
            )
            self.assertTrue(report.get("parity_ok"), msg=str(report))
            self.assertEqual(report.get("rows_imported"), 2)


if __name__ == "__main__":
    unittest.main()
