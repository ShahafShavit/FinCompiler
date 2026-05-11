"""Tests for trade-portfolio SpreadsheetML parse and SQLite upsert."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pipeline.route_inbox import classify_download_basename
from pipeline.trade_portfolio_import import (
    parse_trade_portfolio_workbook,
    upsert_trade_portfolio_snapshot,
)


FIXTURE_MINIMAL = Path(__file__).resolve().parent / "fixtures" / "trade_portfolio_minimal.xls"
FIXTURE_LEUMI_ACHZAKOT = Path(__file__).resolve().parent / "fixtures" / "אחזקות.xls"


class TradePortfolioImportTests(unittest.TestCase):
    def test_classify_achzakot_filename(self) -> None:
        self.assertEqual(classify_download_basename("אחזקות.xls"), "trade_portfolio")

    def test_classify_trade_portfolio_ascii(self) -> None:
        self.assertEqual(classify_download_basename("my-trade-portfolio.xlsx"), "trade_portfolio")

    def test_classify_holdings_over_transactions(self) -> None:
        self.assertEqual(classify_download_basename("יתרות.xls"), "holdings")

    def test_parse_fixture_snapshot_date(self) -> None:
        snap, pf, rows = parse_trade_portfolio_workbook(str(FIXTURE_MINIMAL))
        self.assertEqual(snap, "2026-05-11")
        self.assertEqual(pf, "954-037317/51")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["security_number"], "111")
        self.assertEqual(rows[0]["security_name"], "Alpha ETF")
        self.assertEqual(rows[0]["snapshot_date"], "2026-05-11")
        self.assertEqual(rows[1]["security_number"], "222")

    def test_parse_leumi_achzakot_fixture_redacted(self) -> None:
        """Leumi-shaped SpreadsheetML (redacted fixture): leading whitespace before ``<?xml`` (``ET.parse`` would fail)."""
        if not FIXTURE_LEUMI_ACHZAKOT.is_file():
            self.skipTest(f"missing fixture {FIXTURE_LEUMI_ACHZAKOT}")
        snap, pf, rows = parse_trade_portfolio_workbook(str(FIXTURE_LEUMI_ACHZAKOT))
        self.assertEqual(snap, "2026-01-01")
        self.assertEqual(pf, "123-123123/12")
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]["security_number"], "1231231")
        self.assertEqual(rows[0]["security_name"], "AABBCCDD")
        self.assertAlmostEqual(rows[0]["value_ils"], 12312.12, places=2)
        self.assertAlmostEqual(rows[0]["daily_change_pct"], 0.0033, places=6)

    def test_upsert_replaces_snapshot_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "ledger.sqlite")
            _, _, rows = parse_trade_portfolio_workbook(str(FIXTURE_MINIMAL))
            r1 = upsert_trade_portfolio_snapshot(rows, db_path=db)
            self.assertEqual(r1["inserted"], 2)
            conn = sqlite3.connect(db)
            try:
                n = conn.execute("SELECT COUNT(*) FROM trade_portfolio_position").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(n, 2)

            r2 = upsert_trade_portfolio_snapshot(rows[:1], db_path=db)
            self.assertEqual(r2["inserted"], 1)
            conn = sqlite3.connect(db)
            try:
                n = conn.execute("SELECT COUNT(*) FROM trade_portfolio_position").fetchone()[0]
                sec = conn.execute(
                    "SELECT security_number FROM trade_portfolio_position WHERE snapshot_date = ?",
                    ("2026-05-11",),
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(n, 1)
            self.assertEqual(sec, [("111",)])


if __name__ == "__main__":
    unittest.main()
