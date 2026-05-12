"""QoL-only grouping of store categories under user-defined top labels (ledger tables ``top_category_column``, ``top_categories``)."""

from __future__ import annotations

import sqlite3
from typing import Any

# Applied by migrate_ledger_db v16 and full_schema.sql (fresh DBs).
TOP_CATEGORIES_V16_DDL = """
CREATE TABLE IF NOT EXISTS top_category_column (
    top_name    TEXT NOT NULL PRIMARY KEY,
    sort_order  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS top_categories (
    top_name      TEXT NOT NULL,
    sub_category  TEXT NOT NULL,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (top_name, sub_category),
    FOREIGN KEY (top_name) REFERENCES top_category_column(top_name) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_top_categories_sub ON top_categories (sub_category);
"""


def _distinct_store_categories(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute(
        "SELECT DISTINCT TRIM(category) AS c FROM store_category "
        "WHERE category IS NOT NULL AND TRIM(category) != ''"
    )
    return {str(r[0]) for r in cur.fetchall() if r and r[0]}


def build_top_categories_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Return layout for integrity UI and categorize queue.

    Shape: ``{ ok, columns: [{ top_name, sub_categories }], unassigned: string[] }``.
    """
    pool = sorted(_distinct_store_categories(conn))
    col_rows = conn.execute(
        "SELECT top_name FROM top_category_column ORDER BY sort_order ASC, top_name ASC"
    ).fetchall()
    columns_out: list[dict[str, Any]] = []
    assigned: set[str] = set()
    for (top_name,) in col_rows:
        t = str(top_name)
        subs = [
            str(r[0])
            for r in conn.execute(
                "SELECT sub_category FROM top_categories WHERE top_name = ? ORDER BY sort_order ASC, sub_category ASC",
                (t,),
            ).fetchall()
        ]
        assigned.update(subs)
        columns_out.append({"top_name": t, "sub_categories": subs})
    unassigned = [c for c in pool if c not in assigned]
    return {"ok": True, "columns": columns_out, "unassigned": unassigned}


def replace_top_categories_layout(
    conn: sqlite3.Connection, columns: list[dict[str, Any]]
) -> tuple[bool, str | None]:
    """
    Replace all column headers and memberships.

    ``columns`` is a list of ``{"top_name": str, "sub_categories": [str, ...]}``.
    Every ``sub_category`` must exist in ``store_category``; no duplicates across columns.
    """
    conn.execute("PRAGMA foreign_keys = ON")
    pool = _distinct_store_categories(conn)
    seen_subs: set[str] = set()
    normalized: list[tuple[str, list[str]]] = []
    for i, col in enumerate(columns):
        raw_name = str((col or {}).get("top_name") or "").strip()
        if not raw_name:
            return False, "top_name must be non-empty for each column"
        subs_raw = (col or {}).get("sub_categories")
        if not isinstance(subs_raw, list):
            return False, "sub_categories must be a list for each column"
        subs = [str(s).strip() for s in subs_raw if str(s).strip()]
        if len(set(subs)) != len(subs):
            return False, "duplicate sub_category within a column"
        for s in subs:
            if s not in pool:
                return False, f"unknown sub_category not in store_category: {s!r}"
            if s in seen_subs:
                return False, f"sub_category appears in more than one column: {s!r}"
            seen_subs.add(s)
        normalized.append((raw_name, subs))

    top_names = [t for t, _ in normalized]
    if len(set(top_names)) != len(top_names):
        return False, "duplicate top_name in column list"

    conn.execute("DELETE FROM top_categories")
    conn.execute("DELETE FROM top_category_column")
    for order, (top_name, subs) in enumerate(normalized):
        conn.execute(
            "INSERT INTO top_category_column (top_name, sort_order) VALUES (?, ?)",
            (top_name, order),
        )
        for j, sub in enumerate(subs):
            conn.execute(
                "INSERT INTO top_categories (top_name, sub_category, sort_order) VALUES (?, ?, ?)",
                (top_name, sub, j),
            )
    return True, None


def parse_top_categories_put_body(data: Any) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Parse JSON-decoded body; returns (columns, error_message)."""
    if not isinstance(data, dict):
        return None, "body must be a JSON object"
    cols = data.get("columns")
    if not isinstance(cols, list):
        return None, "columns must be a list"
    out: list[dict[str, Any]] = []
    for c in cols:
        if not isinstance(c, dict):
            return None, "each column must be an object"
        out.append(
            {
                "top_name": c.get("top_name"),
                "sub_categories": c.get("sub_categories"),
            }
        )
    return out, None
