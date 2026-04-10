"""
Load and export transaction rows from ``ledger_transaction`` (SQLite-first pipeline).

Replaces the old ``compiled.csv`` round-trip for the transactions compile/categorize path.

Ledger rows do not include legacy CSV-only columns ``מזהה עסקה`` or ``תאריך עדכון``; identity and
ingestion timing are ``fingerprint`` and ``ingested_at`` respectively (see ``schema/ledger/README.md``).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

import pandas as pd

from pipeline.ledger_migrate import migrate_ledger_db

log = logging.getLogger(__name__)

# DB columns only (no ``מזהה עסקה`` / ``תאריך עדכון`` — dedupe and ingestion use ``fingerprint`` + ``ingested_at``).
_LEDGER_TX_READ_SQL = """
SELECT
    "תאריך",
    "בחובה",
    "בזכות",
    "מקור עסקה",
    "פירוט נוסף",
    "תאור מורחב",
    "4 ספרות",
    "fingerprint",
    "קטגוריה",
    notes,
    statement_month,
    ingested_at
FROM ledger_transaction
ORDER BY "תאריך", id
"""


def load_transactions_dataframe_from_ledger(db_path: str) -> pd.DataFrame:
    """Return all ledger rows as a DataFrame (empty table → empty frame with expected columns)."""
    migrate_ledger_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(_LEDGER_TX_READ_SQL, conn)
    finally:
        conn.close()

    if not df.empty:
        if "קטגוריה" in df.columns:
            df["קטגוריה"] = df["קטגוריה"].map(lambda x: "" if pd.isna(x) else str(x)).astype(object)
        if "fingerprint" in df.columns:
            df["fingerprint"] = df["fingerprint"].map(lambda x: "" if pd.isna(x) else str(x)).astype(object)
    return df


def export_transactions_dataframe_to_csv(db_path: str, dest_path: str) -> str:
    """Materialize the ledger to a CSV (e.g. Google Sheets push). Creates parent dirs."""
    df = load_transactions_dataframe_from_ledger(db_path)
    parent = os.path.dirname(os.path.abspath(dest_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    out = df.drop(columns=["ingested_at", "statement_month"], errors="ignore")
    out.to_csv(dest_path, index=False)
    log.info("Exported %s ledger rows -> %s", len(out), dest_path)
    return dest_path


def load_stores_dataframe_from_ledger(db_path: str) -> pd.DataFrame:
    """``store`` + ``store_category`` as a stores_to_categories-shaped DataFrame."""
    migrate_ledger_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT s.store_name, sc.category, s.is_static
            FROM store_category sc
            JOIN store s ON s.store_name = sc.store_name
            ORDER BY s.store_name, sc.category
            """,
            conn,
        )
    finally:
        conn.close()
    if df.empty:
        return pd.DataFrame(columns=["store_name", "category", "is_static"])
    return df


def load_known_transactions_backup_from_ledger(db_path: str) -> pd.DataFrame | None:
    """Fingerprint → category hints for ``auto_categorize`` (replaces legacy CSV backup)."""
    migrate_ledger_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT "fingerprint" AS transaction_id, "קטגוריה" AS category
            FROM ledger_transaction
            WHERE "fingerprint" IS NOT NULL AND TRIM("fingerprint") != ''
            """,
            conn,
        )
    finally:
        conn.close()
    if df.empty:
        return None
    df = df.dropna(subset=["transaction_id"])
    df["transaction_id"] = df["transaction_id"].astype(str)
    df["category"] = df["category"].map(lambda x: "" if pd.isna(x) else str(x))
    df.drop_duplicates(subset=["transaction_id"], inplace=True, keep="first")
    return df
