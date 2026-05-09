# Finance compiler — web SPA

React + Vite + TypeScript single-page app served by the Python `web_control` server from [`app/backend`](../backend/web_control/).

Routes:

- **`/`** — Dashboard (KPIs + charts: net worth, allocation, cash flow, top categories, sources). Reads from `/api/dashboard/*`.
- **`/pipeline`** — Pipeline runner (downloads, route, compile, auto-categorize, Sheets push, live log). Wires to `/api/jobs/*`, `/api/sheets/*`, `/api/events` (SSE).
- **`/heatmap/detail`** — Per-month/category drill-down (React). Uses `/heatmap/api/detail` and related APIs.
- **`/holdings/`** — Holdings timeline and manual ingest. Uses `/api/holdings/*`.
- **`/categorize/`** — Category queue. Uses `/categorize/api/*`.

Some legacy flows may still open Python-rendered HTML (e.g. older heatmap drill links); prefer in-app routes above.

## Prerequisites

- Node.js 20+ (matches Vite 6 requirement; tested on 22).
- Python venv from the repo root, with `requirements.txt` installed.

## Development

Two processes: Python control server from the **repository root** with `PYTHONPATH` including `app/backend`, and Vite from **`app/frontend`**.

```bash
# Terminal 1 (repo root): export PYTHONPATH=app/backend   # POSIX
python -m web_control

# Terminal 2
cd app/frontend
npm install
npm run dev
```

Open the **Vite** URL (defaults to <http://127.0.0.1:5173/>). It proxies `/api`, `/heatmap/api`, `/heatmap/detail`, `/categorize`, and `/holdings` to `http://127.0.0.1:8780/`.

## Production build

```bash
cd app/frontend
npm install   # first time / when deps change
npm run build
```

Output is **`dist/`** under this directory. With `PYTHONPATH=app/backend`, run `python -m web_control` from the repo root and open <http://127.0.0.1:8780/> — the server serves `app/frontend/dist/index.html` and `dist/assets/*`.

If you open the Python server before building, you get a placeholder page with instructions instead of a blank screen.

## Layout

```
app/frontend/
├── index.html                 # Vite entry
├── package.json               # deps + npm scripts
├── vite.config.ts             # proxy to :8780
├── tsconfig*.json             # TS configs (project references)
├── src/
│   ├── main.tsx               # React root
│   ├── App.tsx                # Router
│   ├── styles/theme.css       # Dark palette shared by all pages
│   ├── components/TopNav.tsx  # Top nav (mirrors web_control/control_nav.py)
│   ├── lib/
│   │   ├── api.ts             # fetchJson, formatMoney, formatPct
│   │   ├── useEventStream.ts  # SSE hook for /api/events
│   │   └── dashboardTypes.ts  # API typings for /api/dashboard/*
│   └── pages/
│       ├── Dashboard.{tsx,css}
│       ├── Pipeline.{tsx,css}
│       └── Heatmap.{tsx,css}  # CSS scoped under .heatmap-page (RTL stays local)
└── dist/                      # build output (gitignored)
```

## Adding charts / endpoints

1. Add the Python aggregation in `app/backend/web_control/dashboard_api.py` and dispatch it from `handle_dashboard_request`.
2. Add the typing in `app/frontend/src/lib/dashboardTypes.ts`.
3. Add a card component in `Dashboard.tsx` using `useFetch` + Recharts.
