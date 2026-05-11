"""
Holdings balances in SQLite: ``holdings_balance`` melt/wide helpers, upserts, and API support.

Wide frames (date column ``תאריך`` plus per-activity balance columns) come from the compile
path (workbooks) or in-memory edits; all non-date columns become ``activity_type`` keys when
melted. Normalizes known spelling variants for the deposits column.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import pandas as pd

import config
from pipeline.compiler import parse_post_ingest_date_scalar
from ledger import migrate_ledger_db

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


def _norm_date(v: Any) -> str:
    ts = parse_post_ingest_date_scalar(v)
    if pd.isna(ts):
        raise ValueError(f"invalid as_of_date: {v!r}")
    return ts.strftime("%Y-%m-%d")


def _month_start_date(v: Any) -> str:
    ts = pd.to_datetime(_norm_date(v), errors="coerce", format="%Y-%m-%d")
    if pd.isna(ts):
        raise ValueError(f"invalid as_of_date: {v!r}")
    return ts.replace(day=1).strftime("%Y-%m-%d")


def wide_holdings_to_long(df: pd.DataFrame) -> pd.DataFrame:
    if "תאריך" not in df.columns:
        raise ValueError('wide holdings frame must include a "תאריך" column')
    id_vars = ["תאריך"]
    others = [c for c in df.columns if c != "תאריך"]
    if not others:
        raise ValueError("wide holdings frame has no balance columns besides תאריך")
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

    # Holdings are monthly snapshots; anchor any in-month source date to month start.
    long_df["as_of_date"] = long_df["as_of_date"].map(_month_start_date)
    long_df["balance_ils"] = pd.to_numeric(long_df["balance_ils"], errors="coerce").fillna(0.0)
    return long_df


def load_holdings_long_dataframe(db_path: str) -> pd.DataFrame:
    """All rows in ``holdings_balance`` (dedupe identity = ``(as_of_date, activity_type)``)."""
    migrate_ledger_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT as_of_date, activity_type, balance_ils FROM holdings_balance ORDER BY as_of_date, activity_type",
            conn,
        )
    finally:
        conn.close()
    return df


def list_holdings_activity_types(db_path: str | None = None) -> list[str]:
    """Distinct activity types in holdings_balance, sorted by name."""
    db = db_path if db_path is not None else config.ledger_db_file
    migrate_ledger_db(db)
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT activity_type
            FROM holdings_balance
            WHERE activity_type IS NOT NULL AND TRIM(activity_type) != ''
            ORDER BY activity_type COLLATE NOCASE
            """
        ).fetchall()
    finally:
        conn.close()
    return [str(r[0]) for r in rows]


def get_holdings_meta(db_path: str | None = None) -> dict[str, Any]:
    """Metadata snapshot for holdings page filters and stats."""
    db = db_path if db_path is not None else config.ledger_db_file
    migrate_ledger_db(db)
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS row_count,
                COUNT(DISTINCT as_of_date) AS date_count,
                MIN(as_of_date) AS min_date,
                MAX(as_of_date) AS max_date
            FROM holdings_balance
            """
        ).fetchone()
    finally:
        conn.close()
    return {
        "db_path": db,
        "row_count": int(row[0] or 0),
        "date_count": int(row[1] or 0),
        "min_date": row[2],
        "max_date": row[3],
        "activity_types": list_holdings_activity_types(db),
    }


def query_holdings_timeline(
    db_path: str | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    activity_types: list[str] | None = None,
) -> pd.DataFrame:
    """Timeline rows from holdings_balance with optional date/activity filters."""
    db = db_path if db_path is not None else config.ledger_db_file
    migrate_ledger_db(db)

    where_parts: list[str] = []
    params: list[Any] = []
    if start_date:
        where_parts.append("as_of_date >= ?")
        params.append(_norm_date(start_date))
    if end_date:
        where_parts.append("as_of_date <= ?")
        params.append(_norm_date(end_date))
    normalized_activities = []
    if activity_types:
        for name in activity_types:
            n = _canonical_activity(str(name).strip())
            if n:
                normalized_activities.append(n)
    if normalized_activities:
        placeholders = ",".join("?" for _ in normalized_activities)
        where_parts.append(f"activity_type IN ({placeholders})")
        params.extend(normalized_activities)
    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    conn = sqlite3.connect(db)
    try:
        df = pd.read_sql_query(
            f"""
            SELECT as_of_date, activity_type, balance_ils
            FROM holdings_balance
            {where_sql}
            ORDER BY as_of_date ASC, activity_type ASC
            """,
            conn,
            params=params,
        )
    finally:
        conn.close()
    return df


def normalize_holdings_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize one candidate holdings row from API payload."""
    if not isinstance(row, dict):
        raise ValueError("row must be an object")
    as_of_raw = row.get("as_of_date")
    activity_raw = row.get("activity_type")
    balance_raw = row.get("balance_ils")
    as_of_date = _norm_date(as_of_raw)
    activity_type = _canonical_activity(str(activity_raw or "").strip())
    if not activity_type:
        raise ValueError("activity_type is required")
    bal = pd.to_numeric(balance_raw, errors="coerce")
    if pd.isna(bal):
        raise ValueError("balance_ils must be numeric")
    return {
        "as_of_date": as_of_date,
        "activity_type": activity_type,
        "balance_ils": float(bal),
    }


def normalize_holdings_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Normalize API payload rows and dedupe by (as_of_date, activity_type)."""
    if not isinstance(rows, list) or not rows:
        return pd.DataFrame(columns=["as_of_date", "activity_type", "balance_ils"])
    normalized = [normalize_holdings_row(r) for r in rows]
    df = pd.DataFrame(normalized)
    return df.drop_duplicates(subset=["as_of_date", "activity_type"], keep="last").reset_index(drop=True)


def get_holdings_conflicts(
    rows: list[dict[str, Any]],
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Existing rows that would be overwritten by the given payload rows."""
    db = db_path if db_path is not None else config.ledger_db_file
    migrate_ledger_db(db)
    work = normalize_holdings_rows(rows)
    if work.empty:
        return []

    keys = list(zip(work["as_of_date"], work["activity_type"]))
    placeholders = ",".join("(?,?)" for _ in keys)
    params: list[Any] = []
    for d, a in keys:
        params.extend([d, a])

    conn = sqlite3.connect(db)
    try:
        existing = pd.read_sql_query(
            f"""
            SELECT as_of_date, activity_type, balance_ils
            FROM holdings_balance
            WHERE (as_of_date, activity_type) IN ({placeholders})
            """,
            conn,
            params=params,
        )
    finally:
        conn.close()
    if existing.empty:
        return []
    merged = existing.merge(
        work,
        on=["as_of_date", "activity_type"],
        how="inner",
        suffixes=("_existing", "_incoming"),
    )
    merged = merged[merged["balance_ils_existing"] != merged["balance_ils_incoming"]]
    out: list[dict[str, Any]] = []
    for _, r in merged.iterrows():
        out.append(
            {
                "as_of_date": str(r["as_of_date"]),
                "activity_type": str(r["activity_type"]),
                "existing_balance_ils": float(r["balance_ils_existing"]),
                "incoming_balance_ils": float(r["balance_ils_incoming"]),
            }
        )
    return sorted(out, key=lambda x: (x["as_of_date"], x["activity_type"]))


def upsert_holdings_rows(
    rows: list[dict[str, Any]],
    db_path: str | None = None,
    *,
    overwrite_conflicts: bool = False,
) -> dict[str, Any]:
    """Batch upsert normalized holdings rows with optional conflict guard."""
    db = db_path if db_path is not None else config.ledger_db_file
    work = normalize_holdings_rows(rows)
    conflicts = get_holdings_conflicts(work.to_dict(orient="records"), db)
    if conflicts and not overwrite_conflicts:
        return {
            "ok": False,
            "error": "conflicts_detected",
            "message": "Existing rows would be overwritten; set overwrite_conflicts=true to continue.",
            "conflicts": conflicts,
            "rows_received": int(len(work)),
            "rows_upserted": 0,
        }
    report = upsert_holdings_long(work, db)
    return {
        "ok": True,
        "error": None,
        "message": "holdings rows upserted",
        "conflicts": conflicts,
        "rows_received": int(len(work)),
        "rows_upserted": int(report.get("rows_upserted", 0)),
        "holdings_table_count": int(report.get("holdings_table_count", 0)),
        "db_path": db,
    }


def move_holdings_date(
    source_date: Any,
    target_date: Any,
    db_path: str | None = None,
    *,
    overwrite_conflicts: bool = False,
) -> dict[str, Any]:
    """
    Move all holdings rows from one date to another date.

    This is used by the timeline table editor when changing the snapshot date.
    """
    db = db_path if db_path is not None else config.ledger_db_file
    migrate_ledger_db(db)
    source = _norm_date(source_date)
    target = _month_start_date(target_date)
    if source == target:
        return {
            "ok": True,
            "error": None,
            "message": "source and target dates are the same",
            "source_date": source,
            "target_date": target,
            "rows_moved": 0,
        }

    conn = sqlite3.connect(db)
    try:
        src_rows = conn.execute(
            """
            SELECT as_of_date, activity_type, balance_ils
            FROM holdings_balance
            WHERE as_of_date = ?
            ORDER BY activity_type
            """,
            (source,),
        ).fetchall()
        if not src_rows:
            return {
                "ok": False,
                "error": "source_date_not_found",
                "message": f"no holdings rows exist for source date {source}",
                "source_date": source,
                "target_date": target,
                "rows_moved": 0,
            }

        activities = [str(r[1]) for r in src_rows]
        placeholders = ",".join("?" for _ in activities)
        dst_rows = conn.execute(
            f"""
            SELECT activity_type, balance_ils
            FROM holdings_balance
            WHERE as_of_date = ? AND activity_type IN ({placeholders})
            """,
            [target, *activities],
        ).fetchall()
        dst_by_activity = {str(a): float(b) for a, b in dst_rows}
        conflicts = []
        for _, activity, bal in src_rows:
            a = str(activity)
            src_bal = float(bal)
            if a in dst_by_activity and abs(dst_by_activity[a] - src_bal) > 0.01:
                conflicts.append(
                    {
                        "source_date": source,
                        "target_date": target,
                        "activity_type": a,
                        "source_balance_ils": src_bal,
                        "target_balance_ils": float(dst_by_activity[a]),
                    }
                )
        if conflicts and not overwrite_conflicts:
            return {
                "ok": False,
                "error": "conflicts_detected",
                "message": "Target date has rows with different balances; set overwrite_conflicts=true.",
                "source_date": source,
                "target_date": target,
                "rows_moved": 0,
                "conflicts": conflicts,
            }

        conn.execute("BEGIN")
        conn.execute("DELETE FROM holdings_balance WHERE as_of_date = ?", (source,))
        conn.executemany(
            """
            INSERT OR REPLACE INTO holdings_balance (as_of_date, activity_type, balance_ils)
            VALUES (?,?,?)
            """,
            [(target, str(activity), float(bal)) for _, activity, bal in src_rows],
        )
        conn.commit()
        return {
            "ok": True,
            "error": None,
            "message": "holdings date moved",
            "source_date": source,
            "target_date": target,
            "rows_moved": len(src_rows),
            "conflicts": conflicts,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def parse_holdings_paste_grid(text: str) -> dict[str, Any]:
    """
    Parse tab-separated holdings grid text:
      header: תאריך + activity columns
      rows: one date per line, activity balances in cells.
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in raw.split("\n") if ln.strip()]
    if not lines:
        return {
            "ok": False,
            "error": "empty_input",
            "message": "No data pasted.",
            "rows": [],
            "invalid_cells": [],
            "activity_types": [],
        }
    header = [c.strip() for c in lines[0].split("\t")]
    if len(header) < 2:
        return {
            "ok": False,
            "error": "invalid_header",
            "message": "Header must include date column plus at least one activity column.",
            "rows": [],
            "invalid_cells": [],
            "activity_types": [],
        }
    date_col = header[0]
    if date_col not in ("תאריך", "as_of_date", "date"):
        return {
            "ok": False,
            "error": "invalid_header",
            "message": "First column must be תאריך/date.",
            "rows": [],
            "invalid_cells": [],
            "activity_types": [],
        }

    activities = [_canonical_activity(c) for c in header[1:] if str(c).strip()]
    parsed_rows: list[dict[str, Any]] = []
    invalid_cells: list[dict[str, Any]] = []
    for line_idx, ln in enumerate(lines[1:], start=2):
        cols = [c.strip() for c in ln.split("\t")]
        if not any(cols):
            continue
        try:
            as_of_date = _norm_date(cols[0] if cols else "")
        except Exception:
            invalid_cells.append({"line": line_idx, "column": date_col, "value": cols[0] if cols else "", "error": "invalid_date"})
            continue
        for col_idx, activity in enumerate(activities, start=1):
            val = cols[col_idx] if col_idx < len(cols) else ""
            if val == "":
                continue
            num = pd.to_numeric(val, errors="coerce")
            if pd.isna(num):
                invalid_cells.append(
                    {
                        "line": line_idx,
                        "column": header[col_idx] if col_idx < len(header) else activity,
                        "value": val,
                        "error": "invalid_number",
                    }
                )
                continue
            parsed_rows.append(
                {
                    "as_of_date": as_of_date,
                    "activity_type": activity,
                    "balance_ils": float(num),
                }
            )
    normalized = normalize_holdings_rows(parsed_rows)
    return {
        "ok": True,
        "error": None,
        "message": f"Parsed {len(normalized)} holdings rows.",
        "rows": normalized.to_dict(orient="records"),
        "invalid_cells": invalid_cells,
        "activity_types": sorted({str(x) for x in normalized["activity_type"].tolist()}) if not normalized.empty else [],
        "source_line_count": len(lines),
    }


def holdings_long_to_wide(long_df: pd.DataFrame) -> pd.DataFrame:
    """
    Pivot melted holdings to the wide shape used for Sheets push and wide views.

    Empty table → empty frame (no columns); compiler + new ingest still work.
    """
    if long_df.empty:
        return pd.DataFrame()
    work = long_df.copy()
    work["balance_ils"] = pd.to_numeric(work["balance_ils"], errors="coerce").fillna(0.0)
    pt = work.pivot_table(
        index="as_of_date",
        columns="activity_type",
        values="balance_ils",
        aggfunc="last",
    )
    out = pt.reset_index().rename(columns={"as_of_date": "תאריך"})
    out["תאריך"] = pd.to_datetime(out["תאריך"], errors="coerce").dt.date
    cols = ["תאריך"] + sorted([c for c in out.columns if c != "תאריך"])
    return out[cols]


def upsert_holdings_long(
    long_df: pd.DataFrame,
    db_path: str | None = None,
    *,
    clear_holdings_first: bool = False,
) -> dict[str, Any]:
    """
    Upsert melted rows into ``holdings_balance``.

    Dedupe uses the table primary key ``(as_of_date, activity_type)`` via
    ``INSERT OR REPLACE`` — the native database identity for a holdings row.
    """
    db = db_path if db_path is not None else config.ledger_db_file
    migrate_ledger_db(db)

    combined = long_df.copy()
    if not combined.empty:
        combined["balance_ils"] = pd.to_numeric(combined["balance_ils"], errors="coerce").fillna(0.0)
        combined = combined.drop_duplicates(subset=["as_of_date", "activity_type"], keep="last")

    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if clear_holdings_first:
            conn.execute("DELETE FROM holdings_balance")
            conn.commit()
        rows: list[tuple[Any, ...]] = []
        if not combined.empty:
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

    if combined.empty:
        report = {"parity_ok": True, "expected_logical_rows": 0}
    else:
        report = verify_holdings_long(combined, db)
    report["rows_upserted"] = len(rows)
    report["holdings_table_count"] = int(n)
    report["db_path"] = db
    log.info(
        "holdings upsert: %s logical rows into %s; table rows=%s parity=%s",
        len(rows),
        db,
        n,
        report.get("parity_ok"),
    )
    return report


def upsert_holdings_wide_to_ledger(wide_df: pd.DataFrame, db_path: str | None = None) -> dict[str, Any]:
    """Melt a wide holdings frame (``תאריך`` + activity columns) and upsert into ``holdings_balance``."""
    if wide_df.empty:
        return upsert_holdings_long(pd.DataFrame(columns=["as_of_date", "activity_type", "balance_ils"]), db_path)
    long_df = wide_holdings_to_long(wide_df)
    return upsert_holdings_long(long_df, db_path)


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
            amount_mismatch.append(f"{k} incoming={r['balance_ils']} db={gv}")

    parity_ok = not missing and not amount_mismatch
    return {
        "parity_ok": parity_ok,
        "expected_logical_rows": len(exp),
        "missing_keys": list(missing)[:10],
        "amount_mismatches": amount_mismatch[:10],
    }
