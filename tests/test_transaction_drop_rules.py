"""Tests for ``pipeline.transaction_drop_rules`` (materialize-if-missing, validate, append)."""

from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch


class TransactionDropRulesTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)
        import pipeline.transaction_drop_rules as tdr

        importlib.reload(tdr)

    def test_materialize_missing_file_and_dedupe_order(self) -> None:
        import config as config_mod
        import pipeline.transaction_drop_rules as tdr

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)
            importlib.reload(tdr)

            path = config_mod.transaction_drop_rules_file
            self.assertFalse(os.path.isfile(path))
            pairs = tdr.transaction_drop_pairs_from_file()
            self.assertTrue(os.path.isfile(path))
            self.assertGreaterEqual(len(pairs), 10)
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            self.assertEqual(doc.get("version"), 1)
            self.assertIsInstance(doc.get("rules"), list)
            seen: set[tuple[str, str]] = set()
            for r in doc["rules"]:
                key = (r["column"], r["value"])
                self.assertNotIn(key, seen)
                seen.add(key)

    def test_validate_rejects_unknown_rule_keys(self) -> None:
        import pipeline.transaction_drop_rules as tdr

        with self.assertRaises(ValueError):
            tdr.validate_document({"version": 1, "rules": [{"column": "c", "value": "v", "extra": 1}]})

    def test_append_rule_idempotent(self) -> None:
        import config as config_mod
        import pipeline.transaction_drop_rules as tdr

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)
            importlib.reload(tdr)

            tdr.transaction_drop_pairs_from_file()
            col, val = "מקור עסקה", "__test_append_unique__"
            self.assertTrue(tdr.append_rule_if_absent(col, val))
            self.assertFalse(tdr.append_rule_if_absent(col, val))


if __name__ == "__main__":
    unittest.main()
