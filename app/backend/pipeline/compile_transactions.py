"""Compile transactions pipeline step: inbox/raw → normalize → SQLite upsert (+ optional auto-categorize, installment fill)."""

from __future__ import annotations

import glob
import logging
import os
from typing import Any, Callable, Iterable, Optional

import config
import pandas as pd

from . import compiler, spreadsheet_ingest, workbook_normalize
from .transaction_drop_rules import transaction_drop_pairs

log = logging.getLogger(__name__)

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
    "מזהה עסקה",
]


def _notify(msg: str, sink: Optional[Callable[[str], None]]) -> None:
    log.info(msg)
    if sink:
        sink(msg)


def ingest_transactions_inbox(*, sink: Optional[Callable[[str], None]] = None) -> list[str]:
    """Spreadsheet ingest: transactions pipeline inbox -> transactions raw."""
    paths = sorted(glob.glob(os.path.join(config.transactions_inbox_dir, "*.xls*")))
    _notify(f"INGEST TRANSACTIONS: {len(paths)} file(s) -> {config.transactions_raw_dir}", sink)
    for p in paths:
        wb = spreadsheet_ingest.RawDownloadedWorkbook(p)
        wb.to_xlsx(target_raw_dir=config.transactions_raw_dir)
    return paths


def pickle_from_raw_transactions(
    *,
    drop_sources: Optional[Iterable[tuple[str, str]]] = None,
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    """Write normalized transaction frames as pickle (``PIPELINE_DEBUG_DUMP=1`` only)."""
    files = glob.glob(os.path.join(config.transactions_raw_dir, "*.xls*"))
    _notify(f"DEBUG TRANSACTIONS: {len(files)} workbook(s) -> {config.transactions_clean_dir} (*.pkl)", sink)
    pairs = transaction_drop_pairs(drop_sources)
    os.makedirs(config.transactions_clean_dir, exist_ok=True)
    for path in files:
        df = workbook_normalize.load_transaction_clean_dataframe(
            path,
            drop_columns=TRANSACTION_DROP_COLUMNS,
            drop_sources=pairs,
        )
        stem = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(config.transactions_clean_dir, f"{stem}_clean.pkl")
        df.to_pickle(out)
        log.info("debug dump: wrote %s rows=%s", out, len(df))


def ingest_transactions_to_ledger(
    *,
    drop_sources: Optional[Iterable[tuple[str, str]]] = None,
    sink: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Normalize raw transaction workbooks and upsert into SQLite (no full-ledger pandas merge)."""
    pairs = transaction_drop_pairs(drop_sources)
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
            workbook_normalize.load_transaction_clean_dataframe(
                path,
                drop_columns=TRANSACTION_DROP_COLUMNS,
                drop_sources=pairs,
            )
        )
    merged = pd.concat(dfs, ignore_index=True)
    merged = compiler.normalize_transaction_import_dates(merged)
    log.info("phase=dedupe: rows before=%s", len(merged))
    from ledger import (dedupe_import_batch_by_fingerprint,
                        upsert_compiled_dataframe_to_ledger)

    merged = dedupe_import_batch_by_fingerprint(merged)
    log.info("phase=upsert: rows after=%s", len(merged))
    return upsert_compiled_dataframe_to_ledger(merged, config.ledger_db_file)


def compile_transactions_main(
    *,
    run_auto_categorize: bool = False,
    drop_sources: Optional[Iterable[tuple[str, str]]] = None,
    sink: Optional[Callable[[str], None]] = None,
) -> None:
    ingest_transactions_to_ledger(
        drop_sources=drop_sources,
        sink=sink,
    )
    if run_auto_categorize:
        from api.categorize import CategorizeFile

        _notify("CATEGORIZE: auto pass on ledger", sink)
        categorizer = CategorizeFile(ledger_db_path=config.ledger_db_file)
        categorizer.auto_categorize()

    try:
        from ledger import run_installment_statement_month_fill

        run_installment_statement_month_fill(
            config.ledger_db_file,
            dry_run=False,
            sink=sink,
        )
    except Exception:
        log.exception("compile_transactions_main: installment statement_month fill failed")
