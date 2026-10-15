"""Book list, card-payer map, and SMS session for API fetchers."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response

from integrations.onezero import ProbeError
from ledger.api_sync import BookSyncError, clear_session_field, complete_session, prepare_session, session_status
from ledger.books import active_book_id, active_ledger_path, list_books
from ledger.card_payer import list_card_payers, replace_card_payers


def _json(obj: Any, status: int = 200) -> Response:
    return Response(
        content=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
        status_code=status,
        media_type="application/json; charset=utf-8",
    )


def _session_error(exc: Exception) -> Response:
    body: dict[str, Any] = {"ok": False, "error": "session", "message": str(exc)}
    try:
        status = session_status(active_book_id())
    except BookSyncError:
        status = None
    if status:
        body["state"] = status["state"]
        body["fields"] = status["fields"]
    return _json(body, 400)


def register_book_routes(app: FastAPI) -> None:
    r = APIRouter()

    @r.get("/api/books", include_in_schema=False)
    def books_list() -> Response:
        return _json({"ok": True, "books": list_books(), "active": active_book_id()})

    @r.get("/api/books/card-payers", include_in_schema=False)
    def card_payers_get() -> Response:
        return _json(list_card_payers(active_ledger_path()))

    @r.put("/api/books/card-payers", include_in_schema=False)
    async def card_payers_put(request: Request) -> Response:
        try:
            data = json.loads((await request.body()).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return _json({"ok": False, "error": "invalid_json"}, 400)
        rows = data.get("rows") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return _json({"ok": False, "error": "validation_error", "message": "rows must be a list"}, 400)
        return _json(replace_card_payers(active_ledger_path(), rows))

    @r.get("/api/books/session", include_in_schema=False)
    def session_get() -> Response:
        try:
            payload = session_status(active_book_id())
        except BookSyncError as exc:
            return _json({"ok": False, "error": "session", "message": str(exc)}, 400)
        return _json(payload)

    @r.post("/api/books/session/clear", include_in_schema=False)
    async def session_clear(request: Request) -> Response:
        try:
            data = json.loads((await request.body()).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return _json({"ok": False, "error": "invalid_json"}, 400)
        if not isinstance(data, dict):
            data = {}
        try:
            payload = clear_session_field(active_book_id(), str(data.get("field") or ""))
        except (BookSyncError, ProbeError) as exc:
            return _session_error(exc)
        return _json(payload)

    @r.post("/api/books/session/prepare", include_in_schema=False)
    async def session_prepare(request: Request) -> Response:
        try:
            data = json.loads((await request.body()).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return _json({"ok": False, "error": "invalid_json"}, 400)
        if not isinstance(data, dict):
            data = {}
        try:
            payload = prepare_session(
                active_book_id(),
                email=str(data.get("email") or "") or None,
                phone=str(data.get("phone") or "") or None,
                force_sms=bool(data.get("force_sms")),
            )
        except (BookSyncError, ProbeError) as exc:
            return _session_error(exc)
        return _json(payload)

    @r.post("/api/books/session/complete", include_in_schema=False)
    async def session_complete(request: Request) -> Response:
        try:
            data = json.loads((await request.body()).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return _json({"ok": False, "error": "invalid_json"}, 400)
        if not isinstance(data, dict):
            data = {}
        code = data.get("sms_code")
        try:
            payload = complete_session(
                active_book_id(),
                password=str(data.get("password") or ""),
                sms_code=str(code) if code else None,
            )
        except (BookSyncError, ProbeError) as exc:
            return _session_error(exc)
        return _json(payload)

    app.include_router(r)
