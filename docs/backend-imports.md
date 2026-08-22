# Backend import layout

Run Python with **`PYTHONPATH` including `app/backend`** (repository root as cwd). Entry modules:

- **`python -m api.main`** — local HTTP server (React SPA + JSON APIs).
- **`python -m providers`** — one-shot merge of legacy `.env` secrets into `data/private/providers.json`.
- **`python run_pipeline.py`** / **`python -m apps.pipeline_cli`** — CLI pipeline.

## Dependency direction

1. **Delivery** (`api/`) — HTTP, JSON handlers, categorization queue (`api/categorize.py`, `api/categorize_queue.py`); calls `pipeline`, `providers`, `config`, `ledger`.
2. **Application** (`pipeline/__init__.py` orchestration, `api/jobs.py`) — run steps; no HTTP parsing.
3. **Domain** (`pipeline/compile_*.py`, `ledger/store.py`, …) — rules and transforms.
4. **Infrastructure** — `pipeline/fetch.py` (Selenium), `ledger/` (SQLite), `integrations/`, `providers` (JSON).

Avoid importing `api` from `pipeline` **except** `api.categorize` (shared **auto** categorization pass after compile). Do not import `api` from `ledger`.

## Package map

| Module | Role |
|--------|------|
| `ledger/store.py` | Migrations, upsert, patch, queue SQL helpers |
| `ledger/dashboard_sql.py` | Dashboard aggregates |
| `ledger/heatmap_sql.py` | Heatmap month×category + detail SQL |
| `pipeline/fetch.py` | Browser download |
| `pipeline/route_inbox.py` | Route inbox |
| `pipeline/compile_holdings.py` | Compile holdings |
| `pipeline/compile_transactions.py` | Compile transactions |
| `pipeline/backup.py` | Pre-compile snapshot + divergence fingerprint |
| `api/main.py` | FastAPI app |

**Canonical truth:** `config.ledger_db_file` (`data/ledger.sqlite`). Do not treat `compiled.csv`, `fingerprint_db.csv`, or Sheets as authoritative. Static store mappings live in SQLite (`store` / `store_category`), not editable CSV.
