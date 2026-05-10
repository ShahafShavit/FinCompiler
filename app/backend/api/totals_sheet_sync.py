"""
Credentials helper for **push-only** Google Sheets flows.

Heatmap reads from the SQLite ledger directly — there is no pull from Sheets into a local CSV.
"""

from __future__ import annotations

import os

import config


def is_sheets_configured() -> bool:
    from providers import google_api_user_path, google_worksheet_id

    cred = (google_api_user_path() or "").strip()
    sid = (google_worksheet_id() or "").strip()
    if not cred or not sid:
        return False
    return os.path.isfile(os.path.expanduser(cred))
