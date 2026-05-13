"""Read-only queries for ``trade_portfolio_position`` (Portfolio UI)."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

import config
from ledger.store import migrate_ledger_db

# Unit separator — unlikely in bank account or security ids; stable in URLs when encoded.
_SERIES_SEP = "\x1f"

# Chartable REAL columns (API ``metric`` name == SQLite column name).
METRIC_COLUMNS: frozenset[str] = frozenset(
    {
        "value_ils",
        "quantity",
        "last_price",
        "avg_purchase_price",
        "profit_ils",
        "basis_price",
        "daily_change_pct",
        "profit_pct",
        "pct_of_portfolio",
    }
)

DEFAULT_METRIC = "value_ils"

# Stored bank/CSV prices × ``trade_portfolio_position_multiplier.price_multiplier`` (per portfolio + security_number).
_PRICE_METRICS: frozenset[str] = frozenset(
    {"last_price", "avg_purchase_price", "basis_price"}
)


def make_series_id(portfolio_account: str, security_number: str) -> str:
    return f"{portfolio_account}{_SERIES_SEP}{security_number}"


def parse_series_id(series_id: str) -> tuple[str, str]:
    parts = str(series_id).split(_SERIES_SEP, 1)
    if len(parts) != 2 or parts[0] == "" or parts[1] == "":
        raise ValueError("invalid series_id")
    return parts[0], parts[1]


def resolve_metric(metric: str | None) -> str:
    m = (metric or "").strip() or DEFAULT_METRIC
    if m not in METRIC_COLUMNS:
        raise ValueError(
            f"unknown metric {m!r}; allowed: {', '.join(sorted(METRIC_COLUMNS))}"
        )
    return m


def get_trade_portfolio_meta(db_path: str | None = None) -> dict[str, Any]:
    db = db_path if db_path is not None else config.ledger_db_file
    if not os.path.isfile(db):
        return {
            "ok": True,
            "ledger_exists": False,
            "db_path": db,
            "row_count": 0,
            "min_date": None,
            "max_date": None,
            "portfolio_accounts": [],
            "instruments": [],
            "metrics": sorted(METRIC_COLUMNS),
            "default_metric": DEFAULT_METRIC,
        }

    migrate_ledger_db(db)
    conn = sqlite3.connect(db)
    try:
        agg = conn.execute(
            """
            SELECT
                COUNT(*) AS row_count,
                MIN(snapshot_date) AS min_date,
                MAX(snapshot_date) AS max_date
            FROM trade_portfolio_position
            """
        ).fetchone()
        row_count = int(agg[0] or 0)
        min_date, max_date = agg[1], agg[2]

        acct_rows = conn.execute(
            """
            SELECT DISTINCT portfolio_account
            FROM trade_portfolio_position
            ORDER BY portfolio_account
            """
        ).fetchall()
        portfolio_accounts = [str(r[0]) for r in acct_rows]

        inst_rows = conn.execute(
            """
            SELECT
                p.portfolio_account,
                p.security_number,
                (
                    SELECT p2.security_name
                    FROM trade_portfolio_position p2
                    WHERE p2.portfolio_account = p.portfolio_account
                      AND p2.security_number = p.security_number
                    ORDER BY p2.snapshot_date DESC
                    LIMIT 1
                ) AS security_name,
                MIN(p.snapshot_date) AS first_seen,
                MAX(p.snapshot_date) AS last_seen,
                (
                    SELECT p3.value_ils
                    FROM trade_portfolio_position p3
                    WHERE p3.portfolio_account = p.portfolio_account
                      AND p3.security_number = p.security_number
                    ORDER BY p3.snapshot_date DESC
                    LIMIT 1
                ) AS latest_value_ils
            FROM trade_portfolio_position p
            GROUP BY p.portfolio_account, p.security_number
            ORDER BY p.portfolio_account, p.security_number
            """
        ).fetchall()
    finally:
        conn.close()

    instruments: list[dict[str, Any]] = []
    for acc, sec, name, first, last, latest_v in inst_rows:
        sid = make_series_id(str(acc), str(sec))
        label = str(name).strip() if name else str(sec)
        instruments.append(
            {
                "series_id": sid,
                "portfolio_account": str(acc),
                "security_number": str(sec),
                "security_name": str(name) if name is not None else None,
                "label": label,
                "first_seen": first,
                "last_seen": last,
                "latest_value_ils": float(latest_v)
                if latest_v is not None and isinstance(latest_v, (int, float))
                else None,
            }
        )

    return {
        "ok": True,
        "ledger_exists": True,
        "db_path": db,
        "row_count": row_count,
        "min_date": min_date,
        "max_date": max_date,
        "portfolio_accounts": portfolio_accounts,
        "instruments": instruments,
        "metrics": sorted(METRIC_COLUMNS),
        "default_metric": DEFAULT_METRIC,
    }


def query_trade_portfolio_timeseries(
    db_path: str | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    portfolio_account: str | None = None,
    metric: str | None = None,
    series_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Return long-format points for charting."""
    db = db_path if db_path is not None else config.ledger_db_file
    col = resolve_metric(metric)

    if not os.path.isfile(db):
        return {
            "ok": True,
            "ledger_exists": False,
            "metric": col,
            "points": [],
        }

    migrate_ledger_db(db)

    where: list[str] = []
    params: list[Any] = []

    if start_date and str(start_date).strip():
        where.append("p.snapshot_date >= ?")
        params.append(str(start_date).strip())
    if end_date and str(end_date).strip():
        where.append("p.snapshot_date <= ?")
        params.append(str(end_date).strip())
    if portfolio_account and str(portfolio_account).strip():
        where.append("p.portfolio_account = ?")
        params.append(str(portfolio_account).strip())

    series_filter: list[tuple[str, str]] = []
    if series_ids:
        for raw in series_ids:
            s = str(raw).strip()
            if not s:
                continue
            try:
                series_filter.append(parse_series_id(s))
            except ValueError as e:
                raise ValueError(f"invalid series id: {raw!r}") from e

    if series_filter:
        ph = " OR ".join(
            "(p.portfolio_account = ? AND p.security_number = ?)" for _ in series_filter
        )
        where.append(f"({ph})")
        for acc, sec in series_filter:
            params.extend([acc, sec])

    wh_clause = f"WHERE {' AND '.join(where)}" if where else ""

    if col in _PRICE_METRICS:
        v_sql = (
            f"CASE WHEN p.{col} IS NULL THEN NULL "
            f"ELSE p.{col} * COALESCE(m.price_multiplier, 1) END AS v"
        )
    else:
        v_sql = f"p.{col} AS v"

    # ``col`` is validated by resolve_metric only.
    sql = f"""
        SELECT p.snapshot_date, p.portfolio_account, p.security_number, p.security_name, {v_sql},
               p.quantity AS position_quantity
        FROM trade_portfolio_position p
        LEFT JOIN trade_portfolio_position_multiplier m
          ON m.portfolio_account = p.portfolio_account
         AND m.security_number = p.security_number
        {wh_clause}
        ORDER BY p.snapshot_date, p.portfolio_account, p.security_number
    """

    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    points: list[dict[str, Any]] = []
    for snap, acc, sec, sname, v, pos_qty in rows:
        sid = make_series_id(str(acc), str(sec))
        label = str(sname).strip() if sname else str(sec)
        if v is not None and isinstance(v, (int, float)):
            val: float | None = float(v)
        else:
            val = None
        if pos_qty is not None and isinstance(pos_qty, (int, float)):
            qty_val: float | None = float(pos_qty)
        else:
            qty_val = None
        points.append(
            {
                "snapshot_date": str(snap),
                "series_id": sid,
                "value": val,
                "quantity": qty_val,
                "label": label,
                "portfolio_account": str(acc),
                "security_number": str(sec),
            }
        )

    return {
        "ok": True,
        "ledger_exists": True,
        "metric": col,
        "points": points,
    }
