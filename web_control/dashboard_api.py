"""
Aggregations for the React dashboard at ``/`` (served via ``/api/dashboard/*``).

All functions read from the canonical SQLite ledger (``config.ledger_db_file``):
- transactions: ``ledger_transaction`` via ``pipeline.ledger.load_transactions_dataframe_from_ledger``
- holdings: ``holdings_balance`` via direct SQL

Every function returns a JSON-safe dict. When the ledger DB is missing or empty, each
returns ``{"ok": True, ...}`` with empty rows / null KPIs (HTTP 200) so the frontend can
render an empty state without a 500.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

import config
from pipeline.ledger import load_transactions_dataframe_from_ledger

log = logging.getLogger(__name__)


# --- shared helpers ---------------------------------------------------------


def _ledger_path() -> str:
    return config.ledger_db_file


def _ledger_exists() -> bool:
    return os.path.isfile(_ledger_path())


def _empty_payload(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    base: dict[str, Any] = {"ok": True, "rows": [], "ledger_exists": _ledger_exists()}
    if extra:
        base.update(extra)
    return base


def _parse_date_series(s: pd.Series) -> pd.Series:
    """ISO-first parse (matches ledger storage); pandas fallback for any legacy strays."""
    iso = pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    fallback_mask = iso.isna() & s.notna()
    if fallback_mask.any():
        fallback = pd.to_datetime(s[fallback_mask], errors="coerce")
        iso = iso.copy()
        iso.loc[fallback_mask] = fallback
    return iso


def _effective_year_month(df: pd.DataFrame) -> pd.Series:
    """Month bucket: prefer ``statement_month`` (YYYY-MM) when valid; else month-of-``תאריך``."""
    ta = _parse_date_series(df["תאריך"])
    ym = pd.Series(pd.NA, index=df.index, dtype="string")
    has_t = ta.notna()
    ym.loc[has_t] = ta.loc[has_t].dt.strftime("%Y-%m")

    if "statement_month" not in df.columns:
        return ym

    sm_str = df["statement_month"].map(lambda x: "" if pd.isna(x) else str(x).strip())
    valid_sm = sm_str.str.fullmatch(r"\d{4}-\d{2}", na=False)
    out = ym.copy()
    out.loc[valid_sm] = sm_str.loc[valid_sm]
    return out


def _load_tx_df() -> pd.DataFrame | None:
    if not _ledger_exists():
        return None
    try:
        df = load_transactions_dataframe_from_ledger(_ledger_path())
    except Exception:  # noqa: BLE001
        log.exception("dashboard: failed to load ledger transactions")
        return None
    if df.empty:
        return df
    df = df.copy()
    df["בחובה"] = pd.to_numeric(df.get("בחובה"), errors="coerce").fillna(0.0)
    df["בזכות"] = pd.to_numeric(df.get("בזכות"), errors="coerce").fillna(0.0)
    df["תאריך_parsed"] = _parse_date_series(df["תאריך"])
    df["YearMonth"] = _effective_year_month(df)
    return df


def _holdings_conn() -> sqlite3.Connection | None:
    if not _ledger_exists():
        return None
    return sqlite3.connect(_ledger_path())


# --- 1. summary KPIs --------------------------------------------------------


def summary() -> dict[str, Any]:
    """Top-of-dashboard KPIs: net worth, MoM change, last-30d cash flow, savings rate, etc."""
    if not _ledger_exists():
        return {"ok": True, "ledger_exists": False, "kpis": {}}

    out: dict[str, Any] = {"ok": True, "ledger_exists": True}
    kpis: dict[str, Any] = {
        "net_worth_latest": None,
        "net_worth_latest_date": None,
        "net_worth_prev_month": None,
        "net_worth_prev_month_date": None,
        "net_worth_delta_mom": None,
        "income_30d": None,
        "expense_30d": None,
        "net_30d": None,
        "savings_rate_30d": None,
        "uncategorized_count": None,
        "transaction_count": None,
        "last_ingest_date": None,
    }

    conn = _holdings_conn()
    if conn is None:
        out["kpis"] = kpis
        return out
    try:
        # Net worth = sum of latest holdings_balance snapshot.
        latest_row = conn.execute(
            "SELECT MAX(as_of_date) FROM holdings_balance"
        ).fetchone()
        latest_date = latest_row[0] if latest_row else None
        if latest_date:
            kpis["net_worth_latest_date"] = latest_date
            total = conn.execute(
                "SELECT COALESCE(SUM(balance_ils), 0) FROM holdings_balance WHERE as_of_date = ?",
                (latest_date,),
            ).fetchone()[0]
            kpis["net_worth_latest"] = float(total or 0.0)

            prior = conn.execute(
                "SELECT as_of_date FROM holdings_balance WHERE as_of_date < ? GROUP BY as_of_date ORDER BY as_of_date DESC LIMIT 1",
                (latest_date,),
            ).fetchone()
            if prior and prior[0]:
                kpis["net_worth_prev_month_date"] = prior[0]
                prev_total = conn.execute(
                    "SELECT COALESCE(SUM(balance_ils), 0) FROM holdings_balance WHERE as_of_date = ?",
                    (prior[0],),
                ).fetchone()[0]
                kpis["net_worth_prev_month"] = float(prev_total or 0.0)
                kpis["net_worth_delta_mom"] = float((total or 0.0) - (prev_total or 0.0))

        # Transactions-side KPIs.
        tx_row = conn.execute(
            'SELECT COUNT(*), MAX(ingested_at) FROM ledger_transaction'
        ).fetchone()
        kpis["transaction_count"] = int(tx_row[0] or 0) if tx_row else 0
        kpis["last_ingest_date"] = tx_row[1] if tx_row else None

        uncat_row = conn.execute(
            'SELECT COUNT(*) FROM ledger_transaction '
            'WHERE "קטגוריה" IS NULL OR TRIM(COALESCE("קטגוריה", \'\')) = \'\' '
            "OR LOWER(TRIM(COALESCE(\"קטגוריה\", ''))) = 'awaiting'"
        ).fetchone()
        kpis["uncategorized_count"] = int(uncat_row[0] or 0) if uncat_row else 0

        # 30-day window: anchor at MAX(תאריך), include 30 calendar days.
        max_date_row = conn.execute(
            'SELECT MAX(date("תאריך")) FROM ledger_transaction'
        ).fetchone()
        max_date_iso = max_date_row[0] if max_date_row else None
        if max_date_iso:
            try:
                anchor = datetime.strptime(max_date_iso, "%Y-%m-%d").date()
            except ValueError:
                anchor = None
            if anchor:
                start = (anchor - timedelta(days=30)).strftime("%Y-%m-%d")
                row = conn.execute(
                    'SELECT COALESCE(SUM("בזכות"), 0), COALESCE(SUM("בחובה"), 0) '
                    'FROM ledger_transaction '
                    'WHERE "תאריך" IS NOT NULL AND "תאריך" > ? AND "תאריך" <= ?',
                    (start, max_date_iso),
                ).fetchone()
                income = float(row[0] or 0.0)
                expense = float(row[1] or 0.0)
                kpis["income_30d"] = income
                kpis["expense_30d"] = expense
                kpis["net_30d"] = income - expense
                if income > 0:
                    kpis["savings_rate_30d"] = (income - expense) / income
    finally:
        conn.close()

    out["kpis"] = kpis
    return out


# --- 2. holdings: net worth timeline + allocation ---------------------------


def networth_timeline() -> dict[str, Any]:
    """``[{as_of_date, total_ils}]`` summed across activity_type."""
    conn = _holdings_conn()
    if conn is None:
        return _empty_payload()
    try:
        rows = conn.execute(
            """
            SELECT as_of_date, SUM(balance_ils) AS total_ils
            FROM holdings_balance
            GROUP BY as_of_date
            ORDER BY as_of_date ASC
            """
        ).fetchall()
    finally:
        conn.close()
    out_rows = [
        {"as_of_date": str(d), "total_ils": float(t or 0.0)} for d, t in rows
    ]
    return {"ok": True, "ledger_exists": True, "rows": out_rows}


def allocation_latest() -> dict[str, Any]:
    """Latest snapshot breakdown: ``[{activity_type, balance_ils}]``."""
    conn = _holdings_conn()
    if conn is None:
        return _empty_payload({"as_of_date": None})
    try:
        latest = conn.execute(
            "SELECT MAX(as_of_date) FROM holdings_balance"
        ).fetchone()
        latest_date = latest[0] if latest else None
        if not latest_date:
            return {
                "ok": True,
                "ledger_exists": True,
                "as_of_date": None,
                "rows": [],
            }
        rows = conn.execute(
            """
            SELECT activity_type, balance_ils
            FROM holdings_balance
            WHERE as_of_date = ?
            ORDER BY balance_ils DESC
            """,
            (latest_date,),
        ).fetchall()
    finally:
        conn.close()
    return {
        "ok": True,
        "ledger_exists": True,
        "as_of_date": latest_date,
        "rows": [
            {"activity_type": str(a), "balance_ils": float(b or 0.0)} for a, b in rows
        ],
    }


def allocation_timeline() -> dict[str, Any]:
    """Melted rows: ``[{as_of_date, activity_type, balance_ils}]`` (Recharts pivots client-side)."""
    conn = _holdings_conn()
    if conn is None:
        return _empty_payload({"activity_types": []})
    try:
        rows = conn.execute(
            """
            SELECT as_of_date, activity_type, balance_ils
            FROM holdings_balance
            ORDER BY as_of_date ASC, activity_type ASC
            """
        ).fetchall()
    finally:
        conn.close()
    out_rows = [
        {
            "as_of_date": str(d),
            "activity_type": str(a),
            "balance_ils": float(b or 0.0),
        }
        for d, a, b in rows
    ]
    activity_types = sorted({r["activity_type"] for r in out_rows})
    return {
        "ok": True,
        "ledger_exists": True,
        "rows": out_rows,
        "activity_types": activity_types,
    }


# --- 3. transactions: cash flow / categories / sources ----------------------


def _filter_recent_months(df: pd.DataFrame, months: int) -> pd.DataFrame:
    if df.empty or months <= 0:
        return df
    valid = df.dropna(subset=["YearMonth"]).copy()
    if valid.empty:
        return valid
    months_sorted = sorted(valid["YearMonth"].unique(), reverse=True)
    keep = set(months_sorted[:months])
    return valid[valid["YearMonth"].isin(keep)]


def cashflow_monthly(months: int = 24) -> dict[str, Any]:
    """``[{month, income, expense, net}]`` for last N months (effective month)."""
    df = _load_tx_df()
    if df is None or df.empty:
        return _empty_payload({"months": months})
    sub = _filter_recent_months(df, months).copy()
    if sub.empty:
        return _empty_payload({"months": months})
    grouped = sub.groupby("YearMonth", sort=True).agg(
        income=("בזכות", "sum"),
        expense=("בחובה", "sum"),
    )
    grouped["net"] = grouped["income"] - grouped["expense"]
    out_rows = [
        {
            "month": str(idx),
            "income": float(row["income"]),
            "expense": float(row["expense"]),
            "net": float(row["net"]),
        }
        for idx, row in grouped.iterrows()
    ]
    return {"ok": True, "ledger_exists": True, "rows": out_rows, "months": months}


_PERIOD_TO_MONTHS = {"30d": 1, "ytd": -1, "12m": 12, "3m": 3, "6m": 6}


def _period_filter(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """Filter df by period token:
    - ``30d``: last 30 days of ``תאריך_parsed``
    - ``ytd``: current calendar year (anchored at MAX(תאריך))
    - ``12m`` / ``6m`` / ``3m``: last N months by YearMonth
    Default → 12 months.
    """
    if df.empty:
        return df
    p = (period or "12m").lower().strip()
    if p == "30d":
        max_d = df["תאריך_parsed"].max()
        if pd.isna(max_d):
            return df.iloc[0:0]
        cutoff = (max_d - pd.Timedelta(days=30))
        return df[df["תאריך_parsed"] > cutoff]
    if p == "ytd":
        max_d = df["תאריך_parsed"].max()
        if pd.isna(max_d):
            return df.iloc[0:0]
        year = max_d.year
        ym_prefix = f"{year}-"
        return df[df["YearMonth"].fillna("").str.startswith(ym_prefix)]
    n = _PERIOD_TO_MONTHS.get(p, 12)
    if n is None or n <= 0:
        n = 12
    return _filter_recent_months(df, n)


def top_categories(
    period: str = "12m", report_type: str = "expense", limit: int = 10
) -> dict[str, Any]:
    """``[{category, amount}]`` ranked desc."""
    df = _load_tx_df()
    if df is None or df.empty:
        return _empty_payload({"period": period, "type": report_type, "limit": int(limit)})
    sub = _period_filter(df, period)
    if sub.empty:
        return _empty_payload({"period": period, "type": report_type, "limit": int(limit)})
    if report_type == "income":
        col = "בזכות"
    else:
        col = "בחובה"
        report_type = "expense"
    work = sub.copy()
    work["__cat__"] = work["קטגוריה"].fillna("").astype(str).str.strip()
    work.loc[work["__cat__"] == "", "__cat__"] = "(uncategorized)"
    work = work[work[col] > 0]
    if work.empty:
        return _empty_payload({"period": period, "type": report_type, "limit": int(limit)})
    grouped = (
        work.groupby("__cat__")[col]
        .sum()
        .sort_values(ascending=False)
        .head(int(limit))
    )
    out_rows = [{"category": str(k), "amount": float(v)} for k, v in grouped.items()]
    return {
        "ok": True,
        "ledger_exists": True,
        "period": period,
        "type": report_type,
        "limit": int(limit),
        "rows": out_rows,
    }


def sources(months: int = 12) -> dict[str, Any]:
    """``[{source, count, expense, income}]`` over the last N months."""
    df = _load_tx_df()
    if df is None or df.empty:
        return _empty_payload({"months": int(months)})
    sub = _filter_recent_months(df, int(months))
    if sub.empty:
        return _empty_payload({"months": int(months)})
    work = sub.copy()
    work["__src__"] = work["מקור עסקה"].fillna("").astype(str).str.strip()
    work.loc[work["__src__"] == "", "__src__"] = "(unknown)"
    grouped = work.groupby("__src__").agg(
        count=("__src__", "size"),
        expense=("בחובה", "sum"),
        income=("בזכות", "sum"),
    )
    grouped = grouped.sort_values("expense", ascending=False)
    out_rows = [
        {
            "source": str(idx),
            "count": int(row["count"]),
            "expense": float(row["expense"]),
            "income": float(row["income"]),
        }
        for idx, row in grouped.iterrows()
    ]
    return {
        "ok": True,
        "ledger_exists": True,
        "months": int(months),
        "rows": out_rows,
    }


# --- query string helpers (used by server.py) -------------------------------


def _qs_first(qs: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
    val = qs.get(key)
    if not val:
        return default
    first = val[0]
    if first is None:
        return default
    s = str(first).strip()
    return s if s else default


def _qs_int(qs: dict[str, list[str]], key: str, default: int) -> int:
    raw = _qs_first(qs, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def handle_dashboard_request(name: str, qs: dict[str, list[str]]) -> dict[str, Any]:
    """Dispatch by name. Unknown name → ``ok: False``."""
    if name == "summary":
        return summary()
    if name == "networth-timeline":
        return networth_timeline()
    if name == "allocation":
        return allocation_latest()
    if name == "allocation-timeline":
        return allocation_timeline()
    if name == "cashflow-monthly":
        months = _qs_int(qs, "months", 24)
        return cashflow_monthly(months=months)
    if name == "top-categories":
        period = _qs_first(qs, "period", "12m") or "12m"
        report_type = _qs_first(qs, "type", "expense") or "expense"
        limit = _qs_int(qs, "limit", 10)
        return top_categories(period=period, report_type=report_type, limit=limit)
    if name == "sources":
        months = _qs_int(qs, "months", 12)
        return sources(months=months)
    return {"ok": False, "error": "unknown_endpoint", "message": f"unknown dashboard endpoint: {name}"}


__all__ = [
    "summary",
    "networth_timeline",
    "allocation_latest",
    "allocation_timeline",
    "cashflow_monthly",
    "top_categories",
    "sources",
    "handle_dashboard_request",
]
