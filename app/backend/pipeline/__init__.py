"""
Headless finance pipelines: holdings (balances) vs transactions.

Use ``python main.py`` / ``python run_pipeline.py`` or import these functions from automation.
Dashboard / web app should call into this package instead of duplicating steps.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

import config

from . import fetch
from . import route_inbox as route_inbox_mod
from .compile_holdings import compile_holdings_main, ingest_holdings_inbox, pickle_from_raw_holdings
from .compile_transactions import (
    TRANSACTION_DROP_COLUMNS,
    compile_transactions_main,
    ingest_transactions_inbox,
    ingest_transactions_to_ledger,
    pickle_from_raw_transactions,
)
from .transaction_drop_rules import transaction_drop_pairs

import providers

log = logging.getLogger(__name__)


def _notify(msg: str, sink: Optional[Callable[[str], None]]) -> None:
    log.info(msg)
    if sink:
        sink(msg)


def _pipeline_debug_dump() -> bool:
    """When set, write normalized pipeline frames as pickle under pipeline clean dirs for inspection."""
    v = os.environ.get("PIPELINE_DEBUG_DUMP", "").strip().lower()
    return v in ("1", "true", "yes", "on")


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
        config.export_dir,
    ):
        os.makedirs(d, exist_ok=True)


def route_inbox(*, dry_run: bool = False, sink: Optional[Callable[[str], None]] = None) -> dict[str, int]:
    """Move ``*.xls*`` from the shared download folder into pipeline inboxes."""
    _notify("INBOX ROUTE: classifying shared downloads into pipeline inboxes", sink)
    stats = route_inbox_mod.route_shared_download_inbox(dry_run=dry_run)
    _notify(
        f"INBOX ROUTE done: holdings={stats['moved_holdings']} "
        f"transactions={stats['moved_transactions']} "
        f"unknown={stats['moved_unknown']} skipped={stats['skipped']}",
        sink,
    )
    return stats


def fetch_holdings(*, sink: Optional[Callable[[str], None]] = None) -> None:
    _notify("FETCH: bank holdings export", sink)
    p = providers.get_resolved()
    if not p.bank_username or not p.bank_password:
        _notify("FETCH: bank credentials not configured — open Settings → Providers", sink)
        return
    BankCls = providers.bank_class(p.bank_provider)
    b: fetch.Bank | None = None
    try:
        b = BankCls(p.bank_username, p.bank_password)
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
    p = providers.get_resolved()
    if not p.bank_username or not p.bank_password:
        _notify("FETCH: bank credentials not configured — open Settings → Providers", sink)
        return
    BankCls = providers.bank_class(p.bank_provider)
    downloader: fetch.Bank | None = None
    try:
        downloader = BankCls(p.bank_username, p.bank_password)
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


def _fetch_one_credit_portal(
    *,
    card_id: str,
    sink: Optional[Callable[[str], None]],
) -> None:
    p = providers.get_resolved()
    cls = providers.credit_provider_classes().get(card_id)
    if cls is None:
        _notify(f"FETCH: unknown credit provider {card_id!r}", sink)
        return
    if card_id == "max":
        if not p.credit_max_enabled:
            return
        if not p.max_username or not p.max_password:
            _notify("FETCH: Max skipped (credentials not configured in Settings → Providers)", sink)
            return
        user, pw, last6 = p.max_username, p.max_password, None
    elif card_id == "isracard":
        if not p.credit_isracard_enabled:
            return
        if not p.isracard_username or not p.isracard_password or not p.isracard_last6:
            _notify("FETCH: Isracard skipped (credentials not configured in Settings → Providers)", sink)
            return
        user, pw, last6 = p.isracard_username, p.isracard_password, p.isracard_last6
    else:
        return

    label = "Max credit cards" if card_id == "max" else "Isracard"
    _notify(f"FETCH: {label}", sink)
    failed = True
    importer = None
    while failed:
        try:
            if card_id == "isracard":
                importer = cls(user, pw, last6)
            else:
                importer = cls(user, pw)
            try:
                importer.download()
                failed = False
            except FileNotFoundError as e:
                _notify(f"{label} retry: {e}", sink)
        except Exception as e:
            _notify(f"{label} error, retrying: {e}", sink)
    if importer is not None:
        del importer


def fetch_transactions_max_isracard(*, sink: Optional[Callable[[str], None]] = None) -> None:
    """Download enabled credit portals in ``providers.json`` order (max, then isracard)."""
    doc = providers.load_document()
    for card in doc.get("credit_cards") or []:
        if not isinstance(card, dict):
            continue
        cid = str(card.get("id") or "").strip().lower()
        if cid not in providers.credit_provider_classes():
            continue
        if not bool(card.get("enabled")):
            continue
        _fetch_one_credit_portal(card_id=cid, sink=sink)


def run_auto_categorize_with_web_remainder(
    *,
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    """Auto-pass on the SQLite ledger; rows that still need a category are handled in the browser at ``/categorize/``."""
    from api.categorize import CategorizeFile

    from ledger import migrate_ledger_db

    migrate_ledger_db()
    _notify("CATEGORIZE: running auto pass (remaining questions → web app)", sink)
    cf = CategorizeFile(
        ledger_db_path=config.ledger_db_file,
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
        pickle_from_raw_holdings(sink=sink)
    if compile_:
        compile_holdings_main(sink=sink)


def run_all_pipelines_after_shared_downloads(
    *,
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
        pickle_from_raw_transactions(sink=sink)
    if compile_:
        compile_transactions_main(
            run_auto_categorize=auto_categorize,
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
