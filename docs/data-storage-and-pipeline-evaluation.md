# Data Storage, Pipeline Execution, and Editing Surfaces

**Status:** Architecture evaluation — constraints, answers, §12 resolutions, §13 future implementation ideas (updated 2026-04-10 for `schema/ledger` alignment).  
**Scope:** Where to run the finance pipeline, where canonical ledger data lives, and how to edit and analyze it — without assuming a single perfect stack.

---

## 1. Purpose

This document separates three decisions that are often conflated:

| Decision | Question |
|----------|----------|
| **Pipeline execution** | Where does fetch / normalize / compile run? |
| **Canonical ledger** | What is the single source of truth for merged, deduplicated transactions? |
| **Editing & analysis** | Where do category fixes, notes, and ad-hoc exploration happen? |

**Principle:** “Local pipeline” does not require “local-only data.” A pipeline can run on a laptop while the ledger is stored in a database file that is backed up to the cloud. Conversely, “data in the cloud” does not require “pipeline in the cloud.”

---

## 2. Pipeline execution: local vs cloud

### 2.1 Local (or self-hosted machine you control)

**Advantages**

- Failures are visible immediately (terminal, log files, optional local dashboard). Browser automation and portal exports fail often; debugging matches the real environment.
- Fast iteration: change code, rerun, no deploy cycle.
- Credentials stay on a machine under your control (still protect secrets; never commit them).

**Disadvantages**

- The machine must be available when the job runs, unless you later move to an always-on host.

### 2.2 Cloud runner (CI, VPS, serverless)

**Advantages**

- Scheduled runs when the laptop is closed (if the stack supports it).

**Disadvantages**

- Bank and credit **portal automation** often conflicts with headless browsers, IP reputation, and captchas; total time saved may be negative.
- Failures are one step removed unless you add alerting and log aggregation.

### 2.3 Recommendation (default stance)

Run **ingest and compile locally** (or on a single home server you SSH into) where Selenium and API failures are observed directly. Use cloud for **backup, sync, or optional read-only mirrors**, not as the primary place to discover automation breakage.

---

## 3. Storage options

### 3.1 Google Sheets as primary database

| Strengths | Weaknesses |
|-----------|--------------|
| Fast human editing; familiar UI | Not relational; weak typing; empty vs zero vs text |
| Version history and rollback for accidents | Integrity (uniqueness, invariants) enforced only in application code |
| Low friction for small schema tweaks in the UI | Schema change still requires every reader/writer to agree; parsing and validation burden |
| Authenticated Google account | API rate limits and latency; ill-suited for tight programmatic loops |

**Assessment:** Strong as an **optional editing or review surface**; fragile as the **canonical schema owner** unless the model stays minimal and all access goes through one well-tested module.

### 3.2 SQLite (single file on disk)

| Strengths | Weaknesses |
|-----------|------------|
| ACID transactions, indexes, constraints (e.g. unique fingerprint) | No spreadsheet-style undo UI; rely on backups or exports |
| One file to copy and back up; works offline | Typical use: one writer at a time (fine for solo pipelines) |
| Real schema evolution via migrations | Comfortable editing may require a small UI or a disciplined import path |
| No separate database server for solo use | Multi-device simultaneous editing needs a clear sync story |

**Assessment:** Strong **canonical store** for a personal finance compiler; pairs naturally with a locally run pipeline that writes the database after compile.

### 3.3 Managed server database (PostgreSQL, etc.)

**Advantages:** Multi-client access, richer operational patterns if a hosted app or multiple writers appear later.

**Disadvantages:** Hosting, TLS, backups, and credentials for a solo project; you still need an application layer for comfortable day-to-day editing.

**Assessment:** Consider when SQLite is genuinely outgrown (multi-user product, strict remote multi-writer requirements). Premature for “still defining operational model.”

### 3.4 Hybrid architecture

| Layer | Role |
|-------|------|
| **Canonical** | SQLite (or, short term, a single disciplined CSV with one import path) |
| **Optional cloud** | Encrypted backup of the database file; optional **generated** Google Sheet for viewing or occasional edits |
| **Sheets edits** | If allowed, apply only through a **validated import** (types, fingerprints, reject bad rows) — not silent bidirectional sync of ambiguous cells |

**Rollback:** Use **timestamped copies** of the SQLite file (and optionally version control on small exported artifacts). Treat Sheets version history as a **supplement** for the mirrored tab, not the only safety net.

---

## 4. Editing and analysis without “everything in Sheets”

Concern: a proper database removes easy modification and analysis.

| Need | Database-only gap | Mitigation |
|------|-------------------|------------|
| Quick category fixes | Less convenient than Sheets | Small local web or TUI on SQLite; or Sheet **generated from** DB plus controlled re-import |
| Pivots and charts | Spreadsheets feel faster | SQL views; DuckDB over SQLite; export CSV for read-only Sheets/Excel |
| Ad-hoc exploration | Steeper than clicking | Saved queries in `reports/` or a notebook |

A database shifts comfortable analysis toward **SQL plus exports**; the trade is **repeatable, integrity-checked** reporting instead of fragile parsing on every sync.

---

## 5. Requirements mapping

| Goal | Suggested direction |
|------|---------------------|
| Observe pipeline failures when they occur | Local (or SSH) execution; persistent logs; optional notifications on failure |
| Authenticated cloud presence | Encrypted backup of DB; optional read-only or generated Sheet |
| Reduce parsing and schema drift | Canonical SQLite with typed columns and constraints |
| Occasional easy edits | Thin editor or validated Sheet round-trip |
| Rollback after bad push | Timestamped DB snapshots; strict import rules; Sheets history for mirror only |

---

## 6. Default stance (actionable baseline)

1. **Pipeline:** local-first execution so failures are visible and fixable.  
2. **Truth:** move toward **SQLite as canonical ledger** (or one CSV treated as a table with a single ingestion path).  
3. **Sheets:** demote to **export / human buffer**, not the authoritative schema.  
4. **Rollback:** automate dated backups of the database file.  
5. **Analysis:** SQL plus occasional CSV export to spreadsheets when charting in a grid is preferable.

This ordering does not require deleting Google Sheets workflows immediately; it **reorders authority** so the software stops treating ambiguous cells as the source of truth.

---

## 7. Constraints (locked)

| Topic | Decision |
|-------|----------|
| **Editing devices** | Desktop only. No mobile support required now or planned. |
| **Users** | Single user (author). No collaborators or external users planned. |
| **Hosted services** | Acceptable for **backups** (and related: off-site recovery). Not a mandate for live application hosting. |

These choices **remove** multi-user collaboration and mobile-first UX from the matrix. Follow-up answers (§9–§10) lock editing to **desktop + local UI**, **SQL-friendly analysis**, **read-only Sheets** as optional cloud view, and **backup-before-run**.

---

## 8. Follow-up questions — original wording (archive)

The questions below were answered in §9. Kept for traceability.

### 8.1 Editing comfort vs structure

1. Spreadsheet habit vs local UI.  
2. SQL tolerance vs click-only.  
3. Undo model: file restore vs per-cell history.

### 8.2 Google Sheets in the future

4. Sheets role: none, read-only generated tabs, or validated pull.  
5. **API / offline:** Whether **daily work** must run **without calling Google**, or **optional** Google API calls (e.g. pushing a read-only Sheet) are acceptable.

### 8.3 Backups and recovery

6. Recovery point (RPO).  
7. Backup destination.  
8. Secrets placement.

### 8.4 Workflow and automation

9. Backup timing vs pipeline.  
10. Failure visibility beyond logs.

### 8.5 Analysis and exports

11. One-click open in Excel vs export when needed.  
12. Reporting in-repo (`.sql` / Python) vs only in apps.

---

## 9. Author answers (2026-04-09)

| # | Topic | Answer |
|---|--------|--------|
| 1 | **Sheets vs categories** | Categories do not need to be fixed in Sheets; Sheets were chosen for **comfort** (in-place editing, browsing). Main benefit is **access from any device** — a nice plus — but **authoritative edits should not live in Sheets**. |
| 2 | **SQL vs UI** | **SQL-based analysis is favorable** (queries, speed). A **desktop UI** for everyday use and views remains **required**. |
| 3 | **Rollback** | **Restoring a previous `.sqlite` from backup** is acceptable. |
| 4 | **Sheets long-term** | **Read-only tabs** (generated from canonical data) best fit. |
| 5 | **Google API / “offline”** *(§9.1)* | — |
| 6 | **RPO** | **Up to one day** or **up to one month** of lost categorization work is **acceptable** for disaster recovery (distinct from pre-run backup frequency). |
| 7 | **Cloud backup** | **S3 is an option**; **IAM and multi-machine** use matter: may want **more than a dumb bucket** — authenticated access from **different machines** at different times, with a clear sync story to the cloud. |
| 8 | **Secrets** | **Same `.env` / keychain style as today** is acceptable for backup credentials. |
| 9 | **When to backup** | **Before running anything** (pipeline / destructive steps), a **backup must be created** first. |
| 10 | **Failure notifications** | **Interesting**; implementation not decided (e.g. Windows toast, system tray, webhook — optional follow-up). |
| 11 | **Excel / pivots** | **Export when needed** is fine. |
| 12 | **Reports in repo** | **Yes** — maintaining `.sql` or Python report scripts in git is **not an issue**. |

### 9.1 Clarification for question 5 (Google APIs vs offline workflow)

**What the question meant:** After data is local, should **day-to-day categorization** work **without any Google dependency** (no API calls until you manually export), or is it acceptable for the app to **call Google APIs** on demand (e.g. to refresh a read-only Sheet)?

**Resolved direction (from §9 + prior answers):**

- **Canonical state and editing** live **locally** (SQLite + desktop UI). No requirement to be online to edit the ledger.
- **Google Sheets** = **optional cloud mirror** via **read-only push**; using the existing service account / API for that is **acceptable** and does not make Sheets the source of truth.
- **“Offline”** in practice: you can work on the DB without Google; **pushing** a view is an explicit, optional step.

---

## 10. Target architecture summary (from answers)

Default **north star** until implementation changes it:

1. **Canonical store:** SQLite on disk — **one ledger table** (transaction rows keyed by **fingerprint**, with category and other fields on the row) **plus** static mapping tables (e.g. store→category, similar pairs) and holdings — all migrate off **mutable CSV**; see **§13.10**. There is **no** separate “fingerprint metadata” table: legacy **`fingerprint_db.csv`** maps into the ledger at import. **Reference DDL:** `schema/ledger/full_schema.sql`.  
2. **Editing:** **Web UI** as the primary entry point for everyday use; **PyQt** remains useful for supervised reruns and testing — **not** Sheets for authoritative edits.  
3. **Analysis:** SQL (saved queries, views, scripts in repo) + UI for common views.  
4. **Sheets:** **One view** (full ledger), **read-only push** — **access anywhere**, **no pull-based merge** for edits.  
5. **Rollback:** Prior **`.sqlite` snapshot** from backup; no per-cell undo requirement.  
6. **Backups:** **Before any pipeline or destructive run**, create a **local** backup; **RPO** for catastrophic loss: day–month acceptable; **destination** likely **S3** (see §12.2); **pull before edit / push after session** are **human-confirmed** only (§12), never silent pipeline hooks.  
7. **Reports:** Scripts in repo are **in scope**.  
8. **Notifications:** **Nice-to-have**; design TBD; low priority while supervised.  
9. **Operational detail:** Resolutions — **§12**; **future merge/snapshot/schema ideas** — **§13**.

### 10.1 Multi-machine sync and AWS (from answer 7)

Running from **different machines** implies:

- **Same canonical artifact** (e.g. `.sqlite`) should be **restorable from cloud** on any machine, with **clear rules** — see **§12.1** (pull before edit, append-only backups, refuse blind overwrite).
- **S3 + auth:** Prefer **SSO / short-lived credentials** when possible (**§12.6**); **IAM user + access keys** in `.env` remains a valid fallback for automation.
- **Orthogonal to Google Sheets:** S3 holds **durable backups / sync blobs**; Sheets remains a **human-readable optional view**.

---

## 11. Related project context

This repository already uses:

- Local paths under `data/` for pipeline stages and `data/export/compiled/` for merged output.
- Google Sheets integration for sync; worksheet naming may split by calendar year for desktop use while other flows use a single all-time tab concept.

Future implementation should align **one canonical store** with those flows rather than duplicating truth across formats without validation.

---

## 12. Operational resolutions (author, 2026-04-09)

Cross-cutting principles:

- **Refuse blind overwrites:** Do not clobber divergent or newer remote state without a defined **merge** step. Prefer detecting **divergence** and eliminating **forks** and **pseudo-corruption** (data that looks fine but is no longer true).
- **Pipeline scope:** Changes produced by the **pipeline** should be **scoped** — they must not rewrite **past** ledger truth in ways that confuse history (exact rules to be encoded in implementation).
- **Merge ideology:** There must be a **clear merging story** (not ad-hoc last save); the default stance is **conservative** (refuse overwrite until merge).
- **Session hygiene:** **Always download the latest backup before editing**; **upload a new backup after a session** (when work is complete).
- **Deliberate cloud sync:** **Pull** (restore from S3) and **push** (upload backup) are **explicit, confirmation-required actions** in the app or CLI — **not** automatic side effects of the pipeline. Fetch/ingest/compile/categorize must **not** silently pull or push to cloud; accidental or scheduled sync without a human OK is **out of scope** unless deliberately redesigned later with the same “confirm” bar.
- **Full `data/` snapshots:** Snapshot the **entire `data/`** tree where practical, with **cleaning rules and guardrails** so backups do not accumulate irrelevant clutter (e.g. huge raw XLS duplicates if deemed recoverable from banks — see §12.2).
- **Encryption:** Not a priority now; data is somewhat sensitive but not highly classified. **SSE-S3** (see §12.2) is enough to consider later; client-side encryption can wait.
- **Single big ledger:** One logical ledger; **bidirectional Sheets** dies slowly in favor of **one-way push** for viewing; **schema flexibility** in SQLite enables **views** that are hard today.

---

### 12.1 Multi-machine workflow — resolved

| Topic | Decision |
|-------|----------|
| **Simultaneous use** | Two machines **would not normally operate at the same time**; remaining conflicts are **pipeline quirks**, not routine multi-writer edits. |
| **Pull before edit** | **Yes** — always align with latest cloud backup before editing, via a **confirmed** action (dialog / explicit CLI flag), **not** auto-download at pipeline start. |
| **Push after session** | **Yes** — upload a new backup after a work session, also **confirmed** — **not** post-pipeline automation unless you explicitly choose it each time (or a separate, clearly labeled “sync now” flow). |
| **Conflict / overwrite** | **Do not silently overwrite** divergent state. **Backup objects in S3 stay append-only** (each snapshot is a new object; **all historical backups remain intact**). Resolving which **active** canonical copy to use may follow **last-write-wins** only **after** explicit merge/acknowledgment, not silent clobber. |
| **Divergence** | **Always check** for divergence; seek to **eliminate forks** and **pseudo-corruption**. |

---

### 12.2 Backup scope and lifecycle — resolved

| Topic | Decision |
|-------|----------|
| **Contents** | Prefer a **whole `data/` snapshot** with **rules** to avoid clutter. **Downloaded XLS artifacts** may be **excluded** (recoverable from banks). **Category and fingerprint-keyed data** live on **`ledger_transaction`** in SQLite; legacy **`fingerprint_db.csv`** is **one-shot merged** into the ledger at migration, not a parallel canonical file. |
| **Retention** | **Effectively unlimited** — no TTL required for now; **S3 lifecycle** deletion not required. Optional manual pruning far in the future if ever needed. |
| **Encryption** | **SSE-S3** is a reasonable default when turning encryption on: **server-side encryption** by S3 (AWS encrypts objects at rest; you avoid a separate app-level crypto step). Client-side encryption deferred. |
| **Restore** | **Restore = pull from S3** when starting work — aligned with **pull before edit**, and **only** after **explicit confirmation** (same rule as §12 intro: no silent restore). The only variation is **what** is pulled (full snapshot vs minimal artifact); practice matches “each time I work with the software,” with clearer packaging over time. |

---

### 12.3 Migration from current stack — resolved

| Topic | Decision |
|-------|----------|
| **CSV → SQLite** | **One-shot import** (no long dual-write phase). |
| **Sheets** | **Stop pulling from the cloud** once the new path is stable; **bidirectional integration** phased out in favor of **one-way** (view-only) push. |
| **Sheet layout** | **No need for year tabs** — **one sheet with the full ledger** is enough for the Google view. |

---

### 12.4 Data semantics — resolved

| Topic | Decision |
|-------|----------|
| **Dates / installments** | Early priority: track **statement month** and/or **first seen** (or similar) alongside existing **transaction date** — supports installments and reporting. |
| **Fingerprinting** | **Central**; may need **technical revision**; any change needs **migration rules**. **Hashing** is brittle to small changes; **fingerprint** is the stable identity — use and document accordingly. |

---

### 12.5 UI and ergonomics — resolved

| Topic | Decision |
|-------|----------|
| **Primary UI** | **Web** is the **new main entry point**; improve the web app over time. **PyQt** was **quick-and-dirty** for reruns, testing, and supervised runs. |
| **Notifications (§9 #10)** | **Still TBD**; **not urgent** while the pipeline is **fully supervised**. |

---

### 12.6 Security and operations — resolved

| Topic | Decision |
|-------|----------|
| **AWS access** | **SSO with short-lived credentials** is attractive — **authenticated from anywhere**, **prompted** when needed, **no long-lived keys** carried around. May require **iteration**; other auth patterns acceptable if they meet the same goals. |
| **Secrets in git** | **Confirmed** — keep `.env`, databases, and backups **out** of version control (`.gitignore` audit). |
| **Cost** | **Negligible** (~order **$0**) for current usage. |

---

### 12.7 Evaluation status

The **architecture evaluation** for storage, sync, and operational rules is **substantively complete**. Remaining work is **implementation** (merge algorithms, snapshot manifests, fingerprint migrations, web UI) and **optional** follow-ups (notifications, stricter encryption). **§13** records those items as **future ideas** to implement against.

---

## 13. Future ideas and implementation backlog

This section is **not** committed scope — it documents **directions** agreed in principle so implementation does not lose context.

### 13.1 Merge specification (technical follow-up)

A formal merge spec should define:

- **Pipeline-produced rows** vs **user-edited fields** (e.g. categories, notes). Pipeline runs should **append or upsert** by **fingerprint** without silently **rewriting** user overrides unless explicitly designed (e.g. “re-categorize uncategorized only”).
- **“Cannot modify past”** — interpreted as: pipeline merges are **scoped** (e.g. new statement period, new downloads) and do not **reinterpret** historical rows in ways that contradict stored truth; exact rules need code-level definition.
- **Divergence detection** — compare local ledger hash or generation counter vs remote backup metadata **before** opening for edit; **refuse** to replace with a stale or conflicting file without a **merge UI** or explicit export/import path.
- **Append-only cloud history** — each upload is a **new object key**; merging “active” state is separate from **immutable** backup history.

### 13.2 Snapshot manifests and `data/` hygiene

- **Manifest per snapshot** — list included paths, optional checksums, optional exclusion list (e.g. `**/inbox/*.xls` if recoverable from banks).
- **Guardrails** — max size, warn on huge raw folders, optional “thin snapshot” mode (ledger + static + small pipeline state only).

### 13.3 Ledger schema evolution

- **`statement_month`** and/or **`first_seen_at`** (or similar) as first-class columns — supports installments and “when did this row enter the system” without fighting provider `תאריך` alone.
- **`fingerprint_db.csv` retired** — categories and identity are on **`ledger_transaction`** (keyed by **`fingerprint`**). Legacy CSV is imported once; **no** second table that mirrors fingerprint → category. **Hand-rolled** SQL migrations and **`schema_migrations`** as in `schema/ledger/full_schema.sql`.
- **Row timestamps (author decision)** — see **§13.9**:
  - **`ingested_at`** — set on **insert** (row first entered the ledger).
  - **`category_updated_at`** — updated when **category-related** fields change.
  - **`data_updated_at`** — updated when **any other** mutable fields change (everything that is not category semantics).

### 13.4 Fingerprint rules and migrations

- Document **current** fingerprint construction; any change ships with a **migration** (map old → new or recompute with stable inputs).
- **Identity = `fingerprint` only** in the SQLite model. Per-row hashes of the full raw row (e.g. legacy **`מזהה עסקה`**) are **not** stored in the reference schema: they change when banks restate minor text on old rows and caused duplicate “new” rows. Optional non-identity hashing for debugging remains a pipeline concern, not a ledger column.
- **תאריך עדכון** is **not** a ledger column. When it appears on a bank CSV or Sheets export, it may be read **only** to derive **`ingested_at`** (first-insert semantics per §13.9); the stored field is always **`ingested_at`**, not a separate “update date” column.

### 13.5 Google Sheets as a generated view

- **One tab**, full ledger, **push-only** from canonical DB after validation.
- **Push to Sheets** follows the same **deliberate, confirmation-required** rule as S3 (§12): not an automatic follow step to every pipeline run unless you explicitly confirm.
- No **pull** path once stable; **version history** in Sheets remains a **convenience**, not authority.

### 13.6 Web app

- Primary UX for browse, categorize, reports; extend over time per §12.5.

### 13.7 Notifications

- When the pipeline runs **unsupervised**, add a channel (desktop toast, webhook, email) — **TBD**; low priority while runs are supervised.

### 13.8 Security and storage (optional hardening)

- **SSE-S3** when encryption is turned on; revisit **client-side** or **KMS** only if threat model changes.
- **S3 lifecycle** — only if manual pruning ever becomes necessary; retention is **unlimited** by default per §12.2.

### 13.9 Ledger row metadata: `ingested_at`, `category_updated_at`, `data_updated_at`

**Author model (replaces a single ambiguous `last_modified`):**

| Column | When it is set |
|--------|----------------|
| **`ingested_at`** | On **insert** — first time the row exists in the ledger (pipeline or bulk migration). |
| **`category_updated_at`** | When **category-related** fields change (user or controlled rule; define exact columns in schema). |
| **`data_updated_at`** | When **any other** mutable fields change — “all the rest” of the row that is not category semantics. |

Optional user **`notes`** (and similar) on the ledger row bump **`data_updated_at`**, not **`category_updated_at`**, per the reference schema triggers.

**Why three columns**

- Separates **first appearance** from **category edits** vs **other edits** (notes, merchant overrides, etc.) without overloading one timestamp.
- Triggers or application code should update **only** the relevant column family for each change.

**Big migration of old records**

- Legacy rows have **no historical** edit times. Options (document in migration script):
  - Set **`ingested_at = migration_run_timestamp`** for all imported rows (or NULL + backfill rule).
  - Leave **`category_updated_at`** / **`data_updated_at`** **NULL** until a real edit or pipeline rule touches them — clearest for “never touched since import.”
- Re-import **replays** must stay **deterministic**.

**Implementation notes**

- The checked-in reference schema (`schema/ledger/full_schema.sql`) uses **`AFTER UPDATE`** triggers that set timestamp columns with a follow-up **`UPDATE`** on the same row, with trigger lists scoped so recursion does not occur. **`BEFORE UPDATE`** assignment to `NEW.*` is an alternative; either approach is acceptable if recursion is ruled out.
- Timestamps in that DDL use **`datetime('now', 'localtime')`** for wall-clock consistency on the host (SQLite has no `DATE` type; dates/datetimes are ISO **TEXT** with **`STRICT`** typing where declared).
- Pipeline merges that only refresh **raw bank fields** might bump **`ingested_at`** on new rows only; **updates** to existing fingerprints should follow rules in **§13.1** so pipeline does not **fake** user edit timestamps.

### 13.10 Canonical storage: SQLite instead of mutable CSV

**Author direction:** Long-term, **application state should not “live” in `.csv` files** even though CSV is convenient to hand-edit. The target is **SQLite** (or a small number of DB files) as **system of record**.

**Implications**

| Today (typical) | Target |
|-----------------|--------|
| `compiled.csv`, `fingerprint_db.csv`, `stores_to_categories.csv`, `similar_pairs.csv`, etc. | **Tables** in one SQLite file (`ledger_transaction` + `store` / `store_category` + `similar_category_pair` + `holdings_balance`, etc.), with **migrations** and **constraints** — see **`schema/ledger/full_schema.sql`**. |
| Pipeline **intermediate** outputs (`clean/*.csv`, exports) | May remain **CSV as interchange** between steps; **not** the canonical place for ongoing edits. |
| Editing mappings | **Web UI** or **SQL** / small tools — not Notepad on a shared CSV as the primary workflow. |

**CSV remains acceptable for:** export snapshots, one-off analysis, dumping to Sheets, **input** files from banks (until ingested). **Not** for ongoing authoritative state once migration is done.

**Migration scope** will be **large** (ledger + static mappings + one-shot merge of legacy **`fingerprint_db.csv`** into the ledger); plan **one-shot or phased** imports with tests, not silent in-place replacement.

### 13.11 Reference DDL (implementation anchor)

The repository’s **authoritative bootstrap script** for the canonical database is **`schema/ledger/full_schema.sql`**, with **`schema/ledger/README.md`** describing SQLite date/time conventions (`STRICT`, ISO 8601 **TEXT**, local-time defaults in triggers). Update this evaluation when the schema’s behavior meaningfully changes.

---

## Document history

| Date | Change |
|------|--------|
| 2026-04-10 | §10, §12.2, §13.3–13.4, §13.9 implementation notes, §13.10, new §13.11: align with `schema/ledger/full_schema.sql` — ledger-only fingerprint/category data, no row-hash column, no fingerprint_metadata table, reference DDL path. |
| 2026-04-09 | Initial evaluation captured from architecture discussion. |
| 2026-04-09 | Locked constraints (desktop-only, single user, hosted backups OK); added follow-up questions §8. |
| 2026-04-09 | Author answers §9, Q5 clarification §9.1, target architecture §10, multi-machine/IAM §10.1; §8 archived. |
| 2026-04-09 | §12 remaining gaps (multi-machine, backup scope, migration, semantics, UI, security). |
| 2026-04-09 | §12 filled with author operational resolutions (merge rules, `data/` snapshots, SSE-S3 note, one-shot import, web-first UI, SSO). |
| 2026-04-09 | §13 future ideas — merge spec, snapshots, schema/fingerprint, Sheets view, web, notifications, optional hardening. |
| 2026-04-09 | Deliberate pull/push (no auto pipeline sync); §13.9 triggers / `last_modified` migration notes. |
| 2026-04-09 | §13.9 three timestamps (`ingested_at`, `category_updated_at`, `data_updated_at`); §13.10 SQLite over mutable CSV. |
