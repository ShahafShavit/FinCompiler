# Ledger SQLite schema

**Authoritative DDL:** [`full_schema.sql`](full_schema.sql).

## SQLite does not have a `DATE` or `TIMESTAMP` type

From the [SQLite docs](https://www.sqlite.org/lang_datefunc.html): date/time values are stored as **TEXT** (ISO 8601), **REAL** (Julian day), or **INTEGER** (Unix time). There is no separate `DATE` column type in the engine.

This schema uses **ISO 8601 TEXT** on purpose:

| Role | Type | Format |
|------|------|--------|
| Calendar dates | `TEXT` + `CHECK (date(col) = col)` | `YYYY-MM-DD` (canonical; rejects junk) |
| Statement month | `TEXT` + `CHECK` | `YYYY-MM` |
| Event / audit times | `TEXT` + `CHECK (datetime(col) IS NOT NULL)` | parseable by SQLite `datetime()` (typically `YYYY-MM-DD HH:MM:SS`) |
| Money | `REAL` | `"בחובה"`, `"בזכות"` |

**Ingestion must normalize** bank/CSV values into these forms.

### `ingested_at` (schema v8+)

Set by application code on **first insert** (not a trigger). Rules live in `pipeline/ingested_at_rules.py`:

- If the **source row** still carries a bank column historically named **תאריך עדכון** and it is non-empty, that value is parsed and used **only** to choose the calendar date stored as **`ingested_at`**.
- Otherwise: **15th** of the transaction month when `day(תאריך) <= 15`, else **15th of the following month**.

**There is no `תאריך עדכון` column on `ledger_transaction`.** Do not confuse the legacy CSV/Sheets header with a database field.

**`statement_month`** is filled by a separate pipeline later (nullable).

**Upgrading from v6/v7:** There is no in-place migration for very old files — **delete** `ledger.sqlite` (or your `FINANCE_WORKSPACE_ROOT` copy) and run `migrate_ledger_db` + imports again when docs say so; see `pipeline/ledger.py`.

**Clock:** Category/data `*_updated_at` triggers use **`datetime('now', 'localtime')`**.

## Identity

- **`fingerprint`** is the **canonical** dedupe key (`UNIQUE` when non-NULL). It is produced by `pipeline/fingerprint.generate_transaction_fingerprint` and encodes **both** `בחובה` and `בזכות`, so paired same-day opposite flows do not collide. It may be **NULL** until filled by the compile path or by calling helpers in `pipeline.ledger` (e.g. `backfill_null_fingerprints`) / ad-hoc SQL against your DB.
- **Legacy CSV / Excel** sometimes include **מזהה עסקה** (a per-row content hash). That value is **not** stored on `ledger_transaction`. Application code prefers **`fingerprint`**, then legacy hash where present.
- There is **no** separate fingerprint-metadata table.

## `store` / `store_category` (may be deprecated later)

- **`store`**: one row per `store_name`, **`is_static`** as boolean semantics (`INTEGER` `0`/`1`).
- **`store_category`**: `(store_name, category)`; **FK** to `store`. Triggers enforce static vs dynamic rules (see `full_schema.sql` comments).

## Other

- **`notes`**: optional user/app text; bumps `data_updated_at`.
- **`STRICT`** (3.37+): declared types are enforced.
- **Holdings:** `holdings_balance (as_of_date, activity_type, balance_ils)`.

**Apply:** `sqlite3 path/to/ledger.sqlite < full_schema.sql` or `executescript()` with UTF-8.
