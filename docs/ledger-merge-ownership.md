# Ledger merge ownership (MIG-E1)

Concise rules for **`ledger_transaction` upserts** keyed by **`fingerprint`**, aligned with `docs/data-storage-and-pipeline-evaluation.md` Section 13.1 and Section 13.9.

## Legacy CSV headers vs ledger columns (avoid confusion)

Some exports still include Hebrew columns that **do not exist** on `ledger_transaction`:

| Name | Role |
|------|------|
| **מזהה עסקה** | Legacy **row hash** used in older CSV/Sheets workflows. **Not** stored in SQLite. **Never** use it as `fingerprint` or as a merge key into the ledger. |
| **תאריך עדכון** | Optional **source** field for **computing** `ingested_at` on **first insert** only (`pipeline/ingested_at_rules.py`). It is **not** a column on the ledger; only **`ingested_at`** is stored. |

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
| `ingested_at` | Set on **first insert** from `pipeline.ingested_at_rules.compute_ingested_at_iso`. On **update**, **never** change an existing `ingested_at` (first appearance in the ledger stays fixed). |
| `statement_month` | Prefer **existing** when present; otherwise set from incoming (compile may leave NULL until a later step). |
| `category_updated_at`, `data_updated_at` | Maintained by DDL triggers in `schema/ledger/full_schema.sql` when relevant columns change — pipeline code must not assign fake “user edit” times. |

## Rows without `fingerprint`

Compile rows with missing or empty fingerprint are **not** upserted (v8 allows NULL fingerprints for legacy rows; compile path only syncs real keys).

## Implementation

- `pipeline/ledger.py` (`upsert_compiled_dataframe_to_ledger`) — implements the merge rules above for compile output.
- `pipeline.compile_transactions_main` always constructs `Compiler(..., ledger_db=config.ledger_db_file)`; `Compiler.save_main` calls `upsert_compiled_dataframe_to_ledger` (there is **no** `upsert_ledger` flag on `compile_transactions_main`). `pipeline/compiler.py` `update_fingerprint_db` does not run when `ledger_db` is set — category truth is on `ledger_transaction`.
