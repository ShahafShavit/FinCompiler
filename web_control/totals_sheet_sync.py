"""
Read-only sync of the cloud **all-time Totals** worksheet into ``web_totals.csv``.

That tab holds the full history in one sheet (not year-split). Used by the heatmap:
auto-fetch when the local file is missing, and manual refresh from the web UI.
Does not run ``update_local`` prompts or categorizer side effects.
"""

from __future__ import annotations

import logging
import os
import threading

import config
from integrations.google_sheets import GSLink, GoogleSheetsHandler

log = logging.getLogger(__name__)

_pull_lock = threading.Lock()


def is_sheets_configured() -> bool:
    cred = (config.GOOGLE_API_USER or "").strip()
    sid = (config.GOOGLE_WORKSHEET_ID or "").strip()
    if not cred or not sid:
        return False
    return os.path.isfile(os.path.expanduser(cred))


def _pull_impl() -> tuple[bool, str]:
    if not is_sheets_configured():
        return False, (
            "Google Sheets not configured: set GOOGLE_API_USER (path to service account JSON) "
            "and GOOGLE_WORKSHEET_ID in the environment."
        )
    cred = os.path.expanduser((config.GOOGLE_API_USER or "").strip())
    sid = (config.GOOGLE_WORKSHEET_ID or "").strip()
    sheet_name = getattr(config, "totals_sheet_name", "Totals")
    try:
        gsh = GoogleSheetsHandler(cred, sid)
        link = GSLink(gsh)
        ok, msg = link.pull_sheet_readonly_to_csv(sheet_name, config.web_totals_file)
    except Exception as e:  # noqa: BLE001
        log.exception("totals_sheet_sync: pull failed")
        return False, f"{type(e).__name__}: {e}"
    if ok:
        from web_control import heatmap

        heatmap.invalidate_bundle_cache()
    return ok, msg


def refresh_totals_from_cloud() -> tuple[bool, str]:
    """Re-fetch the all-time Totals worksheet from the cloud and overwrite ``web_totals.csv``."""
    with _pull_lock:
        return _pull_impl()


def ensure_totals_csv_present() -> tuple[bool, str | None]:
    """
    If ``web_totals.csv`` is missing or empty, pull once from the cloud (read-only).

    Returns ``(True, None)`` when the file already exists and is non-empty, or after a
    successful fetch. Returns ``(False, error)`` when a fetch was required but failed.
    """
    path = config.web_totals_file
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return True, None
    with _pull_lock:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return True, None
        ok, msg = _pull_impl()
        if ok:
            return True, None
        return False, msg


def local_totals_status() -> dict[str, bool | str]:
    path = config.web_totals_file
    exists = os.path.isfile(path) and os.path.getsize(path) > 0
    return {
        "configured": is_sheets_configured(),
        "local_exists": exists,
        "local_path": path,
        "sheet_name": getattr(config, "totals_sheet_name", "Totals"),
    }
