"""Compile holdings pipeline step: inbox/raw workbooks → wide frame → ledger upsert."""

from __future__ import annotations

import glob
import logging
import os
from typing import Callable, Optional

import pandas as pd

import config

from . import compiler, spreadsheet_ingest, workbook_normalize

log = logging.getLogger(__name__)


def _notify(msg: str, sink: Optional[Callable[[str], None]]) -> None:
    log.info(msg)
    if sink:
        sink(msg)


def ingest_holdings_inbox(*, sink: Optional[Callable[[str], None]] = None) -> list[str]:
    """Spreadsheet ingest: holdings pipeline inbox -> holdings raw."""
    paths = sorted(glob.glob(os.path.join(config.holdings_inbox_dir, "*.xls*")))
    _notify(f"INGEST HOLDINGS: {len(paths)} file(s) -> {config.holdings_raw_dir}", sink)
    for p in paths:
        wb = spreadsheet_ingest.RawDownloadedWorkbook(p)
        wb.to_xlsx(target_raw_dir=config.holdings_raw_dir)
    return paths


def csv_from_raw_holdings(*, sink: Optional[Callable[[str], None]] = None) -> None:
    files = glob.glob(os.path.join(config.holdings_raw_dir, "*.xls*"))
    _notify(f"CSV HOLDINGS: {len(files)} workbook(s) -> {config.holdings_clean_dir}", sink)
    for f in files:
        hf = workbook_normalize.HoldingsFile(f)
        rename_map = {"נכון לתאריך": "תאריך"}
        hf.unify_columns(rename_map)
        hf.to_csv(output_clean_dir=config.holdings_clean_dir)


def compile_holdings_main(*, sink: Optional[Callable[[str], None]] = None) -> None:
    from pipeline.holdings_balance import upsert_holdings_wide_to_ledger

    files = sorted(glob.glob(os.path.join(config.holdings_raw_dir, "*.xls*")))
    if not files:
        _notify("COMPILE HOLDINGS: no raw workbooks; skipping", sink)
        return
    _notify(
        f"COMPILE HOLDINGS: {len(files)} raw workbook(s) -> {config.ledger_db_file}",
        sink,
    )
    parts = [workbook_normalize.load_holdings_unified_wide(f) for f in files]
    merged = pd.concat(parts, ignore_index=True)
    merged["תאריך"] = compiler.parse_post_ingest_date_column(merged["תאריך"])
    merged.drop_duplicates(subset=["תאריך"], keep="last", inplace=True, ignore_index=True)
    _bal_cols = [c for c in merged.columns if c != "תאריך"]
    if _bal_cols:
        merged[_bal_cols] = merged[_bal_cols].fillna(value=0.0)
    merged["תאריך"] = compiler.parse_post_ingest_date_column(merged["תאריך"])
    merged.sort_values(by="תאריך", inplace=True)
    merged.reset_index(drop=True, inplace=True)
    merged["תאריך"] = merged["תאריך"].dt.date
    upsert_holdings_wide_to_ledger(merged, config.ledger_db_file)
