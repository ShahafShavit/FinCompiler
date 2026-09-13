"""WAL connect helper, request-path migrate skip, dashboard cache lock."""

from __future__ import annotations

import importlib
import os
import tempfile
import threading
import unittest
from unittest.mock import patch


class LedgerConnectTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def _workspace(self):
        tmp = tempfile.TemporaryDirectory()
        os.environ["FINANCE_WORKSPACE_ROOT"] = tmp.name
        import config as config_mod

        with patch("dotenv.load_dotenv"):
            importlib.reload(config_mod)
        from ledger import migrate_ledger_db

        migrate_ledger_db()
        self._tmp = tmp
        return config_mod

    def test_connect_ledger_sets_wal_busy_timeout_and_fk(self) -> None:
        config_mod = self._workspace()
        try:
            from ledger import connect_ledger

            conn = connect_ledger(config_mod.ledger_db_file)
            try:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                self.assertEqual(str(mode).lower(), "wal")
                busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
                self.assertEqual(int(busy), 5000)
                fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
                self.assertEqual(int(fk), 1)
            finally:
                conn.close()
        finally:
            self._tmp.cleanup()

    def test_readonly_connect_does_not_call_migrate(self) -> None:
        self._workspace()
        try:
            from api.dashboard import summary
            from api.categorize_queue import summary as cat_summary

            with patch("ledger.store.migrate_ledger_db") as mocked:
                payload = summary()
                cat = cat_summary()
                mocked.assert_not_called()
            self.assertTrue(payload.get("ok"))
            self.assertIn("open_count", cat)
        finally:
            self._tmp.cleanup()

    def test_dashboard_cache_survives_concurrent_reads(self) -> None:
        self._workspace()
        try:
            from api.dashboard import handle_dashboard_request

            errors: list[BaseException] = []

            def worker() -> None:
                try:
                    out = handle_dashboard_request("summary", {})
                    if not out.get("ok"):
                        errors.append(RuntimeError(out))
                except BaseException as e:  # noqa: BLE001
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
        finally:
            self._tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
