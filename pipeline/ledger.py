"""Ledger SQLite operations: migrations, audit, dataframe I/O, category updates, fingerprint backfill,
web totals CSV load, static store mappings, compile upsert.

Migrations (MIG-C2): baseline applies ``schema/ledger/full_schema.sql`` when the DB is empty.

**v8** — nullable ``fingerprint``, ``ingested_at``; no ``מזהה עסקה`` / ``תאריך עדכון`` columns.
Older DBs (v7 or below) require deleting ``ledger.sqlite`` and re-importing (see ``schema/ledger/README.md``).

**v9** — transitional: ``fingerprint_v2`` (``UNIQUE``) plus legacy ``fingerprint`` (superseded by v10).

**v10** — single ``fingerprint`` column (same values as former ``fingerprint_v2``): drop legacy ``fingerprint``,
rename ``fingerprint_v2`` → ``fingerprint``. Requires SQLite 3.35+ (``ALTER TABLE DROP COLUMN``).

Constraint audit mirrors ``full_schema.sql`` CHECK/NOT NULL/FK rules. Compile upsert implements MIG-E2.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

import config

from pipeline.csv_handler import generate_transaction_fingerprint
from pipeline.ingested_at_rules import compute_ingested_at_iso

log = logging.getLogger(__name__)

# --- Constraint audit (read-time; ex-ledger_constraint_audit) ---


@dataclass
class ConstraintViolation:
    """One named rule with violating row count (and optional sample ids)."""

    table: str
    rule_id: str
    description: str
    count: int
    sample_detail: str = ""


@dataclass
class LedgerAuditReport:
    integrity_check: str
    foreign_key_violation_count: int
    violations: list[ConstraintViolation] = field(default_factory=list)
    expected_triggers_present: bool = True
    missing_triggers: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return (
            self.integrity_check == "ok"
            and self.foreign_key_violation_count == 0
            and all(v.count == 0 for v in self.violations)
            and self.expected_triggers_present
        )


def _count(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0])


def audit_ledger_constraints(conn: sqlite3.Connection) -> LedgerAuditReport:
    """
    Run structural PRAGMAs plus one violation-count query per logical constraint.

    ``conn`` should use ``PRAGMA foreign_keys = ON`` if you rely on FK semantics (audit runs
    ``PRAGMA foreign_key_check`` which is independent).
    """
    report = LedgerAuditReport(
        integrity_check=conn.execute("PRAGMA integrity_check").fetchone()[0],
        foreign_key_violation_count=len(list(conn.execute("PRAGMA foreign_key_check"))),
    )

    if report.integrity_check != "ok":
        return report

    expected = (
        "tr_store_category_before_insert_static_limit",
        "tr_store_before_update_static_requires_single_category",
        "tr_ledger_transaction_touch_category_updated_at",
        "tr_ledger_transaction_touch_data_updated_at",
    )
    have = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name IN ({})".format(
                ",".join("?" * len(expected))
            ),
            expected,
        )
    }
    missing = tuple(x for x in expected if x not in have)
    report.expected_triggers_present = len(missing) == 0
    report.missing_triggers = missing

    checks: list[tuple[str, str, str, str]] = [
        # ledger_transaction — mirror CREATE TABLE checks in full_schema.sql
        (
            "ledger_transaction",
            "lt_taarich_iso_date",
            'תאריך must be NULL or ISO date where date(x)=x',
            """SELECT id FROM ledger_transaction
               WHERE NOT ("תאריך" IS NULL OR date("תאריך") = "תאריך")""",
        ),
        (
            "ledger_transaction",
            "lt_bחובה_not_null",
            "בחובה NOT NULL",
            """SELECT id FROM ledger_transaction WHERE "בחובה" IS NULL""",
        ),
        (
            "ledger_transaction",
            "lt_bזכות_not_null",
            "בזכות NOT NULL",
            """SELECT id FROM ledger_transaction WHERE "בזכות" IS NULL""",
        ),
        (
            "ledger_transaction",
            "lt_ingested_at_date_only",
            "ingested_at NOT NULL and date(ingested_at)=ingested_at",
            """SELECT id FROM ledger_transaction
               WHERE ingested_at IS NULL OR date(ingested_at) != ingested_at""",
        ),
        (
            "ledger_transaction",
            "lt_category_updated_at_datetime",
            "category_updated_at NULL or parseable by datetime()",
            """SELECT id FROM ledger_transaction
               WHERE category_updated_at IS NOT NULL
                 AND datetime(category_updated_at) IS NULL""",
        ),
        (
            "ledger_transaction",
            "lt_data_updated_at_datetime",
            "data_updated_at NULL or parseable by datetime()",
            """SELECT id FROM ledger_transaction
               WHERE data_updated_at IS NOT NULL
                 AND datetime(data_updated_at) IS NULL""",
        ),
        (
            "ledger_transaction",
            "lt_statement_month_shape",
            "statement_month NULL or YYYY-MM with valid first day",
            """SELECT id FROM ledger_transaction
               WHERE NOT (
                 statement_month IS NULL
                 OR (
                   length(statement_month) = 7
                   AND date(statement_month || '-01') IS NOT NULL
                   AND strftime('%Y-%m', statement_month || '-01') = statement_month
                 )
               )""",
        ),
        (
            "ledger_transaction",
            "lt_fingerprint_trim",
            "fingerprint NULL or non-empty after trim",
            """SELECT id FROM ledger_transaction
               WHERE NOT (fingerprint IS NULL OR length(trim(fingerprint)) > 0)""",
        ),
        (
            "ledger_transaction",
            "lt_fingerprint_unique",
            "UNIQUE(fingerprint) for non-NULL values (duplicate fingerprint values)",
            """SELECT fingerprint FROM ledger_transaction
               WHERE fingerprint IS NOT NULL
               GROUP BY fingerprint
               HAVING COUNT(*) > 1""",
        ),
        (
            "store_category",
            "sc_store_name_not_null",
            "store_name NOT NULL",
            """SELECT store_name FROM store_category WHERE store_name IS NULL""",
        ),
        (
            "store_category",
            "sc_category_not_null",
            "category NOT NULL",
            """SELECT store_name FROM store_category WHERE category IS NULL""",
        ),
        # store
        (
            "store",
            "store_is_static_boolean",
            "is_static IN (0, 1)",
            """SELECT store_name FROM store WHERE NOT (is_static IN (0, 1))""",
        ),
        (
            "store",
            "store_name_not_null",
            "store_name NOT NULL (PK)",
            """SELECT store_name FROM store WHERE store_name IS NULL""",
        ),
        (
            "store",
            "store_is_static_not_null",
            "is_static NOT NULL",
            """SELECT store_name FROM store WHERE is_static IS NULL""",
        ),
        # similar_category_pair
        (
            "similar_category_pair",
            "scp_p_neq_p2",
            "p1 != p2",
            """SELECT p1 FROM similar_category_pair WHERE p1 = p2""",
        ),
        (
            "similar_category_pair",
            "scp_p1_not_null",
            "p1 NOT NULL",
            """SELECT p1 FROM similar_category_pair WHERE p1 IS NULL""",
        ),
        (
            "similar_category_pair",
            "scp_p2_not_null",
            "p2 NOT NULL",
            """SELECT p1 FROM similar_category_pair WHERE p2 IS NULL""",
        ),
        # holdings_balance
        (
            "holdings_balance",
            "hb_as_of_date",
            "as_of_date ISO date where date(x)=x",
            """SELECT as_of_date FROM holdings_balance
               WHERE NOT (date(as_of_date) = as_of_date)""",
        ),
        (
            "holdings_balance",
            "hb_balance_not_null",
            "balance_ils NOT NULL",
            """SELECT as_of_date FROM holdings_balance WHERE balance_ils IS NULL""",
        ),
        (
            "holdings_balance",
            "hb_activity_not_null",
            "activity_type NOT NULL",
            """SELECT as_of_date FROM holdings_balance WHERE activity_type IS NULL""",
        ),
        (
            "holdings_balance",
            "hb_as_of_not_null",
            "as_of_date NOT NULL",
            """SELECT as_of_date FROM holdings_balance WHERE as_of_date IS NULL""",
        ),
        # schema_migrations
        (
            "schema_migrations",
            "sm_version_not_null",
            "version NOT NULL (PK)",
            """SELECT version FROM schema_migrations WHERE version IS NULL""",
        ),
        (
            "schema_migrations",
            "sm_name_not_null",
            "name NOT NULL",
            """SELECT version FROM schema_migrations WHERE name IS NULL""",
        ),
        (
            "schema_migrations",
            "sm_applied_not_null",
            "applied_at NOT NULL",
            """SELECT version FROM schema_migrations WHERE applied_at IS NULL""",
        ),
    ]

    # store_category: composite PK uniqueness is automatic; FK covered by foreign_key_check
    # Duplicate PK rows cannot exist. Optional explicit duplicate check skipped.

    for table, rule_id, description, sql in checks:
        try:
            n = _count(conn, sql)
        except sqlite3.Error as e:
            report.violations.append(
                ConstraintViolation(
                    table=table,
                    rule_id=rule_id,
                    description=description,
                    count=-1,
                    sample_detail=str(e),
                )
            )
            continue
        detail = ""
        if n > 0:
            try:
                sample = conn.execute(sql + " LIMIT 3").fetchall()
                detail = repr(sample)
            except sqlite3.Error:
                pass
        report.violations.append(
            ConstraintViolation(
                table=table,
                rule_id=rule_id,
                description=description,
                count=n,
                sample_detail=detail,
            )
        )

    return report


def format_report(report: LedgerAuditReport) -> str:
    lines = [
        f"PRAGMA integrity_check: {report.integrity_check}",
        f"PRAGMA foreign_key_check violations: {report.foreign_key_violation_count}",
        f"Expected triggers present: {report.expected_triggers_present}"
        + (f" (missing: {report.missing_triggers})" if report.missing_triggers else ""),
        "",
        "Constraint audits (0 = pass):",
    ]
    for v in report.violations:
        if v.count == 0:
            lines.append(f"  [OK] {v.table}.{v.rule_id} — {v.description}")
        elif v.count < 0:
            lines.append(f"  [ERR] {v.table}.{v.rule_id} — query error: {v.sample_detail}")
        else:
            lines.append(
                f"  [FAIL] {v.table}.{v.rule_id} — {v.description} — {v.count} row(s) {v.sample_detail}"
            )
    lines.append("")
    lines.append("OVERALL: PASSED" if report.ok else "OVERALL: FAILED")
    return "\n".join(lines)


# --- Migrations (ex-ledger_migrate) ---

_BASELINE_TARGET_VERSION = 10

# Must match ``schema/ledger/full_schema.sql`` — table name suffix only differs during migration.
_LEDGER_TX_V9_CREATE_BODY = """
CREATE TABLE ledger_transaction__mig_v9 (
    id    INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,

    "תאריך"          TEXT CHECK ("תאריך" IS NULL OR date("תאריך") = "תאריך"),
    "בחובה"          REAL NOT NULL DEFAULT 0,
    "בזכות"          REAL NOT NULL DEFAULT 0,
    "מקור עסקה"      TEXT,
    "פירוט נוסף"     TEXT,
    "תאור מורחב"     TEXT,
    "4 ספרות"        TEXT,
    "fingerprint"    TEXT,
    "fingerprint_v2" TEXT,
    "קטגוריה"        TEXT,
    notes              TEXT,

    statement_month    TEXT CHECK (
        statement_month IS NULL
        OR (length(statement_month) = 7 AND date(statement_month || '-01') IS NOT NULL AND strftime('%Y-%m', statement_month || '-01') = statement_month)
    ),

    ingested_at           TEXT NOT NULL CHECK (date(ingested_at) = ingested_at),
    category_updated_at   TEXT CHECK (category_updated_at IS NULL OR datetime(category_updated_at) IS NOT NULL),
    data_updated_at       TEXT CHECK (data_updated_at IS NULL OR datetime(data_updated_at) IS NOT NULL),

    CHECK (fingerprint IS NULL OR LENGTH(TRIM(fingerprint)) > 0),
    CHECK ("fingerprint_v2" IS NULL OR LENGTH(TRIM("fingerprint_v2")) > 0),
    UNIQUE ("fingerprint_v2")
) STRICT;
"""


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


def _table_has_fingerprint_v2(conn: sqlite3.Connection) -> bool:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ledger_transaction)")}
    return "fingerprint_v2" in cols


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


def _ensure_ledger_triggers_v10(conn: sqlite3.Connection) -> None:
    """Match ``full_schema.sql`` (``fingerprint`` + data columns bump ``data_updated_at``)."""
    conn.execute("DROP TRIGGER IF EXISTS tr_ledger_transaction_touch_category_updated_at")
    conn.execute("DROP TRIGGER IF EXISTS tr_ledger_transaction_touch_data_updated_at")
    conn.execute(
        """
CREATE TRIGGER tr_ledger_transaction_touch_category_updated_at
AFTER UPDATE ON ledger_transaction
FOR EACH ROW
WHEN NEW."קטגוריה" IS DISTINCT FROM OLD."קטגוריה"
 AND NEW.category_updated_at IS NOT DISTINCT FROM OLD.category_updated_at
BEGIN
    UPDATE ledger_transaction
    SET category_updated_at = datetime('now', 'localtime')
    WHERE id = OLD.id;
END;
"""
    )
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
    OR NEW."fingerprint" IS DISTINCT FROM OLD."fingerprint"
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


def _migrate_ledger_transaction_to_v9(conn: sqlite3.Connection) -> None:
    """Rebuild ``ledger_transaction`` to add ``fingerprint_v2`` and drop ``UNIQUE(fingerprint)``."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ledger_transaction)")}
    if not cols:
        return
    if "fingerprint_v2" in cols:
        return

    conn.execute("DROP VIEW IF EXISTS v_ledger_uncategorized")
    conn.execute("DROP TRIGGER IF EXISTS tr_ledger_transaction_touch_category_updated_at")
    conn.execute("DROP TRIGGER IF EXISTS tr_ledger_transaction_touch_data_updated_at")
    conn.executescript(_LEDGER_TX_V9_CREATE_BODY)
    conn.execute(
        """
        INSERT INTO ledger_transaction__mig_v9 (
            id, "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב", "4 ספרות",
            "fingerprint", "fingerprint_v2", "קטגוריה", notes, statement_month,
            ingested_at, category_updated_at, data_updated_at
        )
        SELECT id, "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב", "4 ספרות",
            "fingerprint", NULL, "קטגוריה", notes, statement_month,
            ingested_at, category_updated_at, data_updated_at
        FROM ledger_transaction
        """
    )
    conn.execute("DROP TABLE ledger_transaction")
    conn.execute("ALTER TABLE ledger_transaction__mig_v9 RENAME TO ledger_transaction")
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_ledger_transaction_date ON ledger_transaction ("תאריך")'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_ledger_transaction_category ON ledger_transaction ("קטגוריה")'
    )


# v10 table shell — must stay aligned with ``schema/ledger/full_schema.sql`` ``ledger_transaction``.
_LEDGER_TX_V10_CREATE_BODY = """
CREATE TABLE ledger_transaction__mig_v10 (
    id    INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,

    "תאריך"          TEXT CHECK ("תאריך" IS NULL OR date("תאריך") = "תאריך"),
    "בחובה"          REAL NOT NULL DEFAULT 0,
    "בזכות"          REAL NOT NULL DEFAULT 0,
    "מקור עסקה"      TEXT,
    "פירוט נוסף"     TEXT,
    "תאור מורחב"     TEXT,
    "4 ספרות"        TEXT,
    "fingerprint"    TEXT,
    "קטגוריה"        TEXT,
    notes              TEXT,

    statement_month    TEXT CHECK (
        statement_month IS NULL
        OR (length(statement_month) = 7 AND date(statement_month || '-01') IS NOT NULL AND strftime('%Y-%m', statement_month || '-01') = statement_month)
    ),

    ingested_at           TEXT NOT NULL CHECK (date(ingested_at) = ingested_at),
    category_updated_at   TEXT CHECK (category_updated_at IS NULL OR datetime(category_updated_at) IS NOT NULL),
    data_updated_at       TEXT CHECK (data_updated_at IS NULL OR datetime(data_updated_at) IS NOT NULL),

    CHECK (fingerprint IS NULL OR LENGTH(TRIM(fingerprint)) > 0),
    UNIQUE ("fingerprint")
) STRICT;
"""


def _migrate_ledger_transaction_to_v10(conn: sqlite3.Connection) -> None:
    """Single ``fingerprint`` column (values from ``fingerprint_v2``, else legacy ``fingerprint``).

    Uses a table rebuild: ``ALTER TABLE DROP COLUMN`` can fail when legacy CHECK clauses reference
    ``fingerprint`` (SQLite may not rewrite them cleanly).
    """
    if not _table_has_fingerprint_v2(conn):
        return

    conn.execute("DROP VIEW IF EXISTS v_ledger_uncategorized")
    conn.execute("DROP TRIGGER IF EXISTS tr_ledger_transaction_touch_category_updated_at")
    conn.execute("DROP TRIGGER IF EXISTS tr_ledger_transaction_touch_data_updated_at")
    conn.executescript(_LEDGER_TX_V10_CREATE_BODY)
    conn.execute(
        """
        INSERT INTO ledger_transaction__mig_v10 (
            id, "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב", "4 ספרות",
            "fingerprint", "קטגוריה", notes, statement_month, ingested_at, category_updated_at, data_updated_at
        )
        SELECT id, "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב", "4 ספרות",
            COALESCE(
                NULLIF(TRIM("fingerprint_v2"), ''),
                NULLIF(TRIM("fingerprint"), '')
            ),
            "קטגוריה", notes, statement_month, ingested_at, category_updated_at, data_updated_at
        FROM ledger_transaction
        """
    )
    conn.execute("DROP TABLE ledger_transaction")
    conn.execute("ALTER TABLE ledger_transaction__mig_v10 RENAME TO ledger_transaction")
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_ledger_transaction_date ON ledger_transaction ("תאריך")'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_ledger_transaction_category ON ledger_transaction ("קטגוריה")'
    )
    conn.execute(
        """
        CREATE VIEW IF NOT EXISTS v_ledger_uncategorized AS
        SELECT *
        FROM ledger_transaction
        WHERE "קטגוריה" IS NULL OR TRIM(COALESCE("קטגוריה", '')) = ''
        """
    )


def migrate_ledger_db(db_path: str | None = None) -> None:
    """
    Ensure the ledger database exists and matches the v10 contract in ``full_schema.sql``.

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

        # Existing v8+ file: v9 (add fingerprint_v2) if needed, then v10 (single fingerprint column).
        _drop_abandoned_v9_columns_if_present(conn)
        ver = _current_schema_version(conn)
        if ver < 9 and not _table_has_fingerprint_v2(conn):
            _migrate_ledger_transaction_to_v9(conn)
        _migrate_ledger_transaction_to_v10(conn)
        _ensure_ledger_triggers_v10(conn)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (9, 'ledger_fingerprint_v2_unique_drop_fingerprint_unique')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (10, 'ledger_single_fingerprint_column_v2_semantics')"
        )
        conn.commit()
    finally:
        conn.close()


# --- Category updates (ex-ledger_category) ---


def update_category_by_fingerprint(db_path: str, fingerprint: str, category: str | None) -> None:
    """Set ``קטגוריה`` for a row; trigger fills ``category_updated_at`` when the value changes."""
    if not fingerprint or not str(fingerprint).strip():
        return
    migrate_ledger_db(db_path)
    cat_str = "" if category is None else str(category)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        fp = str(fingerprint).strip()
        conn.execute(
            'UPDATE ledger_transaction SET "קטגוריה" = ? '
            'WHERE "fingerprint" IS NOT NULL AND TRIM("fingerprint") = ?',
            (cat_str, fp),
        )
        conn.commit()
    finally:
        conn.close()


# --- DataFrame load/export (ex-ledger_dataframe) ---

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
            SELECT
                NULLIF(TRIM("fingerprint"), '') AS transaction_id,
                "קטגוריה" AS category
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


# --- Fingerprint backfill (ex-ledger_fingerprint_backfill) ---

_ROW_KEYS = (
    "תאריך",
    "בחובה",
    "בזכות",
    "מקור עסקה",
    "פירוט נוסף",
    "תאור מורחב",
)


@dataclass
class BackfillStats:
    examined: int = 0
    updated: int = 0
    skipped_uncomputable: int = 0  # algorithm returned None / empty
    skipped_would_duplicate: int = 0  # kept for API compatibility; always 0 (see module doc)


@dataclass
class WouldDuplicateDetail:
    """Deprecated: schema v9 allows duplicate ``fingerprint``; list is always empty."""

    id: int
    computed_fingerprint: str
    conflicts_with_id: int
    conflict_kind: str
    תאריך: str | None
    בחובה: float
    בזכות: float
    makor: str | None


def _row_series_from_sqlite(row: sqlite3.Row) -> pd.Series:
    d = {k: row[k] for k in _ROW_KEYS}
    return pd.Series(d)


def list_would_duplicate_null_rows(
    db_path: str,
) -> tuple[BackfillStats, list[WouldDuplicateDetail]]:
    """
    Back-compat: returns dry-run stats and an **empty** duplicate list (v9 has no UNIQUE on
    ``fingerprint``).
    """
    return _simulate_null_fingerprints(db_path, dry_run=True, collect_duplicates=True)


def _simulate_null_fingerprints(
    db_path: str,
    *,
    dry_run: bool,
    collect_duplicates: bool,
) -> tuple[BackfillStats, list[WouldDuplicateDetail]]:
    del collect_duplicates  # unused
    stats = BackfillStats()
    duplicates: list[WouldDuplicateDetail] = []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            f"""
            SELECT id, {", ".join(f'"{k}"' for k in _ROW_KEYS)}
            FROM ledger_transaction
            WHERE fingerprint IS NULL
            ORDER BY id
            """
        )
        rows = cur.fetchall()
        stats.examined = len(rows)

        for row in rows:
            rid = int(row["id"])
            s = _row_series_from_sqlite(row)
            fp = generate_transaction_fingerprint(s)
            if fp is None or not str(fp).strip():
                stats.skipped_uncomputable += 1
                continue
            fp = str(fp).strip()

            stats.updated += 1
            if not dry_run:
                conn.execute(
                    "UPDATE ledger_transaction SET fingerprint = ? WHERE id = ? AND fingerprint IS NULL",
                    (fp, rid),
                )

        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return stats, duplicates


def backfill_null_fingerprints(db_path: str, *, dry_run: bool = True) -> BackfillStats:
    """
    For each row with ``fingerprint IS NULL``, compute the fingerprint and UPDATE.

    ``dry_run`` when True does not write.
    """
    stats, _ = _simulate_null_fingerprints(db_path, dry_run=dry_run, collect_duplicates=False)
    return stats


# --- Row helpers + web totals CSV + static store mappings (folded from former modules) ---

# Web totals CSV → ``ledger_transaction``
# Tolerant column set for real-world CSVs (includes legacy headers; not all are persisted — see module doc).
_EXPECTED_COLS = [
    "תאריך",
    "מקור עסקה",
    "בחובה",
    "מזהה עסקה",
    "בזכות",
    "פירוט נוסף",
    "4 ספרות",
    "תאור מורחב",
    "קטגוריה",
    "תאריך עדכון",
    "fingerprint",
]


def fingerprint_from_row(row: pd.Series) -> str | None:
    """Return stripped pipeline fingerprint, or None if absent (never use מזהה עסקה)."""
    fp = row.get("fingerprint")
    if pd.isna(fp):
        return None
    s = str(fp).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    return s


def load_web_totals_dataframe(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    missing = [c for c in _EXPECTED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing columns: {missing}; expected {_EXPECTED_COLS}")
    keys_csv: list[str | None] = []
    computed: list[str | None] = []
    for _, row in df.iterrows():
        keys_csv.append(fingerprint_from_row(row))
        computed.append(generate_transaction_fingerprint(row))
    non_null = [k for k in computed if k is not None and str(k).strip()]
    if len(non_null) != len(set(non_null)):
        from collections import Counter

        c = Counter(non_null)
        bad = [k for k, n in c.items() if n > 1][:5]
        raise ValueError(f"duplicate non-null fingerprint values (first few): {bad!r}")
    df = df.copy()
    df["_ledger_fingerprint_csv"] = keys_csv
    df["_ledger_fingerprint"] = computed
    return df


def _normalize_date_text(val: Any) -> str | None:
    if pd.isna(val) or (isinstance(val, str) and not val.strip()):
        return None
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    if not s:
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        ts = pd.to_datetime(s[:10], errors="coerce", format="%Y-%m-%d")
    else:
        ts = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if pd.isna(ts):
        raise ValueError(f"invalid date: {val!r}")
    return ts.strftime("%Y-%m-%d")


def _float_col(val: Any) -> float:
    if pd.isna(val):
        return 0.0
    try:
        x = float(val)
    except (TypeError, ValueError):
        return 0.0
    if x != x:  # NaN
        return 0.0
    return x


def _text_or_none(val: Any) -> str | None:
    if pd.isna(val):
        return None
    s = str(val).strip()
    return s if s else None


def _web_totals_row_tuple(row: pd.Series) -> tuple:
    d = _normalize_date_text(row["תאריך"])
    if not d:
        raise ValueError("missing תאריך")
    te_raw = row["תאריך עדכון"]
    ing = compute_ingested_at_iso(d, te_raw)
    fp = row["_ledger_fingerprint"]
    return (
        d,
        _float_col(row["בחובה"]),
        _float_col(row["בזכות"]),
        _text_or_none(row["מקור עסקה"]),
        _text_or_none(row["פירוט נוסף"]),
        _text_or_none(row["תאור מורחב"]),
        _text_or_none(row["4 ספרות"]),
        fp,
        _text_or_none(row["קטגוריה"]),
        None,
        None,
        ing,
    )


def import_web_totals_to_ledger(
    csv_path: str | None = None,
    db_path: str | None = None,
    *,
    replace: bool = True,
) -> dict[str, Any]:
    """
    Load ``web_totals.csv`` into ``ledger_transaction``.

    Runs ``migrate_ledger_db`` first. If ``replace`` is True, deletes existing ledger rows
    before insert (full reload from CSV).
    """
    path = csv_path if csv_path is not None else config.web_totals_file
    db = db_path if db_path is not None else config.ledger_db_file
    migrate_ledger_db(db)

    df = load_web_totals_dataframe(path)
    rows = [_web_totals_row_tuple(r) for _, r in df.iterrows()]

    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if replace:
            conn.execute("DELETE FROM ledger_transaction")
            conn.commit()
        sql = """
        INSERT INTO ledger_transaction (
            "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב", "4 ספרות",
            "fingerprint", "קטגוריה", notes, statement_month, ingested_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """
        conn.executemany(sql, rows)
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM ledger_transaction").fetchone()[0]
    finally:
        conn.close()

    report = verify_ledger_against_csv(df, db)
    report["rows_imported"] = int(n)
    report["rows_without_fingerprint"] = int(df["_ledger_fingerprint"].isna().sum())
    report["csv_path"] = path
    report["db_path"] = db
    log.info(
        "web_totals import: %s rows into %s (parity ok=%s)",
        n,
        db,
        report.get("parity_ok"),
    )
    return report


def _fp_equal(a: Any, b: Any) -> bool:
    a_null = a is None or (isinstance(a, float) and pd.isna(a)) or pd.isna(a)
    b_null = b is None or (isinstance(b, float) and pd.isna(b)) or pd.isna(b)
    if a_null and b_null:
        return True
    if a_null != b_null:
        return False
    return str(a).strip() == str(b).strip()


def verify_ledger_against_csv(df: pd.DataFrame, db_path: str) -> dict[str, Any]:
    """Row-order parity: import order matches ``ORDER BY id`` (same row count and fields)."""
    conn = sqlite3.connect(db_path)
    try:
        q = """
        SELECT id, "fingerprint", "בחובה", "בזכות", "תאריך", ingested_at
        FROM ledger_transaction
        ORDER BY id
        """
        ldb = pd.read_sql_query(q, conn)
    finally:
        conn.close()

    if len(ldb) != len(df):
        return {
            "parity_ok": False,
            "error": f"row count mismatch: csv={len(df)} db={len(ldb)}",
        }

    csv_debit = pd.to_numeric(df["בחובה"], errors="coerce").fillna(0).sum()
    csv_credit = pd.to_numeric(df["בזכות"], errors="coerce").fillna(0).sum()
    db_debit = pd.to_numeric(ldb["בחובה"], errors="coerce").fillna(0).sum()
    db_credit = pd.to_numeric(ldb["בזכות"], errors="coerce").fillna(0).sum()

    tol = 0.05
    sum_ok = abs(csv_debit - db_debit) < tol and abs(csv_credit - db_credit) < tol

    mismatches: list[str] = []
    for i in range(len(df)):
        cr = df.iloc[i]
        dr = ldb.iloc[i]
        if _normalize_date_text(cr["תאריך"]) != str(dr["תאריך"]):
            mismatches.append(f"row {i} date mismatch")
            continue
        if not _fp_equal(cr["_ledger_fingerprint"], dr["fingerprint"]):
            mismatches.append(
                f"row {i} fingerprint mismatch computed={cr['_ledger_fingerprint']!r} db={dr['fingerprint']!r}"
            )
        cd = _float_col(cr["בחובה"])
        cc = _float_col(cr["בזכות"])
        if abs(float(dr["בחובה"]) - cd) > tol or abs(float(dr["בזכות"]) - cc) > tol:
            mismatches.append(f"row {i} amounts differ")

    out: dict[str, Any] = {
        "parity_ok": sum_ok and len(mismatches) == 0,
        "csv_rows": len(df),
        "db_rows": len(ldb),
        "sum_debit_csv": float(csv_debit),
        "sum_debit_db": float(db_debit),
        "sum_credit_csv": float(csv_credit),
        "sum_credit_db": float(db_credit),
        "order_mismatches": mismatches[:30],
    }
    if mismatches:
        out["parity_ok"] = False
    return out

# Static store / similar pair tables
_STORE_COLS = ["store_name", "category", "is_static"]
_SIMILAR_COLS = ["p1", "p2"]


def _coerce_is_static(val: Any) -> int:
    if pd.isna(val):
        return 0
    try:
        i = int(round(float(val)))
    except (TypeError, ValueError):
        return 0
    return 1 if i == 1 else 0


def load_stores_to_categories_dataframe(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    missing = [c for c in _STORE_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing columns: {missing}; expected {_STORE_COLS}")
    df = df.copy()
    # Drop null store/category before str coercion — NaN must not become the literal "nan"
    # (groupby excludes NA keys; iterrows would still emit them and break store_category FKs).
    df = df.dropna(subset=["store_name", "category"])
    df["store_name"] = df["store_name"].astype(str).str.strip()
    df["category"] = df["category"].astype(str).str.strip()
    df = df[(df["store_name"] != "") & (df["category"] != "")]
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["store_name", "category"], keep="last")
    return df.reset_index(drop=True)


def load_similar_pairs_dataframe(csv_path: str) -> pd.DataFrame:
    """Return ``p1``/``p2`` rows; empty frame if file is missing (caller may warn)."""
    if not os.path.isfile(csv_path):
        return pd.DataFrame(columns=_SIMILAR_COLS)
    df = pd.read_csv(csv_path)
    missing = [c for c in _SIMILAR_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing columns: {missing}; expected {_SIMILAR_COLS}")
    df = df.copy()
    df = df.dropna(subset=["p1", "p2"])
    df["p1"] = df["p1"].astype(str).str.strip()
    df["p2"] = df["p2"].astype(str).str.strip()
    df = df[(df["p1"] != "") & (df["p2"] != "")]
    df = df[df["p1"] != df["p2"]]
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["p1", "p2"], keep="last")
    return df.reset_index(drop=True)


def import_stores_to_ledger(
    csv_path: str | None = None,
    db_path: str | None = None,
    *,
    similar_pairs_csv: str | None = None,
    replace: bool = True,
) -> dict[str, Any]:
    """
    Load store/category mappings and similar category pairs into the ledger DB.

    Runs ``migrate_ledger_db`` first. If ``replace`` is True, deletes all ``store`` rows
    (``store_category`` cascades) and all ``similar_category_pair`` rows before insert.

    ``similar_pairs_csv`` defaults to ``config.similar_categories_file``. If that file is
    missing, similar pairs are skipped (table cleared when ``replace``).
    """
    path = csv_path if csv_path is not None else config.stores_to_categories_file
    sim_path = similar_pairs_csv if similar_pairs_csv is not None else config.similar_categories_file
    db = db_path if db_path is not None else config.ledger_db_file
    migrate_ledger_db(db)

    warnings: list[str] = []
    if not os.path.isfile(sim_path):
        warnings.append(f"Similar pairs file not found (skipped): {sim_path}")

    sim_df = load_similar_pairs_dataframe(sim_path)
    df = load_stores_to_categories_dataframe(path)

    if df.empty and sim_df.empty:
        conn = sqlite3.connect(db)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            if replace:
                conn.execute("DELETE FROM store")
                conn.execute("DELETE FROM similar_category_pair")
                conn.commit()
        finally:
            conn.close()
        msg = "No store/category rows after filter"
        if not os.path.isfile(sim_path):
            msg += "; similar pairs file missing"
        elif sim_df.empty and os.path.isfile(sim_path):
            msg += "; similar_pairs had no valid rows"
        return {
            "ok": True,
            "csv_path": path,
            "similar_pairs_csv": sim_path,
            "db_path": db,
            "stores_inserted": 0,
            "store_category_rows_inserted": 0,
            "similar_pair_rows_inserted": 0,
            "stores_forced_dynamic": 0,
            "warnings": [msg],
        }

    store_rows: list[tuple[str, int]] = []
    forced_dynamic = 0
    if not df.empty:
        for store_name, sub in df.groupby("store_name", sort=True):
            cats = sub["category"].unique()
            if len(cats) > 1:
                static = 0
                if (sub["is_static"].map(_coerce_is_static) == 1).any():
                    forced_dynamic += 1
            else:
                static = _coerce_is_static(sub["is_static"].iloc[0])
            store_rows.append((str(store_name), static))

    sc_tuples = (
        [(str(r["store_name"]), str(r["category"])) for _, r in df.iterrows()] if not df.empty else []
    )
    sim_tuples = [(str(r["p1"]), str(r["p2"])) for _, r in sim_df.iterrows()]

    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if replace:
            conn.execute("DELETE FROM store")
            conn.execute("DELETE FROM similar_category_pair")
        if store_rows:
            conn.executemany(
                "INSERT INTO store (store_name, is_static) VALUES (?, ?)",
                store_rows,
            )
        if sc_tuples:
            conn.executemany(
                "INSERT INTO store_category (store_name, category) VALUES (?, ?)",
                sc_tuples,
            )
        if sim_tuples:
            conn.executemany(
                "INSERT INTO similar_category_pair (p1, p2) VALUES (?, ?)",
                sim_tuples,
            )
        conn.commit()
        n_store = conn.execute("SELECT COUNT(*) FROM store").fetchone()[0]
        n_sc = conn.execute("SELECT COUNT(*) FROM store_category").fetchone()[0]
        n_sim = conn.execute("SELECT COUNT(*) FROM similar_category_pair").fetchone()[0]
    finally:
        conn.close()

    if forced_dynamic:
        warnings.append(
            f"{forced_dynamic} store(s) had is_static=1 on some row but multiple categories; "
            "set to dynamic (is_static=0)."
        )

    log.info(
        "static import: %s stores, %s store_category, %s similar pairs → %s",
        n_store,
        n_sc,
        n_sim,
        db,
    )
    return {
        "ok": True,
        "csv_path": path,
        "similar_pairs_csv": sim_path,
        "db_path": db,
        "stores_inserted": int(n_store),
        "store_category_rows_inserted": int(n_sc),
        "similar_pair_rows_inserted": int(n_sim),
        "stores_forced_dynamic": forced_dynamic,
        "warnings": warnings,
    }


def sync_stores_to_ledger_from_dataframe(db_path: str, df: pd.DataFrame) -> None:
    """
    Replace ``store`` / ``store_category`` from an in-memory frame (same columns as the CSV).

    Does **not** modify ``similar_category_pair`` (pairs stay in the DB until a full static import).
    """
    migrate_ledger_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM store")
        if df.empty:
            conn.commit()
            return

        missing = [c for c in _STORE_COLS if c not in df.columns]
        if missing:
            raise ValueError(f"stores dataframe missing columns: {missing}")

        work = df.copy()
        work = work.dropna(subset=["store_name", "category"])
        work["store_name"] = work["store_name"].astype(str).str.strip()
        work["category"] = work["category"].astype(str).str.strip()
        work = work[(work["store_name"] != "") & (work["category"] != "")]
        if work.empty:
            conn.commit()
            return
        work = work.drop_duplicates(subset=["store_name", "category"], keep="last")

        store_rows: list[tuple[str, int]] = []
        forced_dynamic = 0
        for store_name, sub in work.groupby("store_name", sort=True):
            cats = sub["category"].unique()
            if len(cats) > 1:
                static = 0
                if (sub["is_static"].map(_coerce_is_static) == 1).any():
                    forced_dynamic += 1
            else:
                static = _coerce_is_static(sub["is_static"].iloc[0])
            store_rows.append((str(store_name), static))

        sc_tuples = [(str(r["store_name"]), str(r["category"])) for _, r in work.iterrows()]

        if store_rows:
            conn.executemany(
                "INSERT INTO store (store_name, is_static) VALUES (?, ?)",
                store_rows,
            )
        if sc_tuples:
            conn.executemany(
                "INSERT INTO store_category (store_name, category) VALUES (?, ?)",
                sc_tuples,
            )
        conn.commit()
        if forced_dynamic:
            log.warning(
                "%s store(s) forced dynamic (is_static=1 with multiple categories)",
                forced_dynamic,
            )
    finally:
        conn.close()

# --- Compile upsert (ex-ledger_compile_upsert) ---

_UPSERT_SQL = """
INSERT INTO ledger_transaction (
    "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב", "4 ספרות",
    "fingerprint", "קטגוריה", notes, statement_month, ingested_at
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT("fingerprint") DO UPDATE SET
    "תאריך" = excluded."תאריך",
    "בחובה" = excluded."בחובה",
    "בזכות" = excluded."בזכות",
    "מקור עסקה" = excluded."מקור עסקה",
    "פירוט נוסף" = excluded."פירוט נוסף",
    "תאור מורחב" = excluded."תאור מורחב",
    "4 ספרות" = excluded."4 ספרות",
    "קטגוריה" = CASE
        WHEN TRIM(COALESCE(ledger_transaction."קטגוריה", '')) != '' THEN ledger_transaction."קטגוריה"
        ELSE excluded."קטגוריה"
    END,
    notes = CASE
        WHEN TRIM(COALESCE(ledger_transaction.notes, '')) != '' THEN ledger_transaction.notes
        ELSE excluded.notes
    END,
    statement_month = COALESCE(ledger_transaction.statement_month, excluded.statement_month),
    ingested_at = ledger_transaction.ingested_at
"""


def _row_tuple(row: pd.Series) -> tuple[Any, ...]:
    d = _normalize_date_text(row.get("תאריך"))
    if not d:
        raise ValueError("missing תאריך")
    taarich_hidon = row.get("תאריך עדכון")
    ing = compute_ingested_at_iso(row.get("תאריך"), taarich_hidon)
    fp = generate_transaction_fingerprint(row)
    if fp is None or not str(fp).strip():
        raise ValueError("missing fingerprint")
    return (
        d,
        _float_col(row.get("בחובה")),
        _float_col(row.get("בזכות")),
        _text_or_none(row.get("מקור עסקה")),
        _text_or_none(row.get("פירוט נוסף")),
        _text_or_none(row.get("תאור מורחב")),
        _text_or_none(row.get("4 ספרות")),
        str(fp).strip(),
        _text_or_none(row.get("קטגוריה")),
        _text_or_none(row.get("notes")),
        _text_or_none(row.get("statement_month")),
        ing,
    )


def upsert_compiled_dataframe_to_ledger(
    df: pd.DataFrame,
    db_path: str,
) -> dict[str, Any]:
    """
    Upsert transaction rows into ``ledger_transaction`` by ``fingerprint``.

    Skips rows without a computable ``fingerprint``. Empty ``df`` is a no-op.
    """
    migrate_ledger_db(db_path)
    if df.empty:
        return {"rows_upserted": 0, "rows_skipped_no_fingerprint": 0, "db_path": db_path}

    tuples: list[tuple[Any, ...]] = []
    skipped = 0
    for _, row in df.iterrows():
        fp = generate_transaction_fingerprint(row)
        if fp is None or not str(fp).strip():
            skipped += 1
            continue
        try:
            tuples.append(_row_tuple(row))
        except ValueError as e:
            log.warning("ledger upsert skip row: %s", e)
            skipped += 1

    if not tuples:
        log.info("ledger upsert: no rows with fingerprint to write (%s skipped)", skipped)
        return {
            "rows_upserted": 0,
            "rows_skipped_no_fingerprint": skipped,
            "db_path": db_path,
        }

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executemany(_UPSERT_SQL, tuples)
        conn.commit()
    finally:
        conn.close()

    log.info(
        "ledger upsert: %s row(s) into %s (%s skipped)",
        len(tuples),
        db_path,
        skipped,
    )
    return {
        "rows_upserted": len(tuples),
        "rows_skipped_no_fingerprint": skipped,
        "db_path": db_path,
    }
