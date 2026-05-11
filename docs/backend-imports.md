# Backend import layout

Run Python with **`PYTHONPATH` including `app/backend`** (repository root as cwd). Entry modules:

- **`python -m api`** — local HTTP server (React SPA + JSON APIs).
- **`python -m providers`** — one-shot merge of legacy `.env` secrets into `data/private/providers.json`.

## Dependency direction

1. **Delivery** (`api/`) — HTTP, JSON handlers, and **categorization** (`api/categorize.py`, `api/categorize_queue.py`); calls `pipeline`, `providers`, `config`.
2. **Application** (`pipeline/__init__.py` orchestration, `api/jobs.py`) — run steps; no HTTP parsing.
3. **Domain** (`pipeline/compiler.py`, `pipeline/ledger.py`, …) — rules and transforms.
4. **Infrastructure** — `pipeline/fetch.py` (Selenium), `pipeline/ledger.py` (SQLite), `integrations/`, `providers` (JSON).

Avoid importing `api` from `pipeline` **except** `api.categorize` (shared **auto** categorization pass after compile). Do not import `api` from `ledger`.

## Package map (pipeline)

| Module | UI step |
|--------|---------|
| `pipeline/fetch.py` | Browser download |
| `pipeline/route_inbox.py` | Route inbox |
| `pipeline/compile_holdings.py` | Compile holdings |
| `pipeline/compile_transactions.py` | Compile transactions |
| `pipeline/ledger.py` | SQLite ledger |
| `pipeline/backup.py` | Pre-compile snapshot |
