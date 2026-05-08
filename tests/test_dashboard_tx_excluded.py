"""Dashboard transaction SQL excludes ``excluded_from_calculations = 1`` rows."""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


class DashboardTxExcludedTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_cashflow_monthly_omits_excluded(self) -> None:
        import config as config_mod

        from web_control import dashboard_tx_sql

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from pipeline.ledger import migrate_ledger_db

            migrate_ledger_db()
            db = config_mod.ledger_db_file
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    """
                    INSERT INTO ledger_transaction (
                      "תאריך", "בחובה", "בזכות", "מקור עסקה", "fingerprint", ingested_at, "קטגוריה",
                      excluded_from_calculations
                    ) VALUES ('2024-06-01', 100.0, 0, 'P', 'fp1', '2024-06-01', 'C', 1),
                             ('2024-06-15', 200.0, 0, 'Q', 'fp2', '2024-06-01', 'C', 0)
                    """
                )
                conn.commit()

                rows = dashboard_tx_sql.cashflow_monthly(conn, months=120)
                by_m = {r["month"]: r for r in rows}
                june = by_m.get("2024-06")
                self.assertIsNotNone(june)
                self.assertAlmostEqual(float(june["expense"]), 200.0, places=6)
                self.assertAlmostEqual(float(june["income"]), 0.0, places=6)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
