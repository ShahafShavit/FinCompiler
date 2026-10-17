"""Drop rules exclude uncategorized queue rows. Categorized rows stay included."""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
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
    category: str = "",
    date: str = "2024-01-01",
    detail: str | None = None,
) -> dict:
    return {
        "תאריך": date,
        "בחובה": 0.0,
        "בזכות": 10.0,
        "מקור עסקה": store,
        "פירוט נוסף": detail,
        "תאור מורחב": None,
        "4 ספרות": None,
        "קטגוריה": category,
        "notes": None,
    }


def _seed(tmp: str, rows: list[dict]) -> str:
    os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
    with patch("dotenv.load_dotenv"):
        cfg = _reload_cfg()
    from ledger import migrate_ledger_db, upsert_compiled_dataframe_to_ledger

    db = cfg.ledger_db_file
    migrate_ledger_db(db)
    framed = []
    for r in rows:
        framed.append({**r, "fingerprint": generate_transaction_fingerprint(pd.Series(r))})
    upsert_compiled_dataframe_to_ledger(pd.DataFrame(framed), db)
    return db


def _excluded(db: str, fingerprint: str) -> int:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT excluded_from_calculations FROM ledger_transaction WHERE TRIM(fingerprint) = ?",
            (fingerprint,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise AssertionError(f"missing fingerprint {fingerprint}")
    return int(row[0] or 0)


class DropRulesQueueTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        _reload_cfg()

    def test_discard_excludes_matching_queue_rows_only(self) -> None:
        from api.categorize_queue import discard, next_payload

        rows = [
            _base_tx_row(store="DropMePayee", date="2024-01-01"),
            _base_tx_row(store="DropMePayee", date="2024-01-02"),
            _base_tx_row(store="DropMePayee", date="2024-01-03", category="Food"),
            _base_tx_row(store="KeepMePayee", date="2024-01-04"),
        ]
        fps = [generate_transaction_fingerprint(pd.Series(r)) for r in rows]
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed(tmp, rows)
            err = discard({"prompt_id": fps[0]})
            self.assertIsNone(err)
            self.assertEqual(_excluded(db, fps[0]), 1)
            self.assertEqual(_excluded(db, fps[1]), 1)
            self.assertEqual(_excluded(db, fps[2]), 0)
            self.assertEqual(_excluded(db, fps[3]), 0)

            payload = next_payload()
            pending = payload["pending"]
            self.assertNotEqual(pending.get("kind"), "idle")
            self.assertEqual(pending.get("store_name"), "KeepMePayee")
            self.assertEqual(payload["open_count"], 1)

    def test_put_config_excludes_detail_matches_and_second_sweep_is_zero(self) -> None:
        from api.transaction_drop_rules_config import put_config
        from ledger import exclude_uncategorized_rows_matching_drop_pairs

        rows = [
            _base_tx_row(store="Shop", date="2024-02-01", detail="FEE"),
            _base_tx_row(store="Shop", date="2024-02-02", detail="FEE", category="Bank"),
            _base_tx_row(store="Shop", date="2024-02-03", detail="OTHER"),
        ]
        fps = [generate_transaction_fingerprint(pd.Series(r)) for r in rows]
        body = json.dumps(
            {"version": 1, "rules": [{"column": "פירוט נוסף", "value": "FEE"}]}
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed(tmp, rows)
            status, payload = put_config(body)
            self.assertEqual(status, 200)
            self.assertTrue(payload.get("ok"))
            self.assertEqual(payload.get("queue_excluded"), 1)
            self.assertEqual(_excluded(db, fps[0]), 1)
            self.assertEqual(_excluded(db, fps[1]), 0)
            self.assertEqual(_excluded(db, fps[2]), 0)
            again = exclude_uncategorized_rows_matching_drop_pairs(
                db, [("פירוט נוסף", "FEE")]
            )
            self.assertEqual(again, 0)

    def test_unknown_column_excludes_nothing(self) -> None:
        from ledger import exclude_uncategorized_rows_matching_drop_pairs

        rows = [_base_tx_row(store="Shop", date="2024-03-01")]
        fps = [generate_transaction_fingerprint(pd.Series(r)) for r in rows]
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed(tmp, rows)
            n = exclude_uncategorized_rows_matching_drop_pairs(
                db, [("סוג עסקה", "Shop")]
            )
            self.assertEqual(n, 0)
            self.assertEqual(_excluded(db, fps[0]), 0)

    def test_next_skips_row_that_matches_saved_drop_rule(self) -> None:
        from api.categorize_queue import next_payload
        from pipeline.transaction_drop_rules import save_document_atomic

        rows = [
            _base_tx_row(store="BlockedPayee", date="2024-04-01"),
            _base_tx_row(store="OpenPayee", date="2024-04-02"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            _seed(tmp, rows)
            save_document_atomic(
                {
                    "version": 1,
                    "rules": [{"column": "מקור עסקה", "value": "BlockedPayee"}],
                }
            )
            payload = next_payload()
            pending = payload["pending"]
            self.assertEqual(pending.get("store_name"), "OpenPayee")
            self.assertEqual(payload["open_count"], 1)


if __name__ == "__main__":
    unittest.main()
