"""
Automated tests for categorization (SQLite ledger: auto pass, web-queue prompt/apply).

Run from repo root:
  python -m unittest tests.test_categorization_logic -v
"""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from api.categorize import CategorizeFile, FluidStorePrompt, NewStorePrompt
from pipeline.fingerprint import generate_transaction_fingerprint


def _reload_cfg():
    import config as config_mod

    importlib.reload(config_mod)
    return config_mod


def _base_tx_row(*, store: str = "TestStore", category: str = "") -> dict:
    return {
        "תאריך": "2024-01-01",
        "בחובה": 0.0,
        "בזכות": 10.0,
        "מקור עסקה": store,
        "פירוט נוסף": None,
        "תאור מורחב": None,
        "4 ספרות": None,
        "קטגוריה": category,
        "notes": None,
    }


def _seed_ledger_with_rows(tmp: str, rows: list[dict], *, stores: pd.DataFrame | None = None) -> tuple[object, str]:
    os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
    with patch("dotenv.load_dotenv"):
        cfg = _reload_cfg()
    from ledger import import_stores_to_ledger_from_dataframe, migrate_ledger_db, upsert_compiled_dataframe_to_ledger

    db = cfg.ledger_db_file
    migrate_ledger_db(db)
    framed: list[dict] = []
    for r in rows:
        if "fingerprint" not in r:
            framed.append({**r, "fingerprint": generate_transaction_fingerprint(pd.Series(r))})
        else:
            framed.append(r)
    upsert_compiled_dataframe_to_ledger(pd.DataFrame(framed), db)
    if stores is not None and not stores.empty:
        import_stores_to_ledger_from_dataframe(stores, db, replace=True)
    return cfg, db


def _category_for_fingerprint(db: str, fp: str) -> str:
    conn = sqlite3.connect(db)
    try:
        r = conn.execute(
            'SELECT "קטגוריה" FROM ledger_transaction WHERE TRIM("fingerprint") = ?',
            (fp.strip(),),
        ).fetchone()
        return "" if r is None else str(r[0] or "")
    finally:
        conn.close()


class CategorizeStorenameTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        _reload_cfg()

    def test_load_stores_empty_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, db = _seed_ledger_with_rows(tmp, [_base_tx_row()])
            cf = CategorizeFile(ledger_db_path=db)
            cf.load_stores()
            self.assertEqual(list(cf.stores_df.columns), ["store_name", "category", "is_static"])
            self.assertEqual(len(cf.stores_df), 0)

    def test_static_store_returns_category_without_prompt(self) -> None:
        stores = pd.DataFrame([{"store_name": "StoreA", "category": "Rent", "is_static": 1}])
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, db = _seed_ledger_with_rows(tmp, [_base_tx_row(store="StoreA")], stores=stores)
            cf = CategorizeFile(ledger_db_path=db)
            cf.load_stores()
            row = pd.Series(_base_tx_row(store="StoreA"))
            row["fingerprint"] = generate_transaction_fingerprint(row)
            out = cf.categorize_storename(row, method="auto")
            self.assertEqual(out, "Rent")

    def test_auto_finds_static_store_not_only_first_row(self) -> None:
        stores = pd.DataFrame(
            [
                {"store_name": "ZZZ_Other", "category": "Misc", "is_static": 1},
                {"store_name": "TargetStore", "category": "Fuel", "is_static": 1},
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, db = _seed_ledger_with_rows(tmp, [_base_tx_row(store="TargetStore")], stores=stores)
            cf = CategorizeFile(ledger_db_path=db)
            cf.load_stores()
            row = pd.Series(_base_tx_row(store="TargetStore"))
            row["fingerprint"] = generate_transaction_fingerprint(row)
            out = cf.categorize_storename(row, method="auto")
            self.assertEqual(out, "Fuel")

    def test_fluid_store_picks_existing_dynamic_category(self) -> None:
        stores = pd.DataFrame(
            [
                {"store_name": "FluidShop", "category": "Food", "is_static": 0},
                {"store_name": "FluidShop", "category": "Drinks", "is_static": 0},
            ]
        )
        row_dict = _base_tx_row(store="FluidShop")
        fp = generate_transaction_fingerprint(pd.Series(row_dict))
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, db = _seed_ledger_with_rows(tmp, [{**row_dict, "fingerprint": fp}], stores=stores)
            cf = CategorizeFile(ledger_db_path=db)
            cf.load_stores()
            row = pd.Series({**row_dict, "fingerprint": fp})
            p = cf.build_manual_prompt_for_row(row)
            self.assertIsInstance(p, FluidStorePrompt)
            cf.apply_manual_http_response(row, "fluid", {"category": "Food"})
            self.assertEqual(_category_for_fingerprint(db, fp), "Food")

    def test_fluid_store_adds_new_category_row(self) -> None:
        stores = pd.DataFrame([{"store_name": "FluidOnly", "category": "Old", "is_static": 0}])
        row_dict = _base_tx_row(store="FluidOnly")
        fp = generate_transaction_fingerprint(pd.Series(row_dict))
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, db = _seed_ledger_with_rows(tmp, [{**row_dict, "fingerprint": fp}], stores=stores)
            cf = CategorizeFile(ledger_db_path=db)
            cf.load_stores()
            row = pd.Series({**row_dict, "fingerprint": fp})
            cf.apply_manual_http_response(row, "fluid", {"category": "BrandNew"})
            cf.load_stores()
            match = cf.stores_df[cf.stores_df["store_name"] == "FluidOnly"]
            self.assertIn("BrandNew", match["category"].tolist())
            self.assertEqual(_category_for_fingerprint(db, fp), "BrandNew")

    def test_new_store_prompt_adds_row(self) -> None:
        stores = pd.DataFrame([{"store_name": "Other", "category": "X", "is_static": 1}])
        row_dict = _base_tx_row(store="UnknownShop")
        fp = generate_transaction_fingerprint(pd.Series(row_dict))
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, db = _seed_ledger_with_rows(tmp, [{**row_dict, "fingerprint": fp}], stores=stores)
            cf = CategorizeFile(ledger_db_path=db)
            cf.load_stores()
            row = pd.Series({**row_dict, "fingerprint": fp})
            p = cf.build_manual_prompt_for_row(row)
            self.assertIsInstance(p, NewStorePrompt)
            cf.apply_manual_http_response(row, "new_store", {"category": "Groceries", "is_static": 1})
            cf.load_stores()
            row_new = cf.stores_df[cf.stores_df["store_name"] == "UnknownShop"]
            self.assertEqual(len(row_new), 1)
            self.assertEqual(int(row_new["is_static"].iloc[0]), 1)
            self.assertEqual(_category_for_fingerprint(db, fp), "Groceries")

    def test_resolve_static_revision_sets_is_static(self) -> None:
        """``store.is_static`` is always 0/1 in SQLite; resolve flow updates it via queue API."""
        stores = pd.DataFrame([{"store_name": "Weird", "category": "Misc", "is_static": 0}])
        row_dict = _base_tx_row(store="Weird")
        fp = generate_transaction_fingerprint(pd.Series(row_dict))
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, db = _seed_ledger_with_rows(tmp, [{**row_dict, "fingerprint": fp}], stores=stores)
            cf = CategorizeFile(ledger_db_path=db)
            cf.load_stores()
            cf.apply_session_resolve_static_revision("Weird", "Misc", 1)
            cf.load_stores()
            iv = int(cf.stores_df.loc[cf.stores_df["store_name"] == "Weird", "is_static"].iloc[0])
            self.assertEqual(iv, 1)

    def test_auto_categorize_forward_fill_smoke(self) -> None:
        stores = pd.DataFrame([{"store_name": "S", "category": "Cat", "is_static": 1}])
        row_dict = _base_tx_row(store="S")
        fp = generate_transaction_fingerprint(pd.Series(row_dict))
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, db = _seed_ledger_with_rows(tmp, [{**row_dict, "fingerprint": fp}], stores=stores)
            cf = CategorizeFile(ledger_db_path=db)
            cf.auto_categorize()
            self.assertEqual(_category_for_fingerprint(db, fp), "Cat")


class FluidPromptSerializationTests(unittest.TestCase):
    """Prompt ``to_display_dict()`` must serialize for the web categorize API (JSON)."""

    def test_display_dict_json_serializable(self) -> None:
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
