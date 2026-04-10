"""
Guardrails for :func:`pipeline.csv_handler.generate_transaction_fingerprint`.

The fingerprint must always encode the **same calendar date** as
:func:`pipeline.compiler.parse_post_ingest_date_scalar` for ``תאריך``. Using
``pd.to_datetime(..., dayfirst=True, format="mixed")`` on ISO ``YYYY-MM-DD`` strings
(or on ``str(Timestamp)``) has historically swapped month/day; those mistakes must not return.
"""

from __future__ import annotations

import datetime
import unittest

import pandas as pd

from pipeline.compiler import parse_post_ingest_date_scalar
from pipeline.csv_handler import generate_transaction_fingerprint, generate_transaction_fingerprint_legacy


def _tx_row(
    taarich: object,
    *,
    bh: float = 1.0,
    bz: float = 0.0,
    makor: str = "Test Merchant",
    pirur: str = "",
    teur: str = "",
) -> pd.Series:
    return pd.Series(
        {
            "תאריך": taarich,
            "בחובה": bh,
            "בזכות": bz,
            "מקור עסקה": makor,
            "פירוט נוסף": pirur,
            "תאור מורחב": teur,
        }
    )


def _date_prefix(fp: str) -> str:
    return fp.split(":", 1)[0]


class FingerprintDateContractTests(unittest.TestCase):
    """Fingerprint date segment must match ``parse_post_ingest_date_scalar`` (single source of truth)."""

    def assert_date_prefix_matches_parser(self, row: pd.Series, msg: str = "") -> None:
        ts = parse_post_ingest_date_scalar(row["תאריך"])
        self.assertFalse(pd.isna(ts), msg=f"{msg} parser NaT for תאריך={row['תאריך']!r}")
        assert not pd.isna(ts)
        want = ts.strftime("%Y-%m-%d")
        fp_v2 = generate_transaction_fingerprint(row)
        fp_leg = generate_transaction_fingerprint_legacy(row)
        self.assertIsNotNone(fp_v2, msg=msg)
        self.assertIsNotNone(fp_leg, msg=msg)
        assert fp_v2 is not None and fp_leg is not None
        self.assertEqual(_date_prefix(fp_v2), want, msg=f"{msg} v2={fp_v2!r}")
        self.assertEqual(_date_prefix(fp_leg), want, msg=f"{msg} legacy={fp_leg!r}")

    def test_contract_iso_strings_bank_examples(self) -> None:
        for d in (
            "2024-03-31",
            "2024-04-01",
            "2024-12-15",
            "2026-01-12",
        ):
            with self.subTest(iso=d):
                self.assert_date_prefix_matches_parser(_tx_row(d))

    def test_contract_iso_with_separators(self) -> None:
        """Regional separators are normalized before parsing; date must stay correct."""
        for raw, want_day in (
            ("2024/04/01", "2024-04-01"),
            ("2024.04.01", "2024-04-01"),
        ):
            with self.subTest(raw=raw):
                row = _tx_row(raw)
                self.assert_date_prefix_matches_parser(row)
                ts = parse_post_ingest_date_scalar(raw)
                self.assertFalse(pd.isna(ts))
                assert not pd.isna(ts)
                self.assertEqual(ts.strftime("%Y-%m-%d"), want_day)

    def test_contract_pd_timestamp_and_datetime(self) -> None:
        for val in (
            pd.Timestamp("2026-01-12"),
            datetime.datetime(2026, 1, 12, 15, 30, 0),
            datetime.date(2024, 4, 1),
        ):
            with self.subTest(val=repr(val)):
                self.assert_date_prefix_matches_parser(_tx_row(val))

    def test_contract_datetime_string_with_time_suffix(self) -> None:
        """Excel / ``str(Timestamp)`` often yields a time portion; date prefix must still be correct."""
        for s in (
            "2026-01-12 00:00:00",
            "2024-04-01 23:59:59",
        ):
            with self.subTest(s=s):
                self.assert_date_prefix_matches_parser(_tx_row(s))

    def test_contract_regional_dd_mm_yyyy_via_mixed_path(self) -> None:
        """Non-ISO strings still use dayfirst + mixed; fingerprint must match that parse."""
        raw = "01/12/2026"
        row = _tx_row(raw)
        self.assert_date_prefix_matches_parser(row)
        self.assertEqual(parse_post_ingest_date_scalar(raw).strftime("%Y-%m-%d"), "2026-12-01")

    def test_regression_april_first_never_emits_jan_fourth_prefix(self) -> None:
        """Historical bug: ISO ``2024-04-01`` was misread as January 4 in the fingerprint."""
        row = _tx_row(
            "2024-04-01",
            bh=235.47,
            makor="פז אפליקציית יילו",
        )
        fp = generate_transaction_fingerprint(row)
        self.assertIsNotNone(fp)
        assert fp is not None
        self.assertTrue(fp.startswith("2024-04-01:"), fp)
        self.assertFalse(fp.startswith("2024-01-04:"), fp)

    def test_regression_jan_twelve_timestamp_never_emits_dec_first(self) -> None:
        """Historical bug: ``pd.Timestamp`` string form hit dayfirst/mixed and swapped month/day."""
        row = _tx_row(pd.Timestamp("2026-01-12"), bh=0.0, bz=339.65, makor="מב.ירושלים ס-י")
        fp = generate_transaction_fingerprint(row)
        self.assertIsNotNone(fp)
        assert fp is not None
        self.assertTrue(fp.startswith("2026-01-12:"), fp)
        self.assertFalse(fp.startswith("2026-12-01:"), fp)


class FingerprintOptionalTextTests(unittest.TestCase):
    """Optional columns must not diverge by None vs NaN (v11 contract)."""

    def test_none_na_na_same_fingerprint(self) -> None:
        base = _tx_row(
            "2024-09-05",
            bh=311.0,
            makor="KSP אקספרס-גמא",
            pirur="תשלום 1  מתוך 2",
        )
        a = base.copy()
        a["תאור מורחב"] = None
        b = base.copy()
        b["תאור מורחב"] = float("nan")
        c = base.copy()
        c["תאור מורחב"] = pd.NA
        fa = generate_transaction_fingerprint(a)
        fb = generate_transaction_fingerprint(b)
        fc = generate_transaction_fingerprint(c)
        self.assertEqual(fa, fb)
        self.assertEqual(fa, fc)
        self.assertNotIn("none", fa or "")
        self.assertNotIn("nan", fa or "")


if __name__ == "__main__":
    unittest.main()
