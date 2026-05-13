"""Tests for hist portfolio valuation CSV -> trade_portfolio_position import."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

_CSV_HEADER = (
    "snapshot_date,portfolio_account,security_number,security_name,"
    "segment_index,valuation_kind,quantity,last_price,value_ils,"
    "ils_per_usd_close,yahoo_ticker,price_source,is_usd_denominated\n"
)


def _load_import_tool():
    path = _ROOT / "tools" / "import_hist_portfolio_valuation.py"
    spec = importlib.util.spec_from_file_location("_hist_portfolio_val_imp", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_MOD = None


def _mod():
    global _MOD
    if _MOD is None:
        _MOD = _load_import_tool()
    return _MOD


class ImportHistPortfolioValuationTests(unittest.TestCase):
    def test_upsert_subset_preserves_other_pks(self) -> None:
        mod = _mod()
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "ledger.sqlite")
            csv1 = os.path.join(tmp, "a.csv")
            csv2 = os.path.join(tmp, "b.csv")

            with open(csv1, "w", encoding="utf-8", newline="") as f:
                f.write(_CSV_HEADER)
                f.write(
                    "2024-01-01,954-TEST/1,1,One,0,month_01,10,100,1000,3.5,SPY,yahoo,False\n"
                )
                f.write(
                    "2024-01-01,954-TEST/1,2,Two,0,month_01,20,200,4000,3.5,SPY,yahoo,False\n"
                )
                f.write(
                    "2024-01-02,954-TEST/1,3,Three,0,month_01,30,300,9000,3.5,SPY,yahoo,False\n"
                )

            with open(csv2, "w", encoding="utf-8", newline="") as f:
                f.write(_CSV_HEADER)
                f.write(
                    "2024-01-01,954-TEST/1,1,One,0,month_01,10,111,1110,3.5,SPY,yahoo,False\n"
                )
                f.write(
                    "2024-01-01,954-TEST/1,2,Two,0,month_01,20,222,4440,3.5,SPY,yahoo,False\n"
                )

            mod.import_csv_to_trade_portfolio(csv1, db_path=db)

            conn = sqlite3.connect(db)
            try:
                n = conn.execute("SELECT COUNT(*) FROM trade_portfolio_position").fetchone()[0]
                v1 = conn.execute(
                    "SELECT last_price FROM trade_portfolio_position "
                    "WHERE snapshot_date=? AND portfolio_account=? AND security_number=?",
                    ("2024-01-01", "954-TEST/1", "1"),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(n, 3)
            self.assertAlmostEqual(float(v1), 100.0, places=3)

            mod.import_csv_to_trade_portfolio(csv2, db_path=db)

            conn = sqlite3.connect(db)
            try:
                n2 = conn.execute("SELECT COUNT(*) FROM trade_portfolio_position").fetchone()[0]
                v1b = conn.execute(
                    "SELECT last_price, value_ils FROM trade_portfolio_position "
                    "WHERE snapshot_date=? AND portfolio_account=? AND security_number=?",
                    ("2024-01-01", "954-TEST/1", "1"),
                ).fetchone()
                v2b = conn.execute(
                    "SELECT last_price FROM trade_portfolio_position "
                    "WHERE snapshot_date=? AND portfolio_account=? AND security_number=?",
                    ("2024-01-01", "954-TEST/1", "2"),
                ).fetchone()[0]
                v3 = conn.execute(
                    "SELECT last_price FROM trade_portfolio_position "
                    "WHERE snapshot_date=? AND portfolio_account=? AND security_number=?",
                    ("2024-01-02", "954-TEST/1", "3"),
                ).fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(n2, 3)
            self.assertAlmostEqual(float(v1b[0]), 111.0, places=3)
            self.assertAlmostEqual(float(v1b[1]), 1110.0, places=3)
            self.assertAlmostEqual(float(v2b), 222.0, places=3)
            self.assertAlmostEqual(float(v3), 300.0, places=3)

    def test_dry_run_leaves_db_unchanged(self) -> None:
        mod = _mod()
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "ledger.sqlite")
            csv_path = os.path.join(tmp, "one.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                f.write(_CSV_HEADER)
                f.write(
                    "2024-02-01,954-TEST/9,9,Nine,0,month_01,9,9,81,3.5,SPY,yahoo,False\n"
                )

            mod.import_csv_to_trade_portfolio(csv_path, db_path=db)
            conn = sqlite3.connect(db)
            try:
                n_before = conn.execute("SELECT COUNT(*) FROM trade_portfolio_position").fetchone()[0]
                lp = conn.execute(
                    "SELECT last_price FROM trade_portfolio_position "
                    "WHERE snapshot_date=? AND portfolio_account=? AND security_number=?",
                    ("2024-02-01", "954-TEST/9", "9"),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(n_before, 1)
            self.assertAlmostEqual(float(lp), 9.0, places=3)

            out = mod.import_csv_to_trade_portfolio(
                csv_path,
                db_path=db,
                dry_run=True,
            )
            self.assertTrue(out["dry_run"])
            self.assertEqual(out["rows"], 1)
            self.assertEqual(out["would_replace"], 1)
            self.assertEqual(out["would_insert"], 0)
            self.assertEqual(len(out["sample"]), 1)

            conn = sqlite3.connect(db)
            try:
                n_after = conn.execute("SELECT COUNT(*) FROM trade_portfolio_position").fetchone()[0]
                lp2 = conn.execute(
                    "SELECT last_price FROM trade_portfolio_position "
                    "WHERE snapshot_date=? AND portfolio_account=? AND security_number=?",
                    ("2024-02-01", "954-TEST/9", "9"),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(n_after, 1)
            self.assertAlmostEqual(float(lp2), 9.0, places=3)

    def test_optional_price_multiplier_column(self) -> None:
        mod = _mod()
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "ledger.sqlite")
            hdr = _CSV_HEADER.strip() + ",price_multiplier\n"
            csv_path = os.path.join(tmp, "m.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                f.write(hdr)
                f.write(
                    "2024-03-01,954-MULT/1,1,One,0,month_01,10,100,1000,3.5,SPY,yahoo,False,0.1\n"
                )

            mod.import_csv_to_trade_portfolio(csv_path, db_path=db)
            conn = sqlite3.connect(db)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                m = conn.execute(
                    "SELECT price_multiplier FROM trade_portfolio_position_multiplier "
                    "WHERE portfolio_account=? AND security_number=?",
                    ("954-MULT/1", "1"),
                ).fetchone()
                val = conn.execute(
                    "SELECT value_ils FROM trade_portfolio_position "
                    "WHERE snapshot_date=? AND portfolio_account=? AND security_number=?",
                    ("2024-03-01", "954-MULT/1", "1"),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertIsNotNone(m)
            self.assertAlmostEqual(float(m[0]), 0.1, places=6)
            self.assertAlmostEqual(float(val), 100.0, places=6)

    def test_value_ils_uses_ledger_multiplier_when_csv_omits_cell(self) -> None:
        mod = _mod()
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "ledger.sqlite")
            seed_csv = os.path.join(tmp, "_seed.csv")
            with open(seed_csv, "w", encoding="utf-8", newline="") as f:
                f.write(_CSV_HEADER)
            mod.import_csv_to_trade_portfolio(seed_csv, db_path=db)

            csv_path = os.path.join(tmp, "ledger_mult.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                f.write(_CSV_HEADER)
                f.write(
                    "2024-04-01,954-LED/1,7,Seven,0,month_01,5,200,999999,3.5,SPY,yahoo,False\n"
                )

            conn = sqlite3.connect(db)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.executescript(
                    """
                    INSERT INTO portfolio_instrument (portfolio_account, security_number)
                    VALUES ('954-LED/1', '7');
                    INSERT INTO trade_portfolio_position_multiplier
                        (portfolio_account, security_number, security_name, price_multiplier)
                    VALUES ('954-LED/1', '7', 'Seven', 0.01);
                    """
                )
                conn.commit()
            finally:
                conn.close()

            mod.import_csv_to_trade_portfolio(csv_path, db_path=db)
            conn = sqlite3.connect(db)
            try:
                val = conn.execute(
                    "SELECT value_ils FROM trade_portfolio_position "
                    "WHERE snapshot_date=? AND portfolio_account=? AND security_number=?",
                    ("2024-04-01", "954-LED/1", "7"),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertAlmostEqual(float(val), 10.0, places=6)


if __name__ == "__main__":
    unittest.main()
