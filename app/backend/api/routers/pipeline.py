"""Pipeline job + SSE event routes."""

from __future__ import annotations

import asyncio
import json
import queue as queue_mod
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from api import jobs
from api.utils import StateDep


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

        code, payload = jobs.start_background_job(state, action, options)
        return Response(
            content=_json_bytes(payload),
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

    app.include_router(r)
