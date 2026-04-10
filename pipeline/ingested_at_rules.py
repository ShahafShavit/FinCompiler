"""
Rules for computing ``ingested_at`` on ``ledger_transaction`` (schema v8+).

``ingested_at`` is the **only** persisted ingestion/statement timing column. There is **no**
``תאריך עדכון`` column in SQLite — that name may still appear on **source** CSV/Sheets rows; when
present and non-empty it is parsed here and mapped to the calendar date stored as ``ingested_at``.

- If that source field (passed as ``taarich_hidon`` / row["תאריך עדכון"]) is non-empty, ``ingested_at``
  is that calendar date.
- Otherwise: **15th** of the transaction month if ``day(תאריך) <= 15``, else **15th of the
  following month** (e.g. 2025-10-10 → 2025-10-15; 2025-10-19 → 2025-11-15).

``statement_month`` is left to other pipelines.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd


def _parse_iso_date(val: Any) -> date | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, date):
        return val
    if hasattr(val, "date") and callable(val.date):
        d = val.date()
        if isinstance(d, date):
            return d
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        ts = pd.to_datetime(s[:10], errors="coerce", format="%Y-%m-%d")
    else:
        ts = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if pd.isna(ts):
        return None
    return ts.date()


def compute_ingested_at_iso(
    transaction_date: Any,
    taarich_hidon: Any,
) -> str:
    """
    Return ``YYYY-MM-DD`` for ``ingested_at`` column (SQLite TEXT + datetime() checks).
    """
    upd = _parse_iso_date(taarich_hidon)
    if upd is not None:
        return upd.isoformat()

    tx = _parse_iso_date(transaction_date)
    if tx is None:
        raise ValueError("missing תאריך for ingested_at fallback")

    if tx.day <= 15:
        ing = date(tx.year, tx.month, 15)
    else:
        if tx.month == 12:
            ing = date(tx.year + 1, 1, 15)
        else:
            ing = date(tx.year, tx.month + 1, 15)
    return ing.isoformat()
