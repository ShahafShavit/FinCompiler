"""Effective transaction date (statement_month first) for dashboard SQL and summary KPIs."""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pipeline.ledger import LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR, migrate_ledger_db


class EffectiveTxDateWebTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_effective_tx_date_sql_prefers_statement_month(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            db_path = config_mod.ledger_db_file
            migrate_ledger_db(db_path)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO ledger_transaction (
                        "fingerprint", ingested_at, "תאריך", "בחובה", "בזכות", statement_month
                    ) VALUES (?, ?, ?, 0, 0, ?)
                    """,
                    ("fp-one", "2024-01-15", "2024-01-15", "2024-06"),
                )
                conn.commit()
                got = conn.execute(
                    f"SELECT ({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR}) FROM ledger_transaction LIMIT 1"
                ).fetchone()[0]
                self.assertEqual(got, "2024-06-01")
            finally:
                conn.close()

    def test_dashboard_summary_30d_anchor_uses_effective_date(self) -> None:
        import config as config_mod
        from api import dashboard_api

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            db_path = config_mod.ledger_db_file
            migrate_ledger_db(db_path)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO ledger_transaction (
                        "fingerprint", ingested_at, "תאריך", "בחובה", "בזכות", statement_month
                    ) VALUES ('fp-a', '2024-01-15', '2024-01-15', 0, 0, NULL)
                    """
                )
                conn.execute(
                    """
                    INSERT INTO ledger_transaction (
                        "fingerprint", ingested_at, "תאריך", "בחובה", "בזכות", statement_month
                    ) VALUES ('fp-b', '2024-01-01', '2023-06-01', 0, 500, '2024-12')
                    """
                )
                conn.commit()
            finally:
                conn.close()

            with patch.object(dashboard_api, "_ledger_path", return_value=db_path):
                with patch.object(dashboard_api.config, "ledger_db_file", db_path):
                    out = dashboard_api.summary()

            self.assertTrue(out.get("ledger_exists"))
            kpis = out.get("kpis") or {}
            self.assertEqual(kpis.get("income_30d"), 500.0)

    def test_dashboard_tx_sql_30d_includes_row_by_statement_month(self) -> None:
        import config as config_mod
        from api import dashboard_tx_sql

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            db_path = config_mod.ledger_db_file
            migrate_ledger_db(db_path)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO ledger_transaction (
                        "fingerprint", ingested_at, "תאריך", "בחובה", "בזכות", statement_month
                    ) VALUES ('fp-a', '2024-01-15', '2024-01-15', 0, 0, NULL)
                    """
                )
                conn.execute(
                    """
                    INSERT INTO ledger_transaction (
                        "fingerprint", ingested_at, "תאריך", "בחובה", "בזכות", statement_month
                    ) VALUES ('fp-b', '2024-01-01', '2023-06-01', 0, 250, '2024-12')
                    """
                )
                conn.commit()
                tot = conn.execute(
                    f"""WITH {dashboard_tx_sql.TX_NORM},
{dashboard_tx_sql.PERIOD_TX_30D}
SELECT COALESCE(SUM(income_amt), 0) FROM period_tx"""
                ).fetchone()[0]
                self.assertEqual(float(tot), 250.0)
            finally:
                conn.close()

    def test_dashboard_tx_sql_ytd_year_from_effective_max(self) -> None:
        import config as config_mod
        from api import dashboard_tx_sql

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            db_path = config_mod.ledger_db_file
            migrate_ledger_db(db_path)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO ledger_transaction (
                        "fingerprint", ingested_at, "תאריך", "בחובה", "בזכות", statement_month, "קטגוריה"
                    ) VALUES ('fp-old', '2023-06-01', '2023-06-01', 99, 0, NULL, 'x')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO ledger_transaction (
                        "fingerprint", ingested_at, "תאריך", "בחובה", "בזכות", statement_month, "קטגוריה"
                    ) VALUES ('fp-stmt', '2024-01-01', '2024-01-10', 0, 40, '2025-03', 'y')
                    """
                )
                conn.commit()
                exp = conn.execute(
                    f"""WITH {dashboard_tx_sql.TX_NORM},
{dashboard_tx_sql.PERIOD_TX_YTD}
SELECT COALESCE(SUM(expense_amt), 0) FROM period_tx"""
                ).fetchone()[0]
                self.assertEqual(float(exp), 0.0)
                inc = conn.execute(
                    f"""WITH {dashboard_tx_sql.TX_NORM},
{dashboard_tx_sql.PERIOD_TX_YTD}
SELECT COALESCE(SUM(income_amt), 0) FROM period_tx"""
                ).fetchone()[0]
                self.assertEqual(float(inc), 40.0)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
