"""HTTP route hub: domain routers + holdings/portfolio + SPA shell."""

from __future__ import annotations

import json
import logging

import config
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import RedirectResponse

from api.routers.cash import register_cash_routes
from api.routers.categorize import register_categorize_routes
from api.routers.dashboard import register_dashboard_routes
from api.routers.heatmap import register_heatmap_routes
from api.routers.pipeline import register_pipeline_routes
from api.routers.settings import register_settings_routes
from api.utils import (
    SPA_INDEX_MISSING_BYTES,
    SPA_ROUTES,
    content_type_for,
    json_bytes_strict,
    normalize_http_path,
    run_blocking,
    safe_subpath,
    spa_assets_dir,
    spa_dist_dir,
    spa_index_bytes,
)

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


def register_routes(app: FastAPI) -> None:
    register_categorize_routes(app)
    register_heatmap_routes(app)
    register_dashboard_routes(app)
    register_settings_routes(app)
    register_pipeline_routes(app)
    register_cash_routes(app)

    r = APIRouter()

    @r.get("/categorize", include_in_schema=False)
    @r.get("/categorizer", include_in_schema=False)
    def redirect_categorize() -> RedirectResponse:
        return RedirectResponse("/categorize/", status_code=302)

    @r.get("/holdings", include_in_schema=False)
    def redirect_holdings() -> RedirectResponse:
        return RedirectResponse("/holdings/", status_code=302)

    @r.get("/portfolio", include_in_schema=False)
    def redirect_portfolio() -> RedirectResponse:
        return RedirectResponse("/portfolio/", status_code=302)

    @r.get("/api/holdings/meta", include_in_schema=False)
    def holdings_meta() -> Response:
        from pipeline.holdings_balance import get_holdings_meta

        return Response(
            content=json_bytes_strict(get_holdings_meta(config.ledger_db_file)),
            media_type="application/json; charset=utf-8",
        )

    @r.get("/api/holdings/timeline", include_in_schema=False)
    def holdings_timeline(request: Request) -> Response:
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
        except Exception:  # noqa: BLE001
            log.exception("GET /api/holdings/timeline failed")
            return Response(
                content=json_bytes_strict(
                    {
                        "ok": False,
                        "error": "invalid_request",
                        "message": "Holdings timeline request failed.",
                    }
                ),
                status_code=400,
                media_type="application/json; charset=utf-8",
            )

    @r.get("/api/portfolio/meta", include_in_schema=False)
    def portfolio_meta() -> Response:
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
    def portfolio_timeseries(request: Request) -> Response:
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
            conflicts = await run_blocking(get_holdings_conflicts, rows, config.ledger_db_file)
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
            out = await run_blocking(
                upsert_holdings_rows,
                rows,
                config.ledger_db_file,
                overwrite_conflicts=overwrite,
            )
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
            out = await run_blocking(
                move_holdings_date,
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

    @r.get("/assets/{path:path}", include_in_schema=False)
    def spa_assets(path: str) -> Response:
        root = spa_assets_dir()
        target = safe_subpath(root, path)
        if target is None or not target.is_file():
            return Response(content=b"Not Found", status_code=404, media_type="text/plain; charset=utf-8")
        return Response(
            content=target.read_bytes(),
            media_type=content_type_for(target),
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @r.get("/", include_in_schema=False)
    def spa_root() -> Response:
        return _spa_index_response()

    @r.get("/vite.svg", include_in_schema=False)
    @r.get("/favicon.ico", include_in_schema=False)
    def spa_root_icons(request: Request) -> Response:
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
    def spa_shell_or_404(request: Request, path: str) -> Response:
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
