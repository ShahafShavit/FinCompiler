"""
Import all-time ledger rows from ``web_totals.csv`` into ``ledger_transaction`` (MIG-D1, MIG-D3).

**מזהה עסקה** and **תאריך עדכון** may appear as CSV headers for compatibility with bank exports.
They are **not** SQLite columns: ignore the former for identity; use the latter only inside
``compute_ingested_at_iso`` (via ``ingested_at`` on insert). Only **fingerprint** is written as the
dedupe key; if missing, ``fingerprint`` is **NULL** (awaiting pipeline backfill).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import pandas as pd

import config
from pipeline.ingested_at_rules import compute_ingested_at_iso
from pipeline.ledger_migrate import migrate_ledger_db

log = logging.getLogger(__name__)

# Tolerant column set for real-world CSVs (includes legacy headers; not all are persisted — see module doc).
_EXPECTED_COLS = [
    "תאריך",
    "מקור עסקה",
    "בחובה",
    "מזהה עסקה",
    "בזכות",
    "פירוט נוסף",
    "4 ספרות",
    "תאור מורחב",
    "קטגוריה",
    "תאריך עדכון",
    "fingerprint",
]


def fingerprint_from_row(row: pd.Series) -> str | None:
    """Return stripped pipeline fingerprint, or None if absent (never use מזהה עסקה)."""
    fp = row.get("fingerprint")
    if pd.isna(fp):
        return None
    s = str(fp).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    return s


def load_web_totals_dataframe(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    missing = [c for c in _EXPECTED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing columns: {missing}; expected {_EXPECTED_COLS}")
    keys: list[str | None] = []
    for _, row in df.iterrows():
        keys.append(fingerprint_from_row(row))
    non_null = [k for k in keys if k is not None]
    dup = pd.Series(non_null)
    if dup.duplicated().any():
        bad = dup[dup.duplicated(keep=False)].unique()
        raise ValueError(f"duplicate non-null fingerprint values (first few): {bad[:5]!r}")
    df = df.copy()
    df["_ledger_fingerprint"] = keys
    return df


def _normalize_date_text(val: Any) -> str | None:
    if pd.isna(val) or (isinstance(val, str) and not val.strip()):
        return None
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    if not s:
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        ts = pd.to_datetime(s[:10], errors="coerce", format="%Y-%m-%d")
    else:
        ts = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if pd.isna(ts):
        raise ValueError(f"invalid date: {val!r}")
    return ts.strftime("%Y-%m-%d")


def _float_col(val: Any) -> float:
    if pd.isna(val):
        return 0.0
    try:
        x = float(val)
    except (TypeError, ValueError):
        return 0.0
    if x != x:  # NaN
        return 0.0
    return x


def _text_or_none(val: Any) -> str | None:
    if pd.isna(val):
        return None
    s = str(val).strip()
    return s if s else None


def _row_tuple(row: pd.Series) -> tuple:
    d = _normalize_date_text(row["תאריך"])
    if not d:
        raise ValueError("missing תאריך")
    te_raw = row["תאריך עדכון"]
    ing = compute_ingested_at_iso(d, te_raw)
    fp = row["_ledger_fingerprint"]
    return (
        d,
        _float_col(row["בחובה"]),
        _float_col(row["בזכות"]),
        _text_or_none(row["מקור עסקה"]),
        _text_or_none(row["פירוט נוסף"]),
        _text_or_none(row["תאור מורחב"]),
        _text_or_none(row["4 ספרות"]),
        fp,
        _text_or_none(row["קטגוריה"]),
        None,
        None,
        ing,
    )


def import_web_totals_to_ledger(
    csv_path: str | None = None,
    db_path: str | None = None,
    *,
    replace: bool = True,
) -> dict[str, Any]:
    """
    Load ``web_totals.csv`` into ``ledger_transaction``.

    Runs ``migrate_ledger_db`` first. If ``replace`` is True, deletes existing ledger rows
    before insert (full reload from CSV).
    """
    path = csv_path if csv_path is not None else config.web_totals_file
    db = db_path if db_path is not None else config.ledger_db_file
    migrate_ledger_db(db)

    df = load_web_totals_dataframe(path)
    rows = [_row_tuple(r) for _, r in df.iterrows()]

    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if replace:
            conn.execute("DELETE FROM ledger_transaction")
            conn.commit()
        sql = """
        INSERT INTO ledger_transaction (
            "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב", "4 ספרות",
            "fingerprint", "קטגוריה", notes, statement_month, ingested_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """
        conn.executemany(sql, rows)
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM ledger_transaction").fetchone()[0]
    finally:
        conn.close()

    report = verify_ledger_against_csv(df, db)
    report["rows_imported"] = int(n)
    report["rows_without_fingerprint"] = int(df["_ledger_fingerprint"].isna().sum())
    report["csv_path"] = path
    report["db_path"] = db
    log.info(
        "web_totals import: %s rows into %s (parity ok=%s)",
        n,
        db,
        report.get("parity_ok"),
    )
    return report


def _fp_equal(a: Any, b: Any) -> bool:
    a_null = a is None or (isinstance(a, float) and pd.isna(a)) or pd.isna(a)
    b_null = b is None or (isinstance(b, float) and pd.isna(b)) or pd.isna(b)
    if a_null and b_null:
        return True
    if a_null != b_null:
        return False
    return str(a).strip() == str(b).strip()


def verify_ledger_against_csv(df: pd.DataFrame, db_path: str) -> dict[str, Any]:
    """Row-order parity: import order matches ``ORDER BY id`` (same row count and fields)."""
    conn = sqlite3.connect(db_path)
    try:
        q = """
        SELECT id, "fingerprint", "בחובה", "בזכות", "תאריך", ingested_at
        FROM ledger_transaction
        ORDER BY id
        """
        ldb = pd.read_sql_query(q, conn)
    finally:
        conn.close()

    if len(ldb) != len(df):
        return {
            "parity_ok": False,
            "error": f"row count mismatch: csv={len(df)} db={len(ldb)}",
        }

    csv_debit = pd.to_numeric(df["בחובה"], errors="coerce").fillna(0).sum()
    csv_credit = pd.to_numeric(df["בזכות"], errors="coerce").fillna(0).sum()
    db_debit = pd.to_numeric(ldb["בחובה"], errors="coerce").fillna(0).sum()
    db_credit = pd.to_numeric(ldb["בזכות"], errors="coerce").fillna(0).sum()

    tol = 0.05
    sum_ok = abs(csv_debit - db_debit) < tol and abs(csv_credit - db_credit) < tol

    mismatches: list[str] = []
    for i in range(len(df)):
        cr = df.iloc[i]
        dr = ldb.iloc[i]
        if _normalize_date_text(cr["תאריך"]) != str(dr["תאריך"]):
            mismatches.append(f"row {i} date mismatch")
            continue
        if not _fp_equal(cr["_ledger_fingerprint"], dr["fingerprint"]):
            mismatches.append(f"row {i} fingerprint mismatch csv={cr['_ledger_fingerprint']!r} db={dr['fingerprint']!r}")
        cd = _float_col(cr["בחובה"])
        cc = _float_col(cr["בזכות"])
        if abs(float(dr["בחובה"]) - cd) > tol or abs(float(dr["בזכות"]) - cc) > tol:
            mismatches.append(f"row {i} amounts differ")

    out: dict[str, Any] = {
        "parity_ok": sum_ok and len(mismatches) == 0,
        "csv_rows": len(df),
        "db_rows": len(ldb),
        "sum_debit_csv": float(csv_debit),
        "sum_debit_db": float(db_debit),
        "sum_credit_csv": float(csv_credit),
        "sum_credit_db": float(db_credit),
        "order_mismatches": mismatches[:30],
    }
    if mismatches:
        out["parity_ok"] = False
    return out
