"""
Automated tests for categorization (auto pass, web-queue prompt/apply, temp files).

Run from repo root:
  python -m unittest tests.test_categorization_logic -v

To exercise the real pipeline against a throwaway tree, set FINANCE_WORKSPACE_ROOT to a
temp directory, ``importlib.reload`` on ``config``, then copy fixtures under that root.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import config
from api.categorize import (
    CategorizeFile,
    FluidStorePrompt,
    NewStorePrompt,
    ResolveStaticPrompt,
)

# Minimal columns required for CategorizeFile.__init__ and categorize_storename.
# Includes legacy **מזהה עסקה** (not a ledger column); production ledger paths use **fingerprint**.
_COMPILED_COLS = [
    "מזהה עסקה",
    "מקור עסקה",
    "תאריך",
    "בחובה",
    "בזכות",
    "קטגוריה",
]


def _minimal_compiled_row(tid="1", store="TestStore", cat=""):
    return {
        "מזהה עסקה": tid,
        "מקור עסקה": store,
        "תאריך": "2024-01-01",
        "בחובה": 0,
        "בזכות": 10,
        "קטגוריה": cat,
    }


class CategorizeStorenameTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.stores_path = os.path.join(self._tmp.name, "stores_to_categories.csv")
        self.compiled_path = os.path.join(self._tmp.name, "compiled.csv")

    def _write_compiled(self, row: dict) -> None:
        pd.DataFrame([row], columns=_COMPILED_COLS).to_csv(self.compiled_path, index=False)

    def _write_stores(self, rows: list[dict]) -> None:
        df = pd.DataFrame(rows)
        df.to_csv(self.stores_path, index=False)

    def test_load_stores_creates_missing_file(self) -> None:
        missing = os.path.join(self._tmp.name, "nested", "stores_to_categories.csv")
        self.assertFalse(os.path.isfile(missing))
        self._write_compiled(_minimal_compiled_row())
        with patch.object(config, "stores_to_categories_file", missing):
            cf = CategorizeFile(self.compiled_path)
            cf.load_stores()
        self.assertTrue(os.path.isfile(missing))
        self.assertEqual(
            list(cf.stores_df.columns),
            ["store_name", "category", "is_static"],
        )
        self.assertEqual(len(cf.stores_df), 0)

    def test_static_store_returns_category_without_prompt(self) -> None:
        self._write_stores(
            [{"store_name": "StoreA", "category": "Rent", "is_static": 1}]
        )
        row = _minimal_compiled_row(store="StoreA")
        with patch.object(config, "stores_to_categories_file", self.stores_path):
            self._write_compiled(row)
            cf = CategorizeFile(self.compiled_path)
            cf.load_stores()
            out = cf.categorize_storename(row, method="auto")
        self.assertEqual(out, "Rent")

    def test_auto_finds_static_store_not_only_first_row(self) -> None:
        """Regression: auto mode must scan the full stores table, not stop after row 0."""
        self._write_stores(
            [
                {"store_name": "ZZZ_Other", "category": "Misc", "is_static": 1},
                {"store_name": "TargetStore", "category": "Fuel", "is_static": 1},
            ]
        )
        row = _minimal_compiled_row(store="TargetStore")
        with patch.object(config, "stores_to_categories_file", self.stores_path):
            self._write_compiled(row)
            cf = CategorizeFile(self.compiled_path)
            cf.load_stores()
            out = cf.categorize_storename(row, method="auto")
        self.assertEqual(out, "Fuel")

    def test_fluid_store_picks_existing_dynamic_category(self) -> None:
        self._write_stores(
            [
                {"store_name": "FluidShop", "category": "Food", "is_static": 0},
                {"store_name": "FluidShop", "category": "Drinks", "is_static": 0},
            ]
        )
        row = _minimal_compiled_row(store="FluidShop")
        with patch.object(config, "stores_to_categories_file", self.stores_path):
            self._write_compiled(row)
            cf = CategorizeFile(self.compiled_path)
            cf.load_stores()
            p = cf.build_manual_prompt_for_row(row)
            self.assertIsInstance(p, FluidStorePrompt)
            cf.apply_manual_http_response(row, "fluid", {"category": "Food"})
        out_df = pd.read_csv(self.compiled_path)
        self.assertEqual(str(out_df.loc[0, "קטגוריה"]), "Food")

    def test_fluid_store_adds_new_category_row(self) -> None:
        self._write_stores(
            [{"store_name": "FluidOnly", "category": "Old", "is_static": 0}],
        )
        row = _minimal_compiled_row(store="FluidOnly")
        with patch.object(config, "stores_to_categories_file", self.stores_path):
            self._write_compiled(row)
            cf = CategorizeFile(self.compiled_path)
            cf.load_stores()
            cf.apply_manual_http_response(row, "fluid", {"category": "BrandNew"})
        after = pd.read_csv(self.stores_path)
        self.assertGreaterEqual(len(after), 2)
        match = after[after["store_name"] == "FluidOnly"]
        self.assertIn("BrandNew", match["category"].tolist())
        out_df = pd.read_csv(self.compiled_path)
        self.assertEqual(str(out_df.loc[0, "קטגוריה"]), "BrandNew")

    def test_new_store_prompt_adds_row(self) -> None:
        self._write_stores(
            [{"store_name": "Other", "category": "X", "is_static": 1}],
        )
        row = _minimal_compiled_row(store="UnknownShop")
        with patch.object(config, "stores_to_categories_file", self.stores_path):
            self._write_compiled(row)
            cf = CategorizeFile(self.compiled_path)
            cf.load_stores()
            p = cf.build_manual_prompt_for_row(row)
            self.assertIsInstance(p, NewStorePrompt)
            cf.apply_manual_http_response(row, "new_store", {"category": "Groceries", "is_static": 1})
        after = pd.read_csv(self.stores_path)
        row_new = after[after["store_name"] == "UnknownShop"]
        self.assertEqual(len(row_new), 1)
        self.assertEqual(int(row_new["is_static"].iloc[0]), 1)
        out_df = pd.read_csv(self.compiled_path)
        self.assertEqual(str(out_df.loc[0, "קטגוריה"]), "Groceries")

    def test_resolve_ambiguous_is_static(self) -> None:
        self._write_stores(
            [{"store_name": "Weird", "category": "Misc", "is_static": -1}],
        )
        row = _minimal_compiled_row(store="Weird")
        with patch.object(config, "stores_to_categories_file", self.stores_path):
            self._write_compiled(row)
            cf = CategorizeFile(self.compiled_path)
            cf.load_stores()
            p = cf.build_manual_prompt_for_row(row)
            self.assertIsInstance(p, ResolveStaticPrompt)
            cf.apply_manual_http_response(row, "resolve_static", {"is_static": 1})
        after = pd.read_csv(self.stores_path)
        self.assertEqual(int(after.loc[after["store_name"] == "Weird", "is_static"].iloc[0]), 1)
        out_df = pd.read_csv(self.compiled_path)
        self.assertEqual(str(out_df.loc[0, "קטגוריה"]), "Misc")

    def test_auto_categorize_does_not_read_fingerprint_sidecar_when_ledger_file_exists(self) -> None:
        """MIG-E3: CSV-mode categorizer must not touch fingerprint_db.csv if ledger.sqlite exists."""
        paths: list[str] = []
        real_read_csv = pd.read_csv

        def _wrap(path, *args, **kwargs):
            paths.append(os.path.normpath(os.path.abspath(str(path))))
            return real_read_csv(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as d:
            compiled = os.path.join(d, "compiled.csv")
            pd.DataFrame(
                [
                    {
                        "fingerprint": "fp1",
                        "מקור עסקה": "S",
                        "תאריך": "2024-01-01",
                        "בחובה": 0,
                        "בזכות": 1,
                        "קטגוריה": "",
                    }
                ]
            ).to_csv(compiled, index=False)
            ledger = os.path.join(d, "ledger.sqlite")
            open(ledger, "wb").close()
            fp_sidecar = os.path.join(d, "fingerprint_db.csv")
            pd.DataFrame([{"fingerprint": "fp1", "category": "NeverUsed"}]).to_csv(
                fp_sidecar, index=False
            )
            stores = os.path.join(d, "stores.csv")
            pd.DataFrame(columns=["store_name", "category", "is_static"]).to_csv(
                stores, index=False
            )
            os.makedirs(os.path.join(d, "bak"), exist_ok=True)
            bak = os.path.join(d, "bak", "transaction_category.csv")
            with patch.object(config, "ledger_db_file", ledger), patch.object(
                config, "fingerprint_db_file", fp_sidecar
            ), patch.object(config, "stores_to_categories_file", stores), patch.object(
                config, "transaction_category_file", bak
            ), patch.object(pd, "read_csv", side_effect=_wrap):
                cf = CategorizeFile(compiled)
                cf.load_stores()
                cf.auto_categorize()
        abs_fp = os.path.normpath(os.path.abspath(fp_sidecar))
        self.assertNotIn(abs_fp, paths)


class FluidPromptSerializationTests(unittest.TestCase):
    """Prompt ``to_display_dict()`` must serialize for the web categorize API (JSON)."""

    def test_display_dict_json_matches_real_csv_cell_types(self) -> None:
        p = FluidStorePrompt(
            store_name=np.str_("Shop"),
            date=pd.Timestamp("2024-06-01"),
            expense=np.int64(0),
            income=np.float64(3.5),
            details=np.float64(np.nan),
            digits=None,
            dynamic_categories=("Food",),
            all_categories=("Food", "Other"),
            prompt_id="pid",
        )
        json.loads(json.dumps(p.to_display_dict(), ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
