# Finance compiler — web SPA

React + Vite + TypeScript single-page app served by the Python `web_control` server.

Routes:

- **`/`** — Dashboard (KPIs + charts: net worth, allocation, cash flow, top categories, sources). Reads from `/api/dashboard/*`.
- **`/pipeline`** — Pipeline runner (downloads, route, compile, auto-categorize, Sheets push, live log). Wires to `/api/jobs/*`, `/api/sheets/*`, `/api/events` (SSE).
- **`/heatmap`** — Monthly category heatmap (expense / income / net) with stats. Reads `/heatmap/api/data`, refresh via `/heatmap/api/refresh`. Click a cell → opens `/heatmap/detail` (Python-rendered HTML) in a new tab.

Holdings (`/holdings/`) and Categorize (`/categorize/`) remain Python-rendered by `web_control` and are linked from the SPA's top nav.

## Prerequisites

- Node.js 20+ (matches Vite 6 requirement; tested on 22).
- Python venv from the repo root, with the rest of the project's `requirements.txt` installed.

## Development

Two processes, both run from the repo root.

```bash
# Terminal 1: Python control server (host for /api, /heatmap/api, /holdings, /categorize)
python -m web_control

# Terminal 2: Vite dev server with HMR
cd web
npm install
npm run dev
```

Open the **Vite** URL printed in terminal 2 (defaults to <http://127.0.0.1:5173/>). It proxies `/api`, `/heatmap/api`, `/heatmap/detail`, `/categorize`, and `/holdings` back to `http://127.0.0.1:8780/`.

## Production build

```bash
cd web
npm install   # only the first time / when deps change
npm run build
```

Output lands in `web/dist/`. Then start `python -m web_control` and visit <http://127.0.0.1:8780/> — the Python server serves `web/dist/index.html` for SPA routes (`/`, `/pipeline`, `/heatmap`) and `web/dist/assets/*` as static files.

If you visit the Python server before running `npm run build`, you'll see a placeholder page with the build instructions instead of a blank screen.

## Layout

```
web/
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

1. Add the Python aggregation in `web_control/dashboard_api.py` and dispatch it from `handle_dashboard_request`.
2. Add the typing in `web/src/lib/dashboardTypes.ts`.
3. Add a card component in `Dashboard.tsx` using `useFetch` + Recharts.
