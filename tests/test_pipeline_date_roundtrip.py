"""
Regression tests for ledger date parsing (ISO vs regional) after CSV round-trip.

See :func:`compile_handler.parse_post_ingest_date_scalar`: ISO ``YYYY-MM-DD`` uses a fixed
format; other shapes use ``dayfirst=True`` + ``format=\"mixed\"``.

Historical note: pandas 3.0.x + ``dayfirst=True`` + ``format=\"mixed\"`` on ISO strings could
swap month/day (see pandas issue #58859). The ISO branch avoids that for normal compiled output.
"""

from __future__ import annotations

import io
import re
import unittest

import pandas as pd

from compile_handler import parse_post_ingest_date_scalar, parse_post_ingest_date_column
from csv_handler import generate_transaction_fingerprint


def _compile_stage_parse(date_str: object) -> pd.Timestamp:
    """Same rules as ``Compiler.__compile_new__`` after separator standardization."""
    s = re.sub(r"[-/.]", "-", str(date_str))
    return parse_post_ingest_date_scalar(s)


class PipelineDateRoundtripTests(unittest.TestCase):
    def test_excel_timestamp_fingerprint_is_jan_12_2026(self) -> None:
        ts = pd.Timestamp("2026-01-12")
        row = {
            "תאריך": ts,
            "בחובה": 0.0,
            "בזכות": 339.65,
            "מקור עסקה": "מב.ירושלים ס-י",
            "פירוט נוסף": "",
            "תאור מורחב": "העברה מאת: VISA …",
        }
        fp = generate_transaction_fingerprint(row)
        self.assertIsNotNone(fp)
        assert fp is not None
        self.assertTrue(fp.startswith("2026-01-12:"), fp)

    def test_iso_yyyy_mm_dd_round_trips_to_jan_12(self) -> None:
        self.assertEqual(
            parse_post_ingest_date_scalar("2026-01-12").strftime("%Y-%m-%d"),
            "2026-01-12",
        )

    def test_ambiguous_01_slash_12_slash_2026_still_dec1_with_dayfirst_fallback(self) -> None:
        """Non-ISO regional strings still use dayfirst=True (EU): 01/12/2026 → 1 Dec 2026."""
        s = "01/12/2026"
        parsed = _compile_stage_parse(s)
        self.assertEqual(parsed.strftime("%Y-%m-%d"), "2026-12-01")

    def test_to_csv_read_csv_iso_string_stays_jan_12(self) -> None:
        ts = pd.Timestamp("2026-01-12")
        df = pd.DataFrame({"תאריך": [ts]})
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        buf.seek(0)
        back = pd.read_csv(buf)
        cell = back["תאריך"].iloc[0]
        parsed = parse_post_ingest_date_scalar(cell)
        self.assertEqual(parsed.strftime("%Y-%m-%d"), "2026-01-12")

    def test_parse_post_ingest_date_column_vectorized(self) -> None:
        s = pd.Series(["2026-01-12", "01/12/2026", None])
        out = parse_post_ingest_date_column(s)
        self.assertEqual(out.iloc[0].strftime("%Y-%m-%d"), "2026-01-12")
        self.assertEqual(out.iloc[1].strftime("%Y-%m-%d"), "2026-12-01")


if __name__ == "__main__":
    unittest.main()
