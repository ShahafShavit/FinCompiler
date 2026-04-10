"""
Compile output → ``ledger_transaction`` upsert by ``fingerprint`` (MIG-E2).

See ``docs/ledger-merge-ownership.md`` for merge rules.

``ingested_at`` comes from ``ingested_at_rules.compute_ingested_at_iso``. If the compile dataframe
still has a **source** column ``תאריך עדכון`` (legacy bank export), it is **read only** to help pick
the date for ``ingested_at`` — it is **not** written to SQLite (no such ledger column).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import pandas as pd

from pipeline.ingested_at_rules import compute_ingested_at_iso
from pipeline.ledger_migrate import migrate_ledger_db
from pipeline.web_totals_import import (
    _float_col,
    _normalize_date_text,
    _text_or_none,
    fingerprint_from_row,
)

log = logging.getLogger(__name__)

_UPSERT_SQL = """
INSERT INTO ledger_transaction (
    "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב", "4 ספרות",
    "fingerprint", "קטגוריה", notes, statement_month, ingested_at
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT("fingerprint") DO UPDATE SET
    "תאריך" = excluded."תאריך",
    "בחובה" = excluded."בחובה",
    "בזכות" = excluded."בזכות",
    "מקור עסקה" = excluded."מקור עסקה",
    "פירוט נוסף" = excluded."פירוט נוסף",
    "תאור מורחב" = excluded."תאור מורחב",
    "4 ספרות" = excluded."4 ספרות",
    "קטגוריה" = CASE
        WHEN TRIM(COALESCE(ledger_transaction."קטגוריה", '')) != '' THEN ledger_transaction."קטגוריה"
        ELSE excluded."קטגוריה"
    END,
    notes = CASE
        WHEN TRIM(COALESCE(ledger_transaction.notes, '')) != '' THEN ledger_transaction.notes
        ELSE excluded.notes
    END,
    statement_month = COALESCE(ledger_transaction.statement_month, excluded.statement_month),
    ingested_at = ledger_transaction.ingested_at
"""


def _row_tuple(row: pd.Series) -> tuple[Any, ...]:
    d = _normalize_date_text(row.get("תאריך"))
    if not d:
        raise ValueError("missing תאריך")
    taarich_hidon = row.get("תאריך עדכון")
    ing = compute_ingested_at_iso(row.get("תאריך"), taarich_hidon)
    fp = fingerprint_from_row(row)
    if fp is None:
        raise ValueError("missing fingerprint")
    return (
        d,
        _float_col(row.get("בחובה")),
        _float_col(row.get("בזכות")),
        _text_or_none(row.get("מקור עסקה")),
        _text_or_none(row.get("פירוט נוסף")),
        _text_or_none(row.get("תאור מורחב")),
        _text_or_none(row.get("4 ספרות")),
        fp,
        _text_or_none(row.get("קטגוריה")),
        _text_or_none(row.get("notes")),
        _text_or_none(row.get("statement_month")),
        ing,
    )


def upsert_compiled_dataframe_to_ledger(
    df: pd.DataFrame,
    db_path: str,
) -> dict[str, Any]:
    """
    Upsert transaction rows into ``ledger_transaction`` by ``fingerprint``.

    Skips rows without a usable fingerprint. Empty ``df`` is a no-op.
    """
    migrate_ledger_db(db_path)
    if df.empty:
        return {"rows_upserted": 0, "rows_skipped_no_fingerprint": 0, "db_path": db_path}

    tuples: list[tuple[Any, ...]] = []
    skipped = 0
    for _, row in df.iterrows():
        fp = fingerprint_from_row(row)
        if fp is None:
            skipped += 1
            continue
        try:
            tuples.append(_row_tuple(row))
        except ValueError as e:
            log.warning("ledger upsert skip row: %s", e)
            skipped += 1

    if not tuples:
        log.info("ledger upsert: no rows with fingerprint to write (%s skipped)", skipped)
        return {
            "rows_upserted": 0,
            "rows_skipped_no_fingerprint": skipped,
            "db_path": db_path,
        }

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executemany(_UPSERT_SQL, tuples)
        conn.commit()
    finally:
        conn.close()

    log.info(
        "ledger upsert: %s row(s) into %s (%s skipped)",
        len(tuples),
        db_path,
        skipped,
    )
    return {
        "rows_upserted": len(tuples),
        "rows_skipped_no_fingerprint": skipped,
        "db_path": db_path,
    }
