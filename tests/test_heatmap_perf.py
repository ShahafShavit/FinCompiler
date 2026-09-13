"""Heatmap detail cap, single-view snapshot, hashed SPA asset cache."""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient


class HeatmapPerfTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def _fresh_db(self) -> str:
        import config as config_mod

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["FINANCE_WORKSPACE_ROOT"] = tmp.name
        with patch("dotenv.load_dotenv"):
            importlib.reload(config_mod)
        from ledger import migrate_ledger_db

        migrate_ledger_db()
        return config_mod.ledger_db_file

    def _insert_many(self, db: str, n: int, *, date: str = "2024-01-15", category: str = "Food") -> None:
        statement_month = date[:7]
        rows = [
            (
                date,
                10.0,
                0.0,
                "card",
                f"fp-{date}-{category}-{i}",
                category,
                statement_month,
                date,
                0,
            )
            for i in range(n)
        ]
        conn = sqlite3.connect(db)
        try:
            conn.executemany(
                """
                INSERT INTO ledger_transaction (
                  "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב",
                  "fingerprint", "קטגוריה", statement_month, ingested_at,
                  excluded_from_calculations
                ) VALUES (?, ?, ?, ?, '', '', ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        finally:
            conn.close()

    def test_fetch_detail_rows_caps_and_stamps_truncated(self) -> None:
        from ledger.heatmap_sql import fetch_detail_rows

        db = self._fresh_db()
        self._insert_many(db, 1001)
        rows = fetch_detail_rows(
            db, report_type="expense", year_month="2024-01", category="Food", period="all"
        )
        self.assertEqual(len(rows), 1000)
        self.assertTrue(rows[0]["_detail_truncated"])
        self.assertEqual(rows[0]["_detail_limit"], 1000)

        few = fetch_detail_rows(
            db, report_type="expense", year_month="2024-01", category="Missing", period="all"
        )
        self.assertEqual(few, [])

        self._insert_many(db, 2, date="2024-02-01", category="Rent")
        rent = fetch_detail_rows(
            db, report_type="expense", year_month="2024-02", category="Rent", period="all"
        )
        self.assertEqual(len(rent), 2)
        self.assertFalse(rent[0]["_detail_truncated"])

    def test_detail_payload_sets_truncated_flag(self) -> None:
        from api import heatmap

        db = self._fresh_db()
        self._insert_many(db, 1001)
        heatmap.invalidate_bundle_cache()
        status, payload = heatmap.detail_api_payload(
            {"type": ["expense"], "ym": ["2024-01"], "cat": ["Food"]}
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["detail_limit"], 1000)
        self.assertEqual(len(payload["sections"][0]["rows"]), 1000)

    def test_api_snapshot_type_returns_one_view(self) -> None:
        from api import heatmap

        db = self._fresh_db()
        self._insert_many(db, 3)
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                """
                INSERT INTO ledger_transaction (
                  "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב",
                  "fingerprint", "קטגוריה", statement_month, ingested_at,
                  excluded_from_calculations
                ) VALUES ('2024-01-20', 0, 50, 'payroll', '', '', 'inc-1', 'Salary',
                          '2024-01', '2024-01-20', 0)
                """
            )
            conn.commit()
        finally:
            conn.close()
        heatmap.invalidate_bundle_cache()
        one = heatmap.api_snapshot("expense")
        self.assertTrue(one["ok"])
        self.assertEqual(set(one["views"].keys()), {"expense"})
        self.assertEqual(set(one["statsTables"].keys()), {"expense"})
        full = heatmap.api_snapshot(None)
        self.assertEqual(set(full["views"].keys()), {"expense", "income", "net"})


class SpaAssetCacheTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_hashed_assets_are_immutable_index_is_no_store(self) -> None:
        import config as config_mod

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        assets = Path(tmp.name) / "assets"
        assets.mkdir()
        (assets / "app-hash.js").write_text("ok();", encoding="utf-8")
        os.environ["FINANCE_WORKSPACE_ROOT"] = tmp.name
        with patch("dotenv.load_dotenv"):
            importlib.reload(config_mod)
        from api.main import create_app

        with patch("api.routers.control.spa_assets_dir", return_value=assets):
            with TestClient(create_app()) as client:
                r = client.get("/assets/app-hash.js")
                self.assertEqual(r.status_code, 200)
                cc = (r.headers.get("cache-control") or "").lower()
                self.assertIn("immutable", cc)
                self.assertIn("max-age=31536000", cc)
                idx = client.get("/")
                idx_cc = (idx.headers.get("cache-control") or "").lower()
                self.assertIn("no-store", idx_cc)


if __name__ == "__main__":
    unittest.main()
