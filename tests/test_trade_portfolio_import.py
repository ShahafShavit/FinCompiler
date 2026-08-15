"""Tests for trade-portfolio SpreadsheetML parse and SQLite upsert."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pipeline.route_inbox import classify_download_basename
from pipeline.trade_portfolio_import import (
    _coerce_optional_real,
    parse_trade_portfolio_workbook,
    upsert_trade_portfolio_snapshot,
)

_DASH_DAILY_CHANGE_XML = """<?xml version="1.0"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="Sheet1">
  <Table>
   <Row>
    <Cell><Data ss:Type="String">תאריך:</Data></Cell>
    <Cell><Data ss:Type="String">15.08.26</Data></Cell>
    <Cell><Data ss:Type="String">תיק:</Data></Cell>
    <Cell><Data ss:Type="String">954-037317/51</Data></Cell>
   </Row>
   <Row>
    <Cell><Data ss:Type="String">מספר נייר</Data></Cell>
    <Cell><Data ss:Type="String">שם הנייר</Data></Cell>
    <Cell><Data ss:Type="String">שער קניה ממוצע</Data></Cell>
    <Cell><Data ss:Type="String">כמות אחזקה</Data></Cell>
    <Cell><Data ss:Type="String">שער אחרון</Data></Cell>
    <Cell><Data ss:Type="String">שווי אחזקה ב ₪</Data></Cell>
    <Cell><Data ss:Type="String">% שינוי יומי</Data></Cell>
    <Cell><Data ss:Type="String">רווח ב-%</Data></Cell>
    <Cell><Data ss:Type="String">רווח ב ₪</Data></Cell>
    <Cell><Data ss:Type="String">%  מהתיק</Data></Cell>
    <Cell><Data ss:Type="String">שער בסיס</Data></Cell>
    <Cell><Data ss:Type="String">מס התיק</Data></Cell>
   </Row>
   <Row>
    <Cell><Data ss:Type="String">1194976</Data></Cell>
    <Cell><Data ss:Type="String">GLOEQ</Data></Cell>
    <Cell><Data ss:Type="Number">130.62</Data></Cell>
    <Cell><Data ss:Type="Number">229.67</Data></Cell>
    <Cell><Data ss:Type="Number">196.93</Data></Cell>
    <Cell><Data ss:Type="Number">452.29</Data></Cell>
    <Cell><Data ss:Type="String">-</Data></Cell>
    <Cell><Data ss:Type="Number">0.5076</Data></Cell>
    <Cell><Data ss:Type="Number">152.29</Data></Cell>
    <Cell><Data ss:Type="Number">0.0073</Data></Cell>
    <Cell><Data ss:Type="Number">196.93</Data></Cell>
    <Cell><Data ss:Type="String">954-037317/51</Data></Cell>
   </Row>
  </Table>
 </Worksheet>
</Workbook>
"""


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

    def test_upsert_persists_price_multiplier_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "ledger.sqlite")
            _, _, rows = parse_trade_portfolio_workbook(str(FIXTURE_MINIMAL))
            rows[0]["price_multiplier"] = 0.01
            upsert_trade_portfolio_snapshot(rows, db_path=db)
            conn = sqlite3.connect(db)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                m = conn.execute(
                    "SELECT price_multiplier FROM trade_portfolio_position_multiplier "
                    "WHERE portfolio_account=? AND security_number=?",
                    (rows[0]["portfolio_account"], rows[0]["security_number"]),
                ).fetchone()
                m2 = conn.execute(
                    "SELECT price_multiplier FROM trade_portfolio_position_multiplier "
                    "WHERE portfolio_account=? AND security_number=?",
                    (rows[1]["portfolio_account"], rows[1]["security_number"]),
                ).fetchone()
            finally:
                conn.close()
            self.assertIsNotNone(m)
            self.assertAlmostEqual(float(m[0]), 0.01, places=6)
            self.assertIsNotNone(m2)
            self.assertAlmostEqual(float(m2[0]), 1.0, places=6)

    def test_coerce_optional_real_missing_and_percent(self) -> None:
        self.assertIsNone(_coerce_optional_real("-", field="daily_change_pct"))
        self.assertIsNone(_coerce_optional_real(" — ", field="daily_change_pct"))
        self.assertIsNone(_coerce_optional_real("", field="profit_pct"))
        self.assertIsNone(_coerce_optional_real(None, field="value_ils"))
        self.assertAlmostEqual(_coerce_optional_real(0.0033, field="daily_change_pct"), 0.0033, places=6)
        self.assertAlmostEqual(_coerce_optional_real("0.40%", field="daily_change_pct"), 0.004, places=6)
        self.assertAlmostEqual(_coerce_optional_real("12,123.12", field="value_ils"), 12123.12, places=2)
        with self.assertRaises(ValueError):
            _coerce_optional_real("abc", field="daily_change_pct")

    def test_parse_and_upsert_hyphen_daily_change_pct(self) -> None:
        """Bank exports use '-' when daily change is unavailable; STRICT REAL must get NULL."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "achzakot.xls")
            with open(path, "w", encoding="utf-8") as f:
                f.write(_DASH_DAILY_CHANGE_XML)
            snap, pf, rows = parse_trade_portfolio_workbook(path)
            self.assertEqual(snap, "2026-08-15")
            self.assertEqual(pf, "954-037317/51")
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0]["daily_change_pct"])
            self.assertAlmostEqual(rows[0]["profit_pct"], 0.5076, places=4)

            db = os.path.join(tmp, "ledger.sqlite")
            rep = upsert_trade_portfolio_snapshot(rows, db_path=db)
            self.assertEqual(rep["inserted"], 1)
            conn = sqlite3.connect(db)
            try:
                got = conn.execute(
                    "SELECT daily_change_pct, typeof(daily_change_pct) FROM trade_portfolio_position"
                ).fetchone()
            finally:
                conn.close()
            self.assertIsNone(got[0])
            self.assertEqual(got[1], "null")


if __name__ == "__main__":
    unittest.main()
