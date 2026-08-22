"""HTTP handlers for the physical cash wallet (``/api/cash/*``)."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any
from urllib.parse import parse_qs

import config
from ledger import migrate_ledger_db
from ledger.cash import (
    CashError,
    DENOMINATION_AGOROT,
    apply_backfill,
    balance_agorot,
    delete_event,
    insert_count,
    insert_spend_or_receive,
    last_activity_month,
    list_events,
    parse_month,
    patch_event,
    summary as cash_summary,
)

log = logging.getLogger(__name__)


def _ledger_path() -> str:
    return config.ledger_db_file


def _connect() -> sqlite3.Connection:
    path = _ledger_path()
    migrate_ledger_db(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def handle_summary(query: str = "") -> tuple[int, dict[str, Any]]:
    qs = parse_qs(query, keep_blank_values=False)
    month_raw = (qs.get("month") or [""])[0].strip() or None
    path = _ledger_path()
    if not os.path.isfile(path):
        try:
            ym = parse_month(month_raw) if month_raw else None
        except CashError as e:
            return 400, {"ok": False, "error": e.code, "message": e.message}
        from datetime import date as _date

        return 200, {
            "ok": True,
            "balance_agorot": 0,
            "paper_agorot": 0,
            "coin_agorot": 0,
            "pieces_agorot": 0,
            "inventory": {str(d): 0 for d in DENOMINATION_AGOROT},
            "last_count_date": None,
            "last_activity_month": None,
            "month": ym or _date.today().strftime("%Y-%m"),
            "denominations_agorot": list(DENOMINATION_AGOROT),
            "ledger_exists": False,
        }
    conn = _connect()
    try:
        payload = cash_summary(conn, month_raw)
        payload["ledger_exists"] = True
        return 200, payload
    except CashError as e:
        return 400, {"ok": False, "error": e.code, "message": e.message}
    finally:
        conn.close()


def handle_list_events(query: str = "") -> tuple[int, dict[str, Any]]:
    qs = parse_qs(query, keep_blank_values=False)
    month_raw = (qs.get("month") or [""])[0].strip() or None
    # month=all → full history
    all_history = month_raw is not None and month_raw.lower() in ("all", "*")
    path = _ledger_path()
    if not os.path.isfile(path):
        try:
            ym = None if all_history else (parse_month(month_raw) if month_raw else None)
        except CashError as e:
            return 400, {"ok": False, "error": e.code, "message": e.message}
        activity = None
        return 200, {
            "ok": True,
            "month": "all" if all_history else ym,
            "last_activity_month": activity,
            "balance_agorot": 0,
            "events": [],
            "ledger_exists": False,
        }
    conn = _connect()
    try:
        activity = last_activity_month(conn)
        if all_history:
            events = list_events(conn, month=None)
            month_out: str | None = "all"
        elif month_raw:
            ym = parse_month(month_raw)
            events = list_events(conn, month=ym)
            month_out = ym
        else:
            ym = activity or parse_month(None)
            events = list_events(conn, month=ym)
            month_out = ym
        return 200, {
            "ok": True,
            "month": month_out,
            "last_activity_month": activity,
            "balance_agorot": balance_agorot(conn),
            "events": events,
            "ledger_exists": True,
        }
    except CashError as e:
        return 400, {"ok": False, "error": e.code, "message": e.message}
    finally:
        conn.close()


def _optional_str(body: dict[str, Any], key: str) -> tuple[str | None, tuple[int, dict[str, Any]] | None]:
    if key not in body or body[key] is None:
        return None, None
    if not isinstance(body[key], str):
        return None, (400, {"ok": False, "error": f"invalid_{key}", "message": f"{key} must be a string"})
    return body[key], None


def handle_create_event(raw: bytes) -> tuple[int, dict[str, Any]]:
    try:
        body = json.loads(raw.decode("utf-8") if raw else "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"ok": False, "error": "invalid_json", "message": "Body must be JSON"}
    if not isinstance(body, dict):
        return 400, {"ok": False, "error": "invalid_body", "message": "Body must be an object"}

    kind = str(body.get("kind") or "").strip()
    event_date = str(body.get("event_date") or "").strip()
    note, err = _optional_str(body, "note")
    if err:
        return err
    category, err = _optional_str(body, "category")
    if err:
        return err

    conn = _connect()
    try:
        if kind in ("spend", "receive"):
            amount = body.get("amount_agorot")
            if isinstance(amount, bool) or not isinstance(amount, int):
                return 400, {
                    "ok": False,
                    "error": "invalid_agorot",
                    "message": "amount_agorot must be a positive integer",
                }
            dens = body.get("denominations")
            if dens is not None and not isinstance(dens, dict):
                return 400, {
                    "ok": False,
                    "error": "invalid_denominations",
                    "message": "denominations must be an object when provided",
                }
            event = insert_spend_or_receive(
                conn,
                kind=kind,
                event_date=event_date,
                amount_agorot=amount,
                note=note,
                category=category,
                denominations=dens if isinstance(dens, dict) else None,
            )
        elif kind == "count":
            dens = body.get("denominations")
            if not isinstance(dens, dict):
                return 400, {
                    "ok": False,
                    "error": "invalid_denominations",
                    "message": "denominations object required for count",
                }
            event = insert_count(
                conn,
                event_date=event_date,
                denominations=dens,
                note=note,
                category=category,
            )
        else:
            return 400, {
                "ok": False,
                "error": "invalid_kind",
                "message": "kind must be spend, receive, or count",
            }
        return 200, {
            "ok": True,
            "event": event,
            "balance_agorot": balance_agorot(conn),
        }
    except CashError as e:
        return 400, {"ok": False, "error": e.code, "message": e.message}
    finally:
        conn.close()


def handle_patch_event(event_id: int, raw: bytes) -> tuple[int, dict[str, Any]]:
    try:
        body = json.loads(raw.decode("utf-8") if raw else "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"ok": False, "error": "invalid_json", "message": "Body must be JSON"}
    if not isinstance(body, dict):
        return 400, {"ok": False, "error": "invalid_body", "message": "Body must be an object"}

    kwargs: dict[str, Any] = {}
    if "category" in body:
        if body["category"] is not None and not isinstance(body["category"], str):
            return 400, {"ok": False, "error": "invalid_category", "message": "category must be a string"}
        kwargs["category"] = body["category"]
    if "note" in body:
        if body["note"] is not None and not isinstance(body["note"], str):
            return 400, {"ok": False, "error": "invalid_note", "message": "note must be a string"}
        kwargs["note"] = body["note"]
    if "event_date" in body:
        if not isinstance(body["event_date"], str):
            return 400, {"ok": False, "error": "invalid_date", "message": "event_date must be a string"}
        kwargs["event_date"] = body["event_date"]
    if "amount_agorot" in body:
        amount = body["amount_agorot"]
        if isinstance(amount, bool) or not isinstance(amount, int):
            return 400, {
                "ok": False,
                "error": "invalid_agorot",
                "message": "amount_agorot must be a positive integer",
            }
        kwargs["amount_agorot"] = amount
    if not kwargs:
        return 400, {
            "ok": False,
            "error": "empty_patch",
            "message": "Provide category, note, event_date, and/or amount_agorot",
        }

    path = _ledger_path()
    if not os.path.isfile(path):
        return 404, {"ok": False, "error": "not_found", "message": "cash event not found"}

    conn = _connect()
    try:
        event = patch_event(conn, event_id, **kwargs)
        return 200, {"ok": True, "event": event, "balance_agorot": balance_agorot(conn)}
    except CashError as e:
        code = 404 if e.code == "not_found" else 400
        return code, {"ok": False, "error": e.code, "message": e.message}
    finally:
        conn.close()


def handle_delete_event(event_id: int) -> tuple[int, dict[str, Any]]:
    try:
        eid = int(event_id)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "invalid_id", "message": "id must be an integer"}
    if eid <= 0:
        return 400, {"ok": False, "error": "invalid_id", "message": "id must be positive"}

    path = _ledger_path()
    if not os.path.isfile(path):
        return 404, {"ok": False, "error": "not_found", "message": "cash event not found"}

    conn = _connect()
    try:
        deleted = delete_event(conn, eid)
        if not deleted:
            return 404, {"ok": False, "error": "not_found", "message": "cash event not found"}
        return 200, {
            "ok": True,
            "deleted_id": eid,
            "balance_agorot": balance_agorot(conn),
        }
    finally:
        conn.close()


def handle_backfill(raw: bytes) -> tuple[int, dict[str, Any]]:
    try:
        body = json.loads(raw.decode("utf-8") if raw else "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"ok": False, "error": "invalid_json", "message": "Body must be JSON"}
    if not isinstance(body, dict):
        return 400, {"ok": False, "error": "invalid_body", "message": "Body must be an object"}

    text = body.get("text")
    if not isinstance(text, str):
        return 400, {"ok": False, "error": "invalid_text", "message": "text string required"}
    first_row_is_count = body.get("first_row_is_count", True)
    if not isinstance(first_row_is_count, bool):
        return 400, {
            "ok": False,
            "error": "invalid_flag",
            "message": "first_row_is_count must be boolean",
        }

    conn = _connect()
    try:
        payload = apply_backfill(conn, text, first_row_is_count=first_row_is_count)
        return 200, payload
    except CashError as e:
        return 400, {"ok": False, "error": e.code, "message": e.message}
    finally:
        conn.close()
