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
                webdata = os.path.join(tmp, "web", "data")
                os.makedirs(compiled, exist_ok=True)
                os.makedirs(static, exist_ok=True)
                os.makedirs(webdata, exist_ok=True)
                with open(os.path.join(compiled, "compiled.csv"), "w", encoding="utf-8") as f:
                    f.write("a,b\n1,2\n")
                with open(os.path.join(static, "stores_to_categories.csv"), "w", encoding="utf-8") as f:
                    f.write("store,category\nx,y\n")
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
                self.assertIn("web/data", loaded["included_top_level"])
                self.assertGreaterEqual(int(loaded.get("total_bytes", 0)), 1)

                self.assertTrue(
                    os.path.isfile(os.path.join(root, "data", "export", "compiled", "compiled.csv"))
                )
            finally:
                os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
                importlib.reload(config_mod)


if __name__ == "__main__":
    unittest.main()
