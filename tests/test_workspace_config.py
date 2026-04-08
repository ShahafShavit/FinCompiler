"""
Tests for FINANCE_WORKSPACE_ROOT: isolated data/export/web without touching repo trees.

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
        import config

        importlib.reload(config)

    def test_default_paths_relative_to_cwd(self) -> None:
        # .env may set FINANCE_WORKSPACE_ROOT; skip load_dotenv on reload so this test
        # checks the "unset" layout deterministically.
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config

        with patch("dotenv.load_dotenv"):
            importlib.reload(config)
        root = config.workspace_root()
        self.assertEqual(root, "")
        # No FINANCE_WORKSPACE_ROOT: paths are normalized but not anchored to another drive
        self.assertIn("data", config.download_inbox_dir.replace("\\", "/"))
        self.assertIn("export", config.compiled_file.replace("\\", "/"))

    def test_custom_root_prefixes_all_major_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            import config

            importlib.reload(config)
            try:
                norm_tmp = os.path.abspath(os.path.normpath(tmp))
                for path in (
                    config.download_inbox_dir,
                    config.compiled_file,
                    config.stores_to_categories_file,
                    config.fingerprint_db_file,
                    config.web_totals_file,
                    config.holdings_inbox_dir,
                    config.transactions_clean_dir,
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
                importlib.reload(config)


if __name__ == "__main__":
    unittest.main()
