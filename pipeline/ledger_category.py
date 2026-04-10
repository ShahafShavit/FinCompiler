"""Category updates on ``ledger_transaction`` (replaces ``fingerprint_db.csv``)."""

from __future__ import annotations

import sqlite3

from pipeline.ledger_migrate import migrate_ledger_db


def update_category_by_fingerprint(db_path: str, fingerprint: str, category: str | None) -> None:
    """Set ``קטגוריה`` for a row; trigger fills ``category_updated_at`` when the value changes."""
    if not fingerprint or not str(fingerprint).strip():
        return
    migrate_ledger_db(db_path)
    cat_str = "" if category is None else str(category)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            'UPDATE ledger_transaction SET "קטגוריה" = ? WHERE "fingerprint" = ?',
            (cat_str, str(fingerprint).strip()),
        )
        conn.commit()
    finally:
        conn.close()
