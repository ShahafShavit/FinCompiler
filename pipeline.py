"""
Headless finance pipelines: holdings (balances) vs transactions.

Use ``run_pipeline.py`` or import these functions from automation.
UI code should call into this module instead of duplicating steps.
"""
from __future__ import annotations

import glob
import logging
import os
import signal
from typing import Callable, Iterable, Optional

import compile_handler
import config
import csv_handler
import inbox_router
import portal_fetch
import spreadsheet_ingest
from categorizer import CategorizeFile
from interactive_categorization import create_interaction_handler

log = logging.getLogger(__name__)

# Column drops for bank/credit transaction workbooks (same as main UI)
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

# Extra drops used by the transactions UI path (brokerage etc.)
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


def ensure_workspace_dirs() -> None:
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
    """Move ``*.xls*`` from the shared download folder into workspace inboxes."""
    _notify("INBOX ROUTE: classifying shared downloads into workspace inboxes", sink)
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
    b = portal_fetch.Bank(config.bank_username, config.bank_password)
    try:
        b.download("holdings")
    finally:
        del b


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
    downloader = portal_fetch.Bank(config.bank_username, config.bank_password)
    try:
        if credit:
            _notify("FETCH: credit (via bank portal)", sink)
            downloader.download("credit")
        if bank_osh:
            _notify("FETCH: bank account transactions (osh)", sink)
            downloader.download("osh", from_date=from_date, to_date=to_date)
    finally:
        del downloader


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
    """Spreadsheet ingest: holdings workspace inbox -> holdings raw."""
    paths = sorted(glob.glob(os.path.join(config.holdings_inbox_dir, "*.xls*")))
    _notify(f"INGEST HOLDINGS: {len(paths)} file(s) -> {config.holdings_raw_dir}", sink)
    for p in paths:
        wb = spreadsheet_ingest.RawDownloadedWorkbook(p)
        wb.to_xlsx(target_raw_dir=config.holdings_raw_dir)
    return paths


def ingest_transactions_inbox(*, sink: Optional[Callable[[str], None]] = None) -> list[str]:
    """Spreadsheet ingest: transactions workspace inbox -> transactions raw."""
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
    files = glob.glob(os.path.join(config.transactions_raw_dir, "*.xls*"))
    _notify(f"CSV TRANSACTIONS: {len(files)} workbook(s) -> {config.transactions_clean_dir}", sink)
    if drop_sources is not None:
        pairs = list(drop_sources)
    elif drop_profile == "full":
        pairs = TRANSACTION_DROP_SOURCES + TRANSACTION_DROP_SOURCES_UI_EXTRA
    else:
        pairs = list(TRANSACTION_DROP_SOURCES)
    for path in files:
        f = csv_handler.TransactionFile(path)
        f.drop_columns(TRANSACTION_DROP_COLUMNS)
        for col, val in pairs:
            f.drop_by_column_and_value(col, val)
        f.to_csv(output_clean_dir=config.transactions_clean_dir)


def compile_holdings_main(*, sink: Optional[Callable[[str], None]] = None) -> None:
    cleaned = glob.glob(os.path.join(config.holdings_clean_dir, "*.csv"))
    if not cleaned:
        _notify("COMPILE HOLDINGS: no CSV in clean dir; skipping", sink)
        return
    _notify(f"COMPILE HOLDINGS: merging {len(cleaned)} CSV -> {config.holdings_file}", sink)
    d = compile_handler.Compiler(config.holdings_file)
    d.__compile_new__(config.holdings_clean_dir, suffix="holdings")
    d.compile_to_main()
    d.save_all()


def run_categorization_interactive(
    *,
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    """
    Same as the GUI categorize step: ``auto_categorize`` then ``manual_categorizer`` using
    ``create_interaction_handler()`` (terminal or HTTP per ``FINANCE_CATEGORIZE_UI``).
    """
    if not os.path.isfile(config.compiled_file):
        _notify("CATEGORIZE: skip (compiled.csv missing)", sink)
        return
    _notify(
        "CATEGORIZE: interactive (auto, then prompts); FINANCE_CATEGORIZE_UI=http uses the browser",
        sink,
    )
    h = create_interaction_handler()
    old_sigint = signal.getsignal(signal.SIGINT)

    def _sigint(_signum, _frame) -> None:  # noqa: ARG001
        # Ensure HTTP server + blocked prompt wait are torn down before KeyboardInterrupt.
        closer = getattr(h, "close", None)
        if callable(closer):
            closer()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _sigint)
    try:
        f = CategorizeFile(config.compiled_file, interaction_handler=h)
        attach = getattr(h, "attach_categorizer", None)
        if callable(attach):
            attach(f)
        f.auto_categorize()
        f.manual_categorizer()
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        closer = getattr(h, "close", None)
        if callable(closer):
            closer()


def compile_transactions_main(
    *,
    run_auto_categorize: bool = False,
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    cleaned = glob.glob(os.path.join(config.transactions_clean_dir, "*.csv"))
    if not cleaned:
        _notify("COMPILE TRANSACTIONS: no CSV in clean dir; skipping", sink)
        return
    _notify(f"COMPILE TRANSACTIONS: merging {len(cleaned)} CSV -> {config.compiled_file}", sink)
    c = compile_handler.Compiler(config.compiled_file)
    c.__compile_new__(config.transactions_clean_dir, suffix="credit")
    c.compile_to_main()
    main_file, _ = c.save_all()
    c.update_fingerprint_db()
    if run_auto_categorize:
        _notify("CATEGORIZE: auto pass on compiled file", sink)
        categorizer = CategorizeFile(main_file)
        categorizer.auto_categorize()


def run_holdings_pipeline(
    *,
    fetch: bool = False,
    route: bool = True,
    ingest: bool = True,
    to_csv: bool = True,
    compile_: bool = True,
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    ensure_workspace_dirs()
    if fetch:
        fetch_holdings(sink=sink)
    if route:
        route_inbox(sink=sink)
    if ingest:
        ingest_holdings_inbox(sink=sink)
    if to_csv:
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
    ensure_workspace_dirs()
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
    to_csv: bool = True,
    compile_: bool = True,
    auto_categorize: bool = False,
    drop_profile: str = "full",
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    ensure_workspace_dirs()
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
    if to_csv:
        csv_from_raw_transactions(drop_profile=drop_profile, sink=sink)
    if compile_:
        compile_transactions_main(run_auto_categorize=auto_categorize, sink=sink)


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
