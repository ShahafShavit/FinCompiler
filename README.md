# Finance compiler

Python tooling to fetch bank/card exports, route them into per-pipeline folders under `data/pipeline/`, compile CSVs under `data/export/`, and categorize transactions.

Code lives in **directories at the repository root** next to `main.py` (`pipeline/`, `categorization/`, `web_control/`, …) plus `config.py` and `logger.py`. **Run commands from the repo root** (or set `PYTHONPATH` to the repo root) so `import pipeline` and friends resolve. IDEs should use the project directory as the working directory for run configurations.

## Web control dashboard

The local web UI runs fetches, pipeline steps, and shows live logs. Categorization is a **queue** you can open anytime at `/categorize/` (same port as the dashboard).

### 1. Prerequisites

- **Python 3.11+** (3.12+ recommended; match whatever you use for the rest of this project).
- A virtual environment is recommended.

### 2. Install dependencies

This repo’s `.gitignore` expects the environment at **`venv/`** in the project root (not `.venv`). From the repository root:

```bash
python -m venv venv
```

Activate the venv:

- **Windows (cmd):** `venv\Scripts\activate.bat`
- **Windows (PowerShell):** `venv\Scripts\Activate.ps1`
- **macOS / Linux:** `source venv/bin/activate`

Then:

```bash
pip install -r requirements.txt
```

### 3. Configuration

- Copy or create a **`.env`** file in the project root with your portal credentials (bank, cards, etc.), as required by `config` / `pipeline.portal_fetch`.
- Optional: set **`FINANCE_WORKSPACE_ROOT`** to use a separate `data/` tree and `web/` folder (see `config.py`); compiled outputs stay under that root’s `data/export/`.

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
- **Categorization queue:** [http://127.0.0.1:8780/categorize/](http://127.0.0.1:8780/categorize/)

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

### 5. What to use in the browser

- **Home (`/`)** — One **Pipeline** card: check what you want (downloads, route inbox, compile holdings/transactions, auto-categorize), then **Run pipeline**. There is no separate “full vs both” flow—those are just combinations of the same checkboxes.
- **`/categorize/`** — Answer rows that still need a category (after `compiled.csv` exists and an auto pass has run). No separate “session”; the page reflects whatever is still missing a category. Category fields are comboboxes (type to filter or enter a new label).

### Headless CLI (without the web UI)

From the repo root, with the venv activated:

```bash
python run_pipeline.py --help
python run_pipeline.py all
```

(With venv not activated: `venv\Scripts\python.exe run_pipeline.py …` on Windows, or `venv/bin/python run_pipeline.py …` on macOS/Linux.)

Interactive terminal/browser categorization via `run_pipeline.py --categorize` uses `FINANCE_CATEGORIZE_UI` and is separate from the dashboard queue at `/categorize/`.

### Utilities (`scripts/`)

From the repo root (each adds the repo root to `sys.path` when needed):

- `python scripts/verify_ledger_integrity.py` — structural audit of the ledger DB (`pipeline/ledger.py`)
- `python scripts/run_categorize_http_workspace.py` — categorization with HTTP UI against your workspace (`FINANCE_WORKSPACE_ROOT`)
- `python scripts/web_control_restart.py` — stop the control port listener and start `python -m web_control`

### Qt desktop UI

From the repo root:

```bash
python main.py
```
