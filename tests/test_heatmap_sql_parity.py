"""Parity: SQL month×category heatmap vs legacy full-DataFrame builder."""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np


class HeatmapSqlParityTests(unittest.TestCase):
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

    def _insert(
        self,
        db: str,
        *,
        date: str,
        debit: float = 0,
        credit: float = 0,
        source: str,
        category: str,
        fingerprint: str,
        statement_month: str | None = None,
        excluded: int = 0,
    ) -> None:
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                """
                INSERT INTO ledger_transaction (
                  "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב",
                  "fingerprint", "קטגוריה", statement_month, ingested_at,
                  excluded_from_calculations
                ) VALUES (?, ?, ?, ?, '', '', ?, ?, ?, ?, ?)
                """,
                (
                    date,
                    debit,
                    credit,
                    source,
                    fingerprint,
                    category,
                    statement_month,
                    date,
                    excluded,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def test_sql_bundle_matches_pandas_values(self) -> None:
        from api import heatmap
        from ledger import load_transactions_dataframe_from_ledger
        from ledger.heatmap_sql import fetch_month_category_sums

        db = self._fresh_db()
        self._insert(
            db,
            date="2024-01-15",
            debit=100,
            source="A",
            category="Food",
            fingerprint="fp1",
        )
        self._insert(
            db,
            date="2024-01-20",
            debit=50,
            source="B",
            category="Food",
            fingerprint="fp2",
        )
        self._insert(
            db,
            date="2024-02-10",
            credit=200,
            source="Job",
            category="Salary",
            fingerprint="fp3",
        )
        self._insert(
            db,
            date="2024-02-01",
            debit=30,
            source="C",
            category="Transport",
            fingerprint="fp4",
            statement_month="2024-03",
        )
        self._insert(
            db,
            date="2024-01-01",
            debit=999,
            source="X",
            category="Ignore",
            fingerprint="fp5",
            excluded=1,
        )

        df = load_transactions_dataframe_from_ledger(db)
        pandas_bundle = heatmap._build_bundle_from_dataframe(df, db)
        sql_rows = fetch_month_category_sums(db)
        sql_bundle = heatmap._build_bundle_from_month_sums(sql_rows, db)
        self.assertIsNotNone(sql_bundle)
        assert sql_bundle is not None

        for name in ("expenses_pivot", "income_pivot", "net_pivot"):
            left = getattr(pandas_bundle, name)
            right = getattr(sql_bundle, name)
            # Align indices/columns (order may differ before reorder — compare after reorder both)
            common_idx = left.index.intersection(right.index)
            common_cols = left.columns.intersection(right.columns)
            self.assertTrue(len(common_idx) > 0)
            l = left.reindex(index=common_idx, columns=common_cols).fillna(0)
            r = right.reindex(index=common_idx, columns=common_cols).fillna(0)
            np.testing.assert_allclose(l.values, r.values, atol=0.01, err_msg=name)

        # Excluded row must not appear
        self.assertNotIn("Ignore", list(sql_bundle.expenses_pivot.columns))
        # statement_month buckets into March
        self.assertIn("2024-03", sql_bundle.expenses_pivot.index)

    def test_auto_categorize_skips_full_df_when_not_materialized(self) -> None:
        from api.categorize import CategorizeFile

        db = self._fresh_db()
        self._insert(
            db,
            date="2024-01-01",
            debit=10,
            source="Shop",
            category="",
            fingerprint="fp_uncat",
        )
        with patch(
            "ledger.load_transactions_dataframe_from_ledger"
        ) as load_df:
            cf = CategorizeFile(ledger_db_path=db, materialize_transactions=False)
            cf.auto_categorize()
            load_df.assert_not_called()


if __name__ == "__main__":
    unittest.main()
