"""
Prediction explorer API tests.

  PYTHONPATH=app/backend python -m unittest tests.test_prediction_api -v
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from pipeline.fingerprint import generate_transaction_fingerprint


def _reload_cfg():
    import config as config_mod

    importlib.reload(config_mod)
    return config_mod


def _base_tx_row(
    *,
    store: str,
    category: str,
    details: str = "",
    expense: float = 10.0,
    income: float = 0.0,
    date: str = "2024-01-01",
) -> dict:
    return {
        "תאריך": date,
        "בחובה": expense,
        "בזכות": income,
        "מקור עסקה": store,
        "פירוט נוסף": None,
        "תאור מורחב": details,
        "4 ספרות": None,
        "קטגוריה": category,
        "notes": None,
    }


class PredictionApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_status_sample_and_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                cfg = _reload_cfg()
            from ledger import migrate_ledger_db, upsert_compiled_dataframe_to_ledger
            from ledger.category_suggest_job import run_category_suggest_refresh
            from api import prediction

            db = cfg.ledger_db_file
            migrate_ledger_db(db)
            rows = []
            for i, amt in enumerate((120.0, 80.0, 200.0, 55.0)):
                rows.append(
                    _base_tx_row(
                        store="AMZN Mktp",
                        category="Cloud",
                        details="AWS usage invoice",
                        expense=amt,
                        date=f"2024-01-{i + 1:02d}",
                    )
                )
            for i, amt in enumerate((12.0, 8.0, 19.0, 5.0)):
                rows.append(
                    _base_tx_row(
                        store="AMZN Retail",
                        category="Office",
                        details="order household",
                        expense=amt,
                        date=f"2024-02-{i + 1:02d}",
                    )
                )
            framed = []
            for r in rows:
                framed.append({**r, "fingerprint": generate_transaction_fingerprint(pd.Series(r))})
            upsert_compiled_dataframe_to_ledger(pd.DataFrame(framed), db)
            run_category_suggest_refresh(db, embedder=None, iterations=120, force_retrain=True)

            with patch("api.prediction._ledger_path", return_value=db):
                code, status = prediction.handle_status()
                self.assertEqual(code, 200)
                self.assertTrue(status["model_ready"])

                code, sample = prediction.handle_sample("n=5&labeled_only=1")
                self.assertEqual(code, 200)
                self.assertGreaterEqual(sample["n"], 1)
                self.assertIn("predictions", sample["rows"][0])

                code, scored = prediction.handle_score(
                    {
                        "payee": "AMZN Mktp",
                        "details": "AWS usage invoice",
                        "outflow": 90.0,
                        "inflow": 0.0,
                        "top_n": 3,
                    }
                )
                self.assertEqual(code, 200)
                self.assertGreaterEqual(len(scored["predictions"]), 1)
                self.assertEqual(scored["top_category"], "Cloud")

                # JSON body path
                code, scored2 = prediction.handle_score(
                    json.dumps(
                        {
                            "payee": "AMZN Retail",
                            "details": "order household",
                            "outflow": 11.0,
                            "inflow": 0.0,
                        }
                    )
                )
                self.assertEqual(code, 200)
                self.assertEqual(scored2["top_category"], "Office")

                code, trained = prediction.handle_train()
                self.assertEqual(code, 200)
                self.assertTrue(trained.get("trained"))
                self.assertTrue(trained.get("model_ready"))


if __name__ == "__main__":
    unittest.main()
