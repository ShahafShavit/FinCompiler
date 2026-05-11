"""
Route inbox (pipeline step 2): classify browser downloads and move them into per-pipeline inbox folders.

Runs after browser download (``pipeline.fetch``). Shared download folder: ``config.download_inbox_dir``.
Pipeline dirs: ``config.holdings_*``, ``config.transactions_*``, and ``config.trade_portfolio_inbox_dir``.
"""

from __future__ import annotations

import glob
import logging
import os
import shutil

import config

log = logging.getLogger("pipeline.route_inbox")

# Balance pivot exports (יתרות in filename)
HOLDINGS_MARKERS = ("יתרות",)


def _is_trade_portfolio_name(filename: str) -> bool:
    lower = filename.lower()
    if "אחזקות" in filename:
        return True
    return "trade-portfolio" in lower or "trade_portfolio" in lower


def _is_holdings_name(filename: str) -> bool:
    return any(m in filename for m in HOLDINGS_MARKERS)


def classify_download_basename(basename: str) -> str:
    """
    Return ``trade_portfolio``, ``holdings``, ``transactions``, or ``unknown`` for a downloaded file name.

    Order: trade-portfolio markers → holdings (יתרות) → else spreadsheets → transactions.
    """
    lower = basename.lower()
    if not lower.endswith((".xls", ".xlsx", ".xlsm")):
        return "unknown"
    if _is_trade_portfolio_name(basename):
        return "trade_portfolio"
    if _is_holdings_name(basename):
        return "holdings"
    return "transactions"


def _unique_dest(dest_dir: str, basename: str) -> str:
    base, ext = os.path.splitext(basename)
    candidate = os.path.join(dest_dir, basename)
    n = 1
    while os.path.exists(candidate):
        candidate = os.path.join(dest_dir, f"{base}_{n}{ext}")
        n += 1
    return candidate


def route_shared_download_inbox(
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Move every ``*.xls*`` from the shared Chrome download folder into a pipeline inbox.

    Unknown extensions go to ``config.unclassified_download_dir``.

    Returns counts: moved_trade_portfolio, moved_holdings, moved_transactions, moved_unknown, skipped.
    """
    counts = {
        "moved_trade_portfolio": 0,
        "moved_holdings": 0,
        "moved_transactions": 0,
        "moved_unknown": 0,
        "skipped": 0,
    }
    shared = config.download_inbox_dir
    os.makedirs(config.trade_portfolio_inbox_dir, exist_ok=True)
    os.makedirs(config.holdings_inbox_dir, exist_ok=True)
    os.makedirs(config.transactions_inbox_dir, exist_ok=True)
    os.makedirs(config.unclassified_download_dir, exist_ok=True)

    pattern = os.path.join(shared, "*.xls*")
    paths = [p for p in glob.glob(pattern) if os.path.isfile(p)]
    log.info("Routing %s spreadsheet(s) from shared inbox %s", len(paths), shared)

    for src in paths:
        base = os.path.basename(src)
        kind = classify_download_basename(base)
        if kind == "trade_portfolio":
            dest_root = config.trade_portfolio_inbox_dir
        elif kind == "holdings":
            dest_root = config.holdings_inbox_dir
        elif kind == "transactions":
            dest_root = config.transactions_inbox_dir
        else:
            dest_root = config.unclassified_download_dir
            log.warning(
                "Unclassified download (not a workbook): %s -> %s",
                base,
                dest_root,
            )

        dest = _unique_dest(dest_root, base)
        if dry_run:
            log.info("[dry-run] would move %s -> %s", src, dest)
            if kind == "trade_portfolio":
                counts["moved_trade_portfolio"] += 1
            elif kind == "holdings":
                counts["moved_holdings"] += 1
            elif kind == "transactions":
                counts["moved_transactions"] += 1
            else:
                counts["moved_unknown"] += 1
            continue

        try:
            shutil.move(src, dest)
            log.info("Routed %s -> %s (%s)", base, dest, kind)
            if kind == "trade_portfolio":
                counts["moved_trade_portfolio"] += 1
            elif kind == "holdings":
                counts["moved_holdings"] += 1
            elif kind == "transactions":
                counts["moved_transactions"] += 1
            else:
                counts["moved_unknown"] += 1
        except OSError as e:
            log.error("Failed to move %s: %s", src, e)
            counts["skipped"] += 1

    return counts
