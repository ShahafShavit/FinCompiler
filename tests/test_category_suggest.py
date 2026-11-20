"""
CatBoost category suggestion tests.

Run from repo root:
  PYTHONPATH=app/backend python -m unittest tests.test_category_suggest -v
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from api.categorize import CategorizeFile, FluidStorePrompt, NewStorePrompt
from ledger.category_model import (
    StubEmbedder,
    load_stored_suggestions,
)
from ledger.category_suggest import suggest_categories_for_row
from ledger.category_suggest_job import run_category_suggest_refresh
from pipeline.fingerprint import generate_transaction_fingerprint


def _reload_cfg():
    import config as config_mod

    importlib.reload(config_mod)
    return config_mod


def _base_tx_row(
    *,
    store: str = "TestStore",
    category: str = "Food",
    details: str | None = None,
    extra: str | None = None,
    expense: float = 42.0,
    income: float = 0.0,
    date: str = "2024-01-01",
) -> dict:
    return {
        "תאריך": date,
        "בחובה": expense,
        "בזכות": income,
        "מקור עסקה": store,
        "פירוט נוסף": extra,
        "תאור מורחב": details,
        "4 ספרות": None,
        "קטגוריה": category,
        "notes": None,
    }


def _seed_ledger_with_rows(tmp: str, rows: list[dict]) -> tuple[str, object]:
    os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
    with patch("dotenv.load_dotenv"):
        cfg = _reload_cfg()
    from ledger import migrate_ledger_db, upsert_compiled_dataframe_to_ledger

    db = cfg.ledger_db_file
    migrate_ledger_db(db)
    framed: list[dict] = []
    for r in rows:
        if "fingerprint" not in r:
            framed.append({**r, "fingerprint": generate_transaction_fingerprint(pd.Series(r))})
        else:
            framed.append(r)
    upsert_compiled_dataframe_to_ledger(pd.DataFrame(framed), db)
    return db, cfg


def _micro_rule_training_rows() -> list[dict]:
    """Labeled examples for the three plan micro-rules (need ≥2 per class)."""
    rows: list[dict] = []
    # Cloud Infrastructure: AMZN + AWS + outflow > 50
    for i, amt in enumerate((120.0, 80.0, 200.0, 55.0)):
        rows.append(
            _base_tx_row(
                store="AMZN Mktp",
                category="Cloud Infrastructure",
                details="AWS usage invoice",
                expense=amt,
                date=f"2024-01-{i + 1:02d}",
            )
        )
    # Office Supplies: AMZN + small outflow
    for i, amt in enumerate((12.0, 8.0, 19.0, 5.0)):
        rows.append(
            _base_tx_row(
                store="AMZN Retail",
                category="Office Supplies",
                details="order household",
                expense=amt,
                date=f"2024-02-{i + 1:02d}",
            )
        )
    # Refund: STATION + inflow > 0
    for i, amt in enumerate((30.0, 45.0, 60.0, 15.0)):
        rows.append(
            _base_tx_row(
                store="SHELL STATION",
                category="Refund",
                details="fuel refund",
                expense=0.0,
                income=amt,
                date=f"2024-03-{i + 1:02d}",
            )
        )
    return rows


class CategorySuggestCatBoostTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_micro_rules_text_and_amounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            labeled = _micro_rule_training_rows()
            cloud_q = _base_tx_row(
                store="AMZN WEB",
                category="",
                details="AWS monthly bill",
                expense=90.0,
                date="2024-06-01",
            )
            supplies_q = _base_tx_row(
                store="AMZN Store",
                category="",
                details="kitchen order",
                expense=14.0,
                date="2024-06-02",
            )
            refund_q = _base_tx_row(
                store="BP STATION",
                category="",
                details="card refund",
                expense=0.0,
                income=25.0,
                date="2024-06-03",
            )
            db, cfg = _seed_ledger_with_rows(tmp, labeled + [cloud_q, supplies_q, refund_q])
            model_path = cfg.category_catboost_model_file
            meta_path = cfg.category_catboost_meta_file

            run_category_suggest_refresh(
                db,
                embedder=None,  # text+amounts only (no MiniLM)
                iterations=200,
                force_retrain=True,
            )
            self.assertTrue(os.path.isfile(model_path))
            self.assertTrue(os.path.isfile(meta_path))

            cloud_fp = generate_transaction_fingerprint(pd.Series(cloud_q))
            supplies_fp = generate_transaction_fingerprint(pd.Series(supplies_q))
            refund_fp = generate_transaction_fingerprint(pd.Series(refund_q))

            cloud = load_stored_suggestions(db, cloud_fp)
            supplies = load_stored_suggestions(db, supplies_fp)
            refund = load_stored_suggestions(db, refund_fp)

            self.assertGreaterEqual(len(cloud), 1)
            self.assertEqual(cloud[0].category, "Cloud Infrastructure")
            self.assertGreaterEqual(len(supplies), 1)
            self.assertEqual(supplies[0].category, "Office Supplies")
            self.assertGreaterEqual(len(refund), 1)
            self.assertEqual(refund[0].category, "Refund")

            # Suggest path reads the table; does not write categories.
            conn = sqlite3.connect(db)
            try:
                before = conn.execute(
                    'SELECT COUNT(*) FROM ledger_transaction WHERE TRIM(COALESCE("קטגוריה", \'\')) = \'\''
                ).fetchone()[0]
            finally:
                conn.close()
            out = suggest_categories_for_row(
                db,
                {**cloud_q, "fingerprint": cloud_fp},
            )
            conn = sqlite3.connect(db)
            try:
                after = conn.execute(
                    'SELECT COUNT(*) FROM ledger_transaction WHERE TRIM(COALESCE("קטגוריה", \'\')) = \'\''
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(before, after)
            self.assertEqual(out[0].category, "Cloud Infrastructure")

    def test_stub_embedder_centroid_features_wire_into_train(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            labeled = _micro_rule_training_rows()
            query = _base_tx_row(
                store="AMZN WEB",
                category="",
                details="AWS monthly bill",
                expense=90.0,
                date="2024-07-01",
            )
            db, cfg = _seed_ledger_with_rows(tmp, labeled + [query])
            run_category_suggest_refresh(
                db,
                embedder=StubEmbedder(),
                iterations=60,
                force_retrain=True,
            )
            from ledger.category_model import load_model_meta

            meta = load_model_meta(cfg.category_catboost_meta_file)
            self.assertIsNotNone(meta)
            assert meta is not None
            self.assertTrue(meta.has_embeddings)
            self.assertGreaterEqual(len(meta.sim_categories), 3)
            fp = generate_transaction_fingerprint(pd.Series(query))
            sugg = load_stored_suggestions(db, fp)
            self.assertGreaterEqual(len(sugg), 1)
            self.assertEqual(sugg[0].category, "Cloud Infrastructure")

    def test_suggest_does_not_write_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            labeled = _micro_rule_training_rows()
            query = _base_tx_row(
                store="AMZN WEB",
                category="",
                details="AWS monthly bill",
                expense=90.0,
                date="2024-08-01",
            )
            db, _cfg = _seed_ledger_with_rows(tmp, labeled + [query])
            run_category_suggest_refresh(db, embedder=None, iterations=100, force_retrain=True)
            fp = generate_transaction_fingerprint(pd.Series(query))
            conn = sqlite3.connect(db)
            try:
                cat_before = conn.execute(
                    'SELECT "קטגוריה" FROM ledger_transaction WHERE fingerprint = ?',
                    (fp,),
                ).fetchone()[0]
            finally:
                conn.close()
            suggest_categories_for_row(db, {**query, "fingerprint": fp})
            conn = sqlite3.connect(db)
            try:
                cat_after = conn.execute(
                    'SELECT "קטגוריה" FROM ledger_transaction WHERE fingerprint = ?',
                    (fp,),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(cat_before, cat_after)
            self.assertFalse(str(cat_after or "").strip())


class CategorySuggestQueueTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_fluid_prompt_includes_stored_suggestions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            labeled = [
                _base_tx_row(
                    store="FluidShop",
                    category="Food",
                    details="lunch cafe meal",
                    expense=30.0,
                    date="2024-01-01",
                ),
                _base_tx_row(
                    store="FluidShop",
                    category="Food",
                    details="dinner cafe meal",
                    expense=35.0,
                    date="2024-01-02",
                ),
                _base_tx_row(
                    store="FluidShop",
                    category="Food",
                    details="breakfast cafe meal",
                    expense=28.0,
                    date="2024-01-06",
                ),
                _base_tx_row(
                    store="GasCo",
                    category="Transport",
                    details="fuel pump diesel",
                    expense=200.0,
                    date="2024-01-03",
                ),
                _base_tx_row(
                    store="GasCo",
                    category="Transport",
                    details="fuel pump gasoline",
                    expense=180.0,
                    date="2024-01-04",
                ),
                _base_tx_row(
                    store="GasCo",
                    category="Transport",
                    details="fuel pump station",
                    expense=150.0,
                    date="2024-01-07",
                ),
            ]
            uncategorized = _base_tx_row(
                store="FluidShop",
                category="",
                details="lunch cafe meal",
                expense=32.0,
                date="2024-01-05",
            )
            db, _cfg = _seed_ledger_with_rows(tmp, labeled + [uncategorized])
            from ledger import import_stores_to_ledger_from_dataframe

            import_stores_to_ledger_from_dataframe(
                pd.DataFrame(
                    [{"store_name": "FluidShop", "category": "Food", "is_static": 0}],
                ),
                db,
                replace=True,
            )
            run_category_suggest_refresh(db, embedder=None, iterations=150, force_retrain=True)
            cf = CategorizeFile(ledger_db_path=db, materialize_transactions=False)
            uncategorized["fingerprint"] = generate_transaction_fingerprint(pd.Series(uncategorized))
            prompt = cf.build_manual_prompt_for_row(pd.Series(uncategorized))
            self.assertIsInstance(prompt, FluidStorePrompt)
            self.assertGreaterEqual(len(prompt.category_suggestions), 1)
            self.assertEqual(prompt.category_suggestions[0].category, "Food")

    def test_new_store_prompt_includes_category_suggestions_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            labeled = _micro_rule_training_rows()
            brand_new = _base_tx_row(
                store="BrandNewVendor",
                category="",
                details="AWS cloud",
                expense=100.0,
                date="2024-09-01",
            )
            db, _cfg = _seed_ledger_with_rows(tmp, labeled + [brand_new])
            run_category_suggest_refresh(db, embedder=None, iterations=150, force_retrain=True)
            cf = CategorizeFile(ledger_db_path=db, materialize_transactions=False)
            brand_new["fingerprint"] = generate_transaction_fingerprint(pd.Series(brand_new))
            prompt = cf.build_manual_prompt_for_row(pd.Series(brand_new))
            self.assertIsInstance(prompt, NewStorePrompt)
            display = prompt.to_display_dict()
            self.assertIn("category_suggestions", display)
            suggestions = display["category_suggestions"]
            self.assertIsInstance(suggestions, list)
            if suggestions:
                self.assertIn("category", suggestions[0])
                self.assertIn("confidence_pct", suggestions[0])


if __name__ == "__main__":
    unittest.main()
