"""Google Sheets desktop sync (Holdings + ledger Totals) for the control server."""

from __future__ import annotations

import logging
import os
from typing import Any

import config
from integrations.google_sheets import GSLink, GoogleSheetsHandler
from providers import google_api_user_path, google_worksheet_id

log = logging.getLogger(__name__)


def is_sheets_configured() -> bool:
    """True when service-account JSON path and spreadsheet id are set and the key file exists."""
    cred = (google_api_user_path() or "").strip()
    sid = (google_worksheet_id() or "").strip()
    if not cred or not sid:
        return False
    return os.path.isfile(os.path.expanduser(cred))


def desktop_sync_sheet_status() -> list[dict[str, Any]]:
    """One entry per worksheet title (Holdings then Totals); data is always loaded from SQLite."""
    return [{"sheet": s, "local_source": "ledger_sqlite"} for s in config.desktop_sync_sheet_order()]


def _ledger_sync_frames() -> tuple[list[str], list]:
    """Build (sheet titles, DataFrames) for Holdings wide + Totals from ``ledger_db_file``."""
    from ledger import load_transactions_dataframe_from_ledger, migrate_ledger_db
    from pipeline.holdings_balance import holdings_long_to_wide, load_holdings_long_dataframe

    migrate_ledger_db()
    db = config.ledger_db_file
    if not os.path.isfile(db):
        raise FileNotFoundError(f"Ledger database not found: {db}")

    sheets = list(config.desktop_sync_sheet_order())
    long_df = load_holdings_long_dataframe(db)
    holdings_wide = holdings_long_to_wide(long_df)
    tx = load_transactions_dataframe_from_ledger(db)
    totals = tx.drop(columns=["ingested_at", "statement_month"], errors="ignore")
    return sheets, [holdings_wide, totals]


def desktop_status() -> dict[str, Any]:
    return {
        "configured": is_sheets_configured(),
        "pairs": desktop_sync_sheet_status(),
        "worksheet_id_set": bool((google_worksheet_id() or "").strip()),
        "ledger_present": os.path.isfile(config.ledger_db_file),
    }


def _handler() -> GoogleSheetsHandler:
    cred = os.path.expanduser((google_api_user_path() or "").strip())
    sid = (google_worksheet_id() or "").strip()
    return GoogleSheetsHandler(cred, sid)


def desktop_preview() -> dict[str, Any]:
    if not is_sheets_configured():
        return {
            "ok": False,
            "error": "not_configured",
            "message": "Configure Google Sheets in Settings → Providers (service account JSON path and spreadsheet id).",
        }
    try:
        sheets, frames = _ledger_sync_frames()
    except FileNotFoundError as e:
        return {"ok": False, "error": "no_ledger", "message": str(e)}
    link = GSLink(_handler())
    report = link.analyze_sync(list(sheets), list(frames), cell_range="A1:ZZ", for_cli=False)
    return {"ok": True, "preview": report}


def desktop_push(*, force: bool) -> tuple[bool, str, dict[str, Any] | None]:
    if not is_sheets_configured():
        return (
            False,
            "Google Sheets not configured — use Settings → Providers.",
            None,
        )
    try:
        sheets, frames = _ledger_sync_frames()
    except FileNotFoundError as e:
        return False, str(e), None
    link = GSLink(_handler())
    try:
        ok, msg, preview = link.push_dataframes_to_cloud(
            list(sheets), list(frames), special_columns=[], cell_range="A1:ZZ", force=force
        )
    except Exception as e:  # noqa: BLE001
        log.exception("desktop sheets push failed")
        return False, f"{type(e).__name__}: {e}", None
    return ok, msg, preview
