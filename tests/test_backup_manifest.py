"""Tests for pipeline backup snapshots and manifest (MIG-B1, MIG-B3)."""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest


class BackupManifestTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_create_backup_and_parse_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            import config as config_mod

            importlib.reload(config_mod)
            try:
                compiled = os.path.join(tmp, "data", "export", "compiled")
                static = os.path.join(tmp, "data", "static")
                private = os.path.join(tmp, "data", "private")
                webdata = os.path.join(tmp, "web", "data")
                os.makedirs(compiled, exist_ok=True)
                os.makedirs(static, exist_ok=True)
                os.makedirs(private, exist_ok=True)
                os.makedirs(webdata, exist_ok=True)
                with open(os.path.join(compiled, "export_marker.txt"), "w", encoding="utf-8") as f:
                    f.write("ok\n")
                with open(os.path.join(static, "static_marker.txt"), "w", encoding="utf-8") as f:
                    f.write("ok\n")
                with open(os.path.join(private, "providers.json"), "w", encoding="utf-8") as f:
                    f.write('{"version":1}\n')
                with open(os.path.join(webdata, "placeholder.txt"), "w", encoding="utf-8") as f:
                    f.write("k\n1\n")

                from pipeline.backup import MANIFEST_FILENAME, create_critical_paths_backup, load_manifest

                root, manifest = create_critical_paths_backup()
                self.assertTrue(os.path.isdir(root))
                mpath = os.path.join(root, MANIFEST_FILENAME)
                self.assertTrue(os.path.isfile(mpath))

                loaded = load_manifest(mpath)
                self.assertEqual(loaded["schema_version"], manifest["schema_version"])
                self.assertIn("data/export/compiled", loaded["included_top_level"])
                self.assertIn("data/static", loaded["included_top_level"])
                self.assertIn("data/private", loaded["included_top_level"])
                self.assertIn("web/data", loaded["included_top_level"])
                self.assertGreaterEqual(int(loaded.get("total_bytes", 0)), 1)

                self.assertTrue(
                    os.path.isfile(os.path.join(root, "data", "export", "compiled", "export_marker.txt"))
                )
            finally:
                os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
                importlib.reload(config_mod)

    def test_divergence_match_then_mismatch_after_edit(self) -> None:
        from ledger import migrate_ledger_db
        from pipeline.backup import (
            create_critical_paths_backup,
            divergence_report,
        )

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            import config as config_mod

            importlib.reload(config_mod)
            try:
                migrate_ledger_db()
                import sqlite3

                conn = sqlite3.connect(config_mod.ledger_db_file)
                try:
                    conn.execute(
                        """
                        INSERT INTO ledger_transaction (
                          "תאריך", "בחובה", "בזכות", "מקור עסקה",
                          "fingerprint", ingested_at
                        ) VALUES ('2024-01-01', 1, 0, 'A', 'fp_div', '2024-01-01')
                        """
                    )
                    conn.commit()
                finally:
                    conn.close()

                create_critical_paths_backup()
                before = divergence_report()
                self.assertTrue(before.get("ok"))
                self.assertTrue(before.get("match"), before)

                conn = sqlite3.connect(config_mod.ledger_db_file)
                try:
                    conn.execute(
                        'UPDATE ledger_transaction SET "בחובה" = 99 WHERE fingerprint = ?',
                        ("fp_div",),
                    )
                    conn.commit()
                finally:
                    conn.close()

                after = divergence_report()
                self.assertTrue(after.get("ok"))
                self.assertFalse(after.get("match"), after)
            finally:
                os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
                importlib.reload(config_mod)


if __name__ == "__main__":
    unittest.main()
