"""SQL aggregations for heatmap matrices and detail drills (no full-ledger pandas load).

Month / category sums reuse ``LEDGER_SQL_EFFECTIVE_YM_EXPR`` and ``LEDGER_SQL_TX_INCLUDED``.
Paint, recency column order, and stats stay in ``api.heatmap`` Python.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

from ledger.store import (
    LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR,
    LEDGER_SQL_EFFECTIVE_YM_EXPR,
    LEDGER_SQL_TX_INCLUDED,
    ledger_connect_readonly,
)

ReportType = Literal["expense", "income", "net"]

_DETAIL_SELECT = f"""
SELECT
  id,
  "תאריך",
  "בחובה",
  "בזכות",
  "מקור עסקה",
  "פירוט נוסף",
  "תאור מורחב",
  "4 ספרות",
  "קטגוריה",
  notes,
  statement_month,
  ingested_at,
  category_updated_at,
  data_updated_at,
  ({LEDGER_SQL_EFFECTIVE_YM_EXPR}) AS YearMonth,
  ({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR}) AS effective_tx_date
FROM ledger_transaction
WHERE {LEDGER_SQL_TX_INCLUDED}
"""


def fetch_month_category_sums(db_path: str) -> list[dict[str, Any]]:
    """Return ``{effective_ym, category, expense_sum, income_sum}`` for included rows."""
    sql = f"""
    SELECT
      ({LEDGER_SQL_EFFECTIVE_YM_EXPR}) AS effective_ym,
      CASE
        WHEN "קטגוריה" IS NULL OR TRIM(COALESCE("קטגוריה", '')) = '' THEN '(uncategorized)'
        ELSE TRIM("קטגוריה")
      END AS category,
      SUM(CASE WHEN COALESCE(CAST("בחובה" AS REAL), 0) > 0
          THEN CAST("בחובה" AS REAL) ELSE 0 END) AS expense_sum,
      SUM(CASE WHEN COALESCE(CAST("בזכות" AS REAL), 0) > 0
          THEN CAST("בזכות" AS REAL) ELSE 0 END) AS income_sum
    FROM ledger_transaction
    WHERE {LEDGER_SQL_TX_INCLUDED}
      AND ({LEDGER_SQL_EFFECTIVE_YM_EXPR}) IS NOT NULL
    GROUP BY 1, 2
    HAVING expense_sum > 0 OR income_sum > 0
    """
    conn = ledger_connect_readonly(db_path)
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [{cols[i]: row[i] for i in range(len(cols))} for row in cur.fetchall()]
    finally:
        conn.close()


def _cat_expr(alias: str = "") -> str:
    p = f"{alias}." if alias else ""
    return f"""CASE
      WHEN {p}"קטגוריה" IS NULL OR TRIM(COALESCE({p}"קטגוריה", '')) = '' THEN '(uncategorized)'
      ELSE TRIM({p}"קטגוריה")
    END"""


def _src_expr(alias: str = "") -> str:
    p = f"{alias}." if alias else ""
    return f"""CASE
      WHEN {p}"מקור עסקה" IS NULL OR TRIM(COALESCE({p}"מקור עסקה", '')) = '' THEN '(unknown)'
      ELSE TRIM({p}"מקור עסקה")
    END"""


def fetch_detail_rows(
    db_path: str,
    *,
    report_type: ReportType = "expense",
    year_month: str | None = None,
    category: str | None = None,
    source: str | None = None,
    period: str = "12m",
    start_ym: str | None = None,
    end_ym: str | None = None,
    recent_months: int | None = None,
) -> list[dict[str, Any]]:
    """Filtered ledger rows for heatmap detail (sorted newest effective date first)."""
    from ledger import dashboard_sql as dashboard_tx_sql

    clauses: list[str] = [LEDGER_SQL_TX_INCLUDED]
    params: list[Any] = []

    if year_month:
        clauses.append(f"({LEDGER_SQL_EFFECTIVE_YM_EXPR}) = ?")
        params.append(year_month)

    if category is not None:
        clauses.append(f"({_cat_expr()}) = ?")
        params.append(category)

    if source is not None:
        clauses.append(f"({_src_expr()}) = ?")
        params.append(source)

    if report_type == "expense":
        clauses.append('COALESCE(CAST("בחובה" AS REAL), 0) > 0')
    elif report_type == "income":
        clauses.append('COALESCE(CAST("בזכות" AS REAL), 0) > 0')
    # net: both sides allowed; caller may split

    bounds = dashboard_tx_sql.normalize_ym_range(start_ym, end_ym)
    if bounds is not None:
        lo, hi = bounds
        clauses.append(f"({LEDGER_SQL_EFFECTIVE_YM_EXPR}) >= ?")
        params.append(lo)
        clauses.append(f"({LEDGER_SQL_EFFECTIVE_YM_EXPR}) <= ?")
        params.append(hi)
    elif recent_months is not None and recent_months > 0:
        clauses.append(
            f"""({LEDGER_SQL_EFFECTIVE_YM_EXPR}) IN (
              SELECT ym FROM (
                SELECT DISTINCT ({LEDGER_SQL_EFFECTIVE_YM_EXPR}) AS ym
                FROM ledger_transaction
                WHERE {LEDGER_SQL_TX_INCLUDED}
                  AND ({LEDGER_SQL_EFFECTIVE_YM_EXPR}) IS NOT NULL
                ORDER BY ym DESC
                LIMIT ?
              )
            )"""
        )
        params.append(int(recent_months))
    else:
        raw = (period or "12m").strip()
        p = raw.lower()
        if p == "all":
            pass
        elif len(raw) == 4 and raw.isdigit():
            clauses.append(f"({LEDGER_SQL_EFFECTIVE_YM_EXPR}) LIKE ?")
            params.append(f"{raw}-%")
        elif p == "30d":
            clauses.append(
                f"""({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR}) IS NOT NULL
                AND ({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR}) > date((
                  SELECT MAX({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR})
                  FROM ledger_transaction WHERE {LEDGER_SQL_TX_INCLUDED}
                ), '-30 days')"""
            )
        elif p == "ytd":
            clauses.append(
                f"""({LEDGER_SQL_EFFECTIVE_YM_EXPR}) LIKE (
                  SELECT strftime('%Y-', MAX({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR}))
                  FROM ledger_transaction WHERE {LEDGER_SQL_TX_INCLUDED}
                ) || '%'"""
            )
        else:
            n = {"12m": 12, "3m": 3, "6m": 6, "30d": 1}.get(p, 12)
            clauses.append(
                f"""({LEDGER_SQL_EFFECTIVE_YM_EXPR}) IN (
                  SELECT ym FROM (
                    SELECT DISTINCT ({LEDGER_SQL_EFFECTIVE_YM_EXPR}) AS ym
                    FROM ledger_transaction
                    WHERE {LEDGER_SQL_TX_INCLUDED}
                      AND ({LEDGER_SQL_EFFECTIVE_YM_EXPR}) IS NOT NULL
                    ORDER BY ym DESC
                    LIMIT ?
                  )
                )"""
            )
            params.append(int(n))

    where = " AND ".join(clauses)
    sql = f"""
    SELECT
      id,
      "תאריך",
      "בחובה",
      "בזכות",
      "מקור עסקה",
      "פירוט נוסף",
      "תאור מורחב",
      "4 ספרות",
      "קטגוריה",
      notes,
      statement_month,
      ingested_at,
      category_updated_at,
      data_updated_at,
      ({LEDGER_SQL_EFFECTIVE_YM_EXPR}) AS YearMonth,
      ({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR}) AS effective_tx_date
    FROM ledger_transaction
    WHERE {where}
    ORDER BY ({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR}) DESC, "תאריך" DESC, id DESC
    LIMIT ?
    """
    cap = 1000
    conn = ledger_connect_readonly(db_path)
    try:
        cur = conn.execute(sql, [*params, cap + 1])
        cols = [d[0] for d in cur.description]
        fetched = [{cols[i]: row[i] for i in range(len(cols))} for row in cur.fetchall()]
    finally:
        conn.close()
    truncated = len(fetched) > cap
    rows = fetched[:cap]
    if rows:
        rows[0] = {**rows[0], "_detail_truncated": truncated, "_detail_limit": cap}
    return rows
