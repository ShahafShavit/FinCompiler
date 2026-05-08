"""JSON API for the /integrity data quality page (read-only reports + safe writes)."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any

import config
from pipeline.ledger import (
    LEDGER_SQL_LT_TX_INCLUDED,
    LEDGER_SQL_TX_INCLUDED,
    forward_fill_uncategorized_for_store_if_static_sql,
    ledger_connect_readonly,
    migrate_ledger_db,
)

log = logging.getLogger(__name__)

_SQL_UNCATEGORIZED = (
    '("קטגוריה" IS NULL OR TRIM(COALESCE("קטגוריה", \'\')) = \'\' '
    'OR LOWER(TRIM(COALESCE("קטגוריה", \'\'))) = \'awaiting\')'
)

_ROW_LIMIT = 200
_GROUP_LIMIT = 50
_RARE_CAT_LIMIT = 80
_ORPHAN_LIMIT = 80

# Installments / statement splits use statement_month; exclude from pseudo-duplicate heuristic.
_NO_STMT_MONTH = "(statement_month IS NULL OR TRIM(COALESCE(statement_month, '')) = '')"
_NO_STMT_MONTH_LT = "(lt.statement_month IS NULL OR TRIM(COALESCE(lt.statement_month, '')) = '')"


def _ledger_path() -> str:
    return config.ledger_db_file


def _section(
    section_id: str,
    title: str,
    *,
    severity: str,
    count: int,
    rows: list[dict[str, Any]],
    note: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": section_id,
        "title": title,
        "severity": severity,
        "count": count,
        "rows": rows,
    }
    if note:
        out["note"] = note
    return out


def _rows_from_conn(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description] if cur.description else []
    out: list[dict[str, Any]] = []
    for tup in cur.fetchall():
        out.append({cols[i]: tup[i] for i in range(len(cols))})
    return out


def build_integrity_report() -> dict[str, Any]:
    path = _ledger_path()
    if not os.path.isfile(path):
        return {"ok": True, "ledger_exists": False, "sections": []}

    migrate_ledger_db(path)
    conn = ledger_connect_readonly(path)
    try:
        sections: list[dict[str, Any]] = []

        excl_n = int(
            conn.execute(
                "SELECT COUNT(*) FROM ledger_transaction WHERE excluded_from_calculations = 1"
            ).fetchone()[0]
            or 0
        )
        excluded_rows = _rows_from_conn(
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
            _section(
                "excluded_transactions",
                "Excluded from calculations (soft-disabled)",
                severity="info",
                count=excl_n,
                rows=excluded_rows,
                note="These rows remain in the ledger but are omitted from heatmap, dashboard totals, "
                "categorize queue, and the checks below.",
            )
        )

        dup_fp = _rows_from_conn(
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
            _section(
                "duplicate_fingerprint",
                "Duplicate fingerprints",
                severity="error",
                count=len(dup_fp),
                rows=dup_fp,
            )
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
        null_fp = _rows_from_conn(
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
            _section(
                "null_fingerprint",
                "Missing fingerprint",
                severity="warning",
                count=null_fp_n,
                rows=null_fp,
            )
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
        pseudo = _rows_from_conn(
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
            _section(
                "pseudo_duplicate_txn",
                "Possible duplicate rows (same day, amounts, source)",
                severity="warning",
                count=pseudo_n,
                rows=pseudo,
                note="Heuristic: multiple distinct fingerprints on identical date, debit/credit, and payee. "
                "Rows with statement_month (installments) are excluded.",
            )
        )

        mirror = _rows_from_conn(
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
            _section(
                "mirror_amount_same_day",
                "Mirror debit/credit same day (same source)",
                severity="info",
                count=len(mirror),
                rows=mirror,
                note="May flag internal transfers or duplicate booking; review context.",
            )
        )

        both = _rows_from_conn(
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
            _section(
                "both_sides_nonzero",
                "Both debit and credit non-zero",
                severity="warning",
                count=len(both),
                rows=both,
            )
        )

        ws_cat = _rows_from_conn(
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
            _section(
                "whitespace_category",
                "Category names with leading/trailing whitespace",
                severity="info",
                count=len(ws_cat),
                rows=ws_cat,
            )
        )

        rare = _rows_from_conn(
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
            _section(
                "rare_categories",
                "Rare categories (≤2 transactions)",
                severity="info",
                count=len(rare),
                rows=rare,
            )
        )

        uncat_row = conn.execute(
            f"""SELECT COUNT(*) FROM ledger_transaction
            WHERE {_SQL_UNCATEGORIZED}
              AND {LEDGER_SQL_TX_INCLUDED}
            """
        ).fetchone()
        uncat_n = int(uncat_row[0] or 0) if uncat_row else 0
        uncat_sample = _rows_from_conn(
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
            _section(
                "uncategorized",
                "Uncategorized / awaiting category",
                severity="warning" if uncat_n else "info",
                count=uncat_n,
                rows=uncat_sample,
            )
        )

        orphans = _rows_from_conn(
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
            _section(
                "store_category_orphans",
                "Ledger categories not present in store mappings",
                severity="info",
                count=len(orphans),
                rows=orphans,
            )
        )

        return {"ok": True, "ledger_exists": True, "sections": sections}
    except Exception:
        log.exception("integrity report failed")
        raise
    finally:
        conn.close()


def list_stores_aggregated() -> dict[str, Any]:
    path = _ledger_path()
    if not os.path.isfile(path):
        return {"ok": True, "ledger_exists": False, "stores": []}
    migrate_ledger_db(path)
    conn = ledger_connect_readonly(path)
    try:
        rows = _rows_from_conn(
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
        stores: list[dict[str, Any]] = []
        for r in rows:
            raw = r.get("categories_joined")
            cats: list[str] = []
            if raw and str(raw).strip():
                cats = [c for c in str(raw).split("%%||%%") if c]
            stores.append(
                {
                    "store_name": r["store_name"],
                    "is_static": int(r["is_static"] or 0),
                    "category_count": int(r["category_count"] or 0),
                    "categories": cats,
                }
            )
        return {"ok": True, "ledger_exists": True, "stores": stores}
    finally:
        conn.close()


def rename_category_api(raw_body: bytes) -> tuple[int, dict[str, Any]]:
    try:
        data = json.loads(raw_body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"ok": False, "error": "invalid_json", "message": "Invalid JSON body"}

    from_cat = str((data or {}).get("from") or "").strip()
    to_cat = str((data or {}).get("to") or "").strip()
    dry_run = bool((data or {}).get("dry_run"))
    if not from_cat or not to_cat:
        return 400, {"ok": False, "error": "validation_error", "message": "from and to must be non-empty"}
    if from_cat == to_cat:
        return 400, {"ok": False, "error": "validation_error", "message": "from and to must differ"}

    path = _ledger_path()
    if not os.path.isfile(path):
        return 404, {"ok": False, "error": "no_ledger", "message": "Ledger database not found"}

    migrate_ledger_db(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        n_led = conn.execute(
            'SELECT COUNT(*) FROM ledger_transaction WHERE "קטגוריה" = ?',
            (from_cat,),
        ).fetchone()[0]
        n_sc = conn.execute(
            "SELECT COUNT(*) FROM store_category WHERE category = ?",
            (from_cat,),
        ).fetchone()[0]
        if dry_run:
            return 200, {
                "ok": True,
                "dry_run": True,
                "would_update": {"ledger_transaction": int(n_led), "store_category": int(n_sc)},
            }
        conn.execute('UPDATE ledger_transaction SET "קטגוריה" = ? WHERE "קטגוריה" = ?', (to_cat, from_cat))
        conn.execute("UPDATE store_category SET category = ? WHERE category = ?", (to_cat, from_cat))
        conn.commit()
        try:
            from web_control import heatmap as _heatmap_mod

            _heatmap_mod.invalidate_bundle_cache()
        except Exception:  # noqa: BLE001
            log.debug("heatmap cache invalidate after rename skipped", exc_info=True)
        return 200, {
            "ok": True,
            "rows_updated": {
                "ledger_transaction": int(n_led),
                "store_category": int(n_sc),
            },
        }
    finally:
        conn.close()


def patch_store_static_api(raw_body: bytes) -> tuple[int, dict[str, Any]]:
    try:
        data = json.loads(raw_body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"ok": False, "error": "invalid_json", "message": "Invalid JSON body"}

    store_name = str((data or {}).get("store_name") or "").strip()
    is_static_raw = (data or {}).get("is_static")
    if not store_name:
        return 400, {"ok": False, "error": "validation_error", "message": "store_name required"}
    try:
        is_static = int(is_static_raw)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "validation_error", "message": "is_static must be 0 or 1"}
    if is_static not in (0, 1):
        return 400, {"ok": False, "error": "validation_error", "message": "is_static must be 0 or 1"}

    path = _ledger_path()
    if not os.path.isfile(path):
        return 404, {"ok": False, "error": "no_ledger", "message": "Ledger database not found"}

    migrate_ledger_db(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        row = conn.execute("SELECT 1 FROM store WHERE store_name = ?", (store_name,)).fetchone()
        if row is None:
            return 404, {"ok": False, "error": "not_found", "message": f"No store {store_name!r}"}

        if is_static == 1:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM store_category WHERE store_name = ?",
                (store_name,),
            ).fetchone()[0]
            if int(cnt or 0) > 1:
                return 409, {
                    "ok": False,
                    "error": "multiple_categories_for_static",
                    "message": "Store has multiple categories; merge in store_category before setting static.",
                }

        conn.execute("UPDATE store SET is_static = ? WHERE store_name = ?", (is_static, store_name))
        conn.commit()
    except sqlite3.OperationalError as e:
        if "cannot set is_static" in str(e).lower():
            return 409, {
                "ok": False,
                "error": "multiple_categories_for_static",
                "message": str(e),
            }
        raise
    finally:
        conn.close()

    forward_filled = 0
    if is_static == 1:
        try:
            forward_filled = forward_fill_uncategorized_for_store_if_static_sql(path, store_name)
        except Exception:  # noqa: BLE001
            log.exception("forward_fill after static toggle for %s", store_name)
    try:
        from web_control import heatmap as _heatmap_mod

        _heatmap_mod.invalidate_bundle_cache()
    except Exception:  # noqa: BLE001
        log.debug("heatmap cache invalidate after store-static skipped", exc_info=True)

    return 200, {"ok": True, "updated": True, "forward_filled_uncategorized": int(forward_filled)}


def patch_ledger_transaction_api(raw_body: bytes) -> tuple[int, dict[str, Any]]:
    """Ledger row patch used from Data Integrity (same patch rules as ``/heatmap/api/transaction``).

    Body may include::

        ``id`` — sqlite ``ledger_transaction.id`` **or**

        ``fingerprint`` — unique dedupe key; ``id`` is resolved server-side.

    One of ``id`` or ``fingerprint`` must be present (``id`` wins if both are set).
    """
    from web_control import heatmap

    try:
        data = json.loads(raw_body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"ok": False, "error": "invalid_json", "message": "Invalid JSON body."}

    if not isinstance(data, dict):
        return 400, {"ok": False, "error": "validation_error", "message": "Body must be a JSON object."}

    patch = data.get("patch")
    if not isinstance(patch, dict):
        return 400, {"ok": False, "error": "validation_error", "message": "patch object required."}

    row_id_raw = data.get("id")
    fp = str(data.get("fingerprint") or "").strip()

    resolved_id: int | None = None
    if row_id_raw is not None and str(row_id_raw).strip() != "":
        try:
            resolved_id = int(row_id_raw)
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "validation_error", "message": "id must be an integer."}
    elif fp:
        path = _ledger_path()
        if not os.path.isfile(path):
            return 404, {"ok": False, "error": "no_ledger", "message": "Ledger database not found"}
        migrate_ledger_db(path)
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            row = conn.execute(
                """
                SELECT id FROM ledger_transaction
                WHERE fingerprint IS NOT NULL AND TRIM(fingerprint) = ?
                """,
                (fp,),
            ).fetchone()
            if row is None:
                return 404, {
                    "ok": False,
                    "error": "not_found",
                    "message": "No ledger row with this fingerprint.",
                }
            resolved_id = int(row[0])
        finally:
            conn.close()
    else:
        return 400, {
            "ok": False,
            "error": "validation_error",
            "message": "Provide id or fingerprint.",
        }

    forward: dict[str, Any] = {
        "id": resolved_id,
        "patch": patch,
    }
    if data.get("confirm_fingerprint_change"):
        forward["confirm_fingerprint_change"] = True
    phrase = data.get("confirm_fingerprint_phrase")
    if phrase is not None:
        forward["confirm_fingerprint_phrase"] = phrase

    status, payload = heatmap.ledger_transaction_patch_api(json.dumps(forward).encode("utf-8"))
    if isinstance(payload, dict) and payload.get("ok"):
        payload = {**payload, "id": resolved_id}
    return status, payload