"""Pipeline job + SSE event routes."""

from __future__ import annotations

import asyncio
import json
import logging
import queue as queue_mod
import threading
import traceback
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from api import jobs
from api.utils import StateDep
from logger import attach_sink_log_handlers, detach_sink_log_handlers

log = logging.getLogger(__name__)

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


def register_pipeline_routes(app: FastAPI) -> None:
    r = APIRouter()

    @r.get("/api/status", include_in_schema=False)
    def api_status(state: StateDep) -> JSONResponse:
        return JSONResponse(
            {
                "running": state.running,
                "current_job": state.current_job,
                "error": state.last_error or None,
            }
        )

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

    app.include_router(r)
