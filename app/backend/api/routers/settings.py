"""Settings / config / sheets HTTP routes."""

from __future__ import annotations

import json
from typing import Any

import config
from fastapi import APIRouter, FastAPI, Request, Response

from api import jobs, providers_config, sheets, transaction_drop_rules_config
from api.utils import StateDep, json_bytes_strict, run_blocking


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def register_settings_routes(app: FastAPI) -> None:
    r = APIRouter()

    @r.get("/api/config", include_in_schema=False)
    def api_config() -> Response:
        host = getattr(config, "control_http_host", "127.0.0.1")
        cport = int(getattr(config, "control_http_port", 8780))
        body = _json_bytes(
            {
                "control_base": f"http://{host}:{cport}/",
                "categorize_url_hint": f"http://{host}:{cport}/categorize/",
                "workspace_root": config.workspace_root() or None,
                "input_dir": config.download_inbox_dir,
                "export_dir": config.export_dir,
                "ledger_db_file": config.ledger_db_file,
            }
        )
        return Response(content=body, media_type="application/json; charset=utf-8")

    @r.get("/api/providers-config", include_in_schema=False)
    def providers_config_get() -> Response:
        return Response(
            content=json_bytes_strict(providers_config.get_config()),
            media_type="application/json; charset=utf-8",
        )

    @r.put("/api/providers-config", include_in_schema=False)
    async def providers_config_put(request: Request) -> Response:
        clen = int(request.headers.get("content-length", "0") or "0")
        raw = await request.body() if clen > 0 else b"{}"
        status, payload = await run_blocking(providers_config.put_config, raw)
        return Response(
            content=json_bytes_strict(payload),
            status_code=status,
            media_type="application/json; charset=utf-8",
        )

    @r.get("/api/transaction-drop-rules", include_in_schema=False)
    def transaction_drop_rules_get() -> Response:
        try:
            doc = transaction_drop_rules_config.get_config()
        except ValueError as e:
            return Response(
                content=json_bytes_strict({"ok": False, "error": "validation_error", "message": str(e)}),
                status_code=500,
                media_type="application/json; charset=utf-8",
            )
        return Response(
            content=json_bytes_strict(doc),
            media_type="application/json; charset=utf-8",
        )

    @r.put("/api/transaction-drop-rules", include_in_schema=False)
    async def transaction_drop_rules_put(request: Request) -> Response:
        clen = int(request.headers.get("content-length", "0") or "0")
        raw = await request.body() if clen > 0 else b"{}"
        status, payload = await run_blocking(transaction_drop_rules_config.put_config, raw)
        return Response(
            content=json_bytes_strict(payload),
            status_code=status,
            media_type="application/json; charset=utf-8",
        )

    @r.get("/api/sheets/status", include_in_schema=False)
    def sheets_status() -> Response:
        return Response(
            content=json_bytes_strict(sheets.desktop_status()),
            media_type="application/json; charset=utf-8",
        )

    @r.post("/api/sheets/preview", include_in_schema=False)
    def sheets_preview(state: StateDep) -> Response:
        code, payload = jobs.start_background_job(state, "sheets_preview", {})
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.post("/api/sheets/push", include_in_schema=False)
    async def sheets_push(request: Request, state: StateDep) -> Response:
        try:
            data = json.loads((await request.body()).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return Response(
                content=json_bytes_strict({"ok": False, "message": "invalid JSON body"}),
                status_code=400,
                media_type="application/json; charset=utf-8",
            )
        opts = data if isinstance(data, dict) else {}
        force = bool(opts.get("force"))
        code, payload = jobs.start_background_job(state, "sheets_push", {"force": force})
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.post("/api/providers/import-env", include_in_schema=False)
    async def providers_import_env() -> Response:
        status, payload = await run_blocking(providers_config.import_dotenv)
        return Response(
            content=json_bytes_strict(payload),
            status_code=status,
            media_type="application/json; charset=utf-8",
        )

    app.include_router(r)
