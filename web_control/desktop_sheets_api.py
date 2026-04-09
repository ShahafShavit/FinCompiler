"""Google Sheets desktop sync (Holdings/Totals year tabs) for the control dashboard API."""

from __future__ import annotations

import logging
import os
from typing import Any

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
    }


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
    sheets, paths = zip(*sync_pairs(), strict=True) if sync_pairs() else ((), ())
    link = GSLink(_handler())
    report = link.analyze_sync(list(sheets), list(paths), cell_range="A1:ZZ", for_cli=False)
    return {"ok": True, "preview": report}


def api_pull(*, force: bool) -> tuple[bool, str, dict[str, Any] | None]:
    if not is_sheets_configured():
        return (
            False,
            "Google Sheets not configured (GOOGLE_API_USER / GOOGLE_WORKSHEET_ID).",
            None,
        )
    sheets, paths = zip(*sync_pairs(), strict=True)
    link = GSLink(_handler())
    try:
        ok, msg, preview = link.pull_desktop_sync_from_cloud(
            list(sheets), list(paths), cell_range="A1:ZZ", regular_data=True, force=force
        )
    except Exception as e:  # noqa: BLE001
        log.exception("desktop_sheets pull failed")
        return False, f"{type(e).__name__}: {e}", None
    return ok, msg, preview


def api_push(*, force: bool) -> tuple[bool, str, dict[str, Any] | None]:
    if not is_sheets_configured():
        return (
            False,
            "Google Sheets not configured (GOOGLE_API_USER / GOOGLE_WORKSHEET_ID).",
            None,
        )
    sheets, paths = zip(*sync_pairs(), strict=True)
    link = GSLink(_handler())
    try:
        ok, msg, preview = link.push_local_csvs_to_cloud(
            list(sheets), list(paths), special_columns=[], cell_range="A1:ZZ", force=force
        )
    except Exception as e:  # noqa: BLE001
        log.exception("desktop_sheets push failed")
        return False, f"{type(e).__name__}: {e}", None
    return ok, msg, preview
