"""Google Sheets desktop sync (Holdings + full-ledger Totals tab) for the control dashboard API."""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator

import config
from integrations.google_sheets import GSLink, GoogleSheetsHandler
from web_control.totals_sheet_sync import is_sheets_configured

log = logging.getLogger(__name__)


def sync_pairs() -> list[tuple[str, str]]:
    return config.desktop_sync_sheet_pairs()


def api_status() -> dict[str, Any]:
    pairs = [{"sheet": s, "local_path": p} for s, p in sync_pairs()]
    return {
        "configured": is_sheets_configured(),
        "pairs": pairs,
        "worksheet_id_set": bool((config.GOOGLE_WORKSHEET_ID or "").strip()),
        "totals_push_source": "ledger_sqlite"
        if os.path.isfile(config.ledger_db_file)
        else "compiled_csv",
    }


@contextmanager
def _desktop_sync_sheet_paths() -> Iterator[tuple[list[str], list[str]]]:
    """
    (Holdings CSV, Totals CSV) paths aligned with :func:`config.desktop_sync_sheet_pairs` sheets.

    When ``ledger.sqlite`` exists, Totals are exported from the DB (canonical); otherwise
    ``compiled.csv`` is used.
    """
    cleanup: list[str] = []
    try:
        sheets = [config.desktop_holdings_sheet_name(), config.desktop_totals_sheet_name()]
        if os.path.isfile(config.ledger_db_file):
            fd, tmp = tempfile.mkstemp(prefix="ledger_totals_", suffix=".csv")
            os.close(fd)
            cleanup.append(tmp)
            from pipeline.ledger import export_transactions_dataframe_to_csv

            export_transactions_dataframe_to_csv(config.ledger_db_file, tmp)
            paths = [config.holdings_file, tmp]
        else:
            paths = [config.holdings_file, config.compiled_file]
        yield sheets, paths
    finally:
        for p in cleanup:
            try:
                os.remove(p)
            except OSError:
                pass


def _handler() -> GoogleSheetsHandler:
    cred = os.path.expanduser((config.GOOGLE_API_USER or "").strip())
    sid = (config.GOOGLE_WORKSHEET_ID or "").strip()
    return GoogleSheetsHandler(cred, sid)


def api_preview() -> dict[str, Any]:
    if not is_sheets_configured():
        return {
            "ok": False,
            "error": "not_configured",
            "message": "Set GOOGLE_API_USER and GOOGLE_WORKSHEET_ID (and a valid service-account JSON path).",
        }
    with _desktop_sync_sheet_paths() as (sheets, paths):
        link = GSLink(_handler())
        report = link.analyze_sync(list(sheets), list(paths), cell_range="A1:ZZ", for_cli=False)
    return {"ok": True, "preview": report}


def api_push(*, force: bool) -> tuple[bool, str, dict[str, Any] | None]:
    if not is_sheets_configured():
        return (
            False,
            "Google Sheets not configured (GOOGLE_API_USER / GOOGLE_WORKSHEET_ID).",
            None,
        )
    with _desktop_sync_sheet_paths() as (sheets, paths):
        link = GSLink(_handler())
        try:
            ok, msg, preview = link.push_local_csvs_to_cloud(
                list(sheets), list(paths), special_columns=[], cell_range="A1:ZZ", force=force
            )
        except Exception as e:  # noqa: BLE001
            log.exception("desktop_sheets push failed")
            return False, f"{type(e).__name__}: {e}", None
    return ok, msg, preview
