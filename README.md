# Finance compiler

Python tooling to fetch bank/card exports, route them into per-pipeline folders under `data/pipeline/`, compile CSVs under `data/export/`, and categorize transactions. The local web UI is a React SPA (`web/`) backed by a Python control server (`web_control/`).

Code lives in **directories at the repository root** next to `main.py` (`pipeline/`, `categorization/`, `web_control/`, `web/`, …) plus `config.py` and `logger.py`. **Run commands from the repo root** (or set `PYTHONPATH` to the repo root) so `import pipeline` and friends resolve. IDEs should use the project directory as the working directory for run configurations.

## Web control dashboard

The local web UI is a React SPA in [`web/`](web/README.md) hosting three routes:

- **`/`** — Dashboard with infographics (net worth, allocation, cash flow, top categories, sources).
- **`/pipeline`** — Pipeline runner: downloads, routing, compile, auto-categorize, Google Sheets push, live SSE log.
- **`/heatmap`** — Monthly category heatmap (expense / income / net) + drill-down.

Categorize (`/categorize/`) and Holdings (`/holdings/`) are React routes in the SPA; the Python server only exposes JSON APIs and static assets.

### 1. Prerequisites

- **Python 3.11+** (3.12+ recommended).
- **Node.js 20+** (only when you need to build or serve the SPA). Tested on 22.
- A Python virtual environment is recommended.

### 2. Install dependencies

This repo’s `.gitignore` expects the environment at **`venv/`** in the project root (not `.venv`). From the repository root:

```bash
python -m venv venv
```

Activate the venv:

- **Windows (cmd):** `venv\Scripts\activate.bat`
- **Windows (PowerShell):** `venv\Scripts\Activate.ps1`
- **macOS / Linux:** `source venv/bin/activate`

Then install Python deps:

```bash
pip install -r requirements.txt
```

Build the SPA once (from the repo root):

```bash
cd web
npm install
npm run build
cd ..
```

This populates `web/dist/`, which the Python server serves as static files. Repeat `npm run build` whenever the SPA source changes.

### 3. Configuration

- Copy or create a **`.env`** file in the project root with your portal credentials (bank, cards, etc.), as required by `config` / `pipeline.portal_fetch`.
- Optional: set **`FINANCE_WORKSPACE_ROOT`** to use a separate `data/` tree (see `config.py`); compiled outputs stay under that root’s `data/export/`.

### 4. Start the server

From the **project root**, use the **venv’s Python**:

- **After activating the venv** (see §2):

  ```bash
  python -m web_control
  ```

- **Without activating** (paths from repo root):

  - **Windows:** `venv\Scripts\python.exe -m web_control`
  - **macOS / Linux:** `venv/bin/python -m web_control`

You should see a log line with the URL. Defaults:

- **Dashboard:** [http://127.0.0.1:8780/](http://127.0.0.1:8780/)
- **Pipeline runner:** [http://127.0.0.1:8780/pipeline](http://127.0.0.1:8780/pipeline)
- **Heatmap:** [http://127.0.0.1:8780/heatmap](http://127.0.0.1:8780/heatmap)
- **Holdings:** [http://127.0.0.1:8780/holdings/](http://127.0.0.1:8780/holdings/)
- **Categorization queue:** [http://127.0.0.1:8780/categorize/](http://127.0.0.1:8780/categorize/)

If you haven't built the SPA yet, the Python server serves a placeholder at `/` with the build instructions instead of a blank page.

Override bind address and port with environment variables (optional):

| Variable | Default | Purpose |
|----------|---------|---------|
| `FINANCE_CONTROL_HTTP_HOST` | `127.0.0.1` | Interface to listen on |
| `FINANCE_CONTROL_HTTP_PORT` | `8780` | HTTP port |

Example (PowerShell, venv activated):

```powershell
$env:FINANCE_CONTROL_HTTP_PORT="9000"
python -m web_control
```

Or without activating:

```powershell
$env:FINANCE_CONTROL_HTTP_PORT="9000"
.\venv\Scripts\python.exe -m web_control
```

Stop the server with **Ctrl+C**.

### 5. SPA development with hot reload

Run the Python server **and** the Vite dev server in two terminals:

```bash
# Terminal 1
python -m web_control

# Terminal 2
cd web
npm run dev
```

Then open the Vite URL (defaults to <http://127.0.0.1:5173/>). Vite proxies `/api/*`, `/heatmap/api/*`, `/heatmap/detail`, `/categorize`, and `/holdings` to the Python server on port 8780. SPA edits hot-reload; Python edits require restarting the Python process.

See [`web/README.md`](web/README.md) for the SPA layout and how to add new charts / endpoints.

### 6. What to use in the browser

- **Home (`/`)** — Dashboard with KPIs and charts. Empty state when `data/ledger.sqlite` is missing.
- **`/pipeline`** — One **Pipeline** card: check what you want (downloads, route inbox, compile holdings/transactions, auto-categorize), then **Run pipeline**. There is no separate “full vs both” flow—those are just combinations of the same checkboxes.
- **`/heatmap`** — Tabbed monthly heatmap; click a cell for a per-month/category drill-down.
- **`/categorize/`** — Manual category queue (React). Answer rows that still need a category after an auto pass. Combobox fields (type to filter or enter a new label).
- **`/holdings/`** — Holdings timeline and manual ingest (React).

### Headless CLI

From the repo root, with the venv activated:

```bash
python run_pipeline.py --help
python main.py --help
python run_pipeline.py all
```

`main.py` is the same pipeline CLI as `run_pipeline.py`.

(With venv not activated: `venv\Scripts\python.exe run_pipeline.py …` on Windows, or `venv/bin/python run_pipeline.py …` on macOS/Linux.)

`run_pipeline.py … --categorize` runs an **automatic** category pass on the ledger, then tells you to finish any remaining rows in the browser at **`/categorize/`** (start `python -m web_control` first). There are no stdin prompts or a separate mini HTTP server for categorization.

### Utilities (`scripts/`)

From the repo root (each adds the repo root to `sys.path` when needed):

- `python scripts/verify_ledger_integrity.py` — structural audit of the ledger DB (`pipeline/ledger.py`)
- `python scripts/web_control_restart.py` — stop the control port listener and start `python -m web_control`
