"""Tests for NULL fingerprint backfill (same algorithm as compile)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pipeline.fingerprint import generate_transaction_fingerprint
from pipeline.ledger import (
    backfill_null_fingerprints,
    list_would_duplicate_null_rows,
    migrate_ledger_db,
)


def _insert_minimal_row(
    conn: sqlite3.Connection,
    *,
    fp: str | None,
    תאריך: str = "2025-06-01",
    בחובה: float = 10.0,
    בזכות: float = 0.0,
    מקור_עסקה: str = "TestStore",
) -> None:
    conn.execute(
        """
        INSERT INTO ledger_transaction (
            "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב",
            "fingerprint", ingested_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            תאריך,
            בחובה,
            בזכות,
            מקור_עסקה,
            None,
            None,
            fp,
            "2025-06-15",
        ),
    )


class LedgerFingerprintBackfillTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)

    def test_backfill_sets_fingerprint_matching_algorithm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                import importlib
                import config as cfg

                importlib.reload(cfg)
            db = cfg.ledger_db_file
            migrate_ledger_db(db)
            conn = sqlite3.connect(db)
            try:
                _insert_minimal_row(conn, fp=None)
                conn.commit()
            finally:
                conn.close()

            want = generate_transaction_fingerprint(
                {
                    "תאריך": "2025-06-01",
                    "בחובה": 10.0,
                    "בזכות": 0.0,
                    "מקור עסקה": "TestStore",
                    "פירוט נוסף": None,
                    "תאור מורחב": None,
                }
            )
            self.assertIsNotNone(want)

            stats = backfill_null_fingerprints(db, dry_run=False)
            self.assertEqual(stats.examined, 1)
            self.assertEqual(stats.updated, 1)
            self.assertEqual(stats.skipped_uncomputable, 0)
            self.assertEqual(stats.skipped_would_duplicate, 0)

            conn = sqlite3.connect(db)
            try:
                got = conn.execute(
                    'SELECT fingerprint FROM ledger_transaction WHERE "מקור עסקה" = ?',
                    ("TestStore",),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(got, want)

    def test_two_null_rows_distinct_stores_get_distinct_fingerprints(self) -> None:
        """Each row gets a unique canonical ``fingerprint`` (different merchants)."""
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                import importlib
                import config as cfg

                importlib.reload(cfg)
            db = cfg.ledger_db_file
            migrate_ledger_db(db)
            conn = sqlite3.connect(db)
            try:
                _insert_minimal_row(conn, fp=None, מקור_עסקה="StoreA")
                _insert_minimal_row(conn, fp=None, מקור_עסקה="StoreB")
                conn.commit()
            finally:
                conn.close()

            st0, dups0 = list_would_duplicate_null_rows(db)
            self.assertEqual(st0.skipped_would_duplicate, 0)
            self.assertEqual(len(dups0), 0)

            stats = backfill_null_fingerprints(db, dry_run=False)
            self.assertEqual(stats.examined, 2)
            self.assertEqual(stats.updated, 2)
            self.assertEqual(stats.skipped_would_duplicate, 0)

            conn = sqlite3.connect(db)
            try:
                n = conn.execute(
                    "SELECT COUNT(DISTINCT fingerprint) FROM ledger_transaction WHERE fingerprint IS NOT NULL"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(n, 2)


if __name__ == "__main__":
    unittest.main()
