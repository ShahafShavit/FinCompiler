"""Ledger SQLite operations: migrations, audit, dataframe I/O, category updates, fingerprint backfill,
static store mappings, compile upsert.

Migrations (MIG-C2): baseline applies ``schema/ledger/full_schema.sql`` when the DB is empty.

**v8** — nullable ``fingerprint``, ``ingested_at``; no ``מזהה עסקה`` / ``תאריך עדכון`` columns.
Older DBs (v7 or below) require deleting ``ledger.sqlite`` and re-importing (see ``schema/ledger/README.md``).

**v9** — transitional: ``fingerprint_v2`` (``UNIQUE``) plus legacy ``fingerprint`` (superseded by v10).

**v10** — single ``fingerprint`` column (same values as former ``fingerprint_v2``): drop legacy ``fingerprint``,
rename ``fingerprint_v2`` → ``fingerprint``. Requires SQLite 3.35+ (``ALTER TABLE DROP COLUMN``).

**v11** — recompute ``fingerprint`` after normalizing optional text (``None`` vs ``NaN``); merge duplicate rows.

**v12** — recompute ``fingerprint`` again using ISO-safe date parsing (same rules as ``parse_post_ingest_date_scalar``);
merge duplicate rows.

**v14** — ``excluded_from_calculations`` (0/1): rows set to 1 are kept in the DB but omitted from
heatmap, dashboard aggregates, categorize queue, and integrity anomaly checks.

Constraint audit mirrors ``full_schema.sql`` CHECK/NOT NULL/FK rules. Compile upsert implements MIG-E2.
"""
from __future__ import annotations

import logging
import math
import os
import re
import sqlite3
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

import config

from pipeline.compiler import parse_post_ingest_date_scalar
from pipeline.csv_handler import generate_transaction_fingerprint
from pipeline.ingested_at_rules import ingested_at_for_new_ledger_row

log = logging.getLogger(__name__)

# Serialize ``migrate_ledger_db`` — ThreadingHTTPServer + parallel dashboard requests can
# otherwise interleave DROP/CREATE TRIGGER and hit "already exists" (see tr_ledger_transaction_*).
_migrate_ledger_lock = threading.RLock()

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
            "ledger_transaction",
            "lt_excluded_from_calculations_boolean",
            "excluded_from_calculations in (0, 1) when present",
            """SELECT id FROM ledger_transaction
               WHERE excluded_from_calculations IS NOT NULL
                 AND excluded_from_calculations NOT IN (0, 1)""",
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


def _ledger_transaction_column_names(conn: sqlite3.Connection) -> set[str]:
    return {str(r[1]) for r in conn.execute("PRAGMA table_info(ledger_transaction)")}


def _recreate_v_ledger_uncategorized_view(conn: sqlite3.Connection) -> None:
    """Aligned with ``schema/ledger/full_schema.sql`` (requires ``excluded_from_calculations`` column)."""
    conn.execute("DROP VIEW IF EXISTS v_ledger_uncategorized")
    conn.execute(
        """
        CREATE VIEW v_ledger_uncategorized AS
        SELECT *
        FROM ledger_transaction
        WHERE ("קטגוריה" IS NULL OR TRIM(COALESCE("קטגוריה", '')) = '')
          AND COALESCE(excluded_from_calculations, 0) = 0
        """
    )


def migrate_ledger_db(db_path: str | None = None) -> None:
    """
    Ensure the ledger database exists and matches the v14 contract in ``full_schema.sql``.

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

    with _migrate_ledger_lock:
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
            ver = _current_schema_version(conn)
            if ver < 11:
                _migrate_fingerprint_optional_text_normalize(conn)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (11, 'fingerprint_optional_text_normalize')"
            )
            ver = _current_schema_version(conn)
            if ver < 12:
                _migrate_fingerprint_iso_date_parse(conn)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (12, 'fingerprint_iso_date_parse_match_compiler')"
            )
            ver = _current_schema_version(conn)
            if ver < 13:
                conn.execute("DROP TABLE IF EXISTS similar_category_pair")
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (13, 'drop_similar_category_pair')"
            )
            lt_cols = _ledger_transaction_column_names(conn)
            if "excluded_from_calculations" not in lt_cols:
                conn.execute(
                    "ALTER TABLE ledger_transaction ADD COLUMN excluded_from_calculations "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            if "excluded_from_calculations" in _ledger_transaction_column_names(conn):
                _recreate_v_ledger_uncategorized_view(conn)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (14, 'ledger_excluded_from_calculations')"
            )
            conn.commit()
        finally:
            conn.close()


def ledger_connect_readonly(db_path: str) -> sqlite3.Connection:
    """Open the ledger database read-only (URI ``mode=ro``)."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


# SQL fragment: effective calendar month for dashboard / reports — valid ``statement_month`` (YYYY-MM)
# per schema CHECK, otherwise the month of ``תאריך``. Same rules as legacy pandas ``YearMonth``.
LEDGER_SQL_EFFECTIVE_YM_EXPR = """
CASE
  WHEN statement_month IS NOT NULL
   AND TRIM(COALESCE(statement_month, '')) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
   AND LENGTH(TRIM(COALESCE(statement_month, ''))) = 7
  THEN TRIM(statement_month)
  ELSE strftime('%Y-%m', date("תאריך"))
END
""".strip()

# Omit from heatmap/dashboard exports and categorize queue when 1 (see schema ``excluded_from_calculations``).
LEDGER_SQL_TX_INCLUDED = "(COALESCE(excluded_from_calculations, 0) = 0)"
LEDGER_SQL_LT_TX_INCLUDED = "(COALESCE(lt.excluded_from_calculations, 0) = 0)"

# Calendar day for 30d/YTD anchoring: first day of valid ``statement_month``, else ``תאריך``.
LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR = """
CASE
  WHEN statement_month IS NOT NULL
   AND TRIM(COALESCE(statement_month, '')) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
   AND LENGTH(TRIM(COALESCE(statement_month, ''))) = 7
  THEN date(TRIM(statement_month) || '-01')
  ELSE date("תאריך")
END
""".strip()


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


def update_categories_by_fingerprint_batch(
    db_path: str,
    items: list[tuple[str, str]],
) -> int:
    """
    Apply category updates in one transaction (categorization UI / auto pass).

    Each item is ``(fingerprint, category_string)``. Skips empty fingerprints.
    """
    params: list[tuple[str, str]] = []
    for fp, cat in items:
        f = str(fp).strip()
        if not f:
            continue
        c = "" if cat is None else str(cat)
        params.append((c, f))
    if not params:
        return 0
    migrate_ledger_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        sql = (
            'UPDATE ledger_transaction SET "קטגוריה" = ? '
            'WHERE "fingerprint" IS NOT NULL AND TRIM("fingerprint") = ?'
        )
        conn.executemany(sql, params)
        conn.commit()
    finally:
        conn.close()
    return len(params)


# --- Transaction patch (heatmap / detail UI) ---------------------------------

LEDGER_TX_PATCH_SAFE_KEYS = frozenset(
    {"notes", "קטגוריה", "4 ספרות", "statement_month", "excluded_from_calculations"}
)
LEDGER_TX_PATCH_FINGERPRINT_KEYS = frozenset(
    {"תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב"}
)
LEDGER_FINGERPRINT_CONFIRM_PHRASE = "REKEY"


def _ledger_patch_sql_column(name: str) -> str:
    if name in ("notes", "statement_month", "ingested_at", "fingerprint", "excluded_from_calculations"):
        return name
    return f'"{name}"'


def _ledger_normalize_optional_text(val: Any, *, empty_as_none: bool) -> str | None:
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except TypeError:
        pass
    s = str(val).strip()
    if not s:
        return None if empty_as_none else ""
    return s


def _ledger_normalize_category(val: Any) -> str:
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except TypeError:
        pass
    return str(val).strip()


def _ledger_normalize_statement_month(val: Any) -> str | None:
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except TypeError:
        pass
    s = str(val).strip()
    if not s:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}", s):
        raise ValueError("statement_month must be YYYY-MM")
    mo = int(s[5:7])
    if mo < 1 or mo > 12:
        raise ValueError("statement_month must be YYYY-MM")
    return s


def _ledger_patch_normalize_date(val: Any) -> str:
    ts = parse_post_ingest_date_scalar(val)
    if pd.isna(ts):
        raise ValueError("תאריך is missing or invalid")
    out = ts.strftime("%Y-%m-%d")
    return out


def _ledger_patch_coerce_amount(val: Any, *, label: str) -> float:
    if val is None:
        raise ValueError(f"{label} is required")
    try:
        if pd.isna(val):
            raise ValueError(f"{label} is required")
    except TypeError:
        pass
    try:
        x = float(val)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{label} must be a number") from e
    if not math.isfinite(x):
        raise ValueError(f"{label} must be a finite number")
    return x


def _ledger_row_fp_basis(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "תאריך": row["תאריך"],
        "בחובה": float(row["בחובה"] or 0),
        "בזכות": float(row["בזכות"] or 0),
        "מקור עסקה": row["מקור עסקה"],
        "פירוט נוסף": row["פירוט נוסף"],
        "תאור מורחב": row["תאור מורחב"],
    }


def patch_ledger_transaction_by_id(
    db_path: str,
    row_id: int,
    patch: Mapping[str, Any],
    *,
    confirm_fingerprint_change: bool = False,
    confirm_fingerprint_phrase: str = "",
) -> dict[str, Any]:
    """
    Apply a partial update to ``ledger_transaction`` by primary key.

    * Safe keys (do not affect fingerprint inputs): ``notes``, ``קטגוריה``, ``4 ספרות``,
      ``statement_month``, ``excluded_from_calculations`` (must be ``0`` or ``1``).
    * Fingerprint keys: Hebrew date/amount/source/description columns — require
      ``confirm_fingerprint_change`` and phrase :data:`LEDGER_FINGERPRINT_CONFIRM_PHRASE`.

    Returns ``{"ok": True}`` or ``{"ok": False, "error": "...", "message": "..."}``.
    """
    migrate_ledger_db(db_path)
    if not patch:
        return {"ok": False, "error": "validation_error", "message": "patch object is empty"}

    patch_keys = frozenset(patch.keys())
    allowed = LEDGER_TX_PATCH_SAFE_KEYS | LEDGER_TX_PATCH_FINGERPRINT_KEYS
    unknown = patch_keys - allowed
    if unknown:
        return {
            "ok": False,
            "error": "validation_error",
            "message": f"unknown patch keys: {', '.join(sorted(unknown))}",
        }

    fp_keys = patch_keys & LEDGER_TX_PATCH_FINGERPRINT_KEYS
    if fp_keys:
        if not confirm_fingerprint_change:
            return {
                "ok": False,
                "error": "fingerprint_confirmation_required",
                "message": "Changing fingerprint-driving fields requires confirm_fingerprint_change and phrase.",
            }
        if str(confirm_fingerprint_phrase).strip() != LEDGER_FINGERPRINT_CONFIRM_PHRASE:
            return {
                "ok": False,
                "error": "fingerprint_phrase_required",
                "message": f"Type the phrase {LEDGER_FINGERPRINT_CONFIRM_PHRASE!r} to confirm.",
            }

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM ledger_transaction WHERE id = ?", (int(row_id),)).fetchone()
        if row is None:
            return {"ok": False, "error": "not_found", "message": "No ledger row with this id"}

        sql_updates: dict[str, Any] = {}
        basis = _ledger_row_fp_basis(row)

        try:
            for k in patch_keys & LEDGER_TX_PATCH_SAFE_KEYS:
                raw = patch[k]
                if k == "notes":
                    sql_updates[k] = _ledger_normalize_optional_text(raw, empty_as_none=True)
                elif k == "קטגוריה":
                    sql_updates[k] = _ledger_normalize_category(raw)
                elif k == "4 ספרות":
                    sql_updates[k] = _ledger_normalize_optional_text(raw, empty_as_none=True)
                elif k == "statement_month":
                    sql_updates[k] = _ledger_normalize_statement_month(raw)
                elif k == "excluded_from_calculations":
                    if raw is True:
                        sql_updates[k] = 1
                    elif raw is False:
                        sql_updates[k] = 0
                    elif isinstance(raw, int) and raw in (0, 1):
                        sql_updates[k] = raw
                    elif isinstance(raw, str):
                        ls = raw.strip().lower()
                        if ls in ("0", "false", "no"):
                            sql_updates[k] = 0
                        elif ls in ("1", "true", "yes"):
                            sql_updates[k] = 1
                        else:
                            raise ValueError("excluded_from_calculations must be 0 or 1")
                    else:
                        raise ValueError("excluded_from_calculations must be 0 or 1")
        except ValueError as e:
            return {"ok": False, "error": "validation_error", "message": str(e)}

        new_fp: str | None = None
        if fp_keys:
            try:
                for k in fp_keys:
                    raw = patch[k]
                    if k == "תאריך":
                        basis[k] = _ledger_patch_normalize_date(raw)
                    elif k in ("בחובה", "בזכות"):
                        basis[k] = _ledger_patch_coerce_amount(raw, label=k)
                    elif k in ("מקור עסקה", "פירוט נוסף", "תאור מורחב"):
                        t = _ledger_normalize_optional_text(raw, empty_as_none=False)
                        basis[k] = "" if t is None else t
            except ValueError as e:
                return {"ok": False, "error": "validation_error", "message": str(e)}
            fp_series = pd.Series(basis)
            computed = generate_transaction_fingerprint(fp_series)
            if computed is None or not str(computed).strip():
                return {
                    "ok": False,
                    "error": "validation_error",
                    "message": "Cannot compute fingerprint from updated fields (check תאריך and amounts).",
                }
            new_fp = str(computed).strip()

            conflict = conn.execute(
                """
                SELECT id FROM ledger_transaction
                WHERE fingerprint IS NOT NULL AND TRIM(fingerprint) = ?
                  AND id != ?
                """,
                (new_fp, int(row_id)),
            ).fetchone()
            if conflict is not None:
                return {
                    "ok": False,
                    "error": "fingerprint_conflict",
                    "message": "Another row already has this fingerprint.",
                    "conflicting_id": int(conflict["id"]),
                }

            sql_updates.update(
                {
                    "תאריך": basis["תאריך"],
                    "בחובה": basis["בחובה"],
                    "בזכות": basis["בזכות"],
                    "מקור עסקה": basis["מקור עסקה"],
                    "פירוט נוסף": basis["פירוט נוסף"],
                    "תאור מורחב": basis["תאור מורחב"],
                    "fingerprint": new_fp,
                }
            )

        if not sql_updates:
            return {"ok": False, "error": "validation_error", "message": "No valid fields to update"}

        assignments = [f"{_ledger_patch_sql_column(k)} = ?" for k in sql_updates]
        values = list(sql_updates.values())
        values.append(int(row_id))
        sql = f"UPDATE ledger_transaction SET {', '.join(assignments)} WHERE id = ?"
        try:
            conn.execute(sql, values)
            conn.commit()
        except sqlite3.IntegrityError as e:
            conn.rollback()
            return {
                "ok": False,
                "error": "fingerprint_conflict",
                "message": str(e) or "UNIQUE fingerprint violation",
            }

        out: dict[str, Any] = {"ok": True}
        if new_fp is not None:
            out["fingerprint"] = new_fp
        return out
    finally:
        conn.close()


# --- DataFrame load/export (ex-ledger_dataframe) ---

# DB columns only (no ``מזהה עסקה`` / ``תאריך עדכון`` — dedupe and ingestion use ``fingerprint`` + ``ingested_at``).
# Only rows included in aggregates (``excluded_from_calculations = 0``).
_LEDGER_TX_READ_BODY = f"""
SELECT
    id,
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
    ingested_at,
    category_updated_at,
    data_updated_at
FROM ledger_transaction
WHERE {LEDGER_SQL_TX_INCLUDED}
"""


_LEDGER_TX_READ_SQL = (
    _LEDGER_TX_READ_BODY.strip()
    + f"\nORDER BY ({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR}), id\n"
)


def read_transactions_dataframe_from_ledger(db_path: str) -> pd.DataFrame:
    """Read all ledger rows without running migrations (schema must already match)."""
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(_LEDGER_TX_READ_SQL, conn)
    finally:
        conn.close()

    if not df.empty:
        if "id" in df.columns:
            df["id"] = pd.to_numeric(df["id"], errors="coerce").astype("Int64")
        if "קטגוריה" in df.columns:
            df["קטגוריה"] = df["קטגוריה"].map(lambda x: "" if pd.isna(x) else str(x)).astype(object)
        if "fingerprint" in df.columns:
            df["fingerprint"] = df["fingerprint"].map(lambda x: "" if pd.isna(x) else str(x)).astype(object)
    return df


def load_transactions_dataframe_from_ledger(db_path: str) -> pd.DataFrame:
    """Return all ledger rows as a DataFrame (empty table → empty frame with expected columns)."""
    migrate_ledger_db(db_path)
    return read_transactions_dataframe_from_ledger(db_path)


_SQL_NEEDS_MANUAL_CATEGORY = """(
    "קטגוריה" IS NULL
    OR TRIM(COALESCE("קטגוריה", '')) = ''
    OR LOWER(TRIM("קטגוריה")) = 'awaiting'
)"""

# Same predicate for ``ledger_transaction AS lt`` (native auto-categorize UPDATE).
_SQL_LT_NEEDS_MANUAL_CATEGORY = """(
    lt."קטגוריה" IS NULL
    OR TRIM(COALESCE(lt."קטגוריה", '')) = ''
    OR LOWER(TRIM(lt."קטגוריה")) = 'awaiting'
)"""


def apply_auto_categories_from_static_stores_sql(db_path: str) -> int:
    """
    Auto pass: set ``קטגוריה`` from ``store`` / ``store_category`` where ``is_static = 1``.

    Only updates rows that still need a manual category (empty / awaiting) and have a
    fingerprint. One ``UPDATE`` — same outcome as scanning ``stores_df`` in Python.
    """
    migrate_ledger_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        sql = f"""
        UPDATE ledger_transaction AS lt
        SET "קטגוריה" = (
            SELECT sc.category
            FROM store AS s
            INNER JOIN store_category AS sc ON sc.store_name = s.store_name
            WHERE s.store_name = lt."מקור עסקה"
              AND s.is_static = 1
            LIMIT 1
        )
        WHERE lt."fingerprint" IS NOT NULL AND TRIM(lt."fingerprint") != ''
          AND {_SQL_LT_NEEDS_MANUAL_CATEGORY}
          AND {LEDGER_SQL_LT_TX_INCLUDED}
          AND EXISTS (
            SELECT 1
            FROM store AS s
            INNER JOIN store_category AS sc ON sc.store_name = s.store_name
            WHERE s.store_name = lt."מקור עסקה"
              AND s.is_static = 1
          )
        """
        conn.execute(sql)
        n = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
        return int(n)
    finally:
        conn.close()


def forward_fill_uncategorized_for_static_stores_sql(db_path: str) -> int:
    """
    Bulk-apply static store categories to **uncategorized** ledger rows only.

    Same implementation as :func:`apply_auto_categories_from_static_stores_sql` — rows that
    already have a non-empty category (not ``awaiting``) are never overwritten.
    """
    return apply_auto_categories_from_static_stores_sql(db_path)


def forward_fill_uncategorized_for_store_if_static_sql(db_path: str, store_name: str) -> int:
    """
    After ``is_static = 1`` is saved for ``store_name``, fill ``קטגוריה`` on other ledger rows
    for that store **only** where the category is still empty / ``awaiting``. Never overwrites
    an already-assigned category.
    """
    migrate_ledger_db(db_path)
    sn = str(store_name or "").strip()
    if not sn:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        row = conn.execute(
            "SELECT 1 FROM store WHERE store_name = ? AND is_static = 1 LIMIT 1",
            (sn,),
        ).fetchone()
        if row is None:
            return 0
        sql = f"""
        UPDATE ledger_transaction AS lt
        SET "קטגוריה" = (
            SELECT sc.category
            FROM store AS s
            INNER JOIN store_category AS sc ON sc.store_name = s.store_name
            WHERE s.store_name = lt."מקור עסקה"
              AND s.is_static = 1
            LIMIT 1
        )
        WHERE lt."מקור עסקה" = ?
          AND lt."fingerprint" IS NOT NULL AND TRIM(lt."fingerprint") != ''
          AND {_SQL_LT_NEEDS_MANUAL_CATEGORY}
          AND {LEDGER_SQL_LT_TX_INCLUDED}
          AND EXISTS (
            SELECT 1
            FROM store AS s
            INNER JOIN store_category AS sc ON sc.store_name = s.store_name
            WHERE s.store_name = lt."מקור עסקה"
              AND s.is_static = 1
          )
        """
        conn.execute(sql, (sn,))
        n = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
        return int(n)
    finally:
        conn.close()


def count_transactions_needing_manual_category(db_path: str) -> int:
    """Rows with empty / awaiting category and a usable fingerprint (manual categorization queue size)."""
    migrate_ledger_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        n = conn.execute(
            f"""
            SELECT COUNT(*) FROM ledger_transaction
            WHERE "fingerprint" IS NOT NULL AND TRIM("fingerprint") != ''
              AND {_SQL_NEEDS_MANUAL_CATEGORY}
              AND {LEDGER_SQL_TX_INCLUDED}
            """
        ).fetchone()[0]
    finally:
        conn.close()
    return int(n)


def count_ledger_transaction_rows(db_path: str) -> int:
    """Total rows in ``ledger_transaction`` (for logging / progress without loading a dataframe)."""
    migrate_ledger_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM ledger_transaction").fetchone()[0]
    finally:
        conn.close()
    return int(n)


def load_first_transaction_needing_manual_category(db_path: str) -> pd.Series | None:
    """Next row for manual categorization (``ORDER BY תאריך, id``)."""
    migrate_ledger_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        base = _LEDGER_TX_READ_BODY.strip()
        q = f"""
        {base}
          AND "fingerprint" IS NOT NULL AND TRIM("fingerprint") != ''
          AND {_SQL_NEEDS_MANUAL_CATEGORY}
        ORDER BY "תאריך", id
        LIMIT 1
        """
        df = pd.read_sql_query(q, conn)
    finally:
        conn.close()
    if df.empty:
        return None
    row = df.iloc[0]
    if "קטגוריה" in row.index:
        row["קטגוריה"] = "" if pd.isna(row["קטגוריה"]) else str(row["קטגוריה"])
    if "fingerprint" in row.index:
        row["fingerprint"] = "" if pd.isna(row["fingerprint"]) else str(row["fingerprint"])
    return row


def load_ledger_transaction_by_stable_id(db_path: str, stable_id: str) -> pd.Series | None:
    """Load one row by ``fingerprint`` (same string as :func:`stable_transaction_key`)."""
    sid = str(stable_id or "").strip()
    if not sid:
        return None
    migrate_ledger_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        base = _LEDGER_TX_READ_BODY.strip()
        q = f"""
        {base}
          AND "fingerprint" IS NOT NULL AND TRIM("fingerprint") = TRIM(?)
        LIMIT 1
        """
        df = pd.read_sql_query(q, conn, params=(sid,))
    finally:
        conn.close()
    if df.empty:
        return None
    row = df.iloc[0]
    if "קטגוריה" in row.index:
        row["קטגוריה"] = "" if pd.isna(row["קטגוריה"]) else str(row["קטגוריה"])
    if "fingerprint" in row.index:
        row["fingerprint"] = "" if pd.isna(row["fingerprint"]) else str(row["fingerprint"])
    return row


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
            f"""
            SELECT
                NULLIF(TRIM("fingerprint"), '') AS transaction_id,
                "קטגוריה" AS category
            FROM ledger_transaction
            WHERE "fingerprint" IS NOT NULL AND TRIM("fingerprint") != ''
              AND {LEDGER_SQL_TX_INCLUDED}
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


def _ledger_nonempty_text(val: object) -> bool:
    if val is None:
        return False
    try:
        if pd.isna(val):
            return False
    except TypeError:
        pass
    return bool(str(val).strip())


def _ledger_pick_first_nonempty(current: object, pool: list[object]) -> object:
    if _ledger_nonempty_text(current):
        return current
    for v in pool:
        if _ledger_nonempty_text(v):
            return v
    return current


def _merge_survivor_ledger_row(survivor: sqlite3.Row, losers: list[sqlite3.Row]) -> dict[str, Any]:
    """Fields to SET on ``survivor`` when collapsing duplicate fingerprints (non-conflicting merge)."""
    out: dict[str, Any] = {}
    for col in ("קטגוריה", "notes", "statement_month"):
        cur = survivor[col]
        pool = [L[col] for L in losers]
        pick = _ledger_pick_first_nonempty(cur, pool)
        if pick != cur:
            out[col] = pick
    sur_ing = survivor["ingested_at"]
    best = sur_ing
    for L in losers:
        li = L["ingested_at"]
        if not li or not str(li).strip():
            continue
        bs = str(sur_ing).strip() if sur_ing else ""
        lis = str(li).strip()
        if not bs or lis > bs:
            best = li
    if best != sur_ing:
        out["ingested_at"] = best
    return out


def _migrate_fingerprint_optional_text_normalize(conn: sqlite3.Connection) -> None:
    """
    Recompute ``fingerprint`` using :func:`generate_transaction_fingerprint` (v11 optional-field rules).

    Rows that map to the same new fingerprint are merged: lowest ``id`` survives; category / notes /
    statement_month / ``ingested_at`` are filled from the others when the survivor's values are empty.
    """
    _run_ledger_fingerprint_recompute_with_row_factory(conn, log_label="v11")


def _migrate_fingerprint_iso_date_parse(conn: sqlite3.Connection) -> None:
    """
    Recompute fingerprints after fixing ISO ``YYYY-MM-DD`` handling in :func:`generate_transaction_fingerprint`
    (must match :func:`parse_post_ingest_date_scalar`). Merges duplicate rows like v11.
    """
    _run_ledger_fingerprint_recompute_with_row_factory(conn, log_label="v12")


def _run_ledger_fingerprint_recompute_with_row_factory(conn: sqlite3.Connection, *, log_label: str) -> None:
    prev_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        _recompute_ledger_fingerprints_merge_duplicates(conn, log_label=log_label)
    finally:
        conn.row_factory = prev_factory


def _recompute_ledger_fingerprints_merge_duplicates(conn: sqlite3.Connection, *, log_label: str) -> None:
    cur = conn.execute(
        f"""
        SELECT id, {", ".join(f'"{k}"' for k in _ROW_KEYS)},
               "fingerprint", "קטגוריה", notes, statement_month, ingested_at
        FROM ledger_transaction
        ORDER BY id
        """
    )
    rows = cur.fetchall()
    if not rows:
        return

    id_to_row = {int(r["id"]): r for r in rows}
    id_to_fp: dict[int, str] = {}
    skipped = 0
    for r in rows:
        rid = int(r["id"])
        s = pd.Series({k: r[k] for k in _ROW_KEYS})
        nf = generate_transaction_fingerprint(s)
        if nf is None or not str(nf).strip():
            skipped += 1
            continue
        id_to_fp[rid] = str(nf).strip()

    if skipped:
        log.warning(
            "fingerprint %s migration: %s row(s) skipped (uncomputable fingerprint; left unchanged)",
            log_label,
            skipped,
        )

    by_fp: dict[str, list[int]] = defaultdict(list)
    for rid, fp in id_to_fp.items():
        by_fp[fp].append(rid)

    updated = 0
    deleted = 0
    for fp, ids in by_fp.items():
        ids.sort()
        if len(ids) == 1:
            rid = ids[0]
            old = id_to_row[rid]["fingerprint"]
            old_s = None if old is None else str(old).strip()
            if old_s != fp:
                conn.execute(
                    "UPDATE ledger_transaction SET fingerprint = ? WHERE id = ?",
                    (fp, rid),
                )
                updated += 1
            continue

        survivor_id = ids[0]
        loser_ids = ids[1:]
        surv = id_to_row[survivor_id]
        losers = [id_to_row[i] for i in loser_ids]
        merged = _merge_survivor_ledger_row(surv, losers)
        if merged:
            cols = ", ".join(f'"{k}" = ?' for k in merged)
            vals = list(merged.values()) + [survivor_id]
            conn.execute(f"UPDATE ledger_transaction SET {cols} WHERE id = ?", vals)
        conn.execute(
            f"DELETE FROM ledger_transaction WHERE id IN ({','.join('?' * len(loser_ids))})",
            loser_ids,
        )
        deleted += len(loser_ids)
        conn.execute(
            "UPDATE ledger_transaction SET fingerprint = ? WHERE id = ?",
            (fp, survivor_id),
        )
        updated += 1

    if updated or deleted:
        log.info(
            "fingerprint %s migration: %s fingerprint row(s) updated, %s duplicate row(s) removed",
            log_label,
            updated,
            deleted,
        )


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


# --- Row helpers for compile upsert + static store mappings ---


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


# Static store tables
_STORE_COLS = ["store_name", "category", "is_static"]


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


def import_stores_to_ledger(
    csv_path: str | None = None,
    db_path: str | None = None,
    *,
    replace: bool = True,
) -> dict[str, Any]:
    """
    Load store/category mappings into the ledger DB.

    Runs ``migrate_ledger_db`` first. If ``replace`` is True, deletes all ``store`` rows
    (``store_category`` cascades) before insert.
    """
    path = csv_path if csv_path is not None else config.stores_to_categories_file
    db = db_path if db_path is not None else config.ledger_db_file
    migrate_ledger_db(db)

    warnings: list[str] = []
    df = load_stores_to_categories_dataframe(path)

    if df.empty:
        conn = sqlite3.connect(db)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            if replace:
                conn.execute("DELETE FROM store")
                conn.commit()
        finally:
            conn.close()
        return {
            "ok": True,
            "csv_path": path,
            "db_path": db,
            "stores_inserted": 0,
            "store_category_rows_inserted": 0,
            "stores_forced_dynamic": 0,
            "warnings": ["No store/category rows after filter"],
        }

    store_rows: list[tuple[str, int]] = []
    forced_dynamic = 0
    for store_name, sub in df.groupby("store_name", sort=True):
        cats = sub["category"].unique()
        if len(cats) > 1:
            static = 0
            if (sub["is_static"].map(_coerce_is_static) == 1).any():
                forced_dynamic += 1
        else:
            static = _coerce_is_static(sub["is_static"].iloc[0])
        store_rows.append((str(store_name), static))

    sc_tuples = [(str(r["store_name"]), str(r["category"])) for _, r in df.iterrows()]

    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if replace:
            conn.execute("DELETE FROM store")
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
        n_store = conn.execute("SELECT COUNT(*) FROM store").fetchone()[0]
        n_sc = conn.execute("SELECT COUNT(*) FROM store_category").fetchone()[0]
    finally:
        conn.close()

    if forced_dynamic:
        warnings.append(
            f"{forced_dynamic} store(s) had is_static=1 on some row but multiple categories; "
            "set to dynamic (is_static=0)."
        )

    log.info("static import: %s stores, %s store_category → %s", n_store, n_sc, db)
    return {
        "ok": True,
        "csv_path": path,
        "db_path": db,
        "stores_inserted": int(n_store),
        "store_category_rows_inserted": int(n_sc),
        "stores_forced_dynamic": forced_dynamic,
        "warnings": warnings,
    }


def sync_stores_to_ledger_from_dataframe(db_path: str, df: pd.DataFrame) -> None:
    """
    Replace ``store`` / ``store_category`` from an in-memory frame (same columns as the CSV).
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


def dedupe_import_batch_by_fingerprint(df: pd.DataFrame) -> pd.DataFrame:
    """
    When the same import batch contains duplicate ``fingerprint`` values, keep one row per
    fingerprint preferring a non-empty ``קטגוריה`` (same rule as legacy ``compile_to_main``).

    Does not load the ledger — caller runs this on **new** rows only before ``executemany``.
    """
    if df.empty or "fingerprint" not in df.columns:
        return df
    work = df.copy()
    if "קטגוריה" not in work.columns:
        work["קטגוריה"] = ""
    work["קטגוריה"] = work["קטגוריה"].astype(object).fillna("")
    work["_sort_key"] = work["קטגוריה"].apply(lambda x: 0 if str(x).strip() != "" else 1)
    work.sort_values(by=["fingerprint", "_sort_key"], ascending=[True, True], inplace=True)
    work = work.drop_duplicates(subset=["fingerprint"], keep="first")
    work.drop(columns=["_sort_key"], inplace=True)
    work.reset_index(drop=True, inplace=True)
    return work


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
    ing = ingested_at_for_new_ledger_row()
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

    New rows get ``ingested_at`` = local insert date (:func:`pipeline.ingested_at_rules.ingested_at_for_new_ledger_row`).
    On conflict, existing ``ingested_at`` is preserved (first insert wins).

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
