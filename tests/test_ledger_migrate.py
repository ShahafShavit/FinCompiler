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
                self.assertEqual(max_v, 10)

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


if __name__ == "__main__":
    unittest.main()
