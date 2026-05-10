"""
Aggregations for the React dashboard at ``/`` (served via ``/api/dashboard/*``).

All functions read from the canonical SQLite ledger (``config.ledger_db_file``):
- transactions: ``ledger_transaction`` via SQLite aggregates (``dashboard_tx_sql``)
- holdings: ``holdings_balance`` via direct SQL

Every function returns a JSON-safe dict. When the ledger DB is missing or empty, each
returns ``{"ok": True, ...}`` with empty rows / null KPIs (HTTP 200) so the frontend can
render an empty state without a 500.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any

import config
from pipeline.ledger import (
    LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR,
    LEDGER_SQL_TX_INCLUDED,
    ledger_connect_readonly,
    migrate_ledger_db,
)

from . import dashboard_tx_sql

log = logging.getLogger(__name__)

_DASH_CACHE_MAX = 64
_dash_response_cache: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()


# --- shared helpers ---------------------------------------------------------


def _ledger_path() -> str:
    return config.ledger_db_file


def _ledger_exists() -> bool:
    return os.path.isfile(_ledger_path())


def _ledger_mtime_ns_for_cache() -> int:
    """Monotonic cache revision for the ledger file; ``-1`` when the file is absent."""
    path = _ledger_path()
    if not os.path.isfile(path):
        return -1
    st = os.stat(path)
    return int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))


def ledger_meta() -> dict[str, Any]:
    """Cheap revision for dashboards: ``stat`` only, no DB open or migrate."""
    path = _ledger_path()
    if not os.path.isfile(path):
        return {"ok": True, "exists": False, "mtime_ns": None}
    st = os.stat(path)
    mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
    return {"ok": True, "exists": True, "mtime_ns": mtime_ns}


def _empty_payload(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    base: dict[str, Any] = {"ok": True, "rows": [], "ledger_exists": _ledger_exists()}
    if extra:
        base.update(extra)
    return base


_PRESET_CATEGORY_WINDOW_LABELS: dict[str, str] = {
    "30d": "Last 30 days",
    "ytd": "Year to date",
    "3m": "Last 3 months",
    "6m": "Last 6 months",
    "12m": "Last 12 months",
}


def _category_window_meta(
    period: str, start_ym: str | None, end_ym: str | None
) -> dict[str, Any]:
    """Echo ``start_ym`` / ``end_ym`` and a short label for custom, all, calendar-year, and presets."""
    bounds = dashboard_tx_sql.normalize_ym_range(start_ym, end_ym)
    if bounds is not None:
        lo, hi = bounds
        return {"start_ym": lo, "end_ym": hi, "window_label": f"{lo} – {hi}"}
    raw = (period or "12m").strip()
    low = raw.lower()
    if low == "all":
        return {"window_label": "All time"}
    if len(raw) == 4 and raw.isdigit():
        return {"window_label": raw}
    lbl = _PRESET_CATEGORY_WINDOW_LABELS.get(low)
    if lbl:
        return {"window_label": lbl}
    return {}


def _tx_conn() -> sqlite3.Connection | None:
    if not _ledger_exists():
        return None
    try:
        return ledger_connect_readonly(_ledger_path())
    except Exception:  # noqa: BLE001
        log.exception("dashboard: failed to open ledger read-only")
        return None


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
        "transactions_total_stored": None,
        "transactions_excluded_count": None,
        "last_ingest_date": None,
    }

    conn = _holdings_conn()
    if conn is None:
        out["kpis"] = kpis
        return out
    try:
        migrate_ledger_db(_ledger_path())
        conn.execute("PRAGMA foreign_keys = ON")
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
        total_stored_row = conn.execute("SELECT COUNT(*) FROM ledger_transaction").fetchone()
        total_stored = int(total_stored_row[0] or 0) if total_stored_row else 0
        kpis["transactions_total_stored"] = total_stored

        tx_row = conn.execute(
            f"SELECT COUNT(*), MAX(ingested_at) FROM ledger_transaction WHERE {LEDGER_SQL_TX_INCLUDED}"
        ).fetchone()
        kpis["transaction_count"] = int(tx_row[0] or 0) if tx_row else 0
        kpis["transactions_excluded_count"] = max(0, total_stored - kpis["transaction_count"])
        kpis["last_ingest_date"] = tx_row[1] if tx_row else None

        uncat_row = conn.execute(
            f"""
            SELECT COUNT(*) FROM ledger_transaction
            WHERE {LEDGER_SQL_TX_INCLUDED}
              AND (
                   "קטגוריה" IS NULL OR TRIM(COALESCE("קטגוריה", '')) = ''
                OR LOWER(TRIM(COALESCE("קטגוריה", ''))) = 'awaiting'
              )
            """
        ).fetchone()
        kpis["uncategorized_count"] = int(uncat_row[0] or 0) if uncat_row else 0

        # 30-day window: anchor at MAX(effective tx date per statement_month / תאריך), 30 calendar days.
        sql_max_eff = (
            f"SELECT MAX(({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR})) FROM ledger_transaction "
            f"WHERE {LEDGER_SQL_TX_INCLUDED}"
        )
        max_date_row = conn.execute(sql_max_eff).fetchone()
        max_date_iso = max_date_row[0] if max_date_row else None
        if max_date_iso:
            try:
                anchor = datetime.strptime(max_date_iso, "%Y-%m-%d").date()
            except ValueError:
                anchor = None
            if anchor:
                start = (anchor - timedelta(days=30)).strftime("%Y-%m-%d")
                row = conn.execute(
                    f"""
                    WITH t AS (
                      SELECT COALESCE(CAST("בזכות" AS REAL), 0.0) AS zc,
                             COALESCE(CAST("בחובה" AS REAL), 0.0) AS bh,
                             ({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR}) AS eff_d
                      FROM ledger_transaction
                      WHERE {LEDGER_SQL_TX_INCLUDED}
                    )
                    SELECT COALESCE(SUM(zc), 0), COALESCE(SUM(bh), 0)
                    FROM t
                    WHERE eff_d IS NOT NULL AND eff_d > ? AND eff_d <= ?
                    """,
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


# --- 2. holdings: allocation -------------------------------------------------


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


def cashflow_monthly(months: int = 24) -> dict[str, Any]:
    """``[{month, income, expense, net}]`` for last N months (effective month)."""
    conn = _tx_conn()
    if conn is None:
        return _empty_payload({"months": months})
    try:
        out_rows = dashboard_tx_sql.cashflow_monthly(conn, months)
    except Exception:  # noqa: BLE001
        log.exception("dashboard: cashflow_monthly failed")
        return _empty_payload({"months": months})
    finally:
        conn.close()
    if not out_rows:
        return _empty_payload({"months": months})
    return {"ok": True, "ledger_exists": True, "rows": out_rows, "months": months}


def top_categories(
    period: str = "12m",
    report_type: str = "expense",
    limit: int = 10,
    start_ym: str | None = None,
    end_ym: str | None = None,
) -> dict[str, Any]:
    """``[{category, amount, pct_of_expense?, pct_of_income?}]`` ranked desc."""
    meta: dict[str, Any] = {
        "period": period,
        "type": report_type,
        "limit": int(limit),
        **_category_window_meta(period, start_ym, end_ym),
    }
    conn = _tx_conn()
    if conn is None:
        return _empty_payload({**meta, "period_income_total": 0.0, "period_expense_total": 0.0})
    try:
        period_income_total, period_expense_total, ranked = (
            dashboard_tx_sql.top_categories_totals_and_rows(
                conn,
                period,
                report_type,
                int(limit),
                start_ym=start_ym,
                end_ym=end_ym,
            )
        )
    except Exception:  # noqa: BLE001
        log.exception("dashboard: top_categories failed")
        return _empty_payload({**meta, "period_income_total": 0.0, "period_expense_total": 0.0})
    finally:
        conn.close()
    meta["period_income_total"] = period_income_total
    meta["period_expense_total"] = period_expense_total
    meta["type"] = "income" if (report_type or "").lower().strip() == "income" else "expense"
    if not ranked:
        return _empty_payload(meta)
    out_rows: list[dict[str, Any]] = []
    for k, v in ranked:
        amt = float(v)
        row: dict[str, Any] = {"category": str(k), "amount": amt}
        if period_expense_total > 0:
            row["pct_of_expense"] = amt / period_expense_total
        if period_income_total > 0:
            row["pct_of_income"] = amt / period_income_total
        out_rows.append(row)
    return {
        "ok": True,
        "ledger_exists": True,
        **meta,
        "rows": out_rows,
    }


def category_period_stats(
    period: str = "12m",
    limit: int = 35,
    start_ym: str | None = None,
    end_ym: str | None = None,
) -> dict[str, Any]:
    """Per category for the period: income, expense, net, txn counts, % of period totals."""
    meta: dict[str, Any] = {
        "period": period,
        "limit": int(limit),
        **_category_window_meta(period, start_ym, end_ym),
    }
    conn = _tx_conn()
    if conn is None:
        return _empty_payload(
            {**meta, "period_income_total": 0.0, "period_expense_total": 0.0, "category_bucket_count": 0}
        )
    try:
        period_income_total, period_expense_total, category_bucket_count, out_rows = (
            dashboard_tx_sql.category_period_stats(
                conn, period, int(limit), start_ym=start_ym, end_ym=end_ym
            )
        )
    except Exception:  # noqa: BLE001
        log.exception("dashboard: category_period_stats failed")
        return _empty_payload(
            {**meta, "period_income_total": 0.0, "period_expense_total": 0.0, "category_bucket_count": 0}
        )
    finally:
        conn.close()
    meta["period_income_total"] = period_income_total
    meta["period_expense_total"] = period_expense_total
    meta["category_bucket_count"] = category_bucket_count
    if not out_rows:
        return _empty_payload(meta)
    return {
        "ok": True,
        "ledger_exists": True,
        **meta,
        "rows": out_rows,
    }


def source_category_matrix(
    months: int = 12,
    direction: str = "expense",
    top_sources: int = 10,
    top_categories: int = 12,
) -> dict[str, Any]:
    """Sparse pivot: sources × categories for expense or income in the last ``months`` buckets."""
    dir_ = (direction or "expense").lower().strip()
    if dir_ not in ("expense", "income"):
        dir_ = "expense"
    meta: dict[str, Any] = {
        "months": int(months),
        "direction": dir_,
        "top_sources": int(top_sources),
        "top_categories": int(top_categories),
    }
    conn = _tx_conn()
    if conn is None:
        return _empty_payload(meta)
    try:
        body = dashboard_tx_sql.source_category_matrix(
            conn,
            int(months),
            dir_,
            int(top_sources),
            int(top_categories),
        )
    except Exception:  # noqa: BLE001
        log.exception("dashboard: source_category_matrix failed")
        return _empty_payload(meta)
    finally:
        conn.close()
    if not body.get("sources"):
        return _empty_payload(meta)
    return {
        "ok": True,
        "ledger_exists": True,
        **meta,
        **body,
    }


def sources(months: int = 12) -> dict[str, Any]:
    """``[{source, count, expense, income}]`` over the last N months."""
    conn = _tx_conn()
    if conn is None:
        return _empty_payload({"months": int(months)})
    try:
        out_rows = dashboard_tx_sql.sources(conn, int(months))
    except Exception:  # noqa: BLE001
        log.exception("dashboard: sources failed")
        return _empty_payload({"months": int(months)})
    finally:
        conn.close()
    if not out_rows:
        return _empty_payload({"months": int(months)})
    return {
        "ok": True,
        "ledger_exists": True,
        "months": int(months),
        "rows": out_rows,
    }


def month_bounds() -> dict[str, Any]:
    """Min / max ``effective_ym`` in the ledger (included rows only), for dashboard range pickers."""
    conn = _tx_conn()
    if conn is None:
        return {"ok": True, "ledger_exists": False, "min_ym": None, "max_ym": None}
    try:
        lo, hi = dashboard_tx_sql.effective_month_bounds(conn)
        return {
            "ok": True,
            "ledger_exists": True,
            "min_ym": lo,
            "max_ym": hi,
        }
    except Exception:  # noqa: BLE001
        log.exception("dashboard: month_bounds failed")
        return {"ok": True, "ledger_exists": _ledger_exists(), "min_ym": None, "max_ym": None}
    finally:
        conn.close()


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


def _qs_cache_key(qs: dict[str, list[str]]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    items: list[tuple[str, tuple[str, ...]]] = []
    for k in sorted(qs.keys()):
        vals = qs.get(k) or []
        items.append((k, tuple(str(x) for x in vals)))
    return tuple(items)


def _dispatch_dashboard_request_uncached(name: str, qs: dict[str, list[str]]) -> dict[str, Any]:
    """Dispatch by name. Unknown name → ``ok: False``."""
    if name == "summary":
        return summary()
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
        start_ym = _qs_first(qs, "start_ym")
        end_ym = _qs_first(qs, "end_ym")
        return top_categories(
            period=period,
            report_type=report_type,
            limit=limit,
            start_ym=start_ym,
            end_ym=end_ym,
        )
    if name == "category-period-stats":
        period = _qs_first(qs, "period", "12m") or "12m"
        raw_l = _qs_int(qs, "limit", 500)
        # limit <= 0: return all categories (no SQL LIMIT). Else cap rows for responsiveness.
        _CAT_CAP = 8000
        limit = 0 if raw_l <= 0 else min(_CAT_CAP, raw_l)
        start_ym = _qs_first(qs, "start_ym")
        end_ym = _qs_first(qs, "end_ym")
        return category_period_stats(
            period=period, limit=limit, start_ym=start_ym, end_ym=end_ym
        )
    if name == "month-bounds":
        return month_bounds()
    if name == "sources":
        months = _qs_int(qs, "months", 12)
        return sources(months=months)
    if name == "source-category-matrix":
        months = _qs_int(qs, "months", 12)
        direction = _qs_first(qs, "direction", "expense") or "expense"
        top_sources = _qs_int(qs, "top_sources", 10)
        top_categories = _qs_int(qs, "top_categories", 12)
        return source_category_matrix(
            months=months,
            direction=direction,
            top_sources=top_sources,
            top_categories=top_categories,
        )
    return {"ok": False, "error": "unknown_endpoint", "message": f"unknown dashboard endpoint: {name}"}


def handle_dashboard_request(name: str, qs: dict[str, list[str]]) -> dict[str, Any]:
    """Dispatch with in-memory JSON cache keyed by endpoint + query + ledger ``mtime_ns``."""
    rev = _ledger_mtime_ns_for_cache()
    ckey = (str(name), _qs_cache_key(qs), rev)
    if ckey in _dash_response_cache:
        _dash_response_cache.move_to_end(ckey)
        return _dash_response_cache[ckey]
    payload = _dispatch_dashboard_request_uncached(name, qs)
    if payload.get("ok") is True:
        _dash_response_cache[ckey] = payload
        _dash_response_cache.move_to_end(ckey)
        while len(_dash_response_cache) > _DASH_CACHE_MAX:
            _dash_response_cache.popitem(last=False)
    return payload


__all__ = [
    "summary",
    "allocation_latest",
    "allocation_timeline",
    "cashflow_monthly",
    "top_categories",
    "category_period_stats",
    "month_bounds",
    "source_category_matrix",
    "sources",
    "ledger_meta",
    "handle_dashboard_request",
]
