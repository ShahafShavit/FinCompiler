"""Tests for stores_to_categories.csv → store / store_category import."""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "stores_to_categories_sample.csv"
_SIMILAR_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "similar_pairs_sample.csv"


class StaticStoreImportTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_import_fixture_counts_and_static_rule(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from pipeline.ledger import import_stores_to_ledger

            db = config_mod.ledger_db_file
            report = import_stores_to_ledger(
                str(_FIXTURE),
                db,
                similar_pairs_csv=str(_SIMILAR_FIXTURE),
                replace=True,
            )
            self.assertTrue(report.get("ok"), msg=str(report))
            self.assertEqual(report.get("stores_inserted"), 3)
            self.assertEqual(report.get("store_category_rows_inserted"), 4)
            self.assertEqual(report.get("similar_pair_rows_inserted"), 2)
            self.assertEqual(report.get("stores_forced_dynamic"), 1)

            conn = sqlite3.connect(db)
            try:
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
                n_sim = conn.execute("SELECT COUNT(*) FROM similar_category_pair").fetchone()[0]
                self.assertEqual(n_sim, 2)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
