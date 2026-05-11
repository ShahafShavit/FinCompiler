"""Tests for ``providers`` (normalize, merge, paths)."""

from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest


class ProvidersTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_normalize_and_merge_keep_password(self) -> None:
        import providers as ps

        base = ps.default_document()
        base["bank"]["credentials"]["username"] = "u1"
        base["bank"]["credentials"]["password"] = "secret1"
        merged = ps.merge_put_update(
            base,
            {"bank": {"credentials": {"username": "u2", "password": ""}}},
        )
        self.assertEqual(merged["bank"]["credentials"]["username"], "u2")
        self.assertEqual(merged["bank"]["credentials"]["password"], "secret1")

    def test_merge_password_null_clears(self) -> None:
        import providers as ps

        base = ps.default_document()
        base["bank"]["credentials"]["password"] = "x"
        merged = ps.merge_put_update(base, {"bank": {"credentials": {"password": None}}})
        self.assertEqual(merged["bank"]["credentials"]["password"], "")

    def test_document_for_api_get_redacts(self) -> None:
        import providers as ps

        doc = ps.default_document()
        doc["bank"]["credentials"]["password"] = "hunter2"
        out = ps.document_for_api_get(doc)
        self.assertTrue(out["bank"]["credentials"]["password_set"])
        self.assertNotIn("password", out["bank"]["credentials"])
        self.assertIn("investment_portfolio", out)
        self.assertTrue(out["investment_portfolio"]["enabled"])

    def test_investment_portfolio_normalize_and_merge(self) -> None:
        import providers as ps

        doc = ps.default_document()
        self.assertTrue(doc["investment_portfolio"]["enabled"])
        raw = ps.normalize_document({"investment_portfolio": {"enabled": False}})
        self.assertFalse(raw["investment_portfolio"]["enabled"])
        merged = ps.merge_put_update(raw, {"investment_portfolio": {"enabled": True}})
        self.assertTrue(merged["investment_portfolio"]["enabled"])
        r = ps.resolve_document(merged)
        self.assertTrue(r.investment_portfolio_enabled)

    def test_providers_file_respects_workspace(self) -> None:
        import config as config_mod
        import providers as ps

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            importlib.reload(config_mod)
            importlib.reload(ps)
            p = ps.providers_file_path()
            self.assertTrue(p.startswith(os.path.normpath(tmp)))
            self.assertTrue(p.endswith("providers.json"))
            try:
                data = ps.default_document()
                data["bank"]["credentials"]["username"] = "x"
                ps.save_document_atomic(data)
                self.assertTrue(os.path.isfile(p))
                with open(p, encoding="utf-8") as f:
                    raw = json.load(f)
                self.assertEqual(raw["bank"]["credentials"]["username"], "x")
            finally:
                os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
                importlib.reload(config_mod)
                importlib.reload(ps)


if __name__ == "__main__":
    unittest.main()
