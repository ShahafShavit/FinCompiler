"""Cash wallet HTTP routes (``/api/cash/*``)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, FastAPI, Request, Response

from api import cash
from api.utils import json_bytes_strict

log = logging.getLogger(__name__)


def register_cash_routes(app: FastAPI) -> None:
    r = APIRouter()

    @r.get("/api/cash/summary", include_in_schema=False)
    async def cash_summary(request: Request) -> Response:
        try:
            code, payload = cash.handle_summary(str(request.url.query))
        except Exception:  # noqa: BLE001
            log.exception("GET /api/cash/summary failed")
            code, payload = 500, {
                "ok": False,
                "error": "server_error",
                "message": "cash summary failed (see server log)",
            }
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.get("/api/cash/events", include_in_schema=False)
    async def cash_events(request: Request) -> Response:
        try:
            code, payload = cash.handle_list_events(str(request.url.query))
        except Exception:  # noqa: BLE001
            log.exception("GET /api/cash/events failed")
            code, payload = 500, {
                "ok": False,
                "error": "server_error",
                "message": "cash events failed (see server log)",
                "events": [],
            }
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.post("/api/cash/events", include_in_schema=False)
    async def cash_create(request: Request) -> Response:
        raw = await request.body()
        try:
            code, payload = cash.handle_create_event(raw)
        except Exception:  # noqa: BLE001
            log.exception("POST /api/cash/events failed")
            code, payload = 500, {
                "ok": False,
                "error": "server_error",
                "message": "cash create failed (see server log)",
            }
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.patch("/api/cash/events/{event_id}", include_in_schema=False)
    async def cash_patch(event_id: int, request: Request) -> Response:
        raw = await request.body()
        try:
            code, payload = cash.handle_patch_event(event_id, raw)
        except Exception:  # noqa: BLE001
            log.exception("PATCH /api/cash/events/%s failed", event_id)
            code, payload = 500, {
                "ok": False,
                "error": "server_error",
                "message": "cash patch failed (see server log)",
            }
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.post("/api/cash/backfill", include_in_schema=False)
    async def cash_backfill(request: Request) -> Response:
        raw = await request.body()
        try:
            code, payload = cash.handle_backfill(raw)
        except Exception:  # noqa: BLE001
            log.exception("POST /api/cash/backfill failed")
            code, payload = 500, {
                "ok": False,
                "error": "server_error",
                "message": "cash backfill failed (see server log)",
            }
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.delete("/api/cash/events/{event_id}", include_in_schema=False)
    async def cash_delete(event_id: int) -> Response:
        try:
            code, payload = cash.handle_delete_event(event_id)
        except Exception:  # noqa: BLE001
            log.exception("DELETE /api/cash/events/%s failed", event_id)
            code, payload = 500, {
                "ok": False,
                "error": "server_error",
                "message": "cash delete failed (see server log)",
            }
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    app.include_router(r)
