"""Ledger constraint audit passes on a fresh migrated empty DB."""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import config


class LedgerConstraintAuditTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        importlib.reload(config)

    def test_fresh_db_passes_audit(self) -> None:
        from pipeline.ledger_constraint_audit import audit_ledger_constraints
        from pipeline.ledger_migrate import migrate_ledger_db

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config)

            migrate_ledger_db(config.ledger_db_file)
            conn = sqlite3.connect(config.ledger_db_file)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                report = audit_ledger_constraints(conn)
            finally:
                conn.close()

            self.assertTrue(report.ok, msg=report.violations)


if __name__ == "__main__":
    unittest.main()
