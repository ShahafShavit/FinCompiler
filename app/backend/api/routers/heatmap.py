"""Heatmap HTTP routes (`/heatmap/api/*`)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, FastAPI, Request, Response

from api import heatmap
from api.utils import json_bytes_strict, run_blocking

log = logging.getLogger(__name__)


def register_heatmap_routes(app: FastAPI) -> None:
    r = APIRouter()

    @r.get("/heatmap/api/data", include_in_schema=False)
    def heatmap_data(request: Request) -> Response:
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(str(request.url)).query, keep_blank_values=False)
        report_type = (qs.get("type") or [None])[0]
        try:
            snap = heatmap.api_snapshot(report_type)
            body = json_bytes_strict(snap)
        except Exception:  # noqa: BLE001
            log.exception("GET /heatmap/api/data failed")
            err = {
                "ok": False,
                "error": "server_error",
                "message": "Heatmap snapshot failed (see server log).",
                "sourceStatus": {},
                "views": {},
                "statsTables": {},
            }
            body = json_bytes_strict(err)
        return Response(content=body, media_type="application/json; charset=utf-8")

    @r.get("/heatmap/api/detail", include_in_schema=False)
    def heatmap_detail(request: Request) -> Response:
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(str(request.url))
        status = 500
        try:
            status, payload = heatmap.detail_api_payload(
                parse_qs(parsed.query, keep_blank_values=True)
            )
            body = json_bytes_strict(payload)
        except Exception:  # noqa: BLE001
            log.exception("GET /heatmap/api/detail failed")
            body = json_bytes_strict(
                {"ok": False, "error": "server_error", "message": "Detail request failed."}
            )
            status = 500
        return Response(content=body, status_code=status, media_type="application/json; charset=utf-8")

    @r.post("/heatmap/api/refresh", include_in_schema=False)
    async def heatmap_refresh(request: Request) -> Response:
        clen = int(request.headers.get("content-length", "0") or "0")
        if clen > 0:
            await request.body()
        try:
            heatmap.invalidate_bundle_cache()
        except Exception:  # noqa: BLE001
            log.exception("POST /heatmap/api/refresh failed")
            return Response(
                content=json_bytes_strict({"ok": False, "message": "Reload failed (see server log)."}),
                status_code=500,
                media_type="application/json; charset=utf-8",
            )
        return Response(
            content=json_bytes_strict(
                {"ok": True, "message": "Reloaded heatmap data from SQLite ledger."}
            ),
            media_type="application/json; charset=utf-8",
        )

    @r.patch("/heatmap/api/transaction", include_in_schema=False)
    async def heatmap_tx_patch(request: Request) -> Response:
        raw = await request.body()
        try:
            status, payload = await run_blocking(heatmap.patch_ledger_transaction, raw)
            body = json_bytes_strict(payload)
        except Exception:  # noqa: BLE001
            log.exception("PATCH /heatmap/api/transaction failed")
            status = 500
            body = json_bytes_strict(
                {
                    "ok": False,
                    "error": "server_error",
                    "message": "Patch failed (see server log).",
                }
            )
        return Response(content=body, status_code=status, media_type="application/json; charset=utf-8")

    app.include_router(r)
