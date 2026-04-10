"""
Import wide ``holdings.csv`` exports into ``holdings_balance`` (relational melt).

Year-specific files may use different **column orders**; all non-date columns become
``activity_type`` keys. Normalizes known spelling variants for the deposits column.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import pandas as pd

import config
from pipeline.ledger import migrate_ledger_db

log = logging.getLogger(__name__)

# 2024 export used "פיקדונות…"; 2025 uses "פקדונות…" — one canonical name in SQLite.
_DEPOSITS_ALIASES = frozenset(
    {
        "פיקדונות וחסכונות",
        "פקדונות וחסכונות",
    }
)
_DEPOSITS_CANONICAL = "פקדונות וחסכונות"


def _canonical_activity(name: str) -> str:
    s = str(name).strip()
    if s in _DEPOSITS_ALIASES:
        return _DEPOSITS_CANONICAL
    return s


def wide_holdings_to_long(df: pd.DataFrame) -> pd.DataFrame:
    if "תאריך" not in df.columns:
        raise ValueError('holdings CSV must include a "תאריך" column')
    id_vars = ["תאריך"]
    others = [c for c in df.columns if c != "תאריך"]
    if not others:
        raise ValueError("holdings CSV has no balance columns besides תאריך")
    long_df = df.melt(
        id_vars=id_vars,
        value_vars=others,
        var_name="activity_type",
        value_name="balance_ils",
    )
    long_df["activity_type"] = long_df["activity_type"].map(_canonical_activity)
    # Aggregate duplicate keys after canonicalization (same row could theoretically repeat)
    long_df = (
        long_df.groupby(["תאריך", "activity_type"], as_index=False)["balance_ils"]
        .sum()
        .rename(columns={"תאריך": "as_of_date"})
    )

    def _norm_date(v: Any) -> str:
        if hasattr(v, "strftime"):
            return v.strftime("%Y-%m-%d")
        s = str(v).strip()
        ts = pd.to_datetime(s, errors="coerce", format="%Y-%m-%d")
        if pd.isna(ts):
            ts = pd.to_datetime(s, errors="coerce", dayfirst=True)
        if pd.isna(ts):
            raise ValueError(f"invalid as_of_date: {v!r}")
        return ts.strftime("%Y-%m-%d")

    long_df["as_of_date"] = long_df["as_of_date"].map(_norm_date)
    long_df["balance_ils"] = pd.to_numeric(long_df["balance_ils"], errors="coerce").fillna(0.0)
    return long_df


def load_holdings_wide_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [str(c).strip() for c in df.columns]
    return wide_holdings_to_long(df)


def import_holdings_csvs(
    paths: list[str],
    db_path: str | None = None,
    *,
    clear_holdings_first: bool = False,
) -> dict[str, Any]:
    """
    Upsert melted rows into ``holdings_balance`` (``INSERT OR REPLACE``).

    If ``clear_holdings_first`` is True, deletes all rows from ``holdings_balance`` first.
    """
    db = db_path if db_path is not None else config.ledger_db_file
    migrate_ledger_db(db)

    frames = [load_holdings_wide_csv(p) for p in paths]
    combined = pd.concat(frames, ignore_index=True)
    # Last file wins on duplicate (as_of_date, activity_type)
    combined = combined.drop_duplicates(subset=["as_of_date", "activity_type"], keep="last")

    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if clear_holdings_first:
            conn.execute("DELETE FROM holdings_balance")
            conn.commit()
        sql = """
        INSERT OR REPLACE INTO holdings_balance (as_of_date, activity_type, balance_ils)
        VALUES (?,?,?)
        """
        rows = [
            (r["as_of_date"], r["activity_type"], float(r["balance_ils"]))
            for _, r in combined.iterrows()
        ]
        conn.executemany(sql, rows)
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM holdings_balance").fetchone()[0]
    finally:
        conn.close()

    report = verify_holdings_long(combined, db)
    report["rows_upserted"] = len(rows)
    report["holdings_table_count"] = int(n)
    report["source_files"] = list(paths)
    log.info(
        "holdings import: %s logical rows from %s file(s); table rows=%s parity=%s",
        len(rows),
        len(paths),
        n,
        report.get("parity_ok"),
    )
    return report


def verify_holdings_long(expected: pd.DataFrame, db_path: str) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        got = pd.read_sql_query(
            "SELECT as_of_date, activity_type, balance_ils FROM holdings_balance",
            conn,
        )
    finally:
        conn.close()

    exp = expected.copy()
    exp["balance_ils"] = pd.to_numeric(exp["balance_ils"], errors="coerce").fillna(0.0)
    got["balance_ils"] = pd.to_numeric(got["balance_ils"], errors="coerce").fillna(0.0)

    exp_keys = set(zip(exp["as_of_date"], exp["activity_type"]))
    got_keys = set(zip(got["as_of_date"], got["activity_type"]))
    missing = exp_keys - got_keys

    tol = 0.01
    amount_mismatch = []
    for _, r in exp.iterrows():
        k = (r["as_of_date"], r["activity_type"])
        if k in missing:
            continue
        sub = got[(got["as_of_date"] == r["as_of_date"]) & (got["activity_type"] == r["activity_type"])]
        if sub.empty:
            continue
        gv = float(sub.iloc[0]["balance_ils"])
        if abs(gv - float(r["balance_ils"])) > tol:
            amount_mismatch.append(f"{k} csv={r['balance_ils']} db={gv}")

    parity_ok = not missing and not amount_mismatch
    return {
        "parity_ok": parity_ok,
        "expected_logical_rows": len(exp),
        "missing_keys": list(missing)[:10],
        "amount_mismatches": amount_mismatch[:10],
    }
