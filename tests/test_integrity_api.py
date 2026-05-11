"""Tests for :mod:`api.integrity`."""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import config as config_mod


class IntegrityApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        importlib.reload(config_mod)

    def _fresh_db(self) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["FINANCE_WORKSPACE_ROOT"] = tmp.name
        with patch("dotenv.load_dotenv"):
            importlib.reload(config_mod)
        from ledger import migrate_ledger_db

        migrate_ledger_db()
        return config_mod.ledger_db_file

    def test_rename_category_updates_ledger_and_store(self) -> None:
        from api import integrity

        db = self._fresh_db()
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                """
                INSERT INTO ledger_transaction (
                  "תאריך", "בחובה", "בזכות", "מקור עסקה", "fingerprint", ingested_at, "קטגוריה"
                ) VALUES ('2024-06-01', 10, 0, 'TestPay', 'fp1', '2024-06-01', 'OldCat')
                """
            )
            conn.execute("INSERT INTO store (store_name, is_static) VALUES ('TestPay', 0)")
            conn.execute("INSERT INTO store_category (store_name, category) VALUES ('TestPay', 'OldCat')")
            conn.commit()
        finally:
            conn.close()

        status, payload = integrity.rename_category(
            json.dumps({"from": "OldCat", "to": "NewCat", "dry_run": False}).encode("utf-8")
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("rows_updated", {}).get("ledger_transaction"), 1)
        self.assertEqual(payload.get("rows_updated", {}).get("store_category"), 1)

        conn = sqlite3.connect(db)
        try:
            c = conn.execute('SELECT "קטגוריה" FROM ledger_transaction WHERE fingerprint = ?', ("fp1",)).fetchone()[0]
            self.assertEqual(c, "NewCat")
            sc = conn.execute("SELECT category FROM store_category WHERE store_name = ?", ("TestPay",)).fetchone()[0]
            self.assertEqual(sc, "NewCat")
        finally:
            conn.close()

    def test_patch_store_static_rejects_multiple_categories(self) -> None:
        from api import integrity

        db = self._fresh_db()
        conn = sqlite3.connect(db)
        try:
            conn.execute("INSERT INTO store (store_name, is_static) VALUES ('X', 0)")
            conn.execute("INSERT INTO store_category (store_name, category) VALUES ('X', 'A')")
            conn.execute("INSERT INTO store_category (store_name, category) VALUES ('X', 'B')")
            conn.commit()
        finally:
            conn.close()

        status, payload = integrity.patch_store_static(
            json.dumps({"store_name": "X", "is_static": 1}).encode("utf-8")
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload.get("error"), "multiple_categories_for_static")

    def test_patch_store_static_ok_and_forward_fill(self) -> None:
        from api import integrity

        db = self._fresh_db()
        conn = sqlite3.connect(db)
        try:
            conn.execute("INSERT INTO store (store_name, is_static) VALUES ('Y', 0)")
            conn.execute("INSERT INTO store_category (store_name, category) VALUES ('Y', 'Groceries')")
            conn.execute(
                """
                INSERT INTO ledger_transaction (
                  "תאריך", "בחובה", "בזכות", "מקור עסקה", "fingerprint", ingested_at, "קטגוריה"
                ) VALUES ('2024-06-01', 5, 0, 'Y', 'fp-y', '2024-06-01', '')
                """
            )
            conn.commit()
        finally:
            conn.close()

        status, payload = integrity.patch_store_static(
            json.dumps({"store_name": "Y", "is_static": 1}).encode("utf-8")
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("ok"))
        self.assertGreaterEqual(int(payload.get("forward_filled_uncategorized") or 0), 1)

        conn = sqlite3.connect(db)
        try:
            cat = conn.execute('SELECT "קטגוריה" FROM ledger_transaction WHERE fingerprint = ?', ("fp-y",)).fetchone()[0]
            self.assertEqual(cat, "Groceries")
        finally:
            conn.close()

    def test_report_has_sections(self) -> None:
        from api import integrity

        self._fresh_db()
        rpt = integrity.build_integrity_report()
        self.assertTrue(rpt.get("ledger_exists"))
        ids = {s["id"] for s in (rpt.get("sections") or [])}
        self.assertIn("uncategorized", ids)
        self.assertIn("duplicate_fingerprint", ids)

    def test_list_stores_aggregated(self) -> None:
        from api import integrity

        db = self._fresh_db()
        conn = sqlite3.connect(db)
        try:
            conn.execute("INSERT INTO store (store_name, is_static) VALUES ('Z', 1)")
            conn.execute("INSERT INTO store_category (store_name, category) VALUES ('Z', 'C1')")
            conn.commit()
        finally:
            conn.close()

        out = integrity.list_stores_aggregated()
        self.assertTrue(out.get("ok"))
        stores = out.get("stores") or []
        self.assertEqual(len(stores), 1)
        self.assertEqual(stores[0]["store_name"], "Z")
        self.assertEqual(stores[0]["is_static"], 1)
        self.assertEqual(stores[0]["categories"], ["C1"])

    def test_integrity_excluded_section_and_uncategorized_filter(self) -> None:
        from api import integrity

        db = self._fresh_db()
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                """
                INSERT INTO ledger_transaction (
                  "תאריך", "בחובה", "בזכות", "מקור עסקה", "fingerprint", ingested_at, "קטגוריה",
                  excluded_from_calculations
                ) VALUES ('2024-06-01', 10, 0, 'Ghost', 'fp-ghost', '2024-06-01', NULL, 1)
                """
            )
            conn.execute(
                """
                INSERT INTO ledger_transaction (
                  "תאריך", "בחובה", "בזכות", "מקור עסקה", "fingerprint", ingested_at, "קטגוריה"
                ) VALUES ('2024-06-02', 20, 0, 'Live', 'fp-live', '2024-06-02', NULL)
                """
            )
            conn.commit()
        finally:
            conn.close()

        with patch.object(integrity, "_ledger_path", return_value=db):
            rpt = integrity.build_integrity_report()

        self.assertTrue(rpt.get("ok"))
        by_id = {s["id"]: s for s in (rpt.get("sections") or [])}
        ex_sec = by_id.get("excluded_transactions")
        self.assertIsNotNone(ex_sec)
        self.assertEqual(ex_sec.get("count"), 1)
        self.assertEqual(by_id["uncategorized"].get("count"), 1)

    def test_patch_ledger_tx_by_fingerprint(self) -> None:
        from api import integrity

        db = self._fresh_db()
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                """
                INSERT INTO ledger_transaction (
                  "תאריך", "בחובה", "בזכות", "מקור עסקה", "fingerprint", ingested_at, "קטגוריה"
                ) VALUES ('2024-08-01', 3, 0, 'Mini', 'fp-x', '2024-08-01', 'Food')
                """
            )
            conn.commit()
        finally:
            conn.close()

        with patch.object(integrity, "_ledger_path", return_value=db):
            status, payload = integrity.patch_ledger_transaction(
                json.dumps({"fingerprint": "fp-x", "patch": {"excluded_from_calculations": 1}}).encode("utf-8")
            )
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("ok"))

        conn = sqlite3.connect(db)
        try:
            excl = conn.execute(
                "SELECT excluded_from_calculations FROM ledger_transaction WHERE fingerprint = ?",
                ("fp-x",),
            ).fetchone()[0]
            self.assertEqual(int(excl), 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
