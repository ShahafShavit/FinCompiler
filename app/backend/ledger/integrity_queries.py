"""SQL and row materialization for data-integrity reports (no HTTP)."""

from __future__ import annotations

import sqlite3
from typing import Any

from ledger.store import LEDGER_SQL_LT_TX_INCLUDED, LEDGER_SQL_TX_INCLUDED

_SQL_UNCATEGORIZED = (
    '("קטגוריה" IS NULL OR TRIM(COALESCE("קטגוריה", \'\')) = \'\' '
    'OR LOWER(TRIM(COALESCE("קטגוריה", \'\'))) = \'awaiting\')'
)

_ROW_LIMIT = 200
_GROUP_LIMIT = 50
_RARE_CAT_LIMIT = 80
_ORPHAN_LIMIT = 80

_NO_STMT_MONTH = "(statement_month IS NULL OR TRIM(COALESCE(statement_month, '')) = '')"
_NO_STMT_MONTH_LT = "(lt.statement_month IS NULL OR TRIM(COALESCE(lt.statement_month, '')) = '')"


def rows_from_conn(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description] if cur.description else []
    out: list[dict[str, Any]] = []
    for tup in cur.fetchall():
        out.append({cols[i]: tup[i] for i in range(len(cols))})
    return out


def collect_integrity_report_sections(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Build section dicts (same shape as ``api.integrity._section`` output)."""
    sections: list[dict[str, Any]] = []

    excl_n = int(
        conn.execute(
            "SELECT COUNT(*) FROM ledger_transaction WHERE excluded_from_calculations = 1"
        ).fetchone()[0]
        or 0
    )
    excluded_rows = rows_from_conn(
        conn,
        f"""
            SELECT id, "תאריך", "מקור עסקה", "בחובה", "בזכות", "קטגוריה", fingerprint
            FROM ledger_transaction
            WHERE excluded_from_calculations = 1
            ORDER BY id DESC
            LIMIT {_ROW_LIMIT}
            """,
    )
    sections.append(
        {
            "id": "excluded_transactions",
            "title": "Excluded from calculations (soft-disabled)",
            "severity": "info",
            "count": excl_n,
            "rows": excluded_rows,
            "note": "These rows remain in the ledger but are omitted from heatmap, dashboard totals, "
            "categorize queue, and the checks below.",
        }
    )

    dup_fp = rows_from_conn(
        conn,
        f"""
            SELECT fingerprint, COUNT(*) AS c
            FROM ledger_transaction
            WHERE fingerprint IS NOT NULL AND TRIM(fingerprint) != ''
              AND {LEDGER_SQL_TX_INCLUDED}
            GROUP BY fingerprint
            HAVING c > 1
            LIMIT 20
            """,
    )
    sections.append(
        {
            "id": "duplicate_fingerprint",
            "title": "Duplicate fingerprints",
            "severity": "error",
            "count": len(dup_fp),
            "rows": dup_fp,
        }
    )

    null_fp_n = int(
        conn.execute(
            f"""
                SELECT COUNT(*) FROM ledger_transaction
                WHERE (fingerprint IS NULL OR TRIM(COALESCE(fingerprint, '')) = '')
                  AND {LEDGER_SQL_TX_INCLUDED}
                """
        ).fetchone()[0]
        or 0
    )
    null_fp = rows_from_conn(
        conn,
        f"""
            SELECT id, "תאריך", "מקור עסקה", "בחובה", "בזכות", "קטגוריה"
            FROM ledger_transaction
            WHERE (fingerprint IS NULL OR TRIM(COALESCE(fingerprint, '')) = '')
              AND {LEDGER_SQL_TX_INCLUDED}
            LIMIT {_ROW_LIMIT}
            """,
    )
    sections.append(
        {
            "id": "null_fingerprint",
            "title": "Missing fingerprint",
            "severity": "warning",
            "count": null_fp_n,
            "rows": null_fp,
        }
    )

    pseudo_n = int(
        conn.execute(
            f"""
                SELECT COUNT(*) FROM (
                  SELECT date("תאריך") AS d, "בחובה" AS bh, "בזכות" AS bz,
                         TRIM(COALESCE("מקור עסקה", '')) AS src
                  FROM ledger_transaction
                  WHERE fingerprint IS NOT NULL AND TRIM(fingerprint) != ''
                    AND {_NO_STMT_MONTH}
                    AND {LEDGER_SQL_TX_INCLUDED}
                  GROUP BY 1, 2, 3, 4
                  HAVING COUNT(DISTINCT fingerprint) > 1
                )
                """
        ).fetchone()[0]
        or 0
    )
    pseudo = rows_from_conn(
        conn,
        f"""
            WITH dups AS (
              SELECT date("תאריך") AS d, "בחובה" AS bh, "בזכות" AS bz,
                     TRIM(COALESCE("מקור עסקה", '')) AS src,
                     COUNT(DISTINCT fingerprint) AS nf
              FROM ledger_transaction
              WHERE fingerprint IS NOT NULL AND TRIM(fingerprint) != ''
                AND {_NO_STMT_MONTH}
                AND {LEDGER_SQL_TX_INCLUDED}
              GROUP BY 1, 2, 3, 4
              HAVING nf > 1
              LIMIT {_GROUP_LIMIT}
            )
            SELECT lt.id, lt."תאריך", lt."בחובה", lt."בזכות", lt."מקור עסקה", lt."קטגוריה", lt.fingerprint
            FROM ledger_transaction lt
            INNER JOIN dups ON date(lt."תאריך") = dups.d
              AND lt."בחובה" = dups.bh AND lt."בזכות" = dups.bz
              AND TRIM(COALESCE(lt."מקור עסקה", '')) = dups.src
            WHERE {_NO_STMT_MONTH_LT}
              AND {LEDGER_SQL_LT_TX_INCLUDED}
            ORDER BY lt."תאריך", lt.id
            LIMIT {_ROW_LIMIT}
            """,
    )
    sections.append(
        {
            "id": "pseudo_duplicate_txn",
            "title": "Possible duplicate rows (same day, amounts, source)",
            "severity": "warning",
            "count": pseudo_n,
            "rows": pseudo,
            "note": "Heuristic: multiple distinct fingerprints on identical date, debit/credit, and payee. "
            "Rows with statement_month (installments) are excluded.",
        }
    )

    mirror = rows_from_conn(
        conn,
        f"""
            SELECT a.id AS id_a, b.id AS id_b, a."תאריך", a."בחובה" AS a_debit, a."בזכות" AS a_credit,
                   b."בחובה" AS b_debit, b."בזכות" AS b_credit,
                   a."מקור עסקה" AS source
            FROM ledger_transaction a
            INNER JOIN ledger_transaction b
              ON a."תאריך" = b."תאריך"
             AND a.id < b.id
             AND a."בחובה" = b."בזכות"
             AND a."בזכות" = b."בחובה"
             AND TRIM(COALESCE(a."מקור עסקה", '')) = TRIM(COALESCE(b."מקור עסקה", ''))
            WHERE TRIM(COALESCE(a."מקור עסקה", '')) != ''
              AND (COALESCE(a.excluded_from_calculations, 0) = 0)
              AND (COALESCE(b.excluded_from_calculations, 0) = 0)
            LIMIT {_ROW_LIMIT}
            """,
    )
    sections.append(
        {
            "id": "mirror_amount_same_day",
            "title": "Mirror debit/credit same day (same source)",
            "severity": "info",
            "count": len(mirror),
            "rows": mirror,
            "note": "May flag internal transfers or duplicate booking; review context.",
        }
    )

    both = rows_from_conn(
        conn,
        f"""
            SELECT id, "תאריך", "בחובה", "בזכות", "מקור עסקה", "קטגוריה"
            FROM ledger_transaction
            WHERE "בחובה" != 0 AND "בזכות" != 0
              AND {LEDGER_SQL_TX_INCLUDED}
            LIMIT {_ROW_LIMIT}
            """,
    )
    sections.append(
        {
            "id": "both_sides_nonzero",
            "title": "Both debit and credit non-zero",
            "severity": "warning",
            "count": len(both),
            "rows": both,
        }
    )

    ws_cat = rows_from_conn(
        conn,
        f"""
            SELECT DISTINCT TRIM("קטגוריה") AS trimmed, "קטגוריה" AS raw
            FROM ledger_transaction
            WHERE "קטגוריה" IS NOT NULL
              AND "קטגוריה" != TRIM("קטגוריה")
              AND {LEDGER_SQL_TX_INCLUDED}
            LIMIT 100
            """,
    )
    sections.append(
        {
            "id": "whitespace_category",
            "title": "Category names with leading/trailing whitespace",
            "severity": "info",
            "count": len(ws_cat),
            "rows": ws_cat,
        }
    )

    rare = rows_from_conn(
        conn,
        f"""
            SELECT "קטגוריה" AS category, COUNT(*) AS txn_count
            FROM ledger_transaction
            WHERE "קטגוריה" IS NOT NULL AND TRIM(COALESCE("קטגוריה", '')) != ''
              AND {LEDGER_SQL_TX_INCLUDED}
            GROUP BY "קטגוריה"
            HAVING txn_count <= 2
            ORDER BY txn_count, "קטגוריה"
            LIMIT {_RARE_CAT_LIMIT}
            """,
    )
    sections.append(
        {
            "id": "rare_categories",
            "title": "Rare categories (≤2 transactions)",
            "severity": "info",
            "count": len(rare),
            "rows": rare,
        }
    )

    uncat_row = conn.execute(
        f"""SELECT COUNT(*) FROM ledger_transaction
            WHERE {_SQL_UNCATEGORIZED}
              AND {LEDGER_SQL_TX_INCLUDED}
            """
    ).fetchone()
    uncat_n = int(uncat_row[0] or 0) if uncat_row else 0
    uncat_sample = rows_from_conn(
        conn,
        f"""
            SELECT id, "תאריך", "מקור עסקה", "בחובה", "בזכות", "קטגוריה", fingerprint
            FROM ledger_transaction
            WHERE {_SQL_UNCATEGORIZED}
              AND {LEDGER_SQL_TX_INCLUDED}
            LIMIT 80
            """,
    )
    sections.append(
        {
            "id": "uncategorized",
            "title": "Uncategorized / awaiting category",
            "severity": "warning" if uncat_n else "info",
            "count": uncat_n,
            "rows": uncat_sample,
        }
    )

    orphans = rows_from_conn(
        conn,
        f"""
            SELECT DISTINCT lt."קטגוריה" AS category, COUNT(*) AS txn_count
            FROM ledger_transaction lt
            WHERE lt."קטגוריה" IS NOT NULL AND TRIM(COALESCE(lt."קטגוריה", '')) != ''
              AND {LEDGER_SQL_LT_TX_INCLUDED}
              AND NOT EXISTS (
                SELECT 1 FROM store_category sc WHERE sc.category = lt."קטגוריה"
              )
            GROUP BY lt."קטגוריה"
            ORDER BY txn_count DESC
            LIMIT {_ORPHAN_LIMIT}
            """,
    )
    sections.append(
        {
            "id": "store_category_orphans",
            "title": "Ledger categories not present in store mappings",
            "severity": "info",
            "count": len(orphans),
            "rows": orphans,
        }
    )

    return sections


def stores_aggregated_raw_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows_from_conn(
        conn,
        """
            SELECT s.store_name, s.is_static,
                   COUNT(sc.category) AS category_count,
                   GROUP_CONCAT(sc.category, '%%||%%' ORDER BY sc.category) AS categories_joined
            FROM store s
            LEFT JOIN store_category sc ON sc.store_name = s.store_name
            GROUP BY s.store_name, s.is_static
            ORDER BY s.store_name
            """,
    )
