# Finance compiler

Web-first personal finance tooling: a **React SPA** in [`app/frontend`](app/frontend/README.md) (dashboard, pipeline runner, heatmap, categorize, holdings) backed by a Python HTTP server in [`app/backend/web_control`](app/backend/web_control/). Data flows through `data/pipeline/` into **`data/ledger.sqlite`** (see [`app/backend/config.py`](app/backend/config.py)).

Application code lives under **`app/`**:

- **`app/backend`** — Python packages (`pipeline`, `categorization`, `web_control`, …), `config.py`, `logger.py`, bundled [`schema`](app/backend/schema), and [`scripts`](app/backend/scripts).
- **`app/frontend`** — Vite + React + TypeScript SPA.

**Always use the repository root as the working directory** when running the server or CLI so `data/` and `.env` resolve correctly. Put **`app/backend` on `PYTHONPATH`** so Python can import `web_control`, `pipeline`, and `config`.

Convenient form (POSIX):

```bash
export PYTHONPATH=app/backend
```

PowerShell (repo root):

```powershell
$env:PYTHONPATH = "app/backend"
```

Repo-root shims [`main.py`](main.py) and [`run_pipeline.py`](run_pipeline.py) prepend `app/backend` for you when you run those files only.

## Run the web app

### Prerequisites

- **Python 3.11+** (3.12+ recommended).
- **Node.js 20+** when building or developing the SPA (tested on 22).
- A Python virtual environment is recommended (`.venv` or `venv` at repo root).

### Install dependencies

From the repository root:

```bash
python -m venv .venv
```

Activate the venv (examples):

- **Windows (cmd):** `.venv\Scripts\activate.bat`
- **Windows (PowerShell):** `.venv\Scripts\Activate.ps1`
- **macOS / Linux:** `source .venv/bin/activate`

Then:

```bash
pip install -r requirements.txt
```

Build the SPA once:

```bash
cd app/frontend
npm install
npm run build
cd ../..
```

Output is `app/frontend/dist/`, which the Python server serves as static files. Re-run `npm run build` after frontend changes.

### Configuration

- Copy or create **`.env`** in the project root with portal credentials as required by `config` / `pipeline.portal_fetch`.
- Optional: **`FINANCE_WORKSPACE_ROOT`** points at another tree that contains `data/` (and optionally a workspace-relative `web/` for legacy HTML outputs). See `config.py`.

### Start the server

From the **repository root**, with `PYTHONPATH` including `app/backend` (see above):

```bash
python -m web_control
```

VS Code task **Web (Python): control server** runs [`app/backend/scripts/web_control_restart.py`](app/backend/scripts/web_control_restart.py), which frees port **8780**, sets `PYTHONPATH` for the child process, and starts `web_control` with cwd at the repo root.

Without activating the venv (Windows example):

```cmd
set PYTHONPATH=app\backend
.venv\Scripts\python.exe -m web_control
```

Default URLs:

- **Dashboard:** [http://127.0.0.1:8780/](http://127.0.0.1:8780/)
- **Pipeline runner:** [http://127.0.0.1:8780/pipeline](http://127.0.0.1:8780/pipeline)
- **Heatmap:** [http://127.0.0.1:8780/heatmap](http://127.0.0.1:8780/heatmap)
- **Holdings:** [http://127.0.0.1:8780/holdings/](http://127.0.0.1:8780/holdings/)
- **Categorization:** [http://127.0.0.1:8780/categorize/](http://127.0.0.1:8780/categorize/)

If `app/frontend/dist/` is missing, the server returns a placeholder HTML page with build instructions.

Optional HTTP overrides:

| Variable | Default | Purpose |
|----------|---------|---------|
| `FINANCE_CONTROL_HTTP_HOST` | `127.0.0.1` | Bind address |
| `FINANCE_CONTROL_HTTP_PORT` | `8780` | Port |

### SPA development (hot reload)

Terminal 1 (repo root, `PYTHONPATH=app/backend`):

```bash
python -m web_control
```

Terminal 2:

```bash
cd app/frontend
npm run dev
```

Open [http://127.0.0.1:5173/](http://127.0.0.1:5173/). Vite proxies `/api`, `/heatmap/api`, `/heatmap/legacy-detail`, `/categorize`, and `/holdings` to the Python server on port **8780**.

See [`app/frontend/README.md`](app/frontend/README.md) for layout and how to add charts.

### Browser routes (summary)

- **`/`** — Dashboard (empty state if `data/ledger.sqlite` is missing).
- **`/pipeline`** — Pipeline card with checkboxes and live SSE log.
- **`/heatmap`** — Monthly heatmap and drill-down.
- **`/categorize/`** — Manual category queue after auto-categorize.
- **`/holdings/`** — Holdings timeline and ingest.

## Automation: pipeline CLI

For **cron, Task Scheduler, or headless batch runs** — not the default daily workflow — use the pipeline CLI from the **repository root**:

```bash
python run_pipeline.py --help
python run_pipeline.py all
```

`run_pipeline.py` and `main.py` are thin shims that add `app/backend` to `sys.path` and delegate to `apps.pipeline_cli`.

`run_pipeline.py … --categorize` runs an automatic category pass; finish remaining rows in the browser at **`/categorize/`** while `python -m web_control` is running.

## Utilities (`app/backend/scripts`)

Run from the repo root with `PYTHONPATH=app/backend` (or rely on each script’s path bootstrap where applicable):

- `python app/backend/scripts/verify_ledger_integrity.py` — structural audit of the ledger DB (`pipeline/ledger.py`).
- `python app/backend/scripts/web_control_restart.py` — free port **8780** and start `python -m web_control` with correct `PYTHONPATH` and cwd.
