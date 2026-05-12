"""Tests for ledger ``top_categories`` layout API (QoL grouping only)."""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


class TopCategoriesTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_put_rejects_unknown_subcategory(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from api import integrity
            from ledger import migrate_ledger_db

            migrate_ledger_db()
            conn = sqlite3.connect(config_mod.ledger_db_file)
            try:
                conn.execute("INSERT INTO store (store_name, is_static) VALUES ('s1', 0)")
                conn.execute("INSERT INTO store_category (store_name, category) VALUES ('s1', 'A')")
                conn.commit()
            finally:
                conn.close()

            raw = json.dumps(
                {"columns": [{"top_name": "T1", "sub_categories": ["A", "not-in-store"]}]}
            ).encode("utf-8")
            code, out = integrity.put_top_categories(raw)
            self.assertEqual(code, 400)
            self.assertFalse(out.get("ok"))

    def test_put_roundtrip(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from api import integrity
            from ledger import migrate_ledger_db

            migrate_ledger_db()
            conn = sqlite3.connect(config_mod.ledger_db_file)
            try:
                conn.execute("INSERT INTO store (store_name, is_static) VALUES ('s1', 0)")
                conn.execute(
                    "INSERT INTO store_category (store_name, category) VALUES ('s1', 'A'), ('s1', 'B')"
                )
                conn.commit()
            finally:
                conn.close()

            raw = json.dumps(
                {
                    "columns": [
                        {"top_name": "Food", "sub_categories": ["A"]},
                        {"top_name": "Other", "sub_categories": ["B"]},
                    ]
                }
            ).encode("utf-8")
            code, out = integrity.put_top_categories(raw)
            self.assertEqual(code, 200, out)
            self.assertTrue(out.get("ok"))
            cols = out.get("columns") or []
            self.assertEqual(len(cols), 2)
            self.assertEqual(cols[0].get("top_name"), "Food")
            self.assertEqual(cols[0].get("sub_categories"), ["A"])

            g = integrity.get_top_categories()
            self.assertTrue(g.get("ok"))
            self.assertEqual(g.get("unassigned"), [])

    def test_put_rejects_duplicate_sub_across_columns(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from api import integrity
            from ledger import migrate_ledger_db

            migrate_ledger_db()
            conn = sqlite3.connect(config_mod.ledger_db_file)
            try:
                conn.execute("INSERT INTO store (store_name, is_static) VALUES ('s1', 0)")
                conn.execute("INSERT INTO store_category (store_name, category) VALUES ('s1', 'A')")
                conn.commit()
            finally:
                conn.close()

            raw = json.dumps(
                {
                    "columns": [
                        {"top_name": "C1", "sub_categories": ["A"]},
                        {"top_name": "C2", "sub_categories": ["A"]},
                    ]
                }
            ).encode("utf-8")
            code, out = integrity.put_top_categories(raw)
            self.assertEqual(code, 400)
            self.assertFalse(out.get("ok"))


if __name__ == "__main__":
    unittest.main()
