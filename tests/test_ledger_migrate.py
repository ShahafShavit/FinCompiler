"""
Tests for ``pipeline/ledger.migrate_ledger_db`` — baseline schema and idempotent migrate.
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


class LedgerMigrateTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_migrate_twice_idempotent_and_schema(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from pipeline.ledger import migrate_ledger_db

            db_path = config_mod.ledger_db_file
            migrate_ledger_db()
            migrate_ledger_db()

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='ledger_transaction'"
                ).fetchone()
                self.assertIsNotNone(row)
                max_v = conn.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
                self.assertEqual(max_v, 13)

                conn.execute(
                    'INSERT INTO ledger_transaction ("fingerprint", ingested_at, "תאריך") '
                    'VALUES ("fp-a", "2024-01-15", "2024-01-10")'
                )
                conn.commit()
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        'INSERT INTO ledger_transaction ("fingerprint", ingested_at, "תאריך") '
                        'VALUES ("fp-a", "2024-01-15", "2024-01-11")'
                    )
            finally:
                conn.close()

    def test_v11_merges_legacy_none_vs_nan_fingerprint_suffixes(self) -> None:
        """Rows that only differed by ``none`` vs ``nan`` optional-text artifacts collapse to one."""
        import config as config_mod
        import pandas as pd

        from pipeline.csv_handler import generate_transaction_fingerprint
        from pipeline.ledger import migrate_ledger_db

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            db_path = config_mod.ledger_db_file
            migrate_ledger_db()
            fp_old_a = "2024-09-05:bh311.00_bz0.00:kspאקספרסגמא:תשלום1מתוך2none"
            fp_old_b = "2024-09-05:bh311.00_bz0.00:kspאקספרסגמא:תשלום1מתוך2nan"
            s = pd.Series(
                {
                    "תאריך": "2024-09-05",
                    "בחובה": 311.0,
                    "בזכות": 0.0,
                    "מקור עסקה": "KSP אקספרס-גמא",
                    "פירוט נוסף": "תשלום 1  מתוך 2",
                    "תאור מורחב": None,
                }
            )
            fp_canon = generate_transaction_fingerprint(s)
            self.assertIsNotNone(fp_canon)

            conn = sqlite3.connect(db_path)
            try:
                conn.execute("DELETE FROM schema_migrations WHERE version >= 11")
                conn.commit()
                conn.execute(
                    """
                    INSERT INTO ledger_transaction (
                        "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב",
                        "fingerprint", ingested_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                    """,
                    ("2024-09-05", 311.0, 0.0, "KSP אקספרס-גמא", "תשלום 1  מתוך 2", fp_old_a, "2024-09-05"),
                )
                conn.execute(
                    """
                    INSERT INTO ledger_transaction (
                        "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב",
                        "fingerprint", ingested_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                    """,
                    ("2024-09-05", 311.0, 0.0, "KSP אקספרס-גמא", "תשלום 1  מתוך 2", fp_old_b, "2024-09-06"),
                )
                conn.commit()
            finally:
                conn.close()

            migrate_ledger_db()

            conn = sqlite3.connect(db_path)
            try:
                n = conn.execute("SELECT COUNT(*) FROM ledger_transaction").fetchone()[0]
                self.assertEqual(n, 1)
                row = conn.execute(
                    'SELECT fingerprint, ingested_at FROM ledger_transaction LIMIT 1'
                ).fetchone()
                self.assertEqual(row[0], fp_canon)
                self.assertEqual(row[1], "2024-09-06")
            finally:
                conn.close()

    def test_v13_drops_similar_category_pair(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from pipeline.ledger import migrate_ledger_db

            db_path = config_mod.ledger_db_file
            migrate_ledger_db()

            conn = sqlite3.connect(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE similar_category_pair (
                        p1 TEXT NOT NULL,
                        p2 TEXT NOT NULL,
                        PRIMARY KEY (p1, p2)
                    );
                    INSERT INTO similar_category_pair (p1, p2) VALUES ('a', 'b');
                    DELETE FROM schema_migrations WHERE version = 13;
                    """
                )
                conn.commit()
            finally:
                conn.close()

            migrate_ledger_db()

            conn = sqlite3.connect(db_path)
            try:
                gone = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='similar_category_pair'"
                ).fetchone()
                self.assertIsNone(gone)
                max_v = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
                self.assertEqual(max_v, 13)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
