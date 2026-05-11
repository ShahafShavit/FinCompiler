---
name: bloat-police
description: Audits code bloat, file bloat, and project structure; proposes collapsing duplicate concepts and clearer layering (routers vs services vs domain vs integrations). Use when the user asks for bloat police, structural overview, fewer files for the same idea, API folder reorganization, or separation of business logic from controllers.
disable-model-invocation: true
---

# Bloat police (structure and collapse)

## Goal

Reduce **concept duplication across files** and **unclear boundaries** so one idea has one obvious home. Prefer **merge + rename** over scattering the same responsibility.

## When this skill applies

- User wants an **overview** of bloat, oversized modules, or folder sprawl.
- User asks **why two files exist** for related behavior (e.g. two `*_api.py` modules).
- User wants **layering**: HTTP/controllers vs application services vs domain vs external integrations.

## Audit workflow (do this in order)

1. **Inventory by responsibility** (not by filename): list what each top-level area does in one line each.
2. **Find overlap**: same types, same env keys, same external system (Sheets, DB, jobs), or copy-pasted helpers across files.
3. **Check names vs contents**: a file named for “sync” or “totals” that only holds config checks is a smell — flag **rename or merge**.
4. **Map layering** (see below). Mark violations: business rules inside route handlers, HTTP types leaking into `ledger/` or `pipeline/`, etc.
5. **Propose collapses**: each proposal = **keep path**, **merge from → into**, **new name if rename**, **what breaks** (imports, tests).
6. **Prefer small moves**: one merge or one new package per pass; avoid drive-by refactors outside the bloat scope.

## Layering vocabulary (default targets)

| Layer | Typical contents | Should not |
|-------|------------------|------------|
| **Router / HTTP** | Path, query, status codes, thin delegation | Heavy SQL, Sheets I/O, long algorithms |
| **Service / use-case** | Orchestration for one user-facing action | Raw framework objects passed deep into domain |
| **Domain / ledger / pipeline** | Rules, queries, compile steps, invariants | FastAPI `Request`, response dict shaping |
| **Integration** | One external system (e.g. Google client wrapper) | App-specific policy duplicated in multiple API modules |
| **Config** | Paths, feature flags, env | Business rules |

FinCompiler today: `api/main.py` wires the app; `api/routers/control.py` registers routes; sibling `api/*_api.py` modules often act as **service-style** helpers consumed by routers — treat that as **acceptable but explicit** (or future `api/services/` if it grows).

## Red flags (prioritize these)

- **N files, one concept** (e.g. four modules all touching “Sheets push” with different names).
- **Barrel files** that only re-export without adding clarity.
- **God modules** (>500 lines) mixing unrelated endpoints and helpers — split by **concept**, not by line count alone.
- **Circular or awkward imports** (`api` ↔ `api`) — signal a missing shared layer (often `integrations/` or `services/`).
- **Dead or misleading module docstrings** vs actual code.

## Output format (what the user sees)

Use a short report:

1. **Structure snapshot** — tree or bullet list of major folders and roles.
2. **Overlap table** — concept | files involved | recommendation (merge / rename / extract).
3. **Layering notes** — where boundaries are good vs leaky.
4. **Suggested next PR** — one concrete first step (single rename, single merge, or new subpackage).

## Repository-specific anchor (FinCompiler)

Use this as a **concrete teaching example** when discussing `app/backend/api/`:

- **`app/backend/api/totals_sheet_sync.py`** — small module: push-only Sheets **configuration probe** (`is_sheets_configured`: credential path + spreadsheet id exist on disk). The module docstring describes credentials helper behavior; the **filename** suggests “totals sync” more broadly than the code delivers.
- **`app/backend/api/desktop_sheets_api.py`** — **desktop Sheets sync** for the control API: sheet pairs from config, temp CSV from ledger vs legacy compiled CSV, preview/push via `integrations.google_sheets`. It **imports** `is_sheets_configured` from `totals_sheet_sync`.

**Bloat-police question to answer:** Are these two files justified as separate concepts, or is `totals_sheet_sync` really a **Sheets connectivity / credentials helper** that could live next to `providers` / `integrations` or be **renamed** so callers and grep match intent? `desktop_sheets_api` stays the orchestration surface; the tiny helper file is the usual collapse/rename candidate if nothing else imports `totals_sheet_sync` under that name.

When surveying **`app/backend/api/`**, group modules by **job** (dashboard, categorize+queue, integrity, heatmap, desktop sheets, jobs, providers) rather than by suffix alone (`*_api.py` is not a layer — it is a naming habit).

## Anti-patterns for the agent

- Do not recommend a **big-bang** reshuffle without listing import/test fallout.
- Do not conflate “many files” with bloat if **each file is one clear concept** and imports stay acyclic.
- Do not move domain logic into `api/` just to reduce file count — **extract** the other direction when HTTP leaks inward.
