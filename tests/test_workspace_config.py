"""
Tests for FINANCE_WORKSPACE_ROOT: isolated data/ (including export/) and web/ without touching repo trees.

After tests, ``config`` is reloaded with FINANCE_WORKSPACE_ROOT unset (default layout).
"""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from unittest.mock import patch


class WorkspaceRootTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_default_paths_relative_to_cwd(self) -> None:
        # .env may set FINANCE_WORKSPACE_ROOT; skip load_dotenv on reload so this test
        # checks the "unset" layout deterministically.
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        with patch("dotenv.load_dotenv"):
            importlib.reload(config_mod)
        root = config_mod.workspace_root()
        self.assertEqual(root, "")
        # No FINANCE_WORKSPACE_ROOT: paths are normalized but not anchored to another drive
        self.assertIn("data", config_mod.download_inbox_dir.replace("\\", "/"))
        norm = config_mod.compiled_file.replace("\\", "/")
        self.assertIn("data/export", norm)

    def test_custom_root_prefixes_all_major_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            import config as config_mod

            importlib.reload(config_mod)
            try:
                norm_tmp = os.path.abspath(os.path.normpath(tmp))
                for path in (
                    config_mod.download_inbox_dir,
                    config_mod.compiled_file,
                    config_mod.ledger_db_file,
                    config_mod.providers_file,
                    config_mod.stores_to_categories_file,
                    config_mod.fingerprint_db_file,
                    os.path.join(config_mod.web_dir.rstrip(os.sep), "data"),
                    config_mod.backup_parent_dir,
                    config_mod.holdings_inbox_dir,
                    config_mod.transactions_clean_dir,
                ):
                    ap = os.path.abspath(os.path.normpath(path))
                    try:
                        common = os.path.commonpath([norm_tmp, ap])
                    except ValueError:
                        self.fail(f"path not under workspace: {path!r} vs root {norm_tmp!r}")
                    self.assertEqual(
                        common,
                        norm_tmp,
                        f"expected under {norm_tmp}, got {path!r}",
                    )
            finally:
                os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
                importlib.reload(config_mod)


if __name__ == "__main__":
    unittest.main()
