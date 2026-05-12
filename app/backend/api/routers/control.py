"""HTTP routes migrated from the former stdlib control server."""

from __future__ import annotations

import asyncio
import json
import logging
import queue as queue_mod
import threading
import traceback
from typing import Any

import config
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from api import (
    categorize_queue,
    dashboard,
    heatmap,
    integrity,
    jobs,
    providers_config,
    sheets,
    transaction_drop_rules_config,
)
from api.utils import (
    StateDep,
    SPA_INDEX_MISSING_BYTES,
    SPA_ROUTES,
    content_type_for,
    json_bytes_strict,
    normalize_http_path,
    safe_subpath,
    spa_assets_dir,
    spa_dist_dir,
    spa_index_bytes,
)
from ledger import migrate_ledger_db
from logger import attach_sink_log_handlers, detach_sink_log_handlers

log = logging.getLogger(__name__)


def _spa_index_response() -> Response:
    body = spa_index_bytes()
    if body is None:
        return Response(
            content=SPA_INDEX_MISSING_BYTES,
            status_code=503,
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )
    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


_JOB_SSE_LOGGERS = [
    "pipeline.fetch",
    "pipeline.route_inbox",
    "pipeline.spreadsheet_ingest",
    "pipeline.workbook_normalize",
    "pipeline.fingerprint",
    "pipeline.compiler",
    "api.categorize",
]


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def register_routes(app: FastAPI) -> None:
    r = APIRouter()

    @r.get("/categorize", include_in_schema=False)
    @r.get("/categorizer", include_in_schema=False)
    async def redirect_categorize() -> RedirectResponse:
        return RedirectResponse("/categorize/", status_code=302)

    @r.get("/holdings", include_in_schema=False)
    async def redirect_holdings() -> RedirectResponse:
        return RedirectResponse("/holdings/", status_code=302)

    @r.get("/portfolio", include_in_schema=False)
    async def redirect_portfolio() -> RedirectResponse:
        return RedirectResponse("/portfolio/", status_code=302)

    cat = APIRouter()

    @cat.get("/api/summary", include_in_schema=False)
    async def cat_summary() -> Response:
        code, body, ct = categorize_queue.handle_get("/api/summary")
        return Response(content=body, status_code=code, media_type=ct)

    @cat.get("/api/next", include_in_schema=False)
    async def cat_next() -> Response:
        code, body, ct = categorize_queue.handle_get("/api/next")
        return Response(content=body, status_code=code, media_type=ct)

    @cat.post("/api/respond", include_in_schema=False)
    async def cat_respond(request: Request) -> Response:
        raw = await request.body()
        code, body, ct = categorize_queue.handle_post("/api/respond", raw)
        return Response(content=body, status_code=code, media_type=ct)

    @cat.post("/api/revise", include_in_schema=False)
    async def cat_revise(request: Request) -> Response:
        raw = await request.body()
        code, body, ct = categorize_queue.handle_post("/api/revise", raw)
        return Response(content=body, status_code=code, media_type=ct)

    @cat.post("/api/discard", include_in_schema=False)
    async def cat_discard(request: Request) -> Response:
        raw = await request.body()
        code, body, ct = categorize_queue.handle_post("/api/discard", raw)
        return Response(content=body, status_code=code, media_type=ct)

    app.include_router(cat, prefix="/categorize")

    @r.get("/heatmap/api/data", include_in_schema=False)
    async def heatmap_data() -> Response:
        try:
            snap = heatmap.api_snapshot()
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
    async def heatmap_detail(request: Request) -> Response:
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

    @r.get("/api/status", include_in_schema=False)
    async def api_status(state: StateDep) -> JSONResponse:
        return JSONResponse(
            {
                "running": state.running,
                "current_job": state.current_job,
                "error": state.last_error or None,
            }
        )

    @r.get("/api/config", include_in_schema=False)
    async def api_config() -> Response:
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
    async def providers_config_get() -> Response:
        return Response(
            content=json_bytes_strict(providers_config.get_config()),
            media_type="application/json; charset=utf-8",
        )

    @r.put("/api/providers-config", include_in_schema=False)
    async def providers_config_put(request: Request) -> Response:
        clen = int(request.headers.get("content-length", "0") or "0")
        raw = await request.body() if clen > 0 else b"{}"
        status, payload = providers_config.put_config(raw)
        return Response(
            content=json_bytes_strict(payload),
            status_code=status,
            media_type="application/json; charset=utf-8",
        )

    @r.get("/api/transaction-drop-rules", include_in_schema=False)
    async def transaction_drop_rules_get() -> Response:
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
        status, payload = transaction_drop_rules_config.put_config(raw)
        return Response(
            content=json_bytes_strict(payload),
            status_code=status,
            media_type="application/json; charset=utf-8",
        )

    @r.get("/api/sheets/status", include_in_schema=False)
    async def sheets_status() -> Response:
        return Response(
            content=json_bytes_strict(sheets.desktop_status()),
            media_type="application/json; charset=utf-8",
        )

    @r.post("/api/sheets/preview", include_in_schema=False)
    async def sheets_preview() -> Response:
        snap = sheets.desktop_preview()
        if snap.get("error") == "not_configured":
            return Response(
                content=json_bytes_strict(snap),
                status_code=503,
                media_type="application/json; charset=utf-8",
            )
        if snap.get("error") == "no_ledger":
            return Response(
                content=json_bytes_strict(snap),
                status_code=503,
                media_type="application/json; charset=utf-8",
            )
        return Response(
            content=json_bytes_strict(snap),
            media_type="application/json; charset=utf-8",
        )

    @r.post("/api/sheets/push", include_in_schema=False)
    async def sheets_push(request: Request) -> Response:
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
        ok, msg, preview = sheets.desktop_push(force=force)
        payload: dict = {"ok": ok, "message": msg}
        if preview is not None:
            payload["preview"] = preview
        if ok:
            code = 200
        elif preview is not None:
            code = 409
        elif "not configured" in (msg or "").lower():
            code = 503
        else:
            code = 502
        return Response(
            content=json_bytes_strict(payload),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.post("/api/providers/import-env", include_in_schema=False)
    async def providers_import_env() -> Response:
        status, payload = providers_config.import_dotenv()
        return Response(
            content=json_bytes_strict(payload),
            status_code=status,
            media_type="application/json; charset=utf-8",
        )

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

    @r.patch("/heatmap/api/transaction", include_in_schema=False)
    async def heatmap_tx_patch(request: Request) -> Response:
        raw = await request.body()
        try:
            status, payload = heatmap.patch_ledger_transaction(raw)
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

    @r.post("/api/jobs/run", include_in_schema=False)
    async def jobs_run(request: Request, state: StateDep) -> Response:
        try:
            data = json.loads((await request.body()).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return Response(
                content=_json_bytes({"ok": False, "error": "invalid JSON"}),
                status_code=400,
                media_type="application/json; charset=utf-8",
            )
        action = str(data.get("action") or "").strip()
        options = data.get("options") if isinstance(data.get("options"), dict) else {}
        if not action:
            return Response(
                content=_json_bytes({"ok": False, "error": "action required"}),
                status_code=400,
                media_type="application/json; charset=utf-8",
            )

        ok, info = state.try_start_job(action)
        if not ok:
            return Response(
                content=_json_bytes({"ok": False, "error": info}),
                status_code=409,
                media_type="application/json; charset=utf-8",
            )
        job_id = info

        def worker() -> None:
            def sink(msg: str) -> None:
                state.log_line(msg)

            err: BaseException | None = None
            log_pairs = attach_sink_log_handlers(sink, _JOB_SSE_LOGGERS)
            try:
                jobs.run_action(action, options, sink=sink, control_state=state)
            except BaseException as e:  # noqa: BLE001
                err = e
                tb = traceback.format_exc()
                state.log_line(tb)
            finally:
                detach_sink_log_handlers(log_pairs)
                state.finish_job(err)

        threading.Thread(target=worker, name=f"job-{job_id}", daemon=True).start()
        return Response(
            content=_json_bytes({"ok": True, "job_id": job_id}),
            status_code=202,
            media_type="application/json; charset=utf-8",
        )

    @r.get("/api/holdings/meta", include_in_schema=False)
    async def holdings_meta() -> Response:
        from pipeline.holdings_balance import get_holdings_meta

        return Response(
            content=json_bytes_strict(get_holdings_meta(config.ledger_db_file)),
            media_type="application/json; charset=utf-8",
        )

    @r.get("/api/holdings/timeline", include_in_schema=False)
    async def holdings_timeline(request: Request) -> Response:
        from urllib.parse import parse_qs, urlparse

        from pipeline.holdings_balance import query_holdings_timeline

        parsed = urlparse(str(request.url))
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
            payload = {"ok": True, "rows": df.to_dict(orient="records")}
            return Response(
                content=json_bytes_strict(payload),
                media_type="application/json; charset=utf-8",
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                content=json_bytes_strict(
                    {"ok": False, "error": "invalid_request", "message": str(e)}
                ),
                status_code=400,
                media_type="application/json; charset=utf-8",
            )

    @r.get("/api/portfolio/meta", include_in_schema=False)
    async def portfolio_meta() -> Response:
        from pipeline.trade_portfolio_queries import get_trade_portfolio_meta

        try:
            payload = get_trade_portfolio_meta(config.ledger_db_file)
        except Exception:  # noqa: BLE001
            log.exception("GET /api/portfolio/meta failed")
            payload = {
                "ok": False,
                "ledger_exists": False,
                "error": "server_error",
                "instruments": [],
                "portfolio_accounts": [],
            }
        return Response(
            content=json_bytes_strict(payload),
            media_type="application/json; charset=utf-8",
        )

    @r.get("/api/portfolio/timeseries", include_in_schema=False)
    async def portfolio_timeseries(request: Request) -> Response:
        from urllib.parse import parse_qs, urlparse

        from pipeline.trade_portfolio_queries import query_trade_portfolio_timeseries

        parsed = urlparse(str(request.url))
        qs = parse_qs(parsed.query, keep_blank_values=False)
        from_date = (qs.get("from") or [None])[0]
        to_date = (qs.get("to") or [None])[0]
        account = (qs.get("account") or [None])[0]
        metric = (qs.get("metric") or [None])[0]
        series_params = [str(x) for x in (qs.get("series") or []) if str(x).strip()]
        try:
            payload = query_trade_portfolio_timeseries(
                config.ledger_db_file,
                start_date=str(from_date).strip() if from_date else None,
                end_date=str(to_date).strip() if to_date else None,
                portfolio_account=str(account).strip() if account else None,
                metric=str(metric).strip() if metric else None,
                series_ids=series_params if series_params else None,
            )
            return Response(
                content=json_bytes_strict(payload),
                media_type="application/json; charset=utf-8",
            )
        except ValueError as e:
            return Response(
                content=json_bytes_strict(
                    {"ok": False, "error": "invalid_request", "message": str(e)}
                ),
                status_code=400,
                media_type="application/json; charset=utf-8",
            )
        except Exception:  # noqa: BLE001
            log.exception("GET /api/portfolio/timeseries failed")
            return Response(
                content=json_bytes_strict(
                    {"ok": False, "error": "server_error", "message": "query failed"}
                ),
                status_code=500,
                media_type="application/json; charset=utf-8",
            )

    @r.post("/api/holdings/parse-paste-grid", include_in_schema=False)
    async def holdings_parse_paste(request: Request) -> Response:
        from pipeline.holdings_balance import parse_holdings_paste_grid

        try:
            data = json.loads((await request.body()).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return Response(
                content=json_bytes_strict(
                    {"ok": False, "error": "invalid_json", "message": "invalid JSON body"}
                ),
                status_code=400,
                media_type="application/json; charset=utf-8",
            )
        text = str((data or {}).get("text") or "")
        out = parse_holdings_paste_grid(text)
        return Response(
            content=json_bytes_strict(out),
            media_type="application/json; charset=utf-8",
        )

    @r.post("/api/holdings/check-conflicts", include_in_schema=False)
    async def holdings_check_conflicts(request: Request) -> Response:
        from pipeline.holdings_balance import get_holdings_conflicts

        try:
            data = json.loads((await request.body()).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return Response(
                content=json_bytes_strict(
                    {"ok": False, "error": "invalid_json", "message": "invalid JSON body"}
                ),
                status_code=400,
                media_type="application/json; charset=utf-8",
            )
        rows = data.get("rows") if isinstance(data.get("rows"), list) else []
        try:
            conflicts = get_holdings_conflicts(rows, config.ledger_db_file)
        except Exception as e:  # noqa: BLE001
            return Response(
                content=json_bytes_strict(
                    {"ok": False, "error": "invalid_rows", "message": str(e)}
                ),
                status_code=400,
                media_type="application/json; charset=utf-8",
            )
        return Response(
            content=json_bytes_strict(
                {"ok": True, "conflicts": conflicts, "conflict_count": len(conflicts)}
            ),
            media_type="application/json; charset=utf-8",
        )

    @r.post("/api/holdings/manual-upsert-batch", include_in_schema=False)
    async def holdings_manual_upsert(request: Request) -> Response:
        from pipeline.holdings_balance import upsert_holdings_rows

        try:
            data = json.loads((await request.body()).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return Response(
                content=json_bytes_strict(
                    {"ok": False, "error": "invalid_json", "message": "invalid JSON body"}
                ),
                status_code=400,
                media_type="application/json; charset=utf-8",
            )
        rows = data.get("rows") if isinstance(data.get("rows"), list) else []
        overwrite = bool(data.get("overwrite_conflicts"))
        try:
            out = upsert_holdings_rows(rows, config.ledger_db_file, overwrite_conflicts=overwrite)
        except Exception as e:  # noqa: BLE001
            return Response(
                content=json_bytes_strict(
                    {"ok": False, "error": "invalid_rows", "message": str(e)}
                ),
                status_code=400,
                media_type="application/json; charset=utf-8",
            )
        code = 200 if out.get("ok") else 409
        return Response(
            content=json_bytes_strict(out),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.post("/api/holdings/move-date", include_in_schema=False)
    async def holdings_move_date(request: Request) -> Response:
        from pipeline.holdings_balance import move_holdings_date

        try:
            data = json.loads((await request.body()).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return Response(
                content=json_bytes_strict(
                    {"ok": False, "error": "invalid_json", "message": "invalid JSON body"}
                ),
                status_code=400,
                media_type="application/json; charset=utf-8",
            )
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
            return Response(
                content=json_bytes_strict(
                    {"ok": False, "error": "invalid_request", "message": str(e)}
                ),
                status_code=400,
                media_type="application/json; charset=utf-8",
            )
        code = 200 if out.get("ok") else 409
        return Response(
            content=json_bytes_strict(out),
            status_code=code,
            media_type="application/json; charset=utf-8",
        )

    @r.get("/api/events", include_in_schema=False)
    async def api_events(state: StateDep) -> StreamingResponse:
        q = state.hub.subscribe()

        async def gen() -> Any:
            try:
                while True:

                    def _one() -> tuple[str, Any]:
                        try:
                            return ("ev", q.get(timeout=20.0))
                        except queue_mod.Empty:
                            return ("keep", None)

                    kind, payload = await asyncio.to_thread(_one)
                    if kind == "keep":
                        yield b": keepalive\n\n"
                        continue
                    ev = payload
                    et = str(ev.get("type") or "message")
                    line = json.dumps(ev, ensure_ascii=False)
                    yield f"event: {et}\ndata: {line}\n\n".encode("utf-8")
            finally:
                state.hub.unsubscribe(q)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream; charset=utf-8",
            headers={"Cache-Control": "no-store", "Connection": "close"},
        )

    @r.get("/assets/{path:path}", include_in_schema=False)
    async def spa_assets(path: str) -> Response:
        root = spa_assets_dir()
        target = safe_subpath(root, path)
        if target is None or not target.is_file():
            return Response(content=b"Not Found", status_code=404, media_type="text/plain; charset=utf-8")
        return Response(
            content=target.read_bytes(),
            media_type=content_type_for(target),
            headers={"Cache-Control": "no-store"},
        )

    @r.get("/", include_in_schema=False)
    async def spa_root() -> Response:
        return _spa_index_response()

    @r.get("/vite.svg", include_in_schema=False)
    @r.get("/favicon.ico", include_in_schema=False)
    async def spa_root_icons(request: Request) -> Response:
        p = normalize_http_path(request.url.path)
        target = spa_dist_dir() / p.lstrip("/")
        if target.is_file():
            return Response(
                content=target.read_bytes(),
                media_type=content_type_for(target),
                headers={"Cache-Control": "no-store"},
            )
        return Response(content=b"", status_code=404, media_type="image/x-icon")

    @r.get("/{path:path}", include_in_schema=False)
    async def spa_shell_or_404(request: Request, path: str) -> Response:
        p = normalize_http_path("/" + path if path else "/")
        if p in SPA_ROUTES or p == "/index.html":
            return _spa_index_response()
        if (
            p.startswith("/api/")
            or p.startswith("/heatmap/api/")
            or p.startswith("/assets/")
        ):
            if "/heatmap" in p:
                log.warning("control HTTP 404 GET heatmap-like raw=%r path=%r", request.url, p)
            return Response(content=b"Not Found", status_code=404, media_type="text/plain")
        return _spa_index_response()

    app.include_router(r)
