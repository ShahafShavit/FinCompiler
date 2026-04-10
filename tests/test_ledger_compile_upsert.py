"""Tests for compile → ledger upsert (MIG-E2)."""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd


def _reload_config() -> None:
    import config as config_mod

    importlib.reload(config_mod)
    return config_mod


class LedgerCompileUpsertTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        _reload_config()

    def test_upsert_inserts_and_updates_pipeline_columns(self) -> None:
        from pipeline.ledger_compile_upsert import upsert_compiled_dataframe_to_ledger

        fp = "2025-01-15:100.0:TestStore:extra"
        base = {
            "תאריך": "2025-01-15",
            "בחובה": 100.0,
            "בזכות": 0.0,
            "מקור עסקה": "Store",
            "פירוט נוסף": None,
            "תאור מורחב": None,
            "4 ספרות": None,
            "fingerprint": fp,
            "קטגוריה": "",
        }
        df1 = pd.DataFrame([base])
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                cfg = _reload_config()
            upsert_compiled_dataframe_to_ledger(df1, cfg.ledger_db_file)
            conn = sqlite3.connect(cfg.ledger_db_file)
            try:
                n = conn.execute("SELECT COUNT(*) FROM ledger_transaction").fetchone()[0]
                d = float(
                    conn.execute(
                        'SELECT "בחובה" FROM ledger_transaction WHERE "fingerprint" = ?',
                        (fp,),
                    ).fetchone()[0]
                )
            finally:
                conn.close()
            self.assertEqual(n, 1)
            self.assertAlmostEqual(d, 100.0)

            df2 = pd.DataFrame([{**base, "בחובה": 200.0}])
            upsert_compiled_dataframe_to_ledger(df2, cfg.ledger_db_file)
            conn = sqlite3.connect(cfg.ledger_db_file)
            try:
                d2 = float(
                    conn.execute(
                        'SELECT "בחובה" FROM ledger_transaction WHERE "fingerprint" = ?',
                        (fp,),
                    ).fetchone()[0]
                )
            finally:
                conn.close()
            self.assertAlmostEqual(d2, 200.0)

    def test_upsert_preserves_nonempty_category(self) -> None:
        from pipeline.ledger_compile_upsert import upsert_compiled_dataframe_to_ledger

        fp = "2025-02-01:50.0:Other:more"
        row = {
            "תאריך": "2025-02-01",
            "בחובה": 50.0,
            "בזכות": 0.0,
            "מקור עסקה": "X",
            "פירוט נוסף": None,
            "תאור מורחב": None,
            "4 ספרות": None,
            "fingerprint": fp,
            "קטגוריה": "UserCategory",
        }
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                cfg = _reload_config()
            upsert_compiled_dataframe_to_ledger(pd.DataFrame([row]), cfg.ledger_db_file)

            conflict = {**row, "קטגוריה": ""}
            upsert_compiled_dataframe_to_ledger(pd.DataFrame([conflict]), cfg.ledger_db_file)

            conn = sqlite3.connect(cfg.ledger_db_file)
            try:
                cat = conn.execute(
                    'SELECT "קטגוריה" FROM ledger_transaction WHERE "fingerprint" = ?',
                    (fp,),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(cat, "UserCategory")


if __name__ == "__main__":
    unittest.main()
