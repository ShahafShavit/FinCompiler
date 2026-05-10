"""Dashboard category-period window: all, calendar year, custom YM range, exclusions."""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


class DashboardPeriodWindowTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_period_all_sums_non_excluded_only(self) -> None:
        import config as config_mod

        from pipeline.ledger import migrate_ledger_db
        from api import dashboard_tx_sql

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            migrate_ledger_db()
            db = config_mod.ledger_db_file
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    """
                    INSERT INTO ledger_transaction (
                      "תאריך", "בחובה", "בזכות", "מקור עסקה", "fingerprint", ingested_at, "קטגוריה",
                      excluded_from_calculations
                    ) VALUES
                      ('2023-06-10', 100.0, 0, 'P', 'fp_2023', '2023-06-10', 'Eat', 0),
                      ('2024-01-10', 40.0, 0, 'P', 'fp_2024a', '2024-01-10', 'Eat', 0),
                      ('2024-08-10', 60.0, 0, 'P', 'fp_2024b', '2024-08-10', 'Ride', 0),
                      ('2025-01-05', 999.0, 0, 'P', 'fp_exc', '2025-01-05', 'Ghost', 1),
                      ('2025-03-10', 300.0, 0, 'P', 'fp_2025', '2025-03-10', 'Shop', 0)
                    """
                )
                conn.commit()

                inc, exp, _n, rows = dashboard_tx_sql.category_period_stats(conn, "all", limit=40)
                self.assertAlmostEqual(inc, 0.0, places=6)
                self.assertAlmostEqual(exp, 500.0, places=6)
                by_cat = {r["category"]: r["expense"] for r in rows}
                self.assertAlmostEqual(by_cat["Eat"], 140.0, places=6)
                self.assertAlmostEqual(by_cat["Ride"], 60.0, places=6)
                self.assertAlmostEqual(by_cat["Shop"], 300.0, places=6)
                self.assertNotIn("Ghost", by_cat)
            finally:
                conn.close()

    def test_period_calendar_year_filters_effective_ym(self) -> None:
        import config as config_mod

        from pipeline.ledger import migrate_ledger_db
        from api import dashboard_tx_sql

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            migrate_ledger_db()
            db = config_mod.ledger_db_file
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    """
                    INSERT INTO ledger_transaction (
                      "תאריך", "בחובה", "בזכות", "מקור עסקה", "fingerprint", ingested_at, "קטגוריה",
                      excluded_from_calculations
                    ) VALUES
                      ('2023-06-10', 100.0, 0, 'P', 'fp_2023', '2023-06-10', 'Eat', 0),
                      ('2024-01-10', 40.0, 0, 'P', 'fp_2024a', '2024-01-10', 'Eat', 0),
                      ('2024-08-10', 60.0, 0, 'P', 'fp_2024b', '2024-08-10', 'Ride', 0),
                      ('2025-01-05', 999.0, 0, 'P', 'fp_exc', '2025-01-05', 'Ghost', 1),
                      ('2025-03-10', 300.0, 0, 'P', 'fp_2025', '2025-03-10', 'Shop', 0)
                    """
                )
                conn.commit()

                _inc, exp, _n, rows = dashboard_tx_sql.category_period_stats(conn, "2024", limit=40)
                self.assertAlmostEqual(exp, 100.0, places=6)
                cats = {r["category"] for r in rows}
                self.assertEqual(cats, {"Eat", "Ride"})
            finally:
                conn.close()

    def test_custom_start_end_ym_inclusive_order_independent(self) -> None:
        import config as config_mod

        from pipeline.ledger import migrate_ledger_db
        from api import dashboard_tx_sql

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            migrate_ledger_db()
            db = config_mod.ledger_db_file
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    """
                    INSERT INTO ledger_transaction (
                      "תאריך", "בחובה", "בזכות", "מקור עסקה", "fingerprint", ingested_at, "קטגוריה",
                      excluded_from_calculations
                    ) VALUES
                      ('2023-06-10', 100.0, 0, 'P', 'fp_2023', '2023-06-10', 'Eat', 0),
                      ('2024-01-10', 40.0, 0, 'P', 'fp_2024a', '2024-01-10', 'Eat', 0),
                      ('2024-08-10', 60.0, 0, 'P', 'fp_2024b', '2024-08-10', 'Ride', 0),
                      ('2025-01-05', 999.0, 0, 'P', 'fp_exc', '2025-01-05', 'Ghost', 1),
                      ('2025-03-10', 300.0, 0, 'P', 'fp_2025', '2025-03-10', 'Shop', 0)
                    """
                )
                conn.commit()

                _e1, exp_fwd, _n1, rows_fwd = dashboard_tx_sql.category_period_stats(
                    conn, "12m", limit=40, start_ym="2024-01", end_ym="2024-06"
                )
                _e2, exp_rev, _n2, rows_rev = dashboard_tx_sql.category_period_stats(
                    conn, "12m", limit=40, start_ym="2024-06", end_ym="2024-01"
                )
                self.assertAlmostEqual(exp_fwd, 40.0, places=6)
                self.assertAlmostEqual(exp_rev, 40.0, places=6)
                self.assertEqual({r["category"] for r in rows_fwd}, {"Eat"})
                self.assertEqual({r["category"] for r in rows_rev}, {"Eat"})
            finally:
                conn.close()

    def test_effective_month_bounds(self) -> None:
        import config as config_mod

        from pipeline.ledger import migrate_ledger_db
        from api import dashboard_tx_sql

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            migrate_ledger_db()
            db = config_mod.ledger_db_file
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    """
                    INSERT INTO ledger_transaction (
                      "תאריך", "בחובה", "בזכות", "מקור עסקה", "fingerprint", ingested_at, "קטגוריה",
                      excluded_from_calculations
                    ) VALUES
                      ('2023-06-10', 100.0, 0, 'P', 'fp_2023', '2023-06-10', 'Eat', 0),
                      ('2024-01-10', 40.0, 0, 'P', 'fp_2024a', '2024-01-10', 'Eat', 0),
                      ('2024-08-10', 60.0, 0, 'P', 'fp_2024b', '2024-08-10', 'Ride', 0),
                      ('2025-01-05', 999.0, 0, 'P', 'fp_exc', '2025-01-05', 'Ghost', 1),
                      ('2025-03-10', 300.0, 0, 'P', 'fp_2025', '2025-03-10', 'Shop', 0)
                    """
                )
                conn.commit()

                lo, hi = dashboard_tx_sql.effective_month_bounds(conn)
                self.assertEqual(lo, "2023-06")
                self.assertEqual(hi, "2025-03")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
