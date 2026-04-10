# Data architecture migration plan (living document)

**Status:** Active program plan — align implementation with `docs/data-storage-and-pipeline-evaluation.md` (architecture evaluation).  
**Last updated:** 2026-04-11 — **MIG-D4 signed:** from **2026-04-11**, **`config.ledger_db_file` (SQLite) is the only authoritative ledger** for this project; other ledger representations (**`compiled.csv` as truth**, **`fingerprint_db.csv`**, treating Sheets or raw CSV as authoritative) are **deprecated** — see tracker MIG-D4 notes. **Repo layout:** single `pipeline/ledger.py` for all ledger DB operations (see Section 1.4); **`compile_transactions_main` always passes `ledger_db=config.ledger_db_file`** (no separate `upsert_ledger` flag); with a ledger DB, **`Compiler.save_main` still writes `compiled.csv` as a staging/export mirror**, then upserts into SQLite (CSV is not the sole source of truth). Legacy CSV columns **מזהה עסקה** / **תאריך עדכון** vs ledger **fingerprint** / **ingested_at** — see `docs/ledger-merge-ownership.md`, `schema/ledger/README.md`, `pipeline/ingested_at_rules.py`. **MIG-E3:** `fingerprint_db.csv` is not read or written when `config.ledger_db_file` exists on disk; legacy CSV-only runs (no ledger file) may still exist in code for tests — **not** supported as a production workflow after MIG-D4.  
**Integration branch:** `sqlite` (or your current migration branch). **Data safety:** external copy of `data/` — restore from that if the working tree is corrupted.

**How to use:** Execute phases in order unless a task explicitly allows parallel work. **Update the task tracker table below** as you complete work; keep Section 1.4 (repo assessment) updated when reality changes. Optional: create GitHub Issues with labels `migration` + `MIG-xx` and paste the same IDs for cross-linking.

This plan references the evaluation document by section number in prose (for example, "evaluation Section 12") so headings stay free of the section symbol character.

---

## 0. How to execute and where tasks are tracked

### 0.1 Execution workflow

1. **Branch:** Do migration work on your integration branch (for example `sqlite`). Merge to `main` only when a phase (or a safe chunk) is verified.
2. **Order:** Run **Phase A → J** in sequence. Tasks inside a phase follow dependency arrows in the phase tables (Section 2). Parallel work is only where the plan says so (for example MIG-G4 after MIG-G2).
3. **One slice at a time:** Prefer small commits or PRs that close **one MIG task** (or a tight pair like D1–D3) before starting the next.
4. **Verify after each slice:** Run `python -m unittest discover -s tests -p "test_*.py"` whenever shared code changes; run the **phase verification gate** from Section 2 before declaring that phase done.
5. **Data:** Keep your **external backup** untouched as the gold restore. For day-to-day experiments, use copies under `FINANCE_WORKSPACE_ROOT` or a duplicate folder if needed.
6. **Cutover:** **MIG-D4** signed **2026-04-11** — SQLite (`config.ledger_db_file`) is **canonical**; see Section 0.3 tracker notes. For future projects or branches, record a new D4-style line when authority changes again.
7. **Phase H (S3):** **Deferred** until the local SQLite pipeline, import/cutover, and core read/write paths are stable (see Section 5). Phases A–G and I–J do not require cloud object storage.

### 0.1.1 Cutover operations (maintenance window)

There is **no transition period** where the system remains available on the old model while migration finishes. For the **production cutover**, treat the work as a single **maintenance window**:

- **Downtime:** From the start of migration (import, rewiring compile/categorize/web to SQLite, and any bundled fixes) through **testing until you sign off**, the system is **down** — not in partial service and not “CSV-primary with SQLite on the side.”
- **Finish then validate:** Complete the migration steps for that window, then run your test checklist before bringing normal use back. **MIG-D4** should record when the window was treated as started (if useful), when SQLite became canonical, and when the system was considered good to use again.

Incremental development on branches before that window does not require this downtime; it applies to the **authoritative cutover** you run against real data.

### 0.2 Where the task list lives

| Location | Role |
|----------|------|
| **This file — table below** | **Source of truth for status** (`Not started` → `In progress` → `Done` / `Blocked` / `N/A`). Edit the **Status** and **Notes** cells as you go. |
| **Section 2** | Full specs, acceptance criteria, and verification gates per task — unchanged; the tracker points to these IDs. |
| **GitHub Issues / Projects** (optional) | One issue per MIG ID if you want boards and assignees; keep IDs aligned with this table. |

**Status values:** **Not started** | **In progress** | **Done** | **Blocked** (waiting on dependency or decision) | **N/A** (skipped by explicit decision).

### 0.3 Task tracker (update as you go)

| ID | Phase | Title | Status | Notes |
|----|-------|-------|--------|-------|
| MIG-A1 | A | Architecture traceability matrix | Done | Matrix in Section 1.7 |
| MIG-A2 | A | Baseline data snapshot procedure | Done | User confirmed external/gold backup 2026-04-10 |
| MIG-A3 | A | Test baseline run | Done | 2026-04-11: `python -m unittest discover -s tests -p "test_*.py"` — **53** tests, exit 0, repo root (includes Phase G policy tests, `test_ingested_at_rules`, compile upsert tests, heatmap tests) |
| MIG-B1 | B | Backup helper module | Done | `pipeline/backup.py`, `config.backup_parent_dir` → `data/_backups/` |
| MIG-B2 | B | CLI or control hook (`--backup-first`) | Done | `transactions` / `all` / `both-process`; web control checkbox `p_backup` (enabled only when a compile action — transactions or holdings — is selected; otherwise disabled) |
| MIG-B3 | B | Snapshot manifest (minimal) | Done | `snapshot_manifest.json` + `tests/test_backup_manifest.py` |
| MIG-B4 | B | Document exclusion rules | Done | Thin snapshot exclusions: Phase B subsection after task table (evaluation Sections 12.2, 13.2) |
| MIG-C1 | C | Choose DB path and config | Done | `config.ledger_db_file` → `data/ledger.sqlite` under workspace; `/data/` gitignored |
| MIG-C2 | C | Migration framework | Done | `pipeline/ledger.py` (`migrate_ledger_db`): empty DB loads `schema/ledger/full_schema.sql`; existing v8+ files step through v9/v10 hand-rolled migrations; target schema **v10** (`_BASELINE_TARGET_VERSION` in code) |
| MIG-C3 | C | Initial ledger table + UNIQUE(fingerprint) | Done | Baseline DDL from `migrate_ledger_db` + `schema/ledger/full_schema.sql` (not separate migration files) |
| MIG-C4 | C | Static mappings + holdings tables (one DB; no separate fingerprint_metadata) | Done | Same single DB file: `store` / `store_category`, `similar_category_pair`, `holdings_balance` in `full_schema.sql` |
| MIG-D1 | D | Import: ledger | Done | `pipeline/ledger.py` (`import_web_totals_to_ledger`, etc.) — all-time `web_totals.csv` → `ledger_transaction`; real `fingerprint` only — v8 nullable, no row-hash substitute |
| MIG-D2 | D | Import: static mappings (stores + similar pairs) | Done | `pipeline/ledger.py` (`import_stores_to_ledger`, `sync_stores_to_ledger_from_dataframe`, etc.) — `stores_to_categories.csv` → `store` / `store_category`; `similar_pairs.csv` → `similar_category_pair`; full replace clears both mapping tables; null/empty `store_name` rows dropped. **`fingerprint_db.csv` is not imported** — redundant with **`ledger_transaction.קטגוריה`** (Section 5). |
| MIG-D3 | D | Parity report | Done | `pipeline.ledger.verify_ledger_against_csv` (and related checks after `import_web_totals_to_ledger`): row counts, sum בחובה/בזכות, per-row order checks; callers/tests assert parity — **not** a separate maintained script |
| MIG-D4 | D | Cutover decision log (canonical SQLite) | Done | **2026-04-11:** Operator declares **SQLite ledger only** — `config.ledger_db_file` (e.g. `data/ledger.sqlite`) is **the** authoritative transaction ledger and mapping store. **Deprecated for normal use:** any workflow where truth lives in `compiled.csv`, `fingerprint_db.csv`, `web_totals.csv`, or Google Sheets as **source** (those remain export/view/staging only where the code still writes them). **Return to service:** same date — cutover treated as complete for ongoing use. Legacy no-ledger code paths may remain for automated tests; not a supported production mode after this sign-off. Section 0.1.1. |
| MIG-E1 | E | Merge specification doc | Done | `docs/ledger-merge-ownership.md` (pipeline vs user columns; evaluation Section 13.1) |
| MIG-E2 | E | Compiler integration → SQLite | Done | `compile_transactions_main` always uses `Compiler(..., ledger_db=config.ledger_db_file)`; `Compiler.save_main` writes `compiled.csv` then → `upsert_compiled_dataframe_to_ledger` (there is **no** `upsert_ledger` parameter and **no** `--upsert-ledger` CLI). `update_fingerprint_db()` is a no-op when `ledger_db` is set. **Canonical** merge output is SQLite; `compiled.csv` remains a **non-authoritative** on-disk mirror for export/legacy tooling. |
| MIG-E3 | E | Stop writing fingerprint_db.csv (redundant) | Done | When `data/ledger.sqlite` (or `config.ledger_db_file`) **exists**: `Compiler.update_fingerprint_db` and `CategorizeFile.auto_categorize` do **not** read/write `fingerprint_db.csv`. Legacy **CSV-only** workflow (no ledger file on disk) may still use the sidecar until dropped in a later cleanup. |
| MIG-E4 | E | Divergence detection stub | Not started | |
| MIG-F1 | F | Migration for timestamps | Done | `ingested_at` / timestamp columns in `schema/ledger/full_schema.sql`; `pipeline/ingested_at_rules.py` (`compute_ingested_at_iso`); covered by `tests/test_ingested_at_rules.py` |
| MIG-F2 | F | Align code with schema triggers / timestamp rules | Done | `full_schema.sql` triggers on `ledger_transaction`; `update_category_by_fingerprint` documents trigger behavior for category updates |
| MIG-F3 | F | Pipeline rules for timestamps | Not started | Formal doc + tests that pipeline never fabricates user edit times on user-owned fields (evaluation Section 13.9) beyond what triggers already enforce |
| MIG-G1 | G | Deprecate ledger pull from Sheets | Done | Pull **removed**: no ``update_local``, ``pull_desktop_sync_from_cloud``, ``pull_sheet_readonly_to_csv``, web ``/api/sheets/pull``; PyQt pull control hidden |
| MIG-G2 | G | Push from DB + confirm | Done | Web ``api_push`` / ``api_preview`` use temp SQLite export for Totals when ``ledger.sqlite`` exists; PyQt push actions use ``QMessageBox`` + ``update_cloud(..., confirm=False)`` |
| MIG-G3 | G | Single tab configuration | Done | ``desktop_totals_sheet_name()`` defaults to ``totals_sheet_name`` (``FINANCE_TOTALS_SHEET_NAME``); PyQt uses ``config.desktop_*_sheet_name()`` |
| MIG-G4 | G | Fix heatmap auto-pull | Done | Heatmap reads **SQLite ledger** only; ``/heatmap/api/refresh`` clears cache / reload from DB — **no** Google |
| MIG-H1 | H | S3 object layout | Not started | Deferred: after local system stable; S3 is the chosen backend |
| MIG-H2 | H | Upload flow | Not started | Deferred: after local system stable |
| MIG-H3 | H | Download / restore + divergence | Not started | Deferred: after local system stable |
| MIG-H4 | H | AWS auth documentation | Not started | Deferred: after local system stable |
| MIG-I1 | I | Ledger API from DB (web_control) | Done | **Categorize** + **heatmap**: SQLite via `pipeline.ledger` (`load_transactions_dataframe_from_ledger`, categorize jobs). **Reports** = separate **MIG-I3** (not started). |
| MIG-I2 | I | Categorization UX → DB | Done | `CategorizeFile(ledger_db_path=...)` in PyQt, `web_control/jobs.py`, categorize queue, and pipeline auto-categorize; legacy `file_path` CSV mode remains for no-ledger scenarios. |
| MIG-I3 | I | Reports | Not started | |
| MIG-J1 | J | Remove authoritative static CSV edits | Not started | |
| MIG-J2 | J | Developer documentation | Not started | |

**Phase rollup (optional):** set to `Done` when all tasks in that phase are `Done` or `N/A`: A ☑ B ☑ C ☑ D ☑ E ☐ (blocked on **MIG-E4**) F ☐ (blocked on **MIG-F3**) G ☑ H ☐ I ☐ (blocked on **MIG-I3**) J ☐

---

## 1. Objective and scope

### 1.1 Objective

Move the finance project toward the **target architecture** in the evaluation document: **local-first pipeline**; **SQLite as the canonical ledger** and home for **fingerprint, static mappings, and holdings** in **one database file** (evaluation Sections 6, 10, 13.10); **deliberate, human-confirmed** cloud pull and push, not silent side effects (evaluation Section 12); **one-shot CSV to SQLite import** followed by a **hard cutover** — **no** post-cutover dual-write or “stabilization” period where `compiled.csv` remains authoritative (see Section 5); **Google Sheets** as an optional **read-only, push-only** view with **one tab** for the full ledger (evaluation Sections 10, 12.3, 13.5); **merge semantics, snapshot manifests, schema evolution, timestamps, and web-first UX** sequenced per evaluation Section 13. **S3** backup/sync is the chosen cloud store but **Phase H may be deferred** until the local system is working (Section 5).

### 1.2 In scope

- Introducing a **versioned SQLite schema**, **one-shot and scripted imports**, and **pipeline merge rules** aligned with evaluation Section 13.1.
- **Backup and snapshot** discipline (evaluation Sections 9, 12, 12.2, 13.2).
- **Operational sync** (S3 or equivalent) with **append-only** remote history and **divergence checks** (evaluation Sections 12, 12.1, 13.1).
- **Sheets**: phase out **bidirectional** flows toward **validated push-only** (evaluation Sections 12.3, 13.5).
- **Web application** as the **primary daily interface** over time (evaluation Sections 10, 12.5, 13.6).
- **Row metadata** (`ingested_at`, `category_updated_at`, `data_updated_at`) per evaluation Section 13.9.

### 1.3 Out of scope (unless explicitly pulled in later)

- Multi-user collaboration, mobile clients (evaluation Section 7).
- Replacing Selenium or bank fetch mechanics (except where storage touches them).
- Client-side encryption of backups beyond noting **SSE-S3** as a later toggle (evaluation Section 12.2, 13.8).
- Unsupervised pipeline notifications (evaluation Section 13.7) — track as a follow-up.

### 1.4 Current repository assessment (2026-04-11)

**Configuration (`config.py`).** Runtime paths are centralized: `data/export/compiled/compiled.csv` is the merged transactions output; `data/static/` holds `stores_to_categories.csv`, `similar_pairs.csv`, and (legacy, **CSV-only** when no ledger file) **`fingerprint_db.csv`** — redundant vs **`ledger_transaction.קטגוריה`** when the ledger exists (**MIG-E3**). **`web/data/web_totals.csv`** may still exist for legacy/backup but **heatmap reads `ledger.sqlite`**. `FINANCE_WORKSPACE_ROOT` isolates data for tests or experiments. **Holdings** desktop tab uses a calendar-year suffix by default; **Totals** push uses **`FINANCE_TOTALS_SHEET_NAME`** / `desktop_totals_sheet_name()` (single all-time tab).

**Ledger module (`pipeline/ledger.py`).** All SQLite ledger operations live in **one** module (no separate `ledger_migrate`, `web_totals_import`, or `static_store_import` packages). It includes: **`migrate_ledger_db`** (hand-rolled steps + baseline `schema/ledger/full_schema.sql`); **constraint audit** (`audit_ledger_constraints`, `format_report`); **dataframe I/O** (`load_transactions_dataframe_from_ledger`, `export_transactions_dataframe_to_csv`, store/backup loaders); **category update** (`update_category_by_fingerprint`); **fingerprint NULL backfill** (`backfill_null_fingerprints`, etc.); **web totals path** (`import_web_totals_to_ledger`, `load_web_totals_dataframe`, `verify_ledger_against_csv`, shared row helpers `_normalize_date_text` / `_float_col` / `_text_or_none`); **static store / similar pairs** (`import_stores_to_ledger`, `sync_stores_to_ledger_from_dataframe`, CSV loaders); **compile upsert** (`upsert_compiled_dataframe_to_ledger`). Call sites import from **`pipeline.ledger`** only. **`pipeline/folder_tracking.py`** was removed (unused `FolderTracker`; SSE logger list in `web_control` updated accordingly).

**Pipeline entrypoints.** `pipeline.compile_transactions_main` and related functions in `pipeline/__init__.py` drive compile; `apps/pipeline_cli.py` exposes CLI commands; `apps/qt_main.py` wires PyQt actions; `web_control/jobs.py` runs pipeline actions from the local control server. **SQLite:** `config.ledger_db_file` and `pipeline.ledger.migrate_ledger_db` create/upgrade the ledger DB from `schema/ledger/full_schema.sql`. **`compile_transactions_main` always passes `ledger_db` into `Compiler`; `save_main` writes `compiled.csv` then upserts into SQLite** — the CSV is a **staging/export mirror**; **canonical** merged state for categorization and merge rules is the DB. **Categorization** in PyQt and web control uses `CategorizeFile(ledger_db_path=...)` against that DB. **Heatmap** (`web_control/heatmap`) loads transactions from **`ledger.sqlite`** via `load_transactions_dataframe_from_ledger`. **MIG-D4** signed **2026-04-11:** SQLite ledger is **declared canonical**; CSV/Sheets-as-truth workflows **deprecated** for operator use (see Section 0.3 tracker).

**Scripts (`scripts/`).** Maintenance CLIs for **backfill**, **one-shot import**, and **compiled-date repair** were **removed** from the repo (logic remains in `pipeline.ledger` and tests). **Remaining** entrypoints: `verify_ledger_integrity.py` (wraps `audit_ledger_constraints`), `run_categorize_http_workspace.py`, `web_control_restart.py`, `fill_installment_statement_months.py` (optional statement-month helper against `ledger.sqlite`) — see root `README.md`.

**Tests.** `tests/` contains `unittest`-style modules (for example `test_workspace_config.py`, `test_pipeline_date_roundtrip.py`, `test_categorization_logic.py`, `test_ledger_migrate.py`, `test_web_totals_import.py`, `test_static_store_import.py`, `test_ledger_compile_upsert.py`, `test_ingested_at_rules.py`, `test_phase_g_sheets_policy.py`, `test_heatmap_category_mean.py` — most exercising **`pipeline.ledger`** or compile/timestamp rules. **pytest is not listed in `requirements.txt`**; verification should use **`python -m unittest discover`** unless the project adds pytest later. **Current baseline:** **53** tests passing from repo root (`python -m unittest discover -s tests -p "test_*.py"`).

**Google Sheets.** **Push only:** preview/compare and push from local holdings CSV + SQLite ledger export for Totals. **No pull** in code (removed `update_local`, `pull_desktop_sync_from_cloud`, web pull, PyQt pull). **Heatmap** uses **`ledger.sqlite`** directly, not `web_totals.csv`.

**Web vs PyQt.** The evaluation document targets **web as the primary entry point** (evaluation Section 10) while **PyQt remains** for supervised reruns (evaluation Section 12.5). The repo still centers daily operations on **PyQt plus `web_control`**; **web-first** is a **direction**, not a finished state.

**Backups.** `pipeline/backup.py`, CLI `--backup-first`, and web **`p_backup`** (when enabled for a compile action) implement optional backup-before-run; **there is still no mandatory default** — users can run pipeline without opting in — **gap** vs evaluation Section 9 and Section 12.2 if you require backup every time.

### 1.5 Conflicts between code and the evaluation document

| Topic | Evaluation stance | Current code / behavior | Recommended action |
|--------|-------------------|-------------------------|----------------------|
| Canonical store | SQLite ledger (evaluation Sections 6, 10, 13.10) | **MIG-D4 (2026-04-11):** operator uses **SQLite only** as ledger authority. Code may still expose legacy CSV-only paths for **tests**; production stance = DB. **`compiled.csv`** = mirror/export; **`fingerprint_db.csv`** not authoritative (Section 5). | **`schema/ledger/full_schema.sql`** is the DDL; **`fingerprint_db.csv` is not merged into SQLite** (Section 5). **MIG-E3** + **MIG-D4** aligned. |
| Sheets direction | Push-only, optional view (evaluation Sections 10, 12.3, 13.5) | **No pull** in code; push from DB export when ledger exists | Holdings vs Totals tab names — `config.desktop_*_sheet_name`. |
| Deliberate sync | Explicit confirm for pull/push (evaluation Section 12) | **No pull**; PyQt/web push confirms | — |
| One tab full ledger | One sheet for full history (evaluation Section 12.3) | Desktop sync still uses **year-suffixed** tabs | Migrate desktop sync to **one tab** or document a single source of truth for tab names. |
| Backup before run | Required before pipeline / destructive steps (evaluation Section 9) | Not enforced globally | Add automation or CLI contract in early phases. |

### 1.6 Reference SQLite DDL (bootstrap script)

The canonical bootstrap script is **`schema/ledger/full_schema.sql`**; **`schema/ledger/README.md`** explains ISO dates, `STRICT`, and local-time trigger defaults. The DDL defines **`ledger_transaction`** (including **`fingerprint`**, **`notes`**, evaluation Section 13.9 timestamps; **no** **`מזהה עסקה`** row-hash column), **`store`** / **`store_category`** (with static-store enforcement triggers), **`similar_category_pair`**, **`holdings_balance`**, and timestamp triggers. There is **no** `fingerprint_metadata` table — categories live on the ledger row. **One-shot imports** (via `pipeline/ledger.py` web-totals and static-store helpers, `pipeline/holdings_csv_import`) can fill the ledger (MIG-D1), static mappings (MIG-D2), and holdings. **Ongoing compile** (Phase E) **writes the ledger** via `upsert_compiled_dataframe_to_ledger`. **MIG-D4** (**2026-04-11**) records the formal **canonical SQLite / return-to-service** decision (Section 0.3).

### 1.7 Architecture traceability matrix (MIG-A1)

Maps each phase slice to **`docs/data-storage-and-pipeline-evaluation.md`** sections. **Gap** flags a known mismatch tracked in Section 1.5 or open work.

| Deliverable / phase slice | MIG ID(s) | Evaluation sections | Gap? |
|---------------------------|-----------|---------------------|------|
| Baseline, docs, test gate | A1–A3 | 9, 12, 12.2, 13.2 (habits); 6/10/13 as context | N |
| Backup-before-run, manifests | B1–B4 | 9, 12, 12.2, 13.2 | N |
| SQLite file, migrations, schema | C1–C4 | 6, 10, 13.3, 13.10 | N |
| One-shot import, parity, cutover | D1–D4 | 12.3, 13.3, 13.4, 13.9, 13.10 | N — **MIG-D4** dated **2026-04-11** (Section 0.3) |
| Compiler → DB, merge rules | E1–E4 | 13.1, 13.3, 13.10 | N |
| Timestamps, triggers, pipeline rules | F1–F3 | 12.4, 13.3, 13.9 | Partial — **F3** (documented pipeline rules for user-owned timestamps) still open |
| Sheets push-only, one tab, no silent pull | G1–G4 | 10, 12, 12.3, 13.5 | N — Phase G shipped (Section 1.4) |
| S3 backup/restore (deferred) | H1–H4 | 10.1, 12, 12.1, 12.2, 12.6, 13.1 | N (scheduled later) |
| Web primary UX | I1–I3 | 10, 12.5, 13.6 | Partial — **I1** + **I2** done (DB categorize + heatmap); **I3** reports open |
| Deprecate authoritative CSV edits | J1–J2 | 13.10 | N |

### 1.8 Baseline data snapshot procedure (MIG-A2)

1. **Choose a dated destination** outside the working tree (recommended) or a clearly labeled folder (for example `../finance-data-snapshots/2026-04-10-migration/`) so it is not committed by mistake.
2. **Copy the full tree** you use for real runs: either the repo’s **`data/`** directory or the full **`FINANCE_WORKSPACE_ROOT`** tree if that env var points at your workspace data root.
3. **Critical paths to verify inside the copy:** `data/export/compiled/` (especially `compiled.csv` and `holdings.csv` if used), `data/static/` (`stores_to_categories.csv`, `similar_pairs.csv`, and **`fingerprint_db.csv`** if your pipeline still produces it — **not** imported to SQLite, Section 5), **`data/ledger.sqlite`** (or `config.ledger_db_file`) for canonical ledger + mappings, and `web/data/` (legacy `web_totals.csv` may exist for backup; **heatmap reads the SQLite ledger**, not that CSV).
4. **Do not copy secrets** into instructions that mean “copy everything”: exclude `.env`, service-account JSON, and API key files unless you store them separately with appropriate access control.
5. **Dry run:** perform the copy once; record **date, source root, and destination path** in the MIG-A2 **Notes** cell (Section 0.3).
6. **Restore:** to roll back, replace the active `data/` (or workspace root tree) from this gold copy; keep an **external** backup unchanged as the ultimate restore (Section 0.1).

---

## 2. Phased migration (ordered)

Phases are **sequential** unless noted. Each phase lists **rollback** and **verification** (evaluation Section 12 and Section 13 require testable gates).

### Phase A — Program baseline, branching, and documentation wiring

**Goal.** Freeze assumptions, ensure **traceability** from tasks to evaluation sections, and establish **safe working habits** before schema work. No SQLite requirement yet.

| ID | Title | Description | Dependencies | Owner | Acceptance criteria | Risk |
|----|--------|-------------|--------------|-------|---------------------|------|
| MIG-A1 | Architecture traceability matrix | Maintain a short table mapping **deliverables** to evaluation Sections 6, 10, 12 (including 12.3), 13 (including 13.1, 13.2, 13.3–13.6, 13.9, 13.10) in this file or a linked tracker. | None | User / PM | Matrix reviewed; gaps flagged. | Low |
| MIG-A2 | Baseline data snapshot procedure | Document **manual** steps: copy `data/` (or `FINANCE_WORKSPACE_ROOT` tree) to a dated folder before any migration attempt; list **critical paths** (`export/compiled/`, `static/`, `web/data/`). | None | User | Written procedure; one dry run performed. | Low |
| MIG-A3 | Test baseline run | Record exact commands and outcomes for `python -m unittest discover -s tests -p "test_*.py"` from repo root. | None | Implementer | Log shows **all tests pass** on a clean checkout (or known skips documented). | Low |

**Verification gate (Phase A).**

- **Automated:** `python -m unittest discover -s tests -p "test_*.py"` — **exit code 0** (verification-only gate).
- **Manual / operational:** Confirm a **dated copy** of `data/` exists before Phase B destructive work.
- **Mark:** MIG-A3 is **verification-only**; MIG-A1–A2 mix documentation and manual ops.

**Rollback (Phase A).** No code rollback; discard bad copies of `data/` and restore from snapshot (MIG-A2).

---

### Phase B — Backup-before-run and snapshot discipline (evaluation Sections 9, 12, 12.2, 13.2)

**Goal.** Align operations with **backup before pipeline or destructive steps** and lay groundwork for **manifests** (evaluation Section 13.2).

| ID | Title | Description | Dependencies | Owner | Acceptance criteria | Risk |
|----|--------|-------------|--------------|-------|---------------------|------|
| MIG-B1 | Backup helper module | Implement a small module (path TBD by implementer) that creates a **timestamped** directory or archive under a configurable parent (for example `data/_backups/` or outside repo) including **minimum** critical paths; **exclude** secrets. | Phase A | Implementer | Running the helper before a **mock** pipeline run produces a restorable tree; **no** automatic cloud upload. | Medium |
| MIG-B2 | CLI or control hook integration | Wire **optional** `--backup-first` (CLI) and/or control-server checkbox to invoke MIG-B1 before `transactions` / `full` pipeline actions. | MIG-B1 | Implementer | With flag enabled, backup runs **before** pipeline body; logs path. | Medium |
| MIG-B3 | Snapshot manifest (minimal) | Emit a **JSON or YAML** manifest alongside each backup: timestamp, tool version, list of included **top-level** paths, optional total size (evaluation Section 13.2). | MIG-B1 | Implementer | Manifest file present; parser tested with a **unit test**. | Medium |
| MIG-B4 | Document exclusion rules | Capture rules for **excluding** huge recoverable raw downloads from **full** snapshots if "thin snapshot" mode is used later (evaluation Section 13.2). | MIG-B3 | PM / User | Rules written; aligned with evaluation Section 12.2. | Low |

#### Thin snapshot exclusion rules (MIG-B4)

These rules describe what an **optional “thin” snapshot** may omit compared to a **full** copy of `data/`, consistent with evaluation **Section 12.2** (whole-`data/` preference with **rules to avoid clutter**) and **Section 13.2** (manifests, hygiene, optional thin mode). They do **not** relax **backup-before-run** or **explicit confirmation** for cloud pull/push (evaluation Section 12): thin mode is only about **what files** you choose to include in a given local or future upload bundle.

- **May omit (recoverable or reproducible):** Raw or intermediate pipeline inputs that can be **re-fetched or regenerated** — for example browser/bank downloads under **`data/input/`** (and similar inbox paths), large **`raw/`** / **`clean`** (or `cleaned`) **intermediate** trees under the transactions or holdings pipelines, **virtualenv** directories (`venv/`, `.venv/`), and **tool caches** (`__pycache__/`, `.pytest_cache/`, etc.). Treat **downloaded XLS or bank exports** as the canonical example of “recoverable from banks” (evaluation Section 12.2).
- **Must keep for a useful restore:** The **compiled export** (`data/export/compiled/`, especially `compiled.csv` and holdings export if used), **static mappings** (`data/static/` CSVs until cutover), **`web/data/`** (e.g. heatmap inputs), and — once it exists — the **ledger SQLite file** (`config.ledger_db_file`, e.g. `data/ledger.sqlite`) so **fingerprint-keyed** truth and mappings in the DB are not lost. **Secrets** (`.env`, service accounts) stay **out** of snapshots by policy (evaluation Section 12.6); do not rely on copying “everything” blindly.

If you implement thin mode in tooling, record **included and excluded path patterns** on the **snapshot manifest** (MIG-B3 / evaluation Section 13.2) so restores and audits stay explicit.

**Verification gate (Phase B).**

- **Unit / integration:** New tests for backup helper and manifest parsing; existing `python -m unittest discover -s tests -p "test_*.py"` still passes.
- **Integration checks:** After a test backup, **file count** or **checksum spot-check** on `compiled.csv` copy matches source.
- **Manual:** Trigger pipeline with `--backup-first` (or equivalent); confirm **no** S3/Google call unless explicitly testing those modules.
- **Verification-only:** Review manifest contents against evaluation Section 13.2 checklist.

**Rollback (Phase B).** Restore previous `data/` from pre-Phase-B snapshot; remove bad backup directories. Code rollback via git revert of MIG-B1–B3.

---

### Phase C — SQLite introduction: schema, migrations, single database file

**Goal.** Establish **one SQLite file** as the future **system of record** (evaluation Sections 6, 10, 13.3, 13.10), with **versioned migrations** (evaluation Section 13.3).

| ID | Title | Description | Dependencies | Owner | Acceptance criteria | Risk |
|----|--------|-------------|--------------|-------|---------------------|------|
| MIG-C1 | Choose DB path and config | Add `config` entry for SQLite path (for example `data/ledger.sqlite` under workspace root); **gitignore** updated. | Phase B | Implementer | Path resolves with `FINANCE_WORKSPACE_ROOT`; **`/data/`** in `.gitignore` excludes the file; optional `*.sqlite-wal` / `*.sqlite-shm` patterns for stray sidecars. | Low |
| MIG-C2 | Migration framework | **Hand-rolled migrations** in **`pipeline.ledger.migrate_ledger_db`**: applies **`schema/ledger/full_schema.sql`** on empty DB, then incremental Python/SQL steps; **`schema_migrations`** records versions. No Alembic unless later reconsidered. | MIG-C1 | Implementer | Fresh DB applies migrations idempotently; test creates DB in **temp dir**. | Medium |
| MIG-C3 | Initial ledger table | **DDL:** `schema/ledger/full_schema.sql` (ledger + auxiliary tables + triggers + views). Schema contract: **current `compiled.csv` column headers** **plus** evaluation columns — Section 13.3 (`statement_month`, `first_seen_at`); Section 13.9 timestamps. See `schema/ledger/README.md`. | MIG-C2 | Implementer | `CREATE TABLE` matches that contract; **UNIQUE** on **fingerprint** (evaluation Sections 12.4, 13.4). | High |
| MIG-C4 | Static mapping + holdings tables | Tables for **store** / **store_category**, **similar pairs**, and **holdings** — **same SQLite file** as the ledger. **Fingerprint metadata** is not a separate table: category + identity live on **`ledger_transaction`** (`schema/ledger/full_schema.sql`). **`fingerprint_db.csv` is not imported** — redundant with ledger categories; see Section 5. | MIG-C2 | Implementer | Empty schema loads; holdings model supports the same “all-time” ledger idea as the rest of the migration. | Medium |

**Verification gate (Phase C).**

- **Unit:** Migration tests on empty and upgraded DB.
- **Manual:** Open DB with `sqlite3` CLI: `.schema`, `PRAGMA user_version` or custom version table.
- **Integration:** N/A for production data until Phase D.

**Rollback (Phase C).** Delete `ledger.sqlite`; revert config and migration files from git.

---

### Phase D — One-shot import from CSV and hard cutover (evaluation Section 12.3)

**Goal.** Perform **one-shot import** of existing **`compiled.csv`**, **`stores_to_categories.csv`**, **`similar_pairs.csv`**, and **holdings** as applicable into SQLite, then **stop using CSV as the authoritative ledger** as soon as the implementing change set lands — **no stabilization phase** where the pipeline keeps writing both or keeps `compiled.csv` as source of truth. **`fingerprint_db.csv` is out of scope for import** — redundant with **`ledger_transaction`** category columns (**Section 5**); **MIG-E3** stops writing it.

**Cutover policy (author decision).**

1. A **controlled import** (e.g. **`pipeline.ledger`** helpers or a one-off runner you maintain) loads CSVs into SQLite in a **single** run (author confirms).
2. **Immediately after:** pipeline and apps read/write the **database** only for ledger and mappings. **`compiled.csv` is not** produced or consumed as authoritative output going forward (optional **explicit** CSV **export** for Excel/backup is fine; it is not the compile target).
3. **Maintenance window:** For production cutover, the system stays **unavailable for normal use** from migration start through testing until everything is signed off — see **Section 0.1.1** (no parallel “live on CSV” phase).

| ID | Title | Description | Dependencies | Owner | Acceptance criteria | Risk |
|----|--------|-------------|--------------|-------|---------------------|------|
| MIG-D1 | Import: ledger (API) | Deterministic import via **`pipeline.ledger`** (`import_web_totals_to_ledger`, etc.): row order preserved or documented; **fingerprints** unique; failed duplicates **reported**, not silent drop. | Phase C | Implementer | Import completes; log prints row counts; **UNIQUE** fingerprint holds. | High |
| MIG-D2 | Import: static mappings | Load **`store`** / **`store_category`** and **`similar_category_pair`** from `stores_to_categories.csv` and `similar_pairs.csv` via `pipeline/ledger` (`import_stores_to_ledger`, etc.). **`fingerprint_db.csv`:** **no** import — redundant with **`ledger_transaction.קטגוריה`** (Section 5). Validate row counts vs CSV after import. | Phase C | Implementer | Store and similar-pair row counts match deduped CSVs; triggers satisfied (static vs dynamic stores). | Medium |
| MIG-D3 | Parity report | After import, compare **aggregates** (sum of amount column if present, row counts per year) CSV vs DB. | MIG-D1 | Implementer | Report within **defined tolerance** (exact for counts). | Medium |
| MIG-D4 | Cutover decision log | Record date/time when **SQLite becomes canonical**; freeze direct edits to authoritative CSVs. | MIG-D1–D3 | User / PM | Signed-off note in repo tracker or this document. | Low |

**Static mappings import (MIG-D2 slice).** After `migrate_ledger_db`, call **`import_stores_to_ledger`** from **`pipeline.ledger`** (or sync via **`sync_stores_to_ledger_from_dataframe`**) to populate **`store`**, **`store_category`**, and **`similar_category_pair`**. This is independent of the ledger row import (MIG-D1) but uses the same SQLite file.

**Verification gate (Phase D).**

- **Integration:** Row count **ledger** = CSV data rows; **no duplicate fingerprints**.
- **Integration:** For a sample of rows, **round-trip** key columns (date, amount, fingerprint) match.
- **Manual:** User runs import on a **copy** of production data; validates spot checks.
- **Verification-only:** Independent re-run of import on same inputs yields **byte-identical** DB or documented deterministic variance (only if allowed).

**Rollback (Phase D).** Restore `data/` snapshot from before import; delete SQLite file; continue using CSV-only pipeline (pre-Phase E).

---

### Phase E — Pipeline read/write path: compile to SQLite (merge preparation, evaluation Section 13.1)

**Merge rules (MIG-E1):** **`docs/ledger-merge-ownership.md`** — pipeline-updatable vs user-owned columns for upserts; aligns with evaluation Section 13.1.

**Goal.** **`compile_transactions_main`** upserts **new compiles** into SQLite by **fingerprint** via **`upsert_compiled_dataframe_to_ledger`** (merge rules in **`docs/ledger-merge-ownership.md`**, evaluation Section 13.1). **MIG-E3** is **Done** for ledger-present paths (no sidecar read/write when the ledger file exists). Remaining Phase E work: **MIG-E4** (divergence stub); optional later cleanup of **CSV-only** legacy branches.

| ID | Title | Description | Dependencies | Owner | Acceptance criteria | Risk |
|----|--------|-------------|--------------|-------|---------------------|------|
| MIG-E1 | Merge specification doc | Short **spec** in repo (or section in evaluation doc) listing **pipeline-updatable** vs **user-owned** columns; **refuse** silent overwrite of user fields (evaluation Section 13.1). | Phase D | PM / User | Reviewed and approved. | Medium |
| MIG-E2 | Compiler integration | `Compiler` (or successor) writes **SQLite** as the compile target. **`compiled.csv`** is still written as a **non-authoritative** on-disk mirror/staging when a ledger DB is configured (`save_main`); truth for merge/categorization is the DB. | MIG-E1, Phase D | Implementer | Running compile on **test workspace** updates DB; row counts and key fields match expectations. | High |
| MIG-E3 | Deprecate fingerprint_db.csv | Stop writing **`fingerprint_db.csv`** — it duplicated category data already on **`ledger_transaction`**; there is **no** merge step (Section 5). Categories keyed by **fingerprint** live on **`ledger_transaction`** only (evaluation Section 13.3, 13.10). | MIG-E2 | Implementer | Categorizer reads/writes SQLite ledger; **`fingerprint_db.csv`** removed or export-only. | High |
| MIG-E4 | Divergence detection stub | Compare **local** DB hash or row checksum vs **last export** metadata file for **smoke** tests (evaluation Section 13.1). | MIG-E2 | Implementer | Unit test demonstrates detection of **manual** DB edit vs expected. | Medium |

**Verification gate (Phase E).**

- **Unit / integration:** Compile on fixture data; assert **idempotent** recompile (same fingerprints, no duplicate rows).
- **Integration:** DB state matches expectations from fixtures; **no** requirement to keep CSV round-trip as part of the default compile path (cutover already happened in Phase D).
- **Manual:** Run categorization on **copy** workspace; confirm user-owned fields behave per MIG-E1.

**Rollback (Phase E).** Git revert compiler changes; restore DB and CSV from Phase D snapshot; re-run CSV-only pipeline from backup.

---

### Phase F — Row timestamps and semantic columns (evaluation Sections 12.4, 13.3, 13.9)

**Goal.** Add **`ingested_at`**, **`category_updated_at`**, **`data_updated_at`** with **clear rules**; add **`statement_month` / `first_seen_at`** as needed (evaluation Sections 12.4, 13.3, 13.9). **Progress:** **MIG-F1** and **MIG-F2** are **Done** in code (DDL, triggers, `ingested_at_rules`, categorizer update path). **MIG-F3** (explicit pipeline documentation + broader test coverage for “no fake user times”) remains open.

| ID | Title | Description | Dependencies | Owner | Acceptance criteria | Risk |
|----|--------|-------------|--------------|-------|---------------------|------|
| MIG-F1 | Migration or parity vs reference DDL | **`ingested_at`**, **`category_updated_at`**, **`data_updated_at`** (and related columns) are **already in `schema/ledger/full_schema.sql`**; ensure application migrations match or supersede that file. **Legacy** import sets per evaluation Section 13.9. | Phase E | Implementer | DB schema matches reference; tests cover **INSERT** and **UPDATE** paths. | Medium |
| MIG-F2 | Application + trigger behavior | Reference DDL uses **scoped `AFTER UPDATE`** triggers and **`datetime('now', 'localtime')`** (evaluation Section 13.9). Wire categorizer/pipeline so category changes bump **`category_updated_at`**; **notes** and other data fields bump **`data_updated_at`**; no false user timestamps from pipeline. | MIG-F1 | Implementer | Behavior matches spec; recursion-free. | Medium |
| MIG-F3 | Pipeline rules for timestamps | Document that pipeline **must not** fake user edit times on user-owned fields (evaluation Section 13.9). | MIG-F1 | PM / Implementer | Spec + test cases. | Medium |

**Verification gate (Phase F).**

- **Unit:** SQLite tests for trigger or service-layer behavior.
- **Integration:** After categorizer change, `category_updated_at` non-null where expected.

**Rollback (Phase F).** Restore DB from pre-F backup; revert migration files and code.

---

### Phase G — Google Sheets: push-only, one tab, remove pull dependency (evaluation Sections 12, 12.3, 13.5)

**Goal.** **Stop pulling** ledger authority from Sheets; push **validated** full ledger from SQLite; **one** worksheet for the full history (evaluation Sections 12.3, 13.5).

| ID | Title | Description | Dependencies | Owner | Acceptance criteria | Risk |
|----|--------|-------------|--------------|-------|---------------------|------|
| MIG-G1 | Deprecate `update_local` for ledger | Remove or guard **ledger** pull paths; keep **migration** tooling if needed one-time. | Phase E | Implementer | No default code path overwrites local ledger from Sheets. | High |
| MIG-G2 | Push from DB | `GSLink` (or replacement) reads **from SQLite** export or query; **confirm** step in UI/CLI before push (evaluation Section 12). | MIG-G1 | Implementer | Push requires explicit action + optional second confirm. | Medium |
| MIG-G3 | Single tab configuration | Unify `desktop_totals_sheet_name` / year tabs toward **one** configured tab for full ledger (evaluation Section 12.3). | MIG-G2 | Implementer | Document env vars; PyQt and web agree on tab name for push. | Medium |
| MIG-G4 | Fix heatmap auto-pull | Replace **`ensure_totals_csv_present`** auto network fetch with **explicit** user action or bundled seed file for dev (evaluation Section 12). | None (can parallelize after MIG-G2) | Implementer | First load does **not** hit Google without user opt-in. | Medium |

**Verification gate (Phase G).**

- **Manual:** Fresh install: open heatmap — **no** silent Sheets pull unless user confirms.
- **Integration:** Push produces Sheet row count **consistent** with DB export; **spot-check** columns.
- **Smoke:** Attempt **pull** API on deprecated path — should **fail closed** or log **deprecated**.

**Implementation note (MIG-G4).** `ensure_totals_csv_present` and related auto-pull helpers are **gone** (`tests/test_phase_g_sheets_policy.py`). Heatmap builds pivot data from **`config.ledger_db_file`** via **`load_transactions_dataframe_from_ledger`** (`web_control/heatmap.py`); first load needs a populated ledger file, not a network fetch.

**Rollback (Phase G).** Revert to previous `integrations/google_sheets.py` and web sync behavior via git; restore CSV-led workflow only if Phase E rollback also applied.

---

### Phase H — Cloud backup and restore (S3, evaluation Sections 10.1, 12, 12.1, 12.2)

**Scheduling.** **Deferred** until the **local** SQLite ledger, compile path, and minimum viable read/write flows are stable. **S3** is the intended backend when this phase is implemented; **MinIO-compatible** APIs remain acceptable for dev/testing. Until Phase H ships, rely on **Phase B** local backups and your external copy of `data/`.

**Goal.** Implement **explicit** **pull** (restore) and **push** (upload) of **`data/`** snapshots or **SQLite** plus manifest, with **append-only** remote keys and **divergence** checks (evaluation Sections 12, 12.1).

| ID | Title | Description | Dependencies | Owner | Acceptance criteria | Risk |
|----|--------|-------------|--------------|-------|---------------------|------|
| MIG-H1 | Object layout | Define **S3** key scheme: `prefix/timestamp/machine-id/manifest.json` + bundle (evaluation Section 12.1 append-only). | Phase B | Implementer | Documented; test bucket optional. | Medium |
| MIG-H2 | Upload flow | CLI or web **Confirm** dialog before upload; **no** upload from pipeline default path (evaluation Section 12). | MIG-H1 | Implementer | Dry-run mode writes intended keys without PUT. | Medium |
| MIG-H3 | Download / restore flow | **Confirm** before overwrite local **active** DB; **divergence** check vs local manifest (evaluation Section 13.1). | MIG-H2 | Implementer | Refuses blind overwrite in test scenario. | High |
| MIG-H4 | AWS auth | Prefer **SSO / short-lived** credentials; document **fallback** `.env` keys (evaluation Section 12.6). | MIG-H2 | User / Implementer | README covers auth; no secrets in git. | Medium |

**Verification gate (Phase H).**

- **Integration:** Mock S3 (for example **moto** or localstack) if used; else **dry-run** against real bucket with **test prefix**.
- **Manual:** Restore to **temp** `FINANCE_WORKSPACE_ROOT`; open SQLite; row count matches manifest.

**Rollback (Phase H).** Stop using S3 commands; rely on local backups from Phase B.

---

### Phase I — Web application as primary UX (evaluation Sections 10, 12.5, 13.6)

**Goal.** Shift **browse, categorize, reports** to the **web app**; PyQt remains for **supervised** runs (evaluation Section 12.5).

| ID | Title | Description | Dependencies | Owner | Acceptance criteria | Risk |
|----|--------|-------------|--------------|-------|---------------------|------|
| MIG-I1 | Ledger API from DB | **Categorize** + **heatmap**: **SQLite** via `pipeline.ledger`. | Phase E | Implementer | Categorization and heatmap consistent with DB. | High |
| MIG-I2 | Categorization UX | Categorizer **updates DB** (`update_category_by_fingerprint`, timestamp triggers per **MIG-F2**). **Done** for PyQt + web + queue when `ledger_db_path` is set; legacy CSV mode remains without a DB. | MIG-I1, Phase F | Implementer | E2E manual: category persists after restart. | High |
| MIG-I3 | Reports | Add or migrate **saved SQL** / Python reports per evaluation Section 10 and Section 12 reports stance. | MIG-I1 | Implementer | At least one report documented with command. | Low |

**Verification gate (Phase I).**

- **Manual:** Full session: categorize, refresh, **no** CSV edit required.
- **Automated:** Extend tests where HTTP layer allows fixture DB.

**Rollback (Phase I).** Restore prior `web_control` revision; after hard cutover, emergency fallback is **export from DB** or full restore from backup, not authoritative `compiled.csv`.

---

### Phase J — Deprecate mutable CSV authority (evaluation Section 13.10)

**Goal.** Ensure **ongoing edits** do not live in ad-hoc CSV files; CSV remains **export**, **interchange**, or **bank input** only (evaluation Section 13.10).

| ID | Title | Description | Dependencies | Owner | Acceptance criteria | Risk |
|----|--------|-------------|--------------|-------|---------------------|------|
| MIG-J1 | Remove edit paths to static CSV | Categorizer and mapping editors write **DB** only. | Phase E–I | Implementer | Grep shows no `to_csv` on **authoritative** static files except export jobs. | Medium |
| MIG-J2 | Documentation | Update developer docs: **where** truth lives; how to export for Excel. | MIG-J1 | PM | New developer can follow **one** diagram. | Low |

**Verification gate (Phase J).**

- **Integration grep / CI:** Optional CI rule: fail if authoritative CSV paths are written in non-export modules.

**Rollback (Phase J).** Re-enable CSV writes from prior branch; restore DB from backup.

---

## 3. Critical path

The following tasks **block** the most downstream work:

1. **MIG-C1–C4** — SQLite file, baseline migrations (`migrate_ledger_db` + `full_schema.sql`), ledger + mappings + holdings DDL (everything else assumes a DB).
2. **MIG-D1** — Successful **one-shot** ledger import (unlocks safe pipeline rewiring).
3. **MIG-D4** — **Done 2026-04-11:** formal **canonical SQLite** declaration (operator); not a code deliverable.
4. **MIG-E2–E3** — Compiler → SQLite and sidecar skip (**done** in code). Next on this spine: **MIG-E4**, then web/Sheets consumers.
5. **MIG-G2–G3** — **Done** (Phase G): push from DB + single Totals tab name.
6. **MIG-H3** — Safe restore with **divergence** detection (multi-machine story, evaluation Section 12.1) — **only when Phase H is scheduled**; not on the critical path until local migration is stable.

---

## 4. Verification summary (cross-phase)

**Automated tests (default).**

```text
python -m unittest discover -s tests -p "test_*.py"
```

Run after every phase that touches shared code; add new tests next to `tests/` as features land.

**Integration patterns (where applicable).**

| Check | How |
|--------|-----|
| Row counts | Compare `SELECT COUNT(*)` to CSV `wc -l` minus header after import/export. |
| Fingerprint uniqueness | `SELECT fingerprint, COUNT(*) FROM ledger GROUP BY fingerprint HAVING COUNT(*) > 1` must return **0** rows. |
| Parity at import (Phase D) | MIG-D3 aggregates vs source CSV; after cutover, **no** ongoing CSV-vs-DB parity as part of default compile. |
| Divergence | Inject extra row in SQLite; restore candidate must **flag** mismatch vs manifest. |

**Manual / operational (evaluation Section 12).**

- **Backup-before-run:** Confirm MIG-B2 (or successor) before production pipeline runs.
- **Pull/push:** No **scheduled** or **implicit** sync; user must **confirm** in UI/CLI.
- **Sheets:** Treat Sheet history as **convenience**, not authority (evaluation Section 13.5).

**Marking gate types.**

- **Verification-only:** Phases A (partial), B (partial), D (parity review), H (dry-run restore).
- **Implementation + verification:** Phases C–J as coded tasks complete.

---

## 5. Resolved decisions (author)

These replace the former open-items list; reopen only if implementation uncovers a new gap.

| # | Topic | Decision |
|---|--------|----------|
| 1 | **Ledger schema contract** | **Implemented DDL:** `schema/ledger/full_schema.sql` — **`ledger_transaction`** with **`fingerprint`** (unique), **`notes`**, §13.3 / §13.9 columns; **no** `מזהה עסקה` / row-hash column; **no** `fingerprint_metadata` table. ISO **TEXT** dates/datetimes, **`STRICT`**, local-time trigger defaults. Adjust if new bank CSV columns appear. |
| 2 | **Migrations** | **Hand-rolled** ordered SQL (or equivalent) with a version table — **not** Alembic for now. |
| 3 | **CSV after SQLite** | **No stabilization / dual-write period.** After implementation, **cut off** authoritative CSV usage; SQLite-only for ledger and mappings (optional explicit export remains allowed). |
| 4 | **Cloud object storage** | **S3** is the go-to. **Phase H is deferred** until the **local** system (import, compile, core UI paths) is up and running; rely on local backups until then. |
| 5 | **Holdings vs ledger** | **Same SQLite file** — one database (`ledger_transaction` + **`store`** / **`store_category`** + **`similar_category_pair`** + **`holdings_balance`**, etc.), consistent with a **single all-time ledger** model. |
| 6 | **`fingerprint_db.csv`** | **Not merged and not imported** into SQLite — **redundant** with **`ledger_transaction`** (categories keyed by fingerprint on the ledger row). When a ledger DB file exists, **MIG-E3** behavior skips read/write of the sidecar; **CSV-only** runs without a ledger may still use it until removed in a later cleanup. |
| 7 | **Production cutover window** | **No transition period.** Authoritative cutover runs in a **maintenance window**: system **down** from migration start through post-migration testing until sign-off (**Section 0.1.1**). |

---

## 6. Delegation prompts (optional, for implementation agents)

**Brief: Phase C — SQLite migrations**

- **Context:** `config.py` paths today are CSV-centric; tests use `unittest` and `FINANCE_WORKSPACE_ROOT`. Evaluation Sections 6, 10, 13.3, 13.10 require SQLite as canonical. **Resolved:** hand-rolled migrations in **`pipeline/ledger.py`** (`migrate_ledger_db`); schema = current CSV headers + evaluation columns (Section 5); holdings in **same DB**.
- **Tasks:** Add `ledger.sqlite` path; **hand-rolled** migration runner (**`migrate_ledger_db`**); align with **`schema/ledger/full_schema.sql`** (`ledger_transaction`, `store`/`store_category`, `similar_category_pair`, `holdings_balance`); **UNIQUE(fingerprint)** on ledger; gitignore DB files.
- **Constraints:** No secrets in repo; migrations must run on Windows and Linux paths.
- **Done when:** Fresh DB + tests pass; `unittest` suite green.

**Brief: Phase D — One-shot import and hard cutover**

- **Context:** Production data lives under `data/export/compiled/` and `data/static/` per `config.py`.
- **Tasks:** Deterministic import path; **MIG-D3** parity report vs source CSV; duplicate fingerprint handling **fails loud**; then **remove authoritative CSV path** in the same implementing track (no stabilization window — Section 5). **Progress:** MIG-D1 ledger import and **MIG-D2** static mappings (`pipeline.ledger`) **Done**; **`fingerprint_db.csv`** is **not** part of import (**Section 5**).
- **Constraints:** Align with evaluation Section 12.3 spirit (one-shot); **author decision:** no post-import dual-write.
- **Done when:** Counts and spot checks pass; mainline compile/categorize paths use the ledger DB for ledger/mappings (legacy CSV-only branches may remain until MIG-E3/J1).

**Brief: Phase G — Sheets push-only**

- **Context:** `integrations/google_sheets.py` is **push-only** (no `update_local` / pull). Heatmap uses **`ledger.sqlite`**.
- **Tasks:** (Done) Push from DB export; heatmap from ledger; pull removed.
- **Constraints:** Explicit user confirmation on push; single full-ledger tab (evaluation Section 12.3, 13.5).
- **Done when:** Manual test checklist in Phase G passes; no silent Google calls on first load.

---

## 7. Living document maintenance

After each merged change set, update **Sections 1.4 and 1.6** if the repo or reference DDL changes; update **Section 0.3** task **Status** and **Notes**; use the optional phase rollup checkboxes when a full phase is complete; add **dates** to risky cutovers (especially MIG-D4 and SQLite canonical declaration); append a row to **Document history** (Section end) when layout or task notes materially change.

---

## Document history

| Date | Change |
|------|--------|
| 2026-04-10 | Initial comprehensive migration plan aligned with evaluation Sections 6, 10, 12, 12.3, 13 (including 13.1–13.6, 13.9–13.10). |
| 2026-04-10 | Section 0: execution workflow, where tasks live, and task tracker table with Status/Notes. |
| 2026-04-10 | Section 5: resolved decisions — schema contract, hand-rolled migrations, hard CSV cutover, defer Phase H (S3), single SQLite file including holdings. Phase D/E text and tracker Notes updated. |
| 2026-04-10 | Added `schema/ledger/full_schema.sql` (full SQLite DDL + triggers) and `schema/ledger/README.md`; MIG-C2/C3 references updated. |
| 2026-04-10 | Section 1.6 reference DDL; Section 5 (ledger-only fingerprint data, no row hash); Phase D MIG-D2, Phase F MIG-F1/F2, tracker MIG-D2/E3/F2, delegation prompt; cross-linked to latest schema (no `fingerprint_metadata`, `notes`, localtime). |
| 2026-04-10 | Section 1.7 traceability matrix (MIG-A1); Section 1.8 snapshot procedure (MIG-A2); tracker MIG-A1 Done, MIG-A3 Done (unittest baseline), MIG-A2 In progress pending user dry run. |
| 2026-04-10 | Phase B: `pipeline/backup.py`, CLI `--backup-first`, web control backup checkbox, manifest + test; tracker MIG-B1–B3 Done, MIG-A2 Done. |
| 2026-04-10 | MIG-B4 thin-snapshot exclusion rules (Phase B); MIG-C1 `ledger_db_file`; MIG-C2 `pipeline/ledger.py`; MIG-C3/C4 marked Done — baseline DDL via `full_schema.sql`; tracker + optional `.gitignore` WAL/SHM patterns. |
| 2026-04-10 | MIG-D2 partial: `pipeline/ledger.py`, `tests/test_static_store_import.py`; tracker + Phase D verification note + delegation prompt; MIG-D1 note aligned with v8 fingerprint rules. |
| 2026-04-10 | **Resolved:** `fingerprint_db.csv` — **no** merge/import into SQLite (redundant); MIG-D2 **Done**; Section 5 decision #6; Phase D goal, MIG-C4, MIG-D2/MIG-E3, tracker, delegation prompt, Section 1.4/1.5 updated. |
| 2026-04-10 | Section 0.1.1 **Cutover operations (maintenance window)** — no transition period; downtime through migration + testing; Section 5 decision #7; Phase D cutover policy bullet 3. |
| 2026-04-10 | Clarified in `schema/ledger/README.md`, `docs/ledger-merge-ownership.md`, evaluation §13.4: **מזהה עסקה** and **תאריך עדכון** are not `ledger_transaction` columns; **ingested_at** / **fingerprint** are canonical in SQLite. |
| 2026-04-10 | **Ledger consolidation:** merged former `ledger_migrate`, `ledger_category`, `ledger_dataframe`, `ledger_fingerprint_backfill`, `ledger_constraint_audit`, `ledger_compile_upsert`, `web_totals_import`, and `static_store_import` into **`pipeline/ledger.py`**; all imports use **`pipeline.ledger`**. **Removed** unused **`pipeline/folder_tracking.py`**. **Scripts cleanup:** removed maintained CLIs for backfill, CSV→ledger imports, and compiled-date repair; retained `verify_ledger_integrity.py`, `run_categorize_http_workspace.py`, `web_control_restart.py`. **Docs:** `README.md`, `schema/ledger/README.md`, `docs/ledger-merge-ownership.md` updated for paths. **Tests:** unittest baseline was **30** at this revision (later **32**, then **35** — see newest history row). Tracker MIG-A3 / MIG-D3 notes and **Section 1.4** (repository assessment) rewritten to match. |
| 2026-04-10 | **Reality check:** Tracker and Sections 0–1 aligned with repo — **MIG-C2** notes (v10 migrations; superseded old “max version before baseline” wording); **MIG-E2** **Done** (no `upsert_ledger` / `--upsert-ledger`; compile always passes `ledger_db`); **MIG-E3** **Done** (ledger-present paths skip `fingerprint_db.csv`); **MIG-I1/I2** **In progress**; **MIG-D4** still **Not started**; heatmap remains **`web_totals.csv`**-based. Superseded by next row for unittest count. |
| 2026-04-10 | **MIG-E3 Done:** `categorization/categorizer.py` (`_use_legacy_fingerprint_csv_sidecar`), `pipeline/compiler.py` (`update_fingerprint_db` skips when `config.ledger_db_file` exists); tests `test_auto_categorize_does_not_read_fingerprint_sidecar_when_ledger_file_exists`, `test_update_fingerprint_db_does_not_write_sidecar_when_ledger_file_exists`. Unittest baseline **32** at that edit. Section 1.5 row updated. |
| 2026-04-10 | **Reality pass:** unittest **35**; **MIG-F1/F2** → Done; **MIG-I2** → Done; **MIG-I1** notes split categorize (DB) vs heatmap (`web_totals.csv`); **MIG-E2** clarified — `compiled.csv` still written as non-authoritative staging; Phase rollups A–C checked; **Section 1.4** backups/tests/pipeline wording aligned. |
| 2026-04-10 | **Phase G complete:** Sheets **pull paths removed** from code (not env-gated — `GSLink` has no `update_local` / pull helpers; `totals_sheet_sync` has no `ensure_totals_csv_present` / `refresh_totals_from_cloud`; `desktop_sheets_api` has no `api_pull`). Web/API push+preview from ledger export when `ledger.sqlite` exists; single Totals tab default (`desktop_totals_sheet_name`); PyQt confirm dialogs + `update_cloud(..., confirm=False)`. Heatmap loads from **SQLite** (`web_control/heatmap.py` → `load_transactions_dataframe_from_ledger`). Unittest count at that edit was **39** (later **53** — see 2026-04-11 row). |
| 2026-04-11 | **Reality pass:** unittest baseline **53**; **MIG-I1** → **Done** (per Phase I spec: categorize + heatmap only; **MIG-I3** remains reports); Section 1.4 / 1.8 / tracker / Phase I rollup / document history corrected (removed nonexistent env vars from narrative; snapshot procedure lists `ledger.sqlite`; scripts list includes `fill_installment_statement_months.py`). |
| 2026-04-11 | **MIG-D4 Done:** Operator sign-off — **SQLite (`config.ledger_db_file`) canonical from 2026-04-11**; all other “ledger” representations deprecated for normal use (`compiled.csv` / sidecar / sheet-as-source). Phase D rollup ☑; Sections 0.1, 1.4, 1.5, 1.6, 1.7, header blurb updated. |
