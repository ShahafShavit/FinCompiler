"""File-backed categorization queue: same host as the dashboard, no 'active session'.

``/categorize/api/summary`` — how many compiled rows still need a category.
``/categorize/api/next`` — first question after an auto pass (or idle).
``/categorize/api/respond`` / ``api/revise`` — apply one answer (first-in-queue for respond).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Optional

import config
from categorization.categorizer import CategorizeFile, category_cell_needs_manual, stable_transaction_key
from categorization.interactive.terminal import TerminalCategorizationHandler
from .json_safe import json_bytes_strict

log = logging.getLogger(__name__)

_lock = threading.Lock()
_MAX_REPAIR = 80


def _terminal_handler() -> TerminalCategorizationHandler:
    return TerminalCategorizationHandler()


def _session_categories(cf: CategorizeFile) -> list[str]:
    cf.load_stores()
    if cf.stores_df is None or cf.stores_df.empty:
        return []
    return sorted({str(x) for x in cf.stores_df["category"].tolist() if str(x).strip()})


def summary() -> dict[str, Any]:
    from pipeline.ledger import load_transactions_dataframe_from_ledger
    from pipeline.ledger import migrate_ledger_db

    migrate_ledger_db()
    path = config.ledger_db_file
    if not os.path.isfile(path):
        return {"open_count": 0, "compiled_exists": False}

    df = load_transactions_dataframe_from_ledger(path)
    if "קטגוריה" not in df.columns:
        return {"open_count": int(len(df)), "compiled_exists": True}
    col = df["קטגוריה"]
    n = int(col.map(lambda c: category_cell_needs_manual(c)).sum())
    return {"open_count": n, "compiled_exists": True}


def next_payload() -> dict[str, Any]:
    cats: list[str] = []
    with _lock:
        from pipeline.ledger import migrate_ledger_db

        migrate_ledger_db()
        if not os.path.isfile(config.ledger_db_file):
            return {
                "pending": {"kind": "idle"},
                "open_count": 0,
                "history": [],
                "session_categories": [],
            }
        cf = CategorizeFile(ledger_db_path=config.ledger_db_file, interaction_handler=_terminal_handler())
        cats = _session_categories(cf)
        for _ in range(_MAX_REPAIR):
            cf.auto_categorize()
            open_n = len(cf.awaiting_df)
            if open_n == 0:
                return {
                    "pending": {"kind": "idle"},
                    "open_count": 0,
                    "history": [],
                    "session_categories": cats,
                }
            row = cf.awaiting_df.iloc[0]
            try:
                p = cf.build_manual_prompt_for_row(row)
            except ValueError as e:
                log.warning("queue repair: %s", e)
                cat = cf.categorize_storename(row, method="auto")
                if cat is None:
                    return {
                        "pending": {"kind": "idle"},
                        "open_count": open_n,
                        "history": [],
                        "session_categories": cats,
                        "error": str(e),
                    }
                cf._persist_category_for_transaction(stable_transaction_key(row), str(cat))
                cats = _session_categories(cf)
                continue
            return {
                "pending": p.to_display_dict(),
                "open_count": open_n,
                "history": [],
                "session_categories": _session_categories(cf),
            }
        return {
            "pending": {"kind": "idle"},
            "open_count": 0,
            "history": [],
            "session_categories": cats,
            "error": "queue repair exceeded; check ledger.sqlite and store mappings",
        }


def respond(data: dict[str, Any]) -> Optional[str]:
    tid = str(data.get("prompt_id") or data.get("transaction_id") or "")
    kind = data.get("kind")
    if not tid or not kind:
        return "prompt_id and kind required"
    with _lock:
        from pipeline.ledger import migrate_ledger_db

        migrate_ledger_db()
        cf = CategorizeFile(ledger_db_path=config.ledger_db_file, interaction_handler=_terminal_handler())
        cf.auto_categorize()
        if cf.awaiting_df.empty:
            return "no unanswered rows"
        row0 = cf.awaiting_df.iloc[0]
        if str(stable_transaction_key(row0)) != tid:
            return "not the first unanswered row; refresh /api/next"
        try:
            p = cf.build_manual_prompt_for_row(row0)
        except ValueError as e:
            return str(e)
        if p.to_display_dict()["kind"] != kind:
            return "kind mismatch"
        try:
            cf.apply_manual_http_response(row0, str(kind), data)
        except ValueError as e:
            return str(e)
    return None


def revise(data: dict[str, Any]) -> Optional[str]:
    with _lock:
        from pipeline.ledger import migrate_ledger_db

        migrate_ledger_db()
        cf = CategorizeFile(ledger_db_path=config.ledger_db_file, interaction_handler=_terminal_handler())
        return cf.apply_queue_revise(data)


def handle_get(path: str) -> tuple[int, bytes, str]:
    path = path.rstrip("/") or "/"
    if path == "/api/summary":
        body = json_bytes_strict(summary())
        return (200, body, "application/json; charset=utf-8")
    if path == "/api/next":
        body = json_bytes_strict(next_payload())
        return (200, body, "application/json; charset=utf-8")
    return (404, b"Not Found", "text/plain")


def handle_post(path: str, raw: bytes) -> tuple[int, bytes, str]:
    path = path.rstrip("/") or "/"
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except json.JSONDecodeError:
        return (
            400,
            json_bytes_strict({"ok": False, "error": "invalid JSON"}),
            "application/json; charset=utf-8",
        )
    if path == "/api/respond":
        err = respond(data)
        if err:
            return (
                400,
                json_bytes_strict({"ok": False, "error": err}),
                "application/json; charset=utf-8",
            )
        return (200, json_bytes_strict({"ok": True}), "application/json; charset=utf-8")
    if path == "/api/revise":
        err = revise(data)
        if err:
            return (
                400,
                json_bytes_strict({"ok": False, "error": err}),
                "application/json; charset=utf-8",
            )
        return (200, json_bytes_strict({"ok": True}), "application/json; charset=utf-8")
    return (404, b"Not Found", "text/plain")
