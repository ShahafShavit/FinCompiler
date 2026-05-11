"""Tests for :func:`ledger.patch_ledger_transaction_by_id`."""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from pipeline.fingerprint import generate_transaction_fingerprint


class LedgerPatchTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def _fresh_db(self):
        import config as config_mod

        tmp = tempfile.TemporaryDirectory()
        os.environ["FINANCE_WORKSPACE_ROOT"] = tmp.name
        with patch("dotenv.load_dotenv"):
            importlib.reload(config_mod)
        from ledger import migrate_ledger_db

        migrate_ledger_db()
        return tmp, config_mod.ledger_db_file

    def _insert_row(
        self,
        conn: sqlite3.Connection,
        *,
        dt: str,
        bh: float,
        bz: float,
        makor: str,
        pirut: str | None,
        teur: str | None,
        fp: str,
        notes: str | None = None,
    ) -> int:
        conn.execute(
            """
            INSERT INTO ledger_transaction (
              "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב",
              "fingerprint", ingested_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (dt, bh, bz, makor, pirut, teur, fp, dt, notes),
        )
        conn.commit()
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return int(rid)

    def test_notes_only_preserves_fingerprint(self) -> None:
        from ledger import patch_ledger_transaction_by_id

        tmp, db_path = self._fresh_db()
        try:
            s = pd.Series(
                {
                    "תאריך": "2024-06-01",
                    "בחובה": 12.5,
                    "בזכות": 0.0,
                    "מקור עסקה": "Coffee Shop",
                    "פירוט נוסף": "",
                    "תאור מורחב": None,
                }
            )
            fp = generate_transaction_fingerprint(s)
            assert fp is not None
            conn = sqlite3.connect(db_path)
            try:
                rid = self._insert_row(
                    conn,
                    dt="2024-06-01",
                    bh=12.5,
                    bz=0.0,
                    makor="Coffee Shop",
                    pirut="",
                    teur=None,
                    fp=fp,
                    notes=None,
                )
            finally:
                conn.close()

            out = patch_ledger_transaction_by_id(db_path, rid, {"notes": "memo"})
            self.assertTrue(out.get("ok"))

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT notes, fingerprint FROM ledger_transaction WHERE id = ?", (rid,)
                ).fetchone()
                self.assertEqual(row[0], "memo")
                self.assertEqual(row[1], fp)
            finally:
                conn.close()
        finally:
            tmp.cleanup()

    def test_fingerprint_field_without_confirmation_rejected(self) -> None:
        from ledger import patch_ledger_transaction_by_id

        tmp, db_path = self._fresh_db()
        try:
            s = pd.Series(
                {
                    "תאריך": "2024-06-02",
                    "בחובה": 5.0,
                    "בזכות": 0.0,
                    "מקור עסקה": "Store A",
                    "פירוט נוסף": "",
                    "תאור מורחב": None,
                }
            )
            fp = generate_transaction_fingerprint(s)
            conn = sqlite3.connect(db_path)
            try:
                rid = self._insert_row(
                    conn,
                    dt="2024-06-02",
                    bh=5.0,
                    bz=0.0,
                    makor="Store A",
                    pirut="",
                    teur=None,
                    fp=fp,
                )
            finally:
                conn.close()

            out = patch_ledger_transaction_by_id(
                db_path, rid, {"מקור עסקה": "Store B"}, confirm_fingerprint_change=False
            )
            self.assertFalse(out.get("ok"))
            self.assertEqual(out.get("error"), "fingerprint_confirmation_required")
        finally:
            tmp.cleanup()

    def test_fingerprint_change_with_phrase_updates_row(self) -> None:
        from ledger import LEDGER_FINGERPRINT_CONFIRM_PHRASE, patch_ledger_transaction_by_id

        tmp, db_path = self._fresh_db()
        try:
            s_a = pd.Series(
                {
                    "תאריך": "2024-06-03",
                    "בחובה": 1.0,
                    "בזכות": 0.0,
                    "מקור עסקה": "Alpha",
                    "פירוט נוסף": "",
                    "תאור מורחב": None,
                }
            )
            fp_a = generate_transaction_fingerprint(s_a)
            conn = sqlite3.connect(db_path)
            try:
                rid = self._insert_row(
                    conn,
                    dt="2024-06-03",
                    bh=1.0,
                    bz=0.0,
                    makor="Alpha",
                    pirut="",
                    teur=None,
                    fp=fp_a,
                )
            finally:
                conn.close()

            out = patch_ledger_transaction_by_id(
                db_path,
                rid,
                {"מקור עסקה": "Beta"},
                confirm_fingerprint_change=True,
                confirm_fingerprint_phrase=LEDGER_FINGERPRINT_CONFIRM_PHRASE,
            )
            self.assertTrue(out.get("ok"))
            self.assertIsNotNone(out.get("fingerprint"))
            self.assertNotEqual(out["fingerprint"], fp_a)

            conn = sqlite3.connect(db_path)
            try:
                makor = conn.execute(
                    'SELECT "מקור עסקה", fingerprint FROM ledger_transaction WHERE id = ?', (rid,)
                ).fetchone()
                self.assertEqual(makor[0], "Beta")
                self.assertEqual(makor[1], out["fingerprint"])
            finally:
                conn.close()
        finally:
            tmp.cleanup()

    def test_fingerprint_collision_returns_error(self) -> None:
        from ledger import LEDGER_FINGERPRINT_CONFIRM_PHRASE, patch_ledger_transaction_by_id

        tmp, db_path = self._fresh_db()
        try:
            basis = {
                "תאריך": "2024-06-04",
                "בחובה": 7.0,
                "בזכות": 0.0,
                "מקור עסקה": "DupTest",
                "פירוט נוסף": "",
                "תאור מורחב": None,
            }
            fp = generate_transaction_fingerprint(pd.Series(basis))
            assert fp is not None

            conn = sqlite3.connect(db_path)
            try:
                rid1 = self._insert_row(
                    conn,
                    dt=basis["תאריך"],
                    bh=basis["בחובה"],
                    bz=basis["בזכות"],
                    makor=basis["מקור עסקה"],
                    pirut="",
                    teur=None,
                    fp=fp,
                )
                rid2 = self._insert_row(
                    conn,
                    dt=basis["תאריך"],
                    bh=basis["בחובה"],
                    bz=basis["בזכות"],
                    makor="Other",
                    pirut="",
                    teur=None,
                    fp=generate_transaction_fingerprint(
                        pd.Series({**basis, "מקור עסקה": "Other"})
                    ),
                )
            finally:
                conn.close()

            out = patch_ledger_transaction_by_id(
                db_path,
                rid2,
                {"מקור עסקה": "DupTest"},
                confirm_fingerprint_change=True,
                confirm_fingerprint_phrase=LEDGER_FINGERPRINT_CONFIRM_PHRASE,
            )
            self.assertFalse(out.get("ok"))
            self.assertEqual(out.get("error"), "fingerprint_conflict")
            self.assertEqual(out.get("conflicting_id"), rid1)
        finally:
            tmp.cleanup()

    def test_excluded_from_calculations_patch(self) -> None:
        from ledger import patch_ledger_transaction_by_id

        tmp, db_path = self._fresh_db()
        try:
            fp = generate_transaction_fingerprint(
                pd.Series(
                    {
                        "תאריך": "2025-03-01",
                        "בחובה": 9.0,
                        "בזכות": 0.0,
                        "מקור עסקה": "Cafe",
                        "פירוט נוסף": "",
                        "תאור מורחב": None,
                    }
                )
            )
            assert fp is not None
            conn = sqlite3.connect(db_path)
            try:
                rid = self._insert_row(
                    conn,
                    dt="2025-03-01",
                    bh=9.0,
                    bz=0.0,
                    makor="Cafe",
                    pirut="",
                    teur=None,
                    fp=fp,
                )
            finally:
                conn.close()

            self.assertTrue(
                patch_ledger_transaction_by_id(db_path, rid, {"excluded_from_calculations": 1}).get(
                    "ok"
                )
            )
            conn = sqlite3.connect(db_path)
            try:
                ex = conn.execute(
                    "SELECT excluded_from_calculations FROM ledger_transaction WHERE id = ?",
                    (rid,),
                ).fetchone()[0]
                self.assertEqual(int(ex), 1)
            finally:
                conn.close()

            self.assertTrue(
                patch_ledger_transaction_by_id(db_path, rid, {"excluded_from_calculations": False}).get(
                    "ok"
                )
            )
            conn = sqlite3.connect(db_path)
            try:
                ex = conn.execute(
                    "SELECT excluded_from_calculations FROM ledger_transaction WHERE id = ?",
                    (rid,),
                ).fetchone()[0]
                self.assertEqual(int(ex), 0)
            finally:
                conn.close()
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
