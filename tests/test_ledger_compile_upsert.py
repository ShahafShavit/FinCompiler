"""Tests for compile → ledger upsert (MIG-E2)."""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from pipeline.fingerprint import generate_transaction_fingerprint
from ledger import dedupe_import_batch_by_fingerprint


def _reload_config() -> None:
    import config as config_mod

    importlib.reload(config_mod)
    return config_mod


class LedgerCompileUpsertTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        _reload_config()

    def test_upsert_inserts_and_updates_pipeline_columns(self) -> None:
        from ledger import upsert_compiled_dataframe_to_ledger

        base = {
            "תאריך": "2025-01-15",
            "בחובה": 100.0,
            "בזכות": 0.0,
            "מקור עסקה": "Store",
            "פירוט נוסף": None,
            "תאור מורחב": None,
            "4 ספרות": None,
            "קטגוריה": "",
        }
        fp2 = generate_transaction_fingerprint(pd.Series(base))
        self.assertIsNotNone(fp2)
        df1 = pd.DataFrame([{**base, "notes": None}])
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
                        (fp2,),
                    ).fetchone()[0]
                )
            finally:
                conn.close()
            self.assertEqual(n, 1)
            self.assertAlmostEqual(d, 100.0)

            # Same amounts → same fingerprint; empty notes then filled on conflict.
            df2 = pd.DataFrame([{**base, "notes": "second"}])
            upsert_compiled_dataframe_to_ledger(df2, cfg.ledger_db_file)
            conn = sqlite3.connect(cfg.ledger_db_file)
            try:
                n2 = conn.execute("SELECT COUNT(*) FROM ledger_transaction").fetchone()[0]
                notes = conn.execute(
                    'SELECT notes FROM ledger_transaction WHERE "fingerprint" = ?',
                    (fp2,),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(n2, 1)
            self.assertEqual(notes, "second")

    def test_upsert_preserves_nonempty_category(self) -> None:
        from ledger import upsert_compiled_dataframe_to_ledger

        row = {
            "תאריך": "2025-02-01",
            "בחובה": 50.0,
            "בזכות": 0.0,
            "מקור עסקה": "X",
            "פירוט נוסף": None,
            "תאור מורחב": None,
            "4 ספרות": None,
            "קטגוריה": "UserCategory",
        }
        fp2 = generate_transaction_fingerprint(pd.Series(row))
        self.assertIsNotNone(fp2)
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
                    (fp2,),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(cat, "UserCategory")

    def test_batch_category_updates_by_fingerprint(self) -> None:
        from ledger import update_categories_by_fingerprint_batch, upsert_compiled_dataframe_to_ledger

        row = {
            "תאריך": "2025-03-01",
            "בחובה": 10.0,
            "בזכות": 0.0,
            "מקור עסקה": "Shop",
            "פירוט נוסף": None,
            "תאור מורחב": None,
            "4 ספרות": None,
            "קטגוריה": "",
        }
        fp = generate_transaction_fingerprint(pd.Series(row))
        self.assertIsNotNone(fp)
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                cfg = _reload_config()
            upsert_compiled_dataframe_to_ledger(pd.DataFrame([{**row, "notes": None}]), cfg.ledger_db_file)
            n = update_categories_by_fingerprint_batch(
                cfg.ledger_db_file,
                [(fp, "Food"), (fp, "Food")],
            )
            self.assertEqual(n, 2)
            conn = sqlite3.connect(cfg.ledger_db_file)
            try:
                cat = conn.execute(
                    'SELECT "קטגוריה" FROM ledger_transaction WHERE TRIM("fingerprint") = ?',
                    (str(fp).strip(),),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(cat, "Food")

    def test_dedupe_import_batch_prefers_category(self) -> None:
        base = {
            "תאריך": "2025-06-01",
            "בחובה": 1.0,
            "בזכות": 0.0,
            "מקור עסקה": "DupShop",
            "פירוט נוסף": None,
            "תאור מורחב": None,
            "4 ספרות": None,
            "fingerprint": None,
            "קטגוריה": "",
        }
        r1 = {**base, "קטגוריה": ""}
        r2 = {**base, "קטגוריה": "Food"}
        fp = generate_transaction_fingerprint(pd.Series(r1))
        self.assertIsNotNone(fp)
        r1["fingerprint"] = fp
        r2["fingerprint"] = fp
        df = pd.DataFrame([r1, r2])
        out = dedupe_import_batch_by_fingerprint(df)
        self.assertEqual(len(out), 1)
        self.assertEqual(str(out.iloc[0]["קטגוריה"]), "Food")

    def test_apply_auto_categories_from_static_stores_sql(self) -> None:
        from ledger import apply_auto_categories_from_static_stores_sql
        from ledger import sync_stores_to_ledger_from_dataframe
        from ledger import upsert_compiled_dataframe_to_ledger

        row = {
            "תאריך": "2025-03-01",
            "בחובה": 0.0,
            "בזכות": 10.0,
            "מקור עסקה": "StoreA",
            "פירוט נוסף": None,
            "תאור מורחב": None,
            "4 ספרות": None,
            "קטגוריה": "",
        }
        fp = generate_transaction_fingerprint(pd.Series(row))
        self.assertIsNotNone(fp)
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                cfg = _reload_config()
            stores_df = pd.DataFrame([{"store_name": "StoreA", "category": "Rent", "is_static": 1}])
            sync_stores_to_ledger_from_dataframe(cfg.ledger_db_file, stores_df)
            upsert_compiled_dataframe_to_ledger(pd.DataFrame([{**row, "notes": None}]), cfg.ledger_db_file)
            n = apply_auto_categories_from_static_stores_sql(cfg.ledger_db_file)
            self.assertEqual(n, 1)
            conn = sqlite3.connect(cfg.ledger_db_file)
            try:
                cat = conn.execute(
                    'SELECT "קטגוריה" FROM ledger_transaction WHERE TRIM("fingerprint") = ?',
                    (str(fp).strip(),),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(cat, "Rent")


if __name__ == "__main__":
    unittest.main()
