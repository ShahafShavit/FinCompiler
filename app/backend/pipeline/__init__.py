"""
Headless finance pipelines: holdings (balances) vs transactions.

Use ``python main.py`` / ``python run_pipeline.py`` or import these functions from automation.
Dashboard / web app should call into this package instead of duplicating steps.
"""
from __future__ import annotations

import glob
import logging
import os
from typing import Any, Callable, Iterable, Optional

import pandas as pd

import config

from . import compiler
from . import csv_handler
from . import inbox_router
from . import portal_fetch
from . import spreadsheet_ingest

log = logging.getLogger(__name__)

# Column drops for bank/credit transaction workbooks (same as default web/CLI full profile)
TRANSACTION_DROP_COLUMNS: list[str] = [
    "סכום עסקה",
    "מטבע חיוב",
    "מטבע עסקה מקורי",
    "מטבע מקור",
    "מטבע לחיוב",
    "סכום עסקה מקורי",
    "סכום מקורי",
    "מספר שובר",
    "תאריך חיוב",
    'שער המרה ממטבע מקור/התחשבנות לש"ח',
    "אופן ביצוע ההעסקה",
    "הערות",
    "סוג עסקה",
    "תאריך ערך",
    "הערה",
    "אסמכתא",
    "קטגוריה",
    'היתרה בש"ח',
]

TRANSACTION_DROP_SOURCES = [
    ("מקור עסקה", "כרטיס דביט"),
    ('מקור עסקה', 'ישראכרט בע"מ-י'),
    ('מקור עסקה', "מקס איט פיננ-י"),
    ("מקור עסקה", "פקדון אינטר700"),
]

# Extra drops used by the full web/CLI transaction path (brokerage etc.)
TRANSACTION_DROP_SOURCES_UI_EXTRA = [
    ("מקור עסקה", "קניה-אינטרנט"),
    ("מקור עסקה", "מכירה-אינטרנט"),
    ("מקור עסקה", "פקדון אינטרנט"),
    ("מקור עסקה", "פקדון*"),
    ('מקור עסקה', 'קנית ני"ע'),
    ('מקור עסקה', 'מכירת ני"ע'),
    ('מקור עסקה', 'שינוי בנ"ע'),
    ('מקור עסקה', 'קנית ני""ע'),
    ('מקור עסקה', 'החלפת נייר ערך'),
]


def _notify(msg: str, sink: Optional[Callable[[str], None]]) -> None:
    log.info(msg)
    if sink:
        sink(msg)


def _pipeline_debug_dump() -> bool:
    """When set, mirror legacy cleaned CSVs under pipeline clean dirs for inspection."""
    v = os.environ.get("PIPELINE_DEBUG_DUMP", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def transaction_drop_pairs(
    drop_profile: str,
    drop_sources: Optional[Iterable[tuple[str, str]]] = None,
) -> list[tuple[str, str]]:
    if drop_sources is not None:
        return list(drop_sources)
    if drop_profile == "full":
        return TRANSACTION_DROP_SOURCES + TRANSACTION_DROP_SOURCES_UI_EXTRA
    return list(TRANSACTION_DROP_SOURCES)


def ensure_pipeline_dirs() -> None:
    for d in (
        config.holdings_inbox_dir,
        config.holdings_raw_dir,
        config.holdings_clean_dir,
        config.transactions_inbox_dir,
        config.transactions_raw_dir,
        config.transactions_clean_dir,
        config.unclassified_download_dir,
        config.compiled_dir,
        os.path.dirname(config.transaction_category_file),
    ):
        os.makedirs(d, exist_ok=True)


def route_inbox(*, dry_run: bool = False, sink: Optional[Callable[[str], None]] = None) -> dict[str, int]:
    """Move ``*.xls*`` from the shared download folder into pipeline inboxes."""
    _notify("INBOX ROUTE: classifying shared downloads into pipeline inboxes", sink)
    stats = inbox_router.route_shared_download_inbox(dry_run=dry_run)
    _notify(
        f"INBOX ROUTE done: holdings={stats['moved_holdings']} "
        f"transactions={stats['moved_transactions']} "
        f"unknown={stats['moved_unknown']} skipped={stats['skipped']}",
        sink,
    )
    return stats


def fetch_holdings(*, sink: Optional[Callable[[str], None]] = None) -> None:
    _notify("FETCH: bank holdings export", sink)
    b: portal_fetch.Bank | None = None
    try:
        b = portal_fetch.Bank(config.bank_username, config.bank_password)
        b.download("holdings")
    finally:
        if b is not None:
            b.close()


def fetch_transactions_bank_credit_and_osh(
    *,
    credit: bool = True,
    bank_osh: bool = True,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    """Single Leumi session: optional credit card exports + optional osh (bank transactions)."""
    _notify("FETCH: bank session (credit / osh as requested)", sink)
    downloader: portal_fetch.Bank | None = None
    try:
        downloader = portal_fetch.Bank(config.bank_username, config.bank_password)
        if credit:
            _notify("FETCH: credit (via bank portal)", sink)
            downloader.download("credit")
        if bank_osh:
            _notify("FETCH: bank account transactions (osh)", sink)
            downloader.download("osh", from_date=from_date, to_date=to_date)
    finally:
        if downloader is not None:
            downloader.close()


def run_portal_fetches(
    *,
    holdings: bool = False,
    max_isracard: bool = False,
    bank_credit: bool = False,
    bank_osh: bool = False,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    """
    Run only the selected Selenium downloads into the shared inbox (``data/input/``).

    Order matches ``run_pipeline.py all``: holdings, then Max/Isracard, then one Leumi
    session for the requested credit/osh exports.
    """
    if holdings:
        fetch_holdings(sink=sink)
    if max_isracard:
        fetch_transactions_max_isracard(sink=sink)
    if bank_credit or bank_osh:
        fetch_transactions_bank_credit_and_osh(
            credit=bank_credit,
            bank_osh=bank_osh,
            from_date=from_date,
            to_date=to_date,
            sink=sink,
        )


def fetch_transactions_max_isracard(*, sink: Optional[Callable[[str], None]] = None) -> None:
    """Standalone Max + Isracard downloads (legacy Process type 'credit')."""
    _notify("FETCH: Max credit cards", sink)
    failed = True
    importer = None
    while failed:
        try:
            importer = portal_fetch.MaxCredit(config.max_username, config.max_password)
            try:
                importer.download()
                failed = False
            except FileNotFoundError as e:
                _notify(f"Max retry: {e}", sink)
        except Exception as e:
            _notify(f"Max error, retrying: {e}", sink)
    if importer is not None:
        del importer

    failed = True
    importer = None
    _notify("FETCH: Isracard", sink)
    while failed:
        try:
            importer = portal_fetch.IsracardCredit(
                config.credit_username,
                config.credit_password,
                config.credit_last6,
            )
            try:
                importer.download()
                failed = False
            except FileNotFoundError as e:
                _notify(f"Isracard retry: {e}", sink)
        except Exception as e:
            _notify(f"Isracard error, retrying: {e}", sink)
    if importer is not None:
        del importer


def ingest_holdings_inbox(*, sink: Optional[Callable[[str], None]] = None) -> list[str]:
    """Spreadsheet ingest: holdings pipeline inbox -> holdings raw."""
    paths = sorted(glob.glob(os.path.join(config.holdings_inbox_dir, "*.xls*")))
    _notify(f"INGEST HOLDINGS: {len(paths)} file(s) -> {config.holdings_raw_dir}", sink)
    for p in paths:
        wb = spreadsheet_ingest.RawDownloadedWorkbook(p)
        wb.to_xlsx(target_raw_dir=config.holdings_raw_dir)
    return paths


def ingest_transactions_inbox(*, sink: Optional[Callable[[str], None]] = None) -> list[str]:
    """Spreadsheet ingest: transactions pipeline inbox -> transactions raw."""
    paths = sorted(glob.glob(os.path.join(config.transactions_inbox_dir, "*.xls*")))
    _notify(f"INGEST TRANSACTIONS: {len(paths)} file(s) -> {config.transactions_raw_dir}", sink)
    for p in paths:
        wb = spreadsheet_ingest.RawDownloadedWorkbook(p)
        wb.to_xlsx(target_raw_dir=config.transactions_raw_dir)
    return paths


def csv_from_raw_holdings(*, sink: Optional[Callable[[str], None]] = None) -> None:
    files = glob.glob(os.path.join(config.holdings_raw_dir, "*.xls*"))
    _notify(f"CSV HOLDINGS: {len(files)} workbook(s) -> {config.holdings_clean_dir}", sink)
    for f in files:
        hf = csv_handler.HoldingsFile(f)
        rename_map = {"נכון לתאריך": "תאריך"}
        hf.unify_columns(rename_map)
        hf.to_csv(output_clean_dir=config.holdings_clean_dir)


def csv_from_raw_transactions(
    *,
    drop_profile: str = "full",
    drop_sources: Optional[Iterable[tuple[str, str]]] = None,
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    """Write legacy cleaned CSVs (debug / compatibility). Normal pipeline uses in-memory ingest only."""
    files = glob.glob(os.path.join(config.transactions_raw_dir, "*.xls*"))
    _notify(f"CSV TRANSACTIONS: {len(files)} workbook(s) -> {config.transactions_clean_dir}", sink)
    pairs = transaction_drop_pairs(drop_profile, drop_sources)
    for path in files:
        f = csv_handler.TransactionFile(path)
        f.drop_columns(TRANSACTION_DROP_COLUMNS)
        for col, val in pairs:
            f.drop_by_column_and_value(col, val)
        f.to_csv(output_clean_dir=config.transactions_clean_dir)


def ingest_transactions_to_ledger(
    *,
    drop_profile: str = "full",
    drop_sources: Optional[Iterable[tuple[str, str]]] = None,
    sink: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Normalize raw transaction workbooks and upsert into SQLite (no full-ledger pandas merge)."""
    from pipeline.ledger import dedupe_import_batch_by_fingerprint
    from pipeline.ledger import upsert_compiled_dataframe_to_ledger

    pairs = transaction_drop_pairs(drop_profile, drop_sources)
    files = sorted(glob.glob(os.path.join(config.transactions_raw_dir, "*.xls*")))
    if not files:
        _notify("INGEST TRANSACTIONS: no raw workbooks; skipping", sink)
        return {"rows_upserted": 0, "skipped": True, "db_path": config.ledger_db_file}
    _notify(
        f"INGEST TRANSACTIONS: {len(files)} workbook(s) -> {config.ledger_db_file}",
        sink,
    )
    log.info("phase=normalize: raw dir=%s", config.transactions_raw_dir)
    dfs: list[pd.DataFrame] = []
    for path in files:
        dfs.append(
            csv_handler.load_transaction_clean_dataframe(
                path,
                drop_columns=TRANSACTION_DROP_COLUMNS,
                drop_sources=pairs,
            )
        )
    merged = pd.concat(dfs, ignore_index=True)
    merged = compiler.normalize_transaction_import_dates(merged)
    log.info("phase=dedupe: rows before=%s", len(merged))
    merged = dedupe_import_batch_by_fingerprint(merged)
    log.info("phase=upsert: rows after=%s", len(merged))
    return upsert_compiled_dataframe_to_ledger(merged, config.ledger_db_file)


def compile_holdings_main(*, sink: Optional[Callable[[str], None]] = None) -> None:
    from pipeline.holdings_csv_import import upsert_holdings_wide_to_ledger

    files = sorted(glob.glob(os.path.join(config.holdings_raw_dir, "*.xls*")))
    if not files:
        _notify("COMPILE HOLDINGS: no raw workbooks; skipping", sink)
        return
    _notify(
        f"COMPILE HOLDINGS: {len(files)} raw workbook(s) -> {config.ledger_db_file}",
        sink,
    )
    parts = [csv_handler.load_holdings_unified_wide(f) for f in files]
    merged = pd.concat(parts, ignore_index=True)
    merged["תאריך"] = compiler.parse_post_ingest_date_column(merged["תאריך"])
    merged.drop_duplicates(subset=["תאריך"], keep="last", inplace=True, ignore_index=True)
    # fillna only balance columns — תאריך is datetime64 and cannot take 0.0
    _bal_cols = [c for c in merged.columns if c != "תאריך"]
    if _bal_cols:
        merged[_bal_cols] = merged[_bal_cols].fillna(value=0.0)
    merged["תאריך"] = compiler.parse_post_ingest_date_column(merged["תאריך"])
    merged.sort_values(by="תאריך", inplace=True)
    merged.reset_index(drop=True, inplace=True)
    merged["תאריך"] = merged["תאריך"].dt.date
    upsert_holdings_wide_to_ledger(merged, config.ledger_db_file)


def run_auto_categorize_with_web_remainder(
    *,
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    """Auto-pass on the SQLite ledger; rows that still need a category are handled in the browser at ``/categorize/``."""
    from categorization.categorizer import CategorizeFile
    from categorization.interactive.terminal import TerminalCategorizationHandler

    from pipeline.ledger import migrate_ledger_db

    migrate_ledger_db()
    _notify("CATEGORIZE: running auto pass (remaining questions → web app)", sink)
    cf = CategorizeFile(
        ledger_db_path=config.ledger_db_file,
        interaction_handler=TerminalCategorizationHandler(),
    )
    cf.auto_categorize()
    n = cf.count_rows_needing_category()
    base = (
        f"http://{config.control_http_host}:{int(config.control_http_port)}/categorize/"
    )
    if n:
        _notify(f"CATEGORIZE: {n} transaction(s) still need a category — open {base}", sink)
    else:
        _notify("CATEGORIZE: nothing is missing a category", sink)


def compile_transactions_main(
    *,
    run_auto_categorize: bool = False,
    drop_profile: str = "full",
    drop_sources: Optional[Iterable[tuple[str, str]]] = None,
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    ingest_transactions_to_ledger(
        drop_profile=drop_profile,
        drop_sources=drop_sources,
        sink=sink,
    )
    if run_auto_categorize:
        from categorization.categorizer import CategorizeFile

        _notify("CATEGORIZE: auto pass on ledger", sink)
        categorizer = CategorizeFile(ledger_db_path=config.ledger_db_file)
        categorizer.auto_categorize()

    try:
        from pipeline.installment_statement_months import run_installment_statement_month_fill

        run_installment_statement_month_fill(
            config.ledger_db_file,
            dry_run=False,
            sink=sink,
        )
    except Exception:
        log.exception("compile_transactions_main: installment statement_month fill failed")


def run_holdings_pipeline(
    *,
    fetch: bool = False,
    route: bool = True,
    ingest: bool = True,
    compile_: bool = True,
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    ensure_pipeline_dirs()
    if fetch:
        fetch_holdings(sink=sink)
    if route:
        route_inbox(sink=sink)
    if ingest:
        ingest_holdings_inbox(sink=sink)
    if _pipeline_debug_dump():
        csv_from_raw_holdings(sink=sink)
    if compile_:
        compile_holdings_main(sink=sink)


def run_all_pipelines_after_shared_downloads(
    *,
    drop_profile: str = "full",
    auto_categorize: bool = False,
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    """
    After everything landed in the shared Chrome folder, route once then run
    holdings and transactions pipelines without re-fetching or re-routing.
    """
    ensure_pipeline_dirs()
    route_inbox(sink=sink)
    run_holdings_pipeline(fetch=False, route=False, sink=sink)
    run_transactions_pipeline(
        route=False,
        drop_profile=drop_profile,
        auto_categorize=auto_categorize,
        sink=sink,
    )


def run_transactions_pipeline(
    *,
    fetch_bank_credit: bool = False,
    fetch_bank_osh: bool = False,
    fetch_max_isracard: bool = False,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    route: bool = True,
    ingest: bool = True,
    compile_: bool = True,
    auto_categorize: bool = False,
    drop_profile: str = "full",
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    ensure_pipeline_dirs()
    if fetch_max_isracard:
        fetch_transactions_max_isracard(sink=sink)
    if fetch_bank_credit or fetch_bank_osh:
        fetch_transactions_bank_credit_and_osh(
            credit=fetch_bank_credit,
            bank_osh=fetch_bank_osh,
            from_date=from_date,
            to_date=to_date,
            sink=sink,
        )
    if route:
        route_inbox(sink=sink)
    if ingest:
        ingest_transactions_inbox(sink=sink)
    if _pipeline_debug_dump():
        csv_from_raw_transactions(drop_profile=drop_profile, sink=sink)
    if compile_:
        compile_transactions_main(
            run_auto_categorize=auto_categorize,
            drop_profile=drop_profile,
            sink=sink,
        )


def clean_holdings_workspace(
    *,
    keep_compiled: bool = True,
    include_inbox: bool = False,
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    """Remove intermediates for the holdings pipeline only (default: raw + clean, not inbox)."""
    folders = [
        ("holdings raw", config.holdings_raw_dir),
        ("holdings clean", config.holdings_clean_dir),
    ]
    if include_inbox:
        folders.insert(0, ("holdings inbox", config.holdings_inbox_dir))
    for label, folder in folders:
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    _notify(f"clean holdings: removed {path}", sink)
                except OSError as e:
                    log.warning("Could not remove %s: %s", path, e)
    if not keep_compiled and os.path.isfile(config.holdings_file):
        try:
            os.remove(config.holdings_file)
        except OSError as e:
            log.warning("Could not remove holdings main csv: %s", e)


def clean_transactions_workspace(
    *,
    keep_compiled: bool = True,
    include_inbox: bool = False,
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    """Remove intermediates for the transactions pipeline (default: raw + clean, not inbox)."""
    folders = [config.transactions_raw_dir, config.transactions_clean_dir]
    if include_inbox:
        folders.insert(0, config.transactions_inbox_dir)
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    _notify(f"clean transactions: removed {path}", sink)
                except OSError as e:
                    log.warning("Could not remove %s: %s", path, e)
    if not keep_compiled and os.path.isfile(config.compiled_file):
        try:
            os.remove(config.compiled_file)
        except OSError as e:
            log.warning("Could not remove compiled csv: %s", e)
