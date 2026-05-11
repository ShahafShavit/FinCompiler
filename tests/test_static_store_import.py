"""Tests for static store mappings → ``store`` / ``store_category`` import."""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd


class StaticStoreImportTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_import_fixture_counts_and_static_rule(self) -> None:
        import config as config_mod

        fixture = pd.DataFrame(
            [
                {"store_name": "StaticCafe", "category": "אוכל בחוץ", "is_static": 1.0},
                {"store_name": "DynamicMall", "category": "משק בית", "is_static": 0.0},
                {"store_name": "DynamicMall", "category": "אישי", "is_static": 1.0},
                {"store_name": "SingleRow", "category": "רכב", "is_static": 1.0},
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from ledger import import_stores_to_ledger_from_dataframe

            db = config_mod.ledger_db_file
            report = import_stores_to_ledger_from_dataframe(
                fixture,
                db,
                replace=True,
            )
            self.assertTrue(report.get("ok"), msg=str(report))
            self.assertEqual(report.get("stores_inserted"), 3)
            self.assertEqual(report.get("store_category_rows_inserted"), 4)
            self.assertEqual(report.get("stores_forced_dynamic"), 1)

            conn = sqlite3.connect(db)
            try:
                sim = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='similar_category_pair'"
                ).fetchone()
                self.assertIsNone(sim)
                dyn = conn.execute(
                    "SELECT is_static FROM store WHERE store_name = ?",
                    ("DynamicMall",),
                ).fetchone()
                self.assertEqual(dyn[0], 0)
                st = conn.execute(
                    "SELECT is_static FROM store WHERE store_name = ?",
                    ("StaticCafe",),
                ).fetchone()
                self.assertEqual(st[0], 1)
                n_sc = conn.execute(
                    "SELECT COUNT(*) FROM store_category WHERE store_name = ?",
                    ("DynamicMall",),
                ).fetchone()[0]
                self.assertEqual(n_sc, 2)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
