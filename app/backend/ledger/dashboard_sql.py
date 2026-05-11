"""SQLite aggregations for dashboard transaction endpoints (no pandas).

Period / effective month rules mirror legacy ``api.dashboard`` pandas helpers:
- ``effective_ym``: valid ``statement_month`` (YYYY-MM) per schema, else ``strftime`` month of ``תאריך``.
- ``tx_date``: first day of valid ``statement_month`` or ``date(תאריך)`` — used for 30d / YTD anchors.
- ``30d`` / ``ytd`` / last-N-months buckets match prior behavior.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from typing import Any

_YM_RE = re.compile(r"^(\d{4})-(\d{2})$")
_YM_RANGE_MAX_MONTHS = 240
_CATEGORY_STATS_MAX_ROWS = 8000

from ledger.store import (
    LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR,
    LEDGER_SQL_EFFECTIVE_YM_EXPR,
    LEDGER_SQL_TX_INCLUDED,
)

# --- Shared CTEs ---------------------------------------------------------------------------


TX_NORM = f"""
tx_norm AS (
  SELECT
    ({LEDGER_SQL_EFFECTIVE_YM_EXPR}) AS effective_ym,
    ({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR}) AS tx_date,
    COALESCE(CAST("בזכות" AS REAL), 0.0) AS income_amt,
    COALESCE(CAST("בחובה" AS REAL), 0.0) AS expense_amt,
    CASE
      WHEN "קטגוריה" IS NULL OR TRIM(COALESCE("קטגוריה", '')) = ''
      THEN '(uncategorized)'
      ELSE TRIM("קטגוריה")
    END AS cat_norm,
    CASE
      WHEN "מקור עסקה" IS NULL OR TRIM(COALESCE("מקור עסקה", '')) = ''
      THEN '(unknown)'
      ELSE TRIM("מקור עסקה")
    END AS src_norm
  FROM ledger_transaction
  WHERE {LEDGER_SQL_TX_INCLUDED}
)"""

PERIOD_TX_30D = """
period_tx AS (
  SELECT t.*
  FROM tx_norm t
  WHERE t.tx_date IS NOT NULL
    AND t.tx_date > date((SELECT MAX(tx_date) FROM tx_norm WHERE tx_date IS NOT NULL), '-30 days')
    AND t.tx_date <= (SELECT MAX(tx_date) FROM tx_norm WHERE tx_date IS NOT NULL)
)"""

PERIOD_TX_YTD = """
period_tx AS (
  SELECT t.*
  FROM tx_norm t
  WHERE t.effective_ym IS NOT NULL
    AND t.effective_ym LIKE (
      SELECT strftime('%Y-', MAX(tx_date)) FROM tx_norm WHERE tx_date IS NOT NULL
    ) || '%'
)"""

PERIOD_TX_LAST_N = """
period_tx AS (
  SELECT t.*
  FROM tx_norm t
  WHERE t.effective_ym IS NOT NULL
    AND t.effective_ym IN (
      SELECT effective_ym
      FROM tx_norm
      WHERE effective_ym IS NOT NULL
      GROUP BY effective_ym
      ORDER BY effective_ym DESC
      LIMIT ?
    )
)"""

PERIOD_TX_YM_RANGE = """
period_tx AS (
  SELECT t.*
  FROM tx_norm t
  WHERE t.effective_ym IS NOT NULL
    AND t.effective_ym >= ?
    AND t.effective_ym <= ?
)"""

PERIOD_TO_MONTHS = {"30d": 1, "ytd": -1, "12m": 12, "3m": 3, "6m": 6}
_YEAR_RE = re.compile(r"^\d{4}$")


def is_valid_effective_ym(value: str) -> bool:
    """True if ``value`` is ``YYYY-MM`` with a real calendar month."""
    m = _YM_RE.match((value or "").strip())
    if not m:
        return False
    month = int(m.group(2))
    return 1 <= month <= 12


def ym_range_span_months(lo: str, hi: str) -> int:
    """Inclusive month count from ``lo`` to ``hi`` (both ``YYYY-MM``)."""
    m_lo = _YM_RE.match(lo.strip())
    m_hi = _YM_RE.match(hi.strip())
    if not m_lo or not m_hi:
        return 0
    y1, mo1 = int(m_lo.group(1)), int(m_lo.group(2))
    y2, mo2 = int(m_hi.group(1)), int(m_hi.group(2))
    return (y2 - y1) * 12 + (mo2 - mo1) + 1


def normalize_ym_range(
    start_ym: str | None, end_ym: str | None
) -> tuple[str, str] | None:
    """Return ordered ``(lo, hi)`` if both are valid and span ≤ ``_YM_RANGE_MAX_MONTHS``."""
    if start_ym is None or end_ym is None:
        return None
    a = start_ym.strip()
    b = end_ym.strip()
    if not is_valid_effective_ym(a) or not is_valid_effective_ym(b):
        return None
    lo, hi = (a, b) if a <= b else (b, a)
    if ym_range_span_months(lo, hi) > _YM_RANGE_MAX_MONTHS:
        return None
    return lo, hi


def _with_period_parts(period: str) -> tuple[str, list[Any]]:
    """Return (period_tx_sql, bind_params) from ``period`` token only (no custom YM range)."""
    raw = (period or "12m").strip()
    p = raw.lower()
    if p == "30d":
        return PERIOD_TX_30D, []
    if p == "ytd":
        return PERIOD_TX_YTD, []
    if p == "all":
        return "period_tx AS (SELECT * FROM tx_norm)", []
    if _YEAR_RE.fullmatch(raw):
        y = raw
        return PERIOD_TX_YM_RANGE, [f"{y}-01", f"{y}-12"]
    n = PERIOD_TO_MONTHS.get(p, 12)
    if n is None or n <= 0:
        n = 12
    return PERIOD_TX_LAST_N, [int(n)]


def _with_header(
    period: str,
    start_ym: str | None = None,
    end_ym: str | None = None,
) -> tuple[str, list[Any]]:
    """Build ``tx_norm`` + ``period_tx`` CTE clause and bind params.

    Precedence (documented contract with API):
    1. Both ``start_ym`` and ``end_ym`` valid and span ≤ ``_YM_RANGE_MAX_MONTHS`` → inclusive
       ``effective_ym`` filter.
    2. Else ``period`` token: ``30d``, ``ytd``, ``all``, calendar year ``YYYY``, last-N months,
       default ``12m``.
    """
    bounds = normalize_ym_range(start_ym, end_ym)
    if bounds is not None:
        lo, hi = bounds
        ptx, params = PERIOD_TX_YM_RANGE, [lo, hi]
    else:
        ptx, params = _with_period_parts(period)
    return f"{TX_NORM},\n{ptx}", params


def _with_last_n_months(n: int) -> tuple[str, list[Any]]:
    return f"{TX_NORM},\n{PERIOD_TX_LAST_N}", [max(1, int(n))]


def _with_period_tx_all() -> tuple[str, list[Any]]:
    """All transaction rows (matches pandas path when ``months <= 0``)."""
    return f"{TX_NORM},\nperiod_tx AS (SELECT * FROM tx_norm)", []


def effective_month_bounds(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    """Min / max ``effective_ym`` over included ledger rows (``None`` if empty)."""
    sql = f"""WITH {TX_NORM}
    SELECT MIN(effective_ym), MAX(effective_ym)
    FROM tx_norm
    WHERE effective_ym IS NOT NULL
    """
    row = conn.execute(sql).fetchone()
    if not row or row[0] is None:
        return None, None
    return str(row[0]), str(row[1])


def _with_last_n_or_all(months: int) -> tuple[str, list[Any]]:
    if int(months) <= 0:
        return _with_period_tx_all()
    return _with_last_n_months(months)


def cashflow_monthly(conn: sqlite3.Connection, months: int) -> list[dict[str, Any]]:
    wh, params = _with_last_n_or_all(months)
    sql = f"""WITH {wh}
    SELECT effective_ym AS month,
           COALESCE(SUM(income_amt), 0.0) AS income,
           COALESCE(SUM(expense_amt), 0.0) AS expense
    FROM period_tx
    WHERE effective_ym IS NOT NULL
    GROUP BY effective_ym
    ORDER BY effective_ym
    """
    cur = conn.execute(sql, params)
    rows = []
    for ym, inc, exp in cur.fetchall():
        incf = float(inc or 0.0)
        expf = float(exp or 0.0)
        rows.append(
            {"month": str(ym), "income": incf, "expense": expf, "net": incf - expf}
        )
    return rows


def top_categories_totals_and_rows(
    conn: sqlite3.Connection,
    period: str,
    report_type: str,
    limit: int,
    start_ym: str | None = None,
    end_ym: str | None = None,
) -> tuple[float, float, list[tuple[str, float]]]:
    wh, params = _with_header(period, start_ym=start_ym, end_ym=end_ym)
    sql_tot = f"WITH {wh} SELECT COALESCE(SUM(income_amt), 0.0), COALESCE(SUM(expense_amt), 0.0) FROM period_tx"
    tot = conn.execute(sql_tot, params).fetchone()
    period_income_total = float(tot[0] or 0.0)
    period_expense_total = float(tot[1] or 0.0)

    if report_type == "income":
        col_filter = "income_amt > 0"
        col_sum = "income_amt"
    else:
        col_filter = "expense_amt > 0"
        col_sum = "expense_amt"

    sql_cats = f"""WITH {wh}
    SELECT cat_norm, COALESCE(SUM({col_sum}), 0.0) AS amt
    FROM period_tx
    WHERE {col_filter}
    GROUP BY cat_norm
    ORDER BY amt DESC
    LIMIT ?
    """
    cur = conn.execute(sql_cats, [*params, int(limit)])
    ranked = [(str(r[0]), float(r[1] or 0.0)) for r in cur.fetchall()]
    return period_income_total, period_expense_total, ranked


def category_period_stats(
    conn: sqlite3.Connection,
    period: str,
    limit: int,
    start_ym: str | None = None,
    end_ym: str | None = None,
) -> tuple[float, float, int, list[dict[str, Any]]]:
    wh, params = _with_header(period, start_ym=start_ym, end_ym=end_ym)
    sql_tot = f"WITH {wh} SELECT COALESCE(SUM(income_amt), 0.0), COALESCE(SUM(expense_amt), 0.0) FROM period_tx"
    tot = conn.execute(sql_tot, params).fetchone()
    period_income_total = float(tot[0] or 0.0)
    period_expense_total = float(tot[1] or 0.0)

    sql_cat_count = f"""WITH {wh}
    SELECT COUNT(*) FROM (
      SELECT cat_norm FROM period_tx
      GROUP BY cat_norm
      HAVING SUM(income_amt) > 0 OR SUM(expense_amt) > 0
    )
    """
    cnt_row = conn.execute(sql_cat_count, params).fetchone()
    category_bucket_count = int(cnt_row[0] or 0)

    grp_core = f"""WITH {wh}
    SELECT cat_norm,
           COALESCE(SUM(income_amt), 0.0) AS income,
           COALESCE(SUM(expense_amt), 0.0) AS expense,
           COUNT(*) AS txn_count
    FROM period_tx
    GROUP BY cat_norm
    HAVING SUM(income_amt) > 0 OR SUM(expense_amt) > 0
    ORDER BY SUM(income_amt) + SUM(expense_amt) DESC
    """

    lim = int(limit)
    if lim <= 0:
        sql_grp = f"{grp_core}\n"
        cur = conn.execute(sql_grp, params)
    else:
        cap = min(lim, _CATEGORY_STATS_MAX_ROWS)
        sql_grp = f"{grp_core}\nLIMIT ?\n"
        cur = conn.execute(sql_grp, [*params, cap])
    out_rows: list[dict[str, Any]] = []
    for cat, inc, exp, cnt in cur.fetchall():
        incf = float(inc or 0.0)
        expf = float(exp or 0.0)
        net = incf - expf
        out_rows.append(
            {
                "category": str(cat),
                "income": incf,
                "expense": expf,
                "net": net,
                "txn_count": int(cnt),
                "pct_of_period_income": incf / period_income_total if period_income_total > 0 else 0.0,
                "pct_of_period_expense": expf / period_expense_total if period_expense_total > 0 else 0.0,
            }
        )
    return period_income_total, period_expense_total, category_bucket_count, out_rows


def sources(conn: sqlite3.Connection, months: int) -> list[dict[str, Any]]:
    wh, params = _with_last_n_or_all(months)
    sql = f"""WITH {wh}
    SELECT src_norm,
           COUNT(*) AS cnt,
           COALESCE(SUM(expense_amt), 0.0) AS expense,
           COALESCE(SUM(income_amt), 0.0) AS income
    FROM period_tx
    GROUP BY src_norm
    ORDER BY expense DESC
    """
    cur = conn.execute(sql, params)
    return [
        {
            "source": str(r[0]),
            "count": int(r[1]),
            "expense": float(r[2] or 0.0),
            "income": float(r[3] or 0.0),
        }
        for r in cur.fetchall()
    ]


def source_category_matrix(
    conn: sqlite3.Connection,
    months: int,
    direction: str,
    top_sources: int,
    top_categories: int,
) -> dict[str, Any]:
    dir_ = (direction or "expense").lower().strip()
    if dir_ not in ("expense", "income"):
        dir_ = "expense"
    col_sql = "expense_amt" if dir_ == "expense" else "income_amt"
    wh, params = _with_last_n_or_all(months)
    sql_pairs = f"""WITH {wh}
    SELECT src_norm, cat_norm, COALESCE(SUM({col_sql}), 0.0) AS amt
    FROM period_tx
    WHERE {col_sql} > 0
    GROUP BY src_norm, cat_norm
    """
    cur = conn.execute(sql_pairs, params)
    pair_sum: dict[tuple[str, str], float] = defaultdict(float)
    for s, c, a in cur.fetchall():
        pair_sum[(str(s), str(c))] += float(a or 0.0)

    if not pair_sum:
        return {
            "sources": [],
            "categories": [],
            "cells": [],
            "row_totals": [],
            "col_totals": [],
            "grand_total": 0.0,
        }

    row_totals: dict[str, float] = defaultdict(float)
    col_totals: dict[str, float] = defaultdict(float)
    for (s, c), v in pair_sum.items():
        row_totals[s] += v
        col_totals[c] += v

    grand_total = sum(pair_sum.values())

    k = max(1, int(top_sources))
    m = max(1, int(top_categories))
    ts = sorted(row_totals.keys(), key=lambda x: -row_totals[x])[:k]
    tc = sorted(col_totals.keys(), key=lambda x: -col_totals[x])[:m]
    rest_idx = [s for s in row_totals if s not in ts]
    rest_col = [c for c in col_totals if c not in tc]

    sources_list = list(ts)
    categories_list = list(tc)
    cells: list[list[float]] = []
    for s in ts:
        row_vals = [pair_sum.get((s, c), 0.0) for c in tc]
        if rest_col:
            row_vals.append(sum(pair_sum.get((s, c), 0.0) for c in rest_col))
        cells.append(row_vals)
    if rest_col:
        categories_list.append("(other categories)")

    col_totals_out = [col_totals[c] for c in tc]
    if rest_col:
        col_totals_out.append(sum(col_totals[c] for c in rest_col))

    rows_out_totals = [row_totals[s] for s in ts]

    if rest_idx:
        other_row_vals: list[float] = []
        for c in tc:
            other_row_vals.append(sum(pair_sum.get((s, c), 0.0) for s in rest_idx))
        if rest_col:
            corner = sum(
                pair_sum.get((s, c), 0.0) for s in rest_idx for c in rest_col
            )
            other_row_vals.append(corner)
        if sum(other_row_vals) > 1e-9:
            sources_list.append("(other sources)")
            cells.append(other_row_vals)
            rows_out_totals.append(sum(row_totals[s] for s in rest_idx))

    return {
        "sources": sources_list,
        "categories": categories_list,
        "cells": cells,
        "row_totals": rows_out_totals,
        "col_totals": col_totals_out,
        "grand_total": float(grand_total),
    }
