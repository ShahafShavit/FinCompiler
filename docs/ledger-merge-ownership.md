# Ledger merge ownership (MIG-E1 / MIG-F3)

Concise rules for **`ledger_transaction` upserts** keyed by **`fingerprint`**, aligned with `docs/data-storage-and-pipeline-evaluation.md` Section 13.1 and Section 13.9.

## Legacy CSV headers vs ledger columns (avoid confusion)

Some exports still include Hebrew columns that **do not exist** on `ledger_transaction`:

| Name | Role |
|------|------|
| **מזהה עסקה** | Legacy bank / Sheets column (row hash). **Not** stored in SQLite; **stripped** on workbook normalize. **Never** use it as `fingerprint` or as a merge key into the ledger. |
| **תאריך עדכון** | Not a ledger column. First-insert `ingested_at` is set by `ledger.store.ingested_at_for_new_ledger_row()` (local calendar date). |

Canonical reference: `schema/ledger/README.md` and comments in `schema/ledger/full_schema.sql`.

## Pipeline-owned (always refreshed from compile output on conflict)

These come from bank exports / compile merge and may change when the institution restates data:

| Column | Role |
|--------|------|
| `תאריך` | Transaction date (ISO `YYYY-MM-DD` text) |
| `בחובה`, `בזכות` | Amounts |
| `מקור עסקה`, `פירוט נוסף`, `תאור מורחב`, `4 ספרות` | Merchant / description fields |

## User-owned (do not silently overwrite)

| Column | Rule on `ON CONFLICT(fingerprint)` |
|--------|-------------------------------------|
| `קטגוריה` | If the existing row has a **non-empty** category after trim, **keep it**. Otherwise take the value from the compile row (e.g. newly categorized or still empty). |
| `notes` | Same as category: **preserve** non-empty existing text; otherwise accept pipeline/import. |

## System / first-ingest

| Column | Rule |
|--------|------|
| `ingested_at` | Set on **first insert** via `ledger.store.ingested_at_for_new_ledger_row`. On **update**, **never** change an existing `ingested_at`. |
| `statement_month` | Prefer **existing** when present; otherwise set from incoming. |
| `category_updated_at`, `data_updated_at` | Maintained by DDL triggers — pipeline upserts must **not** assign fake user-edit times. Updating only bank fields must **not** bump `category_updated_at`. |

## Rows without `fingerprint`

Compile rows with missing or empty fingerprint are **not** upserted (v8 allows NULL fingerprints for legacy rows; compile path only syncs real keys).

## Implementation

- `ledger.store.upsert_compiled_dataframe_to_ledger` — merge rules above.
- Transactions compile: `pipeline.compile_transactions` → upsert into `config.ledger_db_file`.
- Covered by `tests/test_ledger_compile_upsert.py` (`test_f3_upsert_preserves_user_category_and_timestamps`).
