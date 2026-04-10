"""
Credentials helper for **push-only** Google Sheets flows.

Heatmap reads from the SQLite ledger directly — there is no pull from Sheets into ``web_totals.csv``.
"""

from __future__ import annotations

import os

import config


def is_sheets_configured() -> bool:
    cred = (config.GOOGLE_API_USER or "").strip()
    sid = (config.GOOGLE_WORKSHEET_ID or "").strip()
    if not cred or not sid:
        return False
    return os.path.isfile(os.path.expanduser(cred))
