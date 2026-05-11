-- =============================================================================
-- FinCompiler — canonical SQLite ledger (full DDL)
-- =============================================================================
-- Single file database: ledger + static mappings + holdings.
-- Aligns with docs/data-storage-and-pipeline-evaluation.md (Sections 13.3, 13.9, 13.10)
-- and current pipeline column names (pipeline/compiler.py, pipeline/workbook_normalize.py).
--
-- Apply with: sqlite3 path/to/ledger.sqlite < full_schema.sql
-- Or from Python: execute script in order after creating an empty file.
--
-- Hebrew **names** stay aligned with compiled.csv. **SQLite has no DATE / TIMESTAMP
-- types** — the portable model is ISO 8601 **TEXT**: `YYYY-MM-DD` for dates,
-- `YYYY-MM-DD HH:MM:SS` for datetimes (see https://www.sqlite.org/lang_datefunc.html).
-- Those work with date(), datetime(), time(), comparisons, and lexicographic sort.
-- STRICT still applies: REAL money, TEXT temporal, INTEGER only for flags/bools.
-- Ingestion must normalize into these forms — that is the contract.
-- =============================================================================

PRAGMA foreign_keys = ON;
-- Recursive triggers default OFF; keep OFF so AFTER UPDATE timestamp patches
-- do not recurse infinitely.

-- -----------------------------------------------------------------------------
-- Migration bookkeeping (hand-rolled migrations may also insert rows here)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (3, 'iso8601_text_dates_and_datetimes');
INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (4, 'ledger_add_notes_column');
INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (5, 'fk_fingerprint_meta_store_model_localtime');
INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (6, 'drop_fingerprint_metadata_and_row_hash');
INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (7, 'ledger_v7_ingested_at_only_drop_taarich_hidon_first_seen');
INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (8, 'ledger_fingerprint_nullable_no_row_hash_fallback');
INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (9, 'ledger_fingerprint_v2_unique_drop_fingerprint_unique');
INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (10, 'ledger_single_fingerprint_column_v2_semantics');
INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (11, 'fingerprint_optional_text_normalize');
INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (12, 'fingerprint_iso_date_parse_match_compiler');
INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (13, 'drop_similar_category_pair');
INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (14, 'ledger_excluded_from_calculations');
INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (15, 'add_trade_portfolio_position');

-- -----------------------------------------------------------------------------
-- Transaction ledger (dedupe key = fingerprint — encodes both debit and credit columns)
-- -----------------------------------------------------------------------------
-- **ingested_at** — only persisted “ingestion / statement timing” field. A source column sometimes
--   labeled **תאריך עדכון** on CSV/Sheets is mapped into **ingested_at** on INSERT only; it is not a column here.
-- **מזהה עסקה** — legacy CSV row hash; **not** stored — identity is **fingerprint** — see schema/ledger/README.md.
-- No INSERT trigger for ingested_at — application supplies values.

CREATE TABLE IF NOT EXISTS ledger_transaction (
    id    INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,

    -- Calendar dates: ISO 8601 date only (canonical: date(x) = x)
    "תאריך"          TEXT CHECK ("תאריך" IS NULL OR date("תאריך") = "תאריך"),
    "בחובה"          REAL NOT NULL DEFAULT 0,
    "בזכות"          REAL NOT NULL DEFAULT 0,
    "מקור עסקה"      TEXT,
    "פירוט נוסף"     TEXT,
    "תאור מורחב"     TEXT,
    "4 ספרות"        TEXT,
    -- Dedupe key: encodes both debit/credit columns (see pipeline.fingerprint.generate_transaction_fingerprint)
    "fingerprint"    TEXT,
    "קטגוריה"        TEXT,
    notes              TEXT,

    -- Statement period label: YYYY-MM (filled by a later pipeline)
    statement_month    TEXT CHECK (
        statement_month IS NULL
        OR (length(statement_month) = 7 AND date(statement_month || '-01') IS NOT NULL AND strftime('%Y-%m', statement_month || '-01') = statement_month)
    ),

    ingested_at           TEXT NOT NULL CHECK (date(ingested_at) = ingested_at),
    category_updated_at   TEXT CHECK (category_updated_at IS NULL OR datetime(category_updated_at) IS NOT NULL),
    data_updated_at       TEXT CHECK (data_updated_at IS NULL OR datetime(data_updated_at) IS NOT NULL),

    -- 1 = omitted from heatmap, dashboard aggregates, categorize queue, integrity anomaly scans
    excluded_from_calculations INTEGER NOT NULL DEFAULT 0 CHECK (excluded_from_calculations IN (0, 1)),

    CHECK (fingerprint IS NULL OR LENGTH(TRIM(fingerprint)) > 0),
    UNIQUE ("fingerprint")
) STRICT;

CREATE INDEX IF NOT EXISTS idx_ledger_transaction_date ON ledger_transaction ("תאריך");
CREATE INDEX IF NOT EXISTS idx_ledger_transaction_category ON ledger_transaction ("קטגוריה");

-- -----------------------------------------------------------------------------
-- Store → categories (replaces data/static/stores_to_categories.csv)
-- -----------------------------------------------------------------------------
-- DEPRECATION: May be replaced or merged with a unified mapping layer later.
--
-- Model: **is_static** is a property of the **store** (boolean 0/1; SQLite has no
-- BOOL type). Static store: **at most one** category row. Dynamic store: multiple
-- category rows allowed. Enforced with triggers (see below).
-- Import: upsert `store` rows first (one row per distinct store_name + is_static),
-- then `store_category` pairs. Conflicting is_static for the same store_name must
-- be resolved in the import script.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS store (
    store_name TEXT NOT NULL PRIMARY KEY,
    -- Boolean: 0 = dynamic (many categories), 1 = static (≤1 category row)
    is_static  INTEGER NOT NULL CHECK (is_static IN (0, 1))
) STRICT;

CREATE TABLE IF NOT EXISTS store_category (
    store_name TEXT NOT NULL,
    category   TEXT NOT NULL,
    PRIMARY KEY (store_name, category),
    FOREIGN KEY (store_name) REFERENCES store (store_name) ON DELETE CASCADE ON UPDATE CASCADE
) STRICT;

CREATE INDEX IF NOT EXISTS idx_store_category_category ON store_category (category);

-- Static store: at most one mapping row
CREATE TRIGGER IF NOT EXISTS tr_store_category_before_insert_static_limit
BEFORE INSERT ON store_category
FOR EACH ROW
WHEN (SELECT is_static FROM store WHERE store_name = NEW.store_name) = 1
 AND (SELECT COUNT(*) FROM store_category WHERE store_name = NEW.store_name) >= 1
BEGIN
    SELECT RAISE(ABORT, 'static store allows at most one category; delete or change store.is_static first');
END;

-- Cannot flip store to static while multiple categories exist
CREATE TRIGGER IF NOT EXISTS tr_store_before_update_static_requires_single_category
BEFORE UPDATE OF is_static ON store
FOR EACH ROW
WHEN NEW.is_static = 1 AND OLD.is_static = 0
 AND (SELECT COUNT(*) FROM store_category WHERE store_name = OLD.store_name) > 1
BEGIN
    SELECT RAISE(ABORT, 'cannot set is_static: store has multiple categories — reduce to one first');
END;

-- -----------------------------------------------------------------------------
-- Holdings (replaces compiled holdings.csv wide layout)
-- -----------------------------------------------------------------------------
-- The pipeline builds a **wide** CSV: one row per `תאריך`, columns = activity types
-- from the bank pivot (`סוג פעילות` values) plus balances. Column names can change
-- when the export adds new activity types, so a fixed multi-column CREATE TABLE
-- would require migrations for every new header.
--
-- Stored relationally instead of JSON: **one row per (as-of date, balance column)**.
-- Import: melt/unpivot the wide CSV in Python. Export to CSV: pivot back to the
-- same shape your app already uses. This keeps SQL queryable (`SUM`, filters per
-- activity_type) without `json_extract`.
--
-- If you ever freeze a permanent set of columns and want a true wide table
-- mirroring CSV 1:1, add a follow-up migration with explicit REAL columns.
-- -----------------------------------------------------------------------------
-- Row identity / dedupe key: (as_of_date, activity_type) — pipeline upserts via INSERT OR REPLACE.
CREATE TABLE IF NOT EXISTS holdings_balance (
    as_of_date    TEXT NOT NULL CHECK (date(as_of_date) = as_of_date),
    activity_type TEXT NOT NULL,
    balance_ils   REAL NOT NULL,
    PRIMARY KEY (as_of_date, activity_type)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_holdings_balance_date ON holdings_balance (as_of_date);
CREATE INDEX IF NOT EXISTS idx_holdings_balance_type ON holdings_balance (activity_type);

-- -----------------------------------------------------------------------------
-- Trade portfolio (securities snapshot exports; often SpreadsheetML .xls)
-- -----------------------------------------------------------------------------
-- One row per position per snapshot. Percents stored as fractions (e.g. 0.0033).
-- Identity: (snapshot_date, portfolio_account, security_number).
CREATE TABLE IF NOT EXISTS trade_portfolio_position (
    snapshot_date       TEXT NOT NULL CHECK (date(snapshot_date) = snapshot_date),
    portfolio_account   TEXT NOT NULL,
    security_number     TEXT NOT NULL,
    security_name       TEXT,
    avg_purchase_price  REAL,
    quantity            REAL,
    last_price          REAL,
    value_ils           REAL,
    daily_change_pct    REAL,
    profit_pct          REAL,
    profit_ils          REAL,
    pct_of_portfolio    REAL,
    basis_price         REAL,
    imported_at         TEXT NOT NULL CHECK (datetime(imported_at) IS NOT NULL),
    PRIMARY KEY (snapshot_date, portfolio_account, security_number)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_trade_portfolio_snapshot ON trade_portfolio_position (snapshot_date);
CREATE INDEX IF NOT EXISTS idx_trade_portfolio_account ON trade_portfolio_position (portfolio_account);

-- =============================================================================
-- Triggers: timestamps (evaluation Section 13.9)
-- =============================================================================
-- ingested_at is **not** auto-filled — import/pipeline set it explicitly.

CREATE TRIGGER IF NOT EXISTS tr_ledger_transaction_touch_category_updated_at
AFTER UPDATE ON ledger_transaction
FOR EACH ROW
WHEN NEW."קטגוריה" IS DISTINCT FROM OLD."קטגוריה"
 AND NEW.category_updated_at IS NOT DISTINCT FROM OLD.category_updated_at
BEGIN
    UPDATE ledger_transaction
    SET category_updated_at = datetime('now', 'localtime')
    WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS tr_ledger_transaction_touch_data_updated_at
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

-- If one UPDATE changes both category and data columns, both triggers may run; acceptable.

-- =============================================================================
-- Views (convenience)
-- =============================================================================

CREATE VIEW IF NOT EXISTS v_ledger_uncategorized AS
SELECT *
FROM ledger_transaction
WHERE ("קטגוריה" IS NULL OR TRIM(COALESCE("קטגוריה", '')) = '')
  AND COALESCE(excluded_from_calculations, 0) = 0;
