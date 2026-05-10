"""HTTP handlers for ``/api/holdings/*`` (GET/POST) — thin routing away from ``server.py``."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import ParseResult, parse_qs

import config
from api.json_safe import json_bytes_strict


def handle_holdings_get(handler: Any, path: str, parsed: ParseResult) -> bool:
    """Return True if the request was handled."""
    if path == "/api/holdings/meta":
        from pipeline.holdings_balance import get_holdings_meta

        body = json_bytes_strict(get_holdings_meta(config.ledger_db_file))
        handler._send(200, body, "application/json; charset=utf-8")
        return True

    if path == "/api/holdings/timeline":
        from pipeline.holdings_balance import query_holdings_timeline

        qs = parse_qs(parsed.query, keep_blank_values=False)
        from_date = (qs.get("from") or [None])[0]
        to_date = (qs.get("to") or [None])[0]
        activities = [str(x) for x in (qs.get("activity") or []) if str(x).strip()]
        try:
            df = query_holdings_timeline(
                config.ledger_db_file,
                start_date=str(from_date).strip() if from_date else None,
                end_date=str(to_date).strip() if to_date else None,
                activity_types=activities,
            )
            payload = {
                "ok": True,
                "rows": df.to_dict(orient="records"),
            }
            handler._send(200, json_bytes_strict(payload), "application/json; charset=utf-8")
        except Exception as e:  # noqa: BLE001
            handler._send(
                400,
                json_bytes_strict({"ok": False, "error": "invalid_request", "message": str(e)}),
                "application/json; charset=utf-8",
            )
        return True

    return False


def handle_holdings_post(handler: Any, path: str) -> bool:
    """Return True if the request was handled."""
    if path == "/api/holdings/parse-paste-grid":
        from pipeline.holdings_balance import parse_holdings_paste_grid

        clen = int(handler.headers.get("Content-Length", "0") or "0")
        raw = handler.rfile.read(clen) if clen > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            handler._send(
                400,
                json_bytes_strict({"ok": False, "error": "invalid_json", "message": "invalid JSON body"}),
                "application/json; charset=utf-8",
            )
            return True
        text = str((data or {}).get("text") or "")
        out = parse_holdings_paste_grid(text)
        handler._send(200, json_bytes_strict(out), "application/json; charset=utf-8")
        return True

    if path == "/api/holdings/check-conflicts":
        from pipeline.holdings_balance import get_holdings_conflicts

        clen = int(handler.headers.get("Content-Length", "0") or "0")
        raw = handler.rfile.read(clen) if clen > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            handler._send(
                400,
                json_bytes_strict({"ok": False, "error": "invalid_json", "message": "invalid JSON body"}),
                "application/json; charset=utf-8",
            )
            return True
        rows = data.get("rows") if isinstance(data.get("rows"), list) else []
        try:
            conflicts = get_holdings_conflicts(rows, config.ledger_db_file)
        except Exception as e:  # noqa: BLE001
            handler._send(
                400,
                json_bytes_strict({"ok": False, "error": "invalid_rows", "message": str(e)}),
                "application/json; charset=utf-8",
            )
            return True
        handler._send(
            200,
            json_bytes_strict({"ok": True, "conflicts": conflicts, "conflict_count": len(conflicts)}),
            "application/json; charset=utf-8",
        )
        return True

    if path == "/api/holdings/manual-upsert-batch":
        from pipeline.holdings_balance import upsert_holdings_rows

        clen = int(handler.headers.get("Content-Length", "0") or "0")
        raw = handler.rfile.read(clen) if clen > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            handler._send(
                400,
                json_bytes_strict({"ok": False, "error": "invalid_json", "message": "invalid JSON body"}),
                "application/json; charset=utf-8",
            )
            return True
        rows = data.get("rows") if isinstance(data.get("rows"), list) else []
        overwrite = bool(data.get("overwrite_conflicts"))
        try:
            out = upsert_holdings_rows(rows, config.ledger_db_file, overwrite_conflicts=overwrite)
        except Exception as e:  # noqa: BLE001
            handler._send(
                400,
                json_bytes_strict({"ok": False, "error": "invalid_rows", "message": str(e)}),
                "application/json; charset=utf-8",
            )
            return True
        code = 200 if out.get("ok") else 409
        handler._send(code, json_bytes_strict(out), "application/json; charset=utf-8")
        return True

    if path == "/api/holdings/move-date":
        from pipeline.holdings_balance import move_holdings_date

        clen = int(handler.headers.get("Content-Length", "0") or "0")
        raw = handler.rfile.read(clen) if clen > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            handler._send(
                400,
                json_bytes_strict({"ok": False, "error": "invalid_json", "message": "invalid JSON body"}),
                "application/json; charset=utf-8",
            )
            return True
        source_date = (data or {}).get("source_date")
        target_date = (data or {}).get("target_date")
        overwrite = bool((data or {}).get("overwrite_conflicts"))
        try:
            out = move_holdings_date(
                source_date,
                target_date,
                config.ledger_db_file,
                overwrite_conflicts=overwrite,
            )
        except Exception as e:  # noqa: BLE001
            handler._send(
                400,
                json_bytes_strict({"ok": False, "error": "invalid_request", "message": str(e)}),
                "application/json; charset=utf-8",
            )
            return True
        code = 200 if out.get("ok") else 409
        handler._send(code, json_bytes_strict(out), "application/json; charset=utf-8")
        return True

    return False
