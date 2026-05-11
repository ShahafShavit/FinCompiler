"""File-backed categorization queue: same host as the dashboard, no 'active session'.

``/api/summary`` — SQL count of uncategorized rows.

``/api/next`` — optional forward-fill for **static** stores (empty categories only), then the
next uncategorized row for the UI.

``/api/respond`` — one ``UPDATE`` for the answered transaction; forward-fill for other empty
rows for that store runs inside :meth:`api.categorize.CategorizeFile.apply_manual_http_response`
when ``is_static = 1``.

``/api/revise`` — corrections only (no global forward-fill here).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Optional

import config
from api.categorize import CategorizeFile, stable_transaction_key
from .json_safe import json_bytes_strict

log = logging.getLogger(__name__)

_lock = threading.Lock()


def _session_categories(cf: CategorizeFile) -> list[str]:
    cf.load_stores()
    if cf.stores_df is None or cf.stores_df.empty:
        return []
    return sorted({str(x) for x in cf.stores_df["category"].tolist() if str(x).strip()})


def summary() -> dict[str, Any]:
    from pipeline.ledger import count_transactions_needing_manual_category
    from pipeline.ledger import migrate_ledger_db

    migrate_ledger_db()
    path = config.ledger_db_file
    if not os.path.isfile(path):
        return {"open_count": 0, "compiled_exists": False}

    n = count_transactions_needing_manual_category(path)
    return {"open_count": n, "compiled_exists": True}


def _ledger_queue_categorize_file() -> CategorizeFile:
    """Lightweight: stores + SQL helpers only (no full ``ledger_transaction`` dataframe)."""
    return CategorizeFile(
        ledger_db_path=config.ledger_db_file,
        materialize_transactions=False,
    )


def next_payload() -> dict[str, Any]:
    cats: list[str] = []
    with _lock:
        from pipeline.ledger import count_transactions_needing_manual_category
        from pipeline.ledger import forward_fill_uncategorized_for_static_stores_sql
        from pipeline.ledger import load_first_transaction_needing_manual_category
        from pipeline.ledger import migrate_ledger_db

        migrate_ledger_db()
        if not os.path.isfile(config.ledger_db_file):
            return {
                "pending": {"kind": "idle"},
                "open_count": 0,
                "history": [],
                "session_categories": [],
            }

        forward_fill_uncategorized_for_static_stores_sql(config.ledger_db_file)

        cf = _ledger_queue_categorize_file()
        cats = _session_categories(cf)

        for attempt in range(2):
            open_n = count_transactions_needing_manual_category(config.ledger_db_file)
            if open_n == 0:
                return {
                    "pending": {"kind": "idle"},
                    "open_count": 0,
                    "history": [],
                    "session_categories": cats,
                }
            row = load_first_transaction_needing_manual_category(config.ledger_db_file)
            if row is None:
                return {
                    "pending": {"kind": "idle"},
                    "open_count": 0,
                    "history": [],
                    "session_categories": cats,
                }
            try:
                p = cf.build_manual_prompt_for_row(row)
            except ValueError as e:
                log.warning("queue repair: %s", e)
                if attempt == 0:
                    cat = cf.categorize_storename(row, method="auto")
                    if cat is not None:
                        cf._persist_category_for_transaction(stable_transaction_key(row), str(cat))
                        cats = _session_categories(cf)
                        continue
                return {
                    "pending": {"kind": "idle"},
                    "open_count": open_n,
                    "history": [],
                    "session_categories": cats,
                    "error": str(e),
                }
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
            "error": "queue repair failed after retry",
        }


def respond(data: dict[str, Any]) -> Optional[str]:
    tid = str(data.get("prompt_id") or data.get("transaction_id") or "")
    kind = data.get("kind")
    if not tid or not kind:
        return "prompt_id and kind required"
    with _lock:
        from pipeline.ledger import load_first_transaction_needing_manual_category
        from pipeline.ledger import migrate_ledger_db

        migrate_ledger_db()

        cf = _ledger_queue_categorize_file()
        row0 = load_first_transaction_needing_manual_category(config.ledger_db_file)
        if row0 is None:
            return "no unanswered rows"
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
        cf = _ledger_queue_categorize_file()
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
