"""Tests for :mod:`api.ledger_search`."""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import config as config_mod


class LedgerSearchTests(unittest.TestCase):
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

    def _insert(
        self,
        db: str,
        *,
        date: str,
        debit: float = 0,
        credit: float = 0,
        source: str = "",
        detail: str = "",
        desc: str = "",
        category: str = "",
        notes: str = "",
        fingerprint: str,
        statement_month: str | None = None,
        excluded: int = 0,
        card: str = "",
    ) -> int:
        conn = sqlite3.connect(db)
        try:
            cur = conn.execute(
                """
                INSERT INTO ledger_transaction (
                  "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב",
                  "4 ספרות", "fingerprint", "קטגוריה", notes, statement_month,
                  ingested_at, excluded_from_calculations
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    date,
                    debit,
                    credit,
                    source,
                    detail,
                    desc,
                    card,
                    fingerprint,
                    category,
                    notes,
                    statement_month,
                    date,
                    excluded,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def test_text_token_and_across_fields(self) -> None:
        from api import ledger_search

        db = self._fresh_db()
        self._insert(
            db,
            date="2024-06-01",
            debit=50,
            source="Cafe Aroma",
            category="Food",
            fingerprint="fp1",
        )
        self._insert(
            db,
            date="2024-06-02",
            debit=50,
            source="Cafe Aroma",
            category="Transport",
            fingerprint="fp2",
        )
        self._insert(
            db,
            date="2024-06-03",
            debit=50,
            source="Other",
            category="Food",
            fingerprint="fp3",
        )

        code, payload = ledger_search.search_ledger({"q": ["Aroma Food"]})
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["rows"][0]["fingerprint"], "fp1")

    def test_numeric_q_hits_amount_not_substring(self) -> None:
        from api import ledger_search

        db = self._fresh_db()
        self._insert(db, date="2024-01-01", debit=100, source="A", fingerprint="fp100")
        self._insert(db, date="2024-01-02", debit=1000, source="B", fingerprint="fp1000")
        self._insert(db, date="2024-01-03", credit=99.90, source="C", fingerprint="fp999")

        code, payload = ledger_search.search_ledger({"q": ["100"]})
        self.assertEqual(code, 200)
        fps = {r["fingerprint"] for r in payload["rows"]}
        self.assertEqual(fps, {"fp100"})

        code2, payload2 = ledger_search.search_ledger({"q": ["99,90"]})
        self.assertEqual(code2, 200)
        self.assertEqual(payload2["total"], 1)
        self.assertEqual(payload2["rows"][0]["fingerprint"], "fp999")

    def test_date_shaped_q_hits_date_and_statement_month(self) -> None:
        from api import ledger_search

        db = self._fresh_db()
        self._insert(
            db,
            date="2024-03-15",
            debit=10,
            source="X",
            fingerprint="fp_day",
        )
        self._insert(
            db,
            date="2023-01-01",
            debit=10,
            source="Y",
            statement_month="2024-03",
            fingerprint="fp_stmt",
        )
        self._insert(
            db,
            date="2024-04-01",
            debit=10,
            source="Z",
            fingerprint="fp_other",
        )

        code, payload = ledger_search.search_ledger({"q": ["2024-03-15"]})
        self.assertEqual(code, 200)
        self.assertEqual({r["fingerprint"] for r in payload["rows"]}, {"fp_day"})

        code2, payload2 = ledger_search.search_ledger({"q": ["2024-03"]})
        self.assertEqual(code2, 200)
        fps = {r["fingerprint"] for r in payload2["rows"]}
        self.assertEqual(fps, {"fp_day", "fp_stmt"})

        code3, payload3 = ledger_search.search_ledger({"q": ["15.03.2024"]})
        self.assertEqual(code3, 200)
        self.assertEqual(payload3["total"], 1)
        self.assertEqual(payload3["rows"][0]["fingerprint"], "fp_day")

    def test_ids_filter_returns_all_requested(self) -> None:
        from api import ledger_search

        db = self._fresh_db()
        a = self._insert(db, date="2024-01-01", debit=10, source="A", fingerprint="fp_a")
        b = self._insert(db, date="2024-02-01", debit=20, source="B", fingerprint="fp_b")
        self._insert(db, date="2024-03-01", debit=30, source="C", fingerprint="fp_c")

        code, payload = ledger_search.search_ledger({"ids": [f"{a},{b}"]})
        self.assertEqual(code, 200)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(len(payload["rows"]), 2)
        self.assertEqual({r["fingerprint"] for r in payload["rows"]}, {"fp_a", "fp_b"})

    def test_filters_combine_with_and(self) -> None:
        from api import ledger_search

        db = self._fresh_db()
        self._insert(
            db,
            date="2024-05-10",
            debit=150,
            source="SuperPharm",
            fingerprint="fp_hit",
        )
        self._insert(
            db,
            date="2024-05-10",
            debit=50,
            source="SuperPharm",
            fingerprint="fp_amt",
        )
        self._insert(
            db,
            date="2024-06-10",
            debit=150,
            source="SuperPharm",
            fingerprint="fp_date",
        )
        self._insert(
            db,
            date="2024-05-10",
            debit=150,
            source="Other",
            fingerprint="fp_src",
        )

        code, payload = ledger_search.search_ledger(
            {
                "q": ["Super"],
                "date_from": ["2024-05-01"],
                "date_to": ["2024-05-31"],
                "amount_min": ["100"],
                "amount_max": ["200"],
            }
        )
        self.assertEqual(code, 200)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["rows"][0]["fingerprint"], "fp_hit")

    def test_excluded_hidden_unless_flag(self) -> None:
        from api import ledger_search

        db = self._fresh_db()
        self._insert(db, date="2024-01-01", debit=10, source="Keep", fingerprint="fp_keep")
        self._insert(
            db,
            date="2024-01-02",
            debit=10,
            source="Gone",
            fingerprint="fp_gone",
            excluded=1,
        )

        code, payload = ledger_search.search_ledger({})
        self.assertEqual(code, 200)
        fps = {r["fingerprint"] for r in payload["rows"]}
        self.assertEqual(fps, {"fp_keep"})

        code2, payload2 = ledger_search.search_ledger({"include_excluded": ["1"]})
        self.assertEqual(code2, 200)
        fps2 = {r["fingerprint"] for r in payload2["rows"]}
        self.assertEqual(fps2, {"fp_keep", "fp_gone"})

    def test_pagination_total_limit_offset(self) -> None:
        from api import ledger_search

        db = self._fresh_db()
        for i in range(5):
            self._insert(
                db,
                date=f"2024-01-{i + 1:02d}",
                debit=i + 1,
                source=f"S{i}",
                fingerprint=f"fp{i}",
            )

        code, payload = ledger_search.search_ledger({"limit": ["2"], "offset": ["0"]})
        self.assertEqual(code, 200)
        self.assertEqual(payload["total"], 5)
        self.assertEqual(payload["limit"], 2)
        self.assertEqual(payload["offset"], 0)
        self.assertEqual(len(payload["rows"]), 2)
        # Newest first
        self.assertEqual(payload["rows"][0]["fingerprint"], "fp4")
        self.assertEqual(payload["rows"][1]["fingerprint"], "fp3")

        code2, payload2 = ledger_search.search_ledger({"limit": ["2"], "offset": ["2"]})
        self.assertEqual(code2, 200)
        self.assertEqual(payload2["total"], 5)
        self.assertEqual([r["fingerprint"] for r in payload2["rows"]], ["fp2", "fp1"])

    def test_sort_and_date_range_use_statement_month(self) -> None:
        from api import ledger_search

        db = self._fresh_db()
        # Old purchase, billed in Dec 2024 — should sort as newest
        self._insert(
            db,
            date="2023-01-10",
            debit=100,
            source="Installment",
            fingerprint="fp_inst",
            statement_month="2024-12",
        )
        self._insert(
            db,
            date="2024-06-15",
            debit=50,
            source="RecentPurchase",
            fingerprint="fp_june",
        )
        self._insert(
            db,
            date="2024-11-01",
            debit=20,
            source="Nov",
            fingerprint="fp_nov",
        )

        code, payload = ledger_search.search_ledger({"limit": ["10"]})
        self.assertEqual(code, 200)
        fps = [r["fingerprint"] for r in payload["rows"]]
        self.assertEqual(fps[0], "fp_inst")  # effective 2024-12-01
        self.assertEqual(fps[1], "fp_nov")
        self.assertEqual(fps[2], "fp_june")

        # Date range on effective date includes installment, excludes June purchase
        code2, payload2 = ledger_search.search_ledger(
            {"date_from": ["2024-11-01"], "date_to": ["2024-12-31"]}
        )
        self.assertEqual(code2, 200)
        fps2 = {r["fingerprint"] for r in payload2["rows"]}
        self.assertEqual(fps2, {"fp_inst", "fp_nov"})

        # Month token uses effective month
        code3, payload3 = ledger_search.search_ledger({"q": ["2024-12"]})
        self.assertEqual(code3, 200)
        self.assertEqual(payload3["total"], 1)
        self.assertEqual(payload3["rows"][0]["fingerprint"], "fp_inst")

    def test_missing_ledger_returns_503(self) -> None:
        from api import ledger_search

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["FINANCE_WORKSPACE_ROOT"] = tmp.name
        with patch("dotenv.load_dotenv"):
            importlib.reload(config_mod)

        code, payload = ledger_search.search_ledger({})
        self.assertEqual(code, 503)
        self.assertFalse(payload["ok"])

    def test_bulk_patch_category_and_rejects_fingerprint_fields(self) -> None:
        import json

        from api import ledger_search

        db = self._fresh_db()
        id_a = self._insert(
            db,
            date="2024-01-01",
            debit=10,
            source="A",
            fingerprint="fp_a",
            category="Old",
        )
        id_b = self._insert(
            db,
            date="2024-01-02",
            debit=20,
            source="B",
            fingerprint="fp_b",
            category="Old",
        )

        bad_code, bad = ledger_search.handle_bulk_patch(
            json.dumps(
                {"ids": [id_a], "patch": {"תאריך": "2024-02-01"}}
            ).encode("utf-8")
        )
        self.assertEqual(bad_code, 400)
        self.assertFalse(bad["ok"])

        code, payload = ledger_search.handle_bulk_patch(
            json.dumps(
                {
                    "ids": [id_a, id_b],
                    "patch": {"קטגוריה": "Food", "excluded_from_calculations": 1},
                }
            ).encode("utf-8")
        )
        # excluded + category together — only one key allowed? Plan says patch can have
        # category OR exclude. Our API allows both safe keys in one patch.
        self.assertIn(code, (200, 207))
        self.assertEqual(payload["updated"], 2)

        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                'SELECT id, "קטגוריה", excluded_from_calculations FROM ledger_transaction ORDER BY id'
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(rows[0][1], "Food")
        self.assertEqual(rows[0][2], 1)
        self.assertEqual(rows[1][1], "Food")
        self.assertEqual(rows[1][2], 1)


if __name__ == "__main__":
    unittest.main()
