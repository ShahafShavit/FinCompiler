"""
Hand-rolled SQLite migrations for the ledger database (MIG-C2).

Baseline applies ``schema/ledger/full_schema.sql`` when the DB is empty (no migrations).

**v8** — nullable ``fingerprint``, ``ingested_at``; no ``מזהה עסקה`` / ``תאריך עדכון`` columns.
Older DBs (v7 or below) require deleting ``ledger.sqlite`` and re-importing (see ``schema/ledger/README.md``).

If a DB was briefly migrated to optional v9 columns, those columns are dropped on open (SQLite 3.35+).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_BASELINE_TARGET_VERSION = 8


def _full_schema_path() -> Path:
    """Resolve ``schema/ledger/full_schema.sql`` from the repository root."""
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / "schema" / "ledger" / "full_schema.sql"


def _current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if row is None:
        return 0
    v = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()
    return int(v[0]) if v is not None else 0


def _ledger_is_pre_v8_legacy_shape(conn: sqlite3.Connection) -> bool:
    """True when ``ledger_transaction`` matches the pre-nullable-fingerprint / v7 layout."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ledger_transaction'"
    ).fetchone()
    if row is None:
        return False
    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(ledger_transaction)")}
    names = set(cols.keys())
    if "first_seen_at" in names:
        return True
    fp = cols.get("fingerprint")
    if fp is not None and int(fp[3]) == 1:
        return True
    return False


def _drop_abandoned_v9_columns_if_present(conn: sqlite3.Connection) -> None:
    """Remove optional columns from an experimental v9 migration (not part of the v8 contract)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ledger_transaction)")}
    for name in ("מזהה עסקה", "תאריך עדכון"):
        if name not in cols:
            continue
        try:
            conn.execute(f'ALTER TABLE ledger_transaction DROP COLUMN "{name}"')
        except sqlite3.OperationalError:
            # Older SQLite without DROP COLUMN — leave columns in place
            pass


def _ensure_data_updated_trigger_v8(conn: sqlite3.Connection) -> None:
    """Match ``full_schema.sql`` (no מזהה / תאריך עדכון in WHEN clause)."""
    conn.execute("DROP TRIGGER IF EXISTS tr_ledger_transaction_touch_data_updated_at")
    conn.execute(
        """
CREATE TRIGGER tr_ledger_transaction_touch_data_updated_at
AFTER UPDATE ON ledger_transaction
FOR EACH ROW
WHEN (
       NEW."תאריך"       IS DISTINCT FROM OLD."תאריך"
    OR NEW."בחובה"       IS DISTINCT FROM OLD."בחובה"
    OR NEW."בזכות"       IS DISTINCT FROM OLD."בזכות"
    OR NEW."מקור עסקה"   IS DISTINCT FROM OLD."מקור עסקה"
    OR NEW."פירוט נוסף"  IS DISTINCT FROM OLD."פירוט נוסף"
    OR NEW."תאור מורחב"  IS DISTINCT FROM OLD."תאור מורחב"
    OR NEW."4 ספרות"     IS DISTINCT FROM OLD."4 ספרות"
    OR NEW.statement_month IS DISTINCT FROM OLD.statement_month
    OR NEW.ingested_at IS DISTINCT FROM OLD.ingested_at
    OR NEW.notes IS DISTINCT FROM OLD.notes
)
AND NEW.data_updated_at IS NOT DISTINCT FROM OLD.data_updated_at
BEGIN
    UPDATE ledger_transaction
    SET data_updated_at = datetime('now', 'localtime')
    WHERE id = OLD.id;
END;
"""
    )


def migrate_ledger_db(db_path: str | None = None) -> None:
    """
    Ensure the ledger database exists and matches the v8+ contract in ``full_schema.sql``.

    Idempotent: safe to call repeatedly. Uses ``config.ledger_db_file`` when
    ``db_path`` is omitted (read at call time so tests can reload ``config``).
    """
    import config as config_mod

    path = db_path if db_path is not None else config_mod.ledger_db_file
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    sql_file = _full_schema_path()
    if not sql_file.is_file():
        raise FileNotFoundError(f"Ledger schema SQL not found: {sql_file}")

    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        current = _current_schema_version(conn)

        if _ledger_is_pre_v8_legacy_shape(conn) or (0 < current < 8):
            raise RuntimeError(
                "Ledger database is older than schema v8 (nullable fingerprint / legacy columns). "
                f"Delete the file and recreate: {os.path.abspath(path)} — then re-run imports."
            )

        if current == 0:
            ddl = sql_file.read_text(encoding="utf-8")
            conn.executescript(ddl)
            conn.commit()
            return

        # Existing v8+ file: align with current DDL (drop abandoned columns, refresh trigger).
        _drop_abandoned_v9_columns_if_present(conn)
        _ensure_data_updated_trigger_v8(conn)
        conn.commit()
    finally:
        conn.close()
