"""Prediction explorer HTTP routes (``/api/prediction/*``)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, FastAPI, Request, Response

from api import prediction
from api.utils import json_bytes_strict, run_blocking

log = logging.getLogger(__name__)


def register_prediction_routes(app: FastAPI) -> None:
    r = APIRouter()

    @r.get("/api/prediction/status", include_in_schema=False)
    def prediction_status() -> Response:
        try:
            code, payload = prediction.handle_status()
        except Exception:  # noqa: BLE001
            log.exception("GET /api/prediction/status failed")
            code, payload = 500, {
                "ok": False,
                "error": "server_error",
                "message": "prediction status failed (see server log)",
            }
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.get("/api/prediction/sample", include_in_schema=False)
    def prediction_sample(request: Request) -> Response:
        try:
            code, payload = prediction.handle_sample(str(request.url.query))
        except Exception:  # noqa: BLE001
            log.exception("GET /api/prediction/sample failed")
            code, payload = 500, {
                "ok": False,
                "error": "server_error",
                "message": "prediction sample failed (see server log)",
                "rows": [],
            }
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.get("/api/prediction/row", include_in_schema=False)
    def prediction_row(request: Request) -> Response:
        try:
            code, payload = prediction.handle_row(str(request.url.query))
        except Exception:  # noqa: BLE001
            log.exception("GET /api/prediction/row failed")
            code, payload = 500, {
                "ok": False,
                "error": "server_error",
                "message": "prediction row failed (see server log)",
            }
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.post("/api/prediction/score", include_in_schema=False)
    async def prediction_score(request: Request) -> Response:
        raw = await request.body()
        try:
            code, payload = await run_blocking(prediction.handle_score, raw)
        except Exception:  # noqa: BLE001
            log.exception("POST /api/prediction/score failed")
            code, payload = 500, {
                "ok": False,
                "error": "server_error",
                "message": "prediction score failed (see server log)",
                "predictions": [],
            }
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.post("/api/prediction/train", include_in_schema=False)
    async def prediction_train() -> Response:
        try:
            code, payload = await run_blocking(prediction.handle_train)
        except Exception:  # noqa: BLE001
            log.exception("POST /api/prediction/train failed")
            code, payload = 500, {
                "ok": False,
                "error": "server_error",
                "message": "prediction train failed (see server log)",
            }
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    app.include_router(r)
