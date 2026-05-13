#!/usr/bin/env python3
"""
One-off: load hist-data/enriched/hist_portfolio_valuation_series.csv into
``trade_portfolio_position`` (INSERT OR REPLACE by PK). Excess CSV columns are
ignored; bank-only DB columns are set NULL.

Run from repo root (recommended) or anywhere; default ``--csv`` / ``--db`` paths
are resolved from the repo root when ``FINANCE_WORKSPACE_ROOT`` is unset::

    python tools/import_hist_portfolio_valuation.py
    python tools/import_hist_portfolio_valuation.py --csv path/to.csv --db path/to/ledger.sqlite
    python tools/import_hist_portfolio_valuation.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from datetime import date, datetime
from typing import Any

# Repo root = parent of tools/
_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(_ROOT, "app", "backend") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "app", "backend"))

import config  # noqa: E402
from ledger.store import migrate_ledger_db  # noqa: E402


def _default_ledger_db_path() -> str:
    """
    Default ledger path for this CLI.

    ``config.ledger_db_file`` is relative to the process CWD when
    ``FINANCE_WORKSPACE_ROOT`` is unset, so running from ``tools/`` would create
    ``tools/data/ledger.sqlite``. When no workspace root is set, anchor to repo
    root (parent of ``tools/``).
    """
    if config.workspace_root():
        return config.ledger_db_file
    return os.path.normpath(os.path.join(_ROOT, "data", "ledger.sqlite"))

EXPECTED_CSV_COLUMNS: frozenset[str] = frozenset(
    {
        "snapshot_date",
        "portfolio_account",
        "security_number",
        "security_name",
        "segment_index",
        "valuation_kind",
        "quantity",
        "last_price",
        "value_ils",
        "real_value",
        "ils_per_usd_close",
        "yahoo_ticker",
        "price_source",
        "is_usd_denominated",
    }
)

OPTIONAL_CSV_COLUMNS: frozenset[str] = frozenset({"price_multiplier"})
ALLOWED_CSV_COLUMNS: frozenset[str] = EXPECTED_CSV_COLUMNS | OPTIONAL_CSV_COLUMNS

# Every column we write (must exist on table after migrate).
REQUIRED_DB_COLUMNS: tuple[str, ...] = (
    "snapshot_date",
    "portfolio_account",
    "security_number",
    "security_name",
    "avg_purchase_price",
    "quantity",
    "last_price",
    "value_ils",
    "daily_change_pct",
    "profit_pct",
    "profit_ils",
    "pct_of_portfolio",
    "basis_price",
    "imported_at",
)

_INSERT_SQL = """
INSERT OR REPLACE INTO trade_portfolio_position (
    snapshot_date, portfolio_account, security_number, security_name,
    avg_purchase_price, quantity, last_price, value_ils,
    daily_change_pct, profit_pct, profit_ils, pct_of_portfolio, basis_price,
    imported_at
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

_INSERT_MULT_SQL = """
INSERT INTO trade_portfolio_position_multiplier (
    portfolio_account, security_number, security_name, price_multiplier
) VALUES (?,?,?,?)
ON CONFLICT(portfolio_account, security_number) DO UPDATE SET
    price_multiplier = excluded.price_multiplier,
    security_name = COALESCE(
        excluded.security_name,
        trade_portfolio_position_multiplier.security_name
    );
"""

_BACKFILL_AVG_PURCHASE_SQL = """
UPDATE trade_portfolio_position AS p
SET avg_purchase_price = ap.src_avg
FROM (
  SELECT
    portfolio_account,
    security_number,
    MAX(avg_purchase_price) AS src_avg
  FROM trade_portfolio_position
  WHERE avg_purchase_price IS NOT NULL
  GROUP BY portfolio_account, security_number
) AS ap
WHERE p.portfolio_account = ap.portfolio_account
  AND p.security_number = ap.security_number
  AND p.avg_purchase_price IS NULL
"""

_BACKFILL_PROFIT_PCT_SQL = """
UPDATE trade_portfolio_position
SET profit_pct = (last_price - avg_purchase_price) / avg_purchase_price
WHERE profit_pct IS NULL
  AND avg_purchase_price IS NOT NULL
  AND last_price IS NOT NULL
  AND avg_purchase_price != 0
"""


def _table_column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f'PRAGMA table_info("{table}")')
    return {str(r[1]) for r in cur.fetchall()}


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _parse_iso_date(s: str) -> str:
    raw = (s or "").strip()
    if not raw:
        raise ValueError("empty snapshot_date")
    try:
        d = date.fromisoformat(raw[:10] if len(raw) >= 10 else raw)
    except ValueError as e:
        raise ValueError(f"invalid snapshot_date {raw!r}") from e
    return d.isoformat()


def _parse_optional_real(s: str) -> float | None:
    t = (s or "").strip()
    if t == "" or t.lower() in ("nan", "none", "null"):
        return None
    return float(t)


def _norm_security_number(s: str) -> str:
    return str(s).strip()


def import_csv_to_trade_portfolio(
    csv_path: str,
    db_path: str | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Migrate DB, validate CSV headers and ``trade_portfolio_position`` schema,
    then INSERT OR REPLACE one row per CSV data line (skipped when ``dry_run``).
    """
    db = db_path if db_path is not None else _default_ledger_db_path()
    ap_csv = os.path.normpath(os.path.join(os.getcwd(), csv_path))
    if not os.path.isfile(ap_csv):
        raise FileNotFoundError(f"CSV not found: {ap_csv}")

    migrate_ledger_db(db)

    imported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cols = _table_column_names(conn, "trade_portfolio_position")
        if not cols:
            raise RuntimeError("trade_portfolio_position table is missing after migrate_ledger_db")
        missing = [c for c in REQUIRED_DB_COLUMNS if c not in cols]
        if missing:
            raise RuntimeError(
                "trade_portfolio_position schema mismatch; missing columns: "
                + ", ".join(missing)
            )

        if not _table_exists(conn, "portfolio_instrument"):
            raise RuntimeError(
                "portfolio_instrument table is missing after migrate_ledger_db"
            )
        if not _table_exists(conn, "trade_portfolio_position_multiplier"):
            raise RuntimeError(
                "trade_portfolio_position_multiplier table is missing after migrate_ledger_db"
            )

        with open(ap_csv, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                raise ValueError("CSV has no header row")
            headers = {h.strip() for h in reader.fieldnames if h and h.strip()}
            if not EXPECTED_CSV_COLUMNS <= headers:
                absent = sorted(EXPECTED_CSV_COLUMNS - headers)
                raise ValueError("CSV header mismatch. Missing: " + repr(absent))
            extra = sorted(headers - ALLOWED_CSV_COLUMNS)
            if extra:
                raise ValueError("CSV header mismatch. Unexpected: " + repr(extra))

            batch: list[tuple[Any, ...]] = []
            mult_batch: list[tuple[Any, ...]] = []
            for i, row in enumerate(reader, start=2):
                snap = _parse_iso_date(row.get("snapshot_date", "") or "")
                if conn.execute("SELECT date(?)", (snap,)).fetchone()[0] != snap:
                    raise ValueError(f"line {i}: snapshot_date {snap!r} fails date() check")

                pf = (row.get("portfolio_account") or "").strip()
                sec = _norm_security_number(row.get("security_number") or "")
                if not pf or not sec:
                    raise ValueError(f"line {i}: portfolio_account and security_number required")

                batch.append(
                    (
                        snap,
                        pf,
                        sec,
                        (row.get("security_name") or "").strip() or None,
                        None,  # avg_purchase_price
                        _parse_optional_real(row.get("quantity", "") or ""),
                        _parse_optional_real(row.get("last_price", "") or ""),
                        _parse_optional_real(row.get("real_value", "") or ""),
                        None,  # daily_change_pct
                        None,  # profit_pct
                        None,  # profit_ils
                        None,  # pct_of_portfolio
                        None,  # basis_price
                        imported_at,
                    )
                )
                if "price_multiplier" in headers:
                    pm_raw = (row.get("price_multiplier") or "").strip()
                    if pm_raw and pm_raw.lower() not in ("nan", "none", "null"):
                        pm = float(pm_raw)
                        if pm <= 0:
                            raise ValueError(f"line {i}: price_multiplier must be > 0")
                        if pm != 1.0:
                            nm = (row.get("security_name") or "").strip() or None
                            mult_batch.append((pf, sec, nm, pm))

        if not batch:
            return {"db": db, "csv": ap_csv, "rows": 0, "dry_run": dry_run}

        if dry_run:
            would_insert = 0
            would_replace = 0
            for tup in batch:
                snap, pf, sec = tup[0], tup[1], tup[2]
                hit = conn.execute(
                    "SELECT 1 FROM trade_portfolio_position "
                    "WHERE snapshot_date = ? AND portfolio_account = ? AND security_number = ?",
                    (snap, pf, sec),
                ).fetchone()
                if hit:
                    would_replace += 1
                else:
                    would_insert += 1
            return {
                "db": db,
                "csv": ap_csv,
                "rows": len(batch),
                "dry_run": True,
                "would_insert": would_insert,
                "would_replace": would_replace,
                "imported_at": imported_at,
                "sample": batch[:5],
            }

        conn.executemany(_INSERT_SQL, batch)
        if mult_batch:
            conn.executemany(_INSERT_MULT_SQL, mult_batch)
        conn.execute(_BACKFILL_AVG_PURCHASE_SQL)
        conn.execute(_BACKFILL_PROFIT_PCT_SQL)
        conn.commit()
        return {"db": db, "csv": ap_csv, "rows": len(batch), "dry_run": False}
    finally:
        conn.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    default_csv = os.path.join(_ROOT, "hist-data", "enriched", "hist_portfolio_valuation_series.csv")
    p = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    p.add_argument("--csv", default=default_csv, help="Path to hist_portfolio_valuation_series.csv")
    p.add_argument(
        "--db",
        default="",
        help=(
            "Path to ledger.sqlite (default: <repo>/data/ledger.sqlite when "
            "FINANCE_WORKSPACE_ROOT is unset; else config.ledger_db_file)"
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate CSV and schema; report how many rows would INSERT vs REPLACE; no writes.",
    )
    return p.parse_args(argv)


def _format_sample_row(tup: tuple[Any, ...]) -> str:
    # Tuple order matches _INSERT_SQL: … value_ils, daily_change_pct, profit_pct, …
    snap, pf, sec, name, _avg, qty, last_price, value_ils, daily_change_pct = tup[:9]
    nm = (name or "")[:48]
    return (
        f"  PK ({snap!r}, {pf!r}, {sec!r}) name={nm!r} \n"
        f"quantity={qty!r} last_price={last_price!r} value_ils={value_ils!r} "
        f"daily_change_pct={daily_change_pct!r}"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    db = args.db.strip() or _default_ledger_db_path()
    out = import_csv_to_trade_portfolio(args.csv, db_path=db, dry_run=args.dry_run)
    if out.get("dry_run"):
        print("Dry run - no INSERT OR REPLACE was executed.")
        print(f"  CSV: {out['csv']}")
        print(f"  DB:  {out['db']}")
        print(f"  Rows that would be upserted: {out['rows']}")
        if out["rows"]:
            print(f"    (new PKs: {out['would_insert']}, existing PKs replaced: {out['would_replace']})")
            print(f"  imported_at would be set to: {out['imported_at']} (all rows)")
            print("  Bank-only columns would be NULL: avg_purchase_price, daily_change_pct, profit_pct,")
            print("    profit_ils, pct_of_portfolio, basis_price")
            print("  Sample (first 5 rows):")
            for tup in out.get("sample") or ():
                print(_format_sample_row(tup))
        return 0
    print(f"Imported {out['rows']} row(s) into {out['db']} from {out['csv']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
