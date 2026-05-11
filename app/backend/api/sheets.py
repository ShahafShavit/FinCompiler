"""Google Sheets desktop sync (Holdings + ledger-export Totals) for the control server."""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator

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


def sync_pairs() -> list[tuple[str, str]]:
    return config.desktop_sync_sheet_pairs()


def desktop_status() -> dict[str, Any]:
    pairs = [{"sheet": s, "local_path": p} for s, p in sync_pairs()]
    return {
        "configured": is_sheets_configured(),
        "pairs": pairs,
        "worksheet_id_set": bool((google_worksheet_id() or "").strip()),
        "totals_push_source": "ledger_sqlite"
        if os.path.isfile(config.ledger_db_file)
        else "compiled_csv",
    }


@contextmanager
def _desktop_sync_sheet_paths() -> Iterator[tuple[list[str], list[str]]]:
    """
    (Holdings CSV, Totals CSV) paths aligned with :func:`config.desktop_sync_sheet_pairs` sheets.

    When ``ledger.sqlite`` exists, Totals are exported from the DB (canonical); otherwise
    a legacy ``compiled.csv`` path may be used if present.
    """
    cleanup: list[str] = []
    try:
        sheets = [config.desktop_holdings_sheet_name(), config.desktop_totals_sheet_name()]
        if os.path.isfile(config.ledger_db_file):
            fd, tmp = tempfile.mkstemp(prefix="ledger_totals_", suffix=".csv")
            os.close(fd)
            cleanup.append(tmp)
            from ledger import export_transactions_dataframe_to_csv

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
    with _desktop_sync_sheet_paths() as (sheets, paths):
        link = GSLink(_handler())
        report = link.analyze_sync(list(sheets), list(paths), cell_range="A1:ZZ", for_cli=False)
    return {"ok": True, "preview": report}


def desktop_push(*, force: bool) -> tuple[bool, str, dict[str, Any] | None]:
    if not is_sheets_configured():
        return (
            False,
            "Google Sheets not configured — use Settings → Providers.",
            None,
        )
    with _desktop_sync_sheet_paths() as (sheets, paths):
        link = GSLink(_handler())
        try:
            ok, msg, preview = link.push_local_csvs_to_cloud(
                list(sheets), list(paths), special_columns=[], cell_range="A1:ZZ", force=force
            )
        except Exception as e:  # noqa: BLE001
            log.exception("desktop sheets push failed")
            return False, f"{type(e).__name__}: {e}", None
    return ok, msg, preview
