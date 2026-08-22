"""Dashboard, ledger search, installment-plans, and integrity HTTP routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, FastAPI, Request, Response

from api import dashboard, installment_plans, integrity, ledger_search
from api.utils import json_bytes_strict

log = logging.getLogger(__name__)


def register_dashboard_routes(app: FastAPI) -> None:
    r = APIRouter()

    @r.get("/api/ledger-meta", include_in_schema=False)
    async def ledger_meta() -> Response:
        try:
            payload = dashboard.ledger_meta()
        except Exception:  # noqa: BLE001
            log.exception("GET /api/ledger-meta failed")
            payload = {"ok": False, "exists": False, "mtime_ns": None, "error": "server_error"}
        return Response(
            content=json_bytes_strict(payload),
            media_type="application/json; charset=utf-8",
        )

    @r.get("/api/ledger/search", include_in_schema=False)
    async def ledger_search_api(request: Request) -> Response:
        try:
            code, payload = ledger_search.handle_search_request(str(request.url.query))
        except Exception:  # noqa: BLE001
            log.exception("GET /api/ledger/search failed")
            code, payload = 500, {
                "ok": False,
                "error": "server_error",
                "message": "ledger search failed (see server log)",
                "total": 0,
                "limit": 50,
                "offset": 0,
                "columns": [],
                "rows": [],
            }
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.post("/api/ledger/bulk-patch", include_in_schema=False)
    async def ledger_bulk_patch_api(request: Request) -> Response:
        raw = await request.body()
        try:
            code, payload = ledger_search.handle_bulk_patch(raw)
        except Exception:  # noqa: BLE001
            log.exception("POST /api/ledger/bulk-patch failed")
            code, payload = 500, {
                "ok": False,
                "error": "server_error",
                "message": "bulk patch failed (see server log)",
            }
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.get("/api/installment-plans", include_in_schema=False)
    async def installment_plans_list(request: Request) -> Response:
        try:
            code, payload = installment_plans.handle_list_request(str(request.url.query))
        except Exception:  # noqa: BLE001
            log.exception("GET /api/installment-plans failed")
            code, payload = 500, {
                "ok": False,
                "error": "server_error",
                "message": "installment plans failed (see server log)",
                "installments": {"summary": {}, "plans": []},
                "recurring": {"summary": {}, "fees": []},
            }
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.patch("/api/installment-plans/{plan_id:path}", include_in_schema=False)
    async def installment_plans_patch(plan_id: str, request: Request) -> Response:
        raw = await request.body()
        try:
            from urllib.parse import unquote

            code, payload = installment_plans.patch_installment_plan(unquote(plan_id), raw)
        except Exception:  # noqa: BLE001
            log.exception("PATCH /api/installment-plans failed")
            code, payload = 500, {
                "ok": False,
                "error": "server_error",
                "message": "installment plan patch failed (see server log)",
            }
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.get("/api/dashboard/{name:path}", include_in_schema=False)
    async def dashboard_named(name: str, request: Request) -> Response:
        from urllib.parse import parse_qs

        qs = parse_qs(str(request.url.query), keep_blank_values=False)
        try:
            payload = dashboard.handle_dashboard_request(name, qs)
        except Exception:  # noqa: BLE001
            log.exception("GET /api/dashboard/%s failed", name)
            payload = {
                "ok": False,
                "error": "server_error",
                "message": f"dashboard {name!r} failed (see server log)",
                "rows": [],
            }
        return Response(
            content=json_bytes_strict(payload),
            media_type="application/json; charset=utf-8",
        )

    @r.get("/api/integrity/report", include_in_schema=False)
    async def integrity_report() -> Response:
        try:
            payload = integrity.build_integrity_report()
            body = json_bytes_strict(payload)
        except Exception:  # noqa: BLE001
            log.exception("GET /api/integrity/report failed")
            body = json_bytes_strict(
                {
                    "ok": False,
                    "error": "server_error",
                    "message": "Integrity report failed (see server log).",
                    "sections": [],
                }
            )
        return Response(content=body, media_type="application/json; charset=utf-8")

    @r.get("/api/integrity/divergence", include_in_schema=False)
    async def integrity_divergence() -> Response:
        try:
            payload = integrity.divergence_status()
            body = json_bytes_strict(payload)
        except Exception:  # noqa: BLE001
            log.exception("GET /api/integrity/divergence failed")
            body = json_bytes_strict(
                {
                    "ok": False,
                    "error": "server_error",
                    "message": "Divergence check failed (see server log).",
                }
            )
        return Response(content=body, media_type="application/json; charset=utf-8")

    @r.get("/api/integrity/stores", include_in_schema=False)
    async def integrity_stores() -> Response:
        try:
            payload = integrity.list_stores_aggregated()
            body = json_bytes_strict(payload)
        except Exception:  # noqa: BLE001
            log.exception("GET /api/integrity/stores failed")
            body = json_bytes_strict(
                {
                    "ok": False,
                    "error": "server_error",
                    "message": "Stores list failed (see server log).",
                    "stores": [],
                }
            )
        return Response(content=body, media_type="application/json; charset=utf-8")

    @r.get("/api/integrity/top-categories", include_in_schema=False)
    async def integrity_top_categories_get() -> Response:
        try:
            payload = integrity.get_top_categories()
            body = json_bytes_strict(payload)
        except Exception:  # noqa: BLE001
            log.exception("GET /api/integrity/top-categories failed")
            body = json_bytes_strict(
                {
                    "ok": False,
                    "error": "server_error",
                    "message": "Top categories load failed (see server log).",
                    "columns": [],
                    "unassigned": [],
                }
            )
        return Response(content=body, media_type="application/json; charset=utf-8")

    @r.put("/api/integrity/top-categories", include_in_schema=False)
    async def integrity_top_categories_put(request: Request) -> Response:
        raw = await request.body()
        try:
            status, payload = integrity.put_top_categories(raw)
            body = json_bytes_strict(payload)
        except Exception:  # noqa: BLE001
            log.exception("PUT /api/integrity/top-categories failed")
            status = 500
            body = json_bytes_strict(
                {
                    "ok": False,
                    "error": "server_error",
                    "message": "Top categories save failed (see server log).",
                }
            )
        return Response(content=body, status_code=status, media_type="application/json; charset=utf-8")

    @r.post("/api/integrity/rename-category", include_in_schema=False)
    async def integrity_rename(request: Request) -> Response:
        raw = await request.body()
        try:
            status, payload = integrity.rename_category(raw)
            body = json_bytes_strict(payload)
        except Exception:  # noqa: BLE001
            log.exception("POST /api/integrity/rename-category failed")
            status = 500
            body = json_bytes_strict(
                {
                    "ok": False,
                    "error": "server_error",
                    "message": "Rename failed (see server log).",
                }
            )
        return Response(content=body, status_code=status, media_type="application/json; charset=utf-8")

    @r.patch("/api/integrity/ledger-tx", include_in_schema=False)
    async def integrity_ledger_tx(request: Request) -> Response:
        raw = await request.body()
        try:
            status, payload = integrity.patch_ledger_transaction(raw)
            body = json_bytes_strict(payload)
        except Exception:  # noqa: BLE001
            log.exception("PATCH /api/integrity/ledger-tx failed")
            status = 500
            body = json_bytes_strict(
                {
                    "ok": False,
                    "error": "server_error",
                    "message": "Ledger row patch failed (see server log).",
                }
            )
        return Response(content=body, status_code=status, media_type="application/json; charset=utf-8")

    @r.patch("/api/integrity/store-static", include_in_schema=False)
    async def integrity_store_static(request: Request) -> Response:
        raw = await request.body()
        try:
            status, payload = integrity.patch_store_static(raw)
            body = json_bytes_strict(payload)
        except Exception:  # noqa: BLE001
            log.exception("PATCH /api/integrity/store-static failed")
            status = 500
            body = json_bytes_strict(
                {
                    "ok": False,
                    "error": "server_error",
                    "message": "Store update failed (see server log).",
                }
            )
        return Response(content=body, status_code=status, media_type="application/json; charset=utf-8")

    app.include_router(r)
