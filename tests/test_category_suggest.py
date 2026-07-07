"""
kNN category suggestion tests.

Run from repo root:
  python -m unittest tests.test_category_suggest -v
"""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from api.categorize import CategorizeFile, FluidStorePrompt, NewStorePrompt
from ledger.category_suggest import (
    CorpusRow,
    _query_row_from_mapping,
    suggest_categories,
    suggest_categories_for_row,
)
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
) -> dict:
    return {
        "תאריך": "2024-01-01",
        "בחובה": expense,
        "בזכות": income,
        "מקור עסקה": store,
        "פירוט נוסף": extra,
        "תאור מורחב": details,
        "4 ספרות": None,
        "קטגוריה": category,
        "notes": None,
    }


def _seed_ledger_with_rows(tmp: str, rows: list[dict]) -> str:
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
    return db


def _corpus_row(
    *,
    payee: str,
    category: str,
    details: str = "",
    expense: float = 10.0,
    fingerprint: str = "",
) -> CorpusRow:
    row = _base_tx_row(store=payee, category=category, details=details, expense=expense)
    mapped = _query_row_from_mapping(row)
    return CorpusRow(
        fingerprint=fingerprint or f"fp-{payee}-{category}",
        payee=mapped.payee,
        description=mapped.description,
        amount=mapped.amount,
        direction=mapped.direction,
        category=category,
        payee_norm=mapped.payee_norm,
        desc_tokens=mapped.desc_tokens,
    )


class CategorySuggestUnitTests(unittest.TestCase):
    def test_empty_corpus_returns_no_suggestions(self) -> None:
        query = _base_tx_row(store="NewShop", category="")
        out = suggest_categories(query, [])
        self.assertEqual(out, [])

    def test_same_payee_high_confidence(self) -> None:
        corpus = [
            _corpus_row(payee="Shufersal", category="Groceries", expense=120.0),
            _corpus_row(payee="Shufersal", category="Groceries", expense=95.0),
            _corpus_row(payee="Shufersal", category="Groceries", expense=88.0),
            _corpus_row(payee="Other", category="Transport", expense=15.0),
        ]
        query = _base_tx_row(store="Shufersal", category="", expense=100.0)
        out = suggest_categories(query, corpus, k=3, min_confidence=0.35)
        self.assertGreaterEqual(len(out), 1)
        self.assertEqual(out[0].category, "Groceries")
        self.assertGreaterEqual(out[0].confidence_pct, 70.0)

    def test_cross_payee_description_match(self) -> None:
        corpus = [
            _corpus_row(payee="VendorA", category="Software", details="cursor subscription monthly"),
            _corpus_row(payee="VendorA", category="Software", details="cursor subscription monthly"),
            _corpus_row(payee="VendorB", category="Food", details="lunch cafe"),
        ]
        query = _base_tx_row(
            store="UnknownVendor",
            category="",
            details="cursor subscription monthly",
            expense=20.0,
        )
        out = suggest_categories(query, corpus, k=3, min_confidence=0.35)
        self.assertGreaterEqual(len(out), 1)
        self.assertEqual(out[0].category, "Software")

    def test_excludes_current_row_from_neighbors(self) -> None:
        fp = "same-row-fp"
        corpus = [
            _corpus_row(
                payee="OnlyStore",
                category="Rent",
                expense=3000.0,
                fingerprint=fp,
            )
        ]
        query = {**_base_tx_row(store="OnlyStore", category="", expense=3000.0), "fingerprint": fp}
        out = suggest_categories(query, corpus, k=3, min_confidence=0.0)
        self.assertEqual(out, [])

    def test_low_confidence_hidden(self) -> None:
        corpus = [
            _corpus_row(payee="Alpha", category="A", expense=10.0),
            _corpus_row(payee="Beta", category="B", expense=90.0),
            _corpus_row(payee="Gamma", category="C", expense=200.0),
        ]
        query = _base_tx_row(store="TotallyDifferent", category="", expense=500.0)
        out = suggest_categories(query, corpus, k=3, min_confidence=0.35)
        self.assertEqual(out, [])


class CategorySuggestLedgerTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_suggest_categories_for_row_from_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed_ledger_with_rows(
                tmp,
                [
                    _base_tx_row(store="Mega", category="Groceries", expense=50.0),
                    _base_tx_row(store="Mega", category="Groceries", expense=60.0),
                    _base_tx_row(store="GasCo", category="Transport", expense=200.0),
                ],
            )
            query = _base_tx_row(store="Mega", category="", expense=55.0)
            out = suggest_categories_for_row(db, query, min_confidence=0.35)
            self.assertGreaterEqual(len(out), 1)
            self.assertEqual(out[0].category, "Groceries")

    def test_fluid_prompt_includes_category_suggestions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rows = [
                _base_tx_row(store="FluidShop", category="Food", expense=30.0),
                _base_tx_row(store="FluidShop", category="Food", expense=35.0),
                _base_tx_row(store="FluidShop", category="", expense=32.0),
            ]
            db = _seed_ledger_with_rows(tmp, rows)
            from ledger import import_stores_to_ledger_from_dataframe

            import_stores_to_ledger_from_dataframe(
                pd.DataFrame(
                    [{"store_name": "FluidShop", "category": "Food", "is_static": 0}],
                ),
                db,
                replace=True,
            )
            cf = CategorizeFile(ledger_db_path=db, materialize_transactions=False)
            uncategorized = rows[-1]
            uncategorized["fingerprint"] = generate_transaction_fingerprint(pd.Series(uncategorized))
            prompt = cf.build_manual_prompt_for_row(pd.Series(uncategorized))
            self.assertIsInstance(prompt, FluidStorePrompt)
            self.assertGreaterEqual(len(prompt.category_suggestions), 1)
            self.assertEqual(prompt.category_suggestions[0].category, "Food")

    def test_new_store_prompt_includes_category_suggestions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed_ledger_with_rows(
                tmp,
                [
                    _base_tx_row(store="Known", category="Utilities", expense=80.0),
                    _base_tx_row(store="Known", category="Utilities", expense=90.0),
                    _base_tx_row(store="BrandNew", category="", expense=85.0),
                ],
            )
            cf = CategorizeFile(ledger_db_path=db, materialize_transactions=False)
            query = _base_tx_row(store="BrandNew", category="", expense=85.0)
            query["fingerprint"] = generate_transaction_fingerprint(pd.Series(query))
            prompt = cf.build_manual_prompt_for_row(pd.Series(query))
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
