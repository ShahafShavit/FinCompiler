"""Categorize HTTP routes (`/categorize/api/*`)."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request, Response

from api import categorize_queue
from api.utils import StateDep


def register_categorize_routes(app: FastAPI) -> None:
    cat = APIRouter()

    @cat.get("/api/summary", include_in_schema=False)
    async def cat_summary() -> Response:
        code, body, ct = categorize_queue.handle_get("/api/summary")
        return Response(content=body, status_code=code, media_type=ct)

    @cat.get("/api/next", include_in_schema=False)
    async def cat_next(state: StateDep) -> Response:
        code, body, ct = categorize_queue.handle_get("/api/next", control_state=state)
        return Response(content=body, status_code=code, media_type=ct)

    @cat.post("/api/respond", include_in_schema=False)
    async def cat_respond(request: Request, state: StateDep) -> Response:
        raw = await request.body()
        code, body, ct = categorize_queue.handle_post(
            "/api/respond", raw, control_state=state
        )
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
