"""Tests for dropping rows where transaction amount columns hold non-numeric text."""

from __future__ import annotations

import os
import tempfile
import unittest

import pandas as pd

from pipeline import workbook_normalize as wn


class NonNumericAmountMaskTests(unittest.TestCase):
    def test_detects_non_numeric_amount(self) -> None:
        df = pd.DataFrame(
            {
                "בחובה": ["12.5", "not-a-number", ""],
                "בזכות": ["0", "0", "1"],
            }
        )
        m = wn._non_numeric_amount_text_mask(df)
        self.assertFalse(bool(m.iloc[0]))
        self.assertTrue(bool(m.iloc[1]))
        self.assertFalse(bool(m.iloc[2]))


class DumpTransactionRowsTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import importlib
        from unittest.mock import patch

        import config as cfg

        with patch("dotenv.load_dotenv"):
            importlib.reload(cfg)

    def test_writes_csv_when_mask_nonempty(self) -> None:
        import importlib
        from unittest.mock import patch

        import config as cfg

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(cfg)

            df = pd.DataFrame({"בחובה": ["x"], "תאריך": ["y"]})
            m = pd.Series([True])
            path = os.path.join(tmp, "book.xlsx")
            out = wn._dump_transaction_rows(
                df, m, source_path=path, drop_reason="amount_columns_non_numeric_text"
            )
            self.assertIsNotNone(out)
            assert out is not None
            self.assertTrue(os.path.isfile(out))
            self.assertIn("dropped_rows", out.replace("\\", "/"))


if __name__ == "__main__":
    unittest.main()
